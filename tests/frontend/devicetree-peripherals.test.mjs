import test from "node:test";
import assert from "node:assert/strict";
import {createPeripheralSession, peripheralAddress, peripheralFieldValue, peripheralLabel, peripheralName, topologyRows} from "../../src/zephyr_utilities/features/devicetree/web/peripherals.js";
import {createBindingSession} from "../../src/zephyr_utilities/features/devicetree/web/bindings.js";
import {mountPeripheralPanel} from "../../src/zephyr_utilities/features/devicetree/web/peripheral-view.js";

const source = '/ { bus: i2c { }; };\n';
const field = (name, type, extra = {}) => ({name, type, editable: true, required: false, local: null, ...extra});
function fixture() {
  return {nodes: [], targets: [], diagnostics: [],
    controllers: [{node_id: "bus", name: "i2c", path: "/i2c", kind: "i2c", status: "disabled", editable: true,
      fields: [field("status", "string", {enum: ["okay", "disabled"]}), field("clock-frequency", "int", {default: 100000})],
      addable: true, candidates: [{compatible: "example,sensor", name: "sensor", addable: true,
        fields: [field("mode", "string", {required: true, enum: ["fast", "slow"], default: "slow"})]}],
      children: [{node_id: "child", name: "sensor@20", path: "/i2c/sensor@20", compatible: "example,sensor", address: 32,
        editable: true, removable: true, fields: [field("rate", "int")], diagnostics: []}], diagnostics: []}],
    gpio_groups: [{node_id: "buttons", name: "buttons", path: "/buttons", kind: "button", editable: true, addable: true,
      add_fields: [field("gpios", "phandle-array", {required: true}), field("zephyr,code", "int", {required: true})],
      children: [], diagnostics: []}],
    gpio_providers: [{reference: "&gpio", path: "/gpio", cells: ["pin", "flags"]}],
    group_options: [{kind: "led", name: "leds", available: true}, {kind: "button", name: "buttons", available: false, reason: "Already exists."}]};
}
function deferred() { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
function harness(wrap = (api) => api, mount = false) {
  const state = {data: {workspace: "/workspace", id: "context", revision: 1}, draftView: {ready: true,
    entry: {generation: 0, text: source, server: {id: "draft", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel; let extraSync; let notifications = 0;
  const calls = []; const commands = []; const reveals = []; const model = fixture();
  const response = () => ({...structuredClone(model), schema_id: "schema" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const notify = () => { notifications++; session?.sync(); panel?.render(); extraSync?.(); };
  const api = wrap(async (url, options) => { calls.push({url, ...options}); return response(); });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(structuredClone(command)); const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"};
      entry.text = entry.server.text; notify(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; notify(); return true; }};
  const root = mount ? new Element("section") : null;
  const options = {api, state, drafts, workspace: () => workspace, onChange: notify};
  if (mount) panel = mountPeripheralPanel(root, {...options, reveal: (node) => reveals.push(node)});
  else session = createPeripheralSession(options);
  return {state, session, panel, root, drafts, calls, commands, model, response, reveals, options,
    notifications: () => notifications,
    extraSync(value) { extraSync = value; },
    workspace(value) { workspace = value; session?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; notify(); },
    dispose() { session?.dispose(); panel?.dispose(); }};
}

test("peripheral inspection is explicit and uses the exact canonical draft guards", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); await app.session.load();
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/peripherals");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.peripheralView.data.controllers[0].fields[1].default, 100000);
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("topology commands share the draft path and refresh current node metadata", async () => {
  const app = harness(); await app.session.load();
  for (const command of [
    {kind: "peripheral_controller", node_id: "bus", values: {status: "okay"}},
    {kind: "peripheral_update", node_id: "child", address: 33, values: {}},
    {kind: "peripheral_remove", node_id: "child"},
  ]) assert.equal(await app.session.command(command), true);
  assert.deepEqual(app.commands.map((command) => command.schema_id), ["schema0", "schema1", "schema2"]);
  assert.equal(app.state.peripheralView.data.revision, 3);
  assert.ok(app.calls.every((call) => call.url.endsWith("/drafts/peripherals"))); app.dispose();
});

test("binding and peripheral observations have separate state and shared invalidation", async () => {
  const app = harness(); const bindings = createBindingSession(app.options); app.extraSync(() => bindings.sync());
  await bindings.load(); await app.session.load();
  assert.ok(app.state.bindingView.data); assert.ok(app.state.peripheralView.data);
  await app.session.command({kind: "peripheral_controller", node_id: "bus", values: {status: "okay"}});
  assert.equal(app.state.bindingView.data, null); assert.equal(app.state.peripheralView.data.revision, 1);
  bindings.dispose(); app.dispose();
});

test("incomplete topology responses never enable peripheral transformations", async () => {
  const app = harness((api) => async (...args) => { const result = await api(...args); delete result.gpio_providers; return result; });
  assert.equal(await app.session.load(), false); assert.match(app.state.peripheralView.error, /does not match/);
  assert.equal(app.state.peripheralView.data, null); app.dispose();
});

test("pending typing invalidates both topology and later actions", async () => {
  const app = harness(); await app.session.load(); app.type(source + "/* pending */");
  assert.equal(app.state.peripheralView.data, null);
  assert.equal(await app.session.command({kind: "peripheral_remove", node_id: "child"}), false);
  assert.equal(app.commands.length, 0); app.dispose();
});

test("late peripheral inspections cannot cross a workspace change", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { const value = await api(...args); await wait.promise; return value; });
  const loading = app.session.load(); app.workspace("/other"); wait.resolve();
  assert.equal(await loading, false); assert.equal(app.state.peripheralView.data, null); app.dispose();
});

test("inspection cancellation aborts only its read request", async () => {
  const wait = deferred(); let signal;
  const app = harness((api) => async (...args) => { signal = args[1].signal; const value = await api(...args); await wait.promise; return value; });
  const loading = app.session.load(); app.session.cancel(); wait.resolve();
  assert.equal(await loading, false); assert.equal(signal.aborted, true);
  assert.match(app.state.peripheralView.message, /cancelled/); assert.equal(app.commands.length, 0); app.dispose();
});

test("rejected topology mutations expose reconciliation without repeating the command", async () => {
  const app = harness(); await app.session.load();
  app.drafts.command = async () => { app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Address collision."; return false; };
  assert.equal(await app.session.command({kind: "peripheral_update", node_id: "child", address: 32, values: {}}), false);
  assert.match(app.state.peripheralView.error, /collision/); assert.equal(app.session.canReconcile(), true);
  assert.equal(await app.session.reconcile(), true); assert.match(app.state.peripheralView.message, /Load peripherals again/);
  assert.equal(app.calls.length, 1); app.dispose();
});

test("disposing a peripheral panel ignores late inspection results", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { const value = await api(...args); await wait.promise; return value; });
  const loading = app.session.load(); const before = app.notifications(); app.dispose(); wait.resolve();
  assert.equal(await loading, false); assert.equal(app.notifications(), before); assert.equal(app.state.peripheralView.data, null);
});

test("numeric fields and node identifiers reject ambiguous source fragments", () => {
  assert.equal(peripheralAddress("0x2a"), 42); assert.throws(() => peripheralAddress("1 2"), /one address/);
  assert.throws(() => peripheralAddress("ADDRESS"), /integers/); assert.throws(() => peripheralAddress("-1"), /integers/);
  assert.equal(peripheralName("sensor-device"), "sensor-device"); assert.throws(() => peripheralName("sensor@20"), /suffix/);
  assert.throws(() => peripheralName("_sensor"), /beginning with a letter/); assert.throws(() => peripheralName("sensor?"), /node name/);
  assert.throws(() => peripheralName("a".repeat(64)), /63/); assert.equal(peripheralName("a".repeat(63)).length, 63);
  assert.equal(peripheralLabel("sensor_1"), "sensor_1"); assert.equal(peripheralLabel(""), ""); assert.throws(() => peripheralLabel("bad-label"), /underscores/);
});

test("local address checks explain reserved I2C addresses and occupied chip selects before a command", () => {
  const parent = fixture().controllers[0];
  assert.throws(() => peripheralAddress("0x7f", parent), /7-bit/);
  assert.throws(() => peripheralAddress("0x20", parent), /already uses/);
  assert.equal(peripheralAddress("0x20", parent, "child"), 32);
  assert.throws(() => peripheralAddress("2", {kind: "spi", chip_selects: [0, 1]}), /cs-gpios/);
  assert.equal(peripheralAddress("1", {kind: "spi", chip_selects: [0, 1]}), 1);
});

test("field values preserve per-entry enumeration and provider layouts", () => {
  assert.deepEqual(peripheralFieldValue(field("array", "array", {enum: [1, 2]}), "2 1 2"), [2, 1, 2]);
  assert.throws(() => peripheralFieldValue(field("mode", "string", {enum: ["fast"]}), "slow"), /enumeration/);
  assert.deepEqual(peripheralFieldValue(field("gpios", "phandle-array"), [{reference: "&gpio", cells: ["4", "0x1"]}]), [{reference: "&gpio", cells: [4, 1]}]);
});

test("topology keys preserve parent-child relationships across revision-specific node IDs", () => {
  const data = fixture(); const before = topologyRows(data); data.controllers[0].node_id = "new-bus"; data.controllers[0].children[0].node_id = "new-child";
  const after = topologyRows(data); assert.deepEqual(before.map((row) => row.key), after.map((row) => row.key));
  assert.equal(after.find((row) => row.node.node_id === "new-child").parent.node_id, "new-bus");
});

test("an unrelated draft command retains paused peripheral presentation until explicitly refreshed", async () => {
  const app = harness(); await app.session.load(); const observed = app.state.peripheralView.data;
  await app.drafts.command({kind: "set_status", node_id: "bus", status: "okay"});
  assert.equal(app.state.peripheralView.data, null); assert.equal(app.state.peripheralView.presentation, observed);
  assert.equal(await app.session.command({kind: "peripheral_remove", node_id: "child"}), false);
  assert.equal(app.commands.length, 1);
  await app.session.load(); assert.equal(app.state.peripheralView.data.revision, 1);
  assert.equal(app.state.peripheralView.presentation, app.state.peripheralView.data); app.dispose();
});

// Minimal event-bubbling DOM for observable panel interactions; server behavior is tested separately.
class Element {
  constructor(tag, document) { this.tagName = tag.toUpperCase(); this.ownerDocument = document || {createElement: (name) => new Element(name, this.ownerDocument)};
    this.children = []; this.parentNode = null; this.dataset = {}; this.attributes = {}; this.listeners = {}; this._text = ""; this._value = null; this.type = ""; this.disabled = false; this.hidden = false; this.checked = false; }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set value(value) { this._value = String(value); }
  get value() { return this._value ?? (this.tagName === "SELECT" ? this.children[0]?.value || "" : ""); }
  append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this); this.parentNode = null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, callback, options) { (this.listeners[type] ||= []).push(callback); options?.signal?.addEventListener("abort", () => { this.listeners[type] = this.listeners[type].filter((item) => item !== callback); }); }
  matches(selector) { if (selector === "[data-peripheral-field]") return "peripheralField" in this.dataset; return this.tagName.toLowerCase() === selector; }
  querySelectorAll(selector) { const matches = selector.split(",").map((value) => value.trim()); return this.children.flatMap((child) => [...(matches.some((value) => child.matches(value)) ? [child] : []), ...child.querySelectorAll(selector)]); }
  closest(selector) { for (let item = this; item; item = item.parentNode) if (item.matches(selector)) return item; return null; }
  dispatch(type) { const event = {target: this, cancelBubble: false, preventDefault() {}}; for (let item = this; item; item = item.parentNode) for (const callback of item.listeners[type] || []) callback(event); }
  get selectedOptions() { return this.children.filter((child) => child.selected); }
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const named = (root, tag, text) => root.querySelectorAll(tag).find((node) => node.textContent.startsWith(text));
async function click(root, text) { const node = named(root, "button", text); assert.ok(node, text); assert.equal(node.disabled, false, text + " enabled"); node.dispatch("click"); if (node.type === "submit") node.closest("form").dispatch("submit"); await settle(); return node; }
function change(node, value) { node.value = value; node.dispatch("change"); }
function chosenField(form, name) { return form.querySelectorAll("fieldset").find((node) => node.children[0].textContent === name); }
function useField(group, value) { const controls = group.querySelectorAll("input, select, textarea"); const include = controls.find((node) => node.type === "checkbox"); include.checked = true; include.dispatch("change"); const input = controls.find((node) => node.type !== "checkbox"); input.value = value; return input; }

test("panel controller controls send only explicitly selected schema fields", async () => {
  const app = harness(undefined, true); await click(app.root, "Load peripherals"); await click(app.root, "I2C · /i2c");
  const form = named(app.root, "form", "Controller properties");
  assert.equal(chosenField(form, "clock-frequency").querySelectorAll("input").find((node) => node.type === "text").value, "");
  useField(chosenField(form, "clock-frequency"), "400000");
  await click(app.root, "Apply controller properties");
  assert.deepEqual(app.commands[0], {kind: "peripheral_controller", node_id: "bus", values: {"clock-frequency": 400000}, schema_id: "schema0"}); app.dispose();
});

test("bus creation requires explicit required values and submits address separately", async () => {
  const app = harness(undefined, true); await click(app.root, "Load peripherals"); await click(app.root, "I2C · /i2c");
  const form = named(app.root, "form", "Add I2C device"); change(form.querySelectorAll("select")[0], "example,sensor");
  const inputs = form.querySelectorAll("input").filter((node) => node.type === "text"); inputs[0].value = "probe"; inputs[1].value = "probe0"; inputs[2].value = "0x31";
  await click(app.root, "Add bus device to draft"); assert.equal(app.commands.length, 0); assert.match(app.state.peripheralView.error, /mode/);
  const mode = chosenField(form, "mode").querySelectorAll("select")[0]; change(mode, "0");
  await click(app.root, "Add bus device to draft");
  assert.deepEqual(app.commands[0], {kind: "peripheral_add", parent_id: "bus", name: "probe", label: "probe0", compatible: "example,sensor", address: 49, values: {mode: "fast"}, schema_id: "schema0"}); app.dispose();
});

test("editing an addressed child keeps its identity and uses one update command", async () => {
  const app = harness(undefined, true); await click(app.root, "Load peripherals"); await click(app.root, "sensor@20");
  const form = named(app.root, "form", "Edit I2C device"); form.querySelectorAll("input")[0].value = "0x21";
  await click(app.root, "Apply device changes");
  assert.deepEqual(app.commands[0], {kind: "peripheral_update", node_id: "child", address: 33, values: {}, schema_id: "schema0"}); app.dispose();
});

test("UART child creation submits no invented bus address", async () => {
  const app = harness(undefined, true); const parent = app.model.controllers[0];
  parent.kind = "uart"; parent.path = "/uart"; parent.name = "uart"; parent.children = []; parent.candidates[0].fields = [];
  await click(app.root, "Load peripherals"); await click(app.root, "UART · /uart");
  const form = named(app.root, "form", "Add UART device"); change(form.querySelectorAll("select")[0], "example,sensor");
  assert.equal(form.querySelectorAll("input").length, 2);
  await click(app.root, "Add bus device to draft"); assert.equal(app.commands[0].address, null); assert.equal(app.commands[0].kind, "peripheral_add"); app.dispose();
});

test("resolved SPI chip-select choices stay bounded to the existing array", async () => {
  const app = harness(undefined, true); const parent = app.model.controllers[0];
  parent.kind = "spi"; parent.path = "/spi"; parent.name = "spi"; parent.children = []; parent.chip_selects = [0, 1]; parent.candidates[0].fields = [];
  await click(app.root, "Load peripherals"); await click(app.root, "SPI · /spi");
  const form = named(app.root, "form", "Add SPI device"); const selects = form.querySelectorAll("select");
  change(selects[0], "example,sensor"); assert.deepEqual(selects[1].children.map((item) => item.value), ["", "0", "1"]);
  change(selects[1], "1"); await click(app.root, "Add bus device to draft"); assert.equal(app.commands[0].address, 1); app.dispose();
});

test("an address with path consumers is read-only while other device fields remain editable", async () => {
  const app = harness(undefined, true); const child = app.model.controllers[0].children[0];
  child.address_editable = false; child.address_reason = "A path reference depends on this address.";
  await click(app.root, "Load peripherals"); await click(app.root, "sensor@20");
  const form = named(app.root, "form", "Edit I2C device"); const address = form.querySelectorAll("input")[0];
  assert.equal(address.readOnly, true); assert.ok(form.textContent.includes("path reference"));
  useField(chosenField(form, "rate"), "50"); await click(app.root, "Apply device changes");
  assert.deepEqual(app.commands[0], {kind: "peripheral_update", node_id: "child", address: 32, values: {rate: 50}, schema_id: "schema0"}); app.dispose();
});

test("GPIO button creation uses provider cell names and explicitly entered key code", async () => {
  const app = harness(undefined, true); await click(app.root, "Load peripherals"); await click(app.root, "BUTTON · /buttons");
  const form = named(app.root, "form", "Add GPIO button"); const provider = chosenField(form, "gpios");
  change(provider.querySelectorAll("select")[0], "&gpio");
  const cells = provider.querySelectorAll("input").filter((node) => node.type === "text"); assert.equal(cells.length, 2);
  assert.ok(provider.textContent.includes("pin")); assert.ok(provider.textContent.includes("flags")); cells[0].value = "7"; cells[1].value = "1";
  useField(chosenField(form, "zephyr,code"), "11"); await click(app.root, "Add button to draft");
  assert.deepEqual(app.commands[0], {kind: "gpio_add", parent_id: "buttons", name: "button", label: "", values: {gpios: [{reference: "&gpio", cells: [7, 1]}], "zephyr,code": 11}, schema_id: "schema0"}); app.dispose();
});

test("unsupported removals stay visible with their dependency explanation", async () => {
  const app = harness(undefined, true); app.model.controllers[0].children[0].removable = false;
  app.model.controllers[0].children[0].remove_reason = "Referenced by chosen.";
  await click(app.root, "Load peripherals"); await click(app.root, "sensor@20");
  assert.equal(named(app.root, "button", "Remove device from draft").disabled, true);
  assert.ok(app.root.textContent.includes("Referenced by chosen.")); assert.equal(app.commands.length, 0); app.dispose();
});

test("GPIO group creation is explicit and preserves unavailable group explanations", async () => {
  const app = harness(undefined, true); await click(app.root, "Load peripherals");
  const form = named(app.root, "form", "Add a GPIO device group"); const select = form.querySelectorAll("select")[0];
  change(select, "button"); assert.equal(named(form, "button", "Add GPIO group to draft").disabled, true); assert.ok(form.textContent.includes("Already exists."));
  change(select, "led"); await click(app.root, "Add GPIO group to draft");
  assert.deepEqual(app.commands[0], {kind: "gpio_group_add", group_kind: "led", name: "leds", schema_id: "schema0"}); app.dispose();
});

test("panel navigation discards loaded topology and disposal removes event handlers", async () => {
  const app = harness(undefined, true); await click(app.root, "Load peripherals"); app.panel.navigate();
  assert.equal(app.state.peripheralView.data, null); const calls = app.calls.length; app.dispose();
  named(app.root, "button", "Load peripherals").dispatch("click"); await settle(); assert.equal(app.calls.length, calls);
});

test("peripheral refresh retains the mounted form and reports results beside its action", async () => {
  const wait = deferred(); let reads = 0;
  const app = harness((api) => async (...args) => { const response = await api(...args); if (++reads > 1) await wait.promise; return response; }, true);
  await click(app.root, "Load peripherals"); await click(app.root, "I2C · /i2c"); app.reveals.length = 0;
  const form = named(app.root, "form", "Controller properties");
  const control = useField(chosenField(form, "clock-frequency"), "400000");
  const container = form.parentNode.parentNode;
  await click(app.root, "Apply controller properties");
  assert.equal(app.state.peripheralView.data, null); assert.equal(container.hidden, false);
  assert.equal(named(app.root, "form", "Controller properties"), form);
  assert.equal(control.value, "400000"); assert.equal(control.disabled, true);
  assert.match(form.textContent, /Refreshing/); assert.deepEqual(app.reveals, []);
  wait.resolve(); await settle();
  assert.equal(container.hidden, false); assert.match(named(app.root, "form", "Controller properties").textContent, /updated/);
  assert.deepEqual(app.reveals, []); app.dispose();
});

test("peripheral input errors remain beside the submitting form without changing the viewport", async () => {
  const app = harness(undefined, true); await click(app.root, "Load peripherals"); await click(app.root, "I2C · /i2c");
  app.reveals.length = 0; await click(app.root, "Apply controller properties");
  assert.match(named(app.root, "form", "Controller properties").textContent, /Select at least one controller field/);
  assert.deepEqual(app.reveals, []); assert.equal(app.commands.length, 0); app.dispose();
});
