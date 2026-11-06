import {retainActionFocus} from "./view-focus.js";
import {bindingTypes, localBindingValue} from "./bindings.js";
import {createPeripheralSession, peripheralAddress, peripheralFieldValue, peripheralLabel,
  peripheralName, topologyRows} from "./peripherals.js";

const array = (value) => Array.isArray(value) ? value : [];
const shown = (value) => typeof value === "string" ? value : JSON.stringify(value);
const exists = (value) => value !== undefined && value !== null;
const gpio = (parent) => ["led", "button"].includes(parent.kind);

export function mountPeripheralPanel(root, {api, state, workspace, drafts, reveal, onChange, onRemove}) {
  const document = root.ownerDocument; const lifetime = new AbortController(); const handlers = new WeakMap();
  let disposed = false; let generation = 0; let rendered; let data; let selectedKey = "";
  const make = (tag, text, className = "") => {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className; return node;
  };
  const listen = (node, event, callback) => {
    if (!handlers.has(node)) handlers.set(node, {}); handlers.get(node)[event] = callback;
  };
  for (const type of ["click", "change", "submit"]) root.addEventListener(type, (event) => {
    if (type === "submit" || type === "click" && event.target.closest("button")) feedbackHost = locateFeedback(event.target);
    for (let node = event.target; node && node !== root; node = node.parentNode) {
      handlers.get(node)?.[type]?.(event); if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (title, action, primary = false) => {
    const node = make("button", title, primary ? "dte-primary" : ""); node.type = "button";
    node.dataset.focusKey = "button:" + title;
    if (action) listen(node, "click", action); return node;
  };
  const label = (title, control) => { control.dataset.focusKey = "field:" + title; const node = make("label", title); node.append(control); return node; };
  const input = (value = "") => { const node = make("input"); node.type = "text"; node.value = String(value); node.spellcheck = false; node.autocomplete = "off"; return node; };
  const option = (select, value, title) => { const node = make("option", title); node.value = value; select.append(node); return node; };
  const note = (parent, text, className = "dte-muted") => { if (text) parent.append(make("p", text, className)); };
  const permitted = (node, allowed) => { node.dataset.peripheralAllowed = String(Boolean(allowed)); node.disabled = !allowed; };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load peripherals", () => run(() => session.load()));
  const cancel = button("Cancel peripheral inspection", () => { generation++; session.cancel(); });
  heading.append(make("h3", "Peripherals and bus devices"), load, cancel);
  const status = make("p", "", "dte-muted"); status.setAttribute("role", "status"); status.tabIndex = -1;
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert"); error.tabIndex = -1;
  const recovery = make("div"); const reconcile = button("Reconcile peripheral draft", () => run(() => session.reconcile()));
  recovery.append(reconcile); note(recovery, "Reconcile the canonical draft, then load peripherals again. The previous action is not repeated.");
  const diagnostics = make("div"); const controls = make("div", undefined, "dte-peripheral-controls");
  const topology = make("ul", undefined, "dte-topology"); topology.setAttribute("aria-label", "Local peripheral topology");
  const detail = make("div", undefined, "dte-peripheral-detail"); detail.tabIndex = -1;
  const groups = make("div");
  controls.append(topology, detail, groups);
  root.append(heading);
  note(root, "Configure supported controllers and devices declared in this overlay. Inherited board topology, pin assignment and hardware modules remain outside these forms. Changes update the working draft; Save and Configure remain separate.");
  root.append(status, error, recovery, diagnostics, controls);
  const feedbackSections = new Set([detail, groups]);
  let feedbackHost;
  let feedbackForm;
  const localFeedback = document.createElement("p"); localFeedback.setAttribute("role", "status");
  function locateFeedback(target) {
    feedbackForm = null;
    for (let item = target; item && item !== root; item = item.parentNode) {
      if (item.tagName === "FORM") feedbackForm = item.children[0]?.textContent;
      if (feedbackSections.has(item)) return item;
    }
    return null;
  }
  function renderFeedback(view) {
    if (!feedbackHost) { localFeedback.hidden = true; localFeedback.remove?.(); return; }
    const destination = [...feedbackHost.querySelectorAll("form")].find((form) => form.children[0]?.textContent === feedbackForm) || feedbackHost;
    localFeedback.className = view.error ? "dte-error" : "dte-muted";
    localFeedback.textContent = view.error || view.message || "";
    localFeedback.hidden = !localFeedback.textContent;
    if (localFeedback.parentNode !== destination) destination.append(localFeedback);
  }
  const session = createPeripheralSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function feedback(message) { session.fail(message); }
  function run(operation) {
    const token = generation; const captured = workspace(); const context = state.data?.id;
    const draft = state.draftView?.entry?.server?.id;
    return retainActionFocus(root, operation, () => !disposed && token === generation
      && captured === workspace() && context === state.data?.id && draft === state.draftView?.entry?.server?.id, lifetime.signal);
  }
  function submit(command, key = selectedKey) { selectedKey = key; return run(() => session.command(command)); }
  function form(parent, title, onSubmit) {
    const node = make("form", undefined, "dte-peripheral-form"); node.append(make("h4", title)); parent.append(node);
    listen(node, "submit", (event) => { event.preventDefault(); try { onSubmit(); } catch (error) { feedback(error.message); } });
    return node;
  }
  function applyButton(parent, title, allowed = true) {
    const node = button(title, null, true); node.type = "submit"; permitted(node, allowed); parent.append(node); return node;
  }
  function targetsSelect(targets, value) {
    const node = make("select"); option(node, "", "Choose a local target");
    for (const target of targets) option(node, target.reference, `${target.reference}${target.path ? " · " + target.path : ""}`);
    const current = targets.find((target) => target.reference === value || target.path && [target.path, `&{${target.path}}`].includes(value)
      || array(target.labels).some((name) => `&${name}` === value));
    if (current) node.value = current.reference; return node;
  }
  function addressControl(parent, value = "") {
    if (parent.kind !== "spi" || !Array.isArray(parent.chip_selects)) return input(value);
    const node = make("select"); option(node, "", "Choose a local chip-select index");
    for (const index of parent.chip_selects) option(node, String(index), String(index));
    if (parent.chip_selects.includes(value)) node.value = String(value); return node;
  }
  function valueControl(parent, property) {
    const current = localBindingValue(property);
    if (Object.hasOwn(property, "const")) { note(parent, `Constant: ${shown(property.const)}`, "dte-path"); return () => property.const; }
    if (property.type === "phandle-array") {
      const providers = array(property.providers || data.gpio_providers); const rows = []; const wrapper = make("div", undefined, "dte-binding-reference-rows");
      function add(value = {}, explicit = false) {
        if (rows.length >= 64) { feedback("A property supports up to 64 provider entries."); return; }
        const group = make("div", undefined, "dte-binding-reference-row"); const target = targetsSelect(providers, value.reference);
        const cells = make("div", undefined, "dte-binding-cells"); const row = {group, target, inputs: []}; rows.push(row);
        const populate = (values = []) => {
          cells.replaceChildren(); row.inputs = [];
          const provider = providers.find((item) => item.reference === target.value);
          if (provider && !array(provider.cells).length) note(cells, "This provider has no specifier cells.");
          for (const [index, name] of array(provider?.cells).entries()) {
            const field = input(exists(values[index]) ? values[index] : ""); row.inputs.push(field); cells.append(label(name, field));
          }
          updateDisabled();
        };
        group.append(label("Provider", target), cells, button("Remove provider entry", () => { rows.splice(rows.indexOf(row), 1); group.remove(); }));
        listen(target, "change", () => populate()); wrapper.append(group); populate(value.cells);
        if (explicit) reveal?.(group);
      }
      parent.append(wrapper, button("Add provider entry", () => { add({}, true); updateDisabled(); }));
      for (const value of array(current)) add(value); if (!rows.length) add();
      if (!providers.length) note(parent, "No unique local provider has an established specifier layout. Use source editing.", "dte-notice");
      return () => rows.map((row) => ({reference: row.target.value, cells: row.inputs.map((node) => node.value)}));
    }
    if (property.type === "phandle" || property.type === "path") {
      const node = targetsSelect(array(data.targets), current); parent.append(label("Referenced node", node)); return () => node.value;
    }
    if (property.type === "phandles") {
      const node = make("select"); node.multiple = true; node.size = 4;
      for (const target of array(data.targets)) { const item = option(node, target.reference, target.reference); item.selected = array(current).includes(target.reference); }
      parent.append(label("Referenced nodes", node)); return () => Array.from(node.selectedOptions).map((item) => item.value);
    }
    if (Array.isArray(property.enum) && !["array", "string-array"].includes(property.type)) {
      const node = make("select"); option(node, "", "Choose an allowed value");
      property.enum.forEach((value, index) => option(node, String(index), shown(value)));
      const index = property.enum.indexOf(current); if (index >= 0) node.value = String(index);
      parent.append(label("Value", node));
      return () => { if (node.value === "") throw new Error("Choose an allowed value."); return property.enum[Number(node.value)]; };
    }
    if (property.type === "boolean") {
      const node = make("select"); option(node, "", "Choose presence"); option(node, "true", "Present");
      if (typeof current === "boolean") node.value = String(current); parent.append(label("Presence", node));
      note(parent, "Use the binding editor for explicit deletion of a boolean property.");
      return () => node.value === "" ? undefined : node.value === "true";
    }
    const node = ["array", "uint8-array", "string-array"].includes(property.type) ? make("textarea") : input();
    node.spellcheck = false; node.rows = 3;
    node.value = !exists(current) ? "" : Array.isArray(current) ? current.join(property.type === "string-array" ? "\n" : " ") : String(current);
    parent.append(label("Value", node));
    if (property.enum) note(parent, `Each entry must be one of: ${property.enum.map(shown).join(", ")}`);
    note(parent, property.type === "string-array" ? "One string per line. Order and repeated entries are retained."
      : property.type === "uint8-array" ? "Bytes from 0 to 255, separated by spaces."
        : ["int", "array"].includes(property.type) ? "Unsigned integers; decimal and 0x hexadecimal are accepted." : "Text is escaped as source data.");
    return () => node.value;
  }
  function fields(parent, properties, creating = false) {
    const entries = [];
    for (const property of array(properties)) {
      const group = make("fieldset", undefined, "dte-peripheral-field"); group.append(make("legend", property.name));
      const required = creating && property.required; const include = make("input"); include.type = "checkbox"; include.checked = Boolean(required);
      const supported = property.editable !== false && bindingTypes.has(property.type);
      permitted(include, supported && !required);
      group.append(label(required ? "Required for this new device" : "Set in this action", include));
      note(group, property.description);
      if (Object.hasOwn(property, "default")) note(group, `Binding default (not applied automatically): ${shown(property.default)}`);
      if (!creating) note(group, property.local ? `Current local value: ${shown(localBindingValue(property))}` : "No local value; inherited value unknown.");
      const body = make("div"); body.dataset.peripheralField = String(include.checked && supported); group.append(body);
      let read;
      if (supported) read = valueControl(body, property);
      else note(body, property.reason || "This field requires source editing.", "dte-notice");
      listen(include, "change", () => { body.dataset.peripheralField = String(include.checked && supported); updateDisabled(); });
      parent.append(group); entries.push({property, include, read});
    }
    return () => Object.fromEntries(entries.filter((entry) => entry.include.checked).map(({property, read}) => {
      if (!read) throw new Error(`${property.name}: this required field needs source editing.`);
      try { return [property.name, Object.hasOwn(property, "const") ? read() : peripheralFieldValue(property, read())]; }
      catch (error) { throw new Error(`${property.name}: ${error.message}`); }
    }));
  }
  function diagnosticsFor(parent, items) { for (const item of array(items)) note(parent, item.message || item, "dte-notice"); }
  function newChild(parent) {
    const isGpio = gpio(parent); const addressable = ["i2c", "spi"].includes(parent.kind); let collect = () => ({}); let candidate;
    const node = form(detail, isGpio ? `Add GPIO ${parent.kind}` : `Add ${parent.kind.toUpperCase()} device`, () => {
      const base = peripheralName(name.value); const common = {parent_id: parent.node_id, name: base,
        label: peripheralLabel(nodeLabel.value), values: collect()};
      if (isGpio) void submit({kind: "gpio_add", ...common}, `${parent.path}/${base}`);
      else {
        if (!candidate?.addable) throw new Error(candidate?.reason || "Choose an available child-device binding.");
        const value = addressable ? peripheralAddress(address.value, parent) : null;
        void submit({kind: "peripheral_add", ...common, compatible: candidate.compatible, address: value}, `${parent.path}/${base}${addressable ? "@" + value.toString(16) : ""}`);
      }
    });
    const name = input(isGpio ? parent.kind : "device"); const nodeLabel = input(); const address = addressControl(parent);
    const candidateSelect = make("select"); const extra = make("div");
    if (!isGpio) {
      option(candidateSelect, "", "Choose a child-device binding");
      for (const item of array(parent.candidates)) option(candidateSelect, item.compatible, `${item.compatible}${item.addable ? "" : " · unavailable"}`);
      node.append(label("Compatible binding", candidateSelect));
    }
    node.append(label("Node name", name), label("Node label (optional)", nodeLabel));
    if (addressable) node.append(label(parent.kind === "i2c" ? "I2C address" : "SPI chip select", address));
    node.append(extra); const apply = applyButton(node, isGpio ? `Add ${parent.kind} to draft` : "Add bus device to draft", false);
    if (isGpio) { collect = fields(extra, parent.add_fields, true); permitted(apply, parent.addable); }
    else listen(candidateSelect, "change", () => {
      candidate = array(parent.candidates).find((item) => item.compatible === candidateSelect.value);
      extra.replaceChildren(); note(extra, candidate?.description); note(extra, candidate?.reason, "dte-notice");
      collect = fields(extra, candidate?.fields, true); permitted(apply, parent.addable && candidate?.addable); updateDisabled();
    });
    note(node, parent.add_reason, "dte-notice");
    note(node, "Adding a device changes only this overlay draft. Controllers, GPIO providers and pin configuration are preserved.");
  }
  function editController(parent) {
    const values = array(parent.fields).map((field) => field.name === "status" && field.type === "string"
      ? {...field, enum: ["okay", "disabled"].filter((value) => !field.enum || field.enum.includes(value))} : field);
    if (!gpio(parent)) {
      const actions = make("div", undefined, "dte-node-actions");
      for (const [title, status] of [["Enable controller", "okay"], ["Disable controller", "disabled"]]) {
        const node = button(title, () => submit({kind: "peripheral_controller", node_id: parent.node_id, values: {status}}));
        permitted(node, parent.editable && values.some((field) => field.name === "status" && field.type === "string" && field.editable !== false
          && (!Object.hasOwn(field, "const") || field.const === status) && field.enum.includes(status))); actions.append(node);
      }
      detail.append(actions);
      let collect;
      const node = form(detail, "Controller properties", () => {
        const changed = collect(); if (!Object.keys(changed).length) throw new Error("Select at least one controller field to change.");
        void submit({kind: "peripheral_controller", node_id: parent.node_id, values: changed});
      });
      collect = fields(node, values); applyButton(node, "Apply controller properties", parent.editable && values.length > 0);
      note(node, "Only selected fields are changed. No pinctrl, routing or inherited controller configuration is inferred.");
    }
    newChild(parent);
  }
  function editChild(parent, child) {
    const isGpio = gpio(parent); const addressable = ["i2c", "spi"].includes(parent.kind); let collect;
    const node = form(detail, isGpio ? `Edit GPIO ${parent.kind}` : `Edit ${parent.kind.toUpperCase()} device`, () => {
      const values = collect();
      if (isGpio) {
        if (!Object.keys(values).length) throw new Error("Select at least one device field to change.");
        void submit({kind: "gpio_update", node_id: child.node_id, values});
      } else {
        const value = addressable ? peripheralAddress(child.address_editable === false ? child.address : address.value, parent, child.node_id) : null;
        const base = child.name.split("@")[0];
        void submit({kind: "peripheral_update", node_id: child.node_id, address: value, values}, addressable ? `${parent.path}/${base}@${value.toString(16)}` : selectedKey);
      }
    });
    const address = addressControl(parent, exists(child.address) ? child.address : "");
    address.readOnly = child.address_editable === false;
    if (address.tagName === "SELECT" && child.address_editable === false) permitted(address, false);
    if (addressable) { node.append(label(parent.kind === "i2c" ? "I2C address" : "SPI chip select", address)); note(node, child.address_reason, "dte-notice"); }
    collect = fields(node, child.fields); applyButton(node, "Apply device changes", child.editable);
    note(node, child.reason, "dte-notice");
    if (addressable) note(node, "Address changes update this existing node's unit address and reg together. Collision checks run before the draft changes.");
    let reviewingRemoval = false;
    const remove = button(onRemove ? "Review device removal" : "Remove device from draft", async () => {
      if (onRemove) {
        if (reviewingRemoval) return;
        reviewingRemoval = true; const token = generation; remove.textContent = "Preparing removal review…";
        try { await onRemove(child.node_id, remove); }
        catch (error) { if (!disposed && token === generation) feedback(error.message || String(error)); }
        finally { reviewingRemoval = false; remove.textContent = "Review device removal"; }
        return;
      }
      return submit({kind: "peripheral_remove", node_id: child.node_id}, parent.path || parent.name);
    });
    permitted(remove, onRemove ? Boolean(child.node_id) : child.removable); detail.append(remove); note(detail, child.remove_reason, "dte-notice");
    note(detail, "Removal keeps the parent controller/group and shared providers. Known consumers or unresolved reference safety can prevent removal. Undo restores a removed local device.");
  }
  function renderDetail() {
    detail.replaceChildren(); const selected = topologyRows(data).find((row) => row.key === selectedKey);
    if (!selected) { note(detail, "Choose a local controller, GPIO group or child device to inspect its supported actions."); return; }
    const {parent, node} = selected;
    detail.append(make("h4", node.path || node.name));
    note(detail, parent ? `Parent: ${parent.path || parent.name} · ${node.compatible || "binding unknown"}`
      : `${gpio(node) ? "GPIO " + node.kind + " group" : node.kind.toUpperCase() + " controller"} · local status: ${node.status || "not specified"}`, "dte-path");
    note(detail, node.reason, "dte-notice"); diagnosticsFor(detail, node.diagnostics);
    if (parent) editChild(parent, node); else editController(node);
  }
  function renderTopology() {
    topology.replaceChildren();
    for (const parent of [...array(data.controllers), ...array(data.gpio_groups)]) {
      const item = make("li"); const key = parent.path || parent.name;
      const select = button(`${parent.kind.toUpperCase()} · ${parent.path || parent.name}`, () => { selectedKey = key; renderSelection(); reveal?.(detail); });
      select.dataset.topologyKey = key; item.append(select);
      if (array(parent.diagnostics).length) note(item, `${parent.diagnostics.length} controller or address diagnostic(s).`, "dte-notice");
      const children = make("ul");
      for (const child of array(parent.children)) {
        const childItem = make("li"); const childKey = child.path || `${key}/${child.name}`;
        const childSelect = button(`${child.name}${exists(child.address) ? " · " + (parent.kind === "i2c" ? "address 0x" + child.address.toString(16) : "chip select " + child.address) : ""}`, () => {
          selectedKey = childKey; renderSelection(); reveal?.(detail);
        });
        childSelect.dataset.topologyKey = childKey; childItem.append(childSelect);
        if (array(child.diagnostics).length) note(childItem, `${child.diagnostics.length} device diagnostic(s).`, "dte-notice");
        children.append(childItem);
      }
      if (children.children.length) item.append(children); else note(item, "No local child devices.");
      topology.append(item);
    }
    if (!topology.children.length) {
      const empty = make("li"); note(empty, "No supported local controllers or GPIO groups were identified. Inherited hardware remains unknown; use source editing to introduce supported declarations."); topology.append(empty);
    }
    renderSelection();
  }
  function renderSelection() {
    for (const item of topology.querySelectorAll("button")) item.setAttribute("aria-pressed", String(item.dataset.topologyKey === selectedKey));
    renderDetail(); updateDisabled();
  }
  function renderGroups() {
    groups.replaceChildren(); if (!array(data.group_options).length) return;
    const node = form(groups, "Add a GPIO device group", () => {
      const selected = data.group_options.find((item) => item.kind === kind.value);
      if (!selected?.available) throw new Error(selected?.reason || "Choose an available GPIO group type.");
      const nameValue = peripheralName(name.value);
      void submit({kind: "gpio_group_add", group_kind: selected.kind, name: nameValue}, "/" + nameValue);
    });
    const kind = make("select"); option(kind, "", "Choose a GPIO group type");
    for (const item of data.group_options) option(kind, item.kind, `${item.kind === "led" ? "GPIO LEDs" : "GPIO buttons"}${item.available ? "" : " · unavailable"}`);
    const name = input(); const reason = make("p", "", "dte-notice"); node.append(label("Group type", kind), label("Group node name", name), reason);
    const apply = applyButton(node, "Add GPIO group to draft", false);
    listen(kind, "change", () => {
      const selected = data.group_options.find((item) => item.kind === kind.value);
      name.value = selected?.name || ""; reason.textContent = selected?.reason || "A group is added under the unique existing local root. No GPIO pin assignment is created.";
      permitted(apply, selected?.available); updateDisabled();
    });
  }
  function updateDisabled() {
    const blocked = state.peripheralView.busy || !session.ready() || !state.peripheralView.data;
    for (const item of controls.querySelectorAll("input, select, textarea, button")) {
      item.disabled = blocked || item.dataset.peripheralAllowed === "false" || item.closest("[data-peripheral-field]")?.dataset.peripheralField === "false";
    }
    load.disabled = state.peripheralView.busy || !session.ready();
    load.textContent = state.peripheralView.busy ? "Loading or updating peripherals…" : "Load peripherals";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile(); reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile peripheral draft";
  }
  function render() {
    if (disposed) return;
    session.sync(); const view = state.peripheralView;
    status.textContent = view.message || (view.busy ? "Inspecting current local peripherals…" : "Load peripherals to inspect this source revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error; controls.hidden = !view.presentation;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnostics.replaceChildren();
      if (data) { diagnosticsFor(diagnostics, data.diagnostics); renderTopology(); renderGroups(); }
    }
    renderFeedback(view);
    updateDisabled();
  }
  render();
  return {render, navigate() { generation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; generation++; lifetime.abort(); session.dispose(); }};
}
