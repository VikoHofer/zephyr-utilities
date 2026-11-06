import test from "node:test";
import assert from "node:assert/strict";
import {mountBindingPanel} from "../../src/zephyr_utilities/features/devicetree/web/binding-view.js";
import {bindingEnumValue, bindingValue, createBindingSession, localBindingValue, unsignedCells} from "../../src/zephyr_utilities/features/devicetree/web/bindings.js";

const original = '/ { test: device { compatible = "example,device"; }; };\n';
function deferred() { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
function harness(wrap = (api) => api, mount = false) {
  const state = {data: {id: "context", revision: 1, workspace: "/workspace"}, draftView: {ready: true,
    entry: {generation: 0, text: original, server: {id: "draft", revision: 0, sha256: "hash0", text: original, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let panel; let notifications = 0;
  const calls = []; const commands = []; const reveals = []; const toasts = [];
  const response = () => ({schema_id: "schema" + state.draftView.entry.server.revision, draft_id: "draft",
    revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256,
    nodes: [{node_id: "node", name: "device", path: "/device", labels: ["test"],
      binding: {state: "matched", path: "/workspace/dts/bindings/example.yaml"},
      properties: [{name: "rate", type: "int", default: 10, required: true, local: null, editable: true}]}],
    targets: [{id: "node", reference: "&test", label: "test", path: "/device", cells: {gpio: ["pin", "flags"]}}], diagnostics: []});
  const publish = () => { notifications++; session?.sync(); panel?.render(); };
  const api = wrap(async (url, {body}) => { calls.push({url, body}); return response(); });
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(command) {
      commands.push(command);
      const entry = state.draftView.entry;
      entry.server = {...entry.server, text: original + "/* updated */\n", revision: entry.server.revision + 1,
        sha256: "hash" + (entry.server.revision + 1)};
      entry.text = entry.server.text; publish(); return true;
    }};
  const options = {api, state, drafts, workspace: () => workspace, onChange: publish};
  const root = mount ? new Element("section") : null;
  if (mount) panel = mountBindingPanel(root, {...options, notify: (message, error) => toasts.push({message, error}),
    reveal: (...args) => reveals.push(args)});
  else session = createBindingSession(options);
  return {session, panel, root, state, drafts, calls, commands, response, reveals, toasts, notifications: () => notifications,
    workspace(value) { workspace = value; session?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; session?.sync(); panel?.render(); },
    dispose() { session?.dispose(); panel?.dispose(); }};
}

test("binding inspection is explicit and never materializes required/default values", async () => {
  const app = harness(); app.session.sync(); assert.equal(app.calls.length, 0);
  assert.equal(await app.session.load(), true);
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.bindingView.data.nodes[0].properties[0].default, 10);
  assert.equal(app.state.draftView.entry.text, original); assert.deepEqual(app.commands, []); app.dispose();
});

test("binding commands use canonical draft path and reload only after the accepted action", async () => {
  const app = harness(); await app.session.load();
  assert.equal(await app.session.command({kind: "binding_set", node_id: "node", name: "rate", value: 20}), true);
  assert.deepEqual(app.commands, [{kind: "binding_set", node_id: "node", name: "rate", value: 20, schema_id: "schema0"}]);
  assert.equal(app.calls.length, 2); assert.equal(app.calls[1].body.expected_draft_revision, 1);
  assert.equal(app.state.bindingView.data.revision, 1); assert.match(app.state.bindingView.message, /Save remains a separate/); app.dispose();
});

test("typing invalidates loaded schema and prevents applying a stale field", async () => {
  const app = harness(); await app.session.load(); app.type(original + "/* pending */");
  assert.equal(app.state.bindingView.data, null);
  assert.equal(await app.session.command({kind: "binding_reset", node_id: "node", name: "rate"}), false);
  assert.deepEqual(app.commands, []); assert.equal(await app.session.load(), false);
  assert.equal(app.calls.length, 1); app.dispose();
});

test("delayed schema load cannot attach to newer local typing", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { const data = await api(...args); await wait.promise; return data; });
  const loading = app.session.load(); app.type(original + "/* pending */"); wait.resolve();
  assert.equal(await loading, false); assert.equal(app.state.bindingView.data, null); assert.equal(app.state.bindingView.busy, false); app.dispose();
});

test("workspace switches reject delayed metadata without changing another draft", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { const data = await api(...args); await wait.promise; return data; });
  const loading = app.session.load(); app.workspace("/other"); wait.resolve();
  assert.equal(await loading, false); assert.equal(app.state.bindingView.data, null); assert.equal(app.state.draftView.entry.text, original); app.dispose();
});

test("context and canonical revision changes invalidate metadata even if bytes repeat", async () => {
  const app = harness(); await app.session.load(); app.state.data.revision++; app.session.sync();
  assert.equal(app.state.bindingView.data, null);
  await app.session.load(); app.state.draftView.entry.server.revision++; app.session.sync();
  assert.equal(app.state.bindingView.data, null); app.dispose();
});

test("mismatched schema responses never enable transformations", async () => {
  const app = harness((api) => async (...args) => ({...await api(...args), sha256: "another-hash"}));
  assert.equal(await app.session.load(), false); assert.match(app.state.bindingView.error, /does not match/);
  assert.equal(app.state.bindingView.data, null); app.dispose();
});

test("incomplete, uncertain or externally paused drafts cannot load binding forms", async () => {
  const app = harness();
  for (const field of ["uncertain", "conflict", "externalOperation", "historyOperation"]) {
    app.state.draftView.entry[field] = true; assert.equal(await app.session.load(), false); app.state.draftView.entry[field] = false;
  }
  app.state.draftView.entry.server.syntax.blocked = true;
  assert.equal(await app.session.load(), false); assert.equal(app.calls.length, 0); app.dispose();
});

test("missing and ambiguous binding states stay visible without synthesizing fields", async () => {
  for (const bindingState of ["missing", "ambiguous", "unsupported"]) {
    const app = harness((api) => async (...args) => {
      const data = await api(...args); data.nodes[0].binding.state = bindingState; data.nodes[0].properties = [];
      data.nodes[0].binding.diagnostics = ["Select source editing for this schema."]; return data;
    });
    assert.equal(await app.session.load(), true); assert.equal(app.state.bindingView.data.nodes[0].binding.state, bindingState);
    assert.deepEqual(app.state.bindingView.data.nodes[0].properties, []); assert.deepEqual(app.commands, []); app.dispose();
  }
});

test("command rejection keeps useful errors and cannot refresh an unaccepted mutation", async () => {
  const app = harness(); await app.session.load();
  app.drafts.command = async () => { app.state.draftView.entry.error = "The binding changed on disk."; return false; };
  assert.equal(await app.session.command({kind: "binding_delete", node_id: "node", name: "rate"}), false);
  assert.match(app.state.bindingView.error, /binding changed/); assert.equal(app.calls.length, 1); assert.equal(app.state.bindingView.busy, false); app.dispose();
});

test("failed metadata refresh still identifies the accepted draft change", async () => {
  let loads = 0;
  const app = harness((api) => async (...args) => { if (++loads > 1) throw new Error("Schema read failed"); return api(...args); });
  await app.session.load();
  assert.equal(await app.session.command({kind: "binding_reset", node_id: "node", name: "rate"}), false);
  assert.equal(app.state.draftView.entry.server.revision, 1);
  assert.match(app.state.bindingView.message, /draft was updated/); assert.match(app.state.bindingView.error, /Schema read failed/);
  assert.equal(app.state.bindingView.data, null); app.dispose();
});

test("network exceptions during a command are visible and release busy state", async () => {
  const app = harness(); await app.session.load(); app.drafts.command = async () => { throw new Error("Unavailable"); };
  assert.equal(await app.session.command({kind: "add_label", node_id: "node", label: "sensor"}), false);
  assert.equal(app.state.bindingView.error, "Unavailable"); assert.equal(app.state.bindingView.busy, false); app.dispose();
});

test("nearby reconciliation discards stale schemas without repeating a failed action", async () => {
  const app = harness(); await app.session.load(); let reconciliations = 0;
  app.drafts.command = async () => { app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Binding inputs changed."; return false; };
  await app.session.command({kind: "binding_set", node_id: "node", name: "rate", value: 20});
  assert.equal(app.session.needsReconcile(), true); assert.equal(app.session.canReconcile(), true);
  assert.equal(app.state.bindingView.data, null);
  app.drafts.reconcile = async () => { reconciliations++; app.state.draftView.entry.uncertain = false; return true; };
  assert.equal(await app.session.reconcile(), true); assert.equal(reconciliations, 1);
  assert.equal(app.session.needsReconcile(), false); assert.equal(app.state.bindingView.data, null);
  assert.equal(app.calls.length, 1); assert.match(app.state.bindingView.message, /Load binding properties again/);
  assert.equal(await app.session.load(), true); app.dispose();
});

test("reconciliation failures stay nearby and release the recovery action", async () => {
  const app = harness(); app.state.draftView.entry.uncertain = true;
  app.drafts.reconcile = async () => { app.state.draftView.entry.error = "Draft is unavailable"; return false; };
  assert.equal(await app.session.reconcile(), false); assert.equal(app.state.bindingView.error, "Draft is unavailable");
  assert.equal(app.session.canReconcile(), true); assert.equal(app.state.bindingView.busy, false);
  app.state.draftView.entry.externalOperation = true; assert.equal(app.session.canReconcile(), false);
  app.state.draftView.entry.externalOperation = false; app.state.draftView.entry.conflict = true;
  assert.equal(app.session.needsReconcile(), false); app.dispose();
});

test("reconciliation preserves retained typing and does not trigger a schema scan", async () => {
  const app = harness(); app.state.draftView.entry.uncertain = true; app.type(original + "/* retained */");
  app.drafts.reconcile = async () => { app.state.draftView.entry.uncertain = false; return true; };
  assert.equal(await app.session.reconcile(), true); assert.match(app.state.bindingView.message, /Sync retained source/);
  assert.equal(app.state.draftView.entry.text, original + "/* retained */"); assert.equal(app.calls.length, 0); app.dispose();
});

test("late reconciliation cannot update a navigated workspace view", async () => {
  const wait = deferred(); const app = harness(); app.state.draftView.entry.uncertain = true;
  app.drafts.reconcile = async () => wait.promise;
  const checking = app.session.reconcile(); assert.equal(app.session.reconciling(), true);
  app.session.clear(); app.workspace("/other"); const before = app.notifications(); wait.resolve(true);
  assert.equal(await checking, false); assert.equal(app.notifications(), before);
  assert.equal(app.state.bindingView.message, ""); assert.equal(app.state.bindingView.busy, false); app.dispose();
});

test("aliases and chosen updates reuse the guarded command path without saving", async () => {
  const app = harness(); await app.session.load();
  for (const kind of ["set_alias", "set_chosen"]) assert.equal(await app.session.command({kind, name: "test", reference: "&test"}), true);
  assert.deepEqual(app.commands.map((item) => item.kind), ["set_alias", "set_chosen"]);
  assert.equal(app.commands[1].schema_id, "schema1"); assert.ok(app.calls.every((item) => item.url.endsWith("/drafts/bindings"))); app.dispose();
});

test("leaving discards view-local schemas and late requests never publish again", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { await wait.promise; return api(...args); });
  const loading = app.session.load(); const before = app.notifications(); app.dispose(); wait.resolve();
  assert.equal(await loading, false); assert.equal(app.notifications(), before); assert.equal(app.state.bindingView.data, null);
});

test("navigation clearing suppresses an older load while a new view loads", async () => {
  const waits = []; const app = harness((api) => async (...args) => { const value = await api(...args); const wait = deferred(); waits.push(wait); await wait.promise; return value; });
  const first = app.session.load(); await Promise.resolve(); await Promise.resolve();
  app.session.clear(); const second = app.session.load(); await Promise.resolve(); await Promise.resolve();
  waits[1].resolve(); assert.equal(await second, true); const data = app.state.bindingView.data;
  waits[0].resolve(); assert.equal(await first, false); assert.equal(app.state.bindingView.data, data); app.dispose();
});

test("explicit cancellation aborts metadata inspection without touching source", async () => {
  const wait = deferred(); let signal;
  const app = harness((api) => async (...args) => { signal = args[1].signal; const data = await api(...args); await wait.promise; return data; });
  const loading = app.session.load(); assert.equal(app.session.scanning(), true);
  app.session.cancel(); assert.equal(signal.aborted, true); assert.equal(app.session.scanning(), false);
  assert.equal(app.state.bindingView.busy, false); assert.match(app.state.bindingView.message, /cancelled/);
  wait.resolve(); assert.equal(await loading, false); assert.equal(app.state.bindingView.data, null);
  assert.equal(app.state.draftView.entry.text, original); assert.deepEqual(app.commands, []); app.dispose();
});

test("a late command cannot clear a newer view's loading indicator", async () => {
  let pauseInspection = false; const inspection = deferred(); const mutation = deferred();
  const app = harness((api) => async (...args) => { const data = await api(...args); if (pauseInspection) await inspection.promise; return data; });
  await app.session.load(); app.drafts.command = async () => mutation.promise;
  const applying = app.session.command({kind: "binding_reset", node_id: "node", name: "rate"});
  app.session.clear(); pauseInspection = true; const loading = app.session.load();
  mutation.resolve(false); assert.equal(await applying, false);
  assert.equal(app.state.bindingView.busy, true); assert.equal(app.session.scanning(), true);
  inspection.resolve(); assert.equal(await loading, true); assert.equal(app.state.bindingView.busy, false); app.dispose();
});

test("typed values preserve literal strings and enforce numeric bounds", () => {
  assert.equal(bindingValue("string", 'quoted "data"'), 'quoted "data"');
  assert.equal(bindingValue("int", "0x10"), 16);
  assert.deepEqual(bindingValue("array", "0, 4 0xff"), [0, 4, 255]);
  assert.deepEqual(bindingValue("uint8-array", "0 255"), [0, 255]);
  assert.throws(() => bindingValue("uint8-array", "256"), /integers/);
  assert.throws(() => bindingValue("int", "1 2"), /one unsigned/);
  assert.throws(() => unsignedCells("-1"), /integers/);
  assert.throws(() => unsignedCells("1 << 2"), /integers/);
  assert.throws(() => unsignedCells("4294967296"), /integers/);
});

test("string arrays and reference arrays keep explicit entry boundaries", () => {
  assert.deepEqual(bindingValue("string-array", "first\nsecond value"), ["first", "second value"]);
  assert.throws(() => bindingValue("string-array", "one\n\nthree"), /nonempty string/);
  assert.deepEqual(bindingValue("phandles", ["&one", "&two"]), ["&one", "&two"]);
  assert.throws(() => bindingValue("phandles", []), /one or more/);
  assert.throws(() => bindingValue("phandle", ""), /target/);
});

test("provider-specific rows preserve zero cells and heterogeneous cell counts", () => {
  assert.deepEqual(bindingValue("phandle-array", [{reference: "&gpio", cells: ["3", "0x1"]},
    {reference: "&zero", cells: []}]), [{reference: "&gpio", cells: [3, 1]}, {reference: "&zero", cells: []}]);
  assert.throws(() => bindingValue("phandle-array", [{reference: "&gpio", cells: ["", "1"]}]), /integers/);
  assert.throws(() => bindingValue("phandle-array", [{reference: "", cells: []}]), /provider/);
});

test("array enumerations constrain each entry while preserving order and repetitions", () => {
  const numbers = {type: "array", enum: [1, 2, 3]}; const strings = {type: "string-array", enum: ["first", "second"]};
  assert.deepEqual(bindingEnumValue(numbers, [3, 1, 3]), [3, 1, 3]);
  assert.deepEqual(bindingEnumValue(strings, ["second", "first"]), ["second", "first"]);
  assert.throws(() => bindingEnumValue(numbers, 2), /entries/);
  assert.throws(() => bindingEnumValue(numbers, ["2"]), /entries/);
  assert.throws(() => bindingEnumValue(strings, ["first", undefined]), /entries/);
  assert.equal(bindingEnumValue({type: "int", enum: [0, 1]}, 0), 0);
});

test("local schema values do not substitute defaults or fabricate opaque values", () => {
  assert.equal(localBindingValue({type: "int", default: 12, local: null}), undefined);
  assert.equal(localBindingValue({type: "int", local: {value: {kind: "cells", value: [5]}}}), 5);
  assert.equal(localBindingValue({type: "boolean", local: {value: {kind: "boolean", value: null}}}), true);
  assert.deepEqual(localBindingValue({type: "phandle-array", local: {parsed: [{reference: "&provider", cells: [4]}]}}), [{reference: "&provider", cells: [4]}]);
  assert.equal(localBindingValue({type: "int", local: {parsed: null}}), null);
});


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
  getBoundingClientRect() { return this.ownerDocument.offscreen?.(this)
    ? {top: 900, bottom: 1200, left: 0, right: 400} : {top: 20, bottom: 40, left: 0, right: 400}; }

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
const settle = () => new Promise((resolve) => setImmediate(resolve));
const named = (root, tag, text) => Array.from(root.querySelectorAll(tag)).find((node) => node.textContent.startsWith(text));
const field = (root, text) => named(root, "label", text).children[0];
async function click(root, text) {
  const node = named(root, "button", text); assert.ok(node, text); assert.equal(node.disabled, false, text + " enabled");
  node.focus(); node.dispatch("click"); if (node.type === "submit") node.closest("form").dispatch("submit"); await settle(); return node;
}
function change(node, value) { node.value = value; node.dispatch("change"); }


test("adding binding reference and array entries keeps focus and announces only offscreen additions", async () => {
  for (const [type, extra, title, message] of [["phandles", {}, "Add reference entry", "Reference entry added to the property form."],
    ["array", {enum: [1, 2, 3]}, "Add array entry", "Array entry added to the property form."]]) {
    const app = harness((api) => async (...args) => {
      const result = await api(...args); result.nodes[0].properties = [{name: "values", type, editable: true, local: null, ...extra}]; return result;
    }, true);
    await click(app.root, "Load binding properties"); change(field(app.root, "Binding property"), "values");
    app.root.scrollTop = 650; app.root.ownerDocument.offscreen = (node) => node.className === "dte-binding-reference-row";
    const before = app.root.querySelectorAll("select").length; const trigger = await click(app.root, title);
    assert.equal(app.root.querySelectorAll("select").length, before + 1);
    assert.deepEqual(app.toasts, [{message, error: false}]); assert.deepEqual(app.reveals, []);
    assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.root.scrollTop, 650);
    app.panel.render(); app.panel.render(); assert.equal(app.toasts.length, 1);
    app.root.ownerDocument.offscreen = () => false; await click(app.root, title); assert.equal(app.toasts.length, 1);
    assert.equal(app.commands.length, 0); app.dispose();
  }
});
