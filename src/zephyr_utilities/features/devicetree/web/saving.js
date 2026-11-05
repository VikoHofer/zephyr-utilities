/** Reviewed source publication and explicit recovery; never writes on passive render. */
import {draftKey} from "./drafts.js";

const PREFIX = "/api/devicetree/editor/drafts/";
const array = (value) => Array.isArray(value) ? value : [];

export function createSaveSession({api, state, workspace, drafts, adopt, onChange}) {
  state.saveView = {busy: false, review: null, error: "", message: "", recoveries: null, recoveryLoading: false, recoveryError: ""};
  const view = state.saveView;
  let disposed = false;
  let generation = 0;
  let listGeneration = 0;
  let currentWorkspace = workspace();
  let release;
  const publish = () => { if (!disposed) onChange(); };
  const post = (path, body) => api(PREFIX + path, {method: "POST", body});
  const currentEntry = () => state.draftView?.entry;
  const noticeIdentity = () => JSON.stringify([workspace(), state.data?.id, state.data?.revision,
    state.document?.id, currentEntry()?.server?.id, currentEntry()?.server?.revision,
    currentEntry()?.server?.sha256, currentEntry()?.generation]);
  let noticeStamp = noticeIdentity();

  function capture() {
    const context = state.data;
    const document = state.document;
    const entry = currentEntry();
    if (!context || !document || !entry?.server || context.workspace !== workspace()) return null;
    return {context, document, entry, key: draftKey(context, document), workspace: workspace(),
      localGeneration: entry.generation, revision: entry.server.revision, sha256: entry.server.sha256};
  }
  function matches(captured, {revision = true} = {}) {
    return !disposed && captured && workspace() === captured.workspace && currentEntry() === captured.entry
      && state.data?.id === captured.context.id && state.data?.revision === captured.context.revision
      && state.document?.id === captured.document.id
      && (!revision || (captured.entry.generation === captured.localGeneration
        && captured.entry.server?.revision === captured.revision && captured.entry.server?.sha256 === captured.sha256));
  }
  const binding = (captured) => ({workspace: captured.workspace, context_id: captured.context.id,
    expected_revision: captured.context.revision});
  const guard = (captured) => ({...binding(captured), draft_id: captured.entry.server.id,
    expected_draft_revision: captured.revision, expected_draft_sha256: captured.sha256});

  function unlock() { const done = release; release = null; done?.(); }
  function cancel({message = ""} = {}) {
    generation++;
    view.review = null; view.busy = false; view.error = "";
    if (message) view.message = message;
    unlock(); publish();
  }
  function sync() {
    const currentStamp = noticeIdentity();
    if (currentStamp !== noticeStamp) {
      noticeStamp = currentStamp;
      view.message = "";
    }
    if (currentWorkspace !== workspace()) {
      currentWorkspace = workspace(); listGeneration++;
      view.recoveries = null; view.recoveryLoading = false; view.recoveryError = ""; view.message = "";
    }
    const review = view.review;
    if (review && (review.workspace !== workspace() || review.captured && !matches(review.captured, {revision: !view.busy && review.kind !== "outcome"}))) {
      generation++;
      view.review = null; view.busy = false; view.error = "";
      view.message = "The context or draft changed. Review the current source again before continuing.";
      unlock();
    }
  }
  async function begin(kind) {
    if (disposed || view.busy) return false;
    cancel();
    let captured = capture();
    if (!captured) { view.error = "Open an overlay draft first."; publish(); return false; }
    release = drafts.pause();
    if (!release) return false;
    const token = ++generation;
    view.busy = true; view.error = ""; view.message = ""; publish();
    try {
      if (kind === "save") {
        const before = captured.localGeneration;
        if (!await drafts.flush() || !matches(captured, {revision: false})) return false;
        captured = capture();
        if (!captured || captured.localGeneration !== before) throw new Error("New typing arrived while preparing the review. Your text is retained; review it again.");
      } else if (captured.entry.flight) {
        await captured.entry.flight;
        if (!matches(captured, {revision: false})) return false;
        captured = capture();
      }
      const data = await post(kind === "save" ? "review" : "compare", guard(captured));
      if (token !== generation || !matches(captured)) return false;
      if (kind === "save" && data.status === "ready") {
        const plan = data.plan;
        if (!plan?.id || plan.path !== captured.document.path || plan.candidate_text !== captured.entry.server.text
            || plan.candidate_sha256 !== captured.sha256 || typeof plan.expires_at !== "number") {
          throw new Error("The review does not match this draft. Open a new review.");
        }
        view.review = {kind, data: plan, captured, workspace: captured.workspace};
      } else {
        const comparison = kind === "save" ? data.comparison : data;
        if (!comparison || comparison.path !== captured.document.path || typeof comparison.text !== "string") {
          throw new Error(data.message || "The file cannot be compared. Your draft is retained and can be exported.");
        }
        view.review = {kind: kind === "reload" ? "reload" : "compare", data: comparison, captured,
          workspace: captured.workspace, blocked: kind === "save", message: data.message || comparison.message || ""};
      }
      return true;
    } catch (error) {
      if (token === generation && matches(captured, {revision: false})) view.error = error.message || String(error);
      return false;
    } finally {
      if (token === generation) {
        view.busy = false;
        if (!view.review) unlock();
        publish();
      }
    }
  }

  async function confirm() {
    const review = view.review;
    if (!review || view.busy || review.workspace !== workspace()) return false;
    if (review.kind === "outcome") return false;
    if (review.kind === "discard-recovery") {
      const token = ++generation;
      view.busy = true; view.error = ""; publish();
      try {
        const result = await post("recovery/discard", {workspace: review.workspace, recovery_id: review.data.record.id,
          expected_recovery_revision: review.data.record.revision});
        if (token !== generation || disposed || review.workspace !== workspace()) return false;
        if (result.status !== "discarded" || result.id !== review.data.record.id) throw new Error("Recovery removal could not be confirmed. Refresh the recovery list.");
        if (view.recoveries) view.recoveries.records = view.recoveries.records.filter((record) => record.id !== result.id);
        view.review = null;
        view.message = "Recovery record deleted. The overlay file and any open draft were not changed.";
        return true;
      } catch (error) {
        if (token === generation && !disposed && review.workspace === workspace()) view.error = error.message || String(error);
        return false;
      } finally {
        if (token === generation) { view.busy = false; if (!view.review) unlock(); publish(); }
      }
    }
    const captured = review.captured || capture();
    if (!captured || !matches(captured)) { cancel({message: "The context changed. Open a new review."}); return false; }
    if (review.kind === "save" && !review.uncertain && review.data.expires_at * 1000 <= Date.now()) {
      view.error = "This review expired. Close it and review the current draft again."; publish(); return false;
    }
    const token = ++generation;
    view.busy = true; view.error = ""; publish();
    try {
      let data;
      if (review.kind === "save") {
        data = await post("save", {...guard(captured), plan_id: review.data.id});
      } else if (review.kind === "recovery") {
        data = await post("recovery/restore", {...binding(captured), document_id: captured.document.id,
          recovery_id: review.data.record.id, expected_recovery_revision: review.data.record.revision,
          comparison_id: review.data.comparison.comparison_id, mode: "keep"});
      } else {
        data = await post("reload", {...guard(captured), comparison_id: review.data.comparison_id,
          mode: review.kind === "reload" ? "discard" : "keep"});
      }
      if (token !== generation || !matches(captured, {revision: false})) return false;
      if (data.status === "conflict" || data.status === "uncertain") {
        view.review = {...review, kind: data.receipt || data.status === "uncertain" ? "outcome" : "compare",
          data: data.comparison || {path: captured.document.path, text: captured.entry.text, can_reload: false},
          receipt: data.receipt, blocked: true,
          message: data.message || "The file changed. Compare the current source before continuing."};
        return false;
      }
      if (!["saved", "unchanged", "reloaded", "restored"].includes(data.status) || !data.context || !data.document || !data.draft) {
        throw new Error("The operation returned an incomplete result. Reopen the overlay to reconcile; your retained draft is still available.");
      }
      // A defensive edit received despite the read-only UI is retained by adopt.
      const preserveLocal = review.kind === "compare" && captured.entry.text !== captured.entry.server.text;
      drafts.adopt(data, captured, {preserveLocal});
      view.review = null;
      adopt(data.context, data.document);
      view.message = data.status === "saved" ? "Overlay saved. Undo creates an unsaved inverse change."
        : data.status === "unchanged" ? "The overlay already matches this draft. No replacement was needed."
          : data.status === "restored" ? "Recovery restored as an unsaved draft. Review and Save when ready."
            : review.kind === "reload" ? "Draft discarded and current file reloaded. No file was written."
              : "Draft retained against the current file. Review the complete diff before saving.";
      return true;
    } catch (error) {
      if (token === generation && matches(captured, {revision: false})) {
        view.error = error.message || String(error);
        if (review.kind === "save") {
          if (captured.entry.generation !== captured.localGeneration) {
            view.review = {...review, kind: "outcome", receipt: {status: "unknown"},
              data: {path: captured.document.path, text: captured.entry.server.text, can_reload: false},
              message: "The Save reply was lost while newer typing arrived. The file may have changed. Your newer text is retained; compare the current file before continuing."};
          } else review.uncertain = true;
        }
      }
      return false;
    } finally {
      if (token === generation) { view.busy = false; if (!view.review) unlock(); publish(); }
    }
  }

  async function listRecoveries() {
    if (disposed) return false;
    const path = workspace(); const token = ++listGeneration;
    view.recoveryLoading = true; view.recoveryError = ""; publish();
    try {
      const data = await post("recovery/list", {workspace: path});
      if (disposed || token !== listGeneration || path !== workspace()) return false;
      if (!Array.isArray(data.records)) throw new Error("The recovery catalogue could not be read.");
      view.recoveries = {workspace: path, records: data.records, diagnostics: array(data.diagnostics), publications: array(data.publications)};
      return true;
    } catch (error) {
      if (!disposed && token === listGeneration && path === workspace()) view.recoveryError = error.message || String(error);
      return false;
    } finally {
      if (!disposed && token === listGeneration && path === workspace()) { view.recoveryLoading = false; publish(); }
    }
  }

  async function reviewRecovery(id) {
    if (disposed || view.busy || !id) return false;
    cancel();
    const path = workspace(); const token = ++generation;
    const captured = capture();
    if (captured) release = drafts.pause();
    view.busy = true; view.error = ""; publish();
    try {
      const data = await post("recovery/read", {workspace: path, recovery_id: id});
      if (disposed || token !== generation || path !== workspace() || captured && !matches(captured)) return false;
      if (data.record?.id !== id || typeof data.record.text !== "string" || !data.comparison) throw new Error("The recovery record is unavailable.");
      view.review = {kind: "recovery", data, workspace: path, captured};
      return true;
    } catch (error) {
      if (!disposed && token === generation && path === workspace()) view.recoveryError = error.message || String(error);
      return false;
    } finally {
      if (token === generation) { view.busy = false; if (!view.review) unlock(); publish(); }
    }
  }

  return {reviewSave: () => begin("save"), compare: () => begin("compare"), reviewReload: () => begin("reload"),
    confirm, cancel, sync, listRecoveries, reviewRecovery,
    reviewRecoveryDiscard() {
      if (view.busy || view.review?.kind !== "recovery") return false;
      view.review = {...view.review, kind: "discard-recovery"}; view.error = ""; publish(); return true;
    },
    dispose() { disposed = true; generation++; listGeneration++; view.review = null; unlock(); },
  };
}
