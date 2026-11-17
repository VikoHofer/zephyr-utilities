import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession, creatorBody, creatorTemplateLabel} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";
import {validateSampleCatalogue, readySample, selectedSample, sampleSelectionError, sampleCheckSelection, currentSampleCompatibility} from "../../src/zephyr_utilities/features/projects/web/sample-state.js";

const one = "/workspace/one";
const two = "/workspace/two";
const hash = "a".repeat(64);
const templates = [
  {id: "minimal-c", version: 1, title: "Minimal C", language: "C", description: "C starter"},
  {id: "minimal-cpp", version: 1, title: "Minimal C++", language: "C++", description: "C++ starter"},
  {id: "local-sample", version: 1, title: "Local Zephyr sample", language: "C", description: "Copy a sample"},
];
const sample = (changes = {}) => ({id: "basic/blinky", title: "Blinky", description: "Blink an LED.", path: "samples/basic/blinky",
  status: "available", fingerprint: hash, file_count: 6, requirements: ["A GPIO LED with the led0 alias."], diagnostics: [], ...changes});
const mnist = (changes = {}) => sample({id: "tinyml/mnist-shell", title: "MNIST shell", description: "Sensor-free digit recognition.",
  path: "bundled:tinyml/mnist", requirements: ["A serial console, C++17 and the managed TFLM module."], ...changes});
const catalogue = (path = one, entries = [sample()]) => ({workspace: path, zephyr: {path: `${path}/zephyr`, version: "4.4.2", revision: "revision"}, samples: entries, diagnostics: []});
const snapshot = (path = one, fingerprint = "environment") => ({workspace: {path}, fingerprint, zephyr: {path: `${path}/zephyr`}, status: "complete", boards: [], sdks: []});
const response = (path = one, fingerprint = "environment") => ({workspace: path, snapshot: snapshot(path, fingerprint), templates, projects: [], jobs: [], diagnostics: []});
const form = (changes = {}) => ({name: "my_app", destination: "", search: "", board_id: "board", target: "board/chip", sdk_id: "sdk", template_id: "local-sample", sample_id: "basic/blinky", ...changes});
function harness(api, changes = {}, initialView = "create") {
  let workspace = one;
  let view = initialView;
  const state = {workspace: one, snapshot: snapshot(), templates, projects: [], jobs: [], form: form(), ...changes};
  let updates = 0;
  const session = createCreatorSession({api, state, workspace: () => workspace, isCreating: () => view === "create", onChange: () => updates++, schedule: () => 1, unschedule() {}});
  return {state, session, changeWorkspace: (path) => { workspace = path; }, changeView: (next) => { view = next; }, updates: () => updates};
}
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }

test("sample discovery stays lazy for C and runs once when local sample is selected", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.body]); return path.endsWith("samples") ? catalogue() : response(); }, {form: form({template_id: "minimal-c"})});
  await app.session.load();
  assert.deepEqual(calls, [["/api/projects/creator", undefined]]);
  app.session.changeField("template_id", "local-sample");
  await Promise.resolve();
  assert.deepEqual(calls[1], ["/api/projects/samples", {workspace: one}]);
  assert.equal(readySample(app.state).id, "basic/blinky");
  await app.session.load();
  await app.session.load({background: true});
  assert.equal(calls.filter(([path]) => path.endsWith("samples")).length, 1);
  app.session.dispose();
});

test("Applications reopening retains the local draft without scanning until Create opens", async () => {
  const calls = [];
  const api = async (path) => { calls.push(path); return path.endsWith("samples") ? catalogue() : response(); };
  const original = harness(api);
  await original.session.load();
  original.session.dispose();
  calls.length = 0;
  const reopened = harness(api, original.state, "applications");
  await reopened.session.load();
  await reopened.session.ensureSamples();
  assert.deepEqual(calls, ["/api/projects/creator"]);
  assert.equal(reopened.state.form.sample_id, "basic/blinky");
  assert.equal(reopened.state.sampleCatalogue, null);
  reopened.changeView("create");
  await reopened.session.tick();
  await reopened.session.ensureSamples();
  assert.deepEqual(calls, ["/api/projects/creator", "/api/projects/samples"]);
  assert.equal(readySample(reopened.state).id, "basic/blinky");
  reopened.session.dispose();
});

test("preview submits the selected observed fingerprint only for local samples", async () => {
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    return path.endsWith("samples") ? catalogue() : {plan: {id: "review", workspace: one}};
  });
  await app.session.loadSamples();
  await app.session.preview();
  assert.equal(calls[1][1].sample_id, "basic/blinky");
  assert.equal(calls[1][1].sample_fingerprint, hash);
  assert.equal(calls[1][1].template_id, "local-sample");
  for (const template_id of ["minimal-c", "minimal-cpp"]) {
    const body = creatorBody(form({template_id}), sample());
    assert.equal(Object.hasOwn(body, "sample_id"), false);
    assert.equal(Object.hasOwn(body, "sample_fingerprint"), false);
  }
  app.session.dispose();
});

test("missing and unsupported samples remain selectable for their explanations but cannot preview", async () => {
  for (const status of ["missing", "unsupported"]) {
    const calls = [];
    const app = harness(async (path) => { calls.push(path); return catalogue(one, [sample({status, fingerprint: null, diagnostics: ["Supporting source is unavailable."]})]); });
    await app.session.loadSamples();
    assert.equal(selectedSample(app.state).status, status);
    assert.deepEqual(selectedSample(app.state).diagnostics, ["Supporting source is unavailable."]);
    assert.equal(readySample(app.state), null);
    await app.session.preview();
    assert.match(app.state.error, /Choose an available local sample/);
    assert.deepEqual(calls, ["/api/projects/samples"]);
    assert.equal(app.state.form.sample_id, "basic/blinky");
    app.session.dispose();
  }
});

test("empty catalogues and no selected sample do not retarget the draft", async () => {
  for (const sample_id of ["", "basic/blinky", "old/sample"]) {
    const app = harness(async () => catalogue(one, []), {form: form({sample_id})});
    await app.session.loadSamples();
    assert.equal(app.state.form.sample_id, sample_id);
    assert.equal(readySample(app.state), null);
    assert.match(sampleSelectionError(app.state), /Choose an available/);
    app.session.dispose();
  }
});

test("unverified catalogues cannot authorize a preview", async () => {
  const invalid = [null, {...catalogue(), workspace: two}, {...catalogue(), zephyr: null}, {...catalogue(), diagnostics: {}},
    catalogue(one, [sample(), sample()]), catalogue(one, [sample({id: "elsewhere"})]), catalogue(one, [sample({path: "../outside"})]),
    catalogue(one, [sample({fingerprint: "short"})]), catalogue(one, [sample({status: "unknown"})]), catalogue(one, [sample({requirements: "missing array"})]),
    catalogue(one, [sample({file_count: -1})])];
  for (const value of invalid) {
    assert.throws(() => validateSampleCatalogue(value, one), /could not be verified/);
    const app = harness(async () => value);
    await app.session.loadSamples();
    assert.equal(app.state.sampleCatalogue.ready, false);
    assert.match(app.state.sampleCatalogue.error, /could not be verified/);
    assert.match(sampleSelectionError(app.state), /Load the local samples/);
    app.session.dispose();
  }
  const app = harness(async () => ({...catalogue(), zephyr: {path: "/different/checkout"}}));
  await app.session.loadSamples();
  assert.equal(app.state.sampleCatalogue.ready, false);
  assert.match(app.state.sampleCatalogue.error, /could not be verified/);
  app.session.dispose();
});

test("refresh failures preserve the selected ID and can be retried explicitly", async () => {
  let fail = true;
  const app = harness(async () => { if (fail) throw new Error("Checkout unavailable"); return catalogue(); });
  await app.session.loadSamples();
  assert.equal(app.state.sampleCatalogue.error, "Checkout unavailable");
  assert.equal(app.state.form.sample_id, "basic/blinky");
  fail = false;
  await app.session.loadSamples();
  assert.equal(app.state.sampleCatalogue.error, "");
  assert.equal(readySample(app.state).fingerprint, hash);
  app.session.dispose();
});

test("refresh invalidates a reviewed or in-flight old sample preview", async () => {
  const pending = deferred();
  let currentHash = hash;
  const app = harness(async (path) => path.endsWith("samples") ? catalogue(one, [sample({fingerprint: currentHash})]) : pending.promise);
  await app.session.loadSamples();
  app.state.plan = {id: "old", workspace: one};
  const preview = app.session.preview();
  currentHash = "b".repeat(64);
  await app.session.loadSamples();
  assert.equal(app.state.plan, null);
  pending.resolve({plan: {id: "late", workspace: one}});
  await preview;
  assert.equal(app.state.plan, null);
  assert.equal(readySample(app.state).fingerprint, currentHash);
  app.session.dispose();
});

test("changing sample discards an in-flight previous-sample preview", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("samples") ? catalogue() : pending.promise);
  await app.session.loadSamples();
  const preview = app.session.preview();
  app.session.changeField("sample_id", "hello_world");
  pending.resolve({plan: {id: "old-blinky", workspace: one}});
  await preview;
  assert.equal(app.state.plan, null);
  app.session.dispose();
});

test("latest discovery request wins and loading cannot preview", async () => {
  const first = deferred(); const second = deferred();
  let calls = 0;
  const app = harness(async () => ++calls === 1 ? first.promise : second.promise);
  const firstLoad = app.session.loadSamples();
  assert.match(sampleSelectionError(app.state), /Wait for/);
  const secondLoad = app.session.loadSamples();
  second.resolve(catalogue(one, [sample({fingerprint: "b".repeat(64)})])); await secondLoad;
  first.resolve(catalogue()); await firstLoad;
  assert.equal(readySample(app.state).fingerprint, "b".repeat(64));
  app.session.dispose();
});

test("workspace and disposal changes reject delayed sample results", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("samples") ? pending.promise : response(two));
  const load = app.session.loadSamples();
  app.changeWorkspace(two); await app.session.load();
  pending.resolve(catalogue()); await load;
  assert.equal(app.state.workspace, two);
  assert.equal(app.state.sampleCatalogue, null);
  assert.equal(app.state.form.sample_id, "");
  app.session.dispose();
  const later = deferred();
  const disposed = harness(async () => later.promise);
  const waiting = disposed.session.loadSamples();
  disposed.session.dispose();
  const count = disposed.updates();
  later.resolve(catalogue()); await waiting;
  assert.equal(disposed.updates(), count);
  assert.equal(disposed.state.sampleCatalogue.ready, false);
});

test("sample drafts survive workspace round trips and remount with a fresh discovery", async () => {
  let path = one;
  const calls = [];
  const api = async (endpoint) => { calls.push(endpoint); return endpoint.endsWith("samples") ? catalogue(path) : response(path); };
  const app = harness(api);
  await app.session.load();
  app.changeWorkspace(two); path = two; await app.session.load();
  assert.equal(app.state.form.template_id, "minimal-c");
  app.changeWorkspace(one); path = one; await app.session.load();
  assert.equal(app.state.form.sample_id, "basic/blinky");
  assert.equal(readySample(app.state).id, "basic/blinky");
  app.state.plan = {id: "old"}; app.session.dispose();
  const reopened = harness(api, app.state);
  assert.equal(reopened.state.sampleCatalogue, null);
  assert.equal(reopened.state.plan, null);
  await reopened.session.load();
  assert.equal(readySample(reopened.state).id, "basic/blinky");
  assert.equal(calls.filter((endpoint) => endpoint.endsWith("samples")).length, 3);
  reopened.session.dispose();
});

test("background environment changes invalidate discovery without automatically rescanning", async () => {
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return path.endsWith("samples") ? catalogue() : response(one, "changed"); });
  await app.session.loadSamples();
  app.state.plan = {id: "old"};
  await app.session.load({background: true});
  assert.equal(app.state.sampleCatalogue, null);
  assert.equal(app.state.plan, null);
  assert.deepEqual(calls, ["/api/projects/samples", "/api/projects/creator"]);
  assert.equal(app.state.form.sample_id, "basic/blinky");
  app.session.dispose();
});

test("sample discovery waits for a usable environment", async () => {
  for (const value of [null, {...snapshot(), status: "stale"}]) {
    let calls = 0;
    const app = harness(async () => calls++, {snapshot: value});
    await app.session.loadSamples();
    assert.equal(calls, 0);
    assert.equal(app.state.sampleCatalogue, null);
    app.session.dispose();
  }
});

test("recorded sample identity supplies the preview and application label", () => {
  assert.equal(creatorTemplateLabel({id: "local-sample", version: 1}, templates, sample()), "Blinky · local Zephyr sample");
  assert.equal(creatorTemplateLabel({id: "local-sample", version: 1}, [], {id: "basic/button"}), "basic/button · local Zephyr sample");
  assert.equal(creatorTemplateLabel({id: "local-sample", version: 1}, templates, mnist()), "MNIST shell · bundled MNIST demo");
});

test("MNIST uses its bundled template identity alongside the three classic local samples", () => {
  const entries = [sample({id: "hello_world", path: "samples/hello_world"}), sample(),
    sample({id: "basic/button", path: "samples/basic/button"}), mnist()];
  const value = catalogue(one, entries);
  assert.equal(validateSampleCatalogue(value, one, `${one}/zephyr`), value);
  for (const entry of [mnist({path: "samples/tinyml/mnist-shell"}), mnist({path: "../applications/mnist_shell"}),
    mnist({path: "../../applications/mnist_shell"}),
    mnist({path: "/different/mnist_shell"}), mnist({id: "tinyml/unreviewed"}), sample({path: "../applications/mnist_shell"})]) {
    assert.throws(() => validateSampleCatalogue(catalogue(one, [entry]), one), /could not be verified/);
  }
});

test("MNIST missing installed assets and framework diagnostics remain visible and prevent review", async () => {
  for (const diagnostic of ["The MNIST runtime assets are not installed.", "The managed TensorFlow Lite Micro module is missing."]) {
    const calls = [];
    const app = harness(async (path) => {
      calls.push(path);
      return catalogue(one, [mnist({status: "missing", fingerprint: null, file_count: 0, diagnostics: [diagnostic]})]);
    }, {form: form({sample_id: "tinyml/mnist-shell"})});
    await app.session.loadSamples();
    assert.equal(app.state.form.sample_id, "tinyml/mnist-shell");
    assert.deepEqual(selectedSample(app.state).diagnostics, [diagnostic]);
    assert.equal(readySample(app.state), null);
    await app.session.preview();
    assert.match(app.state.error, /Choose an available local sample/);
    assert.deepEqual(calls, ["/api/projects/samples"]);
    app.session.dispose();
  }
});

test("MNIST uses the ordinary exact-target review and invalidates earlier compatibility", async () => {
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options?.body]);
    return path.endsWith("samples") ? catalogue(one, [mnist()]) : {plan: {id: "review", workspace: one}};
  }, {form: form({sample_id: "tinyml/mnist-shell", target: "nrf52840dk/nrf52840"})});
  await app.session.loadSamples();
  const selection = sampleCheckSelection(app.state);
  app.state.sampleCheck = {verified: true, stale: false, selection, compatibility: {status: "compatible"}};
  assert.equal(currentSampleCompatibility(app.state).status, "compatible");
  app.session.changeField("target", "nucleo_f446re/stm32f446xx");
  assert.equal(currentSampleCompatibility(app.state), null);
  assert.equal(app.state.sampleCheck.stale, true);
  await app.session.preview();
  assert.equal(calls[1][1].sample_id, "tinyml/mnist-shell");
  assert.equal(calls[1][1].sample_fingerprint, hash);
  assert.equal(calls[1][1].target, "nucleo_f446re/stm32f446xx");
  assert.equal(Object.hasOwn(calls[1][1], "sample_check_id"), false);
  app.session.dispose();
});
