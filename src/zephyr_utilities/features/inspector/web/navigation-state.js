/** Current source reads stay separate from the saved build and resolved tree. */
const array = (value) => Array.isArray(value) ? value : [];

export function filterSourceFiles(files, query = "") {
  const terms = String(query).trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return array(files).filter((file) => {
    const text = [file.name, file.path, file.kind, ...array(file.origins)].join(" ").toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
}

export function createSourceNavigation({api, state, workspace, onChange}) {
  let disposed = false;
  let revision = 0;
  let request;
  let tree;
  let nodePath;
  Object.assign(state, {mode: "", catalog: null, bindings: null, file: null, panel: "", loading: "", error: "", query: "", limit: 100});
  const publish = () => { if (!disposed) onChange(); };
  function cancel() { revision++; request?.abort(); }
  function reset() {
    cancel();
    Object.assign(state, {mode: "", catalog: null, bindings: null, file: null, panel: "", loading: "", error: "", query: "", limit: 100});
  }
  function selection() {
    if (!tree || tree.workspace !== workspace()) return null;
    if (tree.mode === "application") return tree.application_path ? {workspace: tree.workspace, application_path: tree.application_path} : null;
    if (!tree.inspection?.id || !tree.domain?.name) return null;
    return {workspace: tree.workspace, inspection_id: tree.inspection.id, domain: tree.domain.name};
  }
  function identity(value, selected) {
    if (value?.workspace !== selected.workspace) return false;
    if (selected.application_path) return value.mode === "application" && value.application_path === selected.application_path;
    return value.mode !== "application" && value.inspection_id === selected.inspection_id && value.domain === selected.domain;
  }
  function knownFile(id) {
    return [...array(state.catalog?.files), ...array(state.bindings?.bindings).map((entry) => entry.file),
      ...array(state.file?.includes).flatMap((entry) => array(entry.files))].find((file) => file?.id === id);
  }
  function begin(panel, loading) {
    const selected = selection();
    if (disposed || !selected) return null;
    cancel();
    request = new AbortController();
    const captured = revision;
    const capturedTree = tree;
    Object.assign(state, {panel, loading, error: ""});
    return {selected, signal: request.signal,
      current: () => !disposed && revision === captured && tree === capturedTree && workspace() === selected.workspace};
  }
  async function catalog(operation) {
    if (state.catalog) return state.catalog;
    const endpoint = operation.selected.application_path ? "/api/inspector/application-sources" : "/api/inspector/sources";
    const response = await api(endpoint, {method: "POST", signal: operation.signal, body: operation.selected});
    if (!operation.current()) return null;
    if (!identity(response, operation.selected) || typeof response.catalog_id !== "string" || !response.catalog_id || !Array.isArray(response.files)) {
      throw new Error(`The source list does not belong to this ${operation.selected.application_path ? "application" : "image"}. Refresh the source list.`);
    }
    state.catalog = response;
    return response;
  }
  async function finish(operation, action) {
    try { await action(); }
    catch (error) {
      if (operation.current() && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (operation.current()) { state.loading = ""; publish(); }
    }
  }
  async function browse(refresh = false) {
    const operation = begin("sources", "catalog");
    if (!operation) return;
    if (refresh) Object.assign(state, {catalog: null, bindings: null, file: null, query: "", limit: 100});
    publish();
    await finish(operation, () => catalog(operation));
  }
  async function findBindings(path = nodePath) {
    if (tree?.mode === "application") return;
    if (!array(tree?.tree?.nodes).some((node) => node.path === path) || path !== nodePath) return;
    const operation = begin("bindings", "bindings");
    if (!operation) return;
    Object.assign(state, {bindings: null, file: null});
    publish();
    await finish(operation, async () => {
      const currentCatalog = await catalog(operation);
      if (!currentCatalog || !operation.current() || nodePath !== path) return;
      const response = await api("/api/inspector/bindings", {method: "POST", signal: operation.signal,
        body: {...operation.selected, catalog_id: currentCatalog.catalog_id, node_path: path}});
      if (!operation.current() || nodePath !== path) return;
      if (!identity(response, operation.selected) || response.catalog_id !== currentCatalog.catalog_id || response.node_path !== path || !Array.isArray(response.bindings)) {
        throw new Error("The binding results do not belong to the selected node. Find its bindings again.");
      }
      state.bindings = response;
    });
  }
  async function openFile(id) {
    const file = knownFile(id);
    if (!file || file.availability !== "available" || !state.catalog) return;
    const catalogId = state.catalog.catalog_id;
    const operation = begin(state.panel || "sources", "file");
    if (!operation) return;
    state.file = null;
    publish();
    await finish(operation, async () => {
      const endpoint = operation.selected.application_path ? "/api/inspector/application-source-file" : "/api/inspector/source-file";
      const response = await api(endpoint, {method: "POST", signal: operation.signal,
        body: {...operation.selected, catalog_id: catalogId, file_id: id}});
      if (!operation.current()) return;
      if (!identity(response, operation.selected) || response.catalog_id !== catalogId || response.file?.id !== id ||
          response.file.path !== file.path || response.document?.path !== file.path || typeof response.document.text !== "string" || !response.document.sha256 ||
          (file.sha256 && response.document.sha256 !== file.sha256) || (response.file.sha256 && response.file.sha256 !== response.document.sha256)) {
        throw new Error("The opened file does not match the source list. Refresh the source list.");
      }
      state.file = response;
    });
  }
  return {
    browse, findBindings, openFile,
    sync(data, selectedPath) {
      if (disposed) return;
      if (data !== tree || (data && data.workspace !== workspace())) { reset(); tree = data; state.mode = data?.mode === "application" ? "application" : "build"; }
      if (selectedPath !== nodePath) {
        nodePath = selectedPath;
        if (state.panel === "bindings") {
          cancel(); Object.assign(state, {bindings: null, file: null, loading: "", error: "", panel: ""});
        } else {
          state.bindings = null;
          if (state.file?.file?.kind === "binding") state.file = null;
        }
      }
    },
    search(query) { if (!disposed) { state.query = String(query); state.limit = 100; publish(); } },
    more() { if (!disposed) { state.limit += 100; publish(); } },
    close() { if (!disposed) { cancel(); Object.assign(state, {panel: "", file: null, loading: "", error: ""}); publish(); } },
    refresh() { return browse(true); },
    dispose() { disposed = true; reset(); tree = null; },
  };
}
