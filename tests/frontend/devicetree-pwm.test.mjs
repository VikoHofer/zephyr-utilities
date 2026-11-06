import test from "node:test";
import assert from "node:assert/strict";
import {createPWMSession, pwmEntry, pwmFlags, pwmRows} from "../../src/zephyr_utilities/features/devicetree/web/pwm.js";
import {mountPWMPanel} from "../../src/zephyr_utilities/features/devicetree/web/pwm-view.js";

const source = '/ { pwm0: pwm { }; leds { led { pwms = <&pwm0 1 20000000 256>; }; }; };\n';
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; };
const settle = () => new Promise((resolve) => setImmediate(resolve));
const fixture = () => ({nodes: [], targets: [], diagnostics: [],
  providers: [{id: "pwm0", reference: "&pwm0", path: "/pwm", label: "pwm0", cells: ["channel", "period", "flags"], supported: true, status: "okay", channel_count: 4},
    {id: "pwm2", reference: "&pwm2", path: "/pwm2", label: "pwm2", cells: ["channel", "period"], supported: true, status: null, channel_count: null}],
  consumers: [{node_id: "led", name: "led", path: "/leds/led", editable: true, entry: {reference: "&pwm0", cells: [1, 20000000, 256]}, field: {name: "pwms", type: "phandle-array"}}],
  groups: [{node_id: "leds", name: "leds", path: "/leds", addable: true}], group_option: {available: true, name: "pwm_leds"}});

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
      if (command.kind === "pwm_set") model.consumers.find((row) => row.node_id === command.node_id).entry = structuredClone(command.entry);
      if (command.kind === "pwm_group_add") model.groups.push({node_id: "new-group", path: "/" + command.name, name: command.name, addable: true});
      if (command.kind === "pwm_led_add") {
        const group = model.groups.find((row) => row.node_id === command.parent_id);
        model.consumers.push({node_id: "new-led", name: command.name, path: group.path + "/" + command.name, editable: true, entry: structuredClone(command.entry)});
      }
      entry.server = {...entry.server, revision, sha256: "hash" + revision, text: source + "/* revision " + revision + " */"};
      entry.text = entry.server.text; notify(); return true;
    }, async reconcile() { state.draftView.entry.uncertain = false; notify(); return true; }};
  const options = {api, state, workspace: () => workspace, drafts, onChange: notify}; const root = mount ? new Element("section") : null;
  if (mount) panel = mountPWMPanel(root, {...options, onRemove, notify: (message, error) => toasts.push({message, error}), reveal: (node) => reveals.push(node)});
  else session = createPWMSession(options);
  return {state, options, root, panel, session, model, calls, commands, response, drafts, reveals, toasts,
    notifications: () => notifications,
    workspace(value) { workspace = value; session?.sync(); panel?.navigate(); },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; notify(); },
    dispose() { session?.dispose(); panel?.dispose(); }};
}

test("PWM inspection is explicit and uses the canonical draft guard without mutation", async () => {
  const app = harness(); assert.equal(app.calls.length, 0); assert.equal(await app.session.load(), true);
  assert.equal(app.calls[0].url, "/api/devicetree/editor/drafts/pwm");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash0"});
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.commands.length, 0); app.dispose();
});

test("PWM commands refresh the accepted revision and stale typing withholds mutations", async () => {
  const app = harness(); await app.session.load(); const command = {kind: "pwm_set", node_id: "led", entry: {reference: "&pwm0", cells: [2, 10000, 257]}};
  assert.equal(await app.session.command(command), true); assert.deepEqual(app.commands[0], {...command, schema_id: "schema0"});
  assert.equal(app.state.pwmView.data.revision, 1); const observed = app.state.pwmView.data;
  app.type(source + "/* pending */"); assert.equal(app.state.pwmView.data, null); assert.equal(app.state.pwmView.presentation, observed);
  assert.equal(await app.session.command(command), false); assert.equal(app.commands.length, 1); app.dispose();
});

test("incomplete responses and source cannot enable PWM commands", async () => {
  const app = harness({wrap: (api) => async (...args) => { const result = await api(...args); delete result.providers; return result; }});
  assert.equal(await app.session.load(), false); assert.match(app.state.pwmView.error, /does not match/);
  app.state.draftView.entry.server.syntax.blocked = true; assert.equal(await app.session.load(), false);
  assert.equal(app.calls.length, 1); assert.equal(app.commands.length, 0); app.dispose();
});

test("late PWM responses cannot cross source, workspace, navigation or disposal", async () => {
  for (const action of ["source", "workspace", "navigation", "dispose"]) {
    const wait = deferred(); const app = harness({wrap: (api) => async (...args) => { const data = await api(...args); await wait.promise; return data; }});
    const pending = app.session.load();
    if (action === "source") app.type(source + "/* edit */");
    if (action === "workspace") app.workspace("/other");
    if (action === "navigation") app.session.clear();
    if (action === "dispose") app.dispose();
    wait.resolve(); assert.equal(await pending, false, action); assert.equal(app.state.pwmView.data, null); app.dispose();
  }
});

test("cancel and uncertain command reconciliation never repeat a PWM mutation", async () => {
  const wait = deferred(); let signal;
  const cancelled = harness({wrap: (api) => async (...args) => { signal = args[1].signal; const data = await api(...args); await wait.promise; return data; }});
  const pending = cancelled.session.load(); cancelled.session.cancel(); wait.resolve(); assert.equal(await pending, false);
  assert.equal(signal.aborted, true); assert.match(cancelled.state.pwmView.message, /cancelled/); cancelled.dispose();
  const app = harness(); await app.session.load(); let attempts = 0;
  app.drafts.command = async () => { attempts++; app.state.draftView.entry.uncertain = true; app.state.draftView.entry.error = "Reply lost; reconcile."; return false; };
  assert.equal(await app.session.command({kind: "pwm_set", node_id: "led", entry: {reference: "&pwm0", cells: [0, 5000, 0]}}), false);
  assert.match(app.state.pwmView.error, /Reply lost/); assert.equal(await app.session.reconcile(), true); assert.equal(attempts, 1);
  assert.match(app.state.pwmView.message, /Load PWM consumers again/); app.dispose();
});

test("PWM cells reject ambiguous input without inferring channel numbering or losing flag bits", () => {
  const [three, two] = fixture().providers;
  assert.deepEqual(pwmEntry(three, {channel: "0xffffffff", period: "0x100", flags: "0x80000101"}), {reference: "&pwm0", cells: [0xffffffff, 256, 0x80000101]});
  assert.deepEqual(pwmEntry(two, {channel: "4", period: "5000", flags: "ignored"}), {reference: "&pwm2", cells: [4, 5000]});
  assert.equal(pwmFlags("0xffffffff", "0"), 0xfffffffe); assert.equal(pwmFlags("0x80000100", "1"), 0x80000101);
  for (const bad of ["0", "-1", "PWM_MSEC(20)", "1 2", "4294967296"]) assert.throws(() => pwmEntry(three, {channel: "0", period: bad, flags: "0"}));
  assert.throws(() => pwmEntry({...three, supported: false}, {channel: "0", period: "1", flags: "0"}), /supported local/);
  assert.throws(() => pwmEntry({...three, cells: ["period", "channel"]}, {}), /layout/);
  assert.throws(() => pwmFlags("0", "2"), /polarity/);
});

test("PWM selection identity survives revised occurrence ids", () => {
  const model = fixture(); const before = pwmRows(model).map((row) => row.key);
  model.groups[0].node_id = "new-group-id"; model.consumers[0].node_id = "new-consumer-id";
  assert.deepEqual(pwmRows(model).map((row) => row.key), before);
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
const named = (root, tag, text) => Array.from(root.querySelectorAll(tag)).find((node) => node.textContent.startsWith(text));
const field = (root, text) => named(root, "label", text).children[0];
async function click(root, text) {
  const node = named(root, "button", text); assert.ok(node, text); assert.equal(node.disabled, false, text + " enabled");
  node.focus(); node.dispatch("click"); if (node.type === "submit") node.closest("form").dispatch("submit"); await settle(); return node;
}
function change(node, value) { node.value = value; node.dispatch("change"); }

test("PWM edit uses local provider cells and preserves vendor flags while changing polarity", async () => {
  const app = harness({mount: true}); await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led");
  const form = named(app.root, "form", "Edit PWM specifier"); change(field(form, "Flags"), "0x80000100");
  assert.equal(field(form, "Polarity").value, "0"); change(field(form, "Polarity"), "1");
  assert.equal(field(form, "Flags").value, String(0x80000101)); field(form, "Channel").value = "4";
  await click(app.root, "Apply PWM to draft");
  assert.deepEqual(app.commands[0], {kind: "pwm_set", node_id: "led", entry: {reference: "&pwm0", cells: [4, 20000000, 0x80000101]}, schema_id: "schema0"});
  assert.match(app.root.textContent, /Declared channel count: 4/); assert.match(app.root.textContent, /Valid channel numbering.*unknown/); app.dispose();
});

test("two-cell providers omit flags and never manufacture polarity cells", async () => {
  const app = harness({mount: true}); await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led");
  const form = named(app.root, "form", "Edit PWM specifier"); change(field(form, "PWM provider"), "&pwm2");
  assert.equal(field(form, "Flags").disabled, true); assert.equal(field(form, "Polarity").disabled, true);
  await click(app.root, "Apply PWM to draft"); assert.deepEqual(app.commands[0].entry, {reference: "&pwm2", cells: [1, 20000000]}); app.dispose();
});

test("PWM form values and viewport survive passive renders and pending canonical typing", async () => {
  const app = harness({mount: true}); await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led");
  const form = named(app.root, "form", "Edit PWM specifier"); const period = field(form, "Period"); period.value = "1250000"; period.focus(); app.root.scrollTop = 600;
  app.panel.render(); assert.equal(named(app.root, "form", "Edit PWM specifier"), form); assert.equal(period.value, "1250000");
  app.type(source + "/* pending */"); assert.equal(named(app.root, "form", "Edit PWM specifier"), form);
  assert.equal(period.disabled, true); assert.equal(period.value, "1250000"); assert.equal(app.root.scrollTop, 600); assert.equal(app.reveals.length, 0); app.dispose();
});

test("PWM refresh retains the current form and shows its result beside the trigger", async () => {
  const wait = deferred(); let reads = 0;
  const app = harness({mount: true, wrap: (api) => async (...args) => { const result = await api(...args); if (++reads > 1) await wait.promise; return result; }});
  await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led"); app.reveals.length = 0;
  const form = named(app.root, "form", "Edit PWM specifier"); const period = field(form, "Period"); period.value = "75000";
  await click(app.root, "Apply PWM to draft"); assert.equal(named(app.root, "form", "Edit PWM specifier"), form);
  assert.equal(period.disabled, true); assert.equal(period.value, "75000"); assert.match(form.textContent, /Refreshing PWM/);
  wait.resolve(); await settle(); assert.match(named(app.root, "form", "Edit PWM specifier").textContent, /Draft updated/);
  assert.deepEqual(app.reveals, []); assert.equal(app.root.ownerDocument.activeElement.textContent, "Apply PWM to draft"); app.dispose();
});

test("PWM input errors remain beside the submitting form and leave source unchanged", async () => {
  const app = harness({mount: true}); await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led"); app.reveals.length = 0;
  const form = named(app.root, "form", "Edit PWM specifier"); field(form, "Period").value = "0";
  await click(app.root, "Apply PWM to draft"); assert.match(form.textContent, /greater than zero/); assert.equal(app.commands.length, 0);
  assert.deepEqual(app.reveals, []); assert.equal(app.state.draftView.entry.text, source); app.dispose();
});

test("group and PWM LED creation are explicit and select the new local source identity", async () => {
  const app = harness({mount: true}); await click(app.root, "Load PWM consumers");
  const group = named(app.root, "form", "Add a PWM LED group"); field(group, "PWM group node name").value = "indicators";
  await click(app.root, "Add PWM group to draft"); assert.deepEqual(app.commands[0], {kind: "pwm_group_add", name: "indicators", schema_id: "schema0"});
  const led = named(app.root, "form", "Add PWM LED"); assert.ok(led); field(led, "LED node name").value = "status";
  field(led, "Node label").value = "status_pwm"; change(field(led, "PWM provider"), "&pwm2");
  field(led, "Channel").value = "1"; field(led, "Period").value = "10000000";
  await click(app.root, "Add PWM LED to draft");
  assert.deepEqual(app.commands[1], {kind: "pwm_led_add", parent_id: "new-group", name: "status", label: "status_pwm", entry: {reference: "&pwm2", cells: [1, 10000000]}, schema_id: "schema1"});
  assert.ok(named(app.root, "form", "Edit PWM specifier")); app.dispose();
});

test("unsupported consumers and providers explain source access without inventing editable forms", async () => {
  const app = harness({mount: true}); app.model.consumers[0].editable = false; app.model.consumers[0].reason = "Multiple PWM specifiers require source editing.";
  app.model.providers.push({reference: "&odd", path: "/odd", cells: ["custom"], supported: false, reason: "Custom cell layout."});
  app.model.group_option = {available: false, reason: "The local root is ambiguous."};
  await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led");
  assert.match(app.root.textContent, /Multiple PWM specifiers/); assert.match(app.root.textContent, /Custom cell layout/);
  assert.equal(named(app.root, "button", "Apply PWM to draft"), undefined); assert.equal(named(app.root, "button", "Add PWM group to draft").disabled, true);
  assert.equal(app.commands.length, 0); app.dispose();
});

test("empty PWM topology and imported descriptions are rendered as plain text", async () => {
  const app = harness({mount: true}); app.model.consumers = []; app.model.groups = [];
  app.model.diagnostics = ['<img src=x onerror="bad()"> Unsupported evidence'];
  await click(app.root, "Load PWM consumers"); assert.match(app.root.textContent, /No local PWM consumers/);
  assert.match(app.root.textContent, /<img src=x/); assert.equal(app.root.querySelectorAll("img").length, 0); app.dispose();
});

test("PWM removal delegates one review with visible progress and no immediate source command", async () => {
  const wait = deferred(); const reviews = [];
  const app = harness({mount: true, onRemove: async (...args) => { reviews.push(args); await wait.promise; }});
  await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led");
  const trigger = await click(app.root, "Review PWM consumer removal"); assert.equal(trigger.textContent, "Preparing removal review…");
  trigger.dispatch("click"); assert.equal(reviews.length, 1); assert.equal(reviews[0][0], "led"); assert.equal(reviews[0][1], trigger);
  assert.equal(app.commands.length, 0); wait.resolve(); await settle(); assert.equal(trigger.textContent, "Review PWM consumer removal"); app.dispose();
});

test("PWM navigation and disposal clear inspection and ignore late removal errors", async () => {
  let reject; const wait = new Promise((_resolve, no) => { reject = no; });
  const app = harness({mount: true, onRemove: () => wait}); await click(app.root, "Load PWM consumers"); await click(app.root, "PWM · /leds/led");
  await click(app.root, "Review PWM consumer removal"); app.panel.navigate(); assert.equal(app.state.pwmView.data, null);
  reject(new Error("Old context")); await settle(); assert.equal(app.state.pwmView.error, "");
  const count = app.calls.length; app.dispose(); named(app.root, "button", "Load PWM consumers").dispatch("click"); await settle(); assert.equal(app.calls.length, count);
});

test("PWM row selection keeps its trigger focused and announces offscreen details once", async () => {
  const app = harness({mount: true}); await click(app.root, "Load PWM consumers");
  app.root.ownerDocument.offscreen = (node) => node.className === "dte-peripheral-detail";
  app.root.scrollTop = 650; const trigger = await click(app.root, "PWM · /leds/led");
  assert.deepEqual(app.reveals, []); assert.deepEqual(app.toasts, [{message: "Details for /leds/led are ready below.", error: false}]);
  assert.equal(app.root.ownerDocument.activeElement, trigger); assert.equal(app.root.scrollTop, 650);
  app.panel.render(); app.panel.render(); assert.equal(app.toasts.length, 1); app.dispose();
});
