/** Read-only editor requests stay bound to one workspace and context revision. */
const PREFIX = "/api/devicetree/editor/";
const array = (value) => Array.isArray(value) ? value : [];

export function applicationDirectory(value, workspace) {
  if (typeof value !== "string" || !value.trim() || value.length > 4096 || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error("Enter an application directory in the selected workspace.");
  }
  const parts = value.trim().split("/");
  if (parts.includes("..")) throw new Error("Application paths must not contain parent traversal.");
  const base = workspace.replace(/\/+$/, "");
  const normalized = parts.filter((part) => part && part !== ".").join("/");
  const path = value.trim().startsWith("/") ? `/${normalized}` : `${base}/${normalized}`;
  if (!normalized || path === workspace || !path.startsWith(`${base}/`) || path.length > 4096) {
    throw new Error("Choose an application directory inside the selected workspace.");
  }
  return path;
}

export function createEditorSession({api, state, workspace, onChange}) {
  let disposed = false;
  let generation = 0;
  let catalogGeneration = 0;
  let request;
  let catalogRequest;
  let initialized = false;
  Object.assign(state, {data: null, document: null, catalog: null, loading: false,
    documentLoading: false, catalogLoading: false, error: "", documentError: "", catalogError: ""});
  const publish = () => { if (!disposed) onChange(); };

  function followWorkspace() {
    const path = workspace();
    if (state.workspace === path) return false;
    generation++; catalogGeneration++;
    request?.abort(); catalogRequest?.abort();
    Object.assign(state, {workspace: path, selection: null, contextRef: null, data: null,
      document: null, catalog: null, loading: false, documentLoading: false, catalogLoading: false,
      error: "", documentError: "", catalogError: ""});
    initialized = false;
    return true;
  }

  function current(captured, path) {
    return !disposed && captured === generation && path === workspace() && path === state.workspace;
  }

  async function catalog() {
    if (disposed) return false;
    followWorkspace();
    catalogRequest?.abort(); catalogRequest = new AbortController();
    const captured = ++catalogGeneration;
    const path = state.workspace;
    const active = () => !disposed && captured === catalogGeneration && path === workspace() && path === state.workspace;
    Object.assign(state, {catalogLoading: true, catalogError: "", catalog: null}); publish();
    try {
      const data = await api(PREFIX + "catalog", {method: "POST", signal: catalogRequest.signal, body: {workspace: path}});
      if (!active()) return false;
      if (data.workspace !== path || !Array.isArray(data.projects)) throw new Error("The application catalogue changed. Refresh it again.");
      state.catalog = data;
    } catch (error) {
      if (active() && error.name !== "AbortError") state.catalogError = error.message || String(error);
    } finally {
      if (active()) { state.catalogLoading = false; publish(); }
    }
    return active();
  }

  function verifyContext(data, path, expected, intent) {
    if (data.schema_version !== 1 || data.workspace !== path || typeof data.id !== "string" || !data.id
        || !Number.isInteger(data.revision) || data.revision < 1) {
      throw new Error("The editor context changed. Open the application again.");
    }
    if (expected && (data.id !== expected.id || data.revision !== expected.revision)) {
      throw new Error("The editor context revision changed. Open the application again.");
    }
    if (intent?.kind === "project" && (data.project?.id !== intent.project_id || data.configuration?.id !== intent.configuration_id)) {
      throw new Error("The selected application or configuration changed. Open it again.");
    }
    if (intent?.kind === "application" && (data.application?.path !== intent.application_path || data.project != null)) {
      throw new Error("The application directory changed. Choose it again.");
    }
  }

  async function readDocument(data, captured, path, signal) {
    const selected = data.editing_document;
    if (!selected || selected.availability !== "available") return;
    Object.assign(state, {documentLoading: true, document: null, documentError: ""}); publish();
    try {
      const response = await api(PREFIX + "document", {method: "POST", signal,
        body: {workspace: path, context_id: data.id, expected_revision: data.revision, document_id: selected.id}});
      if (!current(captured, path)) return;
      const document = response.document;
      if (response.context_id !== data.id || response.revision !== data.revision || document?.id !== selected.id
          || document.path !== selected.path || typeof document.text !== "string"
          || (selected.sha256 && document.sha256 !== selected.sha256)) {
        throw new Error("The overlay changed. Open the application again before reading it.");
      }
      state.document = document;
    } catch (error) {
      if (current(captured, path) && error.name !== "AbortError") state.documentError = error.message || String(error);
    } finally {
      if (current(captured, path)) { state.documentLoading = false; publish(); }
    }
  }

  async function load(operation, payload, {intent = state.selection, expected = null} = {}) {
    if (disposed) return false;
    followWorkspace();
    request?.abort(); request = new AbortController();
    const signal = request.signal;
    const captured = ++generation;
    const path = state.workspace;
    Object.assign(state, {loading: true, documentLoading: false, error: "", documentError: "", data: null, document: null});
    if (operation === "open") { state.selection = intent; state.contextRef = null; }
    publish();
    try {
      const data = await api(PREFIX + operation, {method: "POST", signal, body: {workspace: path, ...payload}});
      if (!current(captured, path)) return false;
      verifyContext(data, path, expected, intent);
      state.contextRef = {id: data.id, revision: data.revision};
      state.data = data; state.loading = false; publish();
      await readDocument(data, captured, path, signal);
    } catch (error) {
      if (current(captured, path) && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (current(captured, path)) { state.loading = false; publish(); }
    }
    return current(captured, path);
  }

  function invalid(message) {
    generation++; request?.abort();
    Object.assign(state, {data: null, document: null, loading: false, documentLoading: false, error: message, documentError: ""});
    publish();
    return Promise.resolve(true);
  }

  function openProject(projectId, configurationId = null) {
    if (disposed) return Promise.resolve(false);
    followWorkspace();
    const project = array(state.catalog?.projects).find((item) => item.id === projectId);
    if (!project || !array(project.configurations).some((item) => item.id === configurationId)) {
      return invalid("Choose an application and configuration from the current catalogue.");
    }
    return load("open", {project_id: projectId, configuration_id: configurationId},
      {intent: {kind: "project", project_id: projectId, configuration_id: configurationId}});
  }

  function openApplication(value) {
    if (disposed) return Promise.resolve(false);
    followWorkspace();
    try {
      const path = applicationDirectory(value, state.workspace);
      return load("open", {application_path: path}, {intent: {kind: "application", application_path: path}});
    } catch (error) { return invalid(error.message); }
  }

  function revalidate() {
    if (disposed) return Promise.resolve(false);
    followWorkspace();
    const ref = state.contextRef;
    if (!ref) return Promise.resolve(false);
    return load("context", {context_id: ref.id, expected_revision: ref.revision}, {expected: ref});
  }

  function selectDocument(documentId) {
    if (disposed) return Promise.resolve(false);
    followWorkspace();
    const data = state.data;
    if (!data || !array(data.documents).some((item) => item.id === documentId)) {
      return invalid("Choose an overlay from the current application context.");
    }
    return load("select", {context_id: data.id, expected_revision: data.revision, document_id: documentId},
      {expected: {id: data.id, revision: data.revision + 1}});
  }

  followWorkspace();
  return {catalog, openProject, openApplication, selectDocument, revalidate,
    async navigate(view = "editor") {
      if (disposed) return false;
      const changed = followWorkspace();
      if (view && view !== "editor") return invalid("Open the Device Tree Editor workspace to choose an application.");
      if (initialized && !changed) return false;
      initialized = true;
      const restore = state.contextRef ? revalidate() : Promise.resolve(false);
      await Promise.all([catalog(), restore]);
      return !disposed;
    },
    dispose() { disposed = true; generation++; catalogGeneration++; request?.abort(); catalogRequest?.abort(); },
  };
}
