import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";
import {currentSampleCompatibility, sampleCheckSelection, sampleCompatibilityNotice, sampleSelectionError, validateSampleCheckResult} from "../../src/zephyr_utilities/features/projects/web/sample-state.js";

const workspace = "/workspace";
const fingerprint = "a".repeat(64);
const checkId = "b".repeat(32);
const sample = {id: "basic/blinky", path: "samples/basic/blinky", title: "Blinky", description: "Blink a light.", status: "available",
  fingerprint, file_count: 6, requirements: ["An LED output."], diagnostics: []};
const catalogue = () => ({workspace, zephyr: {path: `${workspace}/zephyr`}, samples: [sample], diagnostics: []});
const snapshot = () => ({workspace: {path: workspace}, fingerprint: "environment", zephyr: {path: `${workspace}/zephyr`}, status: "complete",
  boards: [{id: "board", name: "board", status: "discovered", targets: ["board/chip", "board/other"]}],
  sdks: [{id: "sdk", status: "discovered", toolchains: [{family: "GNU", compiler: "/sdk/gcc"}]}]});
const templates = [{id: "local-sample", version: 1, title: "Local sample", description: "Copy a sample", language: "C"}];
const form = () => ({template_id: "local-sample", sample_id: sample.id, board_id: "board", target: "board/chip", sdk_id: "sdk", name: "my_app", destination: "", search: ""});
const response = () => ({workspace, snapshot: snapshot(), templates, projects: [], jobs: [], diagnostics: []});
const selection = (changes = {}) => ({workspace, sample_id: sample.id, sample_fingerprint: fingerprint, board_id: "board", target: "board/chip", sdk_id: "sdk", environment_fingerprint: "environment", ...changes});
const job = (status = "queued", changes = {}) => ({id: "check-job", status, logs: [], metadata: {workspace, operation: "sample-check"}, ...changes});
const completed = (status = "compatible", changes = {}) => job("completed", {result: {check_id: checkId,
  compatibility: {...selection(), status, message: `${status} result`, details: ["Resolved hardware requirements inspected."], ...changes}}});
function harness(api, existing) {
  let selectedWorkspace = workspace;
  const state = existing || {workspace, snapshot: snapshot(), templates, form: form(), projects: [], jobs: []};
  const calls = [];
  const session = createCreatorSession({api: async (path, options) => { calls.push([path, options.body]); return path === "/api/projects/samples" ? catalogue() : api(path, options); },
    state, workspace: () => selectedWorkspace, isCreating: () => true, onChange() {}, schedule: () => 1, unschedule() {}});
  return {state, session, calls, changeWorkspace(path) { selectedWorkspace = path; }};
}
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }

test("compatibility checking is explicit, separately polled, and submits only the selected inputs", async () => {
  const app = harness(async (path) => path === "/api/projects/sample-check" ? job() : path.startsWith("/api/jobs/") ? completed() : response());
  await app.session.loadSamples();
  app.session.changeField("target", "board/other");
  app.session.changeField("target", "board/chip");
  assert.deepEqual(app.calls.map(([path]) => path), ["/api/projects/samples"]);
  assert.equal(sampleCompatibilityNotice(app.state).status, "unknown");
  await app.session.checkSample();
  assert.deepEqual(app.calls[1], ["/api/projects/sample-check", {workspace, sample_id: sample.id, sample_fingerprint: fingerprint, board_id: "board", target: "board/chip", sdk_id: "sdk"}]);
  assert.equal(sampleCompatibilityNotice(app.state).status, "checking");
  assert.deepEqual(app.state.jobs, [], "sample checks do not occupy the creation/build result panel");
  await app.session.tick();
  assert.equal(currentSampleCompatibility(app.state).status, "compatible");
  assert.equal(app.state.sampleCheck.checkId, checkId);
  app.session.dispose();
});

test("unchecked and unknown samples can preview but a known incompatible selection cannot", async () => {
  for (const status of [null, "unknown", "compatible", "incompatible"]) {
    const app = harness(async (path) => path === "/api/projects/sample-check" ? completed(status) : {plan: {id: "preview", workspace}});
    await app.session.loadSamples();
    if (status) await app.session.checkSample();
    await app.session.preview();
    const previews = app.calls.filter(([path]) => path === "/api/projects/preview");
    assert.equal(previews.length, status === "incompatible" ? 0 : 1);
    if (status === "incompatible") {
      assert.match(sampleSelectionError(app.state), /incompatible/);
      assert.equal(currentSampleCompatibility(app.state).status, "incompatible", "blocked local preview must not clear known incompatibility");
    } else assert.equal(previews[0][1].sample_check_id, status ? checkId : undefined);
    app.session.dispose();
  }
});

test("changing sample, template, board, target or SDK invalidates evidence and the review", async () => {
  for (const [key, value] of [["sample_id", "hello_world"], ["template_id", "minimal-c"], ["board_id", "another-board"], ["target", "board/other"], ["sdk_id", "other-sdk"]]) {
    const app = harness(async () => completed());
    await app.session.loadSamples(); await app.session.checkSample();
    app.state.plan = {id: "review"};
    app.session.changeField(key, value);
    assert.equal(currentSampleCompatibility(app.state), null, key);
    assert.equal(app.state.sampleCheck.checkId, null, key);
    assert.equal(app.state.plan, null, key);
    assert.equal(app.calls.filter(([path]) => path === "/api/projects/sample-check").length, 1, "selection never starts another Configure");
    app.session.dispose();
  }
});

test("sample refresh and a changed environment invalidate checked evidence", async () => {
  for (const change of ["samples", "environment"]) {
    const app = harness(async (path) => path === "/api/projects/sample-check" ? completed() : {...response(), snapshot: {...snapshot(), fingerprint: "changed"}});
    await app.session.loadSamples(); await app.session.checkSample();
    if (change === "samples") await app.session.loadSamples();
    else await app.session.load({background: true});
    assert.equal(currentSampleCompatibility(app.state), null);
    assert.equal(app.state.sampleCheck.checkId, null);
    app.session.dispose();
  }
});

test("an old selection's completion cannot certify a new target or restore an old preview", async () => {
  const pending = deferred();
  const app = harness(async (path) => path === "/api/projects/sample-check" ? job() : pending.promise);
  await app.session.loadSamples(); await app.session.checkSample();
  const poll = app.session.tick();
  app.session.changeField("target", "board/other");
  pending.resolve(completed()); await poll;
  assert.equal(app.state.sampleCheck.stale, true);
  assert.equal(currentSampleCompatibility(app.state), null);
  assert.equal(app.state.sampleCheck.checkId, null);
  assert.match(sampleCompatibilityNotice(app.state).message, /Selection changed/);
  app.session.dispose();
});

test("a newly completed check invalidates an unchecked preview still in flight", async () => {
  const pending = deferred();
  const app = harness(async (path) => path === "/api/projects/sample-check" ? job() : path.startsWith("/api/jobs/") ? completed("incompatible") : pending.promise);
  await app.session.loadSamples(); await app.session.checkSample();
  const preview = app.session.preview();
  await app.session.tick();
  pending.resolve({plan: {id: "unchecked", workspace}}); await preview;
  assert.equal(app.state.plan, null);
  assert.equal(currentSampleCompatibility(app.state).status, "incompatible");
  app.session.dispose();
});

test("mismatched and malformed completed checks cannot authorize a preview token", async () => {
  const invalid = [completed("compatible", {target: "elsewhere"}), completed("compatible", {environment_fingerprint: "old"}),
    completed("supported"), completed("compatible", {details: "not an array"}), {...completed(), result: {...completed().result, check_id: "invalid"}}];
  for (const result of invalid) {
    assert.throws(() => validateSampleCheckResult(result.result, selection()), /does not match/);
    const app = harness(async () => result);
    await app.session.loadSamples(); await app.session.checkSample();
    assert.equal(currentSampleCompatibility(app.state), null);
    assert.equal(app.state.sampleCheck.checkId, null);
    assert.match(app.state.sampleCheck.error, /Check compatibility again/);
    app.session.dispose();
  }
});

test("adaptation files and messages remain attached to a validated compatible result", async () => {
  const adaptation = {id: "rgb-blinky", version: 1, title: "RGB LED", message: "Use the board's addressable LED.", files: [{path: "src/main.c", sha256: fingerprint}]};
  const app = harness(async () => completed("compatible", {adaptation}));
  await app.session.loadSamples(); await app.session.checkSample();
  assert.deepEqual(sampleCompatibilityNotice(app.state).adaptation, adaptation);
  assert.deepEqual(sampleCheckSelection(app.state), selection());
  app.session.dispose();
});

test("cancellation wins over an older poll and stale owners cannot replace a subsequent check", async () => {
  const pending = deferred();
  let starts = 0;
  const app = harness(async (path) => path === "/api/projects/sample-check" ? (++starts === 1 ? job() : completed("unknown"))
    : path.endsWith("/cancel") ? job("cancelled") : pending.promise);
  await app.session.loadSamples(); await app.session.checkSample();
  const poll = app.session.tick();
  await app.session.cancelSampleCheck();
  assert.equal(app.state.sampleCheck.job.status, "cancelled");
  await app.session.checkSample();
  pending.resolve(completed("incompatible")); await poll;
  assert.equal(currentSampleCompatibility(app.state).status, "unknown");
  assert.equal(app.calls.filter(([path]) => path.endsWith("/cancel")).length, 1);
  app.session.dispose();
});

test("failed checks and expired job lookups offer a retry and never claim compatibility", async () => {
  for (const failure of ["job", "lookup"]) {
    const app = harness(async (path) => {
      if (path === "/api/projects/sample-check") return failure === "job" ? job("failed", {error: "Tool unavailable."}) : job();
      throw new Error("Job not found.");
    });
    await app.session.loadSamples(); await app.session.checkSample(); await app.session.tick();
    assert.equal(currentSampleCompatibility(app.state), null);
    assert.match(sampleCompatibilityNotice(app.state).message, /Check compatibility|check again/);
    assert.equal(sampleSelectionError(app.state), "", "unknown check outcomes still allow a warned preview");
    app.session.dispose();
  }
});

test("remount revalidates retained job evidence through a fresh catalogue before using its token", async () => {
  const original = harness(async () => completed());
  await original.session.loadSamples(); await original.session.checkSample(); original.session.dispose();
  const reopened = harness(async (path) => path.startsWith("/api/jobs/") ? completed() : response(), original.state);
  assert.equal(reopened.state.sampleCheck.checkId, null);
  assert.equal(currentSampleCompatibility(reopened.state), null);
  await reopened.session.load();
  assert.equal(currentSampleCompatibility(reopened.state).status, "compatible");
  assert.deepEqual(reopened.calls.map(([path]) => path), ["/api/projects/creator", "/api/projects/samples", "/api/jobs/check-job"]);
  reopened.session.dispose();
});

test("expired preview tokens are discarded with actionable recheck feedback", async () => {
  const app = harness(async (path) => {
    if (path === "/api/projects/sample-check") return completed();
    throw new Error("Compatibility check expired.");
  });
  await app.session.loadSamples(); await app.session.checkSample(); await app.session.preview();
  assert.equal(app.state.sampleCheck.checkId, null);
  assert.equal(currentSampleCompatibility(app.state), null);
  assert.match(app.state.error, /Check compatibility again/);
  app.session.dispose();
});

test("workspace switching and disposal ignore delayed results without cancelling accepted server jobs", async () => {
  for (const departure of ["workspace", "dispose"]) {
    const pending = deferred();
    const app = harness(async (path) => path === "/api/projects/sample-check" ? job() : path.startsWith("/api/jobs/") ? pending.promise : {workspace: "/other", projects: [], jobs: []});
    await app.session.loadSamples(); await app.session.checkSample();
    const poll = app.session.tick();
    if (departure === "workspace") { app.changeWorkspace("/other"); await app.session.load(); }
    else app.session.dispose();
    pending.resolve(completed()); await poll;
    assert.equal(currentSampleCompatibility(app.state), null);
    assert.equal(app.calls.some(([path]) => path.endsWith("/cancel")), false);
    app.session.dispose();
  }
});
