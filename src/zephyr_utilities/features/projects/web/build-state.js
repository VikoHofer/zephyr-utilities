/** Read-only build observations stay bound to their workspace and image. */
const array = (value) => Array.isArray(value) ? value : [];
export const buildTextKinds = new Set(["resolved-dts", "preprocessed-dts", "kconfig", "cmake-cache", "build-info"]);
export const selectedBuildDomain = (state) => array(state.observation?.domains).find((domain) => domain.name === state.domain) || null;
export function canReadBuildArtifact(state, kind) {
  const domain = selectedBuildDomain(state);
  return Boolean(state.verified && !state.busy && domain?.freshness === "unchanged" &&
    array(domain.artifacts).some((artifact) => artifact.kind === kind && artifact.availability === "live"));
}
export const canOpenDeviceTree = (state) => canReadBuildArtifact(state, "resolved-dts");
export const canOpenMemory = (state) => canReadBuildArtifact(state, "elf");

export function createBuildSession({api, state, workspace, onChange, navigate}) {
  const lifetime = new AbortController();
  let disposed = false;
  let revision = 0;
  let fileRevision = 0;
  let lastView;
  Object.assign(state, {busy: "", error: "", verified: false, file: null, fileLoading: ""});
  const publish = () => { if (!disposed) onChange(); };
  const current = (path, captured) => !disposed && workspace() === path && state.workspace === path && revision === captured;
  const request = (path, options = {}) => api(path, {...options, signal: lifetime.signal});
  function followWorkspace() {
    const path = workspace();
    if (path === state.workspace) return false;
    if (state.workspace) {
      state.byWorkspace ||= {};
      state.byWorkspace[state.workspace] = {path: state.path, observation: state.observation, domain: state.domain};
    }
    revision++; fileRevision++;
    Object.assign(state, {workspace: path, path: "", observation: null, domain: "", ...state.byWorkspace?.[path],
      busy: "", error: "", verified: false, file: null, fileLoading: ""});
    return true;
  }
  function accept(observation) {
    if (observation?.workspace !== state.workspace || !observation.id || !Array.isArray(observation.domains)) {
      throw new Error("This build observation does not belong to the selected workspace.");
    }
    state.observation = observation;
    state.path = observation.root_build_dir;
    if (!selectedBuildDomain(state)) state.domain = observation.default_domain || observation.domains[0]?.name || "";
    if (!selectedBuildDomain(state)) state.domain = observation.domains[0]?.name || "";
    state.verified = true;
  }
  async function read(kind, endpoint, options) {
    const path = state.workspace;
    const captured = ++revision;
    fileRevision++;
    Object.assign(state, {busy: kind, error: "", verified: false, file: null, fileLoading: ""});
    publish();
    try {
      const observation = await request(endpoint, options);
      if (current(path, captured)) accept(observation);
    } catch (error) {
      if (current(path, captured) && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (current(path, captured)) { state.busy = ""; publish(); }
    }
  }
  function changePath(value) {
    if (disposed) return;
    followWorkspace();
    revision++; fileRevision++;
    Object.assign(state, {path: value, observation: null, domain: "", busy: "", error: "", verified: false, file: null, fileLoading: ""});
    publish();
  }
  async function open(path) {
    if (disposed) return;
    followWorkspace();
    const requested = String(path ?? state.path ?? "").trim();
    if (!requested) { state.error = "Enter an existing build directory."; publish(); return; }
    state.path = requested;
    if (state.observation?.root_build_dir !== requested) { state.observation = null; state.domain = ""; }
    await read("open", "/api/projects/inspect", {method: "POST", body: {workspace: state.workspace, build_dir: requested}});
  }
  async function check() {
    if (disposed) return;
    if (followWorkspace()) publish();
    if (!state.observation || state.busy) return;
    await read("check", `/api/projects/inspections/${encodeURIComponent(state.observation.id)}?workspace=${encodeURIComponent(state.workspace)}`);
  }
  function selectDomain(name) {
    if (disposed) return;
    if (followWorkspace()) { publish(); return; }
    if (!array(state.observation?.domains).some((domain) => domain.name === name)) return;
    fileRevision++;
    Object.assign(state, {domain: name, file: null, fileLoading: "", error: ""});
    publish();
  }
  async function readText(kind) {
    if (disposed) return;
    if (followWorkspace()) { publish(); return; }
    if (!buildTextKinds.has(kind) || !canReadBuildArtifact(state, kind)) return;
    const path = state.workspace;
    const captured = revision;
    const fileRequest = ++fileRevision;
    const domain = state.domain;
    const inspectionId = state.observation.id;
    Object.assign(state, {file: null, fileLoading: kind, error: ""});
    publish();
    try {
      const file = await request("/api/projects/inspection-text", {method: "POST", body: {workspace: path, inspection_id: inspectionId, domain, kind}});
      if (current(path, captured) && fileRevision === fileRequest && state.domain === domain) state.file = {...file, kind, domain};
    } catch (error) {
      if (current(path, captured) && fileRevision === fileRequest && error.name !== "AbortError") {
        state.error = error.message || String(error); state.verified = false;
      }
    } finally {
      if (current(path, captured) && fileRevision === fileRequest) { state.fileLoading = ""; publish(); }
    }
  }
  function openDeviceTree() {
    if (disposed) return;
    if (followWorkspace()) { publish(); return; }
    if (canOpenDeviceTree(state)) navigate(`devicetree/inspect/${encodeURIComponent(state.observation.id)}/${encodeURIComponent(state.domain)}`);
  }
  function openMemory() {
    if (disposed) return;
    if (followWorkspace()) { publish(); return; }
    if (canOpenMemory(state)) navigate(`memory/inspect/${encodeURIComponent(state.observation.id)}/${encodeURIComponent(state.domain)}`);
  }
  async function show(view = "builds") {
    if (disposed) return;
    const previousWorkspace = state.workspace;
    const switched = followWorkspace();
    const changed = lastView !== view;
    lastView = view;
    if (!changed && !switched) return;
    publish();
    if (view.startsWith("builds/") && !(switched && previousWorkspace && !changed)) {
      let path;
      try { path = decodeURIComponent(view.slice("builds/".length)); }
      catch { state.error = "This build link contains an invalid path."; publish(); return; }
      if (path && path !== state.observation?.root_build_dir) { await open(path); return; }
    }
    await check();
  }
  return {open, check, changePath, selectDomain, readText, openDeviceTree, openMemory, navigate: show,
    dispose() { disposed = true; revision++; fileRevision++; lifetime.abort(); }};
}
