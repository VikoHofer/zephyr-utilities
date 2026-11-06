import test from "node:test";
import assert from "node:assert/strict";
import {createCANSession, canRows, canValues} from "../../src/zephyr_utilities/features/devicetree/web/can.js";
import {mountCANPanel} from "../../src/zephyr_utilities/features/devicetree/web/can-view.js";

const source = '/ { can0: can { compatible = "zephyr,can-loopback"; bitrate = <500000>; sample-point = <875>; phys = <&phy0>; }; };\n';
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const settle = () => new Promise((resolve) => setImmediate(resolve));
const fieldMetadata = (name, type, value) => ({name, type, editable: true, local: value === null ? null : {parsed: value}});
function fixture() {
  const transceivers = [{id: "phy0", node_id: "phy0", name: "phy", path: "/phy", reference: "&phy0", labels: ["phy0"],
    compatible: "can-transceiver-gpio", status: "enabled", min_bitrate: 10000, max_bitrate: 1000000, supported: true, reason: ""},
  {id: "phy1", node_id: "phy1", name: "phy1", path: "/phy1", reference: "&phy1", compatible: "can-transceiver-gpio",
    status: "disabled", min_bitrate: null, max_bitrate: 250000, supported: true, reason: ""}];
  return {nodes: [], targets: [], diagnostics: [], transceivers,
    controllers: [{node_id: "can0", name: "can", path: "/can", profile_id: "zephyr-can-loopback", compatible: "zephyr,can-loopback",
      status: "enabled", editable: true, reason: "", nominal_rate: {value: 500000}, data_rate: {value: null}, bitrate: 500000, sample_point: 875, phys: "&phy0", passive_transceiver: null,
      limits: {source: "phys", min_bitrate: 10000, max_bitrate: 1000000, reason: ""}, notes: [],
      fields: {bitrate: fieldMetadata("bitrate", "int", 500000), "sample-point": fieldMetadata("sample-point", "int", 875),
        phys: {...fieldMetadata("phys", "phandle", "&phy0"), providers: transceivers}}}]};
}
function harness({mount = false, wrap = (api) => api, onRemove} = {}) {
  const state = {data: {workspace: "/workspace", id: "context", revision: 1}, draftView: {ready: true,
    entry: {generation: 0, text: source, path: "/workspace/app/app.overlay",
      server: {id: "draft", document_id: "document", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel;
  const calls = []; const commands = []; const reveals = []; const toasts = []; const model = fixture();
  const response = () => ({...structuredClone(model), schema_id: "schema" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const publish = () => { session?.sync(); panel?.render(); };
  const api = wrap(async (url, options) => { calls.push({url, ...options}); return response(); });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(structuredClone(command)); const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      const controller = model.controllers.find((row) => row.node_id === command.node_id);
      for (const [name, value] of Object.entries(command.values)) {
        controller.fields[name].local = {parsed: value}; controller[name.replaceAll("-", "_")] = value;
        if (name === "bitrate") controller.nominal_rate.value = value;
      }
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"};
      entry.text = entry.server.text; publish(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; publish(); return true; }};
  const options = {api, state, workspace: () => workspace, drafts, onChange: publish}; const root = mount ? new Element("section") : null;
  if (mount) panel = mountCANPanel(root, {...options, onRemove, notify: (message, error) => toasts.push({message, error}), reveal: (...args) => reveals.push(args)});
  else session = createCANSession(options);
  return {state, options, root, panel, session, model, calls, commands, response, drafts, reveals, toasts,
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

function group(root, name) { return [...root.querySelectorAll("fieldset")].find((node) => node.children[0]?.textContent === name); }
function selectField(root, name, value) {
  const target = group(root, name); const include = field(target, "Set in this action");
  assert.equal(include.disabled, false, name + " can be selected"); include.checked = true; include.dispatch("change");
  const control = [...target.querySelectorAll("input, select")].find((node) => node !== include);
  assert.equal(control.disabled, false, name + " enabled"); if (value !== undefined) change(control, value); return control;
}
async function controller(app) { await click(app.root, "Load CAN controllers"); return click(app.root, "CAN controller · /can"); }

test("CAN inspection is explicit and bound to the canonical draft without mutation", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); assert.equal(await app.session.load(), true);
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/can");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("CAN commands include observation identity and stop authorizing changes after source typing", async () => {
  const app = harness(); await app.session.load(); const command = {kind: "can_controller_set", node_id: "can0", values: {bitrate: 250000}};
  assert.equal(await app.session.command(command), true); assert.deepEqual(app.commands[0], {...command, schema_id: "schema0"});
  const observed = app.state.canView.data; assert.equal(observed.revision, 1);
  app.type(source + "/* pending */"); assert.equal(app.state.canView.data, null); assert.equal(app.state.canView.presentation, observed);
  assert.equal(await app.session.command(command), false); assert.equal(app.commands.length, 1); app.dispose();
});

test("incomplete CAN responses and malformed source cannot enable a command", async () => {
  const app = harness({wrap: (api) => async (...args) => { const result = await api(...args); delete result.transceivers; return result; }});
  assert.equal(await app.session.load(), false); assert.match(app.state.canView.error, /does not match/);
  app.state.draftView.entry.server.syntax.blocked = true; assert.equal(await app.session.load(), false);
  assert.equal(app.calls.length, 1); assert.equal(app.commands.length, 0); app.dispose();
});

test("late CAN observations cannot cross source, context, workspace, navigation or disposal", async () => {
  for (const action of ["source", "context", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); const app = harness({wrap: (api) => async (...args) => { const data = await api(...args); await wait.promise; return data; }});
    const pending = app.session.load();
    if (action === "source") app.type(source + "/* pending */");
    if (action === "context") app.state.data.revision++;
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.session.clear();
    if (action === "dispose") app.dispose();
    wait.resolve(); assert.equal(await pending, false, action); assert.equal(app.state.canView.data, null); app.dispose();
  }
});

test("CAN cancellation aborts the request and uncertain reconciliation never repeats a mutation", async () => {
  const wait = deferred(); let signal;
  const cancelled = harness({wrap: (api) => async (...args) => { signal = args[1].signal; const data = await api(...args); await wait.promise; return data; }});
  const pending = cancelled.session.load(); cancelled.session.cancel(); wait.resolve(); assert.equal(await pending, false);
  assert.equal(signal.aborted, true); assert.match(cancelled.state.canView.message, /cancelled/); cancelled.dispose();
  const app = harness(); await app.session.load(); let attempts = 0;
  app.drafts.command = async () => { attempts++; app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Reply lost; reconcile."; return false; };
  assert.equal(await app.session.command({kind: "can_controller_set", node_id: "can0", values: {"sample-point": 0}}), false);
  assert.match(app.state.canView.error, /Reply lost/); assert.equal(await app.session.reconcile(), true); assert.equal(attempts, 1);
  assert.match(app.state.canView.message, /Load CAN controllers again/); app.dispose();
});

test("CAN values require an explicit supported subset and retain automatic sample point zero", () => {
  const row = fixture().controllers[0];
  assert.deepEqual(canValues(row, {"sample-point": "0"}), {"sample-point": 0});
  assert.deepEqual(canValues(row, {bitrate: "0xf4240", "sample-point": "999", phys: "&phy0"}), {bitrate: 1000000, "sample-point": 999, phys: "&phy0"});
  assert.deepEqual(canValues({...row, limits: {min_bitrate: null, max_bitrate: null}}, {bitrate: "1"}), {bitrate: 1});
  for (const bitrate of ["0", "1000001", "-1", "CAN_RATE", "1 2", ""]) assert.throws(() => canValues(row, {bitrate}), /1 to 1000000/);
  for (const value of ["1000", "-1", "AUTO", "0,1", ""]) assert.throws(() => canValues(row, {"sample-point": value}), /0 to 999/);
  assert.throws(() => canValues(row, {}), /Select at least one/);
  assert.throws(() => canValues(row, {"bitrate-data": "2000000"}), /supported CAN field/);
  assert.throws(() => canValues(row, {phys: "&missing"}), /supported existing/);
  assert.throws(() => canValues({...row, editable: false, reason: "Ambiguous source"}, {bitrate: "1"}), /Ambiguous source/);
});

test("CAN field restrictions do not prevent editing an independent sample point", () => {
  const row = fixture().controllers[0]; row.fields.bitrate.editable = false; row.fields.bitrate.reason = "Migrate deprecated bus-speed in source.";
  assert.throws(() => canValues(row, {bitrate: "250000"}), /deprecated bus-speed/);
  assert.deepEqual(canValues(row, {"sample-point": "0"}), {"sample-point": 0});
  row.fields["sample-point"].enum = [0, 875]; assert.throws(() => canValues(row, {"sample-point": "900"}), /allowed local binding/);
  row.fields["sample-point"].const = 875; assert.throws(() => canValues(row, {"sample-point": "0"}), /binding constant/);
});

test("CAN row identity survives source occurrence changes and distinguishes evidence kinds", () => {
  const data = fixture(); const before = canRows(data).map((row) => row.key);
  data.controllers[0].node_id = "new-source-id"; data.transceivers[0].node_id = "new-transceiver-id";
  assert.deepEqual(canRows(data).map((row) => row.key), before); assert.equal(new Set(before).size, 3);
});

test("CAN forms start with no selected fields and submit only the explicitly selected sample point", async () => {
  const app = harness({mount: true}); await controller(app);
  const form = named(app.root, "form", "Edit CAN controller settings");
  for (const target of form.querySelectorAll("fieldset")) assert.equal(field(target, "Set in this action").checked, false);
  assert.equal(field(form, "Nominal bitrate").disabled, true); assert.equal(field(form, "Active transceiver").disabled, true);
  await click(app.root, "Apply CAN settings to draft"); assert.equal(app.commands.length, 0); assert.match(form.textContent, /Select at least one/);
  selectField(form, "sample-point", "0"); await click(app.root, "Apply CAN settings to draft");
  assert.deepEqual(app.commands[0], {kind: "can_controller_set", node_id: "can0", values: {"sample-point": 0}, schema_id: "schema0"});
  assert.equal(app.model.controllers[0].bitrate, 500000); assert.equal(app.model.controllers[0].phys, "&phy0");
  assert.match(app.root.textContent, /0 requests automatic/); assert.match(app.root.textContent, /Clock timing feasibility/); app.dispose();
});

test("unselected absent CAN values stay empty and inherited defaults are never prefilled", async () => {
  const app = harness({mount: true}); const row = app.model.controllers[0];
  for (const name of ["bitrate", "sample-point", "phys"]) row.fields[name].local = null;
  row.fields.bitrate.default = 125000; row.bitrate = row.sample_point = row.phys = null;
  await controller(app); const form = named(app.root, "form", "Edit CAN controller settings");
  assert.equal(field(form, "Nominal bitrate").value, ""); assert.equal(field(form, "Sample point").value, ""); assert.equal(field(form, "Active transceiver").value, "");
  assert.match(form.textContent, /No local value; inherited and Kconfig values remain unknown/);
  selectField(form, "bitrate", "250000"); await click(app.root, "Apply CAN settings to draft");
  assert.deepEqual(app.commands[0].values, {bitrate: 250000}); assert.equal(row.sample_point, null); assert.equal(row.phys, null); app.dispose();
});

test("CAN assignment permits an explicit bitrate and local transceiver together without changing transceiver evidence", async () => {
  const app = harness({mount: true}); const before = structuredClone(app.model.transceivers); await controller(app);
  const form = named(app.root, "form", "Edit CAN controller settings"); selectField(form, "bitrate", "250000"); selectField(form, "phys", "&phy1");
  assert.match(group(form, "phys").textContent, /local status: disabled/); assert.match(group(form, "phys").textContent, /maximum bitrate: 250000 bits\/s/);
  await click(app.root, "Apply CAN settings to draft");
  assert.deepEqual(app.commands[0].values, {bitrate: 250000, phys: "&phy1"}); assert.deepEqual(app.model.transceivers, before); app.dispose();
});

test("backend CAN candidate-limit rejection appears beside the submit control without scrolling", async () => {
  const app = harness({mount: true}); await controller(app);
  app.drafts.command = async () => { app.state.draftView.entry.error = "The preserved CAN FD data rate exceeds the selected transceiver limit."; return false; };
  const form = named(app.root, "form", "Edit CAN controller settings"); selectField(form, "bitrate", "250000"); selectField(form, "phys", "&phy1");
  const trigger = await click(app.root, "Apply CAN settings to draft");
  assert.match(form.textContent, /preserved CAN FD data rate/); assert.equal(app.root.ownerDocument.activeElement, trigger);
  assert.equal(app.commands.length, 0); assert.deepEqual(app.reveals, []); assert.deepEqual(app.toasts, []); app.dispose();
});

test("deprecated and opaque dependency fields remain unavailable while the independent sample point stays editable", async () => {
  const app = harness({mount: true}); const row = app.model.controllers[0];
  row.fields.bitrate.editable = false; row.fields.bitrate.reason = "Deprecated bus-speed is preserved; migrate it in source.";
  row.fields.phys.editable = false; row.fields.phys.reason = "Opaque transceiver controls require source editing.";
  await controller(app); const form = named(app.root, "form", "Edit CAN controller settings");
  assert.equal(field(group(form, "bitrate"), "Set in this action").disabled, true);
  assert.equal(field(group(form, "phys"), "Set in this action").disabled, true);
  assert.match(form.textContent, /Deprecated bus-speed/); assert.match(form.textContent, /Opaque transceiver controls/);
  selectField(form, "sample-point", "850"); await click(app.root, "Apply CAN settings to draft");
  assert.deepEqual(app.commands[0].values, {"sample-point": 850}); app.dispose();
});

test("CAN transceiver evidence is read-only and names unknown limits and shared impact", async () => {
  const app = harness({mount: true}); app.model.transceivers[1].supported = false; app.model.transceivers[1].reason = "Missing literal GPIO control.";
  await click(app.root, "Load CAN controllers"); await click(app.root, "CAN transceiver · /phy1");
  const detail = named(app.root, "h4", "/phy1").parentNode;
  assert.equal(detail.querySelectorAll("input, select, form").length, 0); assert.match(detail.textContent, /minimum bitrate: unknown/);
  assert.match(detail.textContent, /Missing literal GPIO control/); assert.match(detail.textContent, /all controllers sharing/);
  assert.equal(named(detail, "button", "Review whole controller removal"), undefined); assert.equal(app.commands.length, 0); app.dispose();
});

test("passive CAN transceiver evidence and active-reference precedence are visible and preserved", async () => {
  const app = harness({mount: true}); const row = app.model.controllers[0];
  row.passive_transceiver = {path: "/can/can-transceiver", min_bitrate: null, max_bitrate: 500000, supported: true};
  row.notes = ["The active phys reference takes precedence over the preserved passive can-transceiver child."];
  const before = structuredClone(row.passive_transceiver); await controller(app);
  assert.match(app.root.textContent, /Preserved passive transceiver: \/can\/can-transceiver/); assert.match(app.root.textContent, /takes precedence/);
  selectField(app.root, "sample-point", "0"); await click(app.root, "Apply CAN settings to draft");
  assert.deepEqual(row.passive_transceiver, before); app.dispose();
});

test("CAN fields, selection and viewport survive passive renders and pending source typing", async () => {
  const app = harness({mount: true}); await controller(app); const form = named(app.root, "form", "Edit CAN controller settings");
  const rate = selectField(form, "bitrate", "125000"); rate.focus(); app.root.scrollTop = 650;
  app.panel.render(); assert.equal(named(app.root, "form", "Edit CAN controller settings"), form); assert.equal(rate.value, "125000");
  app.type(source + "/* pending */"); assert.equal(rate.disabled, true); assert.equal(rate.value, "125000");
  assert.equal(field(group(form, "bitrate"), "Set in this action").checked, true); assert.equal(app.root.scrollTop, 650);
  assert.deepEqual(app.reveals, []); assert.deepEqual(app.toasts, []); app.dispose();
});

test("CAN refresh retains the form while busy and restores equivalent submit focus without scrolling", async () => {
  const wait = deferred(); let reads = 0;
  const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
  await controller(app); const form = named(app.root, "form", "Edit CAN controller settings"); const rate = selectField(form, "bitrate", "250000");
  await click(app.root, "Apply CAN settings to draft"); assert.equal(named(app.root, "form", "Edit CAN controller settings"), form);
  assert.equal(rate.disabled, true); assert.equal(rate.value, "250000"); assert.match(form.textContent, /Refreshing CAN controllers/);
  wait.resolve(); await settle(); assert.match(named(app.root, "form", "Edit CAN controller settings").textContent, /Draft updated/);
  assert.equal(app.root.ownerDocument.activeElement.textContent, "Apply CAN settings to draft");
  assert.deepEqual(app.root.ownerDocument.activeElement.focusOptions, {preventScroll: true}); assert.deepEqual(app.reveals, []); app.dispose();
});

test("CAN selection announces offscreen details without moving viewport or focus", async () => {
  const app = harness({mount: true}); await click(app.root, "Load CAN controllers");
  app.root.ownerDocument.detailBounds = {top: 900, bottom: 1200, left: 0, right: 400}; app.root.scrollTop = 650;
  const trigger = await click(app.root, "CAN controller · /can");
  assert.deepEqual(app.toasts, [{message: "Details for /can are ready below.", error: false}]); assert.deepEqual(app.reveals, []);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.root.scrollTop, 650);
  app.panel.render(); app.panel.render(); assert.equal(app.toasts.length, 1); app.dispose();
});

test("explicit CAN range errors announce offscreen feedback once and preserve focus and source", async () => {
  const app = harness({mount: true}); await controller(app); app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400};
  selectField(app.root, "bitrate", "1000001"); const trigger = await click(app.root, "Apply CAN settings to draft");
  assert.equal(app.toasts.length, 1); assert.match(app.toasts[0].message, /1 to 1000000/); assert.equal(app.toasts[0].error, true);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.commands.length, 0); assert.equal(app.state.draftView.entry.text, source);
  app.panel.render(); app.panel.render(); assert.equal(app.toasts.length, 1); assert.deepEqual(app.reveals, []); app.dispose();
});

test("CAN async feedback only announces the still-current explicit action", async () => {
  for (const action of ["current", "source", "context", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); let reads = 0;
    const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
    await controller(app); selectField(app.root, "sample-point", "0");
    app.root.ownerDocument.feedbackBounds = {top: 820, bottom: 860, left: 0, right: 400};
    await click(app.root, "Apply CAN settings to draft"); app.panel.render(); assert.equal(app.toasts.length, 0);
    if (action === "source") app.type(source + "/* pending */");
    if (action === "context") app.state.data.revision++;
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.panel.navigate();
    if (action === "dispose") app.dispose();
    wait.resolve(); await settle(); assert.equal(app.toasts.length, action === "current" ? 1 : 0, action); assert.deepEqual(app.reveals, []);
    if (action === "current") { assert.match(app.toasts[0].message, /Draft updated/); assert.equal(app.toasts[0].error, false); }
    app.dispose();
  }
});

test("CAN removal reviews the whole controller once and leaves its source unchanged", async () => {
  const wait = deferred(); const reviews = [];
  const app = harness({mount: true, onRemove: async (...args) => { reviews.push(args); await wait.promise; }}); await controller(app);
  const trigger = await click(app.root, "Review whole controller removal"); assert.equal(trigger.textContent, "Preparing removal review…");
  trigger.dispatch("click"); assert.equal(reviews.length, 1); assert.equal(reviews[0][0], "can0"); assert.equal(reviews[0][1], trigger);
  assert.match(app.root.textContent, /whole controller node and its children/); assert.equal(app.commands.length, 0);
  wait.resolve(); await settle(); assert.equal(trigger.textContent, "Review whole controller removal"); app.dispose();
});

test("CAN removal errors and disposed listeners cannot affect a later view", async () => {
  const wait = deferred(); const app = harness({mount: true, onRemove: () => wait.promise}); await controller(app);
  await click(app.root, "Review whole controller removal"); app.panel.navigate(); wait.reject(new Error("Old context")); await settle();
  assert.equal(app.state.canView.error, ""); assert.deepEqual(app.toasts, []);
  const count = app.calls.length; app.dispose(); named(app.root, "button", "Load CAN controllers").dispatch("click"); await settle(); assert.equal(app.calls.length, count);
});

test("empty CAN observations and imported descriptions render safely without forms", async () => {
  const app = harness({mount: true}); app.model.controllers = []; app.model.transceivers = []; app.model.diagnostics = ['<img src=x onerror="bad()"> Unknown CAN evidence.'];
  await click(app.root, "Load CAN controllers"); assert.match(app.root.textContent, /No reviewed local CAN controllers or transceivers/);
  assert.match(app.root.textContent, /<img src=x/); assert.equal(app.root.querySelectorAll("img, form").length, 0); app.dispose();
});


test("CAN nominal checks use effective or selected transceiver limits without constraining sample-only updates", () => {
  const row = fixture().controllers[0];
  assert.throws(() => canValues(row, {bitrate: "9999"}), /nominal bitrate.*explicit limits/);
  assert.deepEqual(canValues(row, {bitrate: "10000"}), {bitrate: 10000});
  assert.throws(() => canValues(row, {phys: "&phy1"}), /nominal bitrate.*explicit limits/);
  assert.deepEqual(canValues(row, {bitrate: "250000", phys: "&phy1"}), {bitrate: 250000, phys: "&phy1"});
  row.nominal_rate.value = 1;
  assert.deepEqual(canValues(row, {"sample-point": "0"}), {"sample-point": 0});
  row.limits = {min_bitrate: null, max_bitrate: null}; assert.deepEqual(canValues(row, {bitrate: "1"}), {bitrate: 1});
});

test("CAN PHY changes validate preserved explicit data rate while absent rates remain unknown", () => {
  const row = fixture().controllers[0]; row.nominal_rate.value = null; row.data_rate.value = 500000;
  assert.throws(() => canValues(row, {phys: "&phy1"}), /preserved CAN FD data rate/);
  assert.deepEqual(canValues(row, {bitrate: "250000"}), {bitrate: 250000}, "nominal-only edits preserve existing FD settings");
  row.data_rate.value = null; assert.deepEqual(canValues(row, {phys: "&phy1"}), {phys: "&phy1"});
  row.data_rate.value = 250000; assert.deepEqual(canValues(row, {phys: "&phy1"}), {phys: "&phy1"});
});

test("known CAN limit errors stay local and never create a draft request or reconciliation state", async () => {
  const app = harness({mount: true}); await controller(app); selectField(app.root, "bitrate", "9999");
  const trigger = await click(app.root, "Apply CAN settings to draft");
  assert.match(named(app.root, "form", "Edit CAN controller settings").textContent, /nominal bitrate.*explicit limits/);
  assert.equal(app.calls.length, 1); assert.equal(app.commands.length, 0); assert.equal(app.state.draftView.entry.uncertain, undefined);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.deepEqual(app.reveals, []); app.dispose();
});
