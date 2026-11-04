/** Creation and build requests stay bound to the workspace and reviewed inputs. */
import {activeJob, filterBoards} from "./environment-state.js";

const defaults = () => ({name: "my_app", destination: "", search: "", board_id: "", target: "", sdk_id: ""});
const configurationDefaults = () => ({name: "", search: "", board_id: "", target: "", sdk_id: ""});
const array = (value) => Array.isArray(value) ? value : [];
export const creatorBoards = (snapshot, query = "") => filterBoards(array(snapshot?.boards).filter((board) => board.status === "discovered" && array(board.targets).length), array(snapshot?.socs), {query});
export const creatorSdks = (snapshot) => array(snapshot?.sdks).filter((sdk) => sdk.status === "discovered" && array(sdk.toolchains).some((toolchain) => toolchain.family === "GNU" && toolchain.compiler));
export function creatorBody(form) {
  return Object.fromEntries(["name", "destination", "board_id", "target", "sdk_id"].map((key) => [key, String(form[key] || "").trim()]));
}
export function configurationBody(form) {
  return Object.fromEntries(["name", "board_id", "target", "sdk_id"].map((key) => [key, String(form[key] || "").trim()]));
}
export function selectedConfiguration(state, project) {
  const id = state.configurationSelections?.[project.id];
  return id ? array(project.configurations).find((entry) => entry.id === id) || null : {id: null, name: "Original", profile: project.profile};
}
export function currentCreatorJob(state) {
  return array(state.jobs).find(activeJob) || array(state.jobs).find((job) => job.id === state.jobId) || array(state.jobs)[0] || null;
}
export function createCreatorSession({api, state, workspace, onChange, pendingBoard, schedule = setTimeout, unschedule = clearTimeout}) {
  const lifetime = new AbortController();
  let disposed = false;
  let epoch = 0;
  let formRevision = 0;
  let buildRevision = 0;
  let configurationRevision = 0;
  let timer;
  let reading = false;
  let readSequence = 0;
  Object.assign(state, {loading: false, busy: "", error: "", cancelling: false});
  // Review plans are server-session capabilities; fetch new ones after remount.
  state.plan = null;
  state.buildPlan = null;
  state.form ||= defaults();
  state.configurationSelections ||= {};
  state.configurationDrafts ||= {};
  state.configurationEditor ||= "";
  state.configurationNotice = null;
  const publish = () => { if (!disposed) onChange(); };
  const current = (path, captured) => !disposed && workspace() === path && state.workspace === path && epoch === captured;
  const request = (path, options = {}) => api(path, {...options, signal: lifetime.signal});
  function followWorkspace() {
    const path = workspace();
    if (path === state.workspace) return false;
    if (state.workspace) {
      state.formsByWorkspace ||= {};
      state.formsByWorkspace[state.workspace] = {...state.form};
    }
    epoch++; formRevision++; buildRevision++; configurationRevision++;
    Object.assign(state, {workspace: path, snapshot: null, projects: [], jobs: [], diagnostics: [], jobId: null, plan: null, buildPlan: null, error: "", loading: false, busy: "", cancelling: false, form: {...defaults(), ...state.formsByWorkspace?.[path]}, configurationSelections: {}, configurationDrafts: {}, configurationEditor: "", configurationNotice: null});
    return true;
  }
  function choosePending() {
    const value = pendingBoard?.();
    if (value?.workspace !== state.workspace) return;
    const board = creatorBoards(state.snapshot).find((entry) => entry.id === value.board_id);
    if (!board) return;
    if (state.form.board_id !== board.id) {
      state.form.board_id = board.id; state.form.target = ""; state.form.search = "";
      state.plan = null; formRevision++;
    }
  }
  function acceptData(data) {
    if (data.workspace !== state.workspace) return;
    const snapshot = data.snapshot?.workspace?.path === state.workspace ? data.snapshot : null;
    if (snapshot?.fingerprint !== state.snapshot?.fingerprint) { state.plan = null; state.buildPlan = null; formRevision++; buildRevision++; configurationRevision++; }
    const previousProjects = array(state.projects);
    state.snapshot = snapshot;
    state.projects = array(data.projects).filter((project) => project.workspace === state.workspace);
    if (state.buildPlan && !state.projects.some((project) => project.id === state.buildPlan.project_id)) state.buildPlan = null;
    for (const previous of previousProjects) {
      const project = state.projects.find((item) => item.id === previous.id);
      if (!project || JSON.stringify(selectedConfiguration(state, previous)) !== JSON.stringify(selectedConfiguration(state, project))) {
        buildRevision++;
        if (state.buildPlan?.project_id === previous.id) state.buildPlan = null;
      }
    }
    for (const projectId of new Set([...Object.keys(state.configurationSelections), ...Object.keys(state.configurationDrafts)])) {
      const project = state.projects.find((item) => item.id === projectId);
      if (!project) {
        delete state.configurationSelections[projectId]; delete state.configurationDrafts[projectId];
        if (state.configurationEditor === projectId) state.configurationEditor = "";
      } else if (!selectedConfiguration(state, project)) {
        state.configurationSelections[projectId] = "";
        state.configurationNotice = {project_id: projectId, message: "The selected configuration is no longer available. Original is selected; review it before building."};
      }
    }
    state.jobs = array(data.jobs).filter((job) => !job.metadata?.workspace || job.metadata.workspace === state.workspace);
    state.diagnostics = array(data.diagnostics);
    state.needsReload = false;
    choosePending();
    for (const form of [state.form, ...Object.values(state.configurationDrafts)]) {
      const board = creatorBoards(snapshot).find((entry) => entry.id === form.board_id);
      if (!board) { form.board_id = ""; form.target = ""; }
      else if (!array(board.targets).includes(form.target)) form.target = "";
      if (!creatorSdks(snapshot).some((sdk) => sdk.id === form.sdk_id)) form.sdk_id = "";
    }
  }
  async function load({background = false} = {}) {
    if (disposed) return;
    followWorkspace();
    const path = state.workspace;
    const captured = epoch;
    const sequence = ++readSequence;
    if (!background) { state.loading = true; publish(); }
    try {
      const data = await request("/api/projects/creator");
      if (current(path, captured) && sequence === readSequence) acceptData(data);
    } catch (error) {
      if (current(path, captured) && sequence === readSequence && error.name !== "AbortError") {
        state.error = error.message || String(error); state.plan = null; state.buildPlan = null;
      }
    } finally {
      if (current(path, captured) && sequence === readSequence) { state.loading = false; publish(); }
    }
  }
  function changeField(key, value) {
    if (disposed) return;
    followWorkspace();
    if (!(key in defaults())) return;
    if (state.form[key] === value) return;
    state.form[key] = value;
    if (key === "board_id") state.form.target = "";
    formRevision++; state.plan = null; state.error = "";
    publish();
  }
  function selectConfiguration(projectId, configurationId) {
    if (disposed) return;
    followWorkspace();
    const project = array(state.projects).find((item) => item.id === projectId);
    if (!project || configurationId && !array(project.configurations).some((item) => item.id === configurationId)) return;
    if ((state.configurationSelections[projectId] || "") === configurationId) return;
    state.configurationSelections[projectId] = configurationId;
    buildRevision++; configurationRevision++; state.buildPlan = null; state.configurationNotice = null; state.error = "";
    publish();
  }
  function editConfiguration(projectId) {
    if (disposed) return;
    followWorkspace();
    const project = array(state.projects).find((item) => item.id === projectId);
    if (!project) return;
    const profile = selectedConfiguration(state, project)?.profile;
    if (!state.configurationDrafts[projectId]) {
      const board = creatorBoards(state.snapshot).find((item) => item.id === profile?.board_id);
      state.configurationDrafts[projectId] = {...configurationDefaults(), board_id: board?.id || "",
        target: array(board?.targets).includes(profile?.target) ? profile.target : "",
        sdk_id: creatorSdks(state.snapshot).some((item) => item.id === profile?.sdk?.id) ? profile.sdk.id : ""};
    }
    state.configurationEditor = projectId;
    configurationRevision++;
    state.configurationNotice = null;
    publish();
  }
  function closeConfiguration() {
    if (disposed) return;
    state.configurationEditor = "";
    configurationRevision++;
    publish();
  }
  function changeConfigurationField(projectId, key, value) {
    if (disposed) return;
    followWorkspace();
    const form = state.configurationDrafts[projectId];
    if (!form || !(key in configurationDefaults()) || form[key] === value) return;
    form[key] = value;
    if (key === "board_id") form.target = "";
    configurationRevision++; state.error = ""; state.configurationNotice = null;
    publish();
  }
  async function action(kind, endpoint, body, accept, {review = null} = {}) {
    if (disposed) return;
    if (followWorkspace()) { publish(); await load(); return; }
    if (state.busy || state.loading || array(state.jobs).some(activeJob)) return;
    const path = state.workspace;
    const captured = epoch;
    const reviewed = review?.();
    state.busy = kind; state.error = "";
    if (kind === "preview") state.plan = null;
    if (kind === "build-preview") state.buildPlan = null;
    publish();
    try {
      const payload = typeof body === "function" ? body() : body;
      const data = await request(endpoint, {method: "POST", body: {...payload, workspace: path}});
      if (current(path, captured) && (!review || reviewed === review())) accept(data);
    } catch (error) {
      if (current(path, captured) && error.name !== "AbortError") { state.error = error.message || String(error); state.plan = null; state.buildPlan = null; }
    } finally {
      if (current(path, captured)) { state.busy = ""; publish(); }
    }
  }
  function acceptJob(job) {
    state.jobs = [job, ...array(state.jobs).filter((entry) => entry.id !== job.id)];
    state.jobId = job.id; state.plan = null; state.buildPlan = null;
    state.needsReload = true;
  }
  const preview = () => action("preview", "/api/projects/preview", () => creatorBody(state.form), (data) => {
    if (data.plan?.workspace === state.workspace) state.plan = data.plan;
  }, {review: () => formRevision});
  const create = () => {
    if (!state.plan) return Promise.resolve();
    return action("create", "/api/projects/create", () => ({plan_id: state.plan.id}), acceptJob);
  };
  const previewBuild = (projectId, operation) => {
    if (!["configure", "build"].includes(operation) || !array(state.projects).some((project) => project.id === projectId)) return Promise.resolve();
    const configurationId = state.configurationSelections[projectId];
    return action("build-preview", "/api/projects/build-preview", {project_id: projectId, operation, ...(configurationId ? {configuration_id: configurationId} : {})}, (data) => {
      if (data.plan?.workspace === state.workspace) state.buildPlan = data.plan;
    }, {review: () => buildRevision});
  };
  const saveConfiguration = (projectId) => {
    const form = state.configurationDrafts[projectId];
    if (!form || !array(state.projects).some((project) => project.id === projectId)) return Promise.resolve();
    const revision = configurationRevision;
    return action("configuration-save", "/api/projects/configurations", {project_id: projectId, ...configurationBody(form)}, (data) => {
      const configuration = data.configuration;
      const project = array(state.projects).find((item) => item.id === projectId);
      if (!project || configuration?.project_id !== projectId || configuration.workspace !== state.workspace) return;
      project.configurations = [...array(project.configurations).filter((item) => item.id !== configuration.id), configuration];
      readSequence++;
      if (revision === configurationRevision) {
        state.configurationSelections[projectId] = configuration.id;
        state.configurationEditor = "";
        delete state.configurationDrafts[projectId];
      }
      buildRevision++; state.buildPlan = null;
      state.configurationNotice = {project_id: projectId, message: `Configuration “${configuration.name}” saved. Choose Configure or Build when ready.`};
      state.needsReload = true;
    });
  };
  const build = () => {
    if (!state.buildPlan) return Promise.resolve();
    return action("build", "/api/projects/build", () => ({build_plan_id: state.buildPlan.id}), acceptJob);
  };
  async function cancel() {
    if (disposed) return;
    if (followWorkspace()) { publish(); await load(); return; }
    const job = currentCreatorJob(state);
    if (!activeJob(job) || state.cancelling) return;
    const path = state.workspace;
    const captured = epoch;
    state.cancelling = true; publish();
    try {
      const updated = await request(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {method: "POST", body: {}});
      if (current(path, captured)) acceptJob(updated);
    } catch (error) {
      if (current(path, captured) && error.name !== "AbortError") { state.error = error.message || String(error); state.plan = null; state.buildPlan = null; }
    } finally { if (current(path, captured)) { state.cancelling = false; publish(); } }
  }
  async function tick({force = false} = {}) {
    if (disposed || reading) return;
    const switched = followWorkspace();
    if (switched) publish();
    if (!switched && (state.busy || state.loading || (!force && !state.needsReload && !array(state.jobs).some(activeJob)))) return;
    reading = true;
    try { await load({background: !switched && !force}); } finally { reading = false; }
  }
  function queue() { timer = schedule(async () => { await tick(); if (!disposed) queue(); }, 1200); }
  followWorkspace(); queue();
  return {load, refresh: () => tick({force: true}), preview, create, previewBuild, build, cancel, changeField,
    selectConfiguration, editConfiguration, closeConfiguration, changeConfigurationField, saveConfiguration,
    tick, dispose() { disposed = true; epoch++; lifetime.abort(); if (timer !== undefined) unschedule(timer); }};
}
