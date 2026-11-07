import {createChipSession} from "./chip.js";
import {activationChoices, mountPinAssignment} from "./chip-assignment.js";

const array = (value) => Array.isArray(value) ? value : [];
const message = (value) => typeof value === "string" ? value : value?.message || value?.reason || "";
const signalLabel = (value) => value.label || [value.controller, value.signal].filter(Boolean).join(" · ");
const inventoryLabel = (data) => data?.chip?.inventory === "gpio-slots" ? "Logical GPIO slots" : "Logical pin view";

export function pinMatches(pin, query) {
  return !query || [pin.name, pin.id, ...array(pin.assignments).map(signalLabel), ...array(pin.functions).map(signalLabel)]
    .join(" ").toLowerCase().includes(query.trim().toLowerCase());
}

export function chipFitSize(width, height) {
  return Math.max(0, Math.floor(Math.min(width, height)));
}

function chipGeometry(pinCount) {
  const span = Math.max(330, (Math.ceil(pinCount / 4) - 1) * 24);
  const growth = span - 330;
  return {span, size: 900 + growth, width: 400 + growth, height: 440 + growth,
    right: 650 + growth, bottom: 670 + growth, centerX: 450 + growth / 2, centerY: 450 + growth / 2};
}

/** Logical layout deliberately does not claim physical package-pad placement. */
export function pinPositions(pins) {
  const perSide = Math.ceil(pins.length / 4);
  const geometry = chipGeometry(pins.length);
  return pins.map((pin, index) => {
    const side = Math.floor(index / perSide); const position = index % perSide;
    const count = Math.min(perSide, pins.length - side * perSide);
    const offset = 35 + position * geometry.span / Math.max(1, count - 1);
    return {pin, side, x: side === 0 ? 250 : side === 1 ? 250 + offset : side === 2 ? geometry.right : geometry.right - offset,
      y: side === 0 ? 230 + offset : side === 1 ? 230 : side === 2 ? geometry.bottom - offset : geometry.bottom};
  });
}

export function mountChipPanel(root, options) {
  const {state, workspace, onChange} = options;
  const document = root.ownerDocument; const lifetime = new AbortController();
  const retained = state.chipPresentation ||= {selected: null, query: "", zoom: 1};
  let disposed = false; let renderedData; let renderedPin; let lastStale;
  let pinControls = []; let listControls = []; let drag; let resizeObserver; let geometry = chipGeometry(0);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const svgNode = (tag, attributes = {}, text) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const listen = (element, name, fn) => element.addEventListener(name, fn, {signal: lifetime.signal});
  const button = (label, callback) => {
    const item = node("button", "", label); item.type = "button"; listen(item, "click", callback); return item;
  };
  const heading = node("div", "dte-section-heading");
  const inventoryBadge = node("span", "dte-badge", "Logical pin view");
  heading.append(node("h2", "", "Chip and pins"), inventoryBadge);
  const status = node("p", "dte-muted"); status.setAttribute("role", "status");
  const revision = node("p", "dte-muted dte-chip-revision"); revision.hidden = true;
  const error = node("p", "dte-error"); error.setAttribute("role", "alert");
  const actions = node("div", "dte-node-actions");
  const retry = button("Retry discovery", () => { void session.retry(); });
  const cancel = button("Cancel discovery", () => { void session.cancel(); });
  actions.append(retry, cancel);
  const tools = node("div", "dte-chip-tools"); const searchLabel = node("label", "", "Find a pin or function");
  const search = node("input"); search.type = "search"; search.placeholder = "PA5, I2C, UART…"; search.value = retained.query;
  searchLabel.append(search); const count = node("p", "dte-muted");
  const zoomOut = button("Zoom out", () => zoom(-0.25)); const zoomIn = button("Zoom in", () => zoom(0.25));
  const fit = button("Fit chip", () => { retained.zoom = 1; applyZoom(); viewport.scrollLeft = 0; viewport.scrollTop = 0; });
  tools.append(searchLabel, zoomOut, zoomIn, fit);
  const columns = node("div", "dte-chip-columns"); const canvasColumn = node("div", "dte-chip-canvas-column");
  const viewport = node("div", "dte-chip-viewport"); viewport.setAttribute("aria-label", "Logical chip pin diagram");
  const svg = svgNode("svg", {viewBox: "0 0 900 900", class: "dte-chip-svg", role: "group", "aria-label": "Chip pins"});
  viewport.append(svg);
  const legend = node("p", "dte-muted dte-chip-legend", "Outlined: unassigned or unknown · Filled: configured · Marked: restricted. Positions are logical, not package pad locations.");
  const listDetails = node("details", "dte-chip-pin-list"); listDetails.append(node("summary", "", "Searchable pin list"));
  const list = node("div", "dte-chip-list-buttons"); listDetails.append(count, list);
  canvasColumn.append(viewport, legend, listDetails);
  const inspector = node("section", "dte-chip-inspector"); inspector.setAttribute("aria-label", "Selected pin");
  inspector.tabIndex = -1;
  const inspectorHeading = node("div"); const assignmentRoot = node("section", "dte-chip-activation");
  const inspectorContent = node("div"); inspector.append(inspectorHeading, assignmentRoot, inspectorContent);
  const assignment = mountPinAssignment(assignmentRoot, options);
  columns.append(canvasColumn, inspector);
  const diagnostics = node("details", "dte-chip-evidence"); diagnostics.append(node("summary", "", "Discovery coverage and evidence"));
  const evidence = node("div"); diagnostics.append(evidence);
  root.append(heading, status, revision, error, actions, tools, columns, diagnostics);
  const session = createChipSession({...options, onChange});

  // Larger logical inventories retain the same maximum label size as the original diagram.
  function zoomLimit() { return Math.ceil(2.5 * geometry.size / 900 * 4) / 4; }
  function zoom(delta) { retained.zoom = Math.max(1, Math.min(zoomLimit(), retained.zoom + delta)); applyZoom(); }
  function applyZoom() {
    const css = document.defaultView?.getComputedStyle?.(viewport);
    const bounds = viewport.getBoundingClientRect?.();
    const width = bounds ? bounds.width - (parseFloat(css?.borderLeftWidth) || 0) - (parseFloat(css?.borderRightWidth) || 0)
      : viewport.clientWidth || 0;
    const height = parseFloat(css?.maxHeight) || (document.defaultView?.innerHeight || 900) * .76;
    const size = chipFitSize(width, height);
    if (size > 0) {
      const left = viewport.scrollLeft; const top = viewport.scrollTop;
      viewport.style.height = `${size}px`;
      svg.style.width = `${size * retained.zoom}px`; svg.style.height = `${size * retained.zoom}px`;
      viewport.scrollLeft = left; viewport.scrollTop = top;
    }
    zoomOut.disabled = retained.zoom <= 1; zoomIn.disabled = retained.zoom >= zoomLimit();
  }
  function select(id, {focus = false} = {}) {
    retained.selected = id; updateSelection(); renderInspector();
    if (focus) pinControls.find((entry) => entry.pin.id === id)?.element.focus({preventScroll: true});
  }
  function updateSelection() {
    const query = retained.query;
    for (const {pin, element} of pinControls) {
      const selected = pin.id === retained.selected;
      element.setAttribute("aria-pressed", String(selected)); element.setAttribute("tabindex", selected ? "0" : "-1");
      element.setAttribute("data-match", String(pinMatches(pin, query)));
    }
    const matching = array(state.chipView.data?.pins).filter((pin) => pinMatches(pin, query));
    for (const {pin, element} of listControls) {
      element.hidden = !pinMatches(pin, query); element.setAttribute("aria-pressed", String(pin.id === retained.selected));
    }
    count.textContent = `${matching.length} matching ${state.chipView.data?.chip?.inventory === "gpio-slots" ? "GPIO slots" : "pins"}.`;
    if (query.trim()) listDetails.open = true;
  }
  function addText(parent, label, value) {
    if (value == null || value === "") return;
    const line = node("p", "dte-muted"); line.append(node("strong", "", `${label}: `), node("span", "", String(value))); parent.append(line);
  }
  function renderInspector() {
    const data = state.chipView.data; const pin = array(data?.pins).find((item) => item.id === retained.selected);
    assignment.render(data, pin);
    if (renderedPin === pin && lastStale === state.chipView.stale) return;
    renderedPin = pin; lastStale = state.chipView.stale; inspectorHeading.replaceChildren(); inspectorContent.replaceChildren();
    if (!pin) { inspectorContent.append(node("h3", "", "Select a pin"), node("p", "dte-muted", "Choose a pin on the chip or use the searchable list to inspect its assignments and supported function evidence.")); return; }
    inspectorHeading.append(node("h3", "", pin.name || pin.id));
    if (state.chipView.stale) inspectorContent.append(node("p", "dte-notice", "Previous observation. Current assignments are being refreshed."));
    addText(inspectorHeading, "Port / index", [pin.port, pin.index].filter((value) => value != null).join(" / "));
    addText(inspectorHeading, "Package pad", pin.pad || pin.package_pad);
    for (const restriction of array(pin.restrictions)) inspectorContent.append(node("p", "dte-notice", message(restriction)));
    inspectorContent.append(node("h4", "", "Configured assignments"));
    if (!array(pin.assignments).length) inspectorContent.append(node("p", "dte-muted", "No assignment decoded from the observed source. This does not establish that board wiring is unused."));
    for (const assignment of array(pin.assignments)) {
      const item = node("div", "dte-chip-assignment"); item.append(node("strong", "", signalLabel(assignment) || "Configured GPIO"));
      addText(item, "State", assignment.state); addText(item, "Origin", assignment.origin);
      const source = assignment.source;
      addText(item, "Source", typeof source === "string" ? source : source?.path ? `${source.path}${source.line ? ":" + source.line : ""}` : null);
      inspectorContent.append(item);
    }
    const functionEvidence = data?.activation ? node("details", "dte-chip-function-evidence") : inspectorContent;
    if (data?.activation) { functionEvidence.append(node("summary", "", "Other function evidence")); inspectorContent.append(functionEvidence); }
    else functionEvidence.append(node("h4", "", "Function alternatives"));
    const implemented = activationChoices(data, pin);
    const signalId = (value) => String(value || "").replace(/^(?:UART|UARTE|TWIM?|SPIM?)_/, "").toLowerCase();
    const functions = array(pin.functions).filter((entry) => !data?.activation || !implemented.some((choice) =>
      choice.kind === "gpio" ? entry.signal === "GPIO" : choice.controller === entry.controller && choice.signal === signalId(entry.signal)));
    if (!functions.length) functionEvidence.append(node("p", "dte-muted", "No verified function alternatives are available for this pin yet."));
    const reasons = new Map();
    for (const option of functions) if (option.reason) reasons.set(option.reason, (reasons.get(option.reason) || 0) + 1);
    const sharedReasons = new Set([...reasons].filter(([, count]) => count > 1).map(([reason]) => reason));
    for (const option of functions) {
      const item = node("div", "dte-chip-function");
      item.append(node("strong", "", signalLabel(option) || "Function"));
      if (option.reason && !sharedReasons.has(option.reason)) item.append(node("p", "dte-muted", option.reason));
      item.setAttribute("aria-disabled", "true"); functionEvidence.append(item);
    }
    for (const reason of sharedReasons) functionEvidence.append(node("p", "dte-muted", reason));
    const explained = [...reasons.keys()].some((reason) => /assignment.*(?:construction|verified)/i.test(reason));
    inspectorContent.append(node("p", "dte-notice", `${explained || data?.activation ? "" : "Pin assignment is under construction. "}Configured labels describe source settings, not runtime electrical activity.`));
  }
  function renderPins(data) {
    geometry = chipGeometry(array(data?.pins).length);
    retained.zoom = Math.min(retained.zoom, zoomLimit());
    const activePin = pinControls.find((item) => item.element === document.activeElement);
    const activeList = listControls.find((item) => item.element === document.activeElement);
    const title = svgNode("title", {}, data?.chip?.name || state.data?.target?.requested || "Selected chip");
    svg.replaceChildren(title); list.replaceChildren(); pinControls = []; listControls = [];
    svg.setAttribute("viewBox", `0 0 ${geometry.size} ${geometry.size}`);
    svg.append(svgNode("rect", {x: 250, y: 230, width: geometry.width, height: geometry.height, rx: 10, class: "dte-chip-body"}),
      svgNode("text", {x: geometry.centerX, y: geometry.centerY - 30, "text-anchor": "middle", class: "dte-chip-name"}, data?.chip?.name || "Selected chip"),
      svgNode("text", {x: geometry.centerX, y: geometry.centerY + 5, "text-anchor": "middle", class: "dte-chip-package"}, data?.chip?.package || "Package not established"),
      svgNode("text", {x: geometry.centerX, y: geometry.centerY + 40, "text-anchor": "middle", class: "dte-chip-package"}, inventoryLabel(data)));
    for (const {pin, side, x, y} of pinPositions(array(data?.pins))) {
      const assigned = array(pin.assignments).length > 0; const restricted = array(pin.restrictions).length > 0;
      const name = pin.name || pin.id; const assignment = [...new Set(array(pin.assignments).map(signalLabel).filter(Boolean))].join(", ");
      const group = svgNode("g", {class: "dte-chip-pin", role: "button", tabindex: "-1", "aria-pressed": "false",
        "aria-label": `${name}${assignment ? ": " + assignment : ""}${restricted ? ", restricted" : ""}`,
        "data-assigned": String(assigned), "data-restricted": String(restricted)});
      group.append(svgNode("title", {}, `${name}${assignment ? " · " + assignment : ""}`));
      const horizontal = side === 0 || side === 2; const direction = side === 0 || side === 1 ? -1 : 1;
      const endX = horizontal ? x + direction * 205 : x; const endY = horizontal ? y : y + direction * 205;
      group.append(svgNode("line", {x1: x, y1: y, x2: endX, y2: endY}), svgNode("circle", {cx: x, cy: y, r: 3.5}));
      const badge = svgNode("g", {transform: horizontal ? `translate(${x + direction * 120} ${y})` : `translate(${x} ${y + direction * 120}) rotate(-90)`});
      badge.append(svgNode("rect", {x: -87, y: -10, width: 174, height: 20, rx: 5}),
        svgNode("text", {x: 0, y: 4, "text-anchor": "middle"}, assignment ? `${name} · ${assignment}`.slice(0, 29) : name));
      group.append(badge); svg.append(group); pinControls.push({pin, element: group});
      listen(group, "click", () => select(pin.id));
      listen(group, "keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(pin.id); }
        if (["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"].includes(event.key)) {
          event.preventDefault(); const index = pinControls.findIndex((entry) => entry.pin.id === pin.id);
          const next = event.key === "Home" ? 0 : event.key === "End" ? pinControls.length - 1
            : (index + (["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 1) + pinControls.length) % pinControls.length;
          select(pinControls[next].pin.id, {focus: true});
        }
      });
      const row = button(`${name}${assignment ? " · " + assignment : ""}${restricted ? " · Restricted" : ""}`, () => select(pin.id));
      row.dataset.pinId = pin.id; list.append(row); listControls.push({pin, element: row});
    }
    if (data && !array(data.pins).some((pin) => pin.id === retained.selected)) retained.selected = data.pins[0]?.id || null;
    updateSelection(); applyZoom(); renderedPin = null;
    if (activePin) pinControls.find((item) => item.pin.id === activePin.pin.id)?.element.focus({preventScroll: true});
    else if (activeList) listControls.find((item) => item.pin.id === activeList.pin.id)?.element.focus({preventScroll: true});
  }
  listen(search, "input", () => { retained.query = search.value; updateSelection(); });
  listen(root, "keydown", (event) => {
    if (event.key === "Escape" && inspector.contains(document.activeElement)) {
      event.preventDefault(); pinControls.find((entry) => entry.pin.id === retained.selected)?.element.focus({preventScroll: true});
    }
  });
  listen(viewport, "pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest?.(".dte-chip-pin")) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
    viewport.setPointerCapture?.(event.pointerId);
  });
  listen(viewport, "pointermove", (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    viewport.scrollLeft = drag.left - event.clientX + drag.x; viewport.scrollTop = drag.top - event.clientY + drag.y;
  });
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) listen(viewport, name, () => { drag = null; });
  if (document.defaultView?.ResizeObserver) {
    let previousWidth;
    resizeObserver = new document.defaultView.ResizeObserver((entries) => {
      if (disposed) return;
      const width = entries[0]?.contentRect.width;
      if (width !== previousWidth) { previousWidth = width; applyZoom(); }
    });
    resizeObserver.observe(canvasColumn);
  }
  if (document.defaultView?.addEventListener) listen(document.defaultView, "resize", applyZoom);

  return {render() {
    if (disposed) return;
    session.sync(); const view = state.chipView; const data = view.data;
    const scope = JSON.stringify([workspace(), state.data?.application?.path, state.data?.target?.requested]);
    if (state.data && retained.scope !== scope) { retained.scope = scope; retained.selected = null; retained.query = ""; search.value = ""; retained.zoom = 1; }
    root.setAttribute("aria-busy", String(view.busy));
    status.textContent = view.error ? "" : view.message || (state.loading ? "Opening the selected project…" : "Chip discovery starts automatically when a target is selected.");
    status.hidden = !status.textContent; error.textContent = view.error; error.hidden = !view.error;
    retry.hidden = view.busy || !view.error && !view.message.includes("cancelled") && data?.supported !== false && !data?.assignment_reason;
    cancel.hidden = !view.busy; cancel.disabled = !view.jobId;
    actions.hidden = retry.hidden && cancel.hidden;
    tools.hidden = !data?.pins?.length; columns.setAttribute("data-stale", String(view.stale));
    if (renderedData !== data) {
      renderedData = data; renderPins(data); evidence.replaceChildren();
      const slots = data?.chip?.inventory === "gpio-slots";
      const boardRevision = data?.selection?.revision;
      revision.hidden = !boardRevision?.effective;
      revision.textContent = boardRevision?.effective ? `Board revision ${boardRevision.effective}`
        + (boardRevision.match === "default" ? " selected automatically."
          : boardRevision.match === "compatible" ? ` selected for requested revision ${boardRevision.requested}.` : ".") : "";
      inventoryBadge.textContent = inventoryLabel(data);
      viewport.setAttribute("aria-label", slots ? "Logical GPIO slot diagram" : "Logical chip pin diagram");
      svg.setAttribute("aria-label", slots ? "Chip GPIO slots" : "Chip pins");
      legend.textContent = "Outlined: unassigned or unknown · Filled: configured · Marked: restricted. "
        + (slots ? "Slots are logical GPIO indices from DTS, not confirmed package pads or board connections."
          : "Positions are logical, not package pad locations.");
      addText(evidence, "Coverage", data?.coverage); addText(evidence, "Assignments", data?.assignment_reason);
      addText(evidence, "Requested board revision", boardRevision?.requested);
      addText(evidence, "Resolved board revision", boardRevision?.effective);
      addText(evidence, "Board revision overlays", array(data?.selection?.board_overlays).join("\n"));
      addText(evidence, "Inventory", slots ? "GPIO controller slots from the selected DTS. Package bonding and board wiring are not established."
        : data?.chip?.inventory === "pin-table" ? "Pins and alternatives from the selected Zephyr package pin table." : null);
      if (data?.reason) evidence.append(node("p", "dte-notice", data.reason));
      for (const item of array(data?.diagnostics)) evidence.append(node("p", "dte-muted", message(item)));
      diagnostics.hidden = !data;
    }
    renderInspector();
  }, navigate() { session.navigate(); }, dispose() { disposed = true; lifetime.abort(); resizeObserver?.disconnect(); assignment.dispose(); session.dispose(); }};
}
