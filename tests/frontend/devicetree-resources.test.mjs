import test from "node:test";
import assert from "node:assert/strict";
import {createRemovalSession, createResourceSession} from "../../src/zephyr_utilities/features/devicetree/web/resources.js";
import {mountResourcePanel} from "../../src/zephyr_utilities/features/devicetree/web/resource-view.js";
import {mountModulePanel} from "../../src/zephyr_utilities/features/devicetree/web/module-view.js";

const source = '/ { gpio0: gpio { gpio-controller; }; aliases { led = &gpio0; }; };\n';
const location = {node_id: "gpio", property_id: null, path: "/gpio", name: "gpio", start: 4, end: 37};
const aliasLocation = {node_id: "aliases", property_id: "alias", path: "/aliases", name: "led", start: 48, end: 60};
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; };
const settle = () => new Promise((resolve) => setImmediate(resolve));

function harness({mount = false, wrap = (api) => api} = {}) {
  const state = {data: {id: "context", revision: 1, workspace: "/workspace"}, draftView: {ready: true,
    entry: {generation: 0, path: "/workspace/app/app.overlay", text: source,
      server: {id: "draft", document_id: "document", revision: 0, sha256: "hash0", text: source, syntax: {blocked: false}}}}};
  let workspace = "/workspace"; let session; let removal; let panel; let releaseCount = 0;
  const calls = []; const commands = []; const sourceCalls = []; const reveals = [];
  const model = {nodes: [{node_id: "root", key: "node:/", path: "/", name: "/", unique: true},
    {node_id: "gpio", key: "node:/gpio", path: "/gpio", name: "gpio", unique: true},
    {node_id: "leaf", key: "node:/leaf", path: "/leaf", name: "leaf", unique: true}],
    graph: {references: []}, coverage: "literal-local-source", external_unknown: true,
    diagnostics: [{id: "conflict", code: "gpio-conflict", severity: "error", message: "Two active consumers use the same exclusive GPIO.", locations: [location], repair_choices: []},
      {id: "unknown", code: "external-unknown", severity: "unknown", message: "Inherited consumers remain unknown.", locations: [], repair_choices: []}]};
  const response = () => ({...structuredClone(model), schema_version: 1, schema_id: "schema" + state.draftView.entry.server.revision,
    draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256});
  const notify = () => { session?.sync(); removal?.sync(); panel?.render(); };
  const api = wrap(async (url, options) => {
    calls.push({url, ...options});
    if (url.endsWith("removal/review")) {
      const {node_id: nodeId, repairs, schema_id: schema} = options.body; const leaf = nodeId === "leaf"; const selected = repairs.includes("repair-alias");
      return {schema_version: 1, review_id: "review" + calls.length, schema_id: schema,
        draft_id: "draft", revision: state.draftView.entry.server.revision, sha256: state.draftView.entry.server.sha256,
        target: {node_id: nodeId, name: nodeId, path: "/" + nodeId, location: {...location, node_id: nodeId}}, descendants: [],
        dependants: leaf ? [] : [{id: "dependency", reference: "&gpio0", source_node_id: "aliases", property_id: "alias", location: aliasLocation,
          kind: "alias", repair_id: "repair-alias", message: "The led alias refers to this GPIO provider."}],
        repair_choices: leaf ? [] : [{id: "repair-alias", property_id: "alias", label: "Remove local led from /aliases", location: aliasLocation,
          consequence: "Only this alias property will be removed.", selected}],
        blockers: leaf || selected ? [] : [{message: "Select the explicit alias repair or keep this provider.", location: aliasLocation}],
        unknowns: ["External consumers remain unknown."], can_apply: leaf || selected,
        diff: {text: leaf || selected ? "- removed declaration\n" : "", truncated: false},
        candidate_text: leaf || selected ? "/ { };\n" : null, expires_at: new Date(Date.now() + 600000).toISOString()};
    }
    return response();
  });
  const drafts = {
    pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    pause() {
      const entry = state.draftView.entry; if (entry.externalOperation) return null;
      const token = {}; entry.externalOperation = token; notify();
      return () => { if (entry.externalOperation === token) { delete entry.externalOperation; releaseCount++; notify(); } };
    },
    async command(command) {
      assert.equal(state.draftView.entry.externalOperation, undefined); commands.push(structuredClone(command));
      const entry = state.draftView.entry; const revision = entry.server.revision + 1;
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: "/ { };\n"}; entry.text = entry.server.text; notify(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; notify(); return true; },
  };
  const options = {api, state, workspace: () => workspace, drafts, onChange: notify};
  const root = mount ? new Element("section") : null;
  if (mount) panel = mountResourcePanel(root, {...options, reveal: (...args) => reveals.push(args), onSource: (...args) => sourceCalls.push(args)});
  else {
    session = createResourceSession(options);
    removal = createRemovalSession({...options, resources: session});
  }
  return {state, options, root, session, removal, panel, calls, commands, drafts, model, response, reveals, sourceCalls,
    get releaseCount() { return releaseCount; },
    workspace(value) { workspace = value; panel?.navigate(); notify(); },
    type(text) { state.draftView.entry.text = text; state.draftView.entry.generation++; notify(); },
    dispose() { session?.dispose(); removal?.dispose(); panel?.dispose(); },
  };
}

test("resource inspection is explicit, guarded and never mutates source", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); assert.equal(await app.session.load(), true);
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/resources");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("incomplete source permits read-only resource inspection but never a command or removal", async () => {
  const app = harness(); app.state.draftView.entry.server.syntax.blocked = true;
  assert.equal(await app.session.load(), true);
  assert.equal(await app.session.command({kind: "remove_reviewed", review_id: "fake"}), false);
  assert.equal(await app.removal.begin("gpio"), false); assert.equal(app.commands.length, 0); app.dispose();
});

test("old diagnostics are retained as non-actionable presentation after typing", async () => {
  const app = harness(); await app.session.load(); const original = app.state.resourceView.data;
  app.type(source + "/* draft */"); assert.equal(app.state.resourceView.data, null);
  assert.equal(app.state.resourceView.presentation, original); assert.match(app.state.resourceView.message, /do not block/);
  assert.equal(await app.removal.begin("gpio"), false); app.dispose();
});

test("late resource responses cannot cross context or workspace changes", async () => {
  const wait = deferred(); const app = harness({wrap: (api) => async (...args) => { const data = await api(...args); await wait.promise; return data; }});
  const load = app.session.load(); app.workspace("/other"); wait.resolve(); assert.equal(await load, false);
  assert.equal(app.state.resourceView.data, null); assert.equal(app.commands.length, 0); app.dispose();
});

test("removal review locks source and shows dependencies without applying repairs", async () => {
  const app = harness(); await app.session.load(); assert.equal(await app.removal.begin("gpio"), true);
  const request = app.calls.at(-1); assert.equal(request.url, "/api/devicetree/editor/drafts/removal/review");
  assert.equal(request.body.schema_id, "schema0"); assert.equal(request.body.node_id, "gpio"); assert.deepEqual(request.body.repairs, []);
  assert.equal(app.state.removalView.review.data.dependants.length, 1); assert.equal(app.removal.canApply(), false);
  assert.ok(app.state.draftView.entry.externalOperation); assert.equal(app.commands.length, 0);
  app.removal.cancel(); assert.equal(app.releaseCount, 1); assert.equal(app.state.draftView.entry.externalOperation, undefined); app.dispose();
});

test("explicit repairs require a fresh preview and apply one reviewed canonical command", async () => {
  const app = harness(); await app.session.load(); await app.removal.begin("gpio");
  assert.equal(app.removal.select("repair-alias", true), true); assert.equal(app.removal.canApply(), false);
  assert.equal(app.commands.length, 0); await app.removal.update(); assert.deepEqual(app.calls.at(-1).body.repairs, ["repair-alias"]);
  const reviewId = app.state.removalView.review.data.review_id;
  assert.equal(app.removal.canApply(), true); assert.equal(await app.removal.apply(), true);
  assert.deepEqual(app.commands, [{kind: "remove_reviewed", review_id: reviewId}]); assert.equal(app.releaseCount, 1);
  assert.equal(app.state.resourceView.data, null); assert.ok(app.state.removalView.outcome); app.dispose();
});

test("invalid, expired and changed-source reviews cannot apply", async () => {
  const invalid = harness({wrap: (api) => async (...args) => { const data = await api(...args); if (args[0].endsWith("/review")) data.sha256 = "wrong"; return data; }});
  await invalid.session.load(); assert.equal(await invalid.removal.begin("leaf"), false); assert.equal(invalid.releaseCount, 1);
  assert.match(invalid.state.removalView.error, /does not match/); invalid.dispose();
  const app = harness(); await app.session.load(); await app.removal.begin("leaf");
  app.state.removalView.review.data.expires_at = new Date(0).toISOString(); assert.equal(app.removal.canApply(), false);
  assert.equal(await app.removal.apply(), false); assert.equal(app.commands.length, 0);
  app.type(source + "/* new text */"); assert.equal(app.state.removalView.review, null); assert.equal(app.releaseCount, 1); app.dispose();
});

test("cancelled and late previews do not reopen reviews or retain source locks", async () => {
  const wait = deferred(); const app = harness({wrap: (api) => async (...args) => { const data = await api(...args); if (args[0].endsWith("/review")) await wait.promise; return data; }});
  await app.session.load(); const pending = app.removal.begin("gpio"); app.removal.cancel(); wait.resolve();
  assert.equal(await pending, false); assert.equal(app.state.removalView.review, null); assert.equal(app.releaseCount, 1); app.dispose();
});

test("failed repair previews retain the previous review but prevent applying it", async () => {
  const app = harness({wrap: (api) => async (...args) => { if (args[0].endsWith("/review") && args[1].body.repairs.length) throw new Error("Binding changed"); return api(...args); }});
  await app.session.load(); await app.removal.begin("gpio"); app.removal.select("repair-alias", true);
  assert.equal(await app.removal.update(), false); assert.ok(app.state.removalView.review); assert.equal(app.removal.canApply(), false);
  assert.match(app.state.removalView.error, /Binding changed/); app.removal.cancel(); assert.equal(app.releaseCount, 1); app.dispose();
});

test("an uncertain apply keeps its error visible and cannot be retried automatically", async () => {
  const app = harness(); await app.session.load(); await app.removal.begin("leaf");
  let attempts = 0;
  app.drafts.command = async () => { attempts++; app.state.draftView.entry.uncertain = true;
    app.state.draftView.entry.error = "Reply lost. Reconcile the current draft."; return false; };
  assert.equal(await app.removal.apply(), false); app.removal.sync();
  assert.ok(app.state.removalView.review); assert.match(app.state.removalView.error, /Reply lost/);
  assert.equal(app.removal.canApply(), false); assert.equal(await app.removal.apply(), false); assert.equal(attempts, 1);
  app.removal.cancel(); assert.equal(app.releaseCount, 1); app.dispose();
});

// Small browser-like DOM: NodeLists have no Array helpers and native dialog events do not bubble.
class Element {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = document || {activeElement: null,
      createElement: (name) => new Element(name, this.ownerDocument)};
    if (!document) this.ownerDocument.root = this;
    this.children = []; this.parentNode = null; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.classList = {toggle() {}}; this.style = {}; this._text = ""; this._value = null; this.disabled = false; this.hidden = false;
    this.checked = false; this.type = ""; this.open = false; this.scrollTop = 0;
  }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set value(value) { this._value = String(value); }
  get value() { return this._value ?? (this.tagName === "SELECT" ? this.children[0]?.value || "" : ""); }
  get isConnected() { return this === this.ownerDocument.root || Boolean(this.parentNode?.isConnected); }
  append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((item) => item !== this); this.parentNode = null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  focus(options) { if (this.disabled || this.hidden) return; this.ownerDocument.activeElement = this; this.focusOptions = options; }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatch("close"); }
  addEventListener(type, callback, options) {
    (this.listeners[type] ||= []).push(callback);
    options?.signal?.addEventListener("abort", () => { this.listeners[type] = this.listeners[type].filter((item) => item !== callback); });
  }
  matches(selector) { return selector === "[hidden]" ? this.hidden : this.tagName.toLowerCase() === selector; }
  querySelectorAll(selector) {
    const tags = selector.split(",").map((value) => value.trim());
    const nodes = this.children.flatMap((child) => [...(tags.some((tag) => child.matches(tag)) ? [child] : []), ...child.querySelectorAll(selector)]);
    return Object.assign({length: nodes.length, [Symbol.iterator]: () => nodes[Symbol.iterator]()}, nodes);
  }
  closest(selector) { for (let item = this; item; item = item.parentNode) if (item.matches(selector)) return item; return null; }
  dispatch(type) {
    const event = {target: this, cancelBubble: false, preventDefault() { this.defaultPrevented = true; }};
    for (let item = this; item; item = ["cancel", "close"].includes(type) ? null : item.parentNode) for (const callback of item.listeners[type] || []) callback(event);
    return event;
  }
}
const named = (root, tag, text) => Array.from(root.querySelectorAll(tag)).find((node) => node.textContent.startsWith(text));
const field = (root, text) => named(root, "label", text).children[0];
async function click(root, text) {
  const node = named(root, "button", text); assert.ok(node, text); assert.equal(node.disabled, false, text + " enabled");
  node.focus(); node.dispatch("click"); await settle(); return node;
}
function change(node, value) { node.value = value; node.dispatch("change"); }

test("diagnostic selection reveals its source once and stale results cannot navigate", async () => {
  const app = harness({mount: true}); await click(app.root, "Load diagnostics");
  await click(app.root, "Inspect source: /gpio"); assert.equal(app.sourceCalls.length, 1);
  assert.deepEqual(app.sourceCalls[0][0], location); assert.equal(app.sourceCalls[0][1].revision, 0); assert.equal(app.reveals.length, 0);
  app.panel.render(); assert.equal(app.sourceCalls.length, 1);
  app.type(source + "/* changed */"); assert.equal(named(app.root, "button", "Inspect source: /gpio").disabled, true);
  assert.match(app.root.textContent, /earlier draft/); assert.equal(app.commands.length, 0); app.dispose();
});

test("filters keep unknown evidence distinct and imported messages remain plain text", async () => {
  const app = harness({mount: true}); app.model.diagnostics[1].message = '<img src=x onerror="bad()"> unknown';
  await click(app.root, "Load diagnostics"); assert.match(app.root.textContent, /1 conflicts.*1 unknown/);
  change(field(app.root, "Diagnostic category"), "unknown");
  assert.equal(Array.from(app.root.querySelectorAll("article")).length, 1);
  assert.match(app.root.textContent, /<img src=x/); assert.equal(app.root.querySelectorAll("img").length, 0);
  change(field(app.root, "Diagnostic category"), "warning"); assert.match(app.root.textContent, /No diagnostics in this category/); app.dispose();
});

test("conflicts precede incomplete evidence and duplicate repair locations have one source action", async () => {
  const app = harness({mount: true}); app.model.diagnostics.reverse();
  app.model.diagnostics[1].repair_choices = [{kind: "pins", label: "Inspect pins", location}];
  await click(app.root, "Load diagnostics");
  const rows = Array.from(app.root.querySelectorAll("article")); assert.equal(rows[0].dataset.severity, "error");
  assert.match(rows[0].textContent, /Conflict: GPIO pin use/);
  assert.equal(rows[0].querySelectorAll("button").length, 1); assert.doesNotMatch(rows[0].textContent, /Inspect pins/); app.dispose();
});

test("native Escape cancels without mutation and returns focus to the review trigger", async () => {
  const app = harness({mount: true}); await click(app.root, "Load diagnostics"); change(field(app.root, "Removal target"), "gpio");
  const trigger = await click(app.root, "Review removal"); const dialog = app.root.querySelectorAll("dialog")[0];
  assert.equal(dialog.open, true); assert.match(dialog.textContent, /Known dependants/); assert.match(dialog.textContent, /led alias/);
  const event = dialog.dispatch("cancel"); assert.equal(event.defaultPrevented, true);
  assert.equal(dialog.open, false); assert.equal(app.commands.length, 0); assert.equal(app.releaseCount, 1);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.deepEqual(trigger.focusOptions, {preventScroll: true}); app.dispose();
});

test("repair preview retains its footer focus, requires explicit confirmation, and reports success in dialog", async () => {
  const app = harness({mount: true}); await click(app.root, "Load diagnostics"); change(field(app.root, "Removal target"), "gpio");
  await click(app.root, "Review removal"); const dialog = app.root.querySelectorAll("dialog")[0];
  const repair = field(dialog, "Remove local led"); repair.checked = true; repair.dispatch("change");
  assert.equal(named(dialog, "button", "Apply removal to draft").disabled, true); assert.equal(app.commands.length, 0);
  const update = await click(dialog, "Update removal preview"); assert.equal(app.root.ownerDocument.activeElement, update);
  assert.equal(named(dialog, "button", "Apply removal to draft").disabled, false); assert.equal(app.commands.length, 0);
  await click(dialog, "Apply removal to draft"); assert.equal(app.commands.length, 1); assert.equal(dialog.open, true);
  assert.match(dialog.textContent, /Removal applied to draft/); assert.match(dialog.textContent, /Undo is available/);
  assert.equal(app.root.ownerDocument.activeElement, named(dialog, "button", "Close removal review"));
  await click(dialog, "Close removal review"); assert.equal(dialog.open, false); app.dispose();
});

test("inspection from a removal review closes and unlocks before showing source", async () => {
  const app = harness({mount: true}); await click(app.root, "Load diagnostics"); change(field(app.root, "Removal target"), "gpio");
  await click(app.root, "Review removal"); const dialog = app.root.querySelectorAll("dialog")[0];
  await click(dialog, "Inspect dependant source"); assert.equal(dialog.open, false); assert.equal(app.releaseCount, 1);
  assert.equal(app.sourceCalls.length, 1); assert.deepEqual(app.sourceCalls[0][0], aliasLocation); assert.equal(app.commands.length, 0); app.dispose();
});

test("external removal entry loads diagnostics explicitly and late navigation cannot reopen it", async () => {
  const wait = deferred(); const app = harness({mount: true, wrap: (api) => async (...args) => { const data = await api(...args); if (args[0].endsWith("/review")) await wait.promise; return data; }});
  const pending = app.panel.reviewRemoval("gpio"); await settle(); assert.equal(app.calls.length, 2);
  app.panel.navigate(); wait.resolve(); assert.equal(await pending, false);
  assert.equal(app.root.querySelectorAll("dialog")[0].open, false); assert.equal(app.state.draftView.entry.externalOperation, undefined);
  assert.equal(app.reveals.length, 0); app.dispose();
});

test("module removal entry delegates review even when direct leaf removal was blocked", async () => {
  const app = harness(); const root = new Element("section"); const reviews = [];
  const parent = {node_id: "bus", key: "node:/i2c", path: "/i2c", name: "i2c", kind: "i2c", role: "controller", children: []};
  const child = {node_id: "leaf", key: "node:/i2c/leaf@76", parent_id: "bus", parent_key: parent.key, path: "/i2c/leaf@76", name: "leaf@76",
    kind: "i2c", role: "module", fields: [], address: 118, editable: true, removable: false, remove_reason: "Alias depends on this node", recipe_matches: []};
  const model = {schema_id: "schema0", draft_id: "draft", revision: 0, sha256: "hash0", recipes: [], parents: [parent], instances: [child], targets: [], gpio_providers: [], diagnostics: [], graph: {nodes: [parent, child], edges: []}};
  const panel = mountModulePanel(root, {...app.options, onChange() {}, onRemove: (...args) => reviews.push(args),
    api: async (url) => url.endsWith("module-layout") ? {schema_version: 1, layout_id: "layout", revision: 0, positions: {}} : structuredClone(model)});
  await click(root, "Load modules"); await click(root, "leaf@76"); const trigger = await click(root, "Review module removal");
  assert.equal(reviews.length, 1); assert.equal(reviews[0][0], "leaf"); assert.equal(reviews[0][1], trigger);
  assert.doesNotMatch(root.textContent, /Preparing removal review/);
  assert.equal(app.commands.length, 0); panel.dispose(); app.dispose();
});
