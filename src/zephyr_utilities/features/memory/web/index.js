/** Memory feature. The application shell owns routing and shared services. */
import {reconcileSnapshots} from "./snapshot-state.js";
import {createMemoryHandoff} from "./handoff-state.js";

export function mount(root, context) {
  root = root.querySelector("#memory-feature");
  const $ = (id) => root.querySelector(`#${id}`);
  const lifetime = new AbortController();
  const timers = new Set();
  const frames = new Set();
  const downloads = new Set();
  let disposed = false;
  let observer;
  let snapshotsRequest = 0;
  let snapshotOptionsReady = false;
  let pendingDelete = null;
  let deleting = false;
  let generating = false;
  let discoveryRequest = 0;
  // Detached generated rows need no external lifetime reference; removing a row
  // releases its listeners. Static controls are explicitly aborted on disposal.
  const listen = (node, type, action) => node.addEventListener(type, action, root.contains(node) ? {signal: lifetime.signal} : undefined);
  const later = (action, delay) => {
    const timer = setTimeout(() => { timers.delete(timer); if (!disposed) action(); }, delay);
    timers.add(timer);
    return timer;
  };
  const frame = (action) => {
    const id = requestAnimationFrame(() => { frames.delete(id); if (!disposed) action(); });
    frames.add(id);
  };
  const defaults = {token: "", snapshots: [], current: null, snapshotRequest: 0, build: null, memory: "ram", compareMemory: "ram", comparison: null, compareRequest: 0, node: null, ancestors: [], units: "kib", symbolLimit: 50, changeLimit: 60, job: null, folder: null, noticeTimer: null, view: "overview"};
  const state = Object.assign(context.state, {...defaults, ...context.state});
  state.token = context.session.token;
  state.inspection ||= {};
  const inspection = createMemoryHandoff({api: (path, options) => context.api(path, options), state: state.inspection,
    workspace: () => context.workspace.path, onChange: renderInspection});
  const palette = ["#337f6c", "#72927a", "#ae7957", "#48717c", "#8b9560", "#648f9a", "#b29472", "#6c827d"];
  const views = {overview: "Overview", explorer: "Memory Explorer", compare: "Compare", snapshots: "Snapshots"};
  const integer = new Intl.NumberFormat("en-US");
  const decimal = new Intl.NumberFormat("en-US", {maximumFractionDigits: 2});

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function button(text, className, action) {
    const node = el("button", className, text);
    node.type = "button";
    listen(node, "click", () => run(action));
    return node;
  }
  function clear(node) { node.replaceChildren(); }
  function useBytes(value) { return state.units === "bytes" || (value !== 0 && Math.abs(value) < 1024); }
  function fmt(value, signed = false) {
    if (value === null || value === undefined) return "—";
    const prefix = signed && value > 0 ? "+" : "";
    return useBytes(value) ? `${prefix}${integer.format(value)} B` : `${prefix}${decimal.format(value / 1024)} KiB`;
  }
  function pct(value, total) { return total ? `${decimal.format(value / total * 100)}%` : "—"; }
  function date(value) { return value ? new Date(value).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "Unknown date"; }
  function tail(path) { return String(path || "").replaceAll("\\", "/").split("/").filter(Boolean).pop() || "Build"; }
  function color(name) {
    let hash = 0;
    for (const char of String(name)) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return palette[Math.abs(hash) % palette.length];
  }
  function notice(message, error = false) {
    if (disposed) return;
    clearTimeout(state.noticeTimer);
    $("notice-text").textContent = message;
    $("notice").classList.toggle("error", error);
    $("notice").hidden = false;
    root.querySelectorAll(".dialog-error").forEach((node) => node.remove());
    const dialog = [...root.querySelectorAll("dialog[open]")].at(-1);
    if (error && dialog) {
      const alert = el("div", "warning-item dialog-error", message);
      alert.setAttribute("role", "alert");
      dialog.querySelector(".dialog-heading").after(alert);
    }
    if (!error) state.noticeTimer = later(() => { $("notice").hidden = true; }, 8000);
  }
  async function run(action) {
    try { await action(); }
    catch (error) { if (!disposed && error.name !== "AbortError") notice(error.message || String(error), true); }
  }
  async function api(path, options = {}) {
    if (disposed) throw new DOMException("Feature closed", "AbortError");
    const data = await context.api(path, {...options, signal: lifetime.signal});
    if (disposed) throw new DOMException("Feature closed", "AbortError");
    return data;
  }
  function showView(view, route = true) {
    if (!views[view]) view = "overview";
    state.view = view;
    for (const name of Object.keys(views)) $("view-" + name).hidden = name !== view;
    root.querySelectorAll("[data-view]").forEach((node) => {
      const active = node.dataset.view === view;
      node.classList.toggle("active", active);
      if (active) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current");
    });
    $("view-label").textContent = views[view];
    if (route) context.navigate(`memory/${view}`);
    if (view === "explorer" && state.current) frame(renderTree);
  }
  function showWarnings(target, messages) {
    clear(target);
    [...new Set((messages || []).filter(Boolean))].forEach((message) => target.append(el("div", "warning-item", message)));
  }
  function report() { return state.current?.reports?.[state.memory] || null; }
  function leaves() { return report()?.leaves || []; }
  function emptyRow(tbody, text, colspan) {
    const row = el("tr"); const cell = el("td", "table-empty", text); cell.colSpan = colspan; row.append(cell); tbody.append(row);
  }
  function numberCell(value, signed = false) {
    const cell = el("td", "numeric", fmt(value, signed));
    if (value !== null && value !== undefined) cell.title = `${signed && value > 0 ? "+" : ""}${integer.format(value)} bytes`;
    if (signed && value) cell.classList.add(value > 0 ? "increase-text" : "decrease-text");
    return cell;
  }
  function nameCell(item, interactive = false) {
    const cell = el("td");
    const name = interactive ? button(item.name, "text-button code", () => selectNode(item)) : el("span", "cell-name code", item.name);
    cell.append(name);
    if (item.path && item.path !== item.name) cell.append(el("span", "cell-sub", item.path));
    return cell;
  }
  function metricCard(kind, total, detail, delta = null) {
    const card = el("article", `metric ${kind}`);
    const header = el("div", "metric-header");
    header.append(el("span", "", kind === "ram" ? "RAM footprint" : "ROM / Flash footprint"), el("span", "metric-symbol", kind === "ram" ? "▤" : "▥"));
    const value = el("div", "metric-value");
    value.title = total === null || total === undefined ? "Report not available" : `${integer.format(total)} bytes`;
    const bytes = total !== null && total !== undefined ? useBytes(total) : state.units === "bytes";
    value.append(el("span", "", total === null || total === undefined ? "—" : decimal.format(bytes ? total : total / 1024)), el("small", "", bytes ? "bytes" : "KiB"));
    const foot = el("div", "metric-foot");
    foot.append(el("span", "", detail));
    foot.append(el("span", delta === null ? "" : delta > 0 ? "increase-text" : "decrease-text", delta === null ? "Capacity unknown" : `${fmt(delta, true)} change`));
    card.append(header, value, foot);
    return card;
  }
  function renderOverview() {
    const snap = state.current;
    $("overview-empty").hidden = Boolean(snap);
    $("overview-content").hidden = !snap;
    $("explorer-empty").hidden = Boolean(snap);
    $("explorer-content").hidden = !snap;
    if (!snap) return;
    $("current-name").textContent = snap.name;
    const metadata = [snap.build ? snap.build.board || "Target unknown" : "Imported report", snap.build?.domain, date(snap.created_at)].filter(Boolean);
    $("current-meta").textContent = metadata.join("  ·  ");
    clear($("overview-metrics"));
    for (const kind of ["ram", "rom"]) $("overview-metrics").append(metricCard(kind, snap.reports?.[kind]?.total_size ?? null, snap.reports?.[kind] ? "Statically allocated / reserved" : "Report unavailable"));
    showWarnings($("snapshot-warnings"), [...(snap.warnings || []), ...(snap.reports?.ram?.warnings || []), ...(snap.reports?.rom?.warnings || [])]);
    renderDistribution(); renderLargest(); renderMetadata(); renderExplorer();
  }
  function renderMetadata() {
    const snap = state.current, target = $("build-metadata"); clear(target);
    const fields = [["Snapshot ID", snap.id], ["Captured", date(snap.created_at)], ["Board", snap.build?.board], ["Application", snap.build?.application], ["Build directory", snap.build?.path], ["Image / domain", snap.build?.domain], ["Zephyr version", snap.build?.zephyr_version], ["Toolchain", snap.build?.toolchain], ["ELF", snap.build?.elf_path], ["Notes", snap.notes]];
    for (const [label, value] of fields) { if (value) target.append(el("dt", "", label), el("dd", "", typeof value === "object" ? JSON.stringify(value) : value)); }
  }
  function rootChildren(rep) {
    const root = rep?.tree;
    if (!root) return [];
    return (root.children?.length ? root.children : [root]).filter((node) => node.size > 0).slice().sort((a, b) => b.size - a.size);
  }
  function renderDistribution() {
    const target = $("distribution"); clear(target);
    const rep = report();
    if (!rep) { target.append(el("p", "muted", `No ${state.memory.toUpperCase()} report in this snapshot.`)); return; }
    const groups = rootChildren(rep), strip = el("div", "distribution-strip");
    for (const group of groups) {
      const segment = button("", "distribution-segment", () => { showView("explorer"); selectNode(group); });
      segment.style.flex = `${group.size} 1 0%`; segment.style.background = color(group.name);
      segment.title = `${group.name}: ${fmt(group.size)}`; segment.setAttribute("aria-label", segment.title); strip.append(segment);
    }
    target.append(strip);
    for (const group of groups.slice(0, 7)) {
      const row = el("div", "distribution-row"), dot = el("span", "color-dot"); dot.style.background = color(group.name);
      const name = button(group.name, "text-button label", () => { showView("explorer"); selectNode(group); });
      row.append(dot, name, el("span", "value", fmt(group.size)), el("span", "percent", pct(group.size, rep.total_size))); target.append(row);
    }
    if (!groups.length) target.append(el("p", "muted", "This report contains no allocated bytes."));
  }
  function renderLargest() {
    const target = $("largest-overview"); clear(target); $("largest-memory").textContent = state.memory.toUpperCase();
    const sorted = leaves().slice().sort((a, b) => b.size - a.size).slice(0, 5);
    for (const item of sorted) {
      const wrapper = el("div", "consumer"), top = el("div", "consumer-top"), name = button(item.name, "text-button consumer-name", () => { showView("explorer"); selectNode(item); });
      name.title = item.path; top.append(name, el("span", "consumer-value", fmt(item.size)));
      const track = el("div", "consumer-track"), fill = el("div", "consumer-fill"); fill.style.width = `${sorted[0].size ? item.size / sorted[0].size * 100 : 0}%`; track.append(fill); wrapper.append(top, track); target.append(wrapper);
    }
    if (!sorted.length) target.append(el("p", "muted", "No symbols available for this memory type."));
  }
  function setMemory(kind) {
    state.memory = kind; state.node = null; state.ancestors = []; state.symbolLimit = 50;
    root.querySelectorAll("[data-memory]").forEach((node) => node.setAttribute("aria-pressed", String(node.dataset.memory === kind)));
    renderOverview();
  }
  function findPath(root, id, chain = []) {
    if (!root) return null;
    if (root.id === id) return [...chain, root];
    for (const child of root.children || []) { const found = findPath(child, id, [...chain, root]); if (found) return found; }
    return null;
  }
  function selectNode(node) {
    const rep = report(); if (!rep) return;
    const chain = findPath(rep.tree, node.id);
    if (!chain) return;
    state.node = chain.at(-1); state.ancestors = chain;
    renderTree();
  }
  function renderExplorer() {
    if (!state.current) return;
    $("explorer-snapshot-name").textContent = state.current.name;
    const rep = report();
    $("explorer-total").textContent = rep ? `${state.memory.toUpperCase()} · ${fmt(rep.total_size)} total · static footprint` : `${state.memory.toUpperCase()} report unavailable`;
    if (rep && !state.node) { state.node = rep.tree; state.ancestors = [rep.tree]; }
    renderTree(); renderSymbols();
  }
  // Weighted binary subdivision keeps rectangle areas proportional without a chart dependency.
  function rectangles(items, x, y, width, height) {
    if (!items.length) return [];
    if (items.length === 1) return [{node: items[0], x, y, width, height}];
    const sum = items.reduce((total, item) => total + item.size, 0);
    if (sum <= 0) return [];
    let amount = 0, split = 0;
    while (split < items.length - 1 && (amount < sum / 2 || split === 0)) amount += items[split++].size;
    const ratio = amount / sum;
    if (width >= height) return [...rectangles(items.slice(0, split), x, y, width * ratio, height), ...rectangles(items.slice(split), x + width * ratio, y, width * (1 - ratio), height)];
    return [...rectangles(items.slice(0, split), x, y, width, height * ratio), ...rectangles(items.slice(split), x, y + height * ratio, width, height * (1 - ratio))];
  }
  function renderTree() {
    const rep = report(), target = $("treemap"), crumbs = $("tree-breadcrumbs"), table = $("children-table");
    clear(target); clear(crumbs); clear(table); $("selected-detail").hidden = true;
    if (!rep || !state.node) { target.append(el("div", "treemap-message", "No report available for this memory type.")); return; }
    state.ancestors.forEach((node, i) => { if (i) crumbs.append(el("span", "", "/")); crumbs.append(button(i === 0 ? "All memory" : node.name, "", () => selectNode(node))); });
    const node = state.node, children = node.children?.length ? node.children : [node];
    const sorted = children.slice().sort((a, b) => b.size - a.size), positive = sorted.filter((item) => item.size > 0);
    // Keep the drawing bounded; omitted tiny rectangles remain individually available in the table.
    const drawn = positive.slice(0, 180);
    if (positive.length > 180) drawn.push({id: "__other__", name: `${positive.length - 180} smaller entries`, size: positive.slice(180).reduce((sum, item) => sum + item.size, 0), grouped: true});
    const width = target.clientWidth || 700, height = target.clientHeight || 320;
    for (const rect of rectangles(drawn, 0, 0, width, height)) {
      const item = rect.node;
      const tile = button("", "treemap-tile", () => { if (!item.grouped) selectNode(item); else table.scrollIntoView({block: "nearest"}); });
      tile.style.left = `${rect.x / width * 100}%`; tile.style.top = `${rect.y / height * 100}%`; tile.style.width = `${rect.width / width * 100}%`; tile.style.height = `${rect.height / height * 100}%`; tile.style.background = color(item.name);
      const title = `${item.name}: ${integer.format(item.size)} bytes (${pct(item.size, rep.total_size)} of report)`;
      tile.title = title; tile.setAttribute("aria-label", title);
      if (rect.width > 80 && rect.height > 40) tile.append(el("strong", "", item.name), el("small", "", fmt(item.size)));
      else { tile.style.padding = "0"; tile.tabIndex = -1; }
      target.append(tile);
    }
    if (!positive.length) target.append(el("div", "treemap-message", "No allocated bytes at this level."));
    for (const item of sorted) {
      const row = el("tr"); row.append(nameCell(item, true), el("td", "code", item.section || "—"), numberCell(item.size), el("td", "numeric", pct(item.size, rep.total_size))); table.append(row);
    }
    if (!node.children?.length) {
      $("selected-detail").hidden = false;
      const address = typeof node.address === "number" ? `0x${node.address.toString(16)}` : node.address;
      $("selected-detail").textContent = [node.path, node.section && `Section: ${node.section}`, address && `Address: ${address}`, `${integer.format(node.size)} bytes`].filter(Boolean).join(" · ");
    }
  }
  function renderSymbols() {
    const target = $("symbols-table"); clear(target);
    const rep = report(), query = $("symbol-search").value.trim().toLowerCase(), sort = $("symbol-sort").value;
    const filtered = leaves().filter((item) => `${item.name} ${item.path} ${item.section || ""}`.toLowerCase().includes(query));
    filtered.sort((a, b) => sort === "name" ? a.name.localeCompare(b.name) : sort === "size-asc" ? a.size - b.size : b.size - a.size);
    $("symbol-count").textContent = `${integer.format(filtered.length)} symbols`;
    for (const item of filtered.slice(0, state.symbolLimit)) {
      const row = el("tr"); row.append(nameCell(item, true), el("td", "code", item.section || "—"), numberCell(item.size), el("td", "numeric", pct(item.size, rep?.total_size))); target.append(row);
    }
    if (!filtered.length) emptyRow(target, "No symbols match this search.", 4);
    $("more-symbols").hidden = filtered.length <= state.symbolLimit;
  }
  async function refreshSnapshots() {
    const request = ++snapshotsRequest;
    const response = await api("/api/memory/snapshots");
    if (request === snapshotsRequest) updateSnapshots(response.snapshots || []);
  }
  function updateSnapshots(snapshots) {
    state.ui ||= {};
    if (snapshotOptionsReady) {
      for (const id of ["baseline-select", "candidate-select"]) state.ui[id] = $(id).value;
    }
    reconcileSnapshots(state, snapshots);
    try {
      const remembered = localStorage.getItem("zme-last-snapshot");
      if (remembered && !snapshots.some((snapshot) => snapshot.id === remembered)) localStorage.removeItem("zme-last-snapshot");
    } catch (_) { /* Remembering a selection is optional. */ }
    renderSnapshots(); renderSnapshotOptions(); renderOverview(); renderComparison();
    snapshotOptionsReady = true;
  }
  function renderSnapshotOptions() {
    for (const [id, placeholder] of [["baseline-select", "Choose a baseline"], ["candidate-select", "Choose a candidate"]]) {
      const select = $(id), value = state.ui?.[id] || ""; clear(select);
      const empty = el("option", "", placeholder); empty.value = ""; select.append(empty);
      for (const snap of state.snapshots) { const option = el("option", "", `${snap.name} · ${date(snap.created_at)}`); option.value = snap.id; select.append(option); }
      if (state.snapshots.some((snap) => snap.id === value)) select.value = value;
    }
  }
  function renderSnapshots() {
    $("snapshot-count").textContent = state.snapshots.length;
    $("snapshots-empty").hidden = Boolean(state.snapshots.length); $("snapshots-content").hidden = !state.snapshots.length;
    const target = $("snapshots-table"); clear(target);
    for (const snap of state.snapshots) {
      const row = el("tr"), title = el("td"), name = button(snap.name, "text-button", () => loadSnapshot(snap.id));
      title.append(name, el("span", "cell-sub", date(snap.created_at)));
      const board = el("td", "", snap.build ? snap.build.board || "Target unknown" : "Imported report"); if (snap.build?.domain) board.append(el("span", "cell-sub", snap.build.domain));
      const actions = el("td"), buttons = el("div", "snapshot-actions");
      buttons.append(button("Open", "button small", () => loadSnapshot(snap.id)), button("Baseline", "button small", () => chooseComparison("baseline", snap.id)), button("Candidate", "button small", () => chooseComparison("candidate", snap.id)));
      const remove = button("Delete", "button small danger", () => openDelete(snap));
      remove.setAttribute("aria-label", `Delete snapshot ${snap.name}`);
      buttons.append(remove);
      actions.append(buttons); row.append(title, board, numberCell(snap.totals?.ram ?? null), numberCell(snap.totals?.rom ?? null), actions); target.append(row);
    }
  }
  async function loadSnapshot(id, changeView = true) {
    const request = ++state.snapshotRequest;
    const selected = state.current?.id === id ? state.node?.id : null;
    const snapshot = await api(`/api/memory/snapshots/${encodeURIComponent(id)}`);
    if (request !== state.snapshotRequest || !state.snapshots.some((item) => item.id === id)) return;
    state.current = snapshot;
    state.node = selected ? findPath(snapshot.reports?.[state.memory]?.tree, selected)?.at(-1) || null : null;
    state.ancestors = []; state.symbolLimit = 50;
    if (!snapshot.reports?.[state.memory]) state.memory = snapshot.reports?.ram ? "ram" : "rom";
    root.querySelectorAll("[data-memory]").forEach((node) => node.setAttribute("aria-pressed", String(node.dataset.memory === state.memory)));
    try { localStorage.setItem("zme-last-snapshot", id); } catch (_) { /* storage may be unavailable */ }
    renderOverview(); if (changeView) showView("overview");
  }
  function openDelete(snapshot) {
    pendingDelete = snapshot;
    $("delete-name").textContent = snapshot.name;
    $("delete-date").textContent = `Captured ${date(snapshot.created_at)}`;
    $("delete-dialog").querySelectorAll(".dialog-error").forEach((node) => node.remove());
    $("delete-dialog").showModal();
    $("delete-cancel").focus();
  }
  async function deleteSnapshot() {
    if (!pendingDelete || deleting) return;
    const snapshot = pendingDelete;
    deleting = true;
    $("delete-confirm").disabled = true;
    $("delete-cancel").disabled = true;
    $("delete-confirm").textContent = "Deleting…";
    $("delete-dialog").setAttribute("aria-busy", "true");
    try {
      await api(`/api/memory/snapshots/${encodeURIComponent(snapshot.id)}`, {method: "DELETE", body: {}});
      // In-flight list/open requests must not restore a successfully deleted snapshot.
      snapshotsRequest++;
      state.snapshotRequest++;
      updateSnapshots(state.snapshots.filter((item) => item.id !== snapshot.id));
      $("delete-dialog").close();
      pendingDelete = null;
      notice(`Snapshot “${snapshot.name}” deleted.`);
      $("refresh-snapshots").focus();
    } finally {
      deleting = false;
      $("delete-confirm").disabled = false;
      $("delete-cancel").disabled = false;
      $("delete-confirm").textContent = "Delete snapshot";
      $("delete-dialog").removeAttribute("aria-busy");
    }
  }
  function chooseComparison(side, id) {
    invalidateComparison();
    $(side + "-select").value = id; showView("compare");
    if ($("baseline-select").value && $("candidate-select").value) return compare();
    notice(`${side === "baseline" ? "Baseline" : "Candidate"} selected. Choose the other snapshot to compare.`);
  }
  function invalidateComparison() {
    state.compareRequest++;
    state.comparison = null;
    state.changeLimit = 60;
    renderComparison();
  }
  async function compare() {
    const baseline = $("baseline-select").value, candidate = $("candidate-select").value;
    if (!baseline || !candidate) throw new Error("Select both a baseline and a candidate snapshot.");
    const request = ++state.compareRequest;
    $("run-compare").disabled = true; $("run-compare").textContent = "Comparing…";
    try {
      const comparison = await api(`/api/memory/compare?baseline=${encodeURIComponent(baseline)}&candidate=${encodeURIComponent(candidate)}`);
      if (request !== state.compareRequest || baseline !== $("baseline-select").value || candidate !== $("candidate-select").value) return;
      state.comparison = comparison; state.changeLimit = 60; renderComparison();
    }
    finally { $("run-compare").disabled = false; $("run-compare").textContent = "Compare"; }
  }
  function renderComparison() {
    const data = state.comparison; $("compare-empty").hidden = Boolean(data); $("compare-content").hidden = !data; if (!data) return;
    const metrics = $("compare-metrics"); clear(metrics);
    for (const kind of ["ram", "rom"]) {
      const total = data.totals?.[kind] || {};
      const metric = metricCard(kind, total.after, `Baseline ${fmt(total.before)}`, total.delta);
      if (total.delta === null || total.delta === undefined) metric.querySelector(".metric-foot span:last-child").textContent = "Comparison unavailable";
      metrics.append(metric);
    }
    showWarnings($("compare-warnings"), data.warnings || []); renderChanges();
    const config = $("config-table"); clear(config);
    $("config-summary").textContent = `Configuration changes (${data.config_changes?.length || 0})`;
    for (const change of data.config_changes || []) { const row = el("tr"); row.append(el("td", "code", change.key), el("td", "code", change.before ?? "—"), el("td", "code", change.after ?? "—")); config.append(row); }
    if (!data.config_changes?.length) emptyRow(config, "No recorded configuration differences. Imported reports may not include configuration evidence.", 3);
  }
  function renderChanges() {
    if (!state.comparison) return;
    const target = $("changes-table"), chart = $("delta-chart"); clear(target); clear(chart);
    const query = $("change-search").value.trim().toLowerCase(), all = $("show-unchanged").checked;
    const changes = (state.comparison.changes?.[state.compareMemory] || []).filter((item) => (all || item.status !== "unchanged") && `${item.name} ${item.path}`.toLowerCase().includes(query)).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    const top = changes.filter((item) => item.delta).slice(0, 7), max = Math.max(...top.map((item) => Math.abs(item.delta)), 1);
    for (const change of top) {
      const row = el("div", "delta-row"), name = el("span", "delta-name code", change.name); name.title = change.path;
      const track = el("div", "delta-track"), bar = el("div", `delta-bar ${change.delta > 0 ? "increase" : "decrease"}`); bar.style.width = `${Math.abs(change.delta) / max * 48}%`; track.append(bar);
      row.append(name, track, el("span", `numeric ${change.delta > 0 ? "increase-text" : "decrease-text"}`, fmt(change.delta, true))); chart.append(row);
    }
    for (const change of changes.slice(0, state.changeLimit)) {
      const row = el("tr"), status = el("td"), tag = el("span", `status-tag ${change.status}`, change.status); status.append(tag);
      if (change.ambiguous) { const badge = el("span", "status-tag ambiguous", "Grouped"); badge.title = "Duplicate identities are grouped; individual symbol correspondence is ambiguous."; status.append(badge); }
      if (change.attribution_warning) { const badge = el("span", "status-tag ambiguous", "Attribution"); badge.title = change.attribution_warning; status.append(badge); }
      row.append(nameCell(change), status, numberCell(change.before), numberCell(change.after), numberCell(change.delta, true)); target.append(row);
    }
    if (!changes.length) emptyRow(target, "No matching changes for this memory type.", 5);
    $("more-changes").hidden = changes.length <= state.changeLimit;
  }
  function reconcileBuildWorkspace() {
    if (state.workspacePath !== context.workspace.path) {
      $("project-path").value = context.workspace.path || "";
      state.workspacePath = context.workspace.path;
      state.ui ||= {};
      state.ui["project-path"] = $("project-path").value;
      discoveryRequest++;
      state.build = null;
      clear($("build-results"));
      $("build-results").append(el("p", "muted", "Search a folder to find available builds."));
      $("build-selection").hidden = true;
      showWarnings($("discovery-warnings"), []);
      $("discover-button").disabled = false;
      $("discover-button").textContent = "Find builds";
    }
  }
  function openBuild() {
    inspection.clear();
    $("inspection-dialog").close();
    reconcileBuildWorkspace();
    showView(state.view);
    if (!$("build-dialog").open) $("build-dialog").showModal();
    $("project-path").focus();
  }
  function renderInspection() {
    if (disposed) return;
    const {preview, loading, error, selection} = state.inspection;
    $("inspection-status").textContent = loading ? "Checking the inspected image…" : preview?.build.ready ? "Ready to generate RAM and ROM reports from this image." : "This image is unavailable for report generation.";
    $("inspection-image").textContent = selection?.domain || "Unknown image";
    $("inspection-target").textContent = preview?.context?.effective_target || "Target unknown";
    $("inspection-summary").hidden = !preview;
    showWarnings($("inspection-errors"), [error, preview?.build.error]);
    const identity = $("inspection-identity"); clear(identity);
    if (preview) {
      for (const [label, value] of [["Build directory", preview.build.path], ["ELF", preview.elf.path], ["ELF SHA-256", preview.elf.sha256]]) {
        identity.append(el("dt", "", label), el("dd", "code", value));
      }
      if (preview.context?.cached_board) identity.append(el("dt", "", "Cached board"), el("dd", "code", preview.context.cached_board));
    }
    $("inspection-details").hidden = !preview;
    $("inspection-refresh").disabled = loading || !selection || generating;
    $("inspection-generate").disabled = generating || Boolean(state.job) || !inspection.canGenerate();
    $("inspection-job-hint").hidden = !state.job;
  }
  function navigate(view) {
    if (disposed) return;
    reconcileBuildWorkspace();
    if (view === "inspect" || view?.startsWith("inspect/")) {
      discoveryRequest++;
      state.build = null;
      clear($("build-results"));
      $("build-results").append(el("p", "muted", "Search a folder to find available builds."));
      $("discover-button").disabled = false;
      $("discover-button").textContent = "Find builds";
      $("build-selection").hidden = true;
      $("folder-dialog").close();
      $("build-dialog").close();
      $("inspection-details").open = false;
      showView("overview", false);
      run(() => inspection.open(view));
      if (!$("inspection-dialog").open) $("inspection-dialog").showModal();
    } else {
      inspection.clear();
      $("inspection-dialog").close();
      showView(view || "overview", false);
    }
  }
  function rememberPath(path) {
    try { const recent = JSON.parse(localStorage.getItem("zme-recent-paths") || "[]"); localStorage.setItem("zme-recent-paths", JSON.stringify([path, ...recent.filter((item) => item !== path)].slice(0, 8))); renderRecentPaths(); } catch (_) { /* optional convenience */ }
  }
  function renderRecentPaths() {
    try { clear($("recent-paths")); for (const path of JSON.parse(localStorage.getItem("zme-recent-paths") || "[]")) { const option = el("option"); option.value = path; $("recent-paths").append(option); } } catch (_) { /* optional convenience */ }
  }
  async function discover() {
    const path = $("project-path").value.trim(); if (!path) throw new Error("Enter a project or build directory.");
    inspection.clear();
    const captured = ++discoveryRequest, workspace = context.workspace.path;
    const current = () => !disposed && captured === discoveryRequest && context.workspace.path === workspace;
    $("discover-button").disabled = true; $("discover-button").textContent = "Searching…"; clear($("build-results")); $("build-results").append(el("p", "loader", "Looking for Zephyr builds…")); state.build = null; $("build-selection").hidden = true;
    try {
      const response = await api(`/api/memory/builds?path=${encodeURIComponent(path)}`);
      if (!current()) return;
      rememberPath(path); clear($("build-results")); showWarnings($("discovery-warnings"), response.warnings);
      for (const build of response.builds || []) {
        const label = el("label", `build-card${build.ready ? "" : " unready"}`), radio = el("input"); radio.type = "radio"; radio.name = "selected-build"; radio.value = build.path; radio.disabled = !build.ready;
        const content = el("div", "build-card-content"); content.append(el("strong", "", `${tail(build.application || build.path)}${build.domain ? ` / ${build.domain}` : ""}`), el("span", "cell-sub code", build.path));
        const labels = el("div", "build-labels"); labels.append(el("span", "badge quiet", build.board || "Board unknown"), el("span", "badge quiet", build.ready ? "READY TO ANALYZE" : "UNAVAILABLE")); if (build.zephyr_version) labels.append(el("span", "badge quiet", `Zephyr ${build.zephyr_version}`)); content.append(labels);
        if (build.error) content.append(el("span", "cell-sub", build.error));
        listen(radio, "change", () => { if (!current()) return; inspection.clear(); state.build = build; root.querySelectorAll(".build-card").forEach((node) => node.classList.remove("selected")); label.classList.add("selected"); $("build-selection").hidden = false; $("selected-build-hint").textContent = `Analyze ${build.domain || tail(build.path)} · no rebuild`; });
        label.append(radio, content); $("build-results").append(label);
      }
      if (!response.builds?.length) $("build-results").append(el("p", "loader", "No Zephyr builds found. Select a directory containing a completed build with CMakeCache.txt and a final ELF."));
      if (response.builds?.filter((build) => build.ready).length === 1) { const radio = $("build-results").querySelector("input:not(:disabled)"); radio.checked = true; radio.dispatchEvent(new Event("change")); }
    } catch (error) { if (current()) { clear($("build-results")); $("build-results").append(el("p", "warning-item", error.message)); } }
    finally { if (current()) { $("discover-button").disabled = false; $("discover-button").textContent = "Find builds"; } }
  }
  async function browse(path) {
    const response = await api(`/api/folders?path=${encodeURIComponent(path)}`); state.folder = response; $("folder-path").value = response.path; $("folder-up").disabled = !response.parent; clear($("folder-list"));
    for (const folder of response.folders || []) $("folder-list").append(button(folder.name, "", () => browse(folder.path)));
    if (!response.folders?.length) $("folder-list").append(el("p", "loader", "No subfolders in this directory."));
  }
  async function generate() {
    reconcileBuildWorkspace();
    if (!state.build?.ready) throw new Error("Select a completed build first.");
    await startReport({build_path: state.build.path, name: $("capture-name").value.trim()});
  }
  async function generateInspection() {
    try { await startReport(inspection.jobBody($("inspection-name").value.trim())); }
    catch (error) { inspection.invalidate(error.message || String(error)); throw error; }
  }
  async function startReport(body) {
    if (state.job) throw new Error("Wait for the current report job or cancel it before starting another.");
    if (generating) return;
    generating = true;
    $("generate-button").disabled = true;
    renderInspection();
    try {
      const job = await api("/api/memory/jobs", {method: "POST", body});
      state.job = job.id; state.lastJob = job; state.jobDismissed = false; $("build-dialog").close(); $("inspection-dialog").close(); $("job-panel").hidden = false; $("job-title").textContent = "Analyzing your build…"; $("job-message").textContent = "Reading the existing ELF and creating a durable snapshot."; $("job-activity").hidden = false; $("cancel-job").hidden = false; $("cancel-job").disabled = false; $("dismiss-job").hidden = true; $("job-log-open").hidden = true; $("job-log").textContent = "";
      showView("overview"); pollJob(job.id);
    } finally { generating = false; if (!disposed) { $("generate-button").disabled = false; renderInspection(); } }
  }
  async function pollJob(id) {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      state.lastJob = job;
      renderJob(job);
      if (job.status === "completed") {
        state.job = null;
        renderInspection();
        $("job-activity").hidden = true; $("cancel-job").hidden = true; $("dismiss-job").hidden = false; $("job-title").textContent = "Reports generated & snapshot saved"; $("job-message").textContent = "Your captured reports are ready to explore and compare.";
        await refreshSnapshots();
        if (state.snapshots.some((snapshot) => snapshot.id === job.snapshot_id)) {
          await loadSnapshot(job.snapshot_id);
          notice("Snapshot saved. Your original build has not been rebuilt.");
        } else {
          $("job-title").textContent = "Report generation completed";
          $("job-message").textContent = "This capture has been deleted from saved snapshots.";
        }
      } else if (job.status === "failed" || job.status === "cancelled") {
        state.job = null; $("job-activity").hidden = true; $("cancel-job").hidden = true; $("dismiss-job").hidden = false; $("job-title").textContent = job.status === "cancelled" ? "Report generation cancelled" : "Report generation failed"; $("job-message").textContent = job.error || "The job was cancelled.";
        renderInspection();
        if (job.status === "failed") notice(job.error || "Report generation failed. Check the generation log.", true);
      } else if (state.job === id) later(() => pollJob(id), 700);
    } catch (error) { if (disposed || error.name === "AbortError") return; $("job-activity").hidden = true; $("cancel-job").hidden = true; $("dismiss-job").hidden = false; $("job-title").textContent = "Could not retrieve job status"; $("job-message").textContent = "The analysis may still be running. Refresh snapshots to check for a completed capture."; notice(error.message, true); if (state.job === id) later(() => pollJob(id), 3000); }
  }
  async function importReports(event) {
    event.preventDefault();
    const ram = $("ram-file").files[0], rom = $("rom-file").files[0];
    if (!ram && !rom) throw new Error("Choose at least one RAM or ROM JSON file.");
    $("import-submit").disabled = true; $("import-submit").textContent = "Importing…";
    try {
      const parse = async (file) => { if (!file) return null; if (file.size > 32 * 1024 * 1024) throw new Error(`${file.name} exceeds the 32 MiB report limit.`); try { return JSON.parse(await file.text()); } catch (_) { throw new Error(`${file.name} is not valid JSON.`); } };
      const snap = await api("/api/memory/import", {method: "POST", body: {ram: await parse(ram), rom: await parse(rom), name: $("import-name").value.trim()}});
      $("import-dialog").close(); $("import-form").reset(); await refreshSnapshots(); await loadSnapshot(snap.id); notice("Reports imported and saved as a snapshot.");
    } finally { $("import-submit").disabled = false; $("import-submit").textContent = "Import & save snapshot"; }
  }
  async function saveSnapshot(event) {
    event.preventDefault(); if (!state.current) return;
    const name = $("snapshot-name").value.trim(); if (!name) throw new Error("Enter a snapshot name.");
    state.current = await api(`/api/memory/snapshots/${encodeURIComponent(state.current.id)}`, {method: "PATCH", body: {name, notes: $("snapshot-notes").value}});
    invalidateComparison();
    $("edit-dialog").close(); await refreshSnapshots(); renderOverview(); notice("Snapshot name and notes saved.");
  }
  async function exportSnapshot(format) {
    if (!state.current) return;
    const response = await fetch(`/api/memory/snapshots/${encodeURIComponent(state.current.id)}/export?format=${format}`, {headers: {"X-Session-Token": state.token}, signal: lifetime.signal});
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || "Could not export this snapshot."); }
    const blob = await response.blob();
    if (disposed) return;
    const url = URL.createObjectURL(blob), link = el("a"); downloads.add(url);
    link.href = url; link.download = `${state.current.name.replace(/[^a-zA-Z0-9_.-]/g, "-") || "snapshot"}.${format}`; root.append(link); link.click(); link.remove(); later(() => { URL.revokeObjectURL(url); downloads.delete(url); }, 1000);
  }
  function bind() {
    root.querySelectorAll("[data-view]").forEach((node) => listen(node, "click", () => showView(node.dataset.view)));
    root.querySelectorAll("[data-close]").forEach((node) => listen(node, "click", () => $(node.dataset.close).close()));
    root.querySelectorAll("[data-memory]").forEach((node) => listen(node, "click", () => setMemory(node.dataset.memory)));
    root.querySelectorAll("[data-compare-memory]").forEach((node) => listen(node, "click", () => { state.compareMemory = node.dataset.compareMemory; root.querySelectorAll("[data-compare-memory]").forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.compareMemory === state.compareMemory))); state.changeLimit = 60; renderChanges(); }));
    root.querySelectorAll("[data-export]").forEach((node) => listen(node, "click", () => run(() => exportSnapshot(node.dataset.export))));
    for (const id of ["build-open", "empty-build-open", "explorer-build-open", "snapshots-build-open"]) listen($(id), "click", openBuild);
    for (const id of ["import-open", "empty-import-open"]) listen($(id), "click", () => $("import-dialog").showModal());
    listen($("notice-close"), "click", () => { $("notice").hidden = true; });
    listen($("discover-form"), "submit", (event) => { event.preventDefault(); run(discover); });
    listen($("browse-open"), "click", () => run(async () => { await browse($("project-path").value.trim() || "/"); $("folder-dialog").showModal(); }));
    listen($("folder-form"), "submit", (event) => { event.preventDefault(); run(() => browse($("folder-path").value)); });
    listen($("folder-up"), "click", () => run(() => browse(state.folder.parent)));
    listen($("use-folder"), "click", () => { $("project-path").value = state.folder.path; $("folder-dialog").close(); });
    listen($("generate-button"), "click", () => run(generate));
    listen($("inspection-generate"), "click", () => run(generateInspection));
    listen($("inspection-refresh"), "click", () => run(() => inspection.refresh()));
    listen($("inspection-choose"), "click", openBuild);
    listen($("cancel-job"), "click", () => run(async () => { if (state.job) { $("cancel-job").disabled = true; try { await api(`/api/jobs/${encodeURIComponent(state.job)}/cancel`, {method: "POST", body: {}}); } catch (error) { $("cancel-job").disabled = false; throw error; } } }));
    listen($("dismiss-job"), "click", () => { state.jobDismissed = true; $("job-panel").hidden = true; $("job-log-open").hidden = false; });
    listen($("job-log-open"), "click", () => { state.jobDismissed = false; $("job-panel").hidden = false; $("job-log-open").hidden = true; $("job-panel").querySelector("details").open = true; $("job-panel").scrollIntoView({block: "start"}); });
    listen($("import-form"), "submit", (event) => { event.preventDefault(); run(() => importReports(event)); });
    listen($("edit-snapshot"), "click", () => { if (state.current) { $("snapshot-name").value = state.current.name; $("snapshot-notes").value = state.current.notes || ""; $("edit-dialog").showModal(); } });
    listen($("edit-form"), "submit", (event) => { event.preventDefault(); run(() => saveSnapshot(event)); });
    listen($("delete-form"), "submit", (event) => { event.preventDefault(); run(deleteSnapshot); });
    listen($("delete-dialog"), "cancel", (event) => { if (deleting) event.preventDefault(); });
    listen($("open-explorer"), "click", () => showView("explorer"));
    listen($("overview-compare"), "click", () => run(() => chooseComparison("candidate", state.current.id)));
    listen($("refresh-snapshots"), "click", () => run(refreshSnapshots));
    listen($("run-compare"), "click", () => run(compare));
    listen($("baseline-select"), "change", invalidateComparison);
    listen($("candidate-select"), "change", invalidateComparison);
    listen($("symbol-search"), "input", () => { state.symbolLimit = 50; renderSymbols(); });
    listen($("symbol-sort"), "change", renderSymbols);
    listen($("more-symbols"), "click", () => { state.symbolLimit += 100; renderSymbols(); });
    listen($("more-changes"), "click", () => { state.changeLimit += 100; renderChanges(); });
    listen($("change-search"), "input", () => { state.changeLimit = 60; renderChanges(); });
    listen($("show-unchanged"), "change", () => { state.changeLimit = 60; renderChanges(); });
    listen($("unit-select"), "change", () => { state.units = $("unit-select").value; renderOverview(); renderSnapshots(); renderComparison(); });
    observer = new ResizeObserver(() => { if (!disposed && state.view === "explorer") renderTree(); });
    observer.observe($("treemap"));
  }
  function renderJob(job) {
    const active = !["completed", "failed", "cancelled"].includes(job.status);
    $("job-panel").hidden = Boolean(state.jobDismissed && !active);
    $("job-log-open").hidden = !state.jobDismissed || active;
    $("job-log").textContent = (job.logs || []).join("\n");
    $("job-activity").hidden = !active;
    $("cancel-job").hidden = !active;
    $("cancel-job").disabled = false;
    $("dismiss-job").hidden = active;
    $("job-title").textContent = active ? "Analyzing your build…" : job.status === "completed" ? "Reports generated & snapshot saved" : job.status === "cancelled" ? "Report generation cancelled" : "Report generation failed";
    $("job-message").textContent = active ? "Report generation continues while you use another tool." : job.error || (job.status === "completed" ? "Your captured reports are ready to explore and compare." : "The job was cancelled.");
    renderInspection();
  }
  const persistedInputs = ["baseline-select", "candidate-select", "symbol-search", "symbol-sort", "change-search", "show-unchanged", "project-path"];
  async function init() {
    $("project-path").value = state.ui?.["project-path"] || context.workspace.path || context.session.initial_path || "";
    reconcileBuildWorkspace();
    $("storage-path").textContent = context.session.storage_path ? `Snapshot storage: ${context.session.storage_path}` : "";
    $("unit-select").value = state.units;
    for (const id of persistedInputs) {
      if (state.ui?.[id] === undefined || id.endsWith("-select")) continue;
      if ($(id).type === "checkbox") $(id).checked = state.ui[id]; else $(id).value = state.ui[id];
    }
    root.querySelectorAll("[data-memory]").forEach((node) => node.setAttribute("aria-pressed", String(node.dataset.memory === state.memory)));
    root.querySelectorAll("[data-compare-memory]").forEach((node) => node.setAttribute("aria-pressed", String(node.dataset.compareMemory === state.compareMemory)));
    renderRecentPaths(); renderOverview(); renderComparison();
    if (state.lastJob) renderJob(state.lastJob);
    await refreshSnapshots();
    let previous = state.current?.id;
    if (!previous) { try { previous = localStorage.getItem("zme-last-snapshot"); } catch (_) { /* optional convenience */ } }
    const first = state.snapshots.find((snap) => snap.id === previous) || state.snapshots[0];
    if (first) await loadSnapshot(first.id, false);
    const jobs = await api("/api/jobs");
    if (!state.job) {
      const active = (jobs.jobs || []).find((job) => (job.feature_id === "memory" || job.feature === "memory") && !["completed", "failed", "cancelled"].includes(job.status));
      if (active) { state.job = active.id; state.lastJob = active; state.jobDismissed = false; renderJob(active); }
    }
    if (state.job) pollJob(state.job);
  }
  bind();
  showView(state.view, false);
  run(init);
  return {
    navigate,
    dispose() {
      if (disposed) return;
      state.ui = Object.fromEntries(persistedInputs.map((id) => [id, $(id).type === "checkbox" ? $(id).checked : $(id).value]));
      disposed = true;
      inspection.dispose();
      lifetime.abort();
      observer?.disconnect();
      timers.forEach(clearTimeout);
      frames.forEach(cancelAnimationFrame);
      downloads.forEach((url) => URL.revokeObjectURL(url));
      root.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
      // Cancelling a network request never cancels the server's report job.
    },
  };
}
