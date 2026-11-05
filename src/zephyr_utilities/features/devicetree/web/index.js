import {createEditorSession} from "./state.js";
import {createDraftSession, editorText, nodeSelection, propertySelection, restoreNodeSelection,
  restorePropertySelection, sourceOffset, sourceSelection} from "./drafts.js";

const array = (value) => Array.isArray(value) ? value : [];
const text = (value, fallback = "Unknown") => value == null || value === "" ? fallback : String(value);

export function mount(host, context) {
  const root = host.querySelector("[data-dte-root]");
  const $ = (name) => root.querySelector(`[data-dte-${name}]`);
  const document = root.ownerDocument;
  const lifetime = new AbortController();
  const state = context.state;
  let disposed = false;
  let actionGeneration = 0;
  let renderedWorkspace;
  let renderedCatalog;
  let renderedData;
  let renderedDraftControls;
  let session;
  let drafts;

  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = String(value);
    return element;
  }
  function option(select, value, label) {
    const element = node("option", "", label); element.value = value; select.append(element);
  }
  function fact(parent, label, value, path = false) {
    const item = node("div");
    item.append(node("dt", "", label), node("dd", path ? "dte-path" : "", text(value)));
    parent.append(item);
  }
  function notice(parent, message) { parent.append(node("p", "dte-notice", message)); }
  function list(parent, values, ordered = false) {
    const element = node(ordered ? "ol" : "ul", "dte-list");
    for (const value of values) element.append(node("li", "", value));
    parent.append(element);
  }
  function configurations() {
    const project = array(state.catalog?.projects).find((item) => item.id === $("project").value);
    const select = $("configuration"); select.replaceChildren();
    for (const entry of array(project?.configurations)) option(select, entry.id || "", entry.name || "Original");
    const desired = state.formConfigurationId || "";
    if (array(project?.configurations).some((item) => (item.id || "") === desired)) select.value = desired;
    select.disabled = !project || state.catalogLoading;
    $("open-project").disabled = !project || !select.options.length || state.loading;
  }
  function renderCatalog() {
    if (renderedCatalog !== state.catalog) {
      renderedCatalog = state.catalog;
      const select = $("project"); select.replaceChildren();
      option(select, "", "Choose an application");
      for (const project of array(state.catalog?.projects)) {
        const unavailable = project.availability?.state && project.availability.state !== "available";
        option(select, project.id, `${project.name || project.path}${unavailable ? " · Unavailable" : ""}`);
      }
      const desired = state.formProjectId || state.selection?.project_id || "";
      if (array(state.catalog?.projects).some((item) => item.id === desired)) select.value = desired;
      configurations();
      const diagnostics = $("catalog-diagnostics"); diagnostics.replaceChildren();
      for (const entry of array(state.catalog?.diagnostics)) notice(diagnostics, entry.message || entry);
    }
    $("project").disabled = state.catalogLoading || !array(state.catalog?.projects).length;
    $("configuration").disabled = state.catalogLoading || !$("project").value;
    $("open-project").disabled = state.loading || state.catalogLoading || !$("project").value || !$("configuration").options.length;
    $("open-project").textContent = state.loading && state.selection?.kind === "project" ? "Opening…" : "Open application";
    $("refresh-catalog").disabled = state.catalogLoading;
    $("refresh-catalog").textContent = state.catalogLoading ? "Refreshing…" : "Refresh list";
    const count = array(state.catalog?.projects).length;
    $("catalog-status").textContent = state.catalogLoading ? "Loading registered applications…"
      : !state.catalogError ? count ? `${count} registered application${count === 1 ? "" : "s"} in this workspace.`
        : "No registered applications in this workspace. You can open an application directory below." : "";
    $("catalog-error").hidden = !state.catalogError;
    $("catalog-error").textContent = state.catalogError || "";
  }
  function renderEvidence(data) {
    const container = $("evidence"); container.replaceChildren();
    for (const [kind, title] of [["predicted", "Predicted inputs"], ["captured", "Captured inputs"]]) {
      const records = array(data.input_evidence?.[kind]);
      if (!records.length) {
        container.append(node("p", "dte-muted", `${title}: not recorded.`));
        continue;
      }
      for (const record of records) {
        const group = node("section", "dte-evidence");
        group.append(node("h4", "", title));
        if (record.message) group.append(node("p", "dte-muted", record.message));
        if (record.target) group.append(node("p", "dte-path", `Target: ${record.target}`));
        const provenance = record.provenance || {};
        const provenanceText = [provenance.kind, provenance.attempt_id ? `Attempt ${provenance.attempt_id}` : "",
          provenance.captured_at, provenance.configuration_revision != null ? `Configuration revision ${provenance.configuration_revision}` : ""].filter(Boolean).join(" · ");
        if (provenanceText) group.append(node("p", "dte-muted", provenanceText));
        const files = array(record.files).map((file) => typeof file === "string" ? file
          : [file.path, file.state, file.role, file.reason].filter(Boolean).join(" · "));
        if (files.length) list(group, files);
        else group.append(node("p", "dte-muted", "No overlay paths recorded in this evidence."));
        container.append(group);
      }
    }
    container.append(node("p", "dte-muted", `Input coverage: ${text(data.input_evidence?.coverage, "unknown")}.`));
  }
  function renderEnvironment(data) {
    const container = $("environment"); container.replaceChildren();
    const facts = node("dl", "dte-facts dte-small-facts");
    const environment = data.environment || {};
    fact(facts, "Environment observation", environment.observation_id || environment.id);
    fact(facts, "Environment freshness", environment.freshness || environment.state);
    fact(facts, "Zephyr checkout", environment.zephyr_base || environment.zephyr?.path, true);
    fact(facts, "Selected SDK", [environment.sdk?.path, environment.sdk?.version].filter(Boolean).join(" · "), true);
    fact(facts, "Toolchain", environment.toolchain?.name || environment.sdk?.toolchain_variant || environment.toolchain_variant);
    container.append(facts);
    if (!data.environment) container.append(node("p", "dte-muted", "No selected environment is known for this directory."));
    const roots = array(data.roots);
    if (roots.length) list(container, roots.map((entry) => [entry.kind, entry.path, entry.origin,
      typeof entry.availability === "string" ? entry.availability : entry.availability?.state].filter(Boolean).join(" · ")));
    else container.append(node("p", "dte-muted", "No root selection recorded."));
  }
  function renderContext(data) {
    if (renderedData === data) return;
    renderedData = data;
    if (!data) return;
    $("context-title").textContent = [data.application?.name || data.application?.path, data.configuration?.name,
      data.target?.requested].filter(Boolean).join(" · ");
    const facts = $("facts"); facts.replaceChildren();
    fact(facts, "Application", data.application?.name || data.application?.path, true);
    fact(facts, "Exact target", data.target?.requested, true);
    fact(facts, "Configuration", data.configuration?.name, false);
    fact(facts, "Application directory", data.application?.path, true);
    fact(facts, "Availability", data.application?.availability?.state);
    fact(facts, "Image / domain", data.image?.domain || data.build_evidence?.domain);
    fact(facts, "Selected Zephyr", [data.environment?.zephyr?.path || data.environment?.zephyr_base,
      data.environment?.zephyr?.version].filter(Boolean).join(" · "), true);
    fact(facts, "Selected SDK", [data.environment?.sdk?.path, data.environment?.sdk?.version].filter(Boolean).join(" · "), true);
    fact(facts, "Environment evidence", data.environment?.freshness || data.environment?.state);
    const diagnostics = $("diagnostics"); diagnostics.replaceChildren();
    if (data.application?.availability?.reason) notice(diagnostics, data.application.availability.reason);
    for (const entry of array(data.diagnostics)) notice(diagnostics, entry.message || entry);

    const select = $("overlay"); select.replaceChildren(); option(select, "", "Choose an overlay");
    for (const entry of array(data.documents)) option(select, entry.id,
      `${entry.relative_path || entry.path}${entry.availability !== "available" ? ` · ${entry.availability}` : ""}`);
    select.value = data.editing_document?.id || "";
    select.disabled = !array(data.documents).length;
    const selected = data.editing_document;
    $("overlay-status").textContent = data.application?.availability?.state !== "available"
      ? data.application?.availability?.reason || "Application sources are unavailable. Restore the selected source directory and reopen it."
      : !array(data.documents).length ? "No application overlays were found. Source creation is planned for a later stage."
      : selected ? "Draft changes stay in this session. Selecting an overlay does not activate it in the build recipe."
        : "Choose the overlay to inspect. A file being present does not prove that the selected build uses it.";
    const documentFacts = $("document-facts"); documentFacts.replaceChildren();
    if (selected) {
      fact(documentFacts, "Selected overlay", selected.relative_path || selected.path, true);
      fact(documentFacts, "Selection evidence", selected.usage);
      fact(documentFacts, "Availability", selected.availability);
      if (selected.reason) notice(documentFacts, selected.reason);
      if (array(selected.origins).length) fact(documentFacts, "Origin", selected.origins.join(" · "));
    }
    const fragments = $("fragments"); fragments.replaceChildren();
    const overlays = data.fragments?.overlays;
    if (!overlays) fragments.append(node("p", "dte-muted", "Unknown for this source-only context."));
    else {
      const modes = {auto: "Automatic discovery", append: "Append selected overlays", replace: "Replace automatic overlays"};
      fragments.append(node("p", "", modes[overlays.mode] || text(overlays.mode)));
      if (array(overlays.files).length) list(fragments, overlays.files, true);
      else fragments.append(node("p", "dte-muted", "No explicit overlay files requested."));
    }
    renderEvidence(data); renderEnvironment(data);
  }

  function draftEntry() { return state.draftView?.entry; }
  function selectedNode() {
    const entry = draftEntry();
    return array(entry?.server?.nodes)[entry?.selectedNodeIndex || 0];
  }
  function selectedProperty() {
    const entry = draftEntry();
    return entry?.selectedPropertyIndex >= 0 ? array(selectedNode()?.properties)[entry.selectedPropertyIndex] : null;
  }
  function populateProperty() {
    const property = selectedProperty();
    $("property-name").value = property?.name || "";
    $("property-kind").value = property?.value?.kind || "string";
    $("property-value").value = property?.value?.kind === "cells" ? array(property.value.value).join(" ")
      : property?.value?.kind === "string" ? property.value.value : "";
    propertyControls();
  }
  function propertyControls() {
    const node = selectedNode();
    const property = selectedProperty();
    const disabled = !node || node.editable === false || Boolean(property && !property.editable);
    for (const name of ["property-name", "property-kind", "property-value", "apply-property"]) $(name).disabled = disabled;
    $("property-name").readOnly = Boolean(property);
    $("remove-property").disabled = disabled || !property;
    $("value-label").hidden = $("property-kind").value === "boolean";
    $("property-help").textContent = property && !property.editable ? "This source value is opaque or conflicts with another declaration. Edit it in source."
      : $("property-kind").value === "cells" ? "Enter unsigned 32-bit integers separated by spaces. Decimal and 0x hexadecimal are accepted."
        : $("property-kind").value === "boolean" ? "A boolean property is present without an assigned value."
          : "String contents are escaped as data. Removing a local property may reveal an inherited value.";
  }
  function renderDraft() {
    const view = state.draftView;
    const entry = view?.entry;
    const server = entry?.server;
    const pending = drafts?.pending();
    const busy = Boolean(view?.loading || entry?.flight || entry?.historyOperation);
    const error = view?.error || entry?.error || "";
    const available = Boolean(view?.ready && server && !pending && !busy && !entry.uncertain && !entry.conflict && !server.syntax.blocked);
    $("draft-status").textContent = view?.loading ? "Opening and revalidating the draft…"
      : entry?.uncertain ? "The last request needs reconciliation. Your local text is retained."
        : busy ? "Updating the session draft…" : pending ? "Local typing is waiting to sync. Visual controls are paused."
          : server ? `Session draft revision ${server.revision}. No file has been written.${server.history_truncated ? " Earlier undo history reached its limit." : ""}`
            : "Choose an available overlay to begin. Drafts are retained until this application session ends.";
    $("dirty").textContent = pending || server?.dirty ? "Unsaved changes" : "Unchanged";
    $("draft-error").hidden = !error; $("draft-error").textContent = error;
    $("conflict").hidden = !entry?.conflict;
    $("server-source").textContent = entry?.conflict ? server?.text || "" : "";
    $("use-local").disabled = busy; $("use-server").disabled = busy;
    $("reconcile").hidden = !entry?.uncertain || entry?.conflict;
    $("reconcile").disabled = busy;
    $("sync").disabled = !view?.ready || busy || !pending || entry?.conflict;
    $("undo").disabled = !view?.ready || busy || entry?.conflict || (!pending && !server?.can_undo);
    $("redo").disabled = !view?.ready || busy || entry?.conflict || (!pending && !server?.can_redo);
    $("source").disabled = !view?.ready;
    $("source").readOnly = Boolean(entry?.historyOperation || (entry?.flight && entry.flightKind && entry.flightKind !== "source"));
    const value = entry ? editorText(entry.text) : state.document ? editorText(state.document.text) : "";
    if ($("source").value !== value) {
      const start = $("source").selectionStart; const end = $("source").selectionEnd;
      $("source").value = value;
      if (document.activeElement === $("source")) $("source").setSelectionRange(Math.min(start, value.length), Math.min(end, value.length));
    }
    $("source-path").textContent = entry?.path || state.document?.path || state.data?.editing_document?.path || "";
    $("source-error").hidden = !state.documentError; $("source-error").textContent = state.documentError || "";
    $("source-status").textContent = server ? "Typing updates this draft after a short pause. Both source and visual changes share Undo/Redo."
      : state.documentLoading ? "Reading current overlay source…" : "Choose an available overlay to read its source.";
    $("visual-controls").hidden = !available || !array(server?.nodes).length;
    $("visual-notice").hidden = available && array(server?.nodes).length > 0;
    $("visual-notice").textContent = !view?.ready ? "Open an overlay to inspect its declarations."
      : pending || busy ? "Waiting for the current source revision; previous declarations are hidden."
        : entry?.uncertain || entry?.conflict ? "Reconcile the draft before making visual changes."
          : server?.syntax.blocked ? "The current source is incomplete or outside visual support. Your text is retained; correct it in source or use Undo."
            : "This overlay has no node declarations. Add a node or reference block in source to use visual controls.";
    const diagnostics = $("syntax-diagnostics"); diagnostics.replaceChildren();
    if (!pending) for (const item of array(server?.syntax?.diagnostics)) notice(diagnostics, item.message || item.code);
    const oldHint = entry?.nodeHint;
    if (entry && server && entry.renderedRevision !== server.revision) {
      entry.renderedRevision = server.revision;
      if (oldHint) {
        entry.selectedNodeIndex = restoreNodeSelection(server.nodes, oldHint);
        if (entry.selectedNodeIndex < 0 && !server.syntax.blocked) { entry.nodeHint = null; entry.selectionLost = true; }
      } else {
        entry.selectedNodeIndex = entry.selectionLost ? -1 : 0;
        entry.nodeHint = nodeSelection(server.nodes, entry.selectedNodeIndex);
      }
      entry.selectedPropertyIndex = restorePropertySelection(array(selectedNode()?.properties), entry.propertyHint);
      if (entry.selectedPropertyIndex < 0 && !server.syntax.blocked) entry.propertyHint = null;
    }
    const controlKey = `${server?.id}:${server?.revision}:${available}:${entry?.selectedNodeIndex || 0}:${entry?.selectedPropertyIndex ?? -1}`;
    if (available && renderedDraftControls !== controlKey) {
      renderedDraftControls = controlKey;
      const select = $("node"); select.replaceChildren();
      array(server.nodes).forEach((item, index) => option(select, String(index), `${item.labels?.length ? item.labels.join(", ") + ": " : ""}${item.name} · occurrence ${index + 1}`));
      select.value = String(entry.selectedNodeIndex || 0);
      const node = selectedNode();
      const statuses = array(node?.properties).filter((property) => property.name === "status");
      $("node-status").textContent = !node ? "Choose a node occurrence to edit its properties." : statuses.length > 1 ? "Multiple local status assignments; choose a property occurrence below."
        : statuses.length ? `Local status: ${text(statuses[0].value?.value, "Opaque or deleted")}` : "Local status: not specified in this block.";
      $("enable").disabled = $("disable").disabled = !node || node.editable === false || statuses.length > 1 || statuses[0]?.editable === false;
      $("show-source").disabled = !node;
      const properties = $("property"); properties.replaceChildren(); option(properties, "", "New property");
      array(node?.properties).forEach((item, index) => option(properties, String(index), `${item.name}${!item.editable ? " · source only" : ""}`));
      properties.value = entry.selectedPropertyIndex >= 0 ? String(entry.selectedPropertyIndex) : "";
      populateProperty();
    }
    $("diff").textContent = server?.diff?.text || "No acknowledged source changes.";
    $("diff-status").textContent = pending ? "The diff reflects the last acknowledged draft; your latest typing is still pending."
      : server?.diff?.truncated ? "The diff is shortened to keep this view responsive. The complete draft remains in the source editor."
        : "Compared with the original overlay bytes. This does not write the file.";
    renderRetained();
  }
  function renderRetained() {
    const entries = Object.entries(state.draftCache || {});
    $("retained-panel").hidden = !entries.length;
    $("retained-summary").textContent = `Retained drafts (${entries.length})`;
    const selected = $("retained").value;
    const select = $("retained"); select.replaceChildren();
    for (const [key, entry] of entries) option(select, key, `${entry.label} · ${entry.path}${entry.text !== entry.server?.text || entry.server?.dirty ? " · Changed" : ""}`);
    if (entries.some(([key]) => key === selected)) select.value = selected;
    if (!$("retained-source").hidden) $("retained-source").value = editorText(state.draftCache[select.value]?.text || "");
  }
  function render() {
    if (disposed) return;
    if (renderedWorkspace !== state.workspace) {
      renderedWorkspace = state.workspace; renderedCatalog = undefined; renderedData = undefined;
      if (state.selection?.kind === "project") {
        state.formProjectId = state.selection.project_id; state.formConfigurationId = state.selection.configuration_id;
      } else { state.formProjectId = ""; state.formConfigurationId = null; }
      $("directory").value = state.selection?.kind === "application" ? state.selection.application_path : "";
    }
    renderCatalog(); renderContext(state.data);
    void drafts?.bind(state.data, state.document);
    $("loading").hidden = !state.loading;
    $("error").hidden = !state.error; $("error").textContent = state.error || "";
    $("empty").hidden = Boolean(state.data || state.loading || state.error);
    $("results").hidden = !state.data;
    $("open-directory").disabled = state.loading;
    $("open-directory").textContent = state.loading && state.selection?.kind === "application" ? "Opening…" : "Open directory";
    $("recheck").disabled = state.loading || state.documentLoading;
    $("select-overlay").disabled = state.loading || state.documentLoading || !$("overlay").value;
    $("select-overlay").textContent = state.documentLoading ? "Reading…" : "Open overlay draft";
    renderDraft();
  }
  async function action(operation, destination) {
    const captured = ++actionGeneration;
    const workspace = context.workspace.path;
    const accepted = await operation();
    if (disposed || captured !== actionGeneration || workspace !== context.workspace.path) return;
    if (!accepted && !state.error && !state.documentError && !state.draftView?.error && !draftEntry()?.error) return;
    const target = state.error ? $("error") : state.documentError ? $("source-error")
      : state.draftView?.error || draftEntry()?.error ? $("draft-error")
        : state.data?.application?.availability?.state !== "available" ? $("overlay-status") : destination();
    if (target) context.reveal?.(target);
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  listen($("project"), "change", () => { state.formProjectId = $("project").value; state.formConfigurationId = null; configurations(); });
  listen($("configuration"), "change", () => { state.formConfigurationId = $("configuration").value || null; });
  listen($("project-form"), "submit", (event) => {
    event.preventDefault();
    void action(() => session.openProject($("project").value, $("configuration").value || null), () => $("draft-panel"));
  });
  listen($("directory-form"), "submit", (event) => {
    event.preventDefault(); void action(() => session.openApplication($("directory").value), () => $("draft-panel"));
  });
  listen($("overlay"), "change", () => { $("select-overlay").disabled = !$("overlay").value || state.loading || state.documentLoading; });
  listen($("overlay-form"), "submit", (event) => {
    event.preventDefault(); void action(() => session.selectDocument($("overlay").value), () => $("draft-panel"));
  });
  listen($("refresh-catalog"), "click", () => {
    void action(() => session.catalog(), () => state.catalogError ? $("catalog-error") : null);
  });
  listen($("recheck"), "click", () => { void action(() => session.revalidate(), () => $("context-panel")); });
  listen($("source"), "input", () => { drafts.type($("source").value); });
  const history = (kind, keyboard = false) => action(() => drafts[kind](), () => $(keyboard ? "source" : "draft-status"));
  listen($("source"), "keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === "z" || key === "y") { event.preventDefault(); void history(key === "y" || event.shiftKey ? "redo" : "undo", true); }
  });
  listen($("undo"), "click", () => { void history("undo"); });
  listen($("redo"), "click", () => { void history("redo"); });
  listen($("sync"), "click", () => { void action(() => drafts.flush(), () => $("draft-status")); });
  listen($("reconcile"), "click", () => { void action(() => drafts.reconcile(), () => $("draft-status")); });
  listen($("use-local"), "click", () => { void action(() => drafts.resolveConflict("local"), () => $("draft-status")); });
  listen($("use-server"), "click", () => { void action(() => drafts.resolveConflict("server"), () => $("draft-status")); });
  listen($("node"), "change", () => {
    const entry = draftEntry(); entry.selectedNodeIndex = Number($("node").value); entry.selectedPropertyIndex = -1; entry.propertyHint = null;
    entry.nodeHint = nodeSelection(array(entry.server?.nodes), entry.selectedNodeIndex); entry.selectionLost = false;
    renderDraft();
  });
  listen($("property"), "change", () => {
    const entry = draftEntry(); entry.selectedPropertyIndex = $("property").value === "" ? -1 : Number($("property").value);
    entry.propertyHint = propertySelection(array(selectedNode()?.properties), entry.selectedPropertyIndex);
    renderDraft();
  });
  listen($("property-kind"), "change", propertyControls);
  const sendCommand = (command) => action(() => drafts.command(command), () => $("source-panel"));
  listen($("enable"), "click", () => { void sendCommand({kind: "set_status", node_id: selectedNode().id, status: "okay"}); });
  listen($("disable"), "click", () => { void sendCommand({kind: "set_status", node_id: selectedNode().id, status: "disabled"}); });
  listen($("property-form"), "submit", (event) => {
    event.preventDefault();
    const name = $("property-name").value;
    if (name.length > 256 || !/^[A-Za-z_#][A-Za-z_0-9,#.+?-]*$/.test(name)) {
      draftEntry().error = "Enter a supported property name, such as status, current-speed or vendor,option.";
      renderDraft(); context.reveal?.($("draft-error")); return;
    }
    const kind = $("property-kind").value;
    let value = kind === "boolean" ? null : $("property-value").value;
    if (kind === "cells") {
      const parts = value.trim().split(/[\s,]+/);
      if (!parts.length || parts.length > 64 || parts.some((part) => !/^(?:0[xX][\da-fA-F]+|\d+)$/.test(part) || Number(part) > 0xffffffff)) {
        draftEntry().error = "Enter one to 64 unsigned 32-bit integer cells, separated by spaces."; renderDraft(); context.reveal?.($("draft-error")); return;
      }
      value = parts.map(Number);
    }
    const command = {kind: "set_property", node_id: selectedNode().id, name, value: {kind, value}};
    if (selectedProperty()) command.property_id = selectedProperty().id;
    void sendCommand(command);
  });
  listen($("remove-property"), "click", () => { void sendCommand({kind: "remove_property", property_id: selectedProperty().id}); });
  listen($("show-source"), "click", () => {
    const selected = selectedProperty() || selectedNode(); const entry = draftEntry();
    if (!selected || !entry?.server || drafts.pending()) return;
    const [start, end] = sourceSelection(entry.text, selected.start, selected.end);
    context.reveal?.($("source")); $("source").setSelectionRange(start, end);
  });
  function linkSource() {
    const entry = draftEntry();
    if (!entry?.server || drafts.pending() || entry.flight || entry.uncertain || entry.server.syntax.blocked) return;
    const offset = sourceOffset(entry.text, $("source").selectionStart);
    const nodes = entry.server.nodes.map((node, index) => ({node, index})).filter(({node}) => node.start <= offset && offset <= node.end);
    nodes.sort((left, right) => (left.node.end - left.node.start) - (right.node.end - right.node.start));
    const found = nodes[0]; if (!found) return;
    entry.selectedNodeIndex = found.index; entry.nodeHint = nodeSelection(entry.server.nodes, found.index); entry.selectionLost = false;
    entry.selectedPropertyIndex = array(found.node.properties).findIndex((property) => property.start <= offset && offset <= property.end);
    entry.propertyHint = propertySelection(array(found.node.properties), entry.selectedPropertyIndex);
    renderDraft();
  }
  listen($("source"), "click", linkSource); listen($("source"), "keyup", linkSource);
  listen($("view-retained"), "click", () => {
    $("retained-source").hidden = false; $("retained-source").value = editorText(state.draftCache[$("retained").value]?.text || "");
    context.reveal?.($("retained-source"));
  });
  listen($("retained"), "change", () => { $("retained-source").hidden = true; });
  drafts = createDraftSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  session = createEditorSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  render();
  return {
    navigate(view) { actionGeneration++; return session.navigate(view); },
    dispose() { disposed = true; actionGeneration++; lifetime.abort(); session.dispose(); drafts.dispose(); },
  };
}
