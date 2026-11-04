import {createSourceNavigation, filterSourceFiles} from "./navigation-state.js";

export function createSourceView(root, context) {
  const $ = (name) => root.querySelector(`[data-dt-${name}]`);
  const array = (value) => Array.isArray(value) ? value : [];
  const lifetime = new AbortController();
  const state = context.state;
  let disposed = false;
  let renderedFile;
  let session;
  let selection;
  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  }
  function textValue(value) { return typeof value === "string" ? value : JSON.stringify(value); }
  function openButton(file, label = "Open file") {
    const button = element("button", "dt-button", label);
    button.type = "button"; button.dataset.sourceFileId = file.id;
    button.disabled = file.availability !== "available" || Boolean(state.loading);
    button.setAttribute("aria-label", `${label} ${file.name || file.path}`);
    return button;
  }
  function diagnostics(target, values) {
    target.replaceChildren();
    const messages = array(values).filter(Boolean);
    target.hidden = !messages.length;
    if (!messages.length) return;
    const details = element("details");
    details.append(element("summary", "", `Navigation details (${messages.length})`));
    const list = element("ul", "dt-source-list");
    for (const message of messages) list.append(element("li", "", typeof message === "string" ? message : message.message || JSON.stringify(message)));
    details.append(list); target.append(details);
  }
  function fileRow(file) {
    const row = element("div", "dt-source-row");
    const content = element("div", "dt-source-description");
    content.append(element("strong", "", file.name || file.path), element("p", "dt-path", file.path));
    const origins = array(file.origins);
    if (origins.length) content.append(element("p", "dt-muted", origins.join(" · ")));
    if (file.availability !== "available") content.append(element("p", "dt-muted", file.reason || `File ${file.availability || "unavailable"}.`));
    row.append(content, openButton(file));
    return row;
  }
  function renderSources() {
    $("source-files").hidden = state.panel !== "sources";
    if (state.panel !== "sources") return;
    if ($("source-search").value !== state.query) $("source-search").value = state.query;
    const files = filterSourceFiles(state.catalog?.files, state.query);
    const shown = files.slice(0, state.limit);
    $("source-count").textContent = state.catalog ? `${files.length.toLocaleString()} files${shown.length < files.length ? ` · Showing ${shown.length}` : ""}` : "";
    $("source-more").hidden = shown.length >= files.length;
    const target = $("source-list"); target.replaceChildren();
    for (const file of shown) target.append(fileRow(file));
    if (state.catalog && !shown.length) target.append(element("p", "dt-muted", state.query ? "No source files match this search." : state.mode === "application" ? "No Device Tree files, configuration fragments or local bindings were found in this application." : "No source files are available from this build's recorded inputs."));
    diagnostics($("source-diagnostics"), state.catalog?.diagnostics);
  }
  function renderBindings() {
    $("binding-results").hidden = state.panel !== "bindings";
    if (state.panel !== "bindings") return;
    const result = state.bindings;
    $("binding-node").textContent = result?.node_path || "";
    $("binding-compatible").textContent = result ? array(result.compatibles).join(", ") || "No compatible strings were recorded for this node." : "";
    const list = $("binding-list"); list.replaceChildren();
    for (const binding of array(result?.bindings)) {
      const card = element("article", "dt-binding-card");
      const header = element("div", "dt-binding-heading");
      header.append(element("h3", "", binding.file.name || binding.file.path),
        element("span", "dt-badge", binding.evidence === "recorded-binding" ? "Recorded binding" : "Compatible candidate"));
      card.append(header, element("p", "dt-path", binding.file.path));
      if (binding.compatible) card.append(element("p", "dt-muted", `Compatible: ${binding.compatible}`));
      if (binding.on_bus) card.append(element("p", "dt-muted", `On bus: ${textValue(binding.on_bus)}`));
      if (binding.bus) card.append(element("p", "dt-muted", `Provides bus: ${textValue(binding.bus)}`));
      if (binding.description) card.append(element("p", "dt-binding-description", binding.description));
      if (binding.file.reason) card.append(element("p", "dt-muted", binding.file.reason));
      card.append(openButton(binding.file, "Open binding")); list.append(card);
    }
    if (result && !array(result.bindings).length) list.append(element("p", "dt-muted", "No binding candidates were found in the recorded binding directories."));
    diagnostics($("binding-diagnostics"), [...array(state.catalog?.diagnostics), ...array(result?.diagnostics)]);
  }
  function renderFile() {
    const file = state.file;
    $("source-document").hidden = !file;
    if (file === renderedFile) return;
    renderedFile = file;
    if (!file) return;
    $("source-file-title").textContent = file.file.name || "Current file";
    $("source-file-path").textContent = file.document.path;
    $("source-file-text").textContent = file.document.text;
    $("source-file-hash").textContent = file.document.sha256;
    $("source-file-kind").textContent = file.file.kind || "source";
    $("source-file-details").open = false;
    const includes = $("source-includes"); includes.replaceChildren();
    for (const item of array(file.includes)) {
      const row = element("div", "dt-include-row");
      row.append(element("strong", "dt-path", item.name));
      if (item.note) row.append(element("p", "dt-muted", item.note));
      if (!array(item.files).length) row.append(element("p", "dt-muted", "No navigable file was found for this include."));
      for (const included of array(item.files)) row.append(fileRow(included));
      includes.append(row);
    }
    $("source-includes-section").hidden = !array(file.includes).length;
    const properties = $("binding-properties"); properties.replaceChildren();
    for (const property of array(file.properties)) {
      const row = element("div", "dt-binding-property");
      row.append(element("h4", "dt-path", property.name));
      const labels = [property.type, property.required === true ? "required" : property.required === false ? "optional" : null].filter(Boolean);
      if (labels.length) row.append(element("p", "dt-muted", labels.join(" · ")));
      if (property.description) row.append(element("p", "", property.description));
      if (property.enum !== undefined) row.append(element("p", "dt-muted", `Allowed values: ${textValue(property.enum)}`));
      if (property.default !== undefined) row.append(element("p", "dt-muted", `Default: ${textValue(property.default)}`));
      properties.append(row);
    }
    $("binding-properties-section").hidden = !array(file.properties).length;
    $("binding-properties-section").open = false;
    diagnostics($("source-file-diagnostics"), file.diagnostics);
  }
  function render() {
    if (disposed) return;
    $("source-navigation").hidden = !state.panel;
    $("source-close").hidden = state.mode === "application";
    $("source-notice").textContent = state.mode === "application"
      ? "Browse current application files before configuring. Include links are candidates; the files used by a build and the resolved hardware are unknown."
      : "These are current files. They may differ from the files used to build this image. Source editing and binding validation remain planned.";
    $("source-navigation-title").textContent = state.panel === "bindings" ? "Bindings for the selected node" : "Current source files";
    $("source-loading").hidden = !state.loading;
    $("source-loading").textContent = state.loading === "file" ? "Reading the current file…" : state.loading === "bindings" ? "Finding bindings in the recorded directories…" : state.mode === "application" ? "Finding application source files…" : "Reading the recorded source list…";
    $("source-error").hidden = !state.error; $("source-error").textContent = state.error;
    $("source-refresh").disabled = Boolean(state.loading);
    $("source-search").disabled = !state.catalog || Boolean(state.loading);
    renderSources(); renderBindings(); renderFile();
  }
  const listen = (target, event, callback) => target.addEventListener(event, callback, {signal: lifetime.signal});
  listen($("browse-sources"), "click", () => { session.browse(); $("source-navigation").scrollIntoView({block: "start"}); });
  listen($("source-refresh"), "click", () => session.refresh());
  listen($("source-close"), "click", () => session.close());
  listen($("source-search"), "input", () => session.search($("source-search").value));
  listen($("source-more"), "click", () => session.more());
  listen($("source-navigation"), "click", (event) => {
    const target = event.target.closest("[data-source-file-id]");
    if (target && root.contains(target)) {
      const id = target.dataset.sourceFileId;
      session.openFile(id).then(() => {
        if (!disposed && state.file?.file.id === id) {
          $("source-file-text").focus({preventScroll: true});
          $("source-document").scrollIntoView({block: "start"});
        }
      });
    }
  });
  session = createSourceNavigation({api: context.api, state, workspace: context.workspace, onChange: render});
  render();
  return {
    sync(data, path) {
      const changed = data !== selection; selection = data;
      session.sync(data, path); $("browse-sources").disabled = !data || data.mode === "application";
      if (changed && data?.mode === "application") session.browse();
      render();
    },
    findBindings(path) { session.findBindings(path); $("source-navigation").scrollIntoView({block: "start"}); },
    dispose() { disposed = true; lifetime.abort(); session.dispose(); },
  };
}
