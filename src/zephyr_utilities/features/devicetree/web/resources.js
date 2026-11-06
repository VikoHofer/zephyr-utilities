import {createDraftObservationSession} from "./draft-observation.js";

const PREFIX = "/api/devicetree/editor/drafts/";
const array = (value) => Array.isArray(value);

export function createResourceSession(options) {
  return createDraftObservationSession({...options, route: "resources", stateKey: "resourceView", allowIncomplete: true,
    validResponse: (data) => array(data.diagnostics) && array(data.nodes) && array(data.graph?.references),
    labels: {
      changed: "These diagnostics describe an earlier draft. Load diagnostics to inspect the current source; old results do not block editing or Save.",
      cancelled: "Resource inspection cancelled. The working draft is unchanged.",
      refreshing: "Refreshing resource diagnostics for the updated draft…",
      blocked: "Finish or reconcile the current draft request before loading diagnostics.",
      mismatch: "The resource response does not match this draft. Load diagnostics again.",
      updated: "Resource diagnostics now describe the updated draft. Save remains separate.",
      loaded: "Diagnostics describe the current observed draft. Unknown evidence is listed separately from conflicts.",
      rejected: "The resource command was not accepted. Reconcile the draft before another action.",
      pending: "Draft reconciled. Sync retained source before loading diagnostics.",
      reconciled: "Draft reconciled. Load diagnostics again before reviewing removal.",
    }});
}

/** A removal review never mutates source until its explicit command is accepted. */
export function createRemovalSession({api, state, workspace, drafts, resources, onChange}) {
  const view = state.removalView = {busy: false, applying: false, review: null, outcome: null, error: "", message: "", selected: [], previewDirty: false};
  let disposed = false; let generation = 0; let request; let release; let applying = false;
  const publish = () => { if (!disposed) onChange(); };
  const entry = () => state.draftView?.entry;
  const guard = () => {
    const current = entry();
    if (!state.draftView?.ready || !current?.server || state.data?.workspace !== workspace()) return null;
    return {workspace: workspace(), context_id: state.data.id, expected_revision: state.data.revision,
      draft_id: current.server.id, expected_draft_revision: current.server.revision,
      expected_draft_sha256: current.server.sha256, local_generation: current.generation};
  };
  const same = (captured) => captured && !disposed && !drafts.pending() && JSON.stringify(guard()) === JSON.stringify(captured);
  const body = (captured) => { const {local_generation: _local, ...fields} = captured; return fields; };
  const unlock = () => { const done = release; release = null; done?.(); };
  const canStart = () => Boolean(resources.ready() && !state.resourceView.busy && !entry()?.server.syntax?.blocked);
  function cancel({message = ""} = {}) {
    generation++; request?.abort(); request = null;
    view.review = null; view.outcome = null; view.busy = false; view.applying = false; view.error = ""; view.message = message;
    view.selected = []; view.previewDirty = false; unlock(); publish();
  }
  function sync() {
    if (view.review && !applying && !same(view.review.captured)) {
      cancel({message: "The draft or context changed. Review removal again against the current source."});
    }
  }
  function valid(data, captured, schema, nodeId) {
    return data?.schema_version === 1 && typeof data.review_id === "string" && data.schema_id === schema
      && data.draft_id === captured.draft_id && data.revision === captured.expected_draft_revision
      && data.sha256 === captured.expected_draft_sha256 && data.target?.node_id === nodeId
      && ["descendants", "dependants", "repair_choices", "blockers", "unknowns"].every((key) => array(data[key]))
      && typeof data.can_apply === "boolean" && typeof data.diff?.text === "string"
      && (data.candidate_text === null || typeof data.candidate_text === "string") && Number.isFinite(Date.parse(data.expires_at));
  }
  async function preview(captured, schema, nodeId, repairs) {
    const token = ++generation; const controller = new AbortController(); request = controller;
    view.busy = true; view.error = ""; publish();
    try {
      const data = await api(PREFIX + "removal/review", {method: "POST",
        body: {...body(captured), schema_id: schema, node_id: nodeId, repairs}, signal: controller.signal});
      if (token !== generation || !same(captured)) return false;
      if (!valid(data, captured, schema, nodeId)) throw new Error("The removal review does not match this draft. Close it and review again.");
      view.review = {data, captured, schema};
      view.selected = data.repair_choices.filter((item) => item.selected).map((item) => item.id);
      view.previewDirty = false; view.message = ""; return true;
    } catch (error) {
      if (token === generation && same(captured)) { view.error = error.message || String(error); view.previewDirty = Boolean(view.review); }
      return false;
    } finally {
      if (request === controller) request = null;
      if (!disposed && token === generation) { view.busy = false; if (!view.review) unlock(); publish(); }
    }
  }
  async function begin(nodeId) {
    if (disposed || view.busy || typeof nodeId !== "string") return false;
    cancel();
    if (!canStart()) { view.error = "Load a complete, reconciled draft before reviewing removal."; publish(); return false; }
    const data = state.resourceView.data; const captured = guard();
    if (!data || !captured || data.draft_id !== captured.draft_id || data.revision !== captured.expected_draft_revision || data.sha256 !== captured.expected_draft_sha256) {
      view.error = "Load current resource diagnostics before reviewing removal."; publish(); return false;
    }
    release = drafts.pause();
    if (!release) { view.error = "Another review is using this draft. Close it before reviewing removal."; publish(); return false; }
    return preview(captured, data.schema_id, nodeId, []);
  }
  function select(id, checked) {
    const review = view.review;
    if (!review || view.busy || !same(review.captured) || !review.data.repair_choices.some((item) => item.id === id)) return false;
    view.selected = checked ? [...new Set([...view.selected, id])] : view.selected.filter((item) => item !== id);
    view.previewDirty = true; view.error = ""; publish(); return true;
  }
  function update() {
    const review = view.review;
    if (!review || view.busy || !same(review.captured)) return Promise.resolve(false);
    return preview(review.captured, review.schema, review.data.target.node_id, [...view.selected]);
  }
  function canApply() {
    const review = view.review;
    return Boolean(review && !view.busy && !view.previewDirty && same(review.captured) && review.data.can_apply
      && !entry()?.uncertain && !entry()?.conflict && Date.parse(review.data.expires_at) > Date.now());
  }
  async function apply() {
    if (!canApply()) { view.error = "Update an unexpired removal preview before applying it."; publish(); return false; }
    const review = view.review; const token = ++generation;
    applying = true; view.applying = true; view.busy = true; view.error = "";
    unlock(); publish();
    try {
      const accepted = await drafts.command({kind: "remove_reviewed", review_id: review.data.review_id});
      const current = guard();
      if (disposed || token !== generation || !current || current.workspace !== review.captured.workspace
          || current.context_id !== review.captured.context_id || current.draft_id !== review.captured.draft_id) return false;
      if (!accepted) { view.error = entry()?.error || "The removal was not accepted. Reconcile the draft before reviewing again."; return false; }
      view.review = null; view.selected = []; view.previewDirty = false;
      view.message = "Removal and the selected repairs are in the working draft. Undo is available; review and Save the overlay separately.";
      view.outcome = {message: view.message};
      resources.sync(); return true;
    } catch (error) {
      if (!disposed && token === generation) view.error = error.message || String(error);
      return false;
    } finally {
      applying = false;
      if (!disposed && token === generation) { view.busy = false; view.applying = false; publish(); }
    }
  }
  return {begin, select, update, apply, canApply, canStart, sync, cancel,
    dispose() { disposed = true; generation++; request?.abort(); unlock(); view.review = null; },
  };
}
