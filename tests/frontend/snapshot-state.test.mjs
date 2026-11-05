import test from "node:test";
import assert from "node:assert/strict";
import {completedJobSnapshot, reconcileSnapshots} from "../../src/zephyr_utilities/features/memory/web/snapshot-state.js";

function selection() {
  return {
    current: {id: "baseline"}, node: {id: "symbol"}, ancestors: ["root"], snapshotRequest: 2,
    ui: {"baseline-select": "baseline", "candidate-select": "candidate", "symbol-search": "buffer"},
    comparison: {baseline: {id: "baseline"}, candidate: {id: "candidate"}}, compareRequest: 4,
  };
}

test("deleting the open baseline clears its report and comparison while preserving the candidate", () => {
  const state = selection();
  reconcileSnapshots(state, [{id: "candidate"}]);
  assert.equal(state.current, null);
  assert.equal(state.node, null);
  assert.deepEqual(state.ancestors, []);
  assert.equal(state.snapshotRequest, 3);
  assert.equal(state.comparison, null);
  assert.equal(state.compareRequest, 5);
  assert.equal(state.ui["baseline-select"], "");
  assert.equal(state.ui["candidate-select"], "candidate");
  assert.equal(state.ui["symbol-search"], "buffer");
});

test("deleting the candidate leaves the open baseline available and invalidates pending comparisons", () => {
  const state = selection();
  state.comparison = null; // A comparison request can still be running.
  reconcileSnapshots(state, [{id: "baseline"}]);
  assert.equal(state.current.id, "baseline");
  assert.equal(state.snapshotRequest, 2);
  assert.equal(state.ui["candidate-select"], "");
  assert.equal(state.compareRequest, 5);
});

test("deleting an unrelated snapshot preserves the existing report and comparison", () => {
  const state = selection();
  const comparison = state.comparison;
  reconcileSnapshots(state, [{id: "baseline"}, {id: "candidate"}]);
  assert.equal(state.current.id, "baseline");
  assert.equal(state.comparison, comparison);
  assert.equal(state.compareRequest, 4);
});

test("an empty history clears saved choices and stale comparison data after remount", () => {
  const state = selection();
  reconcileSnapshots(state, []);
  assert.deepEqual(state.snapshots, []);
  assert.equal(state.current, null);
  assert.equal(state.comparison, null);
  assert.equal(state.ui["baseline-select"], "");
  assert.equal(state.ui["candidate-select"], "");
  reconcileSnapshots(state, []);
  assert.equal(state.compareRequest, 5, "repeated refreshes should not invalidate again");
});

test("a completed report offers its saved snapshot without changing the report being read", () => {
  const state = selection();
  const before = {current: state.current, node: state.node, ancestors: state.ancestors, comparison: state.comparison};
  const captured = {id: "new-report", name: "New capture"};
  const job = {status: "completed", snapshot_id: captured.id};
  reconcileSnapshots(state, [{id: "baseline"}, {id: "candidate"}, captured]);
  assert.equal(completedJobSnapshot(job, state.snapshots), captured);
  for (const [field, value] of Object.entries(before)) assert.equal(state[field], value);
  assert.equal(state.snapshotRequest, 2);
  assert.equal(state.compareRequest, 4);
});

test("report result availability follows snapshot deletion, refresh and restored job history", () => {
  const captured = {id: "capture"};
  const job = {status: "completed", result: {snapshot_id: captured.id}};
  assert.equal(completedJobSnapshot(job, [captured]), captured);
  assert.equal(completedJobSnapshot(job, []), null, "a deleted capture has no View snapshot destination");
  assert.equal(completedJobSnapshot(JSON.parse(JSON.stringify(job)), [captured]), captured);
  for (const status of ["queued", "running", "failed", "cancelled"]) {
    assert.equal(completedJobSnapshot({...job, status}, [captured]), null);
  }
  assert.equal(completedJobSnapshot(null, [captured]), null);
  assert.equal(completedJobSnapshot({status: "completed"}, [captured]), null);
});
