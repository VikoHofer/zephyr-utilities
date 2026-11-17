import test from "node:test";
import assert from "node:assert/strict";
import {createDemoSession, validateDemoCatalogue, selectedDemo, filteredBoards, selectedBoard, availableSdks, selectionProblem, handoffBody} from "../../src/zephyr_utilities/features/tinyml/web/state.js";
import {mount} from "../../src/zephyr_utilities/features/tinyml/web/index.js";

const workspace = "/workspace";
const source = "a".repeat(64), environment = "b".repeat(64), token = "c".repeat(32);
const board = {id: "nordic", name: "nrf52840dk", full_name: "Nordic nRF52840 DK", vendor: "nordic", socs: ["nrf52840"],
  status: "discovered", targets: ["nrf52840dk/nrf52840", "nrf52840dk@1.0.0/nrf52840"]};
const otherBoard = {id: "st", name: "nucleo_f446re", full_name: "ST Nucleo F446RE", status: "discovered", targets: ["nucleo_f446re/stm32f446xx"]};
const sdk = {id: "sdk", path: "/sdk", version: "1.0", status: "discovered", toolchains: [{family: "GNU", compiler: "/sdk/arm-gcc"}]};
const demo = (changes = {}) => ({demo_id: "mnist-shell", variant_id: "reference", sample_id: "tinyml/mnist-shell", title: "MNIST digit recognition",
  framework: "TensorFlow Lite Micro", description: "Sensor-free inference.", status: "available", requirements: ["A serial shell and the reviewed module."],
  diagnostics: [], source_fingerprint: source, ...changes});
const catalogue = (path = workspace, changes = {}) => ({schema_version: 1, workspace: path, environment_fingerprint: environment,
  snapshot: {status: "complete", workspace: {path}, fingerprint: environment, boards: [board, otherBoard], sdks: [sdk]}, demos: [demo()], diagnostics: [], ...changes});
const chosenForm = () => ({demo_id: "mnist-shell", variant_id: "reference", search: "", board_id: board.id, target: board.targets[0], sdk_id: sdk.id});
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
function harness(api = async () => catalogue(), existing) {
  let currentWorkspace = workspace;
  const calls = [], navigation = [];
  let changes = 0;
  const state = existing || {};
  const session = createDemoSession({state, workspace: () => currentWorkspace, onChange: () => changes++, navigate: (route) => navigation.push(route),
    api: async (path, options) => { calls.push([path, options]); return api(path, options); }});
  return {state, session, calls, navigation, changes: () => changes, changeWorkspace(path) { currentWorkspace = path; }};
}
function select(app) {
  for (const [key, value] of Object.entries(chosenForm())) app.session.changeField(key, value);
}

test("opening and refreshing the chooser only read the public demo catalogue", async () => {
  const app = harness();
  await app.session.navigate(); await app.session.navigate();
  assert.equal(app.calls.length, 1);
  assert.deepEqual(app.calls[0][0], "/api/projects/demo-catalog");
  assert.deepEqual(app.calls[0][1].body, {workspace});
  assert.equal(app.calls[0][1].method, "POST");
  assert.equal(selectedDemo(app.state).demo_id, "mnist-shell");
  assert.equal(selectedBoard(app.state), null, "browsing does not select a board");
  assert.deepEqual(app.navigation, []);
  await app.session.load();
  assert.equal(app.calls.length, 2);
  assert.ok(app.calls.every(([path]) => path === "/api/projects/demo-catalog"));
  app.session.dispose();
});

test("catalogue validation binds workspace, evidence, recipe identity and target metadata", () => {
  const value = catalogue();
  assert.equal(validateDemoCatalogue(value, workspace), value);
  for (const invalid of [null, {...value, workspace: "/other"}, {...value, schema_version: 2}, {...value, environment_fingerprint: "short"},
    {...value, demos: [demo({demo_id: "arbitrary"})]}, {...value, demos: [demo({variant_id: "optimized"})]},
    {...value, demos: [demo({source_fingerprint: null})]}, {...value, demos: [demo({requirements: "not an array"})]},
    {...value, snapshot: {...value.snapshot, workspace: {path: "/other"}}}, {...value, snapshot: {...value.snapshot, fingerprint: "different"}},
    {...value, snapshot: {...value.snapshot, boards: [{...board, targets: "anything"}]}}]) {
    assert.throws(() => validateDemoCatalogue(invalid, workspace), /could not be verified/);
  }
});

test("missing analysis, source and dependency states explain why selection cannot continue", async () => {
  for (const value of [catalogue(workspace, {snapshot: null, environment_fingerprint: null, diagnostics: ["Analyze the workspace."]}),
    catalogue(workspace, {demos: [demo({status: "missing", source_fingerprint: null, diagnostics: ["Source is missing."]})]}),
    catalogue(workspace, {demos: [demo({status: "unsupported", source_fingerprint: null, diagnostics: ["Dependency revision is unsupported."]})]})]) {
    const app = harness(async () => value);
    await app.session.load(); select(app);
    assert.ok(selectionProblem(app.state));
    await app.session.continueToProjects();
    assert.ok(app.state.handoffError);
    assert.equal(app.calls.length, 1);
    assert.deepEqual(app.navigation, []);
    app.session.dispose();
  }
});

test("target filtering searches chip and exact target text and excludes unavailable boards and SDKs", async () => {
  const value = catalogue();
  value.snapshot.boards.push({...board, id: "missing-board", status: "missing"});
  value.snapshot.sdks.push({...sdk, id: "without-compiler", toolchains: [{family: "GNU"}]}, {...sdk, id: "missing-sdk", status: "missing"});
  const app = harness(async () => value);
  await app.session.load();
  app.session.changeField("search", "nordic nrf52840");
  assert.deepEqual(filteredBoards(app.state).map((entry) => entry.id), [board.id]);
  app.session.changeField("search", "stm32f446xx");
  assert.deepEqual(filteredBoards(app.state).map((entry) => entry.id), [otherBoard.id]);
  assert.deepEqual(availableSdks(app.state).map((entry) => entry.id), [sdk.id]);
  app.session.dispose();
});

test("continue submits only bound selection inputs and navigates through the verified Projects token", async () => {
  const app = harness(async (path) => path.endsWith("demo-catalog") ? catalogue() : {id: token, route: `projects/create/${token}`});
  await app.session.load(); select(app);
  const expected = {workspace, demo_id: "mnist-shell", variant_id: "reference", source_fingerprint: source,
    environment_fingerprint: environment, selection: {board_id: board.id, target: board.targets[0], sdk_id: sdk.id}};
  assert.deepEqual(handoffBody(app.state), expected);
  await app.session.continueToProjects();
  assert.equal(app.calls[1][0], "/api/projects/creation-handoffs");
  assert.deepEqual(app.calls[1][1].body, expected);
  assert.deepEqual(app.navigation, [`projects/create/${token}`]);
  assert.equal(app.state.submitting, false);
  app.session.dispose();
});

test("foreign targets, unavailable SDKs and stale environments cannot authorize a handoff", async () => {
  for (const [key, value] of [["target", otherBoard.targets[0]], ["sdk_id", "not-installed"], ["board_id", "missing"]]) {
    const app = harness(); await app.session.load(); select(app); app.session.changeField(key, value);
    await app.session.continueToProjects();
    assert.equal(app.calls.length, 1); assert.ok(app.state.handoffError); app.session.dispose();
  }
  const app = harness(async () => catalogue(workspace, {snapshot: {...catalogue().snapshot, status: "stale"}}));
  await app.session.load(); select(app); await app.session.continueToProjects();
  assert.match(app.state.handoffError, /Analyze this workspace/); assert.equal(app.calls.length, 1); app.session.dispose();
});

test("handoff response must bind its opaque ID to the exact Projects route", async () => {
  for (const result of [{id: token, route: "projects/create"}, {id: token, route: `projects/create/${"d".repeat(32)}`},
    {id: "not-a-token", route: "projects/create/not-a-token"}, {id: token, route: "https://outside.invalid/"}]) {
    const app = harness(async (path) => path.endsWith("demo-catalog") ? catalogue() : result);
    await app.session.load(); select(app); await app.session.continueToProjects();
    assert.match(app.state.handoffError, /could not be verified/); assert.deepEqual(app.navigation, []); app.session.dispose();
  }
});

test("editing any selection while a handoff is pending suppresses late navigation", async () => {
  for (const [key, value] of [["board_id", otherBoard.id], ["target", board.targets[1]], ["sdk_id", "changed"], ["search", "st"]]) {
    const pending = deferred();
    const app = harness(async (path) => path.endsWith("demo-catalog") ? catalogue() : pending.promise);
    await app.session.load(); select(app);
    const handoff = app.session.continueToProjects();
    assert.equal(app.state.submitting, true);
    app.session.changeField(key, value);
    assert.equal(app.calls[1][1].signal.aborted, true);
    pending.resolve({id: token, route: `projects/create/${token}`}); await handoff;
    assert.equal(app.state.submitting, false); assert.deepEqual(app.navigation, []); app.session.dispose();
  }
});

test("workspace changes and disposal suppress late catalogue and handoff responses", async () => {
  const pending = deferred();
  const app = harness(async (path, options) => options.body.workspace === workspace ? pending.promise : catalogue("/other"));
  const original = app.session.load();
  app.changeWorkspace("/other"); await app.session.navigate();
  pending.resolve(catalogue()); await original;
  assert.equal(app.state.catalogue.workspace, "/other"); assert.equal(app.calls[0][1].signal.aborted, true); app.session.dispose();
  const disposedRead = deferred();
  const disposed = harness(async () => disposedRead.promise);
  const reading = disposed.session.load(); disposed.session.dispose();
  const updates = disposed.changes(); disposedRead.resolve(catalogue()); await reading;
  assert.equal(disposed.state.catalogue, null); assert.equal(disposed.changes(), updates);
  for (const dispose of [false, true]) {
    const later = deferred();
    const next = harness(async (path) => path.endsWith("demo-catalog") ? catalogue() : later.promise);
    await next.session.load(); select(next); const handoff = next.session.continueToProjects();
    if (dispose) next.session.dispose(); else { next.changeWorkspace("/other"); await next.session.navigate(); }
    const changed = next.changes(); later.resolve({id: token, route: `projects/create/${token}`}); await handoff;
    assert.deepEqual(next.navigation, []); assert.equal(next.changes(), changed); next.session.dispose();
  }
});

test("the latest catalogue wins without replacing form edits made during its read", async () => {
  const first = deferred(), second = deferred();
  let calls = 0;
  const app = harness(async () => ++calls === 1 ? first.promise : second.promise);
  const earlier = app.session.load(), latest = app.session.load();
  app.session.changeField("search", "nordic");
  second.resolve(catalogue(workspace, {demos: [demo({source_fingerprint: "e".repeat(64)})]})); await latest;
  first.resolve(catalogue()); await earlier;
  assert.equal(selectedDemo(app.state).source_fingerprint, "e".repeat(64));
  assert.equal(app.state.form.search, "nordic"); assert.equal(app.calls[0][1].signal.aborted, true);
  app.session.dispose();
});

test("repeated Continue cannot submit duplicate handoffs while the first is pending", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("demo-catalog") ? catalogue() : pending.promise);
  await app.session.load(); select(app);
  const request = app.session.continueToProjects(); await app.session.continueToProjects();
  assert.equal(app.calls.filter(([path]) => path.endsWith("creation-handoffs")).length, 1);
  pending.resolve({id: token, route: `projects/create/${token}`}); await request;
  assert.equal(app.navigation.length, 1); app.session.dispose();
});

test("each workspace retains its own form and remount reads fresh source evidence", async () => {
  const app = harness(async (path, options) => catalogue(options.body.workspace));
  await app.session.load(); select(app);
  app.changeWorkspace("/other"); await app.session.navigate();
  assert.equal(app.state.form.board_id, ""); app.session.changeField("search", "other query");
  app.changeWorkspace(workspace); await app.session.navigate();
  assert.deepEqual(app.state.form, chosenForm());
  app.session.dispose();
  const reopened = harness(async () => catalogue(workspace, {demos: [demo({source_fingerprint: "e".repeat(64)})]}), app.state);
  assert.equal(reopened.state.catalogue, null);
  await reopened.session.navigate();
  assert.equal(reopened.state.form.target, board.targets[0]); assert.equal(selectedDemo(reopened.state).source_fingerprint, "e".repeat(64));
  assert.equal(reopened.calls.length, 1); reopened.session.dispose();
});

test("failed reads and handoffs show nearby errors and can be retried explicitly", async () => {
  let failRead = true, failHandoff = true;
  const app = harness(async (path) => {
    if (path.endsWith("demo-catalog")) { if (failRead) throw new Error("Local source unavailable"); return catalogue(); }
    if (failHandoff) throw new Error("Source changed; refresh demos"); return {id: token, route: `projects/create/${token}`};
  });
  await app.session.load(); assert.equal(app.state.error, "Local source unavailable"); assert.equal(app.state.loading, false);
  failRead = false; await app.session.load(); select(app); await app.session.continueToProjects();
  assert.equal(app.state.handoffError, "Source changed; refresh demos"); assert.equal(app.state.submitting, false);
  failHandoff = false; await app.session.continueToProjects(); assert.equal(app.navigation.length, 1); app.session.dispose();
});

class Element extends EventTarget {
  constructor(tag, document) { super(); Object.assign(this, {tagName: tag, ownerDocument: document, children: [], dataset: {}, attributes: {}, value: "", hidden: false, disabled: false}); }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(""); }
  setAttribute(key, value) { this.attributes[key] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ""; this.children = children; }
  focus() { this.ownerDocument.activeElement = this; }
}
function viewHarness(api) {
  const document = {activeElement: null, createElement(tag) { return new Element(tag, document); }};
  const names = ["root", "heading", "refresh", "load-status", "load-error", "catalogue-diagnostics", "environment", "demo", "demo-title", "framework", "description", "availability", "requirements", "demo-diagnostics", "search", "board", "board-count", "target", "sdk", "selection-note", "continue", "handoff-status", "handoff-error"];
  const fields = Object.fromEntries(names.map((name) => [name, new Element(name === "search" ? "input" : "div", document)]));
  const chooser = new Element("div", document), drawing = new Element("div", document);
  chooser.querySelector = (selector) => fields[selector.slice(13, -1)];
  fields.root.querySelector = (selector) => selector === "[data-tinyml-chooser]" ? chooser
    : selector === "[data-tinyml-drawing]" ? drawing : fields[selector.slice(13, -1)];
  const host = {querySelector: () => fields.root};
  const state = {}, navigation = [], reveals = [];
  const context = {state, api, workspace: {path: workspace}, navigate: (route) => navigation.push(route), reveal: (...args) => reveals.push(args)};
  const instance = mount(host, context);
  return {fields, state, context, instance, navigation, reveals, document};
}

test("rendered requirements are literal text and routine refresh retains controls, focus and scroll behavior", async () => {
  const hostile = '<img src=x onerror="execute()">';
  let calls = 0;
  const app = viewHarness(async () => { calls++; return catalogue(workspace, {demos: [demo({description: hostile, requirements: [hostile], diagnostics: [hostile]})]}); });
  await app.instance.navigate();
  assert.equal(app.fields.description.textContent, hostile); assert.equal(app.fields.requirements.children[0].textContent, hostile);
  assert.equal(app.fields["demo-diagnostics"].children[0].textContent, hostile);
  app.fields.search.focus(); app.fields.search.value = "nrf52840"; app.fields.search.dispatchEvent(new Event("input"));
  assert.equal(app.document.activeElement, app.fields.search); assert.equal(app.state.form.search, "nrf52840");
  assert.match(app.fields["board-count"].textContent, /1 matching boards/);
  app.fields.refresh.focus(); app.fields.refresh.dispatchEvent(new Event("click")); await Promise.resolve(); await Promise.resolve();
  assert.equal(app.document.activeElement, app.fields.refresh); assert.equal(app.fields.refresh.disabled, false);
  assert.deepEqual(app.reveals, []); assert.equal(calls, 2);
  assert.match(app.fields.availability.textContent, /have not been checked/);
  app.instance.dispose();
  app.fields.refresh.dispatchEvent(new Event("click")); assert.equal(calls, 2, "disposed listeners cannot request another catalogue");
});
