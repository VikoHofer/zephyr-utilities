import test from "node:test";
import assert from "node:assert/strict";
import {createActionFeedback} from "../../src/zephyr_utilities/features/inspector/web/action-feedback.js";

function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; }
function harness() {
  let workspace = "/one", disposed = false;
  const revealed = [], notices = [];
  const feedback = createActionFeedback({workspace: () => workspace, isDisposed: () => disposed,
    reveal: (element) => revealed.push(element), notify: (message) => notices.push(message)});
  return {feedback, revealed, notices, switchWorkspace() { workspace = "/two"; }, dispose() { disposed = true; }};
}

test("an explicit action acknowledges pending work and reveals its rendered result once", async () => {
  const app = harness(), request = deferred(), target = {name: "Build context"};
  let rendered = false;
  const work = app.feedback.run(async () => { await request.promise; rendered = true; }, () => rendered ? target : null,
    {pending: "Reading build files…", complete: () => "Build inspection updated."});
  assert.deepEqual(app.notices, ["Reading build files…"]);
  assert.deepEqual(app.revealed, []);
  request.resolve(); await work;
  assert.deepEqual(app.revealed, [target]);
  assert.deepEqual(app.notices, ["Reading build files…", "Build inspection updated."]);
});

test("an action can reveal an inline error instead of a result", async () => {
  const app = harness(), error = {name: "File no longer exists"};
  await app.feedback.run(async () => {}, () => error);
  assert.deepEqual(app.revealed, [error]);
});

test("late completion cannot steal focus after workspace change, disposal or invalidation", async () => {
  for (const abandon of [(app) => app.switchWorkspace(), (app) => app.dispose(), (app) => app.feedback.invalidate()]) {
    const app = harness(), request = deferred();
    const work = app.feedback.run(() => request.promise, () => "old result", {complete: () => "Old result complete"});
    abandon(app); request.resolve(); await work;
    assert.deepEqual(app.revealed, []);
    assert.deepEqual(app.notices, []);
  }
});

test("a later selection supersedes an earlier request's result reveal", async () => {
  const app = harness(), first = deferred();
  const old = app.feedback.run(() => first.promise, () => "old selection");
  await app.feedback.run(async () => {}, () => "current selection");
  first.resolve(); await old;
  assert.deepEqual(app.revealed, ["current selection"]);
});

test("missing targets and passive state updates produce no reveal", async () => {
  const app = harness();
  assert.deepEqual(app.revealed, []);
  await app.feedback.run(async () => {}, () => null, {complete: () => "Not used"});
  app.feedback.invalidate();
  assert.deepEqual(app.revealed, []);
  assert.deepEqual(app.notices, []);
});
