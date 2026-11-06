import test from "node:test";
import assert from "node:assert/strict";
import {createInterruptGPIOSession, interruptGPIOEntry, interruptGPIOFlags, interruptGPIORows} from "../../src/zephyr_utilities/features/devicetree/web/interrupt-gpio.js";
import {mountInterruptGPIOPanel} from "../../src/zephyr_utilities/features/devicetree/web/interrupt-gpio-view.js";

const source = '/ { gpio0: gpio { }; sensor { int-gpios = <&gpio0 1 256>; }; };\n';
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; };
const settle = () => new Promise((resolve) => setImmediate(resolve));
const fixture = () => ({nodes: [], targets: [], diagnostics: [],
  providers: [{id: "gpio0", reference: "&gpio0", path: "/gpio", labels: ["gpio0"], cells: ["pin", "flags"], supported: true, status: "enabled", pin_count: 16, reserved_ranges: [[4, 2]]},
    {id: "gpio1", reference: "&gpio1", path: "/other-gpio", labels: ["gpio1"], cells: ["pin", "flags"], supported: true, status: "disabled", pin_count: null, reserved_ranges: null}],
  consumers: [{node_id: "sensor", name: "sensor", path: "/sensor", property: "int-gpios", recipe_id: "test,interrupt", signal: "Interrupt", description: "Synthetic declared interrupt signal.", editable: true, entry: {reference: "&gpio0", cells: [1, 256]}, field: {name: "int-gpios", type: "phandle-array"}},
    {node_id: "sensor", name: "sensor", path: "/sensor", property: "alert-gpios", recipe_id: "test,interrupt", signal: "Alert", editable: true, entry: null, field: {name: "alert-gpios", type: "phandle-array"}}]});

function harness({mount = false, wrap = (api) => api, onRemove} = {}) {
  const state = {data: {workspace: "/workspace", id: "context", revision: 1}, draftView: {ready: true,
    entry: {generation: 0, text: source, path: "/workspace/app/app.overlay",
      server: {id: "draft", document_id: "document", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel; let notifications = 0;
  const calls = []; const commands = []; const reveals = []; const toasts = []; const model = fixture();
  const response = () => ({...structuredClone(model), schema_id: "schema" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const notify = () => { notifications++; session?.sync(); panel?.render(); };
  const api = wrap(async (url, options) => { calls.push({url, ...options}); return response(); });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(structuredClone(command)); const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      if (command.kind === "interrupt_gpio_set") model.consumers.find((row) => row.node_id === command.node_id && row.property === command.property).entry = structuredClone(command.entry);
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"};
      entry.text = entry.server.text; notify(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; notify(); return true; }};
  const options = {api, state, workspace: () => workspace, drafts, onChange: notify}; const root = mount ? new Element("section") : null;
  if (mount) panel = mountInterruptGPIOPanel(root, {...options, onRemove, notify: (message, error) => toasts.push({message, error}), reveal: (node, options) => reveals.push({node, options})});
  else session = createInterruptGPIOSession(options);
  return {state, options, root, panel, session, model, calls, commands, response, drafts, reveals, toasts,
    notifications: () => notifications,
    workspace(value) { workspace = value; session?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; notify(); },
    dispose() { session?.dispose(); panel?.dispose(); }};
}

test("interrupt GPIO inspection is explicit and uses the canonical draft guard without mutation", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); assert.equal(await app.session.load(), true);
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/interrupt-gpios");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("interrupt GPIO commands refresh the accepted revision and stale typing withholds mutations", async () => {
  const app = harness(); await app.session.load(); const command = {kind: "interrupt_gpio_set", node_id: "sensor", property: "int-gpios", entry: {reference: "&gpio0", cells: [2, 257]}};
  assert.equal(await app.session.command(command), true); assert.deepEqual(app.commands[0], {...command, schema_id: "schema0"});
  assert.equal(app.state.interruptGPIOView.data.revision, 1); const observed = app.state.interruptGPIOView.data;
  app.type(source + "/* pending */"); assert.equal(app.state.interruptGPIOView.data, null); assert.equal(app.state.interruptGPIOView.presentation, observed);
  assert.equal(await app.session.command(command), false); assert.equal(app.commands.length, 1); app.dispose();
});

test("incomplete responses and source cannot enable interrupt GPIO commands", async () => {
  const app = harness({wrap: (api) => async (...args) => { const result = await api(...args); delete result.providers; return result; }});
  assert.equal(await app.session.load(), false); assert.match(app.state.interruptGPIOView.error, /does not match/);
  app.state.draftView.entry.server.syntax.blocked = true; assert.equal(await app.session.load(), false);
  assert.equal(app.calls.length, 1); assert.equal(app.commands.length, 0); app.dispose();
});

test("late interrupt GPIO responses cannot cross source, workspace, navigation or disposal", async () => {
  for (const action of ["source", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); const app = harness({wrap: (api) => async (...args) => { const data = await api(...args); await wait.promise; return data; }});
    const pending = app.session.load();
    if (action === "source") app.type(source + "/* edit */");
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.session.clear();
    if (action === "dispose") app.dispose();
    wait.resolve(); assert.equal(await pending, false, action); assert.equal(app.state.interruptGPIOView.data, null); app.dispose();
  }
});

test("cancel and uncertain command reconciliation never repeat an interrupt GPIO mutation", async () => {
  const wait = deferred(); let signal;
  const cancelled = harness({wrap: (api) => async (...args) => { signal = args[1].signal; const data = await api(...args); await wait.promise; return data; }});
  const pending = cancelled.session.load(); cancelled.session.cancel(); wait.resolve(); assert.equal(await pending, false);
  assert.equal(signal.aborted, true); assert.match(cancelled.state.interruptGPIOView.message, /cancelled/); cancelled.dispose();
  const app = harness(); await app.session.load(); let attempts = 0;
  app.drafts.command = async () => { attempts++; app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Reply lost; reconcile."; return false; };
  assert.equal(await app.session.command({kind: "interrupt_gpio_set", node_id: "sensor", property: "int-gpios", entry: {reference: "&gpio0", cells: [0, 0]}}), false);
  assert.match(app.state.interruptGPIOView.error, /Reply lost/); assert.equal(await app.session.reconcile(), true); assert.equal(attempts, 1);
  assert.match(app.state.interruptGPIOView.message, /Load interrupt GPIOs again/); app.dispose();
});

test("interrupt GPIO cells reject unsupported layouts, expressions and integer overflow", () => {
  const provider = fixture().providers[1];
  assert.deepEqual(interruptGPIOEntry(provider, {pin: "0x1f", flags: "0xffff"}), {reference: "&gpio1", cells: [31, 65535]});
  assert.deepEqual(interruptGPIOEntry(provider, {pin: "0", flags: "0"}), {reference: "&gpio1", cells: [0, 0]});
  for (const pin of ["32", "-1", "GPIO_PIN(2)", "1 2", ""]) assert.throws(() => interruptGPIOEntry(provider, {pin, flags: "0"}));
  for (const flags of ["65536", "-1", "GPIO_ACTIVE_LOW", "1 2", ""]) assert.throws(() => interruptGPIOEntry(provider, {pin: "0", flags}));
  assert.throws(() => interruptGPIOEntry({...provider, supported: false}, {pin: "0", flags: "0"}), /supported local/);
  assert.throws(() => interruptGPIOEntry({...provider, cells: ["flags", "pin"]}, {pin: "0", flags: "0"}), /layout/);
});

test("interrupt GPIO pin limits use only known local count and reservations", () => {
  const [provider, unknown] = fixture().providers;
  assert.deepEqual(interruptGPIOEntry(provider, {pin: "15", flags: "0"}).cells, [15, 0]);
  assert.throws(() => interruptGPIOEntry(provider, {pin: "16", flags: "0"}), /pin count of 16/);
  for (const pin of ["4", "5"]) assert.throws(() => interruptGPIOEntry(provider, {pin, flags: "0"}), /reserved/);
  assert.deepEqual(interruptGPIOEntry(provider, {pin: "6", flags: "0"}).cells, [6, 0]);
  assert.deepEqual(interruptGPIOEntry(unknown, {pin: "31", flags: "0"}).cells, [31, 0]);
});

test("logical GPIO polarity preserves every numeric flag bit except bit zero", () => {
  assert.equal(interruptGPIOFlags("0x8100", "1"), 33025);
  assert.equal(interruptGPIOFlags("0xffff", "0"), 65534);
  assert.equal(interruptGPIOFlags("0xffff", "1"), 65535);
  assert.throws(() => interruptGPIOFlags("", "1"));
  assert.throws(() => interruptGPIOFlags("65536", "0"));
  assert.throws(() => interruptGPIOFlags("256", "2"), /active high or active low/);
});

test("interrupt GPIO selection keys distinguish properties and survive revised source occurrence ids", () => {
  const model = fixture(); const before = interruptGPIORows(model).map((row) => row.key);
  assert.equal(new Set(before).size, 2);
  for (const row of model.consumers) row.node_id = "new-source-id";
  assert.deepEqual(interruptGPIORows(model).map((row) => row.key), before);
});

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
    return this.className === "dte-peripheral-detail" && this.ownerDocument.detailBounds
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

test("interrupt GPIO editing preserves numeric flags while logical polarity changes and names the exact property", async () => {
  const app = harness({mount: true}); await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios");
  const form = named(app.root, "form", "Edit interrupt GPIO specifier"); change(field(form, "Flags"), "0x8100");
  assert.equal(field(form, "Logical active level").value, "0"); change(field(form, "Logical active level"), "1");
  assert.equal(field(form, "Flags").value, "33025"); field(form, "Pin").value = "15";
  await click(app.root, "Apply interrupt GPIO to draft");
  assert.deepEqual(app.commands[0], {kind: "interrupt_gpio_set", node_id: "sensor", property: "int-gpios", entry: {reference: "&gpio0", cells: [15, 33025]}, schema_id: "schema0"});
  assert.match(app.root.textContent, /logical active level, not an interrupt edge or level selection/);
  assert.match(app.root.textContent, /Driver and Kconfig trigger behavior/);
  assert.match(form.textContent, /Declared pin count: 16/); assert.match(form.textContent, /Locally reserved pins: 4–5/); app.dispose();
});

test("adding an absent supported property requires explicit provider, pin and flags without polarity defaults", async () => {
  const app = harness({mount: true}); await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · alert-gpios");
  const form = named(app.root, "form", "Edit interrupt GPIO specifier");
  assert.equal(field(form, "Flags").value, ""); assert.equal(field(form, "Logical active level").value, "");
  assert.equal(field(form, "Logical active level").disabled, true); assert.equal(field(form, "GPIO provider").value, "");
  change(field(form, "GPIO provider"), "&gpio1"); field(form, "Pin").value = "31";
  await click(app.root, "Apply interrupt GPIO to draft"); assert.equal(app.commands.length, 0);
  change(field(form, "Flags"), "0"); assert.equal(field(form, "Logical active level").disabled, false);
  await click(app.root, "Apply interrupt GPIO to draft");
  assert.deepEqual(app.commands[0], {kind: "interrupt_gpio_set", node_id: "sensor", property: "alert-gpios", entry: {reference: "&gpio1", cells: [31, 0]}, schema_id: "schema0"});
  assert.match(app.root.textContent, /local pin count is unknown/); assert.match(app.root.textContent, /Local reserved pin ranges are unknown/);
  assert.equal(app.model.consumers[0].entry.cells[1], 256, "the other property is unchanged"); app.dispose();
});

test("interrupt GPIO fields and viewport survive passive renders and pending source typing", async () => {
  const app = harness({mount: true}); await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios");
  const form = named(app.root, "form", "Edit interrupt GPIO specifier"); const pin = field(form, "Pin"); pin.value = "7"; pin.focus(); app.root.scrollTop = 600;
  app.panel.render(); assert.equal(named(app.root, "form", "Edit interrupt GPIO specifier"), form); assert.equal(pin.value, "7");
  app.type(source + "/* pending */"); assert.equal(named(app.root, "form", "Edit interrupt GPIO specifier"), form);
  assert.equal(pin.disabled, true); assert.equal(pin.value, "7"); assert.equal(app.root.scrollTop, 600); assert.equal(app.reveals.length, 0); app.dispose();
});

test("interrupt GPIO refresh retains the form and displays its result beside the trigger without moving focus", async () => {
  const wait = deferred(); let reads = 0;
  const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
  await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios"); app.reveals.length = 0;
  const form = named(app.root, "form", "Edit interrupt GPIO specifier"); const pin = field(form, "Pin"); pin.value = "3";
  await click(app.root, "Apply interrupt GPIO to draft"); assert.equal(named(app.root, "form", "Edit interrupt GPIO specifier"), form);
  assert.equal(pin.disabled, true); assert.equal(pin.value, "3"); assert.match(form.textContent, /Refreshing interrupt GPIOs/);
  wait.resolve(); await settle(); assert.match(named(app.root, "form", "Edit interrupt GPIO specifier").textContent, /Draft updated/);
  assert.deepEqual(app.reveals, []); assert.equal(app.root.ownerDocument.activeElement.textContent, "Apply interrupt GPIO to draft"); app.dispose();
});

test("reserved and out-of-range interrupt GPIO pins show nearby errors and leave source unchanged", async () => {
  const app = harness({mount: true}); await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios"); app.reveals.length = 0;
  const form = named(app.root, "form", "Edit interrupt GPIO specifier"); field(form, "Pin").value = "4";
  await click(app.root, "Apply interrupt GPIO to draft"); assert.match(form.textContent, /reserved/);
  field(form, "Pin").value = "16"; await click(app.root, "Apply interrupt GPIO to draft"); assert.match(form.textContent, /pin count of 16/);
  assert.equal(app.commands.length, 0); assert.deepEqual(app.reveals, []); assert.equal(app.state.draftView.entry.text, source); app.dispose();
});

test("opaque consumers and unsupported providers explain source access without editable forms", async () => {
  const app = harness({mount: true}); app.model.consumers[0].editable = false; app.model.consumers[0].reason = "Multiple or symbolic GPIO specifiers require source editing.";
  app.model.providers.push({reference: "&hogged", path: "/hogged", cells: ["pin", "flags"], supported: false, reason: "GPIO hog allocations require source editing."});
  await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios");
  assert.match(app.root.textContent, /Multiple or symbolic GPIO specifiers/); assert.match(app.root.textContent, /GPIO hog allocations/);
  assert.equal(named(app.root, "button", "Apply interrupt GPIO to draft"), undefined); assert.equal(app.commands.length, 0); app.dispose();
});

test("empty interrupt GPIO evidence and imported descriptions render as text", async () => {
  const app = harness({mount: true}); app.model.consumers = [];
  app.model.diagnostics = ['<img src=x onerror="bad()"> Unsupported evidence'];
  await click(app.root, "Load interrupt GPIOs"); assert.match(app.root.textContent, /No supported local interrupt GPIO consumers/);
  assert.match(app.root.textContent, /<img src=x/); assert.equal(app.root.querySelectorAll("img").length, 0); app.dispose();
});

test("interrupt GPIO removal reviews the whole consumer node once without mutating the draft", async () => {
  const wait = deferred(); const reviews = [];
  const app = harness({mount: true, onRemove: async (...args) => { reviews.push(args); await wait.promise; }});
  await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios");
  assert.match(app.root.textContent, /whole consumer node, including its other signals/);
  const trigger = await click(app.root, "Review consumer node removal"); assert.equal(trigger.textContent, "Preparing removal review…");
  trigger.dispatch("click"); assert.equal(reviews.length, 1); assert.equal(reviews[0][0], "sensor"); assert.equal(reviews[0][1], trigger);
  assert.equal(app.commands.length, 0); wait.resolve(); await settle(); assert.equal(trigger.textContent, "Review consumer node removal"); app.dispose();
});

test("interrupt GPIO navigation and disposal ignore late removal errors", async () => {
  let reject; const wait = new Promise((_resolve, no) => { reject = no; });
  const app = harness({mount: true, onRemove: () => wait}); await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios");
  await click(app.root, "Review consumer node removal"); app.panel.navigate(); assert.equal(app.state.interruptGPIOView.data, null);
  reject(new Error("Old context")); await settle(); assert.equal(app.state.interruptGPIOView.error, "");
  const count = app.calls.length; app.dispose(); named(app.root, "button", "Load interrupt GPIOs").dispatch("click"); await settle(); assert.equal(app.calls.length, count);
});

test("explicit interrupt GPIO errors announce offscreen feedback once without passive scrolling or focus changes", async () => {
  const app = harness({mount: true}); await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios");
  app.reveals.length = 0; app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400};
  const form = named(app.root, "form", "Edit interrupt GPIO specifier"); field(form, "Flags").value = "65536";
  const trigger = await click(app.root, "Apply interrupt GPIO to draft");
  assert.deepEqual(app.reveals, []); assert.equal(app.toasts.length, 1);
  assert.match(app.toasts[0].message, /0 to 65535/); assert.equal(app.toasts[0].error, true);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.commands.length, 0);
  app.panel.render(); app.panel.render(); assert.equal(app.toasts.length, 1); assert.deepEqual(app.reveals, []); app.dispose();
});

test("async interrupt GPIO results announce feedback only for the still-current explicit action", async () => {
  for (const action of ["current", "source", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); let reads = 0;
    const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
    await click(app.root, "Load interrupt GPIOs"); await click(app.root, "/sensor · int-gpios"); app.reveals.length = 0;
    app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400};
    await click(app.root, "Apply interrupt GPIO to draft"); app.panel.render(); assert.equal(app.reveals.length, 0);
    if (action === "source") app.type(source + "/* pending */");
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.panel.navigate();
    if (action === "dispose") app.dispose();
    wait.resolve(); await settle(); assert.deepEqual(app.reveals, []); assert.equal(app.toasts.length, action === "current" ? 1 : 0, action);
    if (action === "current") {
      assert.match(app.toasts[0].message, /Draft updated/); assert.equal(app.toasts[0].error, false);
      assert.equal(app.root.ownerDocument.activeElement.textContent, "Apply interrupt GPIO to draft"); app.panel.render(); assert.equal(app.toasts.length, 1); assert.deepEqual(app.reveals, []);
    }
    app.dispose();
  }
});


test("interrupt GPIO row selection announces offscreen details without moving focus or repeating on render", async () => {
  const app = harness({mount: true}); await click(app.root, "Load interrupt GPIOs");
  app.root.ownerDocument.detailBounds = {top: 900, bottom: 1200, left: 0, right: 400};
  app.root.scrollTop = 650; const trigger = await click(app.root, "/sensor · int-gpios");
  assert.deepEqual(app.reveals, []); assert.equal(app.toasts.length, 1);
  assert.match(app.toasts[0].message, /Details for .* are ready below/); assert.equal(app.toasts[0].error, false);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.root.scrollTop, 650);
  app.panel.render(); app.panel.render(); assert.equal(app.toasts.length, 1); app.dispose();
});
