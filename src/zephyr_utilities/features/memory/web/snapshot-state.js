/** Reconcile saved selections when snapshots are deleted locally or in another tab. */
export function reconcileSnapshots(state, snapshots) {
  const available = new Set(snapshots.map((snapshot) => snapshot.id));
  state.snapshots = snapshots;
  state.ui ||= {};
  if (state.current && !available.has(state.current.id)) {
    state.current = null;
    state.node = null;
    state.ancestors = [];
    state.snapshotRequest = (state.snapshotRequest || 0) + 1;
  }
  let invalidComparison = Boolean(state.comparison && (
    !available.has(state.comparison.baseline.id) || !available.has(state.comparison.candidate.id)
  ));
  for (const field of ["baseline-select", "candidate-select"]) {
    if (state.ui[field] && !available.has(state.ui[field])) {
      state.ui[field] = "";
      invalidComparison = true;
    }
  }
  if (invalidComparison) {
    state.comparison = null;
    state.compareRequest = (state.compareRequest || 0) + 1;
    state.changeLimit = 60;
  }
}
