import {announceResult} from "./view-feedback.js";
import {retainActionFocus} from "./view-focus.js";
import {localBindingValue} from "./bindings.js";
import {createCANSession, canRows, canValues} from "./can.js";

const array = (value) => Array.isArray(value) ? value : [];

export function mountCANPanel(root, {api, state, workspace, drafts, notify, onChange, onRemove}) {
  const document = root.ownerDocument; const lifetime = new AbortController(); const handlers = new WeakMap();
  let disposed = false; let generation = 0; let rendered; let data; let selectedKey = ""; let feedbackHost; let feedbackForm;
  let fieldControls = [];
  const make = (tag, text, className = "") => {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className; return node;
  };
  const listen = (node, event, callback) => { if (!handlers.has(node)) handlers.set(node, {}); handlers.get(node)[event] = callback; };
  for (const type of ["click", "change", "submit"]) root.addEventListener(type, (event) => {
    if (type === "submit" || type === "click" && event.target.closest("button")) locateFeedback(event.target);
    for (let node = event.target; node && node !== root; node = node.parentNode) {
      handlers.get(node)?.[type]?.(event); if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (title, action, primary = false) => {
    const node = make("button", title, primary ? "dte-primary" : ""); node.type = "button"; node.dataset.focusKey = "button:" + title;
    if (action) listen(node, "click", action); return node;
  };
  const label = (title, control) => { control.dataset.focusKey = "field:" + title; const node = make("label", title); node.append(control); return node; };
  const input = (value = "") => { const node = make("input"); node.type = "text"; node.value = String(value); node.spellcheck = false; node.autocomplete = "off"; return node; };
  const option = (select, value, title) => { const node = make("option", title); node.value = value; select.append(node); };
  const note = (parent, text, className = "dte-muted") => { if (text) parent.append(make("p", text, className)); };
  const permitted = (node, allowed) => { node.dataset.canAllowed = String(Boolean(allowed)); node.disabled = !allowed; };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load CAN controllers", () => run(() => session.load()));
  const cancel = button("Cancel CAN inspection", () => { generation++; session.cancel(); });
  heading.append(make("h3", "CAN controllers"), load, cancel);
  const status = make("p", "", "dte-muted"); status.setAttribute("role", "status");
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert");
  const recovery = make("div"); const reconcile = button("Reconcile CAN draft", () => run(() => session.reconcile()));
  recovery.append(reconcile); note(recovery, "Reconcile the canonical draft, then load CAN controllers again. The previous action is not repeated.");
  const diagnostics = make("div"); const controls = make("div", undefined, "dte-peripheral-controls dte-can-controls");
  const topology = make("ul", undefined, "dte-topology"); topology.setAttribute("aria-label", "Local CAN controllers and transceiver evidence");
  const detail = make("div", undefined, "dte-peripheral-detail"); detail.tabIndex = -1;
  controls.append(topology, detail); root.append(heading);
  note(root, "Edit nominal settings for local zephyr,can-loopback, microchip,mcp2515 and nxp,flexcan controllers. Clock timing feasibility, CAN FD configuration, pins, electrical suitability and hardware readiness remain unknown. Save and Configure are separate actions.");
  root.append(status, error, recovery, diagnostics, controls);
  const localFeedback = make("p"); localFeedback.setAttribute("role", "status");
  function locateFeedback(target) {
    feedbackHost = null; feedbackForm = null;
    for (let node = target; node && node !== root; node = node.parentNode) {
      if (node.tagName === "FORM") feedbackForm = node.children[0]?.textContent;
      if (node === detail) { feedbackHost = node; break; }
    }
  }
  function renderFeedback(view) {
    if (!feedbackHost) { localFeedback.hidden = true; localFeedback.remove?.(); return; }
    const destination = [...feedbackHost.querySelectorAll("form")].find((form) => form.children[0]?.textContent === feedbackForm) || feedbackHost;
    localFeedback.textContent = view.error || view.message || ""; localFeedback.hidden = !localFeedback.textContent;
    localFeedback.className = view.error ? "dte-error" : "dte-muted";
    if (localFeedback.parentNode !== destination) destination.append(localFeedback);
  }
  function announceFeedback() {
    if (disposed) return;
    announceResult(feedbackHost ? localFeedback : state.canView.error ? error : status, notify,
      {error: Boolean(state.canView.error)});
  }
  const session = createCANSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function currentAction() {
    const token = generation; const captured = workspace(); const context = state.data?.id; const revision = state.data?.revision;
    const draft = state.draftView?.entry?.server?.id; const localGeneration = state.draftView?.entry?.generation;
    return () => !disposed && token === generation && captured === workspace() && context === state.data?.id
      && revision === state.data?.revision && draft === state.draftView?.entry?.server?.id
      && localGeneration === state.draftView?.entry?.generation;
  }
  function run(operation) {
    const current = currentAction();
    return retainActionFocus(root, async () => {
      const result = await operation(); if (current()) announceFeedback(); return result;
    }, current, lifetime.signal);
  }
  function limits(parent, value) {
    note(parent, `Declared minimum bitrate: ${Number.isInteger(value?.min_bitrate) ? value.min_bitrate + " bits/s" : "unknown"}. Declared maximum bitrate: ${Number.isInteger(value?.max_bitrate) ? value.max_bitrate + " bits/s" : "unknown"}.`);
    note(parent, value?.reason, "dte-notice");
  }
  function transceiverEvidence(node) {
    note(detail, `${node.compatible || "Binding unknown"} · local status: ${node.status || "unknown"}.`, "dte-path");
    note(detail, node.reference ? `Local reference: ${node.reference}` : "No unique local reference is available.");
    limits(detail, node);
    note(detail, node.supported ? "Eligible as an existing local active transceiver." : node.reason || "This transceiver requires binding or source editing.", "dte-notice");
    note(detail, "Transceiver evidence is read-only here. Its limits and GPIO controls stay unchanged. Use binding or source editing to change them; all controllers sharing this transceiver can be affected.");
  }
  function controllerFields(form, controller) {
    const entries = [];
    for (const name of ["bitrate", "sample-point", "phys"]) {
      const field = controller.fields?.[name]; const allowed = controller.editable === true && field?.editable === true;
      const group = make("fieldset", undefined, "dte-peripheral-field"); group.append(make("legend", name));
      const include = make("input"); include.type = "checkbox"; include.checked = false;
      group.append(label("Set in this action", include)); include.dataset.focusKey = "include:" + name; permitted(include, allowed);
      const observed = localBindingValue(field);
      note(group, !field?.local ? "No local value; inherited and Kconfig values remain unknown."
        : typeof observed === "number" || typeof observed === "string" ? `Current local value: ${observed}`
          : "The local value is opaque; use binding or source editing.");
      if (!allowed) note(group, field?.reason || controller.reason || "This field requires binding or source editing.", "dte-notice");
      let control;
      if (name === "phys") {
        control = make("select"); option(control, "", "Choose an existing local active transceiver");
        const providers = array(field?.providers).filter((provider) => provider.supported === true);
        for (const provider of providers) option(control, provider.reference, `${provider.reference} · ${provider.path || provider.name}`);
        const selected = providers.find((provider) => provider.reference === observed || provider.path && observed === `&{${provider.path}}`
          || array(provider.labels).some((value) => observed === "&" + value));
        if (selected) control.value = selected.reference;
        group.append(label("Active transceiver", control));
        const evidence = make("div"); group.append(evidence);
        const update = () => {
          evidence.replaceChildren(); const provider = providers.find((item) => item.reference === control.value);
          if (provider) { note(evidence, `Selected transceiver local status: ${provider.status || "unknown"}.`); limits(evidence, provider); }
          else note(evidence, "Choose an eligible existing local transceiver. No transceiver is created or enabled.");
        };
        listen(control, "change", update); update();
        note(group, "Assignment checks explicit local nominal and preserved CAN FD rates against the selected limits. Missing rates remain unknown; this does not establish runtime timing or hardware readiness.");
      } else {
        control = input(typeof observed === "number" ? observed : ""); control.inputMode = "numeric";
        group.append(label(name === "bitrate" ? "Nominal bitrate (bits/s)" : "Sample point (per mille)", control));
        note(group, name === "bitrate" ? "Enter 1–1000000 bits/s. The clock and driver may impose narrower timing limits."
          : "Enter 0–999 per mille; 875 means 87.5%. Explicit 0 requests automatic sample-point selection.");
        if (Array.isArray(field?.enum)) note(group, `Allowed binding values: ${field.enum.join(", ")}.`);
        if (field && Object.hasOwn(field, "const")) note(group, `Binding constant: ${field.const}.`);
      }
      control.dataset.focusKey = "value:" + name;
      fieldControls.push({include, control, allowed}); entries.push({name, include, control});
      listen(include, "change", updateDisabled); form.append(group);
    }
    return () => canValues(controller, Object.fromEntries(entries.filter((item) => item.include.checked).map((item) => [item.name, item.control.value])));
  }
  function controllerDetail(controller) {
    note(detail, `${controller.compatible} · local status: ${controller.status || "unknown"}.`, "dte-path");
    note(detail, controller.reason, "dte-notice");
    note(detail, `Effective transceiver limit source: ${controller.limits?.source === "phys" ? "active phys reference" : controller.limits?.source === "passive" ? "passive can-transceiver child" : "unknown"}.`);
    limits(detail, controller.limits);
    if (controller.passive_transceiver) {
      note(detail, `Preserved passive transceiver: ${controller.passive_transceiver.path}.`); limits(detail, controller.passive_transceiver);
    }
    for (const item of array(controller.notes)) note(detail, item.message || item, "dte-notice");
    const form = make("form", undefined, "dte-peripheral-form"); form.append(make("h4", "Edit CAN controller settings")); detail.append(form);
    const read = controllerFields(form, controller);
    const apply = button("Apply CAN settings to draft", null, true); apply.type = "submit"; permitted(apply, controller.editable === true); form.append(apply);
    note(form, "Only fields selected for this action change. Other properties, CAN FD settings, passive children and shared transceiver declarations are preserved. Use binding or source editing to delete properties or migrate deprecated bus-speed.");
    listen(form, "submit", (event) => {
      event.preventDefault();
      try {
        const values = read();
        void run(() => session.command({kind: "can_controller_set", node_id: controller.node_id, values}));
      }
      catch (error) { session.fail(error.message); announceFeedback(); }
    });
    if (!onRemove) return;
    let reviewing = false;
    const remove = button("Review whole controller removal", async () => {
      if (reviewing) return;
      const current = currentAction(); reviewing = true; remove.textContent = "Preparing removal review…";
      try { await onRemove(controller.node_id, remove); }
      catch (error) { if (current()) { session.fail(error.message || String(error)); announceFeedback(); } }
      finally { reviewing = false; remove.textContent = "Review whole controller removal"; }
    });
    detail.append(remove); note(detail, "This review covers the whole controller node and its children. Shared active transceivers are retained.");
  }
  function renderSelection() {
    for (const item of topology.querySelectorAll("button")) item.setAttribute("aria-pressed", String(item.dataset.canKey === selectedKey));
    detail.replaceChildren(); fieldControls = []; const selected = canRows(data).find((row) => row.key === selectedKey);
    if (!selected) note(detail, "Choose a CAN controller to edit selected settings, or a transceiver to inspect its local evidence.");
    else {
      detail.append(make("h4", selected.node.path || selected.node.name));
      if (selected.kind === "controller") controllerDetail(selected.node); else transceiverEvidence(selected.node);
    }
    updateDisabled();
  }
  function renderTopology() {
    topology.replaceChildren();
    for (const row of canRows(data)) {
      const item = make("li"); const select = button(`${row.kind === "controller" ? "CAN controller" : "CAN transceiver"} · ${row.node.path || row.node.name}`, () => {
        if (selectedKey === row.key) return;
        selectedKey = row.key; renderSelection();
        announceResult(detail, notify, {message: `Details for ${row.node.path || row.node.name} are ready below.`});
      }); select.dataset.canKey = row.key; item.append(select); topology.append(item);
    }
    if (!topology.children.length) { const item = make("li"); note(item, "No reviewed local CAN controllers or transceivers were identified. Use binding or source editing for other declarations."); topology.append(item); }
    renderSelection();
  }
  function updateDisabled() {
    const blocked = state.canView.busy || !session.ready() || !state.canView.data;
    for (const node of controls.querySelectorAll("input, select, textarea, button")) node.disabled = blocked || node.dataset.canAllowed === "false";
    for (const {include, control, allowed} of fieldControls) control.disabled = blocked || !allowed || !include.checked;
    load.disabled = state.canView.busy || !session.ready(); load.textContent = state.canView.busy ? "Loading or updating CAN…" : "Load CAN controllers";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile(); reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile CAN draft";
  }
  function render() {
    if (disposed) return;
    session.sync(); const view = state.canView;
    status.textContent = view.message || (view.busy ? "Inspecting current CAN controllers…" : "Load CAN controllers to inspect this source revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error; controls.hidden = !view.presentation;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnostics.replaceChildren();
      if (data) { for (const item of array(data.diagnostics)) note(diagnostics, item.message || item, "dte-notice"); renderTopology(); }
    }
    renderFeedback(view); updateDisabled();
  }
  render();
  return {render, navigate() { generation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; generation++; lifetime.abort(); session.dispose(); }};
}
