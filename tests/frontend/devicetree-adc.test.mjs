import test from "node:test";
import assert from "node:assert/strict";
import {createADCSession, adcChannel, adcConfig, adcEntry, adcRows} from "../../src/zephyr_utilities/features/devicetree/web/adc.js";
import {mountADCPanel} from "../../src/zephyr_utilities/features/devicetree/web/adc-view.js";

const source = '/ { adc0: adc { channel@1 { reg = <1>; }; }; sensor { io-channels = <&adc0 1>; }; };\n';
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; };
const settle = () => new Promise((resolve) => setImmediate(resolve));
const fields = () => ({gain: {enum: ["ADC_GAIN_1", "ADC_GAIN_1_6"]}, reference: {enum: ["ADC_REF_INTERNAL", "ADC_REF_EXTERNAL0"]}, acquisition_time: {type: "int"}});
const fixture = () => ({nodes: [], targets: [], diagnostics: [],
  providers: [{id: "adc0", reference: "&adc0", path: "/adc", label: "adc0", cells: ["input"], supported: true, status: "enabled", addable: true, channel_fields: fields()},
    {id: "adc2", reference: "&adc2", path: "/adc2", label: "adc2", cells: ["input"], supported: true, status: "disabled", addable: false, add_reason: "Child binding is unresolved."}],
  consumers: [{node_id: "sensor", name: "sensor", path: "/sensor", editable: true, entry: {reference: "&adc0", cells: [1]}, field: {name: "io-channels", type: "phandle-array"}}],
  channels: [{node_id: "channel1", parent_id: "adc0", name: "channel@1", path: "/adc/channel@1", channel: 1, gain: "ADC_GAIN_1_6", reference: "ADC_REF_INTERNAL", acquisition_time: 0, fields: fields(), editable: true}]});

function harness({mount = false, wrap = (api) => api, onRemove} = {}) {
  const state = {data: {workspace: "/workspace", id: "context", revision: 1}, draftView: {ready: true,
    entry: {generation: 0, text: source, path: "/workspace/app/app.overlay",
      server: {id: "draft", document_id: "document", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel; let notifications = 0;
  const calls = []; const commands = []; const reveals = []; const model = fixture();
  const response = () => ({...structuredClone(model), schema_id: "schema" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const notify = () => { notifications++; session?.sync(); panel?.render(); };
  const api = wrap(async (url, options) => { calls.push({url, ...options}); return response(); });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(structuredClone(command)); const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      if (command.kind === "adc_set") model.consumers.find((row) => row.node_id === command.node_id).entry = structuredClone(command.entry);
      if (command.kind === "adc_channel_set") Object.assign(model.channels.find((row) => row.node_id === command.node_id), command);
      if (command.kind === "adc_channel_add") {
        const provider = model.providers.find((row) => row.id === command.parent_id); const name = "channel@" + command.channel.toString(16);
        model.channels.push({...command, node_id: "new-channel", name, path: provider.path + "/" + name, editable: true, fields: provider.channel_fields});
      }
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"};
      entry.text = entry.server.text; notify(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; notify(); return true; }};
  const options = {api, state, workspace: () => workspace, drafts, onChange: notify}; const root = mount ? new Element("section") : null;
  if (mount) panel = mountADCPanel(root, {...options, onRemove, reveal: (node, options) => reveals.push({node, options})});
  else session = createADCSession(options);
  return {state, options, root, panel, session, model, calls, commands, response, drafts, reveals,
    notifications: () => notifications,
    workspace(value) { workspace = value; session?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; notify(); },
    dispose() { session?.dispose(); panel?.dispose(); }};
}

test("ADC inspection is explicit and uses the canonical draft guard without mutation", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); assert.equal(await app.session.load(), true);
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/adc");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("ADC commands refresh the accepted revision and stale typing withholds mutations", async () => {
  const app = harness(); await app.session.load(); const command = {kind: "adc_set", node_id: "sensor", entry: {reference: "&adc0", cells: [2]}};
  assert.equal(await app.session.command(command), true); assert.deepEqual(app.commands[0], {...command, schema_id: "schema0"});
  assert.equal(app.state.adcView.data.revision, 1); const observed = app.state.adcView.data;
  app.type(source + "/* pending */"); assert.equal(app.state.adcView.data, null); assert.equal(app.state.adcView.presentation, observed);
  assert.equal(await app.session.command(command), false); assert.equal(app.commands.length, 1); app.dispose();
});

test("incomplete responses and source cannot enable ADC commands", async () => {
  const app = harness({wrap: (api) => async (...args) => { const result = await api(...args); delete result.providers; return result; }});
  assert.equal(await app.session.load(), false); assert.match(app.state.adcView.error, /does not match/);
  app.state.draftView.entry.server.syntax.blocked = true; assert.equal(await app.session.load(), false);
  assert.equal(app.calls.length, 1); assert.equal(app.commands.length, 0); app.dispose();
});

test("late ADC responses cannot cross source, workspace, navigation or disposal", async () => {
  for (const action of ["source", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); const app = harness({wrap: (api) => async (...args) => { const data = await api(...args); await wait.promise; return data; }});
    const pending = app.session.load();
    if (action === "source") app.type(source + "/* edit */");
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.session.clear();
    if (action === "dispose") app.dispose();
    wait.resolve(); assert.equal(await pending, false, action); assert.equal(app.state.adcView.data, null); app.dispose();
  }
});

test("cancel and uncertain command reconciliation never repeat an ADC mutation", async () => {
  const wait = deferred(); let signal;
  const cancelled = harness({wrap: (api) => async (...args) => { signal = args[1].signal; const data = await api(...args); await wait.promise; return data; }});
  const pending = cancelled.session.load(); cancelled.session.cancel(); wait.resolve(); assert.equal(await pending, false);
  assert.equal(signal.aborted, true); assert.match(cancelled.state.adcView.message, /cancelled/); cancelled.dispose();
  const app = harness(); await app.session.load(); let attempts = 0;
  app.drafts.command = async () => { attempts++; app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Reply lost; reconcile."; return false; };
  assert.equal(await app.session.command({kind: "adc_set", node_id: "sensor", entry: {reference: "&adc0", cells: [0]}}), false);
  assert.match(app.state.adcView.error, /Reply lost/); assert.equal(await app.session.reconcile(), true); assert.equal(attempts, 1);
  assert.match(app.state.adcView.message, /Load ADC channels again/); app.dispose();
});

test("ADC input rejects overflow, expressions and ambiguous values while retaining literal channel zero", () => {
  const [provider] = fixture().providers;
  assert.deepEqual(adcEntry(provider, "0x1f"), {reference: "&adc0", cells: [31]});
  assert.deepEqual(adcEntry(provider, "0"), {reference: "&adc0", cells: [0]});
  for (const bad of ["32", "-1", "CHANNEL(2)", "1 2", "", "4294967296"]) assert.throws(() => adcEntry(provider, bad));
  assert.throws(() => adcEntry({...provider, supported: false}, "1"), /supported local/);
  assert.throws(() => adcEntry({...provider, cells: ["input", "flags"]}, "1"), /layout/);
  assert.equal(adcChannel("15"), 15);
});

test("ADC configuration requires explicit binding enum selections and bounded literal acquisition", () => {
  const values = {gain: "ADC_GAIN_1", reference: "ADC_REF_INTERNAL", acquisition_time: "0xffff"};
  assert.deepEqual(adcConfig(fields(), values), {...values, acquisition_time: 65535});
  assert.equal(adcConfig(fields(), {...values, acquisition_time: "0"}).acquisition_time, 0);
  for (const bad of ["65536", "-1", "ADC_ACQ_TIME_DEFAULT", "", "1 2"]) assert.throws(() => adcConfig(fields(), {...values, acquisition_time: bad}));
  assert.throws(() => adcConfig(fields(), {...values, gain: ""}), /explicit gain/);
  assert.throws(() => adcConfig(fields(), {...values, reference: "Unknown"}), /explicit reference/);
});

test("ADC selection identity survives revised occurrence ids", () => {
  const model = fixture(); const before = adcRows(model).map((row) => row.key);
  model.providers[0].id = "new-provider-id"; model.channels[0].node_id = "new-channel-id"; model.consumers[0].node_id = "new-consumer-id";
  assert.deepEqual(adcRows(model).map((row) => row.key), before);
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
    return this.attributes.role === "status" && this.ownerDocument.feedbackBounds
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

test("ADC consumer editing submits one local provider cell and displays hardware uncertainty", async () => {
  const app = harness({mount: true}); await click(app.root, "Load ADC channels"); await click(app.root, "ADC consumer · /sensor");
  const form = named(app.root, "form", "Edit ADC specifier"); change(field(form, "ADC provider"), "&adc2"); field(form, "Channel ID").value = "0x1f";
  await click(app.root, "Apply ADC to draft");
  assert.deepEqual(app.commands[0], {kind: "adc_set", node_id: "sensor", entry: {reference: "&adc2", cells: [31]}, schema_id: "schema0"});
  assert.match(app.root.textContent, /Local provider status: disabled/); assert.match(app.root.textContent, /electrical suitability remain unknown/); app.dispose();
});

test("ADC form values and viewport survive passive renders and pending canonical typing", async () => {
  const app = harness({mount: true}); await click(app.root, "Load ADC channels"); await click(app.root, "ADC consumer · /sensor");
  const form = named(app.root, "form", "Edit ADC specifier"); const channel = field(form, "Channel ID"); channel.value = "7"; channel.focus(); app.root.scrollTop = 600;
  app.panel.render(); assert.equal(named(app.root, "form", "Edit ADC specifier"), form); assert.equal(channel.value, "7");
  app.type(source + "/* pending */"); assert.equal(named(app.root, "form", "Edit ADC specifier"), form);
  assert.equal(channel.disabled, true); assert.equal(channel.value, "7"); assert.equal(app.root.scrollTop, 600); assert.equal(app.reveals.length, 1); app.dispose();
});

test("ADC refresh retains the current form and shows its result beside the trigger", async () => {
  const wait = deferred(); let reads = 0;
  const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
  await click(app.root, "Load ADC channels"); await click(app.root, "ADC consumer · /sensor"); app.reveals.length = 0;
  const form = named(app.root, "form", "Edit ADC specifier"); const channel = field(form, "Channel ID"); channel.value = "3";
  await click(app.root, "Apply ADC to draft"); assert.equal(named(app.root, "form", "Edit ADC specifier"), form);
  assert.equal(channel.disabled, true); assert.equal(channel.value, "3"); assert.match(form.textContent, /Refreshing ADC/);
  wait.resolve(); await settle(); assert.match(named(app.root, "form", "Edit ADC specifier").textContent, /Draft updated/);
  assert.deepEqual(app.reveals, []); assert.equal(app.root.ownerDocument.activeElement.textContent, "Apply ADC to draft"); app.dispose();
});

test("ADC input errors remain beside the submitting form and leave source unchanged", async () => {
  const app = harness({mount: true}); await click(app.root, "Load ADC channels"); await click(app.root, "ADC consumer · /sensor"); app.reveals.length = 0;
  const form = named(app.root, "form", "Edit ADC specifier"); field(form, "Channel ID").value = "32";
  await click(app.root, "Apply ADC to draft"); assert.match(form.textContent, /0 to 31/); assert.equal(app.commands.length, 0);
  assert.deepEqual(app.reveals, []); assert.equal(app.state.draftView.entry.text, source); app.dispose();
});

test("ADC channel creation requires explicit values and selects the new channel for editing", async () => {
  const app = harness({mount: true}); await click(app.root, "Load ADC channels"); await click(app.root, "ADC provider · /adc");
  const form = named(app.root, "form", "Add ADC channel");
  assert.equal(field(form, "Gain").value, ""); assert.equal(field(form, "Reference").value, ""); assert.equal(field(form, "Encoded acquisition").value, "");
  field(form, "Channel ID").value = "10"; await click(app.root, "Add ADC channel to draft");
  assert.match(form.textContent, /explicit gain/); assert.equal(app.commands.length, 0);
  change(field(form, "Gain"), "ADC_GAIN_1"); change(field(form, "Reference"), "ADC_REF_INTERNAL"); field(form, "Encoded acquisition").value = "0";
  await click(app.root, "Add ADC channel to draft");
  assert.deepEqual(app.commands[0], {kind: "adc_channel_add", parent_id: "adc0", channel: 10, gain: "ADC_GAIN_1", reference: "ADC_REF_INTERNAL", acquisition_time: 0, schema_id: "schema0"});
  assert.ok(named(app.root, "form", "Edit ADC channel configuration")); assert.match(app.root.textContent, /Channel ID: 10/); app.dispose();
});

test("ADC channel editing preserves identity and submits only its three supported settings", async () => {
  const app = harness({mount: true}); await click(app.root, "Load ADC channels"); await click(app.root, "ADC channel · /adc/channel@1");
  const form = named(app.root, "form", "Edit ADC channel configuration"); assert.equal(named(form, "label", "Channel ID"), undefined);
  assert.equal(field(form, "Gain").value, "ADC_GAIN_1_6"); assert.equal(field(form, "Encoded acquisition").value, "0");
  change(field(form, "Reference"), "ADC_REF_EXTERNAL0"); field(form, "Encoded acquisition").value = "0x4001";
  await click(app.root, "Apply ADC channel to draft");
  assert.deepEqual(app.commands[0], {kind: "adc_channel_set", node_id: "channel1", gain: "ADC_GAIN_1_6", reference: "ADC_REF_EXTERNAL0", acquisition_time: 16385, schema_id: "schema0"});
  assert.match(app.root.textContent, /every consumer using this provider and channel/);
  assert.match(app.root.textContent, /Consumers outside this overlay may be unknown/);
  assert.match(app.root.textContent, /Optional settings remain in source/); app.dispose();
});

test("unsupported ADC consumers, channels and providers explain source access without editable forms", async () => {
  const app = harness({mount: true}); app.model.consumers[0].editable = false; app.model.consumers[0].reason = "Multiple ADC specifiers require source editing.";
  app.model.channels[0].editable = false; app.model.channels[0].reason = "Symbolic acquisition time requires source editing.";
  app.model.providers.push({reference: "&odd", path: "/odd", cells: ["custom"], supported: false, reason: "Custom cell layout."});
  await click(app.root, "Load ADC channels"); await click(app.root, "ADC consumer · /sensor");
  assert.match(app.root.textContent, /Multiple ADC specifiers/); assert.match(app.root.textContent, /Custom cell layout/);
  assert.equal(named(app.root, "button", "Apply ADC to draft"), undefined);
  await click(app.root, "ADC channel · /adc/channel@1"); assert.match(app.root.textContent, /Symbolic acquisition time/);
  assert.equal(named(app.root, "button", "Apply ADC channel to draft"), undefined);
  await click(app.root, "ADC provider · /adc2"); assert.match(app.root.textContent, /Child binding is unresolved/);
  assert.equal(named(app.root, "button", "Add ADC channel to draft"), undefined); assert.equal(app.commands.length, 0); app.dispose();
});

test("empty ADC topology and imported descriptions are rendered as plain text", async () => {
  const app = harness({mount: true}); app.model.consumers = []; app.model.channels = []; app.model.providers = [];
  app.model.diagnostics = ['<img src=x onerror="bad()"> Unsupported evidence'];
  await click(app.root, "Load ADC channels"); assert.match(app.root.textContent, /No local ADC providers/);
  assert.match(app.root.textContent, /<img src=x/); assert.equal(app.root.querySelectorAll("img").length, 0); app.dispose();
});

test("ADC channel removal delegates one review with visible progress and no immediate source command", async () => {
  const wait = deferred(); const reviews = [];
  const app = harness({mount: true, onRemove: async (...args) => { reviews.push(args); await wait.promise; }});
  await click(app.root, "Load ADC channels"); await click(app.root, "ADC channel · /adc/channel@1");
  const trigger = await click(app.root, "Review ADC channel removal"); assert.equal(trigger.textContent, "Preparing removal review…");
  trigger.dispatch("click"); assert.equal(reviews.length, 1); assert.equal(reviews[0][0], "channel1"); assert.equal(reviews[0][1], trigger);
  assert.equal(app.commands.length, 0); wait.resolve(); await settle(); assert.equal(trigger.textContent, "Review ADC channel removal"); app.dispose();
});

test("ADC navigation and disposal clear inspection and ignore late removal errors", async () => {
  let reject; const wait = new Promise((_resolve, no) => { reject = no; });
  const app = harness({mount: true, onRemove: () => wait}); await click(app.root, "Load ADC channels"); await click(app.root, "ADC consumer · /sensor");
  await click(app.root, "Review ADC consumer removal"); app.panel.navigate(); assert.equal(app.state.adcView.data, null);
  reject(new Error("Old context")); await settle(); assert.equal(app.state.adcView.error, "");
  const count = app.calls.length; app.dispose(); named(app.root, "button", "Load ADC channels").dispatch("click"); await settle(); assert.equal(app.calls.length, count);
});


test("an explicit ADC form error reveals offscreen feedback once without passive scrolling or focus changes", async () => {
  const app = harness({mount: true}); await click(app.root, "Load ADC channels"); await click(app.root, "ADC channel · /adc/channel@1");
  app.reveals.length = 0; app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400};
  const form = named(app.root, "form", "Edit ADC channel configuration"); field(form, "Encoded acquisition").value = "65536";
  const trigger = await click(app.root, "Apply ADC channel to draft");
  assert.equal(app.reveals.length, 1); assert.equal(app.reveals[0].node.parentNode, form);
  assert.match(app.reveals[0].node.textContent, /0 to 65535/);
  assert.deepEqual(app.reveals[0].options, {focus: false, block: "nearest"});
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.commands.length, 0);
  app.panel.render(); app.panel.render(); assert.equal(app.reveals.length, 1); app.dispose();
});

test("ADC async results reveal offscreen feedback only for the still-current explicit action", async () => {
  for (const action of ["current", "source", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); let reads = 0;
    const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
    await click(app.root, "Load ADC channels"); await click(app.root, "ADC consumer · /sensor"); app.reveals.length = 0;
    app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400};
    await click(app.root, "Apply ADC to draft"); app.panel.render(); assert.equal(app.reveals.length, 0, "pending renders do not reveal");
    if (action === "source") app.type(source + "/* pending */");
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.panel.navigate();
    if (action === "dispose") app.dispose();
    wait.resolve(); await settle();
    assert.equal(app.reveals.length, action === "current" ? 1 : 0, action);
    if (action === "current") {
      assert.match(app.reveals[0].node.textContent, /Draft updated/);
      assert.deepEqual(app.reveals[0].options, {focus: false, block: "nearest"});
      assert.equal(app.root.ownerDocument.activeElement.textContent, "Apply ADC to draft");
      app.panel.render(); assert.equal(app.reveals.length, 1);
    }
    app.dispose();
  }
});


test("successful ADC channel creation focuses the new result only when focus has not moved elsewhere", async () => {
  for (const moved of [false, true]) {
    const wait = deferred(); let reads = 0;
    const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
    await click(app.root, "Load ADC channels"); await click(app.root, "ADC provider · /adc");
    const form = named(app.root, "form", "Add ADC channel"); field(form, "Channel ID").value = "2";
    change(field(form, "Gain"), "ADC_GAIN_1"); change(field(form, "Reference"), "ADC_REF_INTERNAL"); field(form, "Encoded acquisition").value = "0";
    await click(app.root, "Add ADC channel to draft");
    const other = new Element("button", app.root.ownerDocument);
    if (moved) other.focus();
    wait.resolve(); await settle();
    const result = named(app.root, "form", "Edit ADC channel configuration").parentNode;
    assert.equal(app.root.ownerDocument.activeElement, moved ? other : result);
    if (!moved) assert.deepEqual(result.focusOptions, {preventScroll: true});
    app.dispose();
  }
});
