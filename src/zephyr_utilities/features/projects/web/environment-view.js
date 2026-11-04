import {activeJob, createEnvironmentSession, filterBoards, filterSocs, lines} from "./environment-state.js";

export function mountEnvironment(root, context) {
  const $ = (name) => root.querySelector(`[data-${name}]`);
  const lifetime = new AbortController();
  const state = context.state;
  state.filters ||= {query: "", chip: "", vendor: ""};
  state.options ||= {zephyr_base: "", board_roots: "", soc_roots: "", sdk_paths: ""};
  state.boardLimit ||= 40;
  state.chipLimit ||= 40;
  let renderedSnapshot;
  let renderedWorkspace;
  let disposed = false;
  let session;
  const number = (value) => new Intl.NumberFormat("en-US").format(value);
  const text = (value, fallback = "Unknown") => value === null || value === undefined || value === "" ? fallback : String(value);
  const array = (value) => Array.isArray(value) ? value : [];
  const date = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "Unknown time";
  const key = (board) => board.id || `${board.name}:${array(board.directories).join(";")}`;
  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = String(value);
    return element;
  }
  function pair(parent, label, value, path = false) {
    const item = node("div", "projects-fact");
    item.append(node("dt", "", label), node("dd", path ? "projects-path" : "", text(value)));
    parent.append(item);
  }
  function list(parent, heading, values, path = false) {
    parent.append(node("h3", "", heading));
    const items = node("ul", path ? "projects-path projects-source-list" : "projects-source-list");
    for (const value of values.length ? values : ["Not reported"]) items.append(node("li", "", value));
    parent.append(items);
  }
  function table(parent, headings, rows) {
    const wrap = node("div", "projects-table-wrap");
    const result = node("table", "projects-table");
    const head = node("thead"); const header = node("tr");
    headings.forEach((heading) => { const th = node("th", "", heading); th.scope = "col"; header.append(th); });
    head.append(header); result.append(head);
    const body = node("tbody");
    rows.forEach((values) => { const row = node("tr"); values.forEach((value) => row.append(node("td", "", text(value)))); body.append(row); });
    result.append(body); wrap.append(result); parent.append(wrap);
  }
  function renderOverview(snapshot) {
    const saved = snapshot.freshness === "saved-observation";
    $("snapshot-meta").textContent = `Observed ${date(snapshot.created_at)} · ${saved ? "Saved observation; freshness not rechecked" : snapshot.cache?.hit ? "Unchanged inputs; cached catalogue" : "Local discovery"}`;
    $("snapshot-status").textContent = {complete: "Discovery complete", partial: "Partial discovery", stale: "Inputs changed during scan"}[snapshot.status] || "Discovery results";
    $("snapshot-status").classList.toggle("warning", snapshot.status !== "complete");
    $("freshness").textContent = snapshot.status === "stale" ? "Some inputs changed during this analysis. Run a full rescan before relying on the catalogue." : `${saved ? "This saved observation has not been rechecked. Analyze again to check for changed inputs. " : "Rescan after changing Zephyr, modules, board definitions or SDK installations. "}No target has been configure-, build- or hardware-validated by this analysis.`;
    const metrics = $("metrics"); metrics.replaceChildren();
    for (const [label, value, caption] of [["Boards", array(snapshot.boards).length, "Declared in local metadata"], ["Chips", array(snapshot.socs).length, "SoCs from selected roots"], ["SDK installations", array(snapshot.sdks).length, "Identified by location"], ["Diagnostics", array(snapshot.diagnostics).length, "Missing or incomplete information"]]) {
      const metric = node("article", "projects-metric");
      metric.append(node("span", "projects-eyebrow", label), node("strong", "", number(value)), node("span", "projects-muted", caption)); metrics.append(metric);
    }
    const identity = $("identity"); identity.replaceChildren();
    pair(identity, "Analysed workspace", snapshot.workspace?.path, true);
    pair(identity, "Zephyr checkout", snapshot.zephyr?.path, true);
    pair(identity, "Zephyr version", snapshot.zephyr?.version);
    pair(identity, "Zephyr commit", snapshot.zephyr?.commit, true);
    pair(identity, "Working tree", snapshot.zephyr?.dirty === true ? "Contains local changes" : snapshot.zephyr?.dirty === false ? "Clean at analysis time" : null);
    pair(identity, "SDK reference in checkout", snapshot.zephyr?.recommended_sdk || snapshot.selection?.recommended_version);
    pair(identity, "West workspace", snapshot.workspace?.west_topdir, true);
    pair(identity, "Manifest", snapshot.workspace?.manifest_path, true);
    const tools = $("tools"); tools.replaceChildren(node("h3", "", "Local tools"));
    table(tools, ["Tool", "Version", "Location", "State"], array(snapshot.tools).map((tool) => [tool.name, tool.version, tool.path, tool.status]));
    const modules = $("modules"); modules.replaceChildren(node("h3", "", "Workspace projects and modules"));
    if (array(snapshot.modules).length) table(modules, ["Project", "Kind", "Location", "State"], snapshot.modules.map((module) => [module.name, module.is_module ? "Zephyr module" : "West project", module.path, module.status]));
    else modules.append(node("p", "projects-muted", "No workspace projects or modules were reported."));
    const roots = $("roots"); roots.replaceChildren();
    for (const [label, entries] of Object.entries(snapshot.roots || {})) list(roots, `${label[0].toUpperCase()}${label.slice(1)} roots`, array(entries).map((entry) => typeof entry === "string" ? entry : `${entry.path} · ${entry.origin || "Selected root"}`), true);
  }
  function renderSdks(snapshot) {
    const target = $("sdks"); target.replaceChildren();
    if (!array(snapshot.sdks).length) target.append(node("p", "projects-muted", "No installed Zephyr SDK was found. Add an installation path above and analyze again. Board and chip browsing does not require an SDK."));
    for (const sdk of array(snapshot.sdks)) {
      const card = node("article", "projects-sdk");
      const heading = node("div", "projects-sdk-heading"); heading.append(node("h3", "", `Zephyr SDK ${text(sdk.version, "(version unknown)")}`), node("span", "projects-label", text(sdk.status, "Discovered")));
      card.append(heading, node("p", "projects-path", sdk.path));
      const facts = node("dl", "projects-sdk-facts"); pair(facts, "Found through", sdk.source); pair(facts, "Evidence", sdk.evidence); card.append(facts);
      const detail = node("details"); detail.append(node("summary", "", `${number(array(sdk.toolchains).length)} installed toolchains`));
      table(detail, ["Toolchain", "Compiler location"], array(sdk.toolchains).map((toolchain) => typeof toolchain === "string" ? [toolchain, "Unknown"] : [toolchain.name, toolchain.compiler || toolchain.path]));
      list(detail, "Host tools", array(sdk.host_tools).map((tool) => typeof tool === "string" ? tool : tool.name));
      if (array(sdk.uninstalled_toolchains).length) list(detail, "Available but not installed", sdk.uninstalled_toolchains.map((tool) => typeof tool === "string" ? tool : tool.name));
      card.append(detail); target.append(card);
    }
    const selection = snapshot.selection || {};
    const selectedPath = selection.requested_path || selection.sdk_path || selection.requested_sdk_path;
    const variant = selection.requested_variant || selection.variant;
    if (selectedPath || variant) target.append(node("p", "projects-muted", `Environment requests ${variant || "a toolchain"}${selectedPath ? ` at ${selectedPath}` : ""}. The effective compiler is unknown until configuration.`));
  }
  function renderDetail(board) {
    const target = $("detail"); target.replaceChildren();
    if (!board) { target.append(node("p", "projects-muted", "Choose a board to inspect its targets and source information.")); return; }
    target.append(node("span", "projects-eyebrow", "BOARD DETAILS"), node("h3", "", board.full_name || board.name), node("p", "projects-path", board.name));
    const details = node("dl", "projects-detail-facts");
    pair(details, "Vendor", board.vendor); pair(details, "Discovery state", board.status); pair(details, "Evidence", board.evidence);
    pair(details, "Chip / SoC", array(board.socs).join(", ")); pair(details, "Default revision", board.default_revision);
    pair(details, "Declared architectures", array(board.architectures).join(", "));
    pair(details, "Declared revisions", array(board.revisions).join(", "));
    target.append(details);
    list(target, "Targets declared by metadata", array(board.targets), true);
    list(target, "Qualifiers", array(board.qualifiers), true);
    list(target, "Origins", array(board.origins));
    list(target, "Source files", array(board.source_files).length ? board.source_files : array(board.directories), true);
    target.append(node("p", "projects-muted", "Targets are discovery candidates. Configuration may resolve revision defaults or reject unsupported combinations."));
    if (board.status === "discovered" && array(board.targets).length) {
      const button = node("button", "projects-button primary", "Use this board");
      button.type = "button"; button.dataset.useBoard = key(board); target.append(button);
    }
  }
  function renderCatalogue() {
    const snapshot = state.snapshot;
    if (!snapshot) return;
    const focused = root.ownerDocument.activeElement?.closest("[data-board]");
    const focusedKey = focused && root.contains(focused) ? focused.dataset.board : null;
    const boards = filterBoards(array(snapshot.boards), array(snapshot.socs), state.filters);
    $("board-count").textContent = `${number(boards.length)} of ${number(array(snapshot.boards).length)} boards`;
    const rows = $("boards"); rows.replaceChildren();
    if (!boards.length) rows.append(node("p", "projects-empty-list", array(snapshot.boards).length ? "No boards match these filters. Try a shorter chip name or clear the vendor filter." : "No boards were discovered. Check diagnostics and additional roots."));
    for (const board of boards.slice(0, state.boardLimit)) {
      const selected = key(board) === state.selectedBoard;
      const button = node("button", `projects-board${selected ? " selected" : ""}`);
      button.type = "button"; button.dataset.board = key(board); button.setAttribute("aria-pressed", String(selected));
      const title = node("span", "projects-board-title"); title.append(node("strong", "", board.full_name || board.name), node("small", "projects-board-state", text(board.status, "Discovered")));
      button.append(title, node("span", "projects-path", board.name), node("span", "projects-muted", [board.vendor, array(board.socs).join(", ")].filter(Boolean).join(" · ") || "Chip and vendor unknown"));
      rows.append(button);
    }
    $("more").hidden = boards.length <= state.boardLimit;
    renderDetail(boards.find((board) => key(board) === state.selectedBoard));
    const chips = filterSocs(array(snapshot.socs), state.filters.chip);
    $("chip-summary").textContent = `Browse discovered chips (${number(chips.length)})`;
    const chipsTarget = $("chips"); chipsTarget.replaceChildren();
    for (const soc of chips.slice(0, state.chipLimit)) {
      const button = node("button", "projects-chip", soc.name); button.type = "button"; button.dataset.soc = soc.name;
      button.title = [soc.family, soc.series, ...(soc.origins || [])].filter(Boolean).join(" · ");
      chipsTarget.append(button);
    }
    if (!chips.length) chipsTarget.append(node("p", "projects-muted", "No matching chips were discovered."));
    $("more-chips").hidden = chips.length <= state.chipLimit;
    if (focusedKey) [...root.querySelectorAll("[data-board]")].find((button) => button.dataset.board === focusedKey)?.focus({preventScroll: true});
  }
  function renderDiagnostics(snapshot) {
    const target = $("diagnostics"); target.replaceChildren();
    const diagnostics = array(snapshot.diagnostics);
    $("diagnostic-count").textContent = `${number(diagnostics.length)} messages`;
    if (!diagnostics.length) target.append(node("p", "projects-muted", "No discovery diagnostics were reported. Build and hardware compatibility remain unverified."));
    for (const diagnostic of diagnostics) {
      const entry = node("article", `projects-diagnostic ${["error", "warning", "info"].includes(diagnostic.severity) ? diagnostic.severity : "info"}`);
      entry.append(node("span", "projects-eyebrow", diagnostic.severity || "Information"), node("p", "", diagnostic.message));
      if (diagnostic.path) entry.append(node("p", "projects-path", diagnostic.path));
      if (diagnostic.code) entry.append(node("small", "projects-muted", diagnostic.code)); target.append(entry);
    }
    const provenance = $("provenance"); provenance.replaceChildren();
    const fields = node("dl", "projects-identity"); pair(fields, "Snapshot", snapshot.id, true); pair(fields, "Schema", snapshot.schema_version); pair(fields, "Fingerprint", snapshot.fingerprint, true); pair(fields, "Observed", date(snapshot.created_at)); provenance.append(fields);
    for (const [kind, entries] of Object.entries(snapshot.changes || {})) {
      if (!Array.isArray(entries)) continue;
      list(provenance, `${kind[0].toUpperCase()}${kind.slice(1)} (${number(entries.length)})`, entries.length ? entries.slice(0, 100).map((item) => typeof item === "string" ? item : item.name || item.id || JSON.stringify(item)) : ["None"]);
      if (entries.length > 100) provenance.append(node("p", "projects-muted", "Showing the first 100 changes."));
    }
  }
  function render() {
    if (disposed) return;
    $("workspace").textContent = state.workspace || context.workspace.path;
    if (renderedWorkspace !== state.workspace) {
      renderedWorkspace = state.workspace;
      for (const input of root.querySelectorAll("[data-option]")) input.value = state.options[input.dataset.option] || "";
    }
    const busy = state.loading || state.submitting || activeJob(state.job);
    $("analyze").disabled = busy; $("rescan").disabled = busy;
    $("analyze").textContent = state.submitting ? "Starting analysis…" : state.loading ? "Loading environment…" : state.snapshot ? "Analyze again" : "Analyze workspace";
    $("error").hidden = !state.error; $("error").textContent = state.error || "";
    const job = state.job;
    $("job").hidden = !job;
    if (job) {
      $("job-title").textContent = {queued: "Analysis queued", running: "Analyzing workspace", completed: "Analysis complete", cancelled: "Analysis cancelled", failed: "Analysis failed"}[job.status] || "Analysis";
      $("job-status").textContent = activeJob(job) ? "Discovery runs locally. You can leave this view while the scan continues." : job.status === "cancelled" ? "The last successful observation is retained." : text(job.error, "Source files and installed tools were not changed.");
      $("logs").textContent = array(job.logs).join("\n") || "No log messages yet.";
      $("cancel").hidden = !activeJob(job); $("cancel").disabled = state.cancelling; $("cancel").textContent = state.cancelling ? "Cancelling…" : "Cancel scan";
    }
    $("results").hidden = !state.snapshot; $("empty").hidden = Boolean(state.snapshot) || state.loading;
    if (state.snapshot !== renderedSnapshot) {
      renderedSnapshot = state.snapshot;
      if (state.snapshot) {
        renderOverview(state.snapshot); renderSdks(state.snapshot); renderDiagnostics(state.snapshot);
        const vendors = [...new Set(array(state.snapshot.boards).map((board) => board.vendor).filter(Boolean))].sort();
        const select = $("vendor"); select.replaceChildren();
        for (const value of ["", ...vendors]) { const option = node("option", "", value || "All vendors"); option.value = value; select.append(option); }
        if (!vendors.includes(state.filters.vendor)) state.filters.vendor = "";
        select.value = state.filters.vendor; renderCatalogue();
      }
    }
  }
  function readOptions(force) {
    const options = {force};
    for (const name of ["board_roots", "soc_roots", "sdk_paths"]) options[name] = lines(state.options[name]);
    if (state.options.zephyr_base.trim()) options.zephyr_base = state.options.zephyr_base.trim();
    return options;
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  for (const input of root.querySelectorAll("[data-option]")) {
    input.value = state.options[input.dataset.option] || "";
    listen(input, "input", () => { state.options[input.dataset.option] = input.value; });
  }
  for (const name of ["query", "chip", "vendor"]) {
    $(name).value = state.filters[name] || "";
    listen($(name), name === "vendor" ? "change" : "input", () => { state.filters[name] = $(name).value; state.boardLimit = 40; state.chipLimit = 40; renderCatalogue(); });
  }
  listen($("analyze"), "click", () => session.start(() => readOptions(false)));
  listen($("rescan"), "click", () => session.start(() => readOptions(true)));
  listen($("cancel"), "click", () => session.cancel());
  listen($("more"), "click", () => { state.boardLimit += 40; renderCatalogue(); });
  listen($("more-chips"), "click", () => { state.chipLimit += 40; renderCatalogue(); });
  listen($("boards"), "click", (event) => { const button = event.target.closest("[data-board]"); if (button && root.contains(button)) { state.selectedBoard = button.dataset.board; renderCatalogue(); } });
  listen($("detail"), "click", (event) => {
    const button = event.target.closest("[data-use-board]");
    if (!button || !root.contains(button)) return;
    state.creatorPendingBoard = {workspace: state.workspace, board_id: button.dataset.useBoard};
    context.navigate("projects/create");
  });
  listen($("chips"), "click", (event) => { const button = event.target.closest("[data-soc]"); if (button && root.contains(button)) { state.filters.chip = button.dataset.soc; $("chip").value = state.filters.chip; state.boardLimit = 40; renderCatalogue(); $("chip").focus(); } });
  session = createEnvironmentSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  render(); session.load();
  return {navigate() { session.tick(); }, dispose() { disposed = true; lifetime.abort(); session.dispose(); }};
}
