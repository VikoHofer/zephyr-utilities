/** The resolved tree stays bound to one workspace, observation and image. */
const array = (value) => Array.isArray(value) ? value : [];

export function inspectionRoute(view = "") {
  if (!view) return null;
  const parts = view.split("/");
  if (parts.length !== 3 || parts[0] !== "inspect" || !/^[a-f0-9]{32}$/.test(parts[1])) throw new Error("Open a build from Project Creator to inspect its Device Tree.");
  let domain;
  try { domain = decodeURIComponent(parts[2]); } catch { throw new Error("The image/domain link is invalid. Open the build again in Project Creator."); }
  if (!domain || domain.length > 256) throw new Error("Choose an image/domain from the build inspection.");
  return {id: parts[1], domain};
}

export function filterNodes(nodes, query = "") {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return array(nodes).filter((node) => {
    const compatible = array(node.properties).find((property) => property.name === "compatible")?.value || "";
    const searchable = [node.path, node.name, ...array(node.labels), compatible].join(" ").toLocaleLowerCase();
    return terms.every((term) => searchable.includes(term));
  });
}

export function createTreeSession({api, state, workspace, onChange, navigate}) {
  let disposed = false;
  let revision = 0;
  let request;
  let opened = false;
  Object.assign(state, {data: null, loading: false, error: "", limit: 200});
  state.selections ||= {};
  const publish = () => { if (!disposed) onChange(); };
  const key = () => `${state.inspectionId}/${state.domainName}`;
  function remember() {
    if (state.inspectionId && state.domainName) state.selections[key()] = {path: state.selectedPath, query: state.query || ""};
  }
  function followWorkspace() {
    const path = workspace();
    if (state.workspace === path) return false;
    revision++; request?.abort();
    Object.assign(state, {workspace: path, inspectionId: null, domainName: null, data: null,
      selectedPath: null, query: "", selections: {}, error: "", loading: false, limit: 200});
    opened = false;
    return true;
  }
  async function load(id, domain) {
    if (disposed) return;
    followWorkspace();
    remember();
    request?.abort(); request = new AbortController();
    const captured = ++revision;
    const path = state.workspace;
    Object.assign(state, {inspectionId: id, domainName: domain, data: null, error: "", loading: true, limit: 200});
    const choice = state.selections[key()] || {};
    state.query = choice.query || "";
    state.selectedPath = choice.path || null;
    opened = true;
    publish();
    const current = () => !disposed && captured === revision && path === workspace() && path === state.workspace;
    try {
      const data = await api("/api/devicetree/inspect", {method: "POST", signal: request.signal,
        body: {workspace: path, inspection_id: id, domain}});
      if (!current()) return;
      if (data.workspace !== path || data.inspection?.id !== id || data.domain?.name !== domain) throw new Error("The build inspection changed. Open the build again in Project Creator.");
      state.data = data;
      if (!array(data.tree?.nodes).some((node) => node.path === state.selectedPath)) state.selectedPath = array(data.tree?.nodes)[0]?.path || null;
      remember();
    } catch (error) {
      if (current() && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (current()) { state.loading = false; publish(); }
    }
  }
  async function open(view = "") {
    if (disposed) return;
    followWorkspace();
    try {
      const target = inspectionRoute(view) || (state.inspectionId && state.domainName ? {id: state.inspectionId, domain: state.domainName} : null);
      if (!target) { publish(); return; }
      if (opened && target.id === state.inspectionId && target.domain === state.domainName) return;
      await load(target.id, target.domain);
    } catch (error) {
      revision++; request?.abort();
      Object.assign(state, {data: null, error: error.message, loading: false});
      publish();
    }
  }
  function select(path) {
    if (disposed || !array(state.data?.tree?.nodes).some((node) => node.path === path)) return;
    state.selectedPath = path; remember(); publish();
  }
  function search(query) {
    if (disposed) return;
    state.query = String(query); state.limit = 200; remember(); publish();
  }
  function switchDomain(domain) {
    if (disposed || !array(state.data?.inspection?.domains).some((item) => item.name === domain)) return;
    navigate(`devicetree/inspect/${state.inspectionId}/${encodeURIComponent(domain)}`);
  }
  followWorkspace();
  return {open, select, search, switchDomain,
    refresh() { return state.inspectionId ? load(state.inspectionId, state.domainName) : Promise.resolve(); },
    more() { if (!disposed) { state.limit += 200; publish(); } },
    dispose() { remember(); disposed = true; revision++; request?.abort(); },
  };
}
