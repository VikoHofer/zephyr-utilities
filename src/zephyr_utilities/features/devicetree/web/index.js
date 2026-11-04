import {createTreeSession, filterNodes} from "./state.js";

export function mount(host, context) {
  const root = host.querySelector("[data-dt-root]");
  const $ = (name) => root.querySelector(`[data-dt-${name}]`);
  const lifetime = new AbortController();
  const state = context.state;
  const array = (value) => Array.isArray(value) ? value : [];
  let disposed = false;
  let session;
  let renderedData;
  let renderedQuery;
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function fact(parent, label, value, path = false) {
    const item = node("div", "");
    item.append(node("dt", "", label), node("dd", path ? "dt-path" : "", value || "Unknown"));
    parent.append(item);
  }
  function sourceList(parent, label, entries) {
    parent.append(node("h3", "", label));
    const list = node("ul", "dt-source-list");
    for (const value of array(entries)) list.append(node("li", "dt-path", typeof value === "string" ? value : JSON.stringify(value)));
    if (!list.childElementCount) parent.append(node("p", "dt-muted", "Not recorded in this build."));
    else parent.append(list);
  }
  function renderContext(data) {
    if (data === renderedData) return;
    renderedData = data;
    if (!data) return;
    const selected = data.domain;
    const build = selected.context || {};
    const select = $("domain"); select.replaceChildren();
    for (const domain of array(data.inspection.domains)) {
      const option = node("option", "", domain.name); option.value = domain.name; select.append(option);
    }
    select.value = selected.name;
    select.disabled = select.options.length < 2;
    const facts = $("context"); facts.replaceChildren();
    fact(facts, "Effective target", build.effective_target);
    fact(facts, "Application", build.application_source_dir, true);
    const sources = $("sources"); sources.replaceChildren();
    const paths = node("dl", "dt-facts");
    fact(paths, "Build directory", selected.build_dir, true);
    fact(paths, "Cached board", build.cached_board);
    fact(paths, "Zephyr checkout", build.zephyr_base, true);
    fact(paths, "Compiler", build.compiler, true);
    fact(paths, "Artifact check", selected.freshness);
    fact(paths, "Resolved DTS SHA-256", data.document.sha256, true);
    sources.append(paths);
    sourceList(sources, "Recorded Device Tree inputs", build.devicetree?.files);
    sourceList(sources, "User overlays", [...array(build.devicetree?.user_files), ...array(build.devicetree?.extra_user_files)]);
    sourceList(sources, "Binding search directories", build.devicetree?.binding_dirs);
    sourceList(sources, "Configuration inputs", build.configuration?.files);
    sources.append(node("p", "dt-muted", "Recorded input lists may be incomplete. The structural view does not resolve bindings or validate properties."));
    const diagnostics = $("diagnostics"); diagnostics.replaceChildren();
    for (const item of [...array(data.inspection.diagnostics), ...array(selected.diagnostics), ...array(data.tree.diagnostics)]) diagnostics.append(node("p", "dt-notice", item.message || item));
    $("document-path").textContent = data.document.path;
    $("document").textContent = data.document.text;
  }
  function renderNodes(data) {
    const nodes = array(data.tree?.nodes);
    $("browser").hidden = data.tree?.status !== "parsed";
    if ($("search").value !== state.query) $("search").value = state.query || "";
    const filtered = filterNodes(nodes, state.query);
    const shown = filtered.slice(0, state.limit);
    $("count").textContent = `${filtered.length.toLocaleString()} of ${nodes.length.toLocaleString()} nodes${filtered.length > shown.length ? ` · Showing ${shown.length}` : ""}`;
    $("more").hidden = shown.length >= filtered.length;
    const list = $("nodes");
    const scroll = renderedQuery === state.query ? list.scrollTop : 0;
    const focused = list.contains(document.activeElement) ? document.activeElement.dataset.nodePath : null;
    renderedQuery = state.query;
    list.replaceChildren();
    if (!shown.length) list.append(node("p", "dt-muted", "No nodes match this search."));
    for (const item of shown) {
      const button = node("button", `dt-node${item.path === state.selectedPath ? " selected" : ""}`);
      button.type = "button"; button.dataset.nodePath = item.path;
      button.setAttribute("aria-pressed", String(item.path === state.selectedPath));
      button.append(node("span", "dt-path", item.path));
      if (array(item.labels).length) button.append(node("span", "dt-muted", item.labels.join(", ")));
      list.append(button);
    }
    if (focused) [...list.querySelectorAll("[data-node-path]")].find((button) => button.dataset.nodePath === focused)?.focus({preventScroll: true});
    list.scrollTop = scroll;
    const selected = nodes.find((item) => item.path === state.selectedPath);
    const detail = $("detail"); detail.replaceChildren();
    if (!selected) { detail.append(node("p", "dt-muted", "Choose a node to inspect its properties.")); return; }
    detail.append(node("h3", "dt-path", selected.path), node("p", "dt-muted", `Resolved DTS line ${selected.line} · ${array(selected.properties).length} properties`));
    if (array(selected.labels).length) detail.append(node("p", "dt-labels", `Labels: ${selected.labels.join(", ")}`));
    if (!array(selected.properties).length) detail.append(node("p", "dt-muted", "This node has no properties."));
    for (const property of array(selected.properties)) {
      const row = node("div", "dt-property");
      row.append(node("h4", "dt-path", property.name), node("pre", "", property.boolean ? "Present (boolean property)" : property.value));
      detail.append(row);
    }
  }
  function render() {
    if (disposed) return;
    $("error").hidden = !state.error; $("error").textContent = state.error || "";
    $("loading").hidden = !state.loading;
    $("empty").hidden = Boolean(state.data || state.loading);
    $("results").hidden = !state.data;
    $("refresh").hidden = !state.inspectionId;
    $("refresh").disabled = state.loading;
    renderContext(state.data);
    if (state.data) renderNodes(state.data);
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  listen($("search"), "input", () => session.search($("search").value));
  listen($("domain"), "change", () => session.switchDomain($("domain").value));
  listen($("refresh"), "click", () => session.refresh());
  listen($("more"), "click", () => session.more());
  listen($("nodes"), "click", (event) => {
    const target = event.target.closest("[data-node-path]");
    if (target && root.contains(target)) session.select(target.dataset.nodePath);
  });
  session = createTreeSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render, navigate: context.navigate});
  render();
  return {navigate(view) { return session.open(view); }, dispose() { disposed = true; lifetime.abort(); session.dispose(); }};
}
