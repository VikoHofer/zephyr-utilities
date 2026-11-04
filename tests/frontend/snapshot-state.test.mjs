import test from "node:test";
import assert from "node:assert/strict";
import {reconcileSnapshots} from "../../src/zephyr_utilities/features/memory/web/snapshot-state.js";

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
