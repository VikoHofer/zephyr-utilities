import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession, creationHandoff} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";
import {readySample} from "../../src/zephyr_utilities/features/projects/web/sample-state.js";

const one = "/workspace/one";
const two = "/workspace/two";
const id = "a".repeat(32);
const hash = "b".repeat(64);
const environment = "e".repeat(64);
const templates = [
  {id: "minimal-c", version: 2, title: "Minimal C", description: "Create C source.", language: "C"},
  {id: "minimal-cpp", version: 2, title: "Minimal C++", description: "Create C++ source.", language: "C++"},
  {id: "local-sample", version: 1, title: "Local sample", description: "Copy local source.", language: "C"},
];
const form = () => ({name: "my_app", destination: "", search: "", board_id: "", target: "", sdk_id: "",
  template_id: "minimal-c", sample_id: ""});
const snapshot = (workspace = one, changes = {}) => ({id: "observation", fingerprint: environment,
  workspace: {path: workspace}, status: "complete", zephyr: {path: `${workspace}/zephyr`},
  boards: [{id: "board-one", name: "alpha_kit", status: "discovered", targets: ["alpha_kit/chip", "alpha_kit/chip/variant"]}],
  sdks: [{id: "sdk-one", status: "discovered", path: "/sdk", toolchains: [{family: "GNU", compiler: "/sdk/gcc"}]}],
  ...changes});
const response = (workspace = one, changes = {}) => ({workspace, snapshot: snapshot(workspace), templates,
  projects: [], jobs: [], diagnostics: [], ...changes});
const handoff = (changes = {}) => ({schema_version: 1, id, workspace: one, demo_id: "mnist-shell", variant_id: "reference",
  title: "MNIST digit recognition", source_fingerprint: hash, environment_fingerprint: environment,
  selection: {board_id: "board-one", target: "alpha_kit/chip", sdk_id: "sdk-one"},
  template_id: "local-sample", sample_id: "tinyml/mnist-shell", ...changes});
const catalogue = (workspace = one, changes = {}) => ({workspace, zephyr: {path: `${workspace}/zephyr`}, samples: [
  {id: "tinyml/mnist-shell", title: "MNIST digit recognition", description: "Sensor-free inference.",
    path: "bundled:tinyml/mnist", status: "available", fingerprint: hash, file_count: 20,
    requirements: ["A serial console and managed TFLM module."], diagnostics: [], ...changes},
], diagnostics: []});
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function harness({api: customApi, state: savedState} = {}) {
  let workspace = one;
  let routeCurrent = true;
  const calls = [];
  const state = savedState || {workspace: one, snapshot: snapshot(), templates, form: form(), projects: [], jobs: []};
  const api = async (path, options = {}) => {
    calls.push({path, options});
    if (customApi) return customApi(path, options, workspace);
    if (path === "/api/projects/creator") return response(workspace);
    if (path === "/api/projects/samples") return catalogue(workspace);
    if (path.startsWith("/api/projects/creation-handoffs/")) return handoff();
    throw new Error(`Unexpected request: ${path}`);
  };
  const session = createCreatorSession({api, state, workspace: () => workspace, isCreating: () => routeCurrent,
    onChange() {}, schedule: () => 1, unschedule() {}});
  return {state, session, calls, changeWorkspace: (value) => { workspace = value; },
    leaveRoute: () => { routeCurrent = false; },
    resolve: () => session.resolveCreationHandoff(id, {isCurrent: () => routeCurrent})};
}

test("only a complete creation capability route yields a handoff identifier", () => {
  assert.equal(creationHandoff(`create/${id}`), id);
  for (const route of ["create", "applications", `create/${id}/extra`, "create/../environment", `create/${"A".repeat(32)}`]) {
    assert.equal(creationHandoff(route), null);
  }
});

test("a pristine demo handoff accepts the bundled MNIST source and selects its target without creating an application", async (t) => {
  const app = harness(); t.after(() => app.session.dispose());
  await app.session.load();
  await app.resolve();
  assert.equal(app.state.creationHandoff.status, "applied");
  assert.equal(app.state.form.template_id, "local-sample");
  assert.equal(app.state.form.sample_id, "tinyml/mnist-shell");
  assert.equal(app.state.form.board_id, "board-one");
  assert.equal(app.state.form.target, "alpha_kit/chip");
  assert.equal(app.state.form.sdk_id, "sdk-one");
  assert.equal(readySample(app.state).fingerprint, hash);
  assert.equal(app.state.plan, null);
  assert.ok(app.calls.some(({path}) => path === `/api/projects/creation-handoffs/${id}?workspace=${encodeURIComponent(one)}`));
  assert.ok(app.calls.every(({path}) => path === "/api/projects/creator" || path === "/api/projects/samples"
    || path.startsWith("/api/projects/creation-handoffs/")));
});

test("a dirty creation draft requires a choice and Keep preserves all its fields", async (t) => {
  const app = harness(); t.after(() => app.session.dispose());
  await app.session.load();
  app.session.changeField("name", "custom_app");
  app.session.changeField("destination", "applications/custom-destination");
  app.session.changeField("template_id", "minimal-cpp");
  const before = {...app.state.form};
  await app.resolve();
  assert.equal(app.state.creationHandoff.status, "review");
  assert.deepEqual(app.state.form, before);
  app.session.keepCreationDraft();
  assert.equal(app.state.creationHandoff.status, "kept");
  assert.deepEqual(app.state.form, before);
  assert.equal(app.calls.filter(({path}) => path === "/api/projects/samples").length, 1);
});

test("Use demo selection preserves custom name and destination while invalidating old reviews", async (t) => {
  const app = harness(); t.after(() => app.session.dispose());
  await app.session.load();
  app.session.changeField("name", "custom_app");
  app.session.changeField("destination", "applications/custom-destination");
  app.state.plan = {id: "old-review", workspace: one};
  app.state.sampleCheck = {verified: true, compatibility: {status: "supported"}, checkId: "old-check"};
  await app.resolve();
  assert.equal(app.state.creationHandoff.status, "review");
  await app.session.useCreationHandoff();
  assert.equal(app.state.creationHandoff.status, "applied");
  assert.equal(app.state.form.name, "custom_app");
  assert.equal(app.state.form.destination, "applications/custom-destination");
  assert.equal(app.state.form.target, "alpha_kit/chip");
  assert.equal(app.state.form.sample_id, "tinyml/mnist-shell");
  assert.equal(app.state.plan, null);
  assert.notEqual(app.state.sampleCheck?.verified, true);
  assert.notEqual(app.state.sampleCheck?.checkId, "old-check");
});

test("edits made while a handoff request is pending are reviewed before use", async (t) => {
  const pending = deferred();
  const requested = deferred();
  const app = harness({api: async (path) => {
    if (path.startsWith("/api/projects/creation-handoffs/")) { requested.resolve(); return pending.promise; }
    return path === "/api/projects/samples" ? catalogue() : response();
  }});
  t.after(() => app.session.dispose());
  await app.session.load();
  const resolving = app.resolve();
  await requested.promise;
  app.session.changeField("name", "typed_while_loading");
  pending.resolve(handoff()); await resolving;
  assert.equal(app.state.creationHandoff.status, "review");
  assert.equal(app.state.form.name, "typed_while_loading");
  assert.equal(app.state.form.template_id, "minimal-c");
});

test("workspace changes and route changes suppress late resolved selections", async () => {
  for (const change of ["workspace", "route", "dispose"]) {
    const pending = deferred();
    const requested = deferred();
    const app = harness({api: async (path, _options, workspace) => {
      if (path.startsWith("/api/projects/creation-handoffs/")) { requested.resolve(); return pending.promise; }
      return path === "/api/projects/samples" ? catalogue(workspace) : response(workspace);
    }});
    await app.session.load();
    const resolving = app.resolve();
    await requested.promise;
    if (change === "workspace") { app.changeWorkspace(two); await app.session.load(); }
    else if (change === "route") app.leaveRoute();
    else app.session.dispose();
    const before = {...app.state.form};
    pending.resolve(handoff()); await resolving;
    assert.deepEqual(app.state.form, before, `${change} must suppress the late selection`);
    assert.notEqual(app.state.creationHandoff?.status, "applied");
    if (change === "workspace") assert.equal(app.state.workspace, two);
    app.session.dispose();
  }
});

test("leaving while source verification is pending cannot apply the resolved selection", async (t) => {
  const pending = deferred(); const requested = deferred();
  const app = harness({api: async (path) => {
    if (path === "/api/projects/samples") { requested.resolve(); return pending.promise; }
    return path === "/api/projects/creator" ? response() : handoff();
  }});
  t.after(() => app.session.dispose());
  const resolving = app.resolve();
  await requested.promise;
  app.leaveRoute();
  const before = {...app.state.form};
  pending.resolve(catalogue()); await resolving;
  assert.deepEqual(app.state.form, before);
  assert.notEqual(app.state.creationHandoff?.status, "applied");
});

test("Use rechecks source identity and preserves the draft if that source changed", async (t) => {
  let sampleReads = 0;
  const app = harness({api: async (path) => path === "/api/projects/creator" ? response()
    : path === "/api/projects/samples" ? catalogue(one, ++sampleReads === 1 ? {} : {fingerprint: "f".repeat(64)}) : handoff()});
  t.after(() => app.session.dispose());
  app.session.changeField("name", "custom_app");
  await app.resolve();
  assert.equal(app.state.creationHandoff.status, "review");
  const before = {...app.state.form};
  await app.session.useCreationHandoff();
  assert.equal(app.state.creationHandoff.status, "error");
  assert.deepEqual(app.state.form, before);
  assert.equal(sampleReads, 2);
});

test("an edit during explicit Use needs another review before selecting the demo", async (t) => {
  let handoffReads = 0;
  const pending = deferred(); const requested = deferred();
  const app = harness({api: async (path) => {
    if (path === "/api/projects/creator") return response();
    if (path === "/api/projects/samples") return catalogue();
    if (++handoffReads === 1) return handoff();
    requested.resolve(); return pending.promise;
  }});
  t.after(() => app.session.dispose());
  app.session.changeField("name", "custom_app");
  await app.resolve();
  const using = app.session.useCreationHandoff();
  await requested.promise;
  app.session.changeField("destination", "applications/edited-during-use");
  pending.resolve(handoff()); await using;
  assert.equal(app.state.creationHandoff.status, "review");
  assert.equal(app.state.form.template_id, "minimal-c");
  assert.equal(app.state.form.destination, "applications/edited-during-use");
});

test("a late initial creator load cannot overwrite the handoff's authoritative load", async (t) => {
  const initial = deferred(); let loads = 0;
  const app = harness({api: async (path) => {
    if (path === "/api/projects/creator") return ++loads === 1 ? initial.promise : response();
    return path === "/api/projects/samples" ? catalogue() : handoff();
  }});
  t.after(() => app.session.dispose());
  const loading = app.session.load();
  await app.resolve();
  assert.equal(app.state.creationHandoff.status, "applied");
  initial.resolve(response(one, {snapshot: snapshot(one, {boards: [], sdks: []})}));
  await loading;
  assert.equal(app.state.form.target, "alpha_kit/chip");
  assert.equal(app.state.form.sdk_id, "sdk-one");
  assert.equal(app.state.creationHandoff.status, "applied");
});

test("expired and malformed capabilities show an error without replacing the draft", async () => {
  const invalid = [null, handoff({id: "other"}), handoff({workspace: two}), handoff({schema_version: 2}),
    handoff({sample_id: "other/sample"}), handoff({source_fingerprint: "invalid"}),
    handoff({environment_fingerprint: "f".repeat(64)}),
    handoff({selection: {board_id: "unknown", target: "alpha_kit/chip", sdk_id: "sdk-one"}}),
    handoff({selection: {board_id: "board-one", target: "alpha_kit/unlisted", sdk_id: "sdk-one"}}),
    handoff({selection: {board_id: "board-one", target: "alpha_kit/chip", sdk_id: "unavailable"}}),
    new Error("The creation handoff expired. Return to TinyML and select it again.")];
  for (const value of invalid) {
    const app = harness({api: async (path) => {
      if (!path.startsWith("/api/projects/creation-handoffs/")) return response();
      if (value instanceof Error) throw value;
      return value;
    }});
    await app.session.load();
    const before = {...app.state.form};
    await app.resolve();
    assert.equal(app.state.creationHandoff.status, "error");
    assert.ok(app.state.creationHandoff.error);
    assert.deepEqual(app.state.form, before);
    app.session.dispose();
  }
});

test("a changed or unavailable current source cannot retain an applied handoff", async () => {
  for (const changes of [{fingerprint: "c".repeat(64)}, {status: "missing", fingerprint: null, diagnostics: ["Source is unavailable."]},
    {path: "../applications/mnist_shell"}]) {
    const app = harness({api: async (path) => path === "/api/projects/creator" ? response()
      : path === "/api/projects/samples" ? catalogue(one, changes) : handoff()});
    await app.session.load();
    await app.resolve();
    assert.equal(app.state.creationHandoff.status, "error");
    assert.ok(app.state.creationHandoff.error);
    assert.equal(app.state.plan, null);
    assert.equal(app.calls.some(({path}) => path === "/api/projects/preview"), false);
    app.session.dispose();
  }
});

test("dirty state survives workspace round trips and remount even after returning to a default value", async (t) => {
  const original = harness();
  await original.session.load();
  original.session.changeField("name", "edited_app");
  original.session.changeField("name", "my_app");
  original.changeWorkspace(two); await original.session.load();
  original.changeWorkspace(one); await original.session.load();
  original.session.dispose();
  const reopened = harness({state: original.state}); t.after(() => reopened.session.dispose());
  await reopened.session.load();
  await reopened.resolve();
  assert.equal(reopened.state.creationHandoff.status, "review");
  assert.equal(reopened.state.form.name, "my_app");
  assert.equal(reopened.state.form.template_id, "minimal-c");
});
