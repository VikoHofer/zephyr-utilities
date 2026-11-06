import test from "node:test";
import assert from "node:assert/strict";
import {createPinSession, gpioKey, pinKey, selectedFlags, selectedPin} from "../../src/zephyr_utilities/features/devicetree/web/pins.js";
import {mountPinPanel} from "../../src/zephyr_utilities/features/devicetree/web/pin-view.js";

const source = '&uart0 { status = "okay"; };\n';
function fixture() {
  const signals = [{role: "UART_TX", port: 0, pin: 6, editable: true}];
  return {supported: true, target: "nrf52840dk/nrf52840", adapter: "nordic", diagnostics: [],
    pins: [{port: 0, pin: 6, name: "P0.06", connector: "Console TX", uses: ["uart0 default UART_TX", "uart0 sleep UART_TX"]},
      {port: 0, pin: 8, name: "P0.08", connector: null, uses: []},
      {port: 0, pin: 9, name: "P0.09", reserved: true, reason: "NFC reservation", uses: []}],
    controllers: [{id: "uart0", label: "uart0", kind: "uart", status: "okay", instance: "0x40002000", editable: true,
      states: ["default", "sleep"].map((name) => ({name, editable: true, shared: false, consumers: ["uart0"],
        roles: ["UART_TX", "UART_RX"], signals: structuredClone(signals)}))}],
    gpio: [{node_id: "led0", path: "/leds/led0", name: "led0", property: "gpios", index: 0, port: 0, pin: 8, flags: ["GPIO_ACTIVE_LOW"], editable: true}],
    gpio_flags: ["GPIO_ACTIVE_HIGH", "GPIO_ACTIVE_LOW", "GPIO_PULL_UP", "GPIO_PULL_DOWN"].map((name) => ({name, label: name}))};
}
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }
function harness(wrap = (api) => api, mount = false) {
  const state = {data: {workspace: "/workspace", id: "context", revision: 1}, draftView: {ready: true,
    entry: {generation: 0, text: source, server: {id: "draft", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel; let notifications = 0;
  const calls = []; const commands = []; const reveals = []; const model = fixture();
  const response = () => ({...structuredClone(model), schema_id: "schema" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const notify = () => { notifications++; session?.sync(); panel?.render(); };
  const api = wrap(async (url, options) => { calls.push({url, ...options}); return response(); });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(structuredClone(command)); const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"};
      entry.text = entry.server.text; notify(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; notify(); return true; }};
  const root = mount ? new Element("section") : null;
  const options = {api, state, drafts, workspace: () => workspace, onChange: notify};
  if (mount) panel = mountPinPanel(root, {...options, reveal: (node) => reveals.push(node)});
  else session = createPinSession(options);
  return {state, session, panel, root, drafts, calls, commands, model, response, reveals, options,
    notifications: () => notifications,
    workspace(value) { workspace = value; session?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; notify(); },
    dispose() { session?.dispose(); panel?.dispose(); }};
}

test("pin observation is explicit, revision guarded and never changes source", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); await app.session.load();
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/pins");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("pin commands use canonical draft history and fresh observations", async () => {
  const app = harness(); await app.session.load();
  assert.equal(await app.session.command({kind: "pin_assign", controller: "uart0", state: "default", role: "UART_RX", port: 0, pin: 8, split_shared: false}), true);
  assert.equal(await app.session.command({kind: "pin_unassign", controller: "uart0", state: "default", role: "UART_TX", split_shared: false}), true);
  assert.deepEqual(app.commands.map((item) => item.schema_id), ["schema0", "schema1"]);
  assert.equal(app.state.pinView.data.revision, 2); assert.equal(app.calls.length, 3); app.dispose();
});

test("unverifiable response shapes cannot enable pin forms", async () => {
  const app = harness((api) => async (...args) => { const result = await api(...args); delete result.gpio_flags; return result; });
  assert.equal(await app.session.load(), false); assert.match(app.state.pinView.error, /does not match/); assert.equal(app.state.pinView.data, null); app.dispose();
});

test("typing invalidates loaded pins and prevents a stale mutation", async () => {
  const app = harness(); await app.session.load(); app.type(source + "/* pending */");
  assert.equal(app.state.pinView.data, null);
  assert.equal(await app.session.command({kind: "pin_unassign", controller: "uart0", state: "default", role: "UART_TX", split_shared: false}), false);
  assert.equal(app.commands.length, 0); app.dispose();
});

test("stale observations retain presentation only within the same draft and context", async () => {
  const app = harness(); await app.session.load(); const observation = app.state.pinView.data;
  app.type(source + "/* pending */");
  assert.equal(app.state.pinView.presentation, observation); assert.equal(app.state.pinView.data, null);
  app.state.data.revision++; app.session.sync();
  assert.equal(app.state.pinView.presentation, observation);
  app.state.data.id = "another-context"; app.session.sync();
  assert.equal(app.state.pinView.presentation, null);
  app.dispose();
});

test("reviewed Save rebinds retain presentation while fresh context metadata is required for commands", async () => {
  const app = harness();
  Object.assign(app.state.data, {application: {path: "/workspace/app", identity: [1, 2]}, workspace_identity: [1, 1],
    project: {id: "project"}, configuration: {id: "configuration"}, target: {requested: "board"}});
  await app.session.load(); const observed = app.state.pinView.presentation;
  app.state.data = {...app.state.data, id: "saved-context", revision: 1};
  app.state.draftView.entry.server.revision++; app.state.draftView.entry.server.sha256 = "saved-hash";
  app.session.sync();
  assert.equal(app.state.pinView.data, null); assert.equal(app.state.pinView.presentation, observed);
  assert.equal(await app.session.command({kind: "pin_assign"}), false);
  assert.equal(app.commands.length, 0);
  app.state.data.target = {requested: "another-board"}; app.session.sync();
  assert.equal(app.state.pinView.presentation, null); app.dispose();
});

test("changing draft identity clears retained presentation even after source invalidation", async () => {
  const app = harness(); await app.session.load(); app.type(source + "/* pending */");
  app.state.draftView.entry.server.id = "another-draft"; app.session.sync();
  assert.equal(app.state.pinView.presentation, null); app.dispose();
});

test("a late pin inspection cannot cross a workspace change", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { const result = await api(...args); await wait.promise; return result; });
  const loading = app.session.load(); app.workspace("/other"); wait.resolve(); assert.equal(await loading, false);
  assert.equal(app.state.pinView.data, null); app.dispose();
});

test("cancellation aborts only the read request and retains source", async () => {
  const wait = deferred(); let signal;
  const app = harness((api) => async (...args) => { signal = args[1].signal; const result = await api(...args); await wait.promise; return result; });
  const loading = app.session.load(); app.session.cancel(); wait.resolve(); assert.equal(await loading, false);
  assert.equal(signal.aborted, true); assert.equal(app.state.draftView.entry.text, source); assert.match(app.state.pinView.message, /cancelled/); app.dispose();
});

test("rejected pin changes expose reconciliation without replaying the command", async () => {
  const app = harness(); await app.session.load();
  app.drafts.command = async () => { app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Pin conflicts with a GPIO consumer."; return false; };
  assert.equal(await app.session.command({kind: "pin_assign", controller: "uart0", state: "default", role: "UART_RX", port: 0, pin: 8, split_shared: false}), false);
  assert.match(app.state.pinView.error, /conflicts/); assert.equal(app.session.canReconcile(), true);
  assert.equal(await app.session.reconcile(), true); assert.match(app.state.pinView.message, /Load pin assignments again/);
  assert.equal(app.calls.length, 1); app.dispose();
});

test("disposal ignores late observation results", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { const result = await api(...args); await wait.promise; return result; });
  const loading = app.session.load(); const before = app.notifications(); app.dispose(); wait.resolve();
  assert.equal(await loading, false); assert.equal(app.notifications(), before); assert.equal(app.state.pinView.data, null);
});

test("pin selection only accepts established available pins", () => {
  const pins = fixture().pins; assert.deepEqual(selectedPin(pins, "0:8"), {port: 0, pin: 8});
  assert.deepEqual(selectedPin(pins, "0:9"), {port: 0, pin: 9});
  assert.throws(() => selectedPin(pins, "1:99"), /evidence/);
  assert.throws(() => selectedPin(pins, "NRF_PSEL(UART_TX, 0, 8)"), /evidence/);
});

test("GPIO flag selection rejects unknown expressions and contradictory choices", () => {
  const flags = fixture().gpio_flags;
  assert.deepEqual(selectedFlags(["GPIO_ACTIVE_LOW", "GPIO_PULL_UP"], flags), ["GPIO_ACTIVE_LOW", "GPIO_PULL_UP"]);
  assert.throws(() => selectedFlags([], flags), /explicitly/);
  assert.throws(() => selectedFlags(["1 | UNKNOWN"], flags), /constants/);
  assert.throws(() => selectedFlags(["GPIO_ACTIVE_LOW", "GPIO_ACTIVE_HIGH"], flags), /polarity/);
  assert.throws(() => selectedFlags(["GPIO_PULL_UP", "GPIO_PULL_DOWN"], flags), /pull direction/);
  assert.throws(() => selectedFlags(["GPIO_ACTIVE_LOW", "GPIO_ACTIVE_LOW"], flags), /distinct/);
});

// Event-bubbling DOM supports form interactions without duplicating browser layout checks.
class Element {
  constructor(tag, document) { this.tagName = tag.toUpperCase(); this.ownerDocument = document || {createElement: (name) => new Element(name, this.ownerDocument)};
    this.children = []; this.parentNode = null; this.dataset = {}; this.attributes = {}; this.listeners = {}; this._text = ""; this._value = null; this.type = ""; this.disabled = false; this.hidden = false; this.checked = false; }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set value(value) { this._value = String(value); }
  get value() { return this._value ?? (this.tagName === "SELECT" ? this.children[0]?.value || "" : ""); }
  append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  focus(options) { this.ownerDocument.activeElement = this; this.focusOptions = options; }
  addEventListener(type, callback, options) { (this.listeners[type] ||= []).push(callback); options?.signal?.addEventListener("abort", () => { this.listeners[type] = this.listeners[type].filter((item) => item !== callback); }); }
  matches(selector) { return this.tagName.toLowerCase() === selector; }
  querySelectorAll(selector) { const matches = selector.split(",").map((value) => value.trim()); return this.children.flatMap((child) => [...(matches.some((value) => child.matches(value)) ? [child] : []), ...child.querySelectorAll(selector)]); }
  closest(selector) { for (let item = this; item; item = item.parentNode) if (item.matches(selector)) return item; return null; }
  dispatch(type) { const event = {target: this, cancelBubble: false, preventDefault() {}}; for (let item = this; item; item = item.parentNode) for (const callback of item.listeners[type] || []) callback(event); }
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const named = (root, tag, text) => root.querySelectorAll(tag).find((node) => node.textContent.startsWith(text));
const field = (root, text) => named(root, "label", text).children[0];
function change(node, value) { node.value = value; node.dispatch("change"); }
async function click(root, text) {
  const node = named(root, "button", text); assert.ok(node, text); assert.equal(node.disabled, false, text + " enabled");
  node.dispatch("click"); if (node.type === "submit") node.closest("form").dispatch("submit"); await settle(); return node;
}
async function signal(app, role = "UART_TX", state = "default") {
  await click(app.root, "Load pin assignments"); change(field(app.root, "Controller"), "uart0");
  change(field(app.root, "Pin control state"), state); change(field(app.root, "Signal role"), role);
}

test("signal assignment requires explicit role and pin and sends symbolic identity", async () => {
  const app = harness(undefined, true); await signal(app, "UART_RX");
  assert.equal(field(app.root, "Signal pin").value, ""); await click(app.root, "Assign signal to draft");
  assert.equal(app.commands.length, 0); assert.match(app.state.pinView.error, /Choose a pin/);
  change(field(app.root, "Signal pin"), "0:8"); await click(app.root, "Assign signal to draft");
  assert.deepEqual(app.commands[0], {kind: "pin_assign", controller: "uart0", state: "default", role: "UART_RX", port: 0, pin: 8, split_shared: false, schema_id: "schema0"}); app.dispose();
});

test("reassignment and unassignment target only the selected state", async () => {
  const app = harness(undefined, true); await signal(app, "UART_TX", "sleep");
  assert.equal(field(app.root, "Signal pin").value, "0:6"); change(field(app.root, "Signal pin"), "0:8");
  await click(app.root, "Reassign signal in draft"); assert.equal(app.commands[0].state, "sleep");
  await click(app.root, "Unassign signal from draft");
  assert.deepEqual(app.commands[1], {kind: "pin_unassign", controller: "uart0", state: "sleep", role: "UART_TX", split_shared: false, schema_id: "schema1"}); app.dispose();
});

test("shared state changes require an explicit split before submitting", async () => {
  const app = harness(undefined, true); app.model.controllers[0].states[0].shared = true; app.model.controllers[0].states[0].consumers = ["uart0", "uart1"];
  await signal(app); assert.ok(app.root.textContent.includes("uart0, uart1"));
  assert.equal(named(app.root, "button", "Reassign signal in draft").disabled, true);
  assert.equal(named(app.root, "button", "Unassign signal from draft").disabled, true);
  const split = field(app.root, "Split this shared state"); split.checked = true; split.dispatch("change");
  await click(app.root, "Unassign signal from draft"); assert.equal(app.commands[0].split_shared, true);
  assert.equal(field(app.root, "Split this shared state").checked, false); app.dispose();
});

test("pin table fills a form without changing source and exposes reservations", async () => {
  const app = harness(undefined, true); await signal(app);
  assert.equal(named(app.root, "button", "P0.09").disabled, false); assert.ok(app.root.textContent.includes("NFC reservation"));
  await click(app.root, "P0.09"); assert.ok(named(app.root, "form", "Peripheral signal assignment").textContent.includes("Reserved: NFC reservation"));
  await click(app.root, "P0.08"); assert.equal(field(app.root, "Signal pin").value, "0:8");
  assert.equal(app.commands.length, 0); assert.equal(app.reveals.at(-1).tagName, "FORM"); app.dispose();
});

test("GPIO form preserves observed flags and writes only explicitly selected symbols", async () => {
  const app = harness(undefined, true); await click(app.root, "Load pin assignments");
  change(field(app.root, "GPIO consumer"), gpioKey(app.model.gpio[0]));
  assert.equal(field(app.root, "GPIO_ACTIVE_LOW").checked, true); assert.equal(field(app.root, "GPIO_PULL_UP").checked, false);
  field(app.root, "GPIO_PULL_UP").checked = true; await click(app.root, "Apply GPIO assignment");
  assert.deepEqual(app.commands[0], {kind: "pin_gpio", node_id: "led0", property: "gpios", index: 0, port: 0, pin: 8,
    flags: ["GPIO_ACTIVE_LOW", "GPIO_PULL_UP"], schema_id: "schema0"}); app.dispose();
});

test("GPIO unassign explains inherited suppression and uses the dedicated command", async () => {
  const app = harness(undefined, true); await click(app.root, "Load pin assignments"); change(field(app.root, "GPIO consumer"), gpioKey(app.model.gpio[0]));
  assert.ok(app.root.textContent.includes("inherited assignment cannot silently return"));
  await click(app.root, "Unassign GPIO property");
  assert.deepEqual(app.commands[0], {kind: "pin_gpio_unassign", node_id: "led0", property: "gpios", index: 0, schema_id: "schema0"}); app.dispose();
});

test("GPIO table selection respects the last selected form", async () => {
  const app = harness(undefined, true); await signal(app); change(field(app.root, "GPIO consumer"), gpioKey(app.model.gpio[0]));
  await click(app.root, "P0.06"); assert.equal(field(app.root, "GPIO pin").value, "0:6");
  assert.equal(field(app.root, "Signal pin").value, "0:6"); assert.equal(app.commands.length, 0); app.dispose();
});

test("GPIO array entries have distinct labels and removal targets the selected index", async () => {
  const app = harness(undefined, true); const first = app.model.gpio[0];
  app.model.gpio.push({...first, index: 1, port: 0, pin: 6});
  await click(app.root, "Load pin assignments"); const consumer = field(app.root, "GPIO consumer");
  assert.ok(consumer.children.some((item) => item.textContent.includes("entry 2")));
  change(consumer, gpioKey(app.model.gpio[1])); assert.ok(app.root.textContent.includes("remaining indices shift"));
  await click(app.root, "Unassign GPIO entry"); assert.equal(app.commands[0].index, 1); app.dispose();
});

test("chip-select entries can be reassigned but cannot silently shift device indices", async () => {
  const app = harness(undefined, true); app.model.gpio[0].property = "cs-gpios";
  await click(app.root, "Load pin assignments"); change(field(app.root, "GPIO consumer"), gpioKey(app.model.gpio[0]));
  assert.equal(named(app.root, "button", "Apply GPIO assignment").disabled, false);
  assert.equal(named(app.root, "button", "Unassign GPIO property").disabled, true);
  assert.ok(app.root.textContent.includes("keep their bus-device indices")); app.dispose();
});

test("unsupported targets and empty observations retain explanations without form actions", async () => {
  const app = harness(undefined, true); app.model.supported = false; app.model.target = "other"; app.model.diagnostics = ["Selected target has no Nordic adapter."];
  await click(app.root, "Load pin assignments"); assert.ok(app.root.textContent.includes("no Nordic adapter"));
  assert.equal(named(app.root, "button", "Assign signal"), undefined); assert.equal(app.commands.length, 0); app.dispose();
  const empty = harness(undefined, true); empty.model.controllers = []; empty.model.gpio = []; empty.model.pins = [];
  await click(empty.root, "Load pin assignments"); assert.ok(empty.root.textContent.includes("No supported controller declarations"));
  assert.ok(empty.root.textContent.includes("No supported GPIO property")); empty.dispose();
});

test("unsupported groups stay visible with their source-edit explanation", async () => {
  const app = harness(undefined, true); app.model.controllers[0].states[0].editable = false;
  app.model.controllers[0].states[0].reason = "Opaque pinctrl expressions require source editing.";
  await signal(app); assert.ok(app.root.textContent.includes("Opaque pinctrl"));
  assert.equal(named(app.root, "button", "Reassign signal in draft").disabled, true);
  assert.equal(named(app.root, "button", "Unassign signal from draft").disabled, true); app.dispose();
});

test("clearing a selection removes previous form commands", async () => {
  const app = harness(undefined, true); await signal(app); change(field(app.root, "Controller"), "");
  named(app.root, "form", "Peripheral signal assignment").dispatch("submit"); await settle(); assert.equal(app.commands.length, 0);
  change(field(app.root, "GPIO consumer"), gpioKey(app.model.gpio[0])); change(field(app.root, "GPIO consumer"), "");
  named(app.root, "form", "GPIO assignment").dispatch("submit"); await settle(); assert.equal(app.commands.length, 0); app.dispose();
});

test("leaving clears observations and disposal removes interaction handlers", async () => {
  const app = harness(undefined, true); await signal(app); app.panel.navigate(); assert.equal(app.state.pinView.data, null);
  const calls = app.calls.length; app.dispose(); named(app.root, "button", "Load pin assignments").dispatch("click");
  await settle(); assert.equal(app.calls.length, calls);
});

test("keys preserve GPIO selection when source IDs change", () => {
  const row = fixture().gpio[0]; assert.equal(gpioKey(row), gpioKey({...row, node_id: "next-revision-id"}));
  assert.equal(pinKey({port: 1, pin: 2}), "1:2");
});

test("explicit selector changes preserve keyboard focus without scrolling", async () => {
  const app = harness(undefined, true); await signal(app);
  const role = field(app.root, "Signal role"); assert.equal(app.root.ownerDocument.activeElement, role);
  assert.deepEqual(role.focusOptions, {preventScroll: true});
  change(field(app.root, "GPIO consumer"), gpioKey(app.model.gpio[0]));
  assert.equal(app.root.ownerDocument.activeElement, field(app.root, "GPIO consumer")); app.dispose();
});

test("pin forms stay mounted and paused during a command refresh without revealing another section", async () => {
  const wait = deferred(); let reads = 0;
  const app = harness((api) => async (...args) => { const response = await api(...args); if (++reads > 1) await wait.promise; return response; }, true);
  await signal(app, "UART_TX", "sleep");
  const controller = field(app.root, "Controller"); const role = field(app.root, "Signal role");
  const form = named(app.root, "form", "Peripheral signal assignment");
  const controls = form.parentNode; const table = app.root.querySelectorAll("details")[0]; table.open = true;
  await click(app.root, "Reassign signal in draft");
  assert.equal(app.state.pinView.data, null); assert.equal(app.state.pinView.busy, true);
  assert.equal(controls.hidden, false); assert.equal(field(app.root, "Controller"), controller);
  assert.equal(field(app.root, "Signal role"), role); assert.equal(role.disabled, true);
  assert.equal(app.root.ownerDocument.activeElement, role);
  assert.match(form.textContent, /Refreshing/); assert.deepEqual(app.reveals, []);
  wait.resolve(); await settle();
  assert.equal(field(app.root, "Controller").value, "uart0");
  assert.equal(field(app.root, "Pin control state").value, "sleep");
  assert.equal(field(app.root, "Signal role").value, "UART_TX");
  assert.equal(app.root.ownerDocument.activeElement, field(app.root, "Signal role"));
  assert.deepEqual(app.root.ownerDocument.activeElement.focusOptions, {preventScroll: true});
  assert.equal(table.open, true); assert.equal(controls.hidden, false); assert.deepEqual(app.reveals, []);
  assert.match(form.textContent, /updated/); app.dispose();
});

test("source typing pauses mounted pin forms and navigation clears them", async () => {
  const app = harness(undefined, true); await signal(app);
  const pin = field(app.root, "Signal pin"); pin.value = "0:8";
  const controls = named(app.root, "form", "Peripheral signal assignment").parentNode;
  app.type(source + "/* typing */");
  assert.equal(controls.hidden, false); assert.equal(field(app.root, "Signal pin"), pin);
  assert.equal(pin.value, "0:8"); assert.equal(pin.disabled, true);
  app.panel.navigate(); assert.equal(controls.hidden, true); assert.equal(app.state.pinView.presentation, null);
  assert.deepEqual(app.reveals, []); app.dispose();
});

test("pin validation feedback appears beside its form without scrolling", async () => {
  const app = harness(undefined, true); await signal(app, "UART_RX");
  await click(app.root, "Assign signal to draft");
  assert.match(named(app.root, "form", "Peripheral signal assignment").textContent, /Choose a pin/);
  assert.deepEqual(app.reveals, []); app.dispose();
});

test("a completed pin update keeps keyboard focus on its equivalent action", async () => {
  const app = harness(undefined, true); await signal(app);
  const apply = named(app.root, "button", "Reassign signal in draft"); apply.focus();
  await click(app.root, "Reassign signal in draft");
  const refreshed = named(app.root, "button", "Reassign signal in draft");
  assert.notEqual(refreshed, apply); assert.equal(app.root.ownerDocument.activeElement, refreshed);
  assert.deepEqual(refreshed.focusOptions, {preventScroll: true}); assert.deepEqual(app.reveals, []); app.dispose();
});

test("a pin response does not move focus back after the user leaves its form", async () => {
  const wait = deferred(); let reads = 0;
  const app = harness((api) => async (...args) => { const response = await api(...args); if (++reads > 1) await wait.promise; return response; }, true);
  await signal(app); named(app.root, "button", "Reassign signal in draft").focus();
  await click(app.root, "Reassign signal in draft");
  const elsewhere = new Element("input", app.root.ownerDocument); elsewhere.focus();
  wait.resolve(); await settle();
  assert.equal(app.root.ownerDocument.activeElement, elsewhere); assert.deepEqual(app.reveals, []); app.dispose();
});
