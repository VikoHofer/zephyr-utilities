/** Preview one inspected image without starting report generation. */
export function inspectionSelection(view) {
  if (view !== "inspect" && !view.startsWith("inspect/")) return null;
  const parts = view.split("/");
  if (parts.length !== 3 || !/^[a-f0-9]{32}$/.test(parts[1])) throw new Error("This inspected image link is invalid. Open the build from Project Creator again.");
  let domain;
  try { domain = decodeURIComponent(parts[2]); }
  catch { throw new Error("This inspected image link contains an invalid image name."); }
  if (!domain || domain.length > 128 || /[\x00-\x1f\x7f]/.test(domain)) throw new Error("This inspected image link contains an invalid image name.");
  return {inspection_id: parts[1], domain};
}

export function createMemoryHandoff({api, state, workspace, onChange}) {
  let disposed = false;
  let revision = 0;
  let request;
  Object.assign(state, {active: false, workspace: workspace(), selection: null, preview: null, loading: false, error: ""});
  const publish = () => { if (!disposed) onChange(); };
  const current = (captured, path) => !disposed && revision === captured && workspace() === path && state.workspace === path;
  function reset(active = false) {
    revision++;
    request?.abort();
    Object.assign(state, {active, workspace: workspace(), selection: null, preview: null, loading: false, error: ""});
  }
  async function read(selection) {
    reset(true);
    state.selection = {...selection};
    state.loading = true;
    const captured = revision;
    const path = state.workspace;
    request = new AbortController();
    publish();
    try {
      const preview = await api("/api/memory/inspection", {method: "POST", body: {...selection}, signal: request.signal});
      if (!current(captured, path)) return;
      if (preview?.workspace !== path || preview.inspection_id !== selection.inspection_id || preview.domain !== selection.domain ||
          typeof preview.build?.path !== "string" || !preview.elf?.path || !preview.elf?.sha256 ||
          (preview.build.ready && preview.build.elf_path !== preview.elf.path)) {
        throw new Error("The inspected image changed while opening it. Open the build from Project Creator again.");
      }
      state.preview = preview;
    } catch (error) {
      if (current(captured, path) && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (current(captured, path)) { state.loading = false; publish(); }
    }
  }
  async function open(view = "") {
    if (disposed) return;
    let selection;
    try { selection = inspectionSelection(view); }
    catch (error) { reset(true); state.error = error.message; publish(); return; }
    if (!selection) { clear(); return; }
    await read({workspace: workspace(), ...selection});
  }
  function clear() { if (!disposed) { reset(); publish(); } }
  function canGenerate() {
    return Boolean(!disposed && state.active && !state.loading && !state.error && state.workspace === workspace() &&
      state.selection && state.preview?.build.ready && state.preview.build.elf_path === state.preview.elf.path);
  }
  function jobBody(name = "") {
    if (!canGenerate()) throw new Error("Open an available inspected image before generating reports.");
    return {build_path: state.preview.build.path, name, selection: {...state.selection}};
  }
  return {
    open, clear, canGenerate, jobBody,
    refresh() { if (!disposed && state.selection) return read({...state.selection, workspace: workspace()}); },
    invalidate(message) {
      if (disposed) return;
      revision++; request?.abort();
      Object.assign(state, {preview: null, loading: false, error: message});
      publish();
    },
    dispose() { disposed = true; revision++; request?.abort(); state.preview = null; state.loading = false; },
  };
}
