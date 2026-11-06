import {announceResult} from "./view-feedback.js";
import {retainActionFocus} from "./view-focus.js";
import {bindingEnumValue, bindingTypes, bindingValue, createBindingSession, localBindingValue} from "./bindings.js";
import {nodeSelection, restoreNodeSelection} from "./drafts.js";

const array = (value) => Array.isArray(value) ? value : [];
const present = (value) => value !== undefined && value !== null;
const display = (value) => typeof value === "string" ? value : JSON.stringify(value);

export function mountBindingPanel(root, {api, state, workspace, drafts, notify, onChange}) {
  const document = root.ownerDocument;
  const lifetime = new AbortController();
  let disposed = false;
  let generation = 0;
  let rendered;
  let selectedIndex = -1;
  let selectedName = "";
  let nodeHint;
  let data;
  let valueReader;
  function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }
  const option = (select, value, text) => { const item = element("option", "", text); item.value = value; select.append(item); };
  const handlers = new WeakMap();
  const listen = (item, event, callback) => {
    if (!handlers.has(item)) handlers.set(item, {});
    handlers.get(item)[event] = callback;
  };
  for (const eventName of ["click", "change", "submit"]) root.addEventListener(eventName, (event) => {
    if (eventName === "submit" || eventName === "click" && event.target.closest("button")) feedbackHost = locateFeedback(event.target);
    for (let item = event.target; item && item !== root; item = item.parentNode) {
      handlers.get(item)?.[eventName]?.(event);
      if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (text, action, primary = false) => {
    const item = element("button", primary ? "dte-primary" : "", text); item.type = "button";
    item.dataset.focusKey = "button:" + text;
    if (action) listen(item, "click", action);
    return item;
  };
  const label = (title, control) => { control.dataset.focusKey = "field:" + title; const item = element("label", "", title); item.append(control); return item; };
  const input = (type = "text") => {
    const item = element("input"); item.type = type; item.autocomplete = "off"; item.spellcheck = false; return item;
  };
  const heading = element("div", "dte-section-heading");
  heading.append(element("h3", "", "Binding properties and references"));
  const load = button("Load binding properties", () => run(() => session.load()));
  const cancel = button("Cancel inspection", () => { generation++; session.cancel(); }); cancel.hidden = true;
  heading.append(load, cancel);
  const intro = element("p", "dte-muted", "Forms use local schemas and declarations in this overlay. Inherited board nodes, external references and macros remain source-only when their meaning cannot be established. Loading does not add defaults or required properties.");
  const status = element("p", "dte-muted"); status.setAttribute("role", "status"); status.tabIndex = -1;
  const error = element("p", "dte-error"); error.setAttribute("role", "alert"); error.tabIndex = -1; error.hidden = true;
  const recovery = element("div"); recovery.hidden = true;
  const reconcile = button("Reconcile binding draft", () => run(() => session.reconcile()));
  recovery.append(reconcile, element("p", "dte-muted", "Check the canonical draft, then reload binding properties. This does not repeat the previous action."));
  const diagnostics = element("div");
  const controls = element("div", "dte-binding-controls"); controls.hidden = true;
  const nodeSelect = element("select");
  const bindingInfo = element("div");
  const propertySelect = element("select");
  const propertyPanel = element("div", "dte-binding-property");
  const labelPanel = element("div", "dte-binding-label");
  const referencePanel = element("div", "dte-binding-references");
  controls.append(label("Binding node", nodeSelect), bindingInfo, label("Binding property", propertySelect), propertyPanel, labelPanel, referencePanel);
  root.append(heading, intro, status, error, recovery, diagnostics, controls);
  const feedbackSections = new Set([propertyPanel, labelPanel, referencePanel]);
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
  const session = createBindingSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function selectedNode() { return array(data?.nodes)[selectedIndex]; }
  function selectedProperty() { return array(selectedNode()?.properties).find((item) => item.name === selectedName); }
  function feedback(message) { session.fail(message); announceFeedback(); }
  function announceFeedback() {
    if (disposed) return;
    announceResult(feedbackHost ? localFeedback : state.bindingView.error ? error : status, notify,
      {error: Boolean(state.bindingView.error)});
  }
  function run(operation) {
    const token = generation; const captured = workspace(); const context = state.data?.id;
    const draft = state.draftView?.entry?.server?.id; const localGeneration = state.draftView?.entry?.generation;
    const current = () => !disposed && token === generation && captured === workspace()
      && context === state.data?.id && draft === state.draftView?.entry?.server?.id
      && localGeneration === state.draftView?.entry?.generation;
    return retainActionFocus(root, async () => {
      const result = await operation();
      if (current()) announceFeedback();
      return result;
    }, current, lifetime.signal);
  }
  function submit(command) { return run(() => session.command(command)); }
  function message(parent, text, className = "dte-muted") { if (text) parent.append(element("p", className, text)); }
  function selectTarget(targets, value = "", title = "Choose a local target") {
    const select = element("select"); option(select, "", title);
    for (const target of targets) option(select, target.reference, `${target.reference}${target.path ? " · " + target.path : ""}`);
    const current = targets.find((target) => target.reference === value || target.path && [target.path, `&{${target.path}}`].includes(value)
      || array(target.labels).some((label) => `&${label}` === value));
    if (current) select.value = current.reference;
    return select;
  }
  function referenceValues(property, current) {
    const wrapper = element("div", "dte-binding-reference-rows");
    const rows = [];
    const targets = property.type === "phandle-array" ? array(property.providers) : array(data?.targets);
    function add(initial = {}, explicit = false) {
      if (rows.length >= 64) { feedback("A property supports up to 64 reference entries."); return; }
      const group = element("div", "dte-binding-reference-row");
      const reference = typeof initial === "string" ? initial : initial.reference;
      const select = selectTarget(targets, reference, property.type === "phandle-array" ? "Choose a provider" : "Choose a local target");
      const cells = element("div", "dte-binding-cells");
      const row = {group, select, cells, inputs: []}; rows.push(row);
      group.append(label(property.type === "phandle-array" ? "Provider" : "Referenced node", select), cells);
      const remove = button("Remove entry", () => { rows.splice(rows.indexOf(row), 1); group.remove(); });
      group.append(remove);
      function populate(values = []) {
        cells.replaceChildren(); row.inputs = [];
        const provider = targets.find((item) => item.reference === select.value);
        if (property.type === "phandle-array" && provider) {
          const names = array(provider.cells);
          if (!names.length) message(cells, "This provider has no specifier cells.");
          names.forEach((name, index) => {
            const field = input(); field.inputMode = "numeric"; field.value = present(values[index]) ? String(values[index]) : "";
            row.inputs.push(field); cells.append(label(String(name), field));
          });
        }
      }
      populate(initial.cells || []);
      listen(select, "change", () => populate());
      wrapper.append(group);
      if (explicit) announceResult(group, notify, {message: "Reference entry added to the property form."});
    }
    for (const entry of array(current)) add(entry);
    if (!rows.length) add();
    const addButton = button("Add reference entry", () => add({}, true));
    const container = element("div"); container.append(wrapper, addButton);
    if (!targets.length) message(container, property.type === "phandle-array"
      ? "No local provider has an established binding and specifier-cell layout for this property. Edit unresolved references in source."
      : "No unique local reference targets are available. Add a label to an existing declaration or edit the source.");
    propertyPanel.append(container);
    return () => bindingValue(property.type, rows.map((row) => property.type === "phandle-array"
      ? {reference: row.select.value, cells: row.inputs.map((field) => field.value)} : row.select.value));
  }
  function renderValue(property) {
    const current = localBindingValue(property);
    if (Object.hasOwn(property, "const")) {
      const constant = element("p", "dte-path", display(property.const));
      propertyPanel.append(label("Required constant", constant));
      return () => property.const;
    }
    if (Array.isArray(property.enum)) {
      const enumSelect = (current) => {
        const select = element("select"); option(select, "", "Choose a value");
        property.enum.forEach((value, index) => option(select, String(index), display(value)));
        const selected = property.enum.findIndex((value) => value === current);
        if (selected >= 0) select.value = String(selected);
        return select;
      };
      if (["array", "string-array"].includes(property.type)) {
        const wrapper = element("div", "dte-binding-reference-rows"); const rows = [];
        const add = (value, explicit = false) => {
          if (rows.length >= 64) { feedback("An array supports up to 64 entries."); return; }
          const group = element("div", "dte-binding-reference-row"); const select = enumSelect(value);
          const row = {group, select}; rows.push(row);
          group.append(label("Array entry", select), button("Remove entry", () => { rows.splice(rows.indexOf(row), 1); group.remove(); }));
          wrapper.append(group); if (explicit) announceResult(group, notify, {message: "Array entry added to the property form."});
        };
        for (const value of array(current)) add(value);
        if (!rows.length) add();
        propertyPanel.append(wrapper, button("Add array entry", () => add(undefined, true)));
        message(propertyPanel, "Each entry must use one of the binding's allowed values. Entry order and repetitions are preserved.");
        return () => bindingEnumValue(property, rows.map(({select}) => select.value === "" ? undefined : property.enum[Number(select.value)]));
      }
      const select = enumSelect(current);
      propertyPanel.append(label("Property value", select));
      return () => bindingEnumValue(property, select.value === "" ? undefined : property.enum[Number(select.value)]);
    }
    if (property.type === "boolean") {
      const select = element("select"); option(select, "", "Choose presence"); option(select, "true", "Present"); option(select, "false", "Absent");
      if (typeof current === "boolean") select.value = String(current);
      propertyPanel.append(label("Property presence", select));
      return () => bindingValue("boolean", select.value === "" ? undefined : select.value === "true");
    }
    if (property.type === "phandle" || property.type === "path") {
      const select = selectTarget(array(data?.targets), current);
      propertyPanel.append(label("Referenced local node", select));
      return () => bindingValue(property.type, select.value);
    }
    if (property.type === "phandles" || property.type === "phandle-array") return referenceValues(property, current);
    const field = ["array", "uint8-array", "string-array"].includes(property.type) ? element("textarea") : input();
    field.spellcheck = false; field.rows = 3;
    field.value = !present(current) ? "" : Array.isArray(current) ? current.join(property.type === "string-array" ? "\n" : " ") : String(current);
    propertyPanel.append(label("Property value", field));
    message(propertyPanel, property.type === "string-array" ? "Enter one string per line. Each line is escaped as source data."
      : property.type === "uint8-array" ? "Enter bytes from 0 to 255 separated by spaces."
        : ["int", "array"].includes(property.type) ? "Enter unsigned 32-bit integers. Decimal and 0x hexadecimal are accepted." : "Text is escaped as source data.");
    return () => bindingValue(property.type, field.value);
  }
  function renderProperty() {
    propertyPanel.replaceChildren(); valueReader = null;
    const selected = selectedNode(); const property = selectedProperty();
    if (!property) { message(propertyPanel, "Choose a property described by this node's binding."); return; }
    message(propertyPanel, property.description);
    const facts = element("dl", "dte-facts dte-binding-facts");
    const fact = (name, value) => { const row = element("div"); row.append(element("dt", "", name), element("dd", "", value)); facts.append(row); };
    fact("Type", property.type || "Unsupported");
    fact("Required", property.required ? "Required by binding; never added automatically" : "Optional");
    fact("Local value", property.deleted ? "Deleted by this overlay" : property.local ? "Declared in this overlay" : "Not declared; inherited value unknown");
    if (Object.hasOwn(property, "default")) fact("Binding default", display(property.default));
    propertyPanel.append(facts);
    if (property.local?.raw) {
      const details = element("details"); details.append(element("summary", "", "Current local source"), element("pre", "dte-source", property.local.raw)); propertyPanel.append(details);
    }
    const editable = property.editable !== false && bindingTypes.has(property.type);
    if (editable) valueReader = renderValue(property);
    else message(propertyPanel, property.reason || "This property cannot be edited with a supported form. Its source remains unchanged and editable in the source panel.", "dte-notice");
    const actions = element("div", "dte-node-actions");
    const apply = button("Apply binding property", () => {
      try { void submit({kind: "binding_set", node_id: selected.node_id, name: property.name, value: valueReader()}); }
      catch (error) { feedback(error.message); }
    }, true);
    apply.disabled = !editable; apply.dataset.bindingAllowed = String(editable);
    const reset = button("Reset local override", () => submit({kind: "binding_reset", node_id: selected.node_id, name: property.name}));
    reset.disabled = !property.resettable; reset.dataset.bindingAllowed = String(Boolean(property.resettable));
    const remove = button("Delete property in overlay", () => submit({kind: "binding_delete", node_id: selected.node_id, name: property.name}));
    remove.disabled = !property.deletable; remove.dataset.bindingAllowed = String(Boolean(property.deletable));
    actions.append(apply, reset, remove); propertyPanel.append(actions);
    message(propertyPanel, "Apply changes only the working draft. Reset removes its local override and may reveal an inherited value or binding default. Delete writes an explicit deletion directive when supported.");
  }
  function renderNode() {
    bindingInfo.replaceChildren(); propertySelect.replaceChildren(); labelPanel.replaceChildren();
    const selected = selectedNode(); const binding = selected?.binding;
    if (!selected) { option(propertySelect, "", "Choose a node first"); renderProperty(); return; }
    nodeHint = nodeSelection(array(data.nodes).map((item) => ({name: `${item.path || ""}:${item.name}`})), selectedIndex);
    message(bindingInfo, `Binding: ${binding?.state || "unavailable"}${binding?.path ? " · " + binding.path : ""}`, "dte-path");
    message(bindingInfo, binding?.description);
    for (const item of array(binding?.diagnostics)) message(bindingInfo, item.message || item, "dte-notice");
    if (array(binding?.candidates).length > 1) {
      const candidates = element("ul", "dte-list");
      for (const item of binding.candidates) candidates.append(element("li", "", typeof item === "string" ? item : item.path || item.name));
      bindingInfo.append(candidates);
    }
    option(propertySelect, "", "Choose a binding property");
    for (const property of array(selected.properties)) option(propertySelect, property.name, `${property.name} · ${property.type || "unsupported"}${property.required ? " · required" : ""}${property.editable === false ? " · source only" : ""}`);
    if (!array(selected.properties).some((item) => item.name === selectedName)) selectedName = "";
    propertySelect.value = selectedName; renderProperty();
    labelPanel.append(element("h4", "", "Node labels"));
    message(labelPanel, array(selected.labels).length ? selected.labels.join(", ") : "This declaration has no local label.", "dte-path");
    if (data.capabilities?.add_label && selected.add_label !== false) {
      const field = input(); const form = element("form", "dte-binding-inline");
      const add = button("Add label"); add.type = "submit";
      form.append(label("New node label", field), add); labelPanel.append(form);
      listen(form, "submit", (event) => {
        event.preventDefault();
        if (!/^[A-Za-z_][A-Za-z_0-9]*$/.test(field.value)) { feedback("Enter a label using letters, numbers and underscores, beginning with a letter or underscore."); return; }
        void submit({kind: "add_label", node_id: selected.node_id, label: field.value});
      });
    }
    message(labelPanel, "Labels identify local reference targets. Renaming labels is not offered without evidence for references in other files.");
  }
  function renderReferences() {
    const expanded = [...referencePanel.querySelectorAll("details")].map((item) => item.open);
    referencePanel.replaceChildren();
    referencePanel.append(element("h4", "", "Aliases and chosen references"));
    message(referencePanel, "References can select only unique targets declared in this overlay. Existing unresolved or external references remain source-only.");
    for (const [index, [kind, title, collection]] of [["alias", "Alias", "aliases"], ["chosen", "Chosen", "chosen"]].entries()) {
      const details = element("details"); details.append(element("summary", "", `${title} references (${array(data?.[collection]).length})`));
      details.open = Boolean(expanded[index]);
      for (const item of array(data?.[collection])) {
        const row = element("div", "dte-binding-existing"); message(row, `${item.name} = ${item.reference || "unresolved source"} · ${item.state || "unknown"}`, "dte-path");
        if (data.capabilities?.[`set_${kind}`] && item.removable !== false) {
          const remove = button(`Remove ${kind} ${item.name}`, () => submit({kind: `remove_${kind}`, name: item.name})); row.append(remove);
        }
        details.append(row);
      }
      if (data.capabilities?.[`set_${kind}`]) {
        const form = element("form", "dte-binding-reference-form");
        const name = input(); const target = selectTarget(array(data?.targets)); const apply = button(`Set ${kind}`); apply.type = "submit";
        form.append(label(`${title} name`, name), label(`${title} target`, target), apply); details.append(form);
        listen(form, "submit", (event) => {
          event.preventDefault();
          if (!name.value || !target.value) { feedback(`Enter the ${kind} name and choose an existing local target.`); return; }
          void submit({kind: `set_${kind}`, name: name.value, reference: target.value});
        });
      }
      referencePanel.append(details);
    }
  }
  listen(nodeSelect, "change", () => { selectedIndex = Number(nodeSelect.value); renderNode(); updateDisabled(); });
  listen(propertySelect, "change", () => { selectedName = propertySelect.value; renderProperty(); updateDisabled(); });
  function updateDisabled() {
    const blocked = state.bindingView.busy || !session.ready() || !state.bindingView.data;
    for (const item of controls.querySelectorAll("input, select, textarea, button")) item.disabled = blocked || item.dataset.bindingAllowed === "false";
    load.disabled = state.bindingView.busy || !session.ready();
    load.textContent = state.bindingView.busy ? "Loading or updating bindings…" : "Load binding properties";
    cancel.hidden = !session.scanning();
    recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile();
    reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile binding draft";
  }
  function render() {
    if (disposed) return;
    session.sync(); const view = state.bindingView;
    status.textContent = view.message || (view.busy ? "Reading current local schemas…" : "Load binding properties to inspect this source revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error;
    controls.hidden = !view.presentation;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnostics.replaceChildren();
      if (data) {
        for (const item of array(data.diagnostics)) message(diagnostics, item.message || item, "dte-notice");
        nodeSelect.replaceChildren(); option(nodeSelect, "-1", "Choose a node declaration");
        array(data.nodes).forEach((item, index) => option(nodeSelect, String(index), `${array(item.labels).length ? item.labels.join(", ") + ": " : ""}${item.path || item.name}`));
        selectedIndex = restoreNodeSelection(array(data.nodes).map((item) => ({name: `${item.path || ""}:${item.name}`})), nodeHint);
        if (!nodeHint && data.nodes.length === 1) selectedIndex = 0;
        nodeSelect.value = String(selectedIndex); renderNode(); renderReferences();
      }
    }
    renderFeedback(view);
    updateDisabled();
  }
  render();
  return {render, navigate() { generation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; generation++; lifetime.abort(); session.dispose(); }};
}
