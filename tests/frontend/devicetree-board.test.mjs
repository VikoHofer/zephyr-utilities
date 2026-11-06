import test from "node:test";
import assert from "node:assert/strict";
import {boardConnection, boardConnections, boardOption, createBoardSession} from "../../src/zephyr_utilities/features/devicetree/web/board.js";
import {mountBoardPanel} from "../../src/zephyr_utilities/features/devicetree/web/board-view.js";

const source = "/ { };\n";
const i2cId = "hardware:/soc/i2c@40005400"; const gpioId = "hardware:/soc/gpio@48000000";
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const settle = () => new Promise((resolve) => setImmediate(resolve));
function fixture() {
  const controller = {id: i2cId, key: i2cId, name: "i2c@40005400", labels: ["i2c1"], path: "/soc/i2c@40005400", kind: "i2c", status: "disabled",
    origin: "board", supported: true, children: [i2cId + "/sensor@76"], pinctrl: [{name: "default", targets: ["hardware:/pinctrl/i2c1_default"]}]};
  const gpio = {id: gpioId, key: gpioId, name: "gpio@48000000", labels: ["gpioa"], path: "/soc/gpio@48000000", status: "okay", origin: "board",
    supported: true, cells: ["pin", "flags"], pin_count: 16, pin_count_origin: "binding-default", children: []};
  const child = {id: i2cId + "/sensor@76", name: "sensor@76", path: "/soc/i2c@40005400/sensor@76", parent_id: i2cId,
    compatible: ["bosch,bme280"], status: "okay", origin: "board", children: []};
  const led = {id: "hardware:/leds/status", name: "status-led", path: "/leds/status", parent_id: "hardware:/leds", status: "okay", origin: "board", children: []};
  return {schema_version: 1, target: {requested: "nucleo_f446re"}, board: {name: "nucleo_f446re", full_name: "Nucleo F446RE", vendor: "st"},
    supported: true, reason: "", coverage: "board+selected-overlay", controllers: [controller], gpio_providers: [gpio],
    nodes: [controller, gpio, child, led, {id: "hardware:/pinctrl/i2c1_default", path: "/pinctrl/i2c1_default", name: "i2c1_default"}],
    pinctrl_states: [], connector_maps: [{id: "hardware:/arduino-header", path: "/arduino-header", entries: [{child_cells: [0, 0], provider_id: gpioId, parent_cells: [3, 0]}]}],
    allocations: [{provider_id: gpioId, owner_id: led.id, pin: 5, kind: "gpio", property: "gpios"}], diagnostics: [], fingerprint: "fingerprint0",
    connection_options: {[i2cId]: [{kind: "i2c", compatible: "bosch,bme280", addable: true, reason: "", address_min: 8, address_max: 119, occupied_addresses: [0x76], enable_required: true}],
      [gpioId]: [{kind: "gpio-led", addable: true, reason: "", pin_min: 0, pin_max: 15, occupied_pins: [5, 8, 9], reserved_ranges: [[15, 1]], flags_min: 0, flags_max: 65535}]}};
}
function harness({mount = false, wrap = (api) => api} = {}) {
  const state = {data: {workspace: "/workspace", id: "context", revision: 1, target: {requested: "nucleo_f446re"}}, draftView: {ready: true,
    entry: {generation: 0, text: source, path: "/workspace/app/app.overlay",
      server: {id: "draft", document_id: "document", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel; let projects = 0;
  const calls = []; const commands = []; const reveals = []; const toasts = []; const model = fixture();
  const response = () => ({...structuredClone(model), observation_id: "observation" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const publish = () => { session?.sync(); panel?.render(); };
  const api = wrap(async (url, options) => { calls.push({url, ...options}); return response(); });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(structuredClone(command)); const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      const connection = command.connection; const controller = model.nodes.find((node) => node.id === connection.controller_id);
      const path = connection.kind === "i2c" ? controller.path + "/" + connection.name + "@" + connection.address.toString(16) : "/app-leds/" + connection.name;
      const device = {id: "hardware:" + path, path, name: path.split("/").at(-1), status: "okay", origin: "overlay", children: [],
        parent_id: connection.kind === "i2c" ? controller.id : "hardware:/app-leds"};
      model.nodes.push(device);
      if (connection.kind === "i2c") { controller.children.push(device.id); if (connection.enable) controller.status = "okay"; model.connection_options[controller.id][0].occupied_addresses.push(connection.address); }
      else { model.allocations.push({kind: "gpio", owner_id: device.id, provider_id: controller.id, pin: connection.pin}); model.connection_options[controller.id][0].occupied_pins.push(connection.pin); }
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"};
      entry.text = entry.server.text; publish(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; publish(); return true; }};
  const options = {api, state, workspace: () => workspace, drafts, onChange: publish}; const root = mount ? new Element("section") : null;
  if (mount) panel = mountBoardPanel(root, {...options, onOpenProjects: () => { projects++; }, notify: (message, error) => toasts.push({message, error}), reveal: (...args) => reveals.push(args)});
  else session = createBoardSession(options);
  return {state, options, root, panel, session, model, calls, commands, response, drafts, reveals, toasts, projects: () => projects,
    workspace(value) { workspace = value; session?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; publish(); },
    dispose() { session?.dispose(); panel?.dispose(); }};
}

// Browser-like DOM uses iterable NodeLists and delegated events to exercise panel behavior.
class Element {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = document || {activeElement: null, defaultView: {innerHeight: 800, innerWidth: 600}, createElement: (name) => new Element(name, this.ownerDocument)};
    if (!document) this.ownerDocument.root = this;
    this.children = []; this.parentNode = null; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this._text = ""; this._value = null; this.disabled = false; this.hidden = false; this.type = ""; this.scrollTop = 0;
  }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set value(value) { this._value = String(value); }
  get value() { return this._value ?? (this.tagName === "SELECT" ? this.children[0]?.value || "" : ""); }
  append(...children) { for (const child of children) { child.remove(); child.parentNode = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this); this.parentNode = null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getBoundingClientRect() {
    return this.className?.includes("dte-board-detail") && this.ownerDocument.detailBounds
      || this.className?.includes("dte-board-workspace") && this.ownerDocument.workspaceBounds
      || this.attributes.role === "status" && this.ownerDocument.feedbackBounds
      || {top: 20, bottom: 40, left: 0, right: 400};
  }
  focus(options) { if (!this.disabled && !this.hidden) { this.ownerDocument.activeElement = this; this.focusOptions = options; } }
  addEventListener(type, callback, options) { (this.listeners[type] ||= []).push(callback); options?.signal?.addEventListener("abort", () => { this.listeners[type] = this.listeners[type].filter((item) => item !== callback); }); }
  matches(selector) { return selector === "[hidden]" ? this.hidden : this.tagName.toLowerCase() === selector; }
  querySelectorAll(selector) {
    const selectors = selector.split(",").map((value) => value.trim());
    const nodes = this.children.flatMap((child) => [...(selectors.some((value) => child.matches(value)) ? [child] : []), ...child.querySelectorAll(selector)]);
    return Object.assign({length: nodes.length, [Symbol.iterator]: () => nodes[Symbol.iterator]()}, nodes);
  }
  closest(selector) { for (let item = this; item; item = item.parentNode) if (item.matches(selector)) return item; return null; }
  dispatch(type) { const event = {target: this, cancelBubble: false, preventDefault() {}}; for (let item = this; item; item = item.parentNode) for (const callback of item.listeners[type] || []) callback(event); }
}
const named = (root, tag, text) => Array.from(root.querySelectorAll(tag)).find((node) => node.textContent.startsWith(text));
const field = (root, text) => named(root, "label", text).children[0];
async function click(root, text) {
  const node = named(root, "button", text); assert.ok(node, text); assert.equal(node.disabled, false, text + " enabled");
  node.focus(); node.dispatch("click"); if (node.type === "submit") node.closest("form").dispatch("submit"); await settle(); return node;
}
function change(node, value) { node.value = value; node.dispatch("change"); }

async function choose(app, kind = "i2c") {
  await click(app.root, "Load board hardware"); await click(app.root, kind === "i2c" ? "Add BME280 sensor" : "Add LED");
  return click(app.root, kind === "i2c" ? "i2c1" : "gpioa");
}
function enable(root) { const node = field(root, "Enable this I2C controller"); node.checked = true; node.dispatch("change"); }
const identity = {name: "device", label: ""};

test("board discovery is explicit and adapts observation identity without changing shared draft guards", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); assert.equal(await app.session.load(), true);
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/board");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.boardView.data.observation_id, "observation0"); assert.equal(app.state.draftView.entry.text, source); app.dispose();
});

test("board commands carry observation_id and never send schema_id to the backend", async () => {
  const app = harness(); await app.session.load();
  const connection = boardConnection(app.state.boardView.data, i2cId, "i2c", {...identity, address: "0x77", enable: true});
  assert.equal(await app.session.command({kind: "board_module_add", connection}), true);
  assert.deepEqual(app.commands[0], {kind: "board_module_add", observation_id: "observation0", connection});
  assert.equal(Object.hasOwn(app.commands[0], "schema_id"), false); assert.equal(app.state.boardView.data.observation_id, "observation1"); app.dispose();
});

test("missing board observation identity or connection capability data never authorizes edits", async () => {
  for (const key of ["observation_id", "connection_options", "board", "gpio_providers"]) {
    const app = harness({wrap: (api) => async (...args) => { const data = await api(...args); delete data[key]; return data; }});
    assert.equal(await app.session.load(), false, key); assert.equal(app.state.boardView.data, null); assert.match(app.state.boardView.error, /does not match/); app.dispose();
  }
});

test("board connection keys are stable and device options come only from declared capabilities", () => {
  const data = fixture(); data.controllers.push(data.gpio_providers[0]);
  assert.equal(boardConnections(data).length, 2); assert.deepEqual(boardConnections(data, "i2c").map((node) => node.id), [i2cId]);
  assert.equal(boardOption(data, i2cId, "gpio-led"), undefined);
  assert.throws(() => boardConnection(data, i2cId, "gpio-led", {...identity, pin: "1", flags: "0"}), /available connection/);
  data.connection_options[i2cId][0].addable = false; data.connection_options[i2cId][0].reason = "No reusable default pin routing.";
  assert.throws(() => boardConnection(data, i2cId, "i2c", {...identity, address: "119", enable: true}), /No reusable default pin routing/);
});

test("I2C placement validates address, existing use and explicit controller enable locally", () => {
  const data = fixture();
  assert.deepEqual(boardConnection(data, i2cId, "i2c", {...identity, address: "0x77", enable: true}),
    {kind: "i2c", controller_id: i2cId, compatible: "bosch,bme280", address: 119, ...identity, enable: true});
  assert.throws(() => boardConnection(data, i2cId, "i2c", {...identity, address: "0x76", enable: true}), /already used/);
  assert.throws(() => boardConnection(data, i2cId, "i2c", {...identity, address: "119", enable: false}), /Enable this I2C controller/);
  for (const address of ["", "0", "128", "ADDR", "118 119"]) assert.throws(() => boardConnection(data, i2cId, "i2c", {...identity, address, enable: true}), /I2C address/);
  data.connection_options[i2cId][0].enable_required = false;
  assert.equal(boardConnection(data, i2cId, "i2c", {...identity, address: "119", enable: false}).enable, false);
});

test("GPIO placement validates known bounds, reservations and observed allocations without guessing polarity", () => {
  const data = fixture();
  assert.deepEqual(boardConnection(data, gpioId, "gpio-led", {...identity, pin: "0", flags: "0"}), {kind: "gpio-led", controller_id: gpioId, pin: 0, flags: 0, ...identity});
  for (const pin of ["5", "8", "9"]) assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...identity, pin, flags: "1"}), /already used/);
  assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...identity, pin: "15", flags: "1"}), /reserved/);
  for (const pin of ["", "16", "-1", "PIN", "1 2"]) assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...identity, pin, flags: "1"}), /GPIO pin/);
  for (const flags of ["", "65536", "-1", "GPIO_ACTIVE_LOW"]) assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...identity, pin: "3", flags}), /GPIO flags/);
  data.connection_options[gpioId][0].pin_max = null;
  assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...identity, pin: "3", flags: "1"}), /range is unknown/);
});

test("a board card and Projects action remain visible before hardware or target selection", async () => {
  const app = harness({mount: true}); assert.match(app.root.textContent, /nucleo_f446re/); assert.equal(app.calls.length, 0);
  app.state.data.target = null; app.panel.render(); assert.match(app.root.textContent, /Choose your board/);
  assert.equal(named(app.root, "button", "Load board hardware").disabled, true); await click(app.root, "Open Projects"); assert.equal(app.projects(), 1);
  assert.equal(app.calls.length, 0); app.dispose();
});

test("unsupported discovery keeps the board and its reason instead of an empty workspace", async () => {
  const app = harness({mount: true}); Object.assign(app.model, {supported: false, reason: "This board's source is unavailable in the selected checkout.", nodes: [], controllers: [], gpio_providers: [], connection_options: {}});
  await click(app.root, "Load board hardware"); assert.match(app.root.textContent, /Nucleo F446RE/); assert.match(app.root.textContent, /source is unavailable/);
  assert.equal(named(app.root, "button", "Add BME280 sensor").disabled, true); assert.equal(app.commands.length, 0); app.dispose();
});

test("the primary connection canvas shows bus children and GPIO consumers as nested device cards", async () => {
  const app = harness({mount: true}); await click(app.root, "Load board hardware");
  const busCard = named(app.root, "button", "i2c1").parentNode; const gpioCard = named(app.root, "button", "gpioa").parentNode;
  assert.match(busCard.textContent, /sensor@76/); assert.match(gpioCard.textContent, /status-led/);
  assert.equal(busCard.querySelectorAll("ul").length, 1); assert.equal(gpioCard.querySelectorAll("ul").length, 1);
  assert.equal(app.commands.length, 0); app.dispose();
});

test("BME280 placement requires explicit address and enable, preserves board routing and follows the refreshed child", async () => {
  const app = harness({mount: true}); await choose(app); const form = named(app.root, "form", "Connect BME280 sensor");
  assert.equal(field(form, "I2C address").value, ""); assert.equal(field(form, "Enable this I2C controller").checked, false);
  assert.match(app.root.textContent, /Existing board pin routing/); assert.match(app.root.textContent, /\/pinctrl\/i2c1_default/);
  change(field(form, "I2C address"), "119"); await click(app.root, "Add BME280 sensor to draft");
  assert.equal(app.commands.length, 0); assert.match(form.textContent, /Select Enable this I2C controller/);
  enable(form); await click(app.root, "Add BME280 sensor to draft");
  assert.deepEqual(app.commands[0], {kind: "board_module_add", observation_id: "observation0", connection:
    {kind: "i2c", controller_id: i2cId, compatible: "bosch,bme280", address: 119, name: "bme280", label: "", enable: true}});
  assert.match(named(app.root, "button", "i2c1").parentNode.textContent, /bme280@77/);
  assert.equal(app.root.ownerDocument.activeElement.textContent, "Add BME280 sensor to draft");
  assert.deepEqual(app.root.ownerDocument.activeElement.focusOptions, {preventScroll: true}); assert.deepEqual(app.reveals, []); app.dispose();
});

test("LED placement offers mapped GPIO choices first and requires an explicit active level", async () => {
  const app = harness({mount: true}); await choose(app, "gpio-led"); const form = named(app.root, "form", "Connect LED");
  const pin = field(form, "GPIO pin"); const polarity = field(form, "LED active level");
  assert.equal(pin.value, ""); assert.equal(polarity.value, ""); assert.match(pin.children[1].textContent, /GPIO pin 3.*arduino-header entry 0, 0/);
  assert.equal(pin.children.find((node) => node.value === "5").disabled, true); assert.equal(pin.children.find((node) => node.value === "15").disabled, true);
  assert.match(form.textContent, /not physical board header positions/);
  change(pin, "3"); await click(app.root, "Add LED to draft"); assert.equal(app.commands.length, 0);
  change(polarity, "0"); await click(app.root, "Add LED to draft");
  assert.deepEqual(app.commands[0].connection, {kind: "gpio-led", controller_id: gpioId, pin: 3, flags: 0, name: "led", label: ""});
  assert.match(named(app.root, "button", "gpioa").parentNode.textContent, /led/); assert.deepEqual(app.reveals, []); app.dispose();
});

test("unavailable connections show the backend reason without a misleading creation form", async () => {
  const app = harness({mount: true}); app.model.connection_options[i2cId][0].addable = false;
  app.model.connection_options[i2cId][0].reason = "The controller has no verified default routing."; await choose(app);
  assert.match(app.root.textContent, /no verified default routing/); assert.equal(app.root.querySelectorAll("form").length, 0);
  assert.equal(app.commands.length, 0); app.dispose();
});

test("board discovery exposes busy and cancel without publishing a late result", async () => {
  const wait = deferred(); let signal;
  const app = harness({mount: true, wrap: (api) => async (...args) => { signal = args[1].signal; const result = await api(...args); await wait.promise; return result; }});
  await click(app.root, "Load board hardware"); assert.ok(named(app.root, "button", "Loading board hardware…")); assert.match(app.root.textContent, /may take a few seconds/);
  await click(app.root, "Cancel discovery"); wait.resolve(); await settle();
  assert.equal(signal.aborted, true); assert.equal(app.state.boardView.data, null); assert.match(app.root.textContent, /discovery cancelled/); assert.deepEqual(app.toasts, []); app.dispose();
});

test("late board discovery cannot cross source, context, workspace, navigation or disposal", async () => {
  for (const action of ["source", "context", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); await wait.promise; return result; }});
    await click(app.root, "Load board hardware");
    if (action === "source") app.type(source + "/* pending */");
    if (action === "context") app.state.data.revision++;
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.panel.navigate();
    if (action === "dispose") app.dispose();
    wait.resolve(); await settle(); assert.equal(app.state.boardView.data, null, action); assert.deepEqual(app.toasts, [], action); app.dispose();
  }
});

test("staged device settings and focus survive passive refresh while source typing pauses creation", async () => {
  const app = harness({mount: true}); await choose(app); const form = named(app.root, "form", "Connect BME280 sensor");
  const address = field(form, "I2C address"); change(address, "119"); enable(form); address.focus(); app.root.scrollTop = 650;
  app.panel.render(); app.panel.render(); assert.equal(named(app.root, "form", "Connect BME280 sensor"), form);
  assert.equal(address.value, "119"); assert.equal(app.root.ownerDocument.activeElement, address); assert.equal(app.root.scrollTop, 650);
  app.type(source + "/* pending */"); assert.equal(address.disabled, true); assert.equal(field(form, "Enable this I2C controller").checked, true);
  assert.deepEqual(app.reveals, []); assert.deepEqual(app.toasts, []); app.dispose();
});

test("connection selection uses offscreen notification instead of scrolling or moving focus", async () => {
  const app = harness({mount: true}); await click(app.root, "Load board hardware"); await click(app.root, "Add BME280 sensor");
  app.root.ownerDocument.detailBounds = {top: 900, bottom: 1300, left: 0, right: 400}; app.root.scrollTop = 650;
  const trigger = await click(app.root, "i2c1"); assert.equal(app.toasts.length, 1); assert.match(app.toasts[0].message, /Complete BME280 sensor settings for i2c1/);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.root.scrollTop, 650); assert.deepEqual(app.reveals, []);
  app.panel.render(); app.panel.render(); assert.equal(app.toasts.length, 1); app.dispose();
});

test("repeated recipe and connection choices announce the next step while retaining staged inputs", async () => {
  const app = harness({mount: true}); await click(app.root, "Load board hardware");
  app.root.ownerDocument.detailBounds = {top: 900, bottom: 1300, left: 0, right: 400}; app.root.scrollTop = 650;
  await click(app.root, "Add BME280 sensor"); const count = app.toasts.length;
  await click(app.root, "Add BME280 sensor"); assert.equal(app.toasts.length, count + 1);
  assert.match(app.toasts.at(-1).message, /Choose a connection for the BME280 sensor/);
  await click(app.root, "i2c1"); const form = named(app.root, "form", "Connect BME280 sensor");
  change(field(form, "I2C address"), "119"); enable(form); change(field(form, "Node label (optional)"), "my_sensor");
  for (const title of ["Add BME280 sensor", "i2c1"]) {
    const before = app.toasts.length; const trigger = await click(app.root, title);
    assert.equal(app.toasts.length, before + 1); assert.match(app.toasts.at(-1).message, /Complete BME280 sensor settings for i2c1/);
    assert.equal(named(app.root, "form", "Connect BME280 sensor"), form);
    assert.equal(field(form, "I2C address").value, "119"); assert.equal(field(form, "Enable this I2C controller").checked, true);
    assert.equal(field(form, "Node label (optional)").value, "my_sensor"); assert.equal(app.root.ownerDocument.activeElement, trigger);
  }
  const countAfter = app.toasts.length; app.panel.render(); assert.equal(app.toasts.length, countAfter);
  assert.equal(app.root.scrollTop, 650); assert.deepEqual(app.reveals, []); assert.equal(app.commands.length, 0); app.dispose();
});

test("All hardware restores inspectable controllers outside the selected recipe and stays explicit", async () => {
  const app = harness({mount: true});
  const spi = {id: "hardware:/soc/spi@1234", name: "spi@1234", labels: ["spi1"], path: "/soc/spi@1234", kind: "spi", status: "okay", children: []};
  app.model.controllers.push(spi); app.model.nodes.push(spi);
  await choose(app, "gpio-led"); assert.equal(named(app.root, "button", "spi1"), undefined); assert.equal(named(app.root, "button", "i2c1"), undefined);
  app.root.ownerDocument.workspaceBounds = {top: 900, bottom: 1300, left: 0, right: 400}; app.root.scrollTop = 650;
  const trigger = await click(app.root, "All hardware");
  assert.ok(named(app.root, "button", "spi1")); assert.ok(named(app.root, "button", "i2c1")); assert.ok(named(app.root, "button", "gpioa"));
  assert.equal(trigger.attributes["aria-pressed"], "true"); assert.equal(named(app.root, "button", "Add LED").attributes["aria-pressed"], "false");
  assert.equal(app.root.querySelectorAll("form").length, 0); assert.equal(app.root.ownerDocument.activeElement, trigger);
  assert.match(app.toasts.at(-1).message, /All board hardware connections/);
  const count = app.toasts.length; await click(app.root, "All hardware"); assert.equal(app.toasts.length, count + 1);
  await click(app.root, "spi1"); assert.match(app.root.textContent, /Connection details for spi1 are ready/);
  app.panel.render(); assert.equal(app.toasts.length, count + 1);
  assert.equal(app.root.scrollTop, 650); assert.deepEqual(app.reveals, []); assert.equal(app.commands.length, 0); app.dispose();
});

test("occupied-pin errors stay local and announce offscreen feedback without creating uncertain draft state", async () => {
  const app = harness({mount: true}); await choose(app, "gpio-led"); const form = named(app.root, "form", "Connect LED");
  change(field(form, "GPIO pin"), "5"); change(field(form, "LED active level"), "1");
  app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400}; const trigger = await click(app.root, "Add LED to draft");
  assert.equal(app.commands.length, 0); assert.equal(app.state.draftView.entry.uncertain, undefined); assert.match(app.toasts[0].message, /already used/);
  assert.equal(app.toasts[0].error, true); assert.equal(app.root.ownerDocument.activeElement, trigger); assert.deepEqual(app.reveals, []);
  app.panel.render(); assert.equal(app.toasts.length, 1); app.dispose();
});

test("async board action feedback is withheld after source, context, workspace, navigation or disposal changes", async () => {
  for (const action of ["current", "source", "context", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); let reads = 0;
    const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
    await choose(app); const form = named(app.root, "form", "Connect BME280 sensor"); change(field(form, "I2C address"), "119"); enable(form);
    app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400}; await click(app.root, "Add BME280 sensor to draft");
    assert.equal(app.toasts.length, 0); assert.equal(named(app.root, "form", "Connect BME280 sensor"), form);
    if (action === "source") app.type(source + "/* pending */");
    if (action === "context") app.state.data.revision++;
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.panel.navigate();
    if (action === "dispose") app.dispose();
    wait.resolve(); await settle(); assert.equal(app.toasts.length, action === "current" ? 1 : 0, action); assert.deepEqual(app.reveals, []); app.dispose();
  }
});

test("failed board discovery gives a visible error and retry leaves the board card intact", async () => {
  let fail = true; const app = harness({mount: true, wrap: (api) => async (...args) => { if (fail) throw new Error("Board reader failed."); return api(...args); }});
  await click(app.root, "Load board hardware"); assert.match(app.root.textContent, /Board reader failed/); assert.match(app.root.textContent, /nucleo_f446re/);
  fail = false; await click(app.root, "Load board hardware"); assert.match(app.root.textContent, /Nucleo F446RE/); assert.ok(named(app.root, "button", "i2c1")); app.dispose();
});

test("uncertain board creation requires reconciliation and never replays the command", async () => {
  const app = harness(); await app.session.load(); let attempts = 0;
  app.drafts.command = async () => { attempts++; app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Reply lost."; return false; };
  assert.equal(await app.session.command({kind: "board_module_add", connection: {kind: "i2c"}}), false);
  assert.match(app.state.boardView.error, /Reply lost/); assert.equal(await app.session.reconcile(), true); assert.equal(attempts, 1);
  assert.equal(app.state.boardView.data, null); assert.match(app.state.boardView.message, /Load board hardware again/); app.dispose();
});

test("imported board descriptions stay text and disposed controls cannot request more discovery", async () => {
  const app = harness({mount: true}); app.model.board.full_name = '<img src=x onerror="bad()">'; app.model.diagnostics = ["<script>bad()</script>"];
  await click(app.root, "Load board hardware"); assert.match(app.root.textContent, /<img src=x/); assert.equal(app.root.querySelectorAll("img, script").length, 0);
  const count = app.calls.length; app.dispose(); named(app.root, "button", "Load board hardware").dispatch("click"); await settle(); assert.equal(app.calls.length, count);
});

test("board device names match backend syntax and reject existing board labels or device paths locally", () => {
  const data = fixture(); const values = {...identity, pin: "3", flags: "0"};
  assert.equal(boardConnection(data, gpioId, "gpio-led", {...values, name: "_indicator"}).name, "_indicator");
  for (const name of ["with.dot", "with+plus", "with,comma", "2led", "x".repeat(65)]) assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...values, name}), /node name/);
  assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...values, label: "x".repeat(65)}), /node label/);
  assert.throws(() => boardConnection(data, gpioId, "gpio-led", {...values, label: "gpioa"}), /already belongs/);
  data.nodes.push({path: "/app-leds/device"}); assert.throws(() => boardConnection(data, gpioId, "gpio-led", values), /already used/);
});

test("LED placement offers a fresh node name after an existing app LED without choosing pin or active level", async () => {
  const app = harness({mount: true}); app.model.nodes.push({id: "hardware:/app-leds/led", path: "/app-leds/led", name: "led"});
  await choose(app, "gpio-led"); const form = named(app.root, "form", "Connect LED");
  assert.equal(field(form, "Node name").value, "led_2"); assert.equal(field(form, "GPIO pin").value, "");
  assert.equal(field(form, "LED active level").value, ""); assert.equal(app.commands.length, 0); app.dispose();
});
