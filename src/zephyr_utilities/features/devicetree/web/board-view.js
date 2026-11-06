import {announceResult} from "./view-feedback.js";
import {retainActionFocus} from "./view-focus.js";
import {boardAvailability, boardCanConnect, boardConnection, boardConnections, boardConstructionNotice, boardLoadedMessage, boardOption, createBoardSession} from "./board.js";

const array = (value) => Array.isArray(value) ? value : [];
const recipeNames = {i2c: "BME280 sensor", "gpio-led": "LED"};

export function mountBoardPanel(root, {api, state, workspace, drafts, notify, onChange, onOpenProjects}) {
  const document = root.ownerDocument; const lifetime = new AbortController(); const handlers = new WeakMap();
  let disposed = false; let navigation = 0; let data; let rendered; let selectedKind = ""; let selectedId = ""; let feedbackHost = null;
  const make = (tag, text, className = "") => {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className; return node;
  };
  const listen = (node, event, callback) => { if (!handlers.has(node)) handlers.set(node, {}); handlers.get(node)[event] = callback; };
  for (const type of ["click", "change", "submit"]) root.addEventListener(type, (event) => {
    if (type === "submit" || type === "click" && event.target.closest("button")) {
      feedbackHost = event.target.closest("form");
    }
    for (let node = event.target; node && node !== root; node = node.parentNode) {
      handlers.get(node)?.[type]?.(event); if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (title, action, primary = false) => {
    const node = make("button", title, primary ? "dte-primary" : ""); node.type = "button"; node.dataset.focusKey = "button:" + title;
    if (action) listen(node, "click", action); return node;
  };
  const label = (title, control) => { control.dataset.focusKey = "field:" + title; const node = make("label", title); node.append(control); return node; };
  const input = (value = "") => { const node = make("input"); node.type = "text"; node.value = String(value); node.autocomplete = "off"; return node; };
  const option = (select, value, title, disabled = false) => { const node = make("option", title); node.value = value; node.disabled = disabled; select.append(node); };
  const note = (parent, text, className = "dte-muted") => { if (text) parent.append(make("p", text, className)); };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load board hardware", () => run(() => session.load()), true);
  const cancel = button("Cancel discovery", () => { navigation++; session.cancel(); });
  heading.append(make("h3", "Hardware workspace"), load, cancel);
  const status = make("p", "", "dte-muted"); status.setAttribute("role", "status");
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert");
  const boardCard = make("article", undefined, "dte-board-card");
  const boardName = make("h4"); const boardTarget = make("p", "", "dte-path"); const boardState = make("p", "", "dte-muted");
  const projects = button("Open Projects", () => onOpenProjects?.()); boardCard.append(boardName, boardTarget, boardState, projects);
  const availability = make("section", undefined, "dte-board-blocker"); availability.setAttribute("aria-label", "Visual connection availability");
  availability.setAttribute("aria-live", "polite"); availability.setAttribute("aria-atomic", "true");
  const availabilityHeading = make("h4"); const availabilityReason = make("p");
  availability.append(availabilityHeading, make("p", boardConstructionNotice), availabilityReason);
  note(availability, "Open Projects to choose another board configuration, or load board hardware again after updating your setup.");
  const recovery = make("div"); const reconcile = button("Reconcile hardware draft", () => run(() => session.reconcile())); recovery.append(reconcile);
  const recipeBar = make("div", undefined, "dte-node-actions dte-board-recipes"); const recipeButtons = new Map();
  const inspectAll = button("All hardware", () => {
    if (selectedKind) { selectedKind = ""; renderConnections(); renderDetail(); updateDisabled(); }
    else renderSelection();
    announceResult(workspaceView, notify, {message: "All board hardware connections are shown below. Choose a connection to inspect its devices and routing."});
  }); recipeBar.append(inspectAll);
  for (const [kind, title] of Object.entries(recipeNames)) {
    const choose = button("Add " + title, () => {
      if (selectedKind !== kind) {
        selectedKind = kind;
        if (!boardConnections(data, kind).some((node) => node.id === selectedId)) selectedId = "";
        renderConnections(); renderDetail(); updateDisabled();
      } else renderSelection();
      announceResult(detail, notify, {message: guidance.textContent});
    }); recipeButtons.set(kind, choose); recipeBar.append(choose);
  }
  const guidance = make("p", "", "dte-muted"); guidance.setAttribute("role", "status");
  const recipeAvailability = make("div", undefined, "dte-notice");
  const workspaceView = make("div", undefined, "dte-board-workspace");
  const connections = make("ul", undefined, "dte-topology dte-board-connections"); connections.setAttribute("aria-label", "Board hardware connections");
  const detail = make("div", undefined, "dte-peripheral-detail dte-board-detail");
  workspaceView.append(connections, detail);
  const diagnostics = make("details"); diagnostics.append(make("summary", "Discovery details")); const diagnosticList = make("div"); diagnostics.append(diagnosticList);
  root.append(heading); note(root, "Choose a sensor or LED and connect it to your board. Changes go into the overlay draft.");
  root.append(boardCard, availability, status, error, recovery, recipeBar, recipeAvailability, guidance, workspaceView, diagnostics);
  const localFeedback = make("p"); localFeedback.setAttribute("role", "status");
  const session = createBoardSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function currentAction() {
    const token = navigation; const captured = workspace(); const context = state.data?.id; const revision = state.data?.revision;
    const draft = state.draftView?.entry?.server?.id; const generation = state.draftView?.entry?.generation;
    return () => !disposed && token === navigation && captured === workspace() && context === state.data?.id
      && revision === state.data?.revision && draft === state.draftView?.entry?.server?.id && generation === state.draftView?.entry?.generation;
  }
  function announceFeedback() {
    const view = state.boardView;
    const destination = feedbackHost && localFeedback.parentNode ? localFeedback : view.error ? error : status.hidden ? availability : status;
    announceResult(destination, notify, {error: Boolean(view.error), message: destination === availability ? view.message : undefined});
  }
  function run(operation) {
    const current = currentAction();
    return retainActionFocus(root, async () => {
      const result = await operation(); if (current() && (result !== false || state.boardView.error)) announceFeedback(); return result;
    }, current, lifetime.signal);
  }
  function renderFeedback() {
    const view = state.boardView;
    if (!feedbackHost) { localFeedback.remove?.(); return; }
    const form = [...detail.querySelectorAll("form")][0];
    if (!form) { localFeedback.remove?.(); return; }
    feedbackHost = form; localFeedback.textContent = view.error || view.message || ""; localFeedback.hidden = !localFeedback.textContent;
    localFeedback.className = view.error ? "dte-error" : "dte-muted";
    if (localFeedback.parentNode !== form) form.append(localFeedback);
  }
  function renderConnections() {
    connections.replaceChildren();
    if (!data) return;
    for (const controller of boardConnections(data, selectedKind)) {
      const item = make("li", undefined, "dte-board-connection"); const choose = button("", () => {
        if (selectedId !== controller.id) { selectedId = controller.id; renderSelection(); renderDetail(); updateDisabled(); }
        else renderSelection();
        announceResult(detail, notify, {message: guidance.textContent});
      });
      choose.dataset.controllerId = controller.id;
      choose.append(make("strong", controller.labels?.[0] || controller.name), make("span", `${controller.kind || "GPIO"} · ${controller.status || "unknown"}`, "dte-muted"));
      note(choose, controller.path, "dte-path"); item.append(choose);
      const devices = connectedNodes(controller);
      if (devices.length) {
        const branch = make("ul", undefined, "dte-board-device-list"); branch.setAttribute("aria-label", "Devices connected to " + (controller.labels?.[0] || controller.name));
        for (const node of devices) {
          const device = make("li", undefined, "dte-board-device"); device.append(make("strong", node.name));
          note(device, `${node.status || "unknown"} · ${node.origin || "source"}`, "dte-muted"); branch.append(device);
        }
        item.append(branch);
      }
      connections.append(item);
    }
    renderSelection();
  }
  function renderSelection() {
    inspectAll.setAttribute("aria-pressed", String(!selectedKind));
    for (const [kind, node] of recipeButtons) node.setAttribute("aria-pressed", String(kind === selectedKind));
    for (const node of connections.querySelectorAll("button")) node.setAttribute("aria-pressed", String(node.dataset.controllerId === selectedId));
    guidance.textContent = selectedKind ? `Choose a connection for the ${recipeNames[selectedKind]}.` : "Choose a device to add, or inspect a connection below.";
    const controller = data && boardConnections(data).find((node) => node.id === selectedId);
    if (controller) {
      const name = controller.labels?.[0] || controller.name;
      const capability = selectedKind && boardOption(data, controller.id, selectedKind);
      guidance.textContent = !selectedKind ? `Connection details for ${name} are ready below.`
        : capability?.addable === true ? `Complete ${recipeNames[selectedKind]} settings for ${name} below.`
          : capability?.reason || controller.reason || "This connection is not available for the selected device.";
    }
    if (data && !boardConnections(data, selectedKind).length && data.supported) guidance.textContent = selectedKind
      ? `No ${selectedKind === "i2c" ? "I2C" : "GPIO"} connection is available for this device in the current observation.`
      : "The board is loaded, but no supported connections were identified.";
  }
  function connectedNodes(controller) {
    const children = new Set(array(controller.children));
    for (const allocation of array(data.allocations)) if (allocation.provider_id === controller.id && allocation.kind === "gpio") children.add(allocation.owner_id);
    return data.nodes.filter((node) => children.has(node.id) || node.parent_id === controller.id);
  }
  function connectedDevices(controller) {
    const nodes = connectedNodes(controller);
    const section = make("div", undefined, "dte-board-connected"); section.append(make("h5", "Connected devices"));
    if (!nodes.length) note(section, "No child devices are declared on this connection.");
    for (const node of nodes) {
      const item = make("div"); item.append(make("strong", node.name)); note(item, `${node.path} · ${node.status || "unknown"} · ${node.origin || "source"}`, "dte-path"); section.append(item);
    }
    detail.append(section);
  }
  function routing(controller) {
    const routes = array(controller.pinctrl).map((route) => {
      const paths = array(route.targets).map((id) => data.nodes.find((node) => node.id === id)?.path || id);
      return `${route.name || "state " + route.index}: ${paths.join(", ") || "unknown"}`;
    });
    if (routes.length) note(detail, "Existing board pin routing — " + routes.join("; ") + ".", "dte-notice");
  }
  function connectionUnavailable(reason) {
    const notice = make("section", undefined, "dte-board-blocker"); notice.setAttribute("aria-label", "Connection unavailable");
    notice.append(make("h5", "This connection is not supported yet"), make("p", boardConstructionNotice), make("p", reason)); detail.append(notice);
  }
  function connectionForm(controller, capability) {
    const kind = selectedKind; const form = make("form", undefined, "dte-peripheral-form dte-board-form");
    form.append(make("h4", `Connect ${recipeNames[kind]}`)); detail.append(form);
    let address; let pin; let flags; let enable;
    if (kind === "i2c") {
      address = make("select"); option(address, "", "Choose the sensor's I2C address");
      for (const value of [0x76, 0x77]) {
        const occupied = capability.occupied_addresses?.includes(value);
        option(address, String(value), `0x${value.toString(16)}${occupied ? " · already in use" : ""}`, occupied);
      }
      form.append(label("I2C address", address));
      note(form, "Choose the address set by your sensor's wiring. The board's existing I2C pin routing is reused.");
      if (capability.enable_required) {
        enable = make("input"); enable.type = "checkbox"; enable.checked = false;
        form.append(label("Enable this I2C controller", enable));
      }
    } else {
      pin = make("select"); option(pin, "", "Choose a GPIO pin");
      const mappings = new Map();
      for (const connector of array(data.connector_maps)) for (const entry of array(connector.entries)) {
        if (entry.provider_id !== controller.id || !Number.isInteger(entry.parent_cells?.[0])) continue;
        const value = entry.parent_cells[0]; const titles = mappings.get(value) || [];
        titles.push(`${connector.path} entry ${array(entry.child_cells).join(", ")}`); mappings.set(value, titles);
      }
      const pins = Number.isInteger(capability.pin_max) ? Array.from({length: Math.max(0, Math.min(capability.pin_max, 255) - (capability.pin_min ?? 0) + 1)}, (_, index) => index + (capability.pin_min ?? 0)) : [];
      pins.sort((left, right) => Number(mappings.has(right)) - Number(mappings.has(left)) || left - right);
      for (const value of pins) {
        const occupied = capability.occupied_pins?.includes(value);
        const reserved = capability.reserved_ranges?.some(([start, count]) => value >= start && value < start + count);
        option(pin, String(value), `GPIO pin ${value}${mappings.has(value) ? " · " + mappings.get(value).join("; ") : ""}${occupied ? " · in use" : reserved ? " · reserved" : ""}`, occupied || reserved);
      }
      flags = make("select"); option(flags, "", "Choose the LED's active level"); option(flags, "0", "Active high"); option(flags, "1", "Active low");
      form.append(label("GPIO pin", pin), label("LED active level", flags));
      note(form, "GPIO indices identify controller pins, not physical board header positions. Choose the active level that matches your LED wiring.");
    }
    const identity = make("details"); identity.append(make("summary", "Node name and label"));
    let nodeName = kind === "i2c" ? "bme280" : "led";
    if (kind === "gpio-led") for (let suffix = 2; data.nodes.some((node) => node.path === "/app-leds/" + nodeName); suffix++) nodeName = "led_" + suffix;
    const name = input(nodeName); const nodeLabel = input();
    identity.append(label("Node name", name), label("Node label (optional)", nodeLabel)); form.append(identity);
    const apply = button(`Add ${recipeNames[kind]} to draft`, null, true); apply.type = "submit"; form.append(apply);
    listen(form, "submit", (event) => {
      event.preventDefault();
      try {
        const connection = boardConnection(data, controller.id, kind, {address: address?.value, pin: pin?.value, flags: flags?.value,
          enable: enable?.checked === true, name: name.value, label: nodeLabel.value});
        void run(() => session.command({kind: "board_module_add", connection}));
      } catch (error) { session.fail(error.message); announceFeedback(); }
    });
  }
  function renderDetail() {
    detail.replaceChildren(); if (!data) return;
    const controller = boardConnections(data).find((node) => node.id === selectedId);
    if (!controller) { note(detail, selectedKind ? "Select a connection card to choose its settings." : "Select a connection card to see its devices and routing."); return; }
    detail.append(make("h4", controller.labels?.[0] || controller.name)); note(detail, controller.path, "dte-path");
    routing(controller); connectedDevices(controller);
    if (!selectedKind) {
      const available = Object.entries(recipeNames).filter(([kind]) => boardOption(data, controller.id, kind)?.addable === true);
      if (available.length) note(detail, "Choose " + available.map(([, title]) => "Add " + title).join(" or ") + " above to connect a device.");
      else connectionUnavailable([...new Set(Object.keys(recipeNames).map((kind) => boardOption(data, controller.id, kind)?.reason).filter(Boolean))].join(" ")
        || controller.reason || "No supported visual device connection is available for this controller.");
      return;
    }
    const capability = boardOption(data, controller.id, selectedKind);
    if (!data.supported || capability?.addable !== true) { connectionUnavailable(capability?.reason || controller.reason || "This connection is not available for the selected device."); return; }
    connectionForm(controller, capability);
  }
  function updateDisabled() {
    const view = state.boardView; const blocked = view.busy || !session.ready() || !view.data;
    for (const node of workspaceView.querySelectorAll("input, select, textarea, button")) node.disabled = blocked;
    inspectAll.disabled = blocked || view.data?.supported !== true;
    for (const [kind, node] of recipeButtons) node.disabled = blocked || !boardCanConnect(view.data, kind);
    load.disabled = view.busy || !session.ready() || !state.data?.target?.requested;
    load.textContent = view.busy ? "Loading board hardware…" : "Load board hardware";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile(); reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile hardware draft";
  }
  function render() {
    if (disposed) return;
    session.sync(); const view = state.boardView; const target = state.data?.target?.requested;
    boardName.textContent = view.presentation?.board?.full_name || view.presentation?.board?.name || target || "Choose your board";
    boardTarget.textContent = target || "No board configuration selected";
    boardState.textContent = !target ? "Open an application with a board configuration and overlay to see its hardware."
      : view.presentation?.supported === false ? "Visual connections are unavailable for this configuration."
        : view.presentation ? "Board and selected overlay hardware" : "Load the selected board's controllers and connections.";
    const unavailable = target && boardAvailability(view.presentation);
    projects.hidden = Boolean(target) && !unavailable || !onOpenProjects;
    availability.hidden = !unavailable;
    if (availabilityHeading.textContent !== (unavailable?.heading || "")) availabilityHeading.textContent = unavailable?.heading || "";
    if (availabilityReason.textContent !== (unavailable?.reason || "")) availabilityReason.textContent = unavailable?.reason || "";
    status.textContent = view.message || (view.busy ? "Reading the board and selected overlay. This may take a few seconds…" : "");
    status.hidden = Boolean(unavailable && view.data === view.presentation && view.data && !view.busy && !view.error
      && view.message === boardLoadedMessage(view.data));
    error.hidden = !view.error; error.textContent = view.error;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnosticList.replaceChildren(); recipeAvailability.replaceChildren();
      if (!data) { selectedId = ""; feedbackHost = null; connections.replaceChildren(); detail.replaceChildren(); }
      else {
        for (const item of array(data.diagnostics)) note(diagnosticList, item.message || item);
        if (selectedKind && !boardCanConnect(data, selectedKind)) selectedKind = "";
        if (data.supported) for (const [kind, title] of Object.entries(recipeNames)) if (!boardCanConnect(data, kind)) {
          const reasons = [...new Set(boardConnections(data, kind).map((node) => boardOption(data, node.id, kind)?.reason).filter(Boolean))];
          note(recipeAvailability, `${title}: ${reasons.join(" ") || "No supported connection was identified for this device."}`);
        }
        if (!boardConnections(data).some((node) => node.id === selectedId)) selectedId = "";
        renderConnections(); renderDetail();
      }
    }
    recipeAvailability.hidden = !recipeAvailability.textContent;
    guidance.hidden = !data || !data.supported; workspaceView.hidden = !data || data.supported === false;
    diagnostics.hidden = !data || !array(data.diagnostics).length;
    renderFeedback(); updateDisabled();
  }
  render();
  return {render, navigate() { navigation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; navigation++; lifetime.abort(); session.dispose(); }};
}
