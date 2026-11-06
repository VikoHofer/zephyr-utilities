import {retainActionFocus} from "./view-focus.js";
import {createPinSession, gpioKey, pinKey, pinName, selectedFlags, selectedPin} from "./pins.js";

const array = (value) => Array.isArray(value) ? value : [];
const roleName = (role) => role.replaceAll("_", " ");
const present = (value) => value !== undefined && value !== null;

export function mountPinPanel(root, {api, state, workspace, drafts, reveal, onChange}) {
  const document = root.ownerDocument; const lifetime = new AbortController(); const handlers = new WeakMap();
  let disposed = false; let generation = 0; let rendered; let data;
  let controllerId = ""; let stateName = "default"; let role = ""; let selectedGpio = "";
  let signalPin; let gpioPin; let pinDestination = "signal";
  const make = (tag, text, className = "") => {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className; return node;
  };
  const listen = (node, event, callback) => { if (!handlers.has(node)) handlers.set(node, {}); handlers.get(node)[event] = callback; };
  for (const type of ["click", "change", "submit"]) root.addEventListener(type, (event) => {
    if (type === "submit" || type === "click" && event.target.closest("button")) feedbackHost = locateFeedback(event.target);
    for (let node = event.target; node && node !== root; node = node.parentNode) {
      handlers.get(node)?.[type]?.(event); if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (text, action, primary = false) => {
    const node = make("button", text, primary ? "dte-primary" : ""); node.type = "button";
    node.dataset.focusKey = "button:" + text;
    if (action) listen(node, "click", action); return node;
  };
  const label = (text, control) => { control.dataset.focusKey = "field:" + text; const node = make("label", text); node.append(control); return node; };
  const option = (select, value, text) => { const node = make("option", text); node.value = value; select.append(node); return node; };
  const note = (parent, text, className = "dte-muted") => { if (text) parent.append(make("p", text, className)); };
  const permitted = (node, allowed) => { node.dataset.pinAllowed = String(Boolean(allowed)); node.disabled = !allowed; };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load pin assignments", () => run(() => session.load()));
  const cancel = button("Cancel pin inspection", () => { generation++; session.cancel(); });
  heading.append(make("h3", "Pins and signals"), load, cancel);
  const status = make("p", "", "dte-muted"); status.setAttribute("role", "status"); status.tabIndex = -1;
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert"); error.tabIndex = -1;
  const recovery = make("div"); const reconcile = button("Reconcile pin draft", () => run(() => session.reconcile()));
  recovery.append(reconcile); note(recovery, "Reconcile the canonical draft, then load pin assignments again. The previous action is not repeated.");
  const diagnostics = make("div"); const controls = make("div", undefined, "dte-pin-controls");
  const signalForm = make("form", undefined, "dte-pin-form"); signalForm.tabIndex = -1;
  const gpioForm = make("form", undefined, "dte-pin-form"); gpioForm.tabIndex = -1;
  const tableDetails = make("details", undefined, "dte-pin-table-details");
  controls.append(signalForm, gpioForm, tableDetails);
  root.append(heading);
  note(root, "Pin assignment uses a target adapter. The current adapter supports nrf52840dk/nrf52840 with verified local checkout evidence. Default and sleep are separate states. Changes update the working draft; Save and Configure remain separate.");
  root.append(status, error, recovery, diagnostics, controls);
  const feedbackSections = new Set([signalForm, gpioForm]);
  let feedbackHost;
  const localFeedback = document.createElement("p"); localFeedback.setAttribute("role", "status");
  function locateFeedback(target) {
    for (let item = target; item && item !== root; item = item.parentNode) if (feedbackSections.has(item)) return item;
    return null;
  }
  function renderFeedback(view) {
    if (!feedbackHost) { localFeedback.hidden = true; localFeedback.remove?.(); return; }
    localFeedback.className = view.error ? "dte-error" : "dte-muted";
    localFeedback.textContent = view.error || view.message || "";
    localFeedback.hidden = !localFeedback.textContent;
    if (localFeedback.parentNode !== feedbackHost) feedbackHost.append(localFeedback);
  }
  const session = createPinSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function feedback(message) { session.fail(message); }
  function run(operation) {
    const token = generation; const captured = workspace(); const context = state.data?.id;
    const draft = state.draftView?.entry?.server?.id;
    return retainActionFocus(root, operation, () => !disposed && token === generation
      && captured === workspace() && context === state.data?.id && draft === state.draftView?.entry?.server?.id, lifetime.signal);
  }
  function submit(command) { return run(() => session.command(command)); }
  function guarded(action) { return () => { try { action(); } catch (error) { feedback(error.message); } }; }
  function diagnosticsFor(parent, items) { for (const item of array(items)) note(parent, typeof item === "string" ? item : item.message, "dte-notice"); }
  function pinSelect(value) {
    const select = make("select"); option(select, "", "Choose a verified SoC pin");
    for (const pin of data.pins) {
      const item = option(select, pinKey(pin), `${pinName(pin)}${pin.reserved ? " · reserved" : ""}${pin.connector ? " · " + pin.connector : ""}`);
      item.disabled = pin.editable === false;
    }
    if (value && data.pins.some((pin) => pinKey(pin) === value)) select.value = value;
    return select;
  }
  function pinDetail(parent, control, destination) {
    const description = make("p", "", "dte-muted"); parent.append(description);
    const update = () => {
      const pin = data.pins.find((item) => pinKey(item) === control.value);
      description.textContent = pin?.reserved ? `Reserved: ${pin.reason || "known board use"}. Assignment is checked against the owning controller.`
        : pin?.reason || "";
    };
    listen(control, "change", () => { pinDestination = destination; update(); }); update();
    return update;
  }
  function renderSignal(focus) {
    signalForm.replaceChildren(); signalPin = null;
    listen(signalForm, "submit", (event) => event.preventDefault());
    signalForm.append(make("h4", "Peripheral signal assignment"));
    const controllers = make("select"); option(controllers, "", "Choose a controller");
    for (const controller of data.controllers) option(controllers, controller.id, `${controller.label || controller.id} · ${controller.kind.toUpperCase()}${controller.editable === false ? " · unavailable" : ""}`);
    controllers.value = data.controllers.some((item) => item.id === controllerId) ? controllerId : "";
    controllerId = controllers.value;
    signalForm.append(label("Controller", controllers));
    if (focus === "controller") controllers.focus?.({preventScroll: true});
    listen(controllers, "change", () => { controllerId = controllers.value; role = ""; pinDestination = "signal"; renderSignal("controller"); updateDisabled(); });
    const controller = data.controllers.find((item) => item.id === controllerId);
    if (!controller) { note(signalForm, data.controllers.length ? "Choose a controller to inspect its states and signal roles." : "No supported controller declarations were established for this source."); return; }
    note(signalForm, `Status: ${controller.status || "unknown"}. Hardware instance: ${controller.instance || "unknown"}. Assigning pins does not enable the controller.`);
    note(signalForm, controller.reason || (controller.editable === false
      ? "This controller's routing is read-only evidence in this adapter. Use source editing to change it." : ""), "dte-notice");
    diagnosticsFor(signalForm, controller.diagnostics);
    const states = make("select");
    for (const item of array(controller.states)) option(states, item.name, item.name);
    stateName = array(controller.states).some((item) => item.name === stateName) ? stateName : controller.states?.[0]?.name || "";
    states.value = stateName; signalForm.append(label("Pin control state", states));
    if (focus === "state") states.focus?.({preventScroll: true});
    listen(states, "change", () => { stateName = states.value; role = ""; pinDestination = "signal"; renderSignal("state"); updateDisabled(); });
    const selected = array(controller.states).find((item) => item.name === stateName);
    if (!selected) { note(signalForm, "No supported default or sleep state is available."); return; }
    note(signalForm, selected.reason, "dte-notice"); diagnosticsFor(signalForm, selected.diagnostics);
    const roles = make("select"); option(roles, "", "Choose a signal role");
    for (const value of array(selected.roles)) option(roles, value, roleName(value));
    role = array(selected.roles).includes(role) ? role : ""; roles.value = role;
    signalForm.append(label("Signal role", roles));
    if (focus === "role") roles.focus?.({preventScroll: true});
    listen(roles, "change", () => { role = roles.value; pinDestination = "signal"; renderSignal("role"); updateDisabled(); });
    const assignment = array(selected.signals).find((item) => item.role === role);
    if (role) note(signalForm, assignment && present(assignment.port) && present(assignment.pin)
      ? `Current ${roleName(role)}: ${pinName(assignment)}.` : `No resolved ${roleName(role)} assignment in this state.`);
    note(signalForm, assignment?.reason, "dte-notice");
    signalPin = pinSelect(assignment && present(assignment.port) && present(assignment.pin) ? pinKey(assignment) : "");
    signalForm.append(label("Signal pin", signalPin)); pinDetail(signalForm, signalPin, "signal");
    const shared = make("input"); shared.type = "checkbox"; shared.checked = false;
    if (selected.shared) {
      note(signalForm, `This state is shared${array(selected.consumers).length ? " by " + selected.consumers.join(", ") : " with other consumers"}. An explicit split preserves the original group for its other consumers.`, "dte-notice");
      signalForm.append(label("Split this shared state for the selected controller", shared));
    }
    const canChange = () => controller.editable !== false && selected.editable !== false && Boolean(role)
      && assignment?.editable !== false && (!selected.shared || shared.checked);
    const assign = button(assignment ? "Reassign signal in draft" : "Assign signal to draft", null, true); assign.type = "submit"; assign.dataset.focusKey = "signal-apply";
    const unassign = button("Unassign signal from draft", guarded(() => {
      if (!canChange() || !assignment) throw new Error("Choose an editable assigned signal and explicitly split a shared state before removing it.");
      void submit({kind: "pin_unassign", controller: controller.id, state: selected.name, role, split_shared: Boolean(selected.shared && shared.checked)});
    }));
    const actions = make("div", undefined, "dte-node-actions"); actions.append(assign, unassign); signalForm.append(actions);
    const availability = () => { permitted(assign, canChange()); permitted(unassign, canChange() && Boolean(assignment)); };
    availability(); listen(shared, "change", () => { availability(); updateDisabled(); });
    listen(signalForm, "submit", (event) => { event.preventDefault(); guarded(() => {
      if (!canChange()) throw new Error("Choose an editable signal and explicitly split a shared state before changing it.");
      void submit({kind: "pin_assign", controller: controller.id, state: selected.name, role,
        ...selectedPin(data.pins, signalPin.value), split_shared: Boolean(selected.shared && shared.checked)});
    })(); });
    note(signalForm, "Unassign removes this signal from the selected state. Shared groups and other signal roles are preserved.");
  }
  function renderGpio(focus = false) {
    gpioForm.replaceChildren(); gpioPin = null;
    listen(gpioForm, "submit", (event) => event.preventDefault());
    gpioForm.append(make("h4", "GPIO assignment and flags"));
    const select = make("select"); option(select, "", "Choose a GPIO property");
    for (const row of data.gpio) {
      const count = data.gpio.filter((item) => item.node_id === row.node_id && item.property === row.property).length;
      option(select, gpioKey(row), `${row.path || row.name} · ${row.property}${count > 1 ? " · entry " + (row.index + 1) : ""}${row.editable === false ? " · unavailable" : ""}`);
    }
    selectedGpio = data.gpio.some((item) => gpioKey(item) === selectedGpio) ? selectedGpio : ""; select.value = selectedGpio;
    gpioForm.append(label("GPIO consumer", select));
    if (focus) select.focus?.({preventScroll: true});
    listen(select, "change", () => { selectedGpio = select.value; pinDestination = "gpio"; renderGpio(true); updateDisabled(); });
    const row = data.gpio.find((item) => gpioKey(item) === selectedGpio);
    if (!row) { note(gpioForm, data.gpio.length ? "Choose an observed GPIO property to edit its pin and symbolic flags." : "No supported GPIO property is present in this overlay. Add a GPIO device or its property before assigning a pin here."); return; }
    note(gpioForm, row.reason, "dte-notice"); diagnosticsFor(gpioForm, row.diagnostics);
    gpioPin = pinSelect(present(row.port) && present(row.pin) ? pinKey(row) : ""); gpioForm.append(label("GPIO pin", gpioPin));
    pinDetail(gpioForm, gpioPin, "gpio");
    const flags = make("fieldset", undefined, "dte-pin-flags"); flags.append(make("legend", "Symbolic GPIO flags"));
    const checks = [];
    for (const item of data.gpio_flags) {
      const name = typeof item === "string" ? item : item.name; const checkbox = make("input"); checkbox.type = "checkbox";
      checkbox.checked = array(row.flags).includes(name); checks.push({name, checkbox}); flags.append(label(typeof item === "string" ? item : item.label || name, checkbox));
    }
    gpioForm.append(flags); note(gpioForm, "Only the explicitly selected constants are written. No pull direction or polarity is inferred from the board connector.");
    const apply = button("Apply GPIO assignment", null, true); apply.type = "submit"; permitted(apply, row.editable !== false);
    const count = data.gpio.filter((item) => item.node_id === row.node_id && item.property === row.property).length;
    const chipSelect = row.property === "cs-gpios";
    const unassign = button(count > 1 ? "Unassign GPIO entry" : "Unassign GPIO property", guarded(() => {
      if (row.editable === false) throw new Error(row.reason || "This GPIO property requires source editing.");
      void submit({kind: "pin_gpio_unassign", node_id: row.node_id, property: row.property, index: row.index});
    })); permitted(unassign, !chipSelect && row.editable !== false && present(row.port) && present(row.pin));
    const actions = make("div", undefined, "dte-node-actions"); actions.append(apply, unassign); gpioForm.append(actions);
    listen(gpioForm, "submit", (event) => { event.preventDefault(); guarded(() => {
      if (row.editable === false) throw new Error(row.reason || "This GPIO property requires source editing.");
      void submit({kind: "pin_gpio", node_id: row.node_id, property: row.property, index: row.index,
        ...selectedPin(data.pins, gpioPin.value), flags: selectedFlags(checks.filter((item) => item.checkbox.checked).map((item) => item.name), data.gpio_flags)});
    })(); });
    note(gpioForm, chipSelect ? "Chip-select entries keep their bus-device indices. Edit shared cs-gpios source explicitly to remove an entry."
      : count > 1 ? "Unassign removes the selected entry and preserves the other entries in order. The remaining indices shift with the array."
      : "Unassign deletes this property explicitly so an inherited assignment cannot silently return. A required property may need replacement before Configure.");
  }
  function renderTable() {
    tableDetails.replaceChildren(); tableDetails.append(make("summary", `Pin table (${data.pins.length} observed pins)`));
    note(tableDetails, "Select an available pin to fill the most recently selected signal or GPIO form. Source changes only when you apply that form. Connector, package and electrical facts without evidence remain unknown.");
    const table = make("table", undefined, "dte-pin-table"); table.append(make("caption", "Selected target pin evidence and observed assignments"));
    const head = make("thead"); const header = make("tr");
    for (const title of ["SoC pin", "Board use", "Observed assignments"]) { const cell = make("th", title); cell.setAttribute("scope", "col"); header.append(cell); }
    head.append(header); const body = make("tbody");
    for (const pin of data.pins) {
      const row = make("tr"); const identity = make("th"); identity.setAttribute("scope", "row");
      const choose = button(pinName(pin), guarded(() => {
        selectedPin(data.pins, pinKey(pin));
        const field = pinDestination === "gpio" ? gpioPin : signalPin; const form = pinDestination === "gpio" ? gpioForm : signalForm;
        if (!field) throw new Error("Choose a controller signal or GPIO consumer before selecting a pin from the table.");
        field.value = pinKey(pin); handlers.get(field)?.change?.(); reveal?.(form);
      })); permitted(choose, pin.editable !== false); identity.append(choose);
      const facts = [pin.connector, pin.reserved ? "Reserved" : "", pin.reason].filter(Boolean).join(". ") || "Board routing unknown";
      row.append(identity, make("td", facts), make("td", array(pin.uses).join("; ") || "No observed assignment")); body.append(row);
    }
    table.append(head, body); tableDetails.append(table);
  }
  function updateDisabled() {
    const blocked = state.pinView.busy || !session.ready() || !state.pinView.data?.supported;
    for (const item of controls.querySelectorAll("input, select, button")) item.disabled = blocked || item.dataset.pinAllowed === "false";
    load.disabled = state.pinView.busy || !session.ready(); load.textContent = state.pinView.busy ? "Loading or updating pins…" : "Load pin assignments";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile(); reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile pin draft";
  }
  function render() {
    if (disposed) return; session.sync(); const view = state.pinView;
    status.textContent = view.message || (view.busy ? "Inspecting target pin evidence…" : "Load pin assignments to inspect this source revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error; controls.hidden = !view.presentation?.supported;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnostics.replaceChildren();
      if (data) {
        note(diagnostics, data.target ? `Selected target: ${data.target}` : "No exact target is established for this context.");
        diagnosticsFor(diagnostics, data.diagnostics);
        if (!data.supported) note(diagnostics, "Pin forms are unavailable for this context. Open a supported registered target with verified local evidence; source editing remains available.", "dte-notice");
        else { renderSignal(); renderGpio(); renderTable(); }
      }
    }
    renderFeedback(view);
    updateDisabled();
  }
  render();
  return {render, navigate() { generation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; generation++; lifetime.abort(); session.dispose(); }};
}
