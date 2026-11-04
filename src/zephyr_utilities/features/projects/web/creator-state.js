/** Creation and build requests stay bound to the workspace and reviewed inputs. */
import {activeJob, filterBoards} from "./environment-state.js";

const defaults = () => ({name: "my_app", destination: "", search: "", board_id: "", target: "", sdk_id: ""});
const array = (value) => Array.isArray(value) ? value : [];
export const creatorBoards = (snapshot, query = "") => filterBoards(array(snapshot?.boards).filter((board) => board.status === "discovered" && array(board.targets).length), array(snapshot?.socs), {query});
export const creatorSdks = (snapshot) => array(snapshot?.sdks).filter((sdk) => sdk.status === "discovered" && array(sdk.toolchains).some((toolchain) => toolchain.family === "GNU" && toolchain.compiler));
export function creatorBody(form) {
  return Object.fromEntries(["name", "destination", "board_id", "target", "sdk_id"].map((key) => [key, String(form[key] || "").trim()]));
}
export function currentCreatorJob(state) {
  return array(state.jobs).find(activeJob) || array(state.jobs).find((job) => job.id === state.jobId) || array(state.jobs)[0] || null;
}
export function createCreatorSession({api, state, workspace, onChange, pendingBoard, schedule = setTimeout, unschedule = clearTimeout}) {
  const lifetime = new AbortController();
  let disposed = false;
  let epoch = 0;
  let formRevision = 0;
  let timer;
  let reading = false;
  let readSequence = 0;
  Object.assign(state, {loading: false, busy: "", error: "", cancelling: false});
  // Review plans are server-session capabilities; fetch new ones after remount.
  state.plan = null;
  state.buildPlan = null;
  state.form ||= defaults();
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
    epoch++; formRevision++;
    Object.assign(state, {workspace: path, snapshot: null, projects: [], jobs: [], diagnostics: [], jobId: null, plan: null, buildPlan: null, error: "", loading: false, busy: "", cancelling: false, form: {...defaults(), ...state.formsByWorkspace?.[path]}});
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
    if (snapshot?.fingerprint !== state.snapshot?.fingerprint) { state.plan = null; state.buildPlan = null; formRevision++; }
    state.snapshot = snapshot;
    state.projects = array(data.projects).filter((project) => project.workspace === state.workspace);
    state.jobs = array(data.jobs).filter((job) => !job.metadata?.workspace || job.metadata.workspace === state.workspace);
    state.diagnostics = array(data.diagnostics);
    state.needsReload = false;
    choosePending();
    const form = state.form;
    const board = creatorBoards(snapshot).find((entry) => entry.id === form.board_id);
    if (!board) { form.board_id = ""; form.target = ""; }
    else if (!array(board.targets).includes(form.target)) form.target = "";
    if (!creatorSdks(snapshot).some((sdk) => sdk.id === form.sdk_id)) form.sdk_id = "";
    const job = currentCreatorJob(state);
    if (job?.status === "failed") { state.plan = null; state.buildPlan = null; }
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
  async function action(kind, endpoint, body, accept, {review = false} = {}) {
    if (disposed) return;
    if (followWorkspace()) { publish(); await load(); return; }
    if (state.busy || state.loading || array(state.jobs).some(activeJob)) return;
    const path = state.workspace;
    const captured = epoch;
    const reviewed = formRevision;
    state.busy = kind; state.error = "";
    if (kind === "preview") state.plan = null;
    if (kind === "build-preview") state.buildPlan = null;
    publish();
    try {
      const payload = typeof body === "function" ? body() : body;
      const data = await request(endpoint, {method: "POST", body: {...payload, workspace: path}});
      if (current(path, captured) && (!review || reviewed === formRevision)) accept(data);
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
  }, {review: true});
  const create = () => {
    if (!state.plan) return Promise.resolve();
    return action("create", "/api/projects/create", () => ({plan_id: state.plan.id}), acceptJob);
  };
  const previewBuild = (projectId, operation) => {
    if (!["configure", "build"].includes(operation) || !array(state.projects).some((project) => project.id === projectId)) return Promise.resolve();
    return action("build-preview", "/api/projects/build-preview", {project_id: projectId, operation}, (data) => {
      if (data.plan?.workspace === state.workspace) state.buildPlan = data.plan;
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
  async function tick() {
    if (disposed || reading) return;
    const switched = followWorkspace();
    if (switched) publish();
    if (!switched && (state.busy || (!state.needsReload && !array(state.jobs).some(activeJob)))) return;
    reading = true;
    try { await load({background: !switched}); } finally { reading = false; }
  }
  function queue() { timer = schedule(async () => { await tick(); if (!disposed) queue(); }, 1200); }
  followWorkspace(); queue();
  return {load, preview, create, previewBuild, build, cancel, changeField, tick, dispose() { disposed = true; epoch++; lifetime.abort(); if (timer !== undefined) unschedule(timer); }};
}
