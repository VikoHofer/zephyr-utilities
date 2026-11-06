/** Revision-bound metadata and commands share the canonical draft lifecycle. */
const PREFIX = "/api/devicetree/editor/drafts/";

/** Presentation survives a reviewed Save's new context identity for the same document. */
export function draftPresentationScope(state, workspace) {
  const context = state.data; const current = state.draftView?.entry;
  if (!current?.server || context?.workspace !== workspace) return null;
  const subject = context.application?.path ? [context.workspace_identity, context.application.path,
    context.application.identity, context.project?.id, context.configuration?.id, context.target?.requested] : context.id;
  return JSON.stringify([workspace, subject, current.path, current.server.document_id, current.server.id]);
}

export function createDraftObservationSession({api, state, workspace, drafts, onChange,
  route, stateKey, labels, validResponse = (data) => Array.isArray(data.nodes) && Array.isArray(data.targets)}) {
  const view = state[stateKey] = {busy: false, data: null, presentation: null, error: "", message: "", captured: null};
  let disposed = false;
  let generation = 0;
  let loading;
  let applying;
  let reconciling;
  let presented;
  const publish = () => { if (!disposed) onChange(); };
  const entry = () => state.draftView?.entry;
  const presentationScope = () => draftPresentationScope(state, workspace());
  function capture() {
    const current = entry();
    if (!state.draftView?.ready || !current?.server || state.data?.workspace !== workspace()) return null;
    return {workspace: workspace(), context_id: state.data.id, expected_revision: state.data.revision,
      draft_id: current.server.id, expected_draft_revision: current.server.revision,
      expected_draft_sha256: current.server.sha256, local_generation: current.generation};
  }
  function matches(captured) {
    const current = capture();
    return Boolean(captured && current && !drafts.pending()
      && Object.keys(current).every((key) => captured[key] === current[key]));
  }
  function ready() {
    const current = entry();
    return Boolean(capture() && !drafts.pending() && !current.flight && !current.historyOperation
      && !current.externalOperation && !current.uncertain && !current.conflict && !current.server.syntax?.blocked);
  }
  function needsReconcile() { return Boolean(capture() && entry()?.uncertain && !entry()?.conflict); }
  function canReconcile() {
    const current = entry();
    return !disposed && !view.busy && needsReconcile() && !current.flight && !current.historyOperation && !current.externalOperation;
  }
  function sync() {
    if (disposed) return;
    if (presented && presented !== presentationScope()) {
      view.presentation = null; presented = null;
    }
    if (view.captured && (!matches(view.captured) || entry()?.uncertain || entry()?.conflict)) {
      view.data = null; view.captured = null;
      if (!applying) view.message = labels.changed;
      view.error = entry()?.uncertain || entry()?.conflict ? entry()?.error || view.error : "";
    }
  }
  function clear() {
    generation++; loading?.abort(); loading = null; applying = null; reconciling = null;
    view.busy = false; view.data = null; view.presentation = null; view.captured = null; presented = null;
    view.error = ""; view.message = "";
  }
  function cancel() {
    if (!loading || applying) return;
    generation++; loading.abort(); loading = null; view.busy = false;
    view.error = ""; view.message = labels.cancelled; publish();
  }
  async function load({afterCommand = false} = {}) {
    if (disposed || loading || !afterCommand && view.busy) return false;
    view.error = "";
    view.message = afterCommand ? labels.refreshing : "";
    if (!ready()) { view.error = labels.blocked; publish(); return false; }
    const captured = capture(); const token = ++generation; const request = new AbortController();
    loading = request; view.busy = true; publish();
    const {local_generation: _local, ...guard} = captured;
    try {
      const data = await api(PREFIX + route, {method: "POST", body: guard, signal: request.signal});
      if (disposed || token !== generation || !matches(captured)) return false;
      if (!data || typeof data.schema_id !== "string" || data.draft_id !== captured.draft_id
          || data.revision !== captured.expected_draft_revision || data.sha256 !== captured.expected_draft_sha256
          || !validResponse(data)) {
        throw new Error(labels.mismatch);
      }
      // Keep the last observation visible while its replacement is read. Only data
      // and captured authorize a command; presentation is never a mutation guard.
      view.data = data; view.captured = captured; view.presentation = data; presented = presentationScope();
      view.message = afterCommand ? labels.updated
        : labels.loaded;
      return true;
    } catch (error) {
      if (!disposed && token === generation && matches(captured)) view.error = error.message || String(error);
      return false;
    } finally {
      if (loading === request) loading = null;
      if (!disposed && token === generation) { view.busy = false; publish(); }
    }
  }
  async function command(command) {
    sync();
    if (disposed || view.busy || !view.data || !matches(view.captured) || !ready()) return false;
    const captured = view.captured; const token = generation; const schema = view.data.schema_id; const request = {};
    applying = request; view.busy = true; view.error = ""; view.message = "Updating the overlay draft…"; publish();
    try {
      const accepted = await drafts.command({...command, schema_id: schema});
      if (disposed || token !== generation || workspace() !== captured.workspace
          || state.data?.id !== captured.context_id || state.draftView?.entry?.server?.id !== captured.draft_id) return false;
      if (!accepted) {
        view.error = entry()?.error || labels.rejected;
        view.message = ""; return false;
      }
      view.data = null; view.captured = null;
      view.busy = false;
      return await load({afterCommand: true});
    } catch (error) {
      if (!disposed && token === generation && workspace() === captured.workspace) {
        view.error = error.message || String(error); view.message = "";
      }
      return false;
    } finally {
      if (applying === request) {
        applying = null;
        if (!disposed) { view.busy = Boolean(loading); sync(); publish(); }
      }
    }
  }
  async function reconcile() {
    if (!canReconcile()) return false;
    const captured = capture(); const token = ++generation; const request = {};
    reconciling = request; view.busy = true; view.data = null; view.captured = null;
    view.error = ""; view.message = "Reconciling the canonical draft…"; publish();
    const current = () => !disposed && token === generation && workspace() === captured.workspace
      && state.data?.id === captured.context_id && state.data?.revision === captured.expected_revision
      && entry()?.server?.id === captured.draft_id;
    try {
      const accepted = await drafts.reconcile();
      if (!current()) return false;
      if (!accepted) {
        view.error = entry()?.error || "The draft could not be reconciled. Retained source remains available.";
        view.message = ""; return false;
      }
      view.message = drafts.pending() ? labels.pending
        : labels.reconciled;
      return true;
    } catch (error) {
      if (current()) { view.error = error.message || String(error); view.message = ""; }
      return false;
    } finally {
      if (reconciling === request) {
        reconciling = null;
        if (current()) { view.busy = false; sync(); publish(); }
      }
    }
  }
  return {load, command, sync, ready, clear, cancel, reconcile, needsReconcile, canReconcile,
    scanning: () => Boolean(loading && !applying), reconciling: () => Boolean(reconciling),
    fail(message) { view.error = message; view.message = ""; publish(); },
    dispose() { disposed = true; clear(); },
  };
}
