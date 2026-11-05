import test from "node:test";
import assert from "node:assert/strict";
import {createReportPoller} from "../../src/zephyr_utilities/features/memory/web/job-state.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function harness(afterUpdate = async () => {}) {
  const state = {job: "A", lastJob: null};
  const requests = [], updates = [], errors = [], scheduled = [];
  let disposed = false;
  const poll = createReportPoller({jobId: () => state.job, isDisposed: () => disposed,
    read(id) { const request = {id, ...deferred()}; requests.push(request); return request.promise; },
    async onUpdate(job) {
      updates.push(job); state.lastJob = job;
      if (["completed", "failed", "cancelled"].includes(job.status)) state.job = null;
      await afterUpdate(job);
    },
    onError(error) { errors.push(error.message); },
    schedule(callback, delay) { scheduled.push({callback, delay}); },
  });
  return {state, poll, requests, updates, errors, scheduled, dispose() { disposed = true; }};
}

test("startup and submission share one poll while requesting and while waiting to retry", async () => {
  const app = harness();
  const first = app.poll("A");
  await app.poll("A");
  assert.equal(app.requests.length, 1);
  app.requests[0].resolve({id: "A", status: "running"}); await first;
  assert.equal(app.scheduled.length, 1);
  assert.equal(app.scheduled[0].delay, 700);
  await app.poll("A");
  assert.equal(app.requests.length, 1, "a pending timer still owns this job's polling");
  const next = app.scheduled.shift().callback();
  assert.equal(app.requests.length, 2);
  app.requests[1].resolve({id: "A", status: "completed"}); await next;
  assert.equal(app.state.job, null);
  assert.deepEqual(app.scheduled, []);
});

test("a delayed A result cannot overwrite an accepted B or clear its active job", async () => {
  const app = harness();
  const old = app.poll("A");
  app.state.job = "B";
  const current = app.poll("B");
  app.requests[1].resolve({id: "B", status: "running"}); await current;
  app.requests[0].resolve({id: "A", status: "completed"}); await old;
  assert.equal(app.state.job, "B");
  assert.deepEqual(app.state.lastJob, {id: "B", status: "running"});
  assert.deepEqual(app.updates.map((job) => job.id), ["B"]);
  assert.equal(app.scheduled.length, 1);
  await app.poll("A"); await app.poll("B");
  assert.equal(app.requests.length, 2);
});

test("a retired job's timer and failed response cannot restart polling or display an error", async () => {
  const app = harness();
  const first = app.poll("A");
  app.requests[0].resolve({id: "A", status: "running"}); await first;
  const retry = app.scheduled.shift().callback;
  app.state.job = "B";
  await retry();
  assert.equal(app.requests.length, 1);
  const current = app.poll("B");
  app.state.job = "C";
  app.requests[1].reject(new Error("Old B request failed")); await current;
  assert.deepEqual(app.errors, []);
  assert.deepEqual(app.scheduled, []);
  assert.equal(app.state.job, "C");
});

test("a new job can start while the terminal handler is refreshing saved snapshots", async () => {
  const refresh = deferred();
  const app = harness((job) => job.status === "completed" ? refresh.promise : Promise.resolve());
  const first = app.poll("A");
  app.requests[0].resolve({id: "A", status: "completed"});
  await Promise.resolve();
  assert.equal(app.state.job, null);
  app.state.job = "B";
  const next = app.poll("B");
  app.requests[1].resolve({id: "B", status: "running"}); await next;
  refresh.resolve(); await first;
  await app.poll("B");
  assert.equal(app.requests.length, 2, "finishing A must not release B's polling owner");
  assert.equal(app.state.job, "B");
  assert.equal(app.scheduled.length, 1);
});

test("current failures retry once and an unrelated response never changes the tracked job", async () => {
  const app = harness();
  const first = app.poll("A");
  app.requests[0].reject(new Error("Connection lost")); await first;
  assert.deepEqual(app.errors, ["Connection lost"]);
  assert.equal(app.state.job, "A");
  assert.equal(app.scheduled[0].delay, 3000);
  await app.poll("A");
  assert.equal(app.requests.length, 1);
  const second = app.scheduled.shift().callback();
  app.requests[1].resolve({id: "other", status: "completed"}); await second;
  assert.equal(app.state.job, "A");
  assert.equal(app.state.lastJob, null);
  assert.match(app.errors[1], /another job/);
  assert.equal(app.scheduled.length, 1);
});

test("disposal ignores late responses without clearing the accepted server job", async () => {
  const app = harness();
  const work = app.poll("A");
  app.dispose();
  app.requests[0].resolve({id: "A", status: "completed"}); await work;
  assert.equal(app.state.job, "A");
  assert.deepEqual(app.updates, []);
  assert.deepEqual(app.scheduled, []);
});
