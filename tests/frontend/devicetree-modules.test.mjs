import test from "node:test";
import assert from "node:assert/strict";
import {createModuleLayoutSession, createModuleSession, modulePosition, modulePositions, moveModule} from "../../src/zephyr_utilities/features/devicetree/web/modules.js";
import {mountModulePanel} from "../../src/zephyr_utilities/features/devicetree/web/module-view.js";

const source = '/ { i2c0: i2c { compatible = "test,i2c"; }; };\n';
const i2cKey = "node:/i2c"; const sensorKey = "node:/i2c/sensor@76";
function fixture() {
  const integer = {name: "spi-max-frequency", type: "int", required: true, editable: true};
  const gpios = {name: "gpios", type: "phandle-array", required: true, editable: true};
  const parent = (node_id, key, kind) => ({node_id, key, name: kind + "0", path: key.slice(5), kind, role: kind === "led" ? "group" : "controller",
    addable: true, editable: true, fields: [], children: [], chip_selects: kind === "spi" ? [0, 1] : null});
  const parents = [parent("i2c", i2cKey, "i2c"), parent("spi", "node:/spi", "spi"), parent("leds", "node:/leds", "led")];
  const recipe = (id, kind, fields) => ({id, version: 1, name: id, description: "Local module recipe", kind, node_name: kind === "led" ? "led" : "sensor",
    software_requirements: ["Configure the relevant driver explicitly."], available: true, reason: "", parents: parents.filter((item) => item.kind === kind)
      .map((item) => ({parent_id: item.node_id, parent_key: item.key, name: item.name, path: item.path, kind, addable: true, reason: "", fields,
        chip_selects: item.chip_selects}))});
  const instance = {node_id: "sensor", key: sensorKey, parent_id: "i2c", parent_key: i2cKey, kind: "i2c", name: "sensor@76", path: "/i2c/sensor@76",
    address: 0x76, address_editable: true, editable: true, removable: true, origin: "source", recipe_matches: [{id: "I2C sensor", version: 1}],
    fields: [{name: "status", type: "string", editable: true, enum: ["okay", "disabled"], local: {parsed: "okay"}}]};
  parents[0].children.push(instance);
  return {recipes: [recipe("I2C sensor", "i2c", []), recipe("SPI device", "spi", [integer]), recipe("GPIO indicator", "led", [gpios])],
    parents, instances: [instance], targets: [], diagnostics: [],
    gpio_providers: [{id: "gpio", reference: "&gpio0", path: "/gpio", cells: ["pin", "flags"]}],
    graph: {nodes: [...parents.map((item) => ({...item})), {...instance, role: "module"},
      {key: "node:/gpio", node_id: "gpio", name: "gpio0", path: "/gpio", kind: "gpio", role: "provider"}],
    edges: [{key: "bus:" + sensorKey, source: i2cKey, target: sensorKey, kind: "bus"}]}};
}
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }
function harness({mount = false, wrap = (api) => api} = {}) {
  const state = {data: {workspace: "/workspace", id: "context", revision: 1}, draftView: {ready: true,
    entry: {generation: 0, text: source, server: {id: "draft", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel; let layout; const calls = []; const commands = []; const reveals = []; const model = fixture();
  let savedLayout = {schema_version: 1, layout_id: "layout", revision: 0, positions: {}};
  const response = () => ({...structuredClone(model), schema_id: "schema" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const notify = () => { session?.sync(); panel?.render(); };
  const api = wrap(async (url, options) => {
    calls.push({url, ...options});
    if (url.endsWith("module-layout/save")) { savedLayout = {...savedLayout, revision: savedLayout.revision + 1, positions: structuredClone(options.body.positions)}; return structuredClone(savedLayout); }
    if (url.endsWith("module-layout")) return structuredClone(savedLayout);
    return response();
  });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(structuredClone(command)); const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"}; entry.text = entry.server.text; notify(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; notify(); return true; }};
  const root = mount ? new Element("section") : null;
  const options = {api, state, drafts, workspace: () => workspace, onChange: notify};
  if (mount) panel = mountModulePanel(root, {...options, reveal: (node) => reveals.push(node)});
  else {
    session = createModuleSession(options);
    layout = createModuleLayoutSession({...options, ready: () => session.ready()});
  }
  return {state, session, panel, layout, root, drafts, calls, commands, model, response, reveals, options,
    workspace(value) { workspace = value; session?.sync(); layout?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; notify(); },
    dispose() { session?.dispose(); panel?.dispose(); layout?.dispose(); }};
}

test("module observation uses an explicit exact revision guard without changing source", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); assert.equal(await app.session.load(), true);
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/modules");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("module commands share canonical history and receive a fresh schema after each action", async () => {
  const app = harness(); await app.session.load();
  await app.session.command({kind: "module_remove", node_id: "sensor"});
  await app.session.command({kind: "module_remove", node_id: "sensor"});
  assert.deepEqual(app.commands.map((item) => item.schema_id), ["schema0", "schema1"]);
  assert.equal(app.state.moduleView.data.revision, 2); app.dispose();
});

test("invalid and late module observations cannot authorize editing", async () => {
  const app = harness({wrap: (api) => async (...args) => { const result = await api(...args); delete result.graph; return result; }});
  assert.equal(await app.session.load(), false); assert.match(app.state.moduleView.error, /does not match/); app.dispose();
  const wait = deferred(); const late = harness({wrap: (api) => async (...args) => { const result = await api(...args); await wait.promise; return result; }});
  const loading = late.session.load(); late.workspace("/other"); wait.resolve(); assert.equal(await loading, false);
  assert.equal(late.state.moduleView.data, null); late.dispose();
});

test("source changes retain a read-only module presentation and block stale commands", async () => {
  const app = harness(); await app.session.load(); const presentation = app.state.moduleView.presentation;
  app.type(source + "/* pending */"); assert.equal(app.state.moduleView.data, null); assert.equal(app.state.moduleView.presentation, presentation);
  assert.equal(await app.session.command({kind: "module_remove", node_id: "sensor"}), false); assert.equal(app.commands.length, 0); app.dispose();
});

test("logical positions are stable path keys, bounded and never overlap", () => {
  const nodes = fixture().graph.nodes; const saved = {[sensorKey]: {x: 2, y: 3}};
  const positions = modulePositions(nodes, saved); assert.deepEqual(positions[sensorKey], saved[sensorKey]);
  assert.deepEqual(modulePositions(nodes.map((item) => ({...item, node_id: "different"})), positions), positions);
  assert.equal(new Set(Object.values(positions).map(({x, y}) => `${x}:${y}`)).size, nodes.length);
  const full = modulePositions(Array.from({length: 256}, (_, index) => ({key: "node:/n" + index})), {"node:/n0": {x: 31, y: 31}});
  assert.equal(Object.keys(full).length, 256); assert.ok(Object.values(full).every(({x, y}) => x <= 31 && y <= 31));
  assert.deepEqual(modulePosition({x: Infinity, y: 0}), {x: 0, y: 0});
  assert.deepEqual(modulePosition({x: 0.5, y: 0}), {x: 0, y: 0});
});

test("keyboard moves swap occupied slots and reject out-of-bounds positions", () => {
  const original = {a: {x: 0, y: 0}, b: {x: 1, y: 0}};
  assert.deepEqual(moveModule(original, "a", 1, 0), {a: {x: 1, y: 0}, b: {x: 0, y: 0}});
  assert.deepEqual(original.a, {x: 0, y: 0}); assert.throws(() => moveModule(original, "a", -1, 0), /edge/);
  assert.throws(() => moveModule(original, "missing", 1, 0), /Select/);
});

test("layout persistence is separate from source/history and uses optimistic revision guards", async () => {
  const app = harness(); await app.session.load(); assert.equal(await app.layout.load(), true);
  const positions = {[sensorKey]: {x: 4, y: 2}}; assert.equal(app.layout.set(positions), true);
  assert.equal(app.layout.canSave(), true); assert.equal(await app.layout.save(), true);
  const request = app.calls.at(-1); assert.equal(request.url, "/api/devicetree/editor/drafts/module-layout/save");
  assert.deepEqual(request.body.positions, positions); assert.equal(request.body.expected_layout_revision, 0); assert.equal(request.body.schema_id, "schema0");
  assert.equal(app.state.moduleLayoutView.revision, 1); assert.equal(app.state.moduleLayoutView.dirty, false);
  assert.equal(app.commands.length, 0); assert.equal(app.state.draftView.entry.text, source); app.dispose();
});

test("failed layout saves keep dirty positions and never replay a source action", async () => {
  const app = harness({wrap: (api) => async (url, options) => {
    if (url.endsWith("/save")) throw new Error("Layout revision conflict. Reload layout."); return api(url, options);
  }});
  await app.session.load(); await app.layout.load(); const positions = {[sensorKey]: {x: 2, y: 4}}; app.layout.set(positions);
  assert.equal(await app.layout.save(), false); assert.equal(app.state.moduleLayoutView.dirty, true);
  assert.deepEqual(app.state.moduleLayoutView.positions, positions); assert.match(app.state.moduleLayoutView.error, /revision conflict/); app.dispose();
});

test("late layout responses cannot overwrite a different source observation or workspace", async () => {
  const wait = deferred(); const app = harness({wrap: (api) => async (...args) => {
    const result = await api(...args); if (args[0].endsWith("module-layout")) await wait.promise; return result;
  }});
  await app.session.load(); const loading = app.layout.load(); app.type(source + "/* changed */"); wait.resolve();
  assert.equal(await loading, false); assert.equal(app.state.moduleLayoutView.loaded, false); assert.equal(app.state.moduleLayoutView.busy, false);
  assert.match(app.state.moduleLayoutView.message, /source changed/); app.workspace("/other"); assert.deepEqual(app.state.moduleLayoutView.positions, {}); app.dispose();
});

test("saved-source context refresh preserves physical layout while rejecting its pending stale response", async () => {
  const wait = deferred(); const app = harness({wrap: (api) => async (...args) => {
    const result = await api(...args); if (args[0].endsWith("module-layout/save")) await wait.promise; return result;
  }});
  Object.assign(app.state.data, {workspace_identity: "workspace-id", application: {path: "/workspace/app", identity: "app-id"},
    project: {id: "project"}, configuration: {id: "configuration"}, target: {requested: "board/soc"}});
  app.state.draftView.entry.path = "/workspace/app/boards/board.overlay"; app.state.draftView.entry.server.document_id = "overlay";
  await app.session.load(); await app.layout.load(); const positions = {[sensorKey]: {x: 9, y: 6}}; app.layout.set(positions);
  const saving = app.layout.save(); app.state.data.id = "refreshed-context"; app.state.data.revision++;
  app.session.sync(); app.layout.sync();
  assert.deepEqual(app.state.moduleLayoutView.positions, positions); assert.equal(app.state.moduleView.data, null);
  wait.resolve(); assert.equal(await saving, false); assert.equal(app.state.moduleLayoutView.dirty, true);
  assert.equal(app.state.moduleLayoutView.revision, 0); assert.deepEqual(app.state.moduleLayoutView.positions, positions);
  app.state.data.application.path = "/workspace/another-app"; app.layout.sync(); assert.deepEqual(app.state.moduleLayoutView.positions, {}); app.dispose();
});

// The browser remains responsible for layout; this DOM exercises event-driven forms and stable identity.
class Element {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = document || {listeners: {}, createElement: (name) => new Element(name, this.ownerDocument),
      addEventListener(type, callback, options) { (this.listeners[type] ||= []).push(callback); options?.signal?.addEventListener("abort", () => { this.listeners[type] = this.listeners[type].filter((item) => item !== callback); }); },
      dispatch(type, target) { for (const callback of this.listeners[type] || []) callback({target}); }};
    this.children = []; this.parentNode = null; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.style = {};
    this.classList = {toggle() {}}; this._text = ""; this._value = null; this.type = ""; this.disabled = false; this.hidden = false; this.checked = false;
  }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set value(value) { this._value = String(value); }
  get value() { return this._value ?? (this.tagName === "SELECT" ? this.children[0]?.value || "" : ""); }
  append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this); this.parentNode = null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  focus(options) { this.ownerDocument.activeElement = this; this.focusOptions = options; this.ownerDocument.dispatch("focusin", this); }
  addEventListener(type, callback, options) { (this.listeners[type] ||= []).push(callback); options?.signal?.addEventListener("abort", () => { this.listeners[type] = this.listeners[type].filter((item) => item !== callback); }); }
  matches(selector) { return selector === "[data-module-field]" ? this.dataset.moduleField !== undefined : this.tagName.toLowerCase() === selector; }
  querySelectorAll(selector) {
    const selectors = selector.split(",").map((value) => value.trim());
    const nodes = this.children.flatMap((child) => [...(selectors.some((value) => child.matches(value)) ? [child] : []), ...child.querySelectorAll(selector)]);
    return Object.assign({length: nodes.length, [Symbol.iterator]: () => nodes[Symbol.iterator]()}, nodes);
  }
  closest(selector) { for (let item = this; item; item = item.parentNode) if (item.matches(selector)) return item; return null; }
  dispatch(type) { const event = {target: this, cancelBubble: false, preventDefault() {}}; for (let item = this; item; item = item.parentNode) for (const callback of item.listeners[type] || []) callback(event); }
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const named = (root, tag, text) => Array.from(root.querySelectorAll(tag)).find((node) => node.textContent.startsWith(text));
const field = (root, text) => named(root, "label", text).children[0];
function change(node, value) { node.value = value; node.dispatch("change"); }
async function click(root, text) {
  const node = named(root, "button", text); assert.ok(node, text); assert.equal(node.disabled, false, text + " enabled");
  node.ownerDocument.dispatch("pointerdown", node); node.focus(); node.dispatch("click"); if (node.type === "submit") node.closest("form").dispatch("submit"); await settle(); return node;
}
async function placement(app, recipe, parent) {
  await click(app.root, "Load modules"); change(field(app.root, "Module recipe"), recipe);
  change(field(app.root, "Connect to bus or GPIO group"), parent);
}

test("keyboard placement connects an I2C recipe and emits only an explicit leaf command", async () => {
  const app = harness({mount: true}); await placement(app, "I2C sensor", i2cKey);
  change(field(app.root, "I2C address"), "0x77"); change(field(app.root, "Node label"), "sensor_two");
  await click(app.root, "Place module in draft");
  assert.deepEqual(app.commands, [{kind: "module_add", recipe_id: "I2C sensor", recipe_version: 1,
    parent_id: "i2c", name: "sensor", label: "sensor_two", address: 0x77, values: {}, schema_id: "schema0"}]);
  assert.equal(app.reveals.length, 0); assert.match(app.root.textContent, /Software requirements/); assert.match(app.root.textContent, /no Kconfig settings/); app.dispose();
});

test("SPI placement collects an explicit chip select and required binding parameter", async () => {
  const app = harness({mount: true}); await placement(app, "SPI device", "node:/spi");
  change(field(app.root, "SPI chip select"), "1");
  const group = named(app.root, "fieldset", "spi-max-frequency"); change(field(group, "Value"), "4000000");
  await click(app.root, "Place module in draft"); assert.equal(app.commands[0].address, 1);
  assert.deepEqual(app.commands[0].values, {"spi-max-frequency": 4000000}); app.dispose();
});

test("GPIO recipe collects provider cells without assigning implicit pins or polarity", async () => {
  const app = harness({mount: true}); await placement(app, "GPIO indicator", "node:/leds");
  change(field(app.root, "Provider"), "&gpio0"); change(field(app.root, "pin"), "12"); change(field(app.root, "flags"), "1");
  await click(app.root, "Place module in draft");
  assert.deepEqual(app.commands[0].values, {gpios: [{reference: "&gpio0", cells: [12, 1]}]});
  assert.equal(app.commands[0].address, null); assert.equal(app.commands.length, 1); app.dispose();
});

test("existing source modules have guarded update and leaf-only removal forms", async () => {
  const app = harness({mount: true}); await click(app.root, "Load modules"); await click(app.root, "sensor@76");
  assert.match(app.root.textContent, /handwritten declarations/); change(field(app.root, "I2C address"), "0x77");
  await click(app.root, "Apply module properties"); assert.deepEqual(app.commands[0], {kind: "module_update", node_id: "sensor", address: 0x77, values: {}, schema_id: "schema0"});
  await click(app.root, "Remove this module from draft"); assert.deepEqual(app.commands[1], {kind: "module_remove", node_id: "sensor", schema_id: "schema1"}); app.dispose();
});

test("selecting and arranging graph cards changes layout only and saves separately", async () => {
  const app = harness({mount: true}); await click(app.root, "Load modules"); const card = await click(app.root, "sensor@76");
  const before = app.state.draftView.entry.text; const currentColumn = card.parentNode.style.gridColumn;
  await click(app.root, "Move right"); assert.equal(Number(card.parentNode.style.gridColumn), Number(currentColumn) + 1);
  await click(app.root, "Save layout"); assert.equal(app.commands.length, 0); assert.equal(app.state.draftView.entry.text, before);
  assert.match(app.state.moduleLayoutView.message, /saved separately/);
  app.panel.navigate(); await click(app.root, "Load modules"); const reopened = named(app.root, "button", "sensor@76");
  assert.equal(reopened.parentNode.style.gridColumn, card.parentNode.style.gridColumn); assert.equal(app.reveals.length, 0); app.dispose();
});

test("pending refresh retains graph cards and focused form controls without scroll requests", async () => {
  const app = harness({mount: true}); await placement(app, "I2C sensor", i2cKey);
  const address = field(app.root, "I2C address"); address.value = "0x77"; address.focus();
  const card = named(app.root, "button", "sensor@76"); app.type(source + "/* pending edit */");
  assert.equal(field(app.root, "I2C address"), address); assert.equal(app.root.ownerDocument.activeElement, address);
  assert.equal(address.disabled, true); assert.equal(named(app.root, "button", "sensor@76"), card);
  assert.equal(named(app.root, "button", "Place module in draft").disabled, true); assert.equal(app.reveals.length, 0); app.dispose();
});

test("placement refresh selects the created source node and keeps operation feedback beside its action", async () => {
  const app = harness({mount: true}); const command = app.drafts.command;
  app.drafts.command = async (value) => {
    const instance = {...structuredClone(app.model.instances[0]), key: "node:/i2c/sensor@77", node_id: "new-sensor", path: "/i2c/sensor@77", name: "sensor@77", address: 0x77};
    app.model.instances.push(instance); app.model.graph.nodes.push({...instance, role: "module"}); app.model.parents[0].children.push(instance);
    return command(value);
  };
  await placement(app, "I2C sensor", i2cKey); change(field(app.root, "I2C address"), "0x77");
  await click(app.root, "Place module in draft");
  assert.equal(named(app.root, "button", "sensor@77").attributes["aria-pressed"], "true");
  assert.equal(app.root.ownerDocument.activeElement, named(app.root, "button", "Apply module properties"));
  assert.deepEqual(app.root.ownerDocument.activeElement.focusOptions, {preventScroll: true});
  const result = Array.from(named(app.root, "button", "Apply module properties").parentNode.querySelectorAll("p")).find((node) => node.attributes.role === "status");
  assert.match(result.textContent, /Draft updated/); assert.equal(app.reveals.length, 0); app.dispose();
});

test("address edits follow the renamed canonical path and keep selection after refresh", async () => {
  const app = harness({mount: true}); const command = app.drafts.command;
  app.drafts.command = async (value) => {
    for (const instance of [app.model.instances[0], app.model.graph.nodes.find((item) => item.key === sensorKey)]) {
      Object.assign(instance, {key: "node:/i2c/sensor@77", path: "/i2c/sensor@77", name: "sensor@77", address: 0x77});
    }
    return command(value);
  };
  await click(app.root, "Load modules"); await click(app.root, "sensor@76"); change(field(app.root, "I2C address"), "0x77");
  await click(app.root, "Apply module properties");
  assert.equal(named(app.root, "button", "sensor@77").attributes["aria-pressed"], "true");
  assert.equal(field(app.root, "I2C address").value, "119"); assert.equal(app.root.ownerDocument.activeElement, named(app.root, "button", "Apply module properties")); app.dispose();
});

test("fresh metadata preserves staged placement parameters and source coverage disclosure state", async () => {
  const app = harness({mount: true}); await placement(app, "GPIO indicator", "node:/leds");
  change(field(app.root, "Provider"), "&gpio0"); change(field(app.root, "pin"), "12"); change(field(app.root, "flags"), "1");
  change(field(app.root, "Node name"), "indicator_two"); const disclosure = app.root.querySelectorAll("details")[0]; disclosure.open = true;
  app.model.recipes[0].description = "Updated local recipe description";
  await click(app.root, "Load modules");
  assert.equal(field(app.root, "Module recipe").value, "GPIO indicator"); assert.equal(field(app.root, "Connect to bus or GPIO group").value, "node:/leds");
  assert.equal(field(app.root, "Node name").value, "indicator_two"); assert.equal(field(app.root, "Provider").value, "&gpio0");
  assert.equal(field(app.root, "pin").value, "12"); assert.equal(field(app.root, "flags").value, "1");
  assert.equal(app.root.querySelectorAll("details")[0], disclosure); assert.equal(disclosure.open, true); app.dispose();
});

test("a completed placement cannot steal focus after the user moves to another control", async () => {
  const wait = deferred(); const app = harness({mount: true}); const command = app.drafts.command;
  app.drafts.command = async (value) => {
    await wait.promise;
    const instance = {...structuredClone(app.model.instances[0]), key: "node:/i2c/sensor@77", node_id: "new-sensor", path: "/i2c/sensor@77", name: "sensor@77", address: 0x77};
    app.model.instances.push(instance); app.model.graph.nodes.push({...instance, role: "module"}); return command(value);
  };
  await placement(app, "I2C sensor", i2cKey); change(field(app.root, "I2C address"), "0x77");
  await click(app.root, "Place module in draft"); const elsewhere = new Element("input", app.root.ownerDocument); elsewhere.focus();
  wait.resolve(); await settle(); assert.equal(app.root.ownerDocument.activeElement, elsewhere); assert.equal(app.reveals.length, 0); app.dispose();
});

test("reconciliation, reload and explicit selection clear obsolete inline module feedback", async () => {
  const app = harness({mount: true}); const duplicate = "Another device already uses this address.";
  const spi = {...structuredClone(app.model.instances[0]), key: "node:/spi/device@0", node_id: "spi-device", parent_id: "spi", parent_key: "node:/spi",
    kind: "spi", name: "device@0", path: "/spi/device@0", address: 0};
  app.model.instances.push(spi); app.model.graph.nodes.push({...spi, role: "module"}); app.model.parents[1].children.push(spi);
  app.drafts.command = async () => { app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = duplicate; return false; };
  await placement(app, "I2C sensor", i2cKey); change(field(app.root, "I2C address"), "0x76");
  await click(app.root, "Place module in draft");
  const inline = () => Array.from(app.root.querySelectorAll("p")).filter((node) => node.className?.includes("dte-module-action-status"));
  assert.ok(inline().some((node) => node.textContent === duplicate));
  await click(app.root, "Reconcile module draft"); assert.ok(inline().every((node) => node.textContent === ""));
  await click(app.root, "Load modules"); await click(app.root, "device@0");
  assert.ok(inline().every((node) => node.textContent === "")); assert.doesNotMatch(app.root.textContent, /Another device already uses/);
  change(field(app.root, "SPI chip select"), "1"); await click(app.root, "Apply module properties");
  assert.ok(inline().some((node) => node.textContent === duplicate));
  await click(app.root, "sensor@76"); assert.ok(inline().every((node) => node.textContent === "")); app.dispose();
});

test("unavailable recipes and empty graphs show an actionable non-mutating state", async () => {
  const app = harness({mount: true}); app.model.graph = {nodes: [], edges: []}; app.model.recipes[0].available = false;
  app.model.recipes[0].reason = "Declare a local bus first."; app.model.recipes[0].parents = [];
  await click(app.root, "Load modules"); change(field(app.root, "Module recipe"), "I2C sensor");
  assert.equal(named(app.root, "button", "Place module in draft").disabled, true);
  assert.match(app.root.textContent, /Declare a local bus first/); assert.match(app.root.textContent, /No supported local buses/);
  assert.equal(app.commands.length, 0); app.dispose();
});
