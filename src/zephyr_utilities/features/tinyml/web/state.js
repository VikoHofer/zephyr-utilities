/** TinyML selects inputs; Projects owns review, publication and build evidence. */
const array = (value) => Array.isArray(value) ? value : [];
const strings = (value) => Array.isArray(value) && value.every((item) => typeof item === "string");
const hash = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const text = (value, limit = 1000) => typeof value === "string" && value.length <= limit;
const defaults = () => ({demo_id: "mnist-shell", variant_id: "reference", search: "", board_id: "", target: "", sdk_id: ""});

export function validateDemoCatalogue(value, workspace) {
  if (!value || value.schema_version !== 1 || value.workspace !== workspace || !strings(value.diagnostics)
    || !(value.environment_fingerprint === null || hash(value.environment_fingerprint))
    || !Array.isArray(value.demos) || value.demos.length > 1
    || !value.demos.every((demo) => demo && demo.demo_id === "mnist-shell" && demo.variant_id === "reference"
      && demo.sample_id === "tinyml/mnist-shell" && demo.framework === "TensorFlow Lite Micro"
      && text(demo.title) && demo.title.trim() && text(demo.description, 5000)
      && ["available", "missing", "unsupported"].includes(demo.status)
      && strings(demo.requirements) && strings(demo.diagnostics)
      && (demo.source_fingerprint === null || hash(demo.source_fingerprint))
      && (demo.status !== "available" || hash(demo.source_fingerprint)))) {
    throw new Error("The demo catalogue could not be verified. Refresh demos to try again.");
  }
  const snapshot = value.snapshot;
  if (snapshot !== null && (!snapshot || !["complete", "partial", "stale"].includes(snapshot.status)
    || snapshot.workspace?.path !== workspace || snapshot.fingerprint !== value.environment_fingerprint
    || !Array.isArray(snapshot.boards) || snapshot.boards.length > 10000
    || !snapshot.boards.every((board) => board && text(board.id) && text(board.name)
      && strings(board.targets) && board.targets.length <= 1024)
    || !Array.isArray(snapshot.sdks) || snapshot.sdks.length > 128
    || !snapshot.sdks.every((sdk) => sdk && text(sdk.id) && text(sdk.path, 4096) && Array.isArray(sdk.toolchains)))) {
    throw new Error("The saved target catalogue could not be verified. Analyze the workspace in Projects.");
  }
  return value;
}

export const selectedDemo = (state) => array(state.catalogue?.demos).find((demo) => demo.demo_id === state.form.demo_id
  && demo.variant_id === state.form.variant_id) || null;
export const allBoards = (state) => array(state.catalogue?.snapshot?.boards).filter((board) => board.status === "discovered" && board.targets.length);
export const selectedBoard = (state) => allBoards(state).find((board) => board.id === state.form.board_id) || null;
export function filteredBoards(state) {
  const terms = state.form.search.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return allBoards(state).filter((board) => {
    const haystack = [board.name, board.full_name, board.vendor, ...array(board.socs), ...board.targets].join(" ").toLocaleLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}
export const availableSdks = (state) => array(state.catalogue?.snapshot?.sdks).filter((sdk) => sdk.status === "discovered"
  && sdk.toolchains.some((tool) => tool?.family === "GNU" && typeof tool.compiler === "string" && tool.compiler));

export function selectionProblem(state) {
  if (state.loading) return "Wait for the demo catalogue to finish loading.";
  if (!state.catalogue) return "Refresh demos to read the local requirements.";
  const snapshot = state.catalogue.snapshot;
  if (!snapshot || snapshot.status === "stale" || !hash(state.catalogue.environment_fingerprint)) {
    return "Analyze this workspace in Projects before choosing a target.";
  }
  const demo = selectedDemo(state);
  if (!demo) return "Choose a listed demo.";
  if (demo.status !== "available") return "Resolve the demo source or dependency requirement before continuing.";
  const board = selectedBoard(state);
  if (!board) return "Choose a discovered board.";
  if (!board.targets.includes(state.form.target)) return "Choose an exact target declared by this board.";
  if (!availableSdks(state).some((sdk) => sdk.id === state.form.sdk_id)) return "Choose an installed GNU Zephyr SDK with a compiler.";
  return "";
}

export function handoffBody(state) {
  const problem = selectionProblem(state);
  if (problem) throw new Error(problem);
  const demo = selectedDemo(state);
  return {workspace: state.workspace, demo_id: demo.demo_id, variant_id: demo.variant_id,
    source_fingerprint: demo.source_fingerprint, environment_fingerprint: state.catalogue.environment_fingerprint,
    selection: {board_id: state.form.board_id, target: state.form.target, sdk_id: state.form.sdk_id}};
}

export function createDemoSession({api, state, workspace, navigate, onChange = () => {}}) {
  let disposed = false;
  let generation = 0;
  let revision = 0;
  let read;
  let handoff;
  state.drafts ||= Object.create(null);
  Object.assign(state, {catalogue: null, loading: false, submitting: false, error: "", handoffError: "", handoffNotice: ""});
  state.form ||= defaults();

  function cancelRequests() {
    read?.abort(); handoff?.abort(); read = null; handoff = null;
    state.loading = false; state.submitting = false;
  }
  function reconcileWorkspace() {
    if (disposed) return false;
    const next = workspace();
    if (state.workspace === next) return false;
    if (typeof state.workspace === "string") state.drafts[state.workspace] = {...state.form};
    cancelRequests(); generation++; revision++;
    state.workspace = next;
    state.form = Object.hasOwn(state.drafts, next) ? {...state.drafts[next]} : defaults();
    Object.assign(state, {catalogue: null, error: "", handoffError: "", handoffNotice: ""});
    return true;
  }
  function current(capturedWorkspace, capturedGeneration) {
    return !disposed && workspace() === capturedWorkspace && state.workspace === capturedWorkspace && generation === capturedGeneration;
  }
  async function load() {
    if (disposed) return;
    reconcileWorkspace();
    read?.abort(); handoff?.abort(); state.submitting = false;
    const request = new AbortController(); read = request;
    const capturedWorkspace = state.workspace, capturedGeneration = ++generation;
    Object.assign(state, {loading: true, error: "", handoffError: "", handoffNotice: ""}); onChange();
    try {
      const result = await api("/api/projects/demo-catalog", {method: "POST", body: {workspace: capturedWorkspace}, signal: request.signal});
      if (!current(capturedWorkspace, capturedGeneration) || request.signal.aborted) return;
      state.catalogue = validateDemoCatalogue(result, capturedWorkspace);
    } catch (error) {
      if (current(capturedWorkspace, capturedGeneration) && !request.signal.aborted) {
        state.catalogue = null; state.error = error?.message || "The local demo catalogue could not be loaded.";
      }
    } finally {
      if (current(capturedWorkspace, capturedGeneration) && read === request) { state.loading = false; read = null; onChange(); }
    }
  }
  function changeField(name, value) {
    if (disposed || !Object.hasOwn(defaults(), name) || typeof value !== "string") return;
    reconcileWorkspace();
    if (state.form[name] === value) return;
    handoff?.abort(); handoff = null; state.submitting = false; revision++;
    state.form[name] = value;
    if (name === "board_id") state.form.target = "";
    if (name === "demo_id") state.form.variant_id = "reference";
    state.handoffError = ""; state.handoffNotice = ""; onChange();
  }
  async function continueToProjects() {
    if (disposed || state.submitting) return;
    reconcileWorkspace();
    let body;
    try { body = handoffBody(state); }
    catch (error) { state.handoffError = error.message; onChange(); return; }
    const capturedWorkspace = state.workspace, capturedGeneration = generation, capturedRevision = revision;
    const request = new AbortController(); handoff = request;
    Object.assign(state, {submitting: true, handoffError: "", handoffNotice: "Preparing the Projects selection…"}); onChange();
    const isCurrent = () => current(capturedWorkspace, capturedGeneration) && revision === capturedRevision && !request.signal.aborted;
    try {
      const result = await api("/api/projects/creation-handoffs", {method: "POST", body, signal: request.signal});
      if (!isCurrent()) return;
      if (!result || !/^[a-f0-9]{32}$/.test(result.id) || result.route !== `projects/create/${result.id}`) {
        throw new Error("The Projects selection could not be verified. Refresh demos and try again.");
      }
      state.handoffNotice = "Opening the existing Project Creator. Review files before creation.";
      navigate(result.route);
    } catch (error) {
      if (isCurrent()) { state.handoffError = error?.message || "The Projects selection could not be prepared."; state.handoffNotice = ""; }
    } finally {
      if (isCurrent() && handoff === request) { state.submitting = false; handoff = null; onChange(); }
    }
  }
  reconcileWorkspace();
  return {load, changeField, continueToProjects,
    async navigate() { if (disposed) return; const changed = reconcileWorkspace(); if (changed || !state.catalogue && !state.loading) await load(); },
    dispose() { if (disposed) return; state.drafts[state.workspace] = {...state.form}; disposed = true; generation++; cancelRequests(); },
  };
}
