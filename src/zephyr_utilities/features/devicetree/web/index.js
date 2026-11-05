import {createEditorSession} from "./state.js";

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
  let renderedDocument;
  let session;

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
    $("overlay-status").textContent = !array(data.documents).length ? "No application overlays were found. Source creation is planned for a later stage."
      : selected ? "This selection identifies the future editing destination. Source is read only in this preview."
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
    $("loading").hidden = !state.loading;
    $("error").hidden = !state.error; $("error").textContent = state.error || "";
    $("empty").hidden = Boolean(state.data || state.loading || state.error);
    $("results").hidden = !state.data;
    $("open-directory").disabled = state.loading;
    $("open-directory").textContent = state.loading && state.selection?.kind === "application" ? "Opening…" : "Open directory";
    $("recheck").disabled = state.loading || state.documentLoading;
    $("select-overlay").disabled = state.loading || state.documentLoading || !$("overlay").value;
    $("select-overlay").textContent = state.documentLoading ? "Reading…" : "Inspect overlay";
    $("source").hidden = !state.document;
    if (renderedDocument !== state.document) {
      renderedDocument = state.document;
      $("source").textContent = state.document?.text || "";
    }
    $("source-path").textContent = state.document?.path || state.data?.editing_document?.path || "";
    $("source-error").hidden = !state.documentError; $("source-error").textContent = state.documentError || "";
    $("source-status").textContent = state.documentLoading ? "Reading current overlay source…"
      : state.document ? `Current source · SHA-256 ${state.document.sha256}`
        : state.data?.editing_document?.availability && state.data.editing_document.availability !== "available"
          ? "The selected overlay is unavailable. Choose another file or correct the application files."
          : "Choose an available overlay to read its current source.";
  }
  async function action(operation, destination) {
    const captured = ++actionGeneration;
    const workspace = context.workspace.path;
    const accepted = await operation();
    if (!accepted || disposed || captured !== actionGeneration || workspace !== context.workspace.path) return;
    const target = state.error ? $("error") : state.documentError ? $("source-error") : destination();
    if (target) context.reveal?.(target);
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  listen($("project"), "change", () => { state.formProjectId = $("project").value; state.formConfigurationId = null; configurations(); });
  listen($("configuration"), "change", () => { state.formConfigurationId = $("configuration").value || null; });
  listen($("project-form"), "submit", (event) => {
    event.preventDefault();
    void action(() => session.openProject($("project").value, $("configuration").value || null), () => $("context-panel"));
  });
  listen($("directory-form"), "submit", (event) => {
    event.preventDefault(); void action(() => session.openApplication($("directory").value), () => $("context-panel"));
  });
  listen($("overlay"), "change", () => { $("select-overlay").disabled = !$("overlay").value || state.loading || state.documentLoading; });
  listen($("overlay-form"), "submit", (event) => {
    event.preventDefault(); void action(() => session.selectDocument($("overlay").value), () => $("source-panel"));
  });
  listen($("refresh-catalog"), "click", () => {
    void action(() => session.catalog(), () => state.catalogError ? $("catalog-error") : null);
  });
  listen($("recheck"), "click", () => { void action(() => session.revalidate(), () => $("context-panel")); });
  session = createEditorSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  render();
  return {
    navigate(view) { actionGeneration++; return session.navigate(view); },
    dispose() { disposed = true; actionGeneration++; lifetime.abort(); session.dispose(); },
  };
}
