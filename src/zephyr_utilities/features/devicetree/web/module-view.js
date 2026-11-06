import {bindingTypes, localBindingValue} from "./bindings.js";
import {peripheralAddress, peripheralFieldValue, peripheralLabel, peripheralName} from "./peripherals.js";
import {createModuleLayoutSession, createModuleSession, modulePositions, moveModule} from "./modules.js";
import {announceResult} from "./view-feedback.js";

const array = (value) => Array.isArray(value) ? value : [];
const shown = (value) => typeof value === "string" ? value : JSON.stringify(value);
const bus = (kind) => ["i2c", "spi"].includes(kind);

export function mountModulePanel(root, {api, state, workspace, drafts, notify, onChange, onRemove}) {
  const document = root.ownerDocument; const lifetime = new AbortController(); const handlers = new WeakMap();
  let disposed = false; let navigation = 0; let data; let rendered; let selected = ""; let detailIdentity = "";
  let positions = {}; let requestedParent = "";
  let readPlacement; let placementState; let pendingSelection; let localActionMessage = ""; let focusAfterCommand = false;
  const cards = new Map(); const formStatuses = new Set();
  const make = (tag, text, className = "") => {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className; return node;
  };
  const listen = (node, event, callback) => {
    if (!handlers.has(node)) handlers.set(node, {}); handlers.get(node)[event] = callback;
  };
  for (const type of ["click", "change", "submit"]) root.addEventListener(type, (event) => {
    for (let node = event.target; node && node !== root; node = node.parentNode) {
      handlers.get(node)?.[type]?.(event); if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (title, action, className = "") => {
    const node = make("button", title, className); node.type = "button";
    if (action) listen(node, "click", action); return node;
  };
  const note = (parent, text, className = "dte-muted") => { if (text) parent.append(make("p", text, className)); };
  const label = (title, control) => { const node = make("label", title); node.append(control); return node; };
  const option = (select, value, title) => { const node = make("option", title); node.value = value; select.append(node); return node; };
  const input = (value = "") => { const node = make("input"); node.type = "text"; node.value = String(value); node.spellcheck = false; node.autocomplete = "off"; return node; };
  const permit = (node, allowed) => { node.dataset.moduleAllowed = String(Boolean(allowed)); node.disabled = !allowed; };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load modules", () => { clearPlacementFeedback(); localActionMessage = ""; void session.load(); });
  const cancel = button("Cancel module inspection", () => session.cancel());
  heading.append(make("h3", "Hardware modules"), load, cancel);
  const status = make("p", "", "dte-module-status dte-muted"); status.setAttribute("role", "status");
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert");
  const recovery = make("div"); const reconcile = button("Reconcile module draft", () => { localActionMessage = ""; void session.reconcile(); });
  recovery.append(reconcile); note(recovery, "Reconcile the canonical draft, then load modules again. The previous action is not repeated.");
  const coverage = make("details"); coverage.append(make("summary", "Source coverage and limitations"));
  const diagnostics = make("div"); coverage.append(diagnostics);
  const empty = make("p", "", "dte-notice");
  const controls = make("div", undefined, "dte-module-controls");
  const toolbar = make("div", undefined, "dte-node-actions");
  const place = button("Place module", () => requestPlacement(), "dte-primary");
  const placementStatus = make("p", "", "dte-module-placement-status dte-muted"); placementStatus.setAttribute("role", "status"); placementStatus.hidden = true;
  toolbar.append(place, placementStatus);
  const workspaceView = make("div", undefined, "dte-module-workspace");
  const canvas = make("div", undefined, "dte-module-canvas"); canvas.setAttribute("role", "group"); canvas.setAttribute("aria-label", "Logical module and bus graph");
  const detail = make("div", undefined, "dte-module-detail");
  const layoutActions = make("div", undefined, "dte-module-layout-actions");
  const layoutStatus = make("p", "Positions affect only this view. Overlay source is unchanged.", "dte-muted"); layoutStatus.setAttribute("role", "status");
  const moveButtons = [];
  for (const [title, dx, dy] of [["Move left", -1, 0], ["Move right", 1, 0], ["Move up", 0, -1], ["Move down", 0, 1]]) {
    const node = button(title, () => {
      try { layout.set(moveModule(positions, selected, dx, dy));
      } catch (error) { layoutStatus.textContent = error.message; }
      updateDisabled();
    });
    moveButtons.push(node); layoutActions.append(node);
  }
  const saveLayout = button("Save layout", () => { layout.set(positions); void layout.save(); });
  const reloadLayout = button("Reload layout", () => { void layout.load(); });
  const layoutError = make("p", "", "dte-error"); layoutError.setAttribute("role", "alert");
  layoutActions.append(saveLayout, reloadLayout, layoutStatus, layoutError); workspaceView.append(canvas, detail); controls.append(toolbar, workspaceView, layoutActions);
  root.append(heading);
  note(root, "Place and connect local devices using binding-aware recipes. This is a logical graph, not a board drawing. Source remains canonical; overlay Save and project Configure are separate actions.");
  root.append(status, error, recovery, empty, coverage, controls);
  const session = createModuleSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  const layout = createModuleLayoutSession({api, state, workspace, ready: () => session.ready() && !state.moduleView.busy,
    onChange() { render(); onChange?.(); }});

  function clearPlacementFeedback() { placementStatus.textContent = ""; placementStatus.hidden = true; }
  function unavailablePlacement() {
    const board = state.data?.target?.requested;
    return board ? `No compatible module connection is available for ${board} in this view yet.`
      : "No connection is available in this view yet. Select a board configuration to load its hardware.";
  }
  function placementGuidance() {
    if (state.moduleView.busy) return "Wait for the current module request to finish, then choose a recipe and connection.";
    if (!state.moduleView.data || !session.ready()) return "Load modules again for the current draft before placing a module.";
    if (!array(data?.recipes).some((recipe) => recipe.available && array(recipe.parents).some((parent) => parent.addable))) return unavailablePlacement();
    const staged = readPlacement?.(); const recipe = data.recipes.find((item) => item.id === staged?.recipe);
    if (!recipe) return requestedParent ? "Choose a module recipe for the selected connection in the placement form."
      : "Choose a module recipe, then a compatible connection in the placement form.";
    if (!recipe.available) return recipe.reason || "Choose an available module recipe for this board.";
    const parent = array(recipe.parents).find((item) => item.parent_key === staged.parent);
    if (!parent) return "Choose a compatible connection for this recipe in the placement form.";
    if (!parent.addable) return parent.reason || "Choose an available connection for this recipe.";
    return "Complete the module fields, then choose Place module in draft.";
  }
  function requestPlacement(parentKey = "") {
    requestedParent = parentKey; select("");
    const message = placementGuidance(); placementStatus.textContent = message; placementStatus.hidden = false;
    announceResult(detail, notify, {message});
  }
  function fail(message) { localActionMessage = message; session.fail(message); }
  async function submit(command, key = selected) {
    clearPlacementFeedback();
    const token = navigation; const revision = data?.revision; const identity = JSON.stringify([workspace(), state.data?.id, state.draftView?.entry?.server?.id]);
    const focus = {original: document.activeElement, moved: false}; const listeners = new AbortController();
    document.addEventListener?.("focusin", (event) => {
      if (event.target !== focus.original && event.target !== document.body) focus.moved = true;
    }, {signal: listeners.signal});
    document.addEventListener?.("pointerdown", () => { focus.moved = true; }, {signal: listeners.signal});
    pendingSelection = {key, revision, identity, focus, follow: true}; localActionMessage = "Updating the module in the overlay draft…";
    let accepted;
    try { accepted = await session.command(command); } finally { listeners.abort(); }
    if (disposed || token !== navigation || identity !== JSON.stringify([workspace(), state.data?.id, state.draftView?.entry?.server?.id])) return accepted;
    localActionMessage = accepted ? "Draft updated. Review and Save the overlay separately." : state.moduleView.error || "Reload modules to inspect the current draft.";
    if (!accepted && state.draftView.entry.server.revision === revision) pendingSelection = null;
    render(); return accepted;
  }
  function form(parent, title, action) {
    const node = make("form", undefined, "dte-module-form"); node.append(make("h4", title)); parent.append(node);
    listen(node, "submit", (event) => { event.preventDefault(); try { action(); } catch (error) { fail(error.message); } });
    return node;
  }
  function apply(parent, title, allowed = true) {
    const node = button(title, null, "dte-primary"); node.type = "submit"; permit(node, allowed);
    const result = make("p", "", "dte-module-action-status dte-muted"); result.setAttribute("role", "status"); formStatuses.add(result);
    parent.append(node, result); return node;
  }
  function referenceSelect(providers, value) {
    const node = make("select"); option(node, "", "Choose a local provider");
    for (const provider of providers) option(node, provider.reference, `${provider.reference}${provider.path ? " · " + provider.path : ""}`);
    const current = providers.find((item) => item.reference === value || item.path && [item.path, `&{${item.path}}`].includes(value)
      || array(item.labels).some((name) => `&${name}` === value));
    if (current) node.value = current.reference; return node;
  }
  function valueControl(parent, property) {
    const current = localBindingValue(property);
    if (Object.hasOwn(property, "const")) { note(parent, `Constant: ${shown(property.const)}`); return () => property.const; }
    if (property.type === "phandle-array") {
      const providers = array(property.providers || data.gpio_providers); const entries = []; const list = make("div", undefined, "dte-binding-reference-rows");
      function add(value = {}) {
        if (entries.length >= 64) { fail("A property supports up to 64 provider entries."); return; }
        const row = make("div", undefined, "dte-binding-reference-row"); const provider = referenceSelect(providers, value.reference);
        const cells = make("div", undefined, "dte-binding-cells"); const entry = {row, provider, inputs: []}; entries.push(entry);
        function populate(values = []) {
          cells.replaceChildren(); entry.inputs = [];
          for (const [index, name] of array(providers.find((item) => item.reference === provider.value)?.cells).entries()) {
            const field = input(values[index] ?? ""); cells.append(label(name, field)); entry.inputs.push(field);
          }
          updateDisabled();
        }
        row.append(label("Provider", provider), cells, button("Remove provider entry", () => {
          entries.splice(entries.indexOf(entry), 1); row.remove();
        }));
        listen(provider, "change", () => populate()); list.append(row); populate(value.cells);
      }
      parent.append(list, button("Add provider entry", () => { add(); updateDisabled(); }));
      for (const value of array(current)) add(value); if (!entries.length) add();
      if (!providers.length) note(parent, "No local provider has an established cell layout. Use source editing to declare the required provider.", "dte-notice");
      return () => entries.map((entry) => ({reference: entry.provider.value, cells: entry.inputs.map((node) => node.value)}));
    }
    if (["phandle", "path"].includes(property.type)) {
      const node = referenceSelect(array(data.targets), current); parent.append(label("Referenced node", node)); return () => node.value;
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
      parent.append(label("Value", node)); return () => {
        if (node.value === "") throw new Error("Choose an allowed value."); return property.enum[Number(node.value)];
      };
    }
    if (property.type === "boolean") {
      const node = make("select"); option(node, "", "Choose presence"); option(node, "true", "Present");
      if (current === true) node.value = "true"; parent.append(label("Presence", node));
      return () => node.value === "true" ? true : undefined;
    }
    const node = ["array", "uint8-array", "string-array"].includes(property.type) ? make("textarea") : input();
    node.spellcheck = false; node.rows = 3;
    node.value = current === undefined || current === null ? "" : Array.isArray(current) ? current.join(property.type === "string-array" ? "\n" : " ") : String(current);
    parent.append(label("Value", node));
    if (property.enum) note(parent, `Allowed entries: ${property.enum.map(shown).join(", ")}`);
    if (["int", "array", "uint8-array"].includes(property.type)) note(parent, "Use decimal or hexadecimal integers. Values must come from your hardware configuration.");
    return () => node.value;
  }
  function fields(parent, properties, creating = false, retained = {}) {
    const entries = [];
    for (const property of array(properties)) {
      const group = make("fieldset", undefined, "dte-peripheral-field"); group.append(make("legend", property.name));
      const required = creating && property.required; const include = make("input"); include.type = "checkbox";
      include.checked = Boolean(required || retained[property.name]?.include);
      const supported = property.editable !== false && bindingTypes.has(property.type); permit(include, supported && !required);
      group.append(label(required ? "Required for this module" : "Set in this action", include)); note(group, property.description);
      if (Object.hasOwn(property, "default")) note(group, `Binding default (not applied automatically): ${shown(property.default)}`);
      if (!creating) note(group, property.local ? `Local value: ${shown(localBindingValue(property))}` : "No local value; inherited value unknown.");
      const body = make("div"); body.dataset.moduleField = String(include.checked && supported); group.append(body);
      let read; if (supported) read = valueControl(body, Object.hasOwn(retained[property.name] || {}, "value")
        ? {...property, local: {parsed: retained[property.name].value}} : property);
      else note(body, property.reason || "This field needs source editing.", "dte-notice");
      listen(include, "change", () => { body.dataset.moduleField = String(include.checked && supported); updateDisabled(); });
      parent.append(group); entries.push({property, include, read});
    }
    const collect = () => Object.fromEntries(entries.filter((entry) => entry.include.checked).map(({property, read}) => {
      if (!read) throw new Error(`${property.name}: this required field needs source editing.`);
      try { return [property.name, Object.hasOwn(property, "const") ? read() : peripheralFieldValue(property, read())]; }
      catch (error) { throw new Error(`${property.name}: ${error.message}`); }
    }));
    collect.snapshot = () => Object.fromEntries(entries.map(({property, include, read}) => {
      try { return [property.name, {include: include.checked, value: read?.()}]; }
      catch { return [property.name, {include: include.checked}]; }
    }));
    return collect;
  }
  function addressControl(parent, value = "") {
    if (parent.kind !== "spi" || !Array.isArray(parent.chip_selects)) return input(value);
    const node = make("select"); option(node, "", "Choose an existing chip-select index");
    for (const index of parent.chip_selects) option(node, String(index), String(index));
    if (parent.chip_selects.includes(value)) node.value = String(value); return node;
  }
  function software(parent, recipe) {
    const section = make("div", undefined, "dte-module-software"); section.append(make("h5", "Software requirements"));
    for (const requirement of array(recipe?.software_requirements)) note(section, requirement);
    note(section, "Informational only. Driver configuration and hardware compatibility are not established by placing this module; no Kconfig settings are changed."); parent.append(section);
  }
  function placement() {
    let recipe; let parent; let collect = () => ({}); let address;
    const node = form(detail, "Place and connect a module", () => {
      if (!recipe?.available || !parent?.addable) throw new Error(parent?.reason || recipe?.reason || "Choose an available recipe and connection.");
      const current = data.recipes.find((item) => item.id === recipe.id)?.parents.find((item) => item.parent_key === parent.parent_key);
      if (!current?.addable) throw new Error("Load modules again before placing on this connection.");
      const nodeName = peripheralName(name.value); const nodeAddress = bus(recipe.kind) ? peripheralAddress(address.value, current) : null;
      const key = `node:${current.path === "/" ? "" : current.path}/${nodeName}${nodeAddress === null ? "" : "@" + nodeAddress.toString(16)}`;
      void submit({kind: "module_add", recipe_id: recipe.id, recipe_version: recipe.version,
        parent_id: current.parent_id, name: nodeName, label: peripheralLabel(nodeLabel.value), address: nodeAddress, values: collect()}, key);
    });
    const recipes = make("select"); option(recipes, "", "Choose a local recipe");
    for (const item of data.recipes) option(recipes, item.id, `${item.name}${item.available ? "" : " · unavailable"}`);
    const parents = make("select"); const description = make("p", "", "dte-muted"); const reason = make("p", "", "dte-notice");
    const name = input(); const nodeLabel = input(); const parameters = make("div"); const requirements = make("div");
    node.append(label("Module recipe", recipes), description, label("Connect to bus or GPIO group", parents), reason,
      label("Node name", name), label("Node label (optional)", nodeLabel), parameters, requirements);
    const add = apply(node, "Place module in draft", false);
    note(node, "Only this module's local node is added. Choose an existing bus or GPIO group; controllers, pin assignments, providers and software settings require their own explicit actions.");
    function connection(retained) {
      parent = array(recipe?.parents).find((item) => item.parent_key === parents.value);
      parameters.replaceChildren();
      reason.textContent = parent?.reason || recipe?.reason || (!recipe
        ? array(data.recipes).some((item) => item.available && array(item.parents).some((connection) => connection.addable))
          ? "Choose a module recipe first, then its connection."
          : unavailablePlacement()
        : !parent ? "Choose a compatible connection, then complete the module fields." : "");
      collect = () => ({}); address = null;
      if (parent) {
        if (bus(recipe.kind)) { address = addressControl(parent, retained?.address ?? ""); parameters.append(label(recipe.kind === "i2c" ? "I2C address" : "SPI chip select", address)); }
        collect = fields(parameters, parent.fields, true, retained?.fields);
      }
      permit(add, recipe?.available && parent?.addable); updateDisabled();
    }
    function chooseRecipe(retained) {
      recipe = data.recipes.find((item) => item.id === recipes.value); parents.replaceChildren(); option(parents, "", "Choose a connection explicitly");
      for (const item of array(recipe?.parents)) option(parents, item.parent_key, `${item.path || item.name}${item.addable ? "" : " · unavailable"}`);
      const parentKey = requestedParent || retained?.parent;
      if (parentKey && array(recipe?.parents).some((item) => item.parent_key === parentKey)) parents.value = parentKey;
      name.value = retained?.name ?? recipe?.node_name ?? ""; nodeLabel.value = retained?.label || ""; description.textContent = recipe?.description || "";
      requirements.replaceChildren(); if (recipe) software(requirements, recipe); connection(retained);
    }
    listen(recipes, "change", () => chooseRecipe()); listen(parents, "change", () => connection());
    if (placementState && data.recipes.some((item) => item.id === placementState.recipe)) recipes.value = placementState.recipe;
    chooseRecipe(placementState);
    readPlacement = () => ({recipe: recipes.value, parent: parents.value, name: name.value, label: nodeLabel.value,
      address: address?.value, fields: collect.snapshot?.() || {}});
  }
  function instanceDetail(instance) {
    const parent = data.parents.find((item) => item.node_id === instance.parent_id || item.key === instance.parent_key);
    if (!parent) { note(detail, "The local connection is unavailable. Use source editing.", "dte-notice"); return; }
    let collect; const node = form(detail, "Module properties", () => {
      const current = data.instances.find((item) => item.key === selected);
      if (!current?.editable) throw new Error(current?.reason || "This source node is not editable through the module form.");
      const values = collect(); const nextAddress = bus(instance.kind) ? peripheralAddress(address.value, parent, current.node_id) : null;
      if (!Object.keys(values).length && nextAddress === current.address) throw new Error("Choose at least one property or change the address.");
      const key = bus(instance.kind) && nextAddress !== current.address
        ? `node:${parent.path === "/" ? "" : parent.path}/${current.name.split("@")[0]}@${nextAddress.toString(16)}` : selected;
      void submit({kind: "module_update", node_id: current.node_id, address: nextAddress, values}, key);
    });
    let address;
    if (bus(instance.kind)) {
      address = addressControl(parent, instance.address ?? ""); permit(address, instance.address_editable !== false);
      node.append(label(instance.kind === "i2c" ? "I2C address" : "SPI chip select", address)); note(node, instance.address_reason, "dte-notice");
    }
    collect = fields(node, instance.fields); apply(node, "Apply module properties", instance.editable);
    note(node, instance.reason, "dte-notice");
    let reviewingRemoval = false;
    const remove = button(onRemove ? "Review module removal" : "Remove this module from draft", async () => {
      const current = data.instances.find((item) => item.key === selected);
      if (onRemove && current) {
        if (reviewingRemoval) return;
        reviewingRemoval = true; const token = navigation;
        localActionMessage = "Preparing removal review…"; updateDisabled();
        try { await onRemove(current.node_id, remove); }
        catch (error) { if (!disposed && token === navigation) fail(error.message || String(error)); }
        finally {
          reviewingRemoval = false;
          if (!disposed && token === navigation && localActionMessage === "Preparing removal review…") { localActionMessage = ""; updateDisabled(); }
        }
        return;
      }
      if (!current?.removable) { fail(current?.remove_reason || "This module cannot be removed by this form."); return; }
      void submit({kind: "module_remove", node_id: current.node_id}, "");
    }); permit(remove, onRemove ? Boolean(instance.node_id) : instance.removable); detail.append(remove);
    note(detail, onRemove ? "Review known dependants, explicit repair choices and the exact source changes before removing this node from the draft."
      : "Removal deletes only this local leaf node. Parent buses, GPIO providers and other source are preserved. Review the resulting diff before Save.");
    note(detail, instance.remove_reason, "dte-notice");
    for (const match of array(instance.recipe_matches)) {
      const recipe = data.recipes.find((item) => item.id === match.id && item.version === match.version);
      if (recipe) { note(detail, `Recognized recipe: ${recipe.name}. This remains an ordinary source node.`); software(detail, recipe); }
    }
  }
  function renderDetail(force = false) {
    const node = data?.graph.nodes.find((item) => item.key === selected);
    const instance = data?.instances.find((item) => item.key === selected);
    const identity = JSON.stringify([selected, instance || node, selected ? null : data?.recipes],
      (key, value) => ["node_id", "parent_id"].includes(key) ? undefined : value);
    if (!force && identity === detailIdentity) return;
    const oldControls = [...detail.querySelectorAll("input, select, textarea, button")];
    const focused = oldControls.indexOf(document.activeElement);
    if (readPlacement) placementState = readPlacement(); readPlacement = null;
    detailIdentity = identity; detail.replaceChildren(); formStatuses.clear();
    if (!selected) {
      placement();
      if (focused >= 0 && !focusAfterCommand) detail.querySelectorAll("input, select, textarea, button")[focused]?.focus({preventScroll: true});
      return;
    }
    if (!node) { note(detail, "This source node is no longer present. Choose another card or place a module."); return; }
    detail.append(make("h4", node.name), make("p", node.path, "dte-path"));
    note(detail, instance ? "Source node. Existing handwritten declarations remain editable without recipe conversion."
      : `${node.role === "provider" ? "GPIO provider" : "Local connection"}. Physical routing and inherited hardware remain unknown unless separately established.`);
    const parent = data.parents.find((item) => item.key === selected);
    if (parent) detail.append(button("Place module on this connection", () => requestPlacement(selected)));
    if (instance) instanceDetail(instance);
    if (focused >= 0 && !focusAfterCommand) detail.querySelectorAll("input, select, textarea, button")[focused]?.focus({preventScroll: true});
  }
  function select(key) {
    clearPlacementFeedback();
    localActionMessage = "";
    if (pendingSelection) pendingSelection.follow = false;
    if (selected === key && detailIdentity) { updateDisabled(); return; }
    selected = key; renderSelection(); renderDetail(); updateDisabled();
  }
  function renderSelection() {
    for (const [key, card] of cards) card.button.setAttribute("aria-pressed", String(key === selected));
    place.setAttribute("aria-pressed", String(!selected));
  }
  function renderPositions() {
    for (const [key, card] of cards) {
      const position = positions[key]; if (!position) continue;
      card.node.style.gridColumn = String(position.x + 1); card.node.style.gridRow = String(position.y + 1);
      card.button.setAttribute("aria-description", `Logical column ${position.x + 1}, row ${position.y + 1}. Select the card to use layout move controls.`);
    }
  }
  function renderGraph() {
    const present = new Set(data.graph.nodes.map((item) => item.key));
    if (pendingSelection && data.revision !== pendingSelection.revision
        && (!pendingSelection.key || present.has(pendingSelection.key))) {
      if (pendingSelection.follow) { selected = pendingSelection.key; focusAfterCommand = pendingSelection.focus; }
      pendingSelection = null;
      localActionMessage = "Draft updated. Review and Save the overlay separately.";
    }
    for (const [key, card] of cards) if (!present.has(key)) { card.node.remove(); cards.delete(key); }
    for (const node of data.graph.nodes) {
      let card = cards.get(node.key);
      if (!card) {
        const wrapper = make("div", undefined, "dte-module-card"); const selectNode = button("", () => select(node.key));
        const title = make("strong"); const kind = make("span", "", "dte-muted"); const path = make("span", "", "dte-path"); const connection = make("span", "", "dte-muted");
        selectNode.append(title, kind, path, connection); wrapper.append(selectNode); canvas.append(wrapper);
        card = {node: wrapper, button: selectNode, title, kind, path, connection}; cards.set(node.key, card);
      }
      card.title.textContent = node.name; card.kind.textContent = `${node.role} · ${node.kind || "local"}`; card.path.textContent = node.path;
      const edges = data.graph.edges.filter((edge) => edge.source === node.key || edge.target === node.key);
      card.connection.textContent = edges.map((edge) => {
        const other = data.graph.nodes.find((item) => item.key === (edge.source === node.key ? edge.target : edge.source));
        return `${edge.kind === "gpio" ? "GPIO" : "Bus"}: ${other?.path || "unknown local connection"}`;
      }).join("; ") || "No established local connection";
    }
    canvas.classList.toggle("dte-module-canvas-empty", !cards.size);
    if (selected && !present.has(selected)) selected = "";
    renderSelection(); renderDetail();
  }
  function updateDisabled() {
    const view = state.moduleView; const blocked = view.busy || !session.ready() || !view.data;
    for (const item of detail.querySelectorAll("input, select, textarea, button")) {
      item.disabled = blocked || item.dataset.moduleAllowed === "false" || item.closest("[data-module-field]")?.dataset.moduleField === "false";
    }
    place.disabled = !data;
    for (const node of moveButtons) node.disabled = !selected || !Object.hasOwn(positions, selected) || !state.moduleLayoutView.loaded || state.moduleLayoutView.busy;
    saveLayout.disabled = !layout.canSave() || Object.keys(positions).length > 256;
    saveLayout.textContent = state.moduleLayoutView.busy ? "Loading or saving layout…" : "Save layout";
    reloadLayout.disabled = state.moduleLayoutView.busy || blocked;
    load.disabled = view.busy || !session.ready(); load.textContent = view.busy ? "Loading or updating modules…" : "Load modules";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile();
    for (const result of formStatuses) result.textContent = localActionMessage ? view.error || localActionMessage : "";
  }
  function render() {
    if (disposed) return;
    session.sync(); layout.sync(); const view = state.moduleView;
    const presentation = view.presentation || view.data;
    coverage.hidden = !presentation; if (!presentation) empty.hidden = true;
    status.textContent = view.message || (view.busy ? "Inspecting local modules…" : "Load modules to inspect this overlay revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error; controls.hidden = !presentation;
    if (rendered !== presentation) {
      rendered = data = presentation; diagnostics.replaceChildren();
      if (data) {
        for (const item of data.diagnostics) note(diagnostics, item.message || item, "dte-notice");
        empty.hidden = data.graph.nodes.length > 0;
        empty.textContent = unavailablePlacement();
        renderGraph();
        if (!state.moduleLayoutView.loaded && !state.moduleLayoutView.busy && view.data) void layout.load();
      } else {
        selected = ""; detailIdentity = ""; positions = {}; readPlacement = null; placementState = null; pendingSelection = null; localActionMessage = "";
        clearPlacementFeedback();
        cards.clear(); canvas.replaceChildren(); detail.replaceChildren(); formStatuses.clear(); layout.clear(); empty.hidden = true;
      }
    }
    if (data) { positions = modulePositions(data.graph.nodes, state.moduleLayoutView.positions); renderPositions(); }
    const layoutView = state.moduleLayoutView;
    layoutStatus.textContent = Object.keys(positions).length > 256 ? "Layout saving supports up to 256 graph nodes. Overlay source is unchanged."
      : layoutView.message || "Positions affect only this view. Overlay source is unchanged.";
    layoutError.hidden = !layoutView.error; layoutError.textContent = layoutView.error;
    updateDisabled();
    if (focusAfterCommand) {
      const focus = focusAfterCommand; focusAfterCommand = false; const active = document.activeElement;
      if (!focus.moved && (!document.hasFocus || document.hasFocus()) && (!active || active === focus.original || active === document.body)) {
        const target = Array.from(detail.querySelectorAll("button")).find((item) => !item.disabled && item.type === "submit") || place;
        target.focus({preventScroll: true});
      }
    }
  }
  render();
  return {render, navigate() { navigation++; session.clear(); layout.clear(); render(); },
    dispose() { disposed = true; navigation++; lifetime.abort(); session.dispose(); layout.dispose(); }};
}
