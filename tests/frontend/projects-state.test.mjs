import test from "node:test";
import assert from "node:assert/strict";
import {activeJob, createEnvironmentSession, filterBoards, filterSocs, lines} from "../../src/zephyr_utilities/features/projects/web/environment-state.js";

const socs = [
  {name: "alpha33", family: "Aurora", series: "A3"},
  {name: "beta52", family: "Borealis", series: "B5"},
  {name: "unboarded", family: "New silicon"},
];
const boards = [
  {id: "alpha", name: "alpha_kit", full_name: "Alpha Development Kit", vendor: "Aurora", socs: ["alpha33"], qualifiers: ["alpha33/cpu0/ns"], targets: ["alpha_kit@2.0/alpha33/cpu0/ns"]},
  {id: "beta", name: "beta_eval", vendor: "Borealis", socs: ["beta52"], targets: ["beta_eval/beta52"]},
  {id: "custom", name: "custom_alpha", vendor: "Local", socs: ["alpha33"]},
];

test("board search composes target text, chip family and vendor filters", () => {
  assert.deepEqual(filterBoards(boards, socs, {query: "KIT @2.0"}).map((item) => item.id), ["alpha"]);
  assert.deepEqual(filterBoards(boards, socs, {chip: "AURORA"}).map((item) => item.id), ["alpha", "custom"]);
  assert.deepEqual(filterBoards(boards, socs, {chip: "alpha", vendor: "Local"}).map((item) => item.id), ["custom"]);
  assert.deepEqual(filterBoards(boards, socs, {chip: "unboarded"}), []);
  assert.deepEqual(filterBoards(boards, socs, {query: "alpha", vendor: "Borealis"}), []);
  assert.equal(filterBoards(boards, socs).length, 3);
});

test("chip browsing includes SoCs without boards and metadata can be incomplete", () => {
  assert.deepEqual(filterSocs(socs, "new").map((item) => item.name), ["unboarded"]);
  assert.deepEqual(filterBoards([{name: "bare", socs: ["unindexed"]}], [], {chip: "INDEX"}).map((item) => item.name), ["bare"]);
  assert.equal(filterBoards([{name: "missing"}], [], {query: "missing"}).length, 1);
  assert.deepEqual(lines(" /root with spaces \r\n\n/root-two\n/root with spaces\n"), ["/root with spaces", "/root-two"]);
});

const snapshot = (path, id = "capture") => ({id, workspace: {path}, boards: [], socs: [], sdks: []});
function harness(api, state = {}) {
  let path = "/workspace/one";
  const scheduled = new Map();
  let nextTimer = 0;
  let publications = 0;
  const session = createEnvironmentSession({api, state, workspace: () => path, onChange: () => publications++, schedule: (callback) => { const id = ++nextTimer; scheduled.set(id, callback); return id; }, unschedule: (id) => scheduled.delete(id)});
  return {session, state, scheduled, changeWorkspace(value) { path = value; }, publications: () => publications};
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

test("opening the environment only reads saved observation and recovers an active job", async () => {
  const calls = [];
  const job = {id: "job1", status: "running", logs: []};
  const app = harness(async (path, options) => {
    calls.push([path, options.method]);
    return {workspace: "/workspace/one", snapshot: snapshot("/workspace/one"), job};
  });
  await app.session.load();
  assert.deepEqual(calls, [["/api/projects/environment", undefined]]);
  assert.equal(app.state.snapshot.id, "capture");
  assert.equal(app.state.job.id, "job1");
  assert.equal(activeJob(app.state.job), true);
  assert.equal(app.state.loading, false);
  app.session.dispose();
});

test("analysis submits options once and a completed job publishes the captured snapshot", async () => {
  const calls = [];
  const pending = deferred();
  const app = harness(async (path, options) => {
    calls.push([path, options]);
    if (path === "/api/projects/analyze") return pending.promise;
    return {id: "job2", status: "completed", result: {snapshot: snapshot("/workspace/one", "new")}, logs: ["Discovered"]};
  });
  const options = {force: true, board_roots: ["/root with spaces"]};
  const start = app.session.start(options);
  assert.equal(app.state.submitting, true);
  await app.session.start(options);
  assert.equal(calls.length, 1, "duplicate clicks must not start competing scans");
  assert.deepEqual(calls[0][1].body, options);
  pending.resolve({id: "job2", status: "queued"}); await start;
  await app.session.tick();
  assert.equal(calls[1][0], "/api/jobs/job2");
  assert.equal(app.state.snapshot.id, "new");
  assert.equal(activeJob(app.state.job), false);
  assert.equal(app.state.submitting, false);
  app.session.dispose();
});

test("workspace changes cannot display an old scan result as the new environment", async () => {
  const pending = deferred();
  let reads = 0;
  const app = harness(async (path) => {
    if (path.startsWith("/api/jobs/")) return pending.promise;
    reads++;
    return {workspace: "/workspace/two", snapshot: snapshot("/workspace/two", "two"), job: null};
  }, {workspace: "/workspace/one", snapshot: snapshot("/workspace/one", "one"), job: {id: "old", status: "running"}});
  const tick = app.session.tick();
  app.changeWorkspace("/workspace/two");
  pending.resolve({id: "old", status: "completed", result: {snapshot: snapshot("/workspace/one", "old-result")}});
  await tick;
  await app.session.tick();
  assert.equal(reads, 1);
  assert.equal(app.state.workspace, "/workspace/two");
  assert.equal(app.state.snapshot.id, "two");
  assert.equal(app.state.job, null);
  app.session.dispose();
});

test("stale load responses and foreign snapshot paths are discarded", async () => {
  const first = deferred();
  let count = 0;
  const app = harness(async () => ++count === 1 ? first.promise : {workspace: "/workspace/two", snapshot: snapshot("/workspace/one"), job: null});
  const oldLoad = app.session.load();
  app.changeWorkspace("/workspace/two");
  await app.session.load();
  first.resolve({workspace: "/workspace/one", snapshot: snapshot("/workspace/one"), job: null});
  await oldLoad;
  assert.equal(app.state.workspace, "/workspace/two");
  assert.equal(app.state.snapshot, null);
  assert.equal(app.state.loading, false);
  app.session.dispose();
});

test("leaving aborts local requests and timers while retaining server job identity for remount", async () => {
  const pending = deferred();
  const calls = [];
  let signal;
  const app = harness(async (path, options) => { calls.push(path); signal = options.signal; return pending.promise; }, {workspace: "/workspace/one", job: {id: "running", status: "running"}});
  const tick = app.session.tick();
  const before = app.publications();
  app.session.dispose();
  assert.equal(signal.aborted, true);
  assert.equal(app.scheduled.size, 0);
  assert.equal(app.state.job.id, "running");
  pending.resolve({id: "running", status: "completed", result: {snapshot: snapshot("/workspace/one")}}); await tick;
  assert.equal(app.publications(), before);
  assert.equal(app.state.snapshot, undefined);
  assert.deepEqual(calls, ["/api/jobs/running"], "disposal must never cancel the server job");
  const reopened = harness(async () => ({workspace: "/workspace/one", snapshot: snapshot("/workspace/one", "finished"), job: {id: "running", status: "completed"}}), app.state);
  await reopened.session.load();
  assert.equal(reopened.state.snapshot.id, "finished");
  reopened.session.dispose();
});

test("explicit cancellation and failed scans retain the previous successful observation", async () => {
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.method]);
    assert.deepEqual(options.body, {}, "shared POST transport requires an explicit JSON object");
    return {id: "scan", status: "cancelled", logs: ["Job cancelled."]};
  }, {workspace: "/workspace/one", snapshot: snapshot("/workspace/one", "baseline"), job: {id: "scan", status: "running"}});
  await app.session.cancel();
  assert.deepEqual(calls, [["/api/jobs/scan/cancel", "POST"]]);
  assert.equal(app.state.snapshot.id, "baseline");
  assert.equal(app.state.cancelling, false);
  app.session.dispose();
  const failed = harness(async () => ({id: "bad", status: "failed", error: "Missing checkout"}), {workspace: "/workspace/one", snapshot: snapshot("/workspace/one", "baseline")});
  await failed.session.start();
  assert.equal(failed.state.error, "Missing checkout");
  assert.equal(failed.state.snapshot.id, "baseline");
  failed.session.dispose();
});

test("saved snapshots retain freshness and cannot be replaced by an older retained job", async () => {
  const saved = {...snapshot("/workspace/one", "newer"), freshness: "saved-observation"};
  const oldResult = snapshot("/workspace/one", "older");
  const app = harness(async () => ({workspace: "/workspace/one", snapshot: saved, job: {id: "old", status: "completed", result: {snapshot: oldResult}}}));
  await app.session.load();
  assert.equal(app.state.snapshot.id, "newer");
  assert.equal(app.state.snapshot.freshness, "saved-observation");
  assert.equal(app.state.job.id, "old");
  app.session.dispose();
});

test("additional paths are isolated per workspace and restored when returning", async () => {
  let returnedPath = "/workspace/one";
  const app = harness(async () => ({workspace: returnedPath, snapshot: null, job: null}));
  app.state.options.zephyr_base = "/workspace/one/vendor-zephyr";
  app.state.options.board_roots = "/workspace/one/hardware";
  app.state.options.sdk_paths = "/workspace/one/custom-sdk";
  app.changeWorkspace("/workspace/two"); returnedPath = "/workspace/two";
  await app.session.tick();
  assert.deepEqual(app.state.options, {zephyr_base: "", board_roots: "", soc_roots: "", sdk_paths: ""});
  app.state.options.soc_roots = "/workspace/two/silicon";
  app.changeWorkspace("/workspace/one"); returnedPath = "/workspace/one";
  await app.session.tick();
  assert.equal(app.state.options.zephyr_base, "/workspace/one/vendor-zephyr");
  assert.equal(app.state.options.board_roots, "/workspace/one/hardware");
  assert.equal(app.state.options.sdk_paths, "/workspace/one/custom-sdk");
  assert.equal(app.state.options.soc_roots, "");
  app.session.dispose();
});

test("scan options are read after a workspace switch even before the next timer tick", async () => {
  let submitted;
  const app = harness(async (_, options) => { submitted = options.body; return {id: "new-workspace", status: "queued"}; });
  app.state.options.board_roots = "/workspace/one/hardware";
  app.changeWorkspace("/workspace/two");
  await app.session.start(() => ({board_roots: lines(app.state.options.board_roots)}));
  assert.deepEqual(submitted, {board_roots: []});
  assert.equal(app.state.workspace, "/workspace/two");
  app.session.dispose();
});

test("request errors release controls and can be retried", async () => {
  let attempts = 0;
  const app = harness(async () => { if (++attempts === 1) throw new Error("Invalid root"); return {id: "retry", status: "queued"}; });
  await app.session.start();
  assert.equal(app.state.error, "Invalid root");
  assert.equal(app.state.submitting, false);
  await app.session.start();
  assert.equal(app.state.error, "");
  assert.equal(app.state.job.id, "retry");
  app.session.dispose();
});
