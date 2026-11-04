/** Creation and build requests stay bound to the workspace and reviewed inputs. */
import {activeJob, filterBoards} from "./environment-state.js";
import {copyFragments, fragmentFormatError, fragmentKinds, fragmentModes, fragmentSelectionError, needsFragmentFiles, profileFragments} from "./fragment-state.js";
import {isLocalSample, validateSampleCatalogue, readySample, sampleSelectionError, sampleLabel} from "./sample-state.js";

const defaults = () => ({name: "my_app", destination: "", search: "", board_id: "", target: "", sdk_id: "", template_id: "minimal-c", sample_id: ""});
const configurationDefaults = () => ({name: "", search: "", board_id: "", target: "", sdk_id: ""});
const array = (value) => Array.isArray(value) ? value : [];
const templateLanguages = {"minimal-c": "C", "minimal-cpp": "C++", "local-sample": "C"};
export function creatorTemplates(value) {
  if (!Array.isArray(value) || value.length > 3 || new Set(value.map((entry) => entry?.id)).size !== value.length) return [];
  if (!value.every((entry) => entry && Object.hasOwn(templateLanguages, entry.id) && entry.version === 1 && entry.language === templateLanguages[entry.id]
    && typeof entry.title === "string" && entry.title.trim() && typeof entry.description === "string" && entry.description.trim())) return [];
  return value;
}
export const creatorTemplateId = (form) => form.template_id === undefined ? "minimal-c" : form.template_id;
export const selectedCreatorTemplate = (state) => creatorTemplates(state.templates).find((entry) => entry.id === creatorTemplateId(state.form)) || null;
export function creatorTemplateLabel(template, templates, sample) {
  if (template?.id === "local-sample" && template.version === 1) return sampleLabel(sample);
  const descriptor = creatorTemplates(templates).find((entry) => entry.id === template?.id && entry.version === template?.version);
  const title = descriptor?.title || (template?.version === 1 && Object.hasOwn(templateLanguages, template?.id) ? `Minimal ${templateLanguages[template.id]}` : template?.id);
  return title ? `${title} · version ${template.version ?? "unknown"}` : "Template unknown";
}
export const creatorBoards = (snapshot, query = "") => filterBoards(array(snapshot?.boards).filter((board) => board.status === "discovered" && array(board.targets).length), array(snapshot?.socs), {query});
export const creatorSdks = (snapshot) => array(snapshot?.sdks).filter((sdk) => sdk.status === "discovered" && array(sdk.toolchains).some((toolchain) => toolchain.family === "GNU" && toolchain.compiler));
export function creatorBody(form, sample) {
  return {...Object.fromEntries(["name", "destination", "board_id", "target", "sdk_id"].map((key) => [key, String(form[key] || "").trim()])),
    ...(creatorTemplateId(form) === "minimal-c" ? {} : {template_id: form.template_id}),
    ...(isLocalSample(form) ? {sample_id: form.sample_id, sample_fingerprint: sample?.fingerprint} : {})};
}
export function configurationBody(form) {
  return {...Object.fromEntries(["name", "board_id", "target", "sdk_id"].map((key) => [key, String(form[key] || "").trim()])),
    ...(form.fragments === undefined ? {} : {fragments: copyFragments(form.fragments)})};
}
export function selectedConfiguration(state, project) {
  const id = state.configurationSelections?.[project.id];
  return id ? array(project.configurations).find((entry) => entry.id === id) || null : {id: null, name: "Original", profile: project.profile};
}
export function currentCreatorJob(state) {
  return array(state.jobs).find(activeJob) || array(state.jobs).find((job) => job.id === state.jobId) || array(state.jobs)[0] || null;
}
export function createCreatorSession({api, state, workspace, onChange, pendingBoard, isCreating = () => false, schedule = setTimeout, unschedule = clearTimeout}) {
  const lifetime = new AbortController();
  let disposed = false;
  let epoch = 0;
  let formRevision = 0;
  let buildRevision = 0;
  let configurationRevision = 0;
  let timer;
  let reading = false;
  let readSequence = 0;
  let fileSequence = 0;
  let sampleSequence = 0;
  Object.assign(state, {loading: false, busy: "", error: "", cancelling: false});
  // Review plans are server-session capabilities; fetch new ones after remount.
  state.plan = null;
  state.buildPlan = null;
  state.form ||= defaults();
  state.configurationSelections ||= {};
  state.configurationDrafts ||= {};
  state.configurationEditor ||= "";
  state.configurationNotice = null;
  state.configurationFiles = {};
  state.sampleCatalogue = null;
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
    epoch++; formRevision++; buildRevision++; configurationRevision++; fileSequence++; sampleSequence++;
    Object.assign(state, {workspace: path, snapshot: null, templates: [], projects: [], jobs: [], diagnostics: [], jobId: null, plan: null, buildPlan: null, error: "", loading: false, busy: "", cancelling: false, form: {...defaults(), ...state.formsByWorkspace?.[path]}, configurationSelections: {}, configurationDrafts: {}, configurationEditor: "", configurationNotice: null, configurationFiles: {}, sampleCatalogue: null});
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
    const templates = creatorTemplates(data.templates);
    if (JSON.stringify(templates) !== JSON.stringify(state.templates)) { state.plan = null; formRevision++; }
    state.templates = templates;
    const snapshot = data.snapshot?.workspace?.path === state.workspace ? data.snapshot : null;
    if (snapshot?.fingerprint !== state.snapshot?.fingerprint || snapshot?.zephyr?.path !== state.snapshot?.zephyr?.path) {
      state.plan = null; state.buildPlan = null; formRevision++; buildRevision++; configurationRevision++;
      sampleSequence++; state.sampleCatalogue = null;
    }
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
        delete state.configurationSelections[projectId]; delete state.configurationDrafts[projectId]; delete state.configurationFiles[projectId];
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
    const editor = state.configurationEditor;
    if (editor && needsFragmentFiles(copyFragments(state.configurationDrafts[editor]?.fragments)) && !state.configurationFiles[editor]) loadConfigurationFiles(editor);
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
    if (!background && current(path, captured) && sequence === readSequence) await ensureSamples();
  }
  async function loadSamples() {
    if (disposed) return;
    followWorkspace();
    if (!isLocalSample(state.form) || !selectedCreatorTemplate(state) || !state.snapshot || state.snapshot.status === "stale") return;
    const path = state.workspace;
    const captured = epoch;
    const sequence = ++sampleSequence;
    const catalogue = {loading: true, ready: false, error: "", samples: [], diagnostics: []};
    state.sampleCatalogue = catalogue;
    formRevision++; state.plan = null;
    publish();
    try {
      const data = await request("/api/projects/samples", {method: "POST", body: {workspace: path}});
      if (!current(path, captured) || sequence !== sampleSequence) return;
      Object.assign(catalogue, validateSampleCatalogue(data, path, state.snapshot?.zephyr?.path), {ready: true});
    } catch (error) {
      if (current(path, captured) && sequence === sampleSequence && error.name !== "AbortError") catalogue.error = error.message || String(error);
    } finally {
      if (current(path, captured) && sequence === sampleSequence) { catalogue.loading = false; publish(); }
    }
  }
  const ensureSamples = () => isCreating() && isLocalSample(state.form) && !state.sampleCatalogue ? loadSamples() : Promise.resolve();
  function changeField(key, value) {
    if (disposed) return;
    followWorkspace();
    if (!(key in defaults())) return;
    if (state.form[key] === value) return;
    state.form[key] = value;
    if (key === "board_id") state.form.target = "";
    formRevision++; state.plan = null; state.error = "";
    publish();
    if (key === "template_id") ensureSamples();
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
    if (state.configurationEditor !== projectId && state.configurationFiles[state.configurationEditor]?.loading) delete state.configurationFiles[state.configurationEditor];
    if (!state.configurationDrafts[projectId]) {
      const board = creatorBoards(state.snapshot).find((item) => item.id === profile?.board_id);
      state.configurationDrafts[projectId] = {...configurationDefaults(), board_id: board?.id || "",
        target: array(board?.targets).includes(profile?.target) ? profile.target : "",
        sdk_id: creatorSdks(state.snapshot).some((item) => item.id === profile?.sdk?.id) ? profile.sdk.id : "",
        ...(profileFragments(profile) === undefined ? {} : {fragments: copyFragments(profileFragments(profile))})};
    }
    state.configurationEditor = projectId;
    configurationRevision++;
    state.configurationNotice = null;
    publish();
    if (needsFragmentFiles(copyFragments(state.configurationDrafts[projectId].fragments))) loadConfigurationFiles(projectId);
  }
  function closeConfiguration() {
    if (disposed) return;
    if (state.configurationFiles[state.configurationEditor]?.loading) delete state.configurationFiles[state.configurationEditor];
    fileSequence++;
    state.configurationEditor = "";
    configurationRevision++;
    publish();
  }
  async function loadConfigurationFiles(projectId) {
    if (disposed) return;
    followWorkspace();
    if (state.configurationEditor !== projectId || !array(state.projects).some((item) => item.id === projectId)) return;
    const path = state.workspace;
    const captured = epoch;
    const sequence = ++fileSequence;
    const catalogue = {loading: true, ready: false, error: "", files: {kconfig: [], overlays: []}, diagnostics: []};
    state.configurationFiles[projectId] = catalogue;
    publish();
    try {
      const data = await request("/api/projects/configuration-files", {method: "POST", body: {workspace: path, project_id: projectId}});
      if (!current(path, captured) || sequence !== fileSequence || state.configurationEditor !== projectId || state.configurationFiles[projectId] !== catalogue) return;
      if (data.workspace !== path || data.project_id !== projectId || !fragmentKinds.every((kind) => Array.isArray(data.files?.[kind]) && data.files[kind].every((item) => typeof item === "string"))) throw new Error("The application file list could not be verified. Refresh files again.");
      catalogue.files = data.files;
      catalogue.diagnostics = array(data.diagnostics);
      catalogue.ready = true;
    } catch (error) {
      if (current(path, captured) && sequence === fileSequence && state.configurationEditor === projectId && error.name !== "AbortError") catalogue.error = error.message || String(error);
    } finally {
      if (current(path, captured) && sequence === fileSequence && state.configurationEditor === projectId && state.configurationFiles[projectId] === catalogue) { catalogue.loading = false; publish(); }
    }
  }
  function changeFragment(projectId, kind, action, value) {
    if (disposed) return;
    followWorkspace();
    const form = state.configurationDrafts[projectId];
    if (!form || !fragmentKinds.includes(kind)) return;
    const fragments = copyFragments(form.fragments);
    if (fragmentFormatError(fragments)) return;
    const part = fragments[kind];
    if (action === "mode") {
      if (!fragmentModes.includes(value) || part.mode === value) return;
      part.mode = value;
      if (value === "auto") part.files = [];
    } else if (action === "add") {
      const catalogue = state.configurationFiles[projectId];
      if (part.mode === "auto" || !catalogue?.ready || catalogue.loading || !catalogue.files[kind].includes(value) || part.files.includes(value) || part.files.length >= 32) return;
      part.files.push(value);
    } else {
      const index = part.files.indexOf(value);
      if (index < 0) return;
      if (action === "remove") part.files.splice(index, 1);
      else if (action === "up" && index > 0) [part.files[index - 1], part.files[index]] = [part.files[index], part.files[index - 1]];
      else if (action === "down" && index < part.files.length - 1) [part.files[index + 1], part.files[index]] = [part.files[index], part.files[index + 1]];
      else return;
    }
    form.fragments = fragments;
    configurationRevision++; state.error = ""; state.configurationNotice = null;
    publish();
    if (needsFragmentFiles(fragments) && !state.configurationFiles[projectId]) loadConfigurationFiles(projectId);
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
  const preview = () => action("preview", "/api/projects/preview", () => {
    if (!selectedCreatorTemplate(state)) throw new Error("Choose an available application template before previewing. Refresh the list if no templates are available.");
    const sampleError = sampleSelectionError(state);
    if (sampleError) throw new Error(sampleError);
    return creatorBody(state.form, readySample(state));
  }, (data) => {
    if (data.plan?.workspace === state.workspace) state.plan = data.plan;
  }, {review: () => formRevision});
  const create = () => {
    if (!state.plan) return Promise.resolve();
    return action("create", "/api/projects/create", () => ({plan_id: state.plan.id}), acceptJob);
  };
  const previewBuild = (projectId, operation) => {
    if (!["configure", "build"].includes(operation) || !array(state.projects).some((project) => project.id === projectId)) return Promise.resolve();
    const project = state.projects.find((item) => item.id === projectId);
    const formatError = fragmentFormatError(copyFragments(profileFragments(selectedConfiguration(state, project)?.profile)));
    if (formatError) { state.error = formatError; state.buildPlan = null; publish(); return Promise.resolve(); }
    const configurationId = state.configurationSelections[projectId];
    return action("build-preview", "/api/projects/build-preview", {project_id: projectId, operation, ...(configurationId ? {configuration_id: configurationId} : {})}, (data) => {
      if (data.plan?.workspace === state.workspace) state.buildPlan = data.plan;
    }, {review: () => buildRevision});
  };
  const saveConfiguration = (projectId) => {
    const form = state.configurationDrafts[projectId];
    if (!form || !array(state.projects).some((project) => project.id === projectId)) return Promise.resolve();
    const fragmentError = fragmentSelectionError(copyFragments(form.fragments), state.configurationFiles[projectId]);
    if (fragmentError) { state.error = fragmentError; publish(); return Promise.resolve(); }
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
  return {load, loadSamples, ensureSamples, refresh: () => tick({force: true}), preview, create, previewBuild, build, cancel, changeField,
    selectConfiguration, editConfiguration, closeConfiguration, changeConfigurationField, saveConfiguration, loadConfigurationFiles, changeFragment,
    tick, dispose() { disposed = true; epoch++; lifetime.abort(); if (timer !== undefined) unschedule(timer); }};
}
