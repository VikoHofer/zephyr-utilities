/** Explicit saved-source configuration; jobs survive leaving the editor. */
import {editorText, needsOverlayCreation} from "./drafts.js";

const PREFIX = "/api/devicetree/editor/drafts/validation/";
const active = (result) => ["queued", "running"].includes(result?.job?.status);

export function diagnosticSelection(source, diagnostic) {
  if (!Number.isInteger(diagnostic?.line) || diagnostic.line < 1) return null;
  const lines = editorText(source).split("\n");
  if (diagnostic.line > lines.length) return null;
  const start = lines.slice(0, diagnostic.line - 1).reduce((sum, line) => sum + line.length + 1, 0);
  return [start, start + lines[diagnostic.line - 1].length];
}

export function projectsValidationRoute(handoff) {
  const id = /^[a-f0-9]{32}$/;
  if (handoff?.feature !== "projects" || !id.test(handoff.project_id)
      || handoff.configuration_id != null && !id.test(handoff.configuration_id)
      || handoff.attempt_id != null && !id.test(handoff.attempt_id)) return null;
  return `projects/applications/${handoff.project_id}/${handoff.configuration_id || "original"}${handoff.attempt_id ? "/" + handoff.attempt_id : ""}`;
}

export function createValidationSession({api, state, workspace, drafts, onChange,
  setTimer = setTimeout, clearTimer = clearTimeout, pollDelay = 1000}) {
  state.validationRuns ||= {};
  const view = state.validationView = {busy: false, refreshing: false, action: "", review: null, error: "", message: "", run: null, stale: false};
  let disposed = false;
  let generation = 0;
  let timer;
  let release;
  let refreshing = null;
  const publish = () => { if (!disposed) onChange(); };
  const post = (path, body) => api(PREFIX + path, {method: "POST", body});
  const entry = () => state.draftView?.entry;
  const key = () => state.data && state.document ? JSON.stringify([workspace(), state.data.project?.id,
    state.data.configuration?.id, state.data.application?.path, state.document.path]) : null;
  function capture() {
    if (!state.draftView?.ready || !entry()?.server || state.data?.workspace !== workspace()) return null;
    return {key: key(), workspace: workspace(), context_id: state.data.id, context_revision: state.data.revision,
      draft_id: entry().server.id, draft_revision: entry().server.revision, sha256: entry().server.sha256,
      local_generation: entry().generation, path: state.document.path, text: entry().server.text};
  }
  function matches(captured) {
    const current = capture();
    return current && captured && Object.keys(current).every((name) => current[name] === captured[name]) && !drafts.pending();
  }
  const guard = (captured) => ({workspace: captured.workspace, context_id: captured.context_id,
    expected_revision: captured.context_revision, draft_id: captured.draft_id,
    expected_draft_revision: captured.draft_revision, expected_draft_sha256: captured.sha256});
  function acceptResult(run, result) {
    if (result.plan_id !== run.plan.id) throw new Error("The configuration result belongs to another review.");
    const previous = run.result?.job;
    if (previous && !result.job) return;
    if (previous && result.job.id !== previous.id) throw new Error("The configure job changed unexpectedly. Review its retained attempt in Projects.");
    if (previous && !["queued", "running"].includes(previous.status) && active(result)) return;
    if (previous?.status === "running" && result.job?.status === "queued") return;
    run.result = result;
  }
  const stopTimer = () => { if (timer !== undefined) clearTimer(timer); timer = undefined; };
  const unlock = () => { const done = release; release = null; done?.(); };
  function closeReview() {
    generation++; view.review = null; view.busy = false; view.action = ""; unlock(); publish();
  }
  function schedule() {
    if (disposed || timer !== undefined || refreshing || !view.run || !(active(view.run.result) || view.run.pending)) return;
    timer = setTimer(() => { timer = undefined; void refresh(); }, pollDelay);
  }
  function sync() {
    if (disposed) return;
    if (view.review && !matches(view.review.captured)) {
      generation++; view.review = null; view.busy = false; view.action = "";
      view.message = "The draft or context changed. Review configuration again."; unlock();
    }
    const run = state.validationRuns[key()] || null;
    if (view.run !== run) {
      stopTimer(); view.run = run; view.error = ""; view.message = "";
    }
    view.stale = Boolean(run && (!matches(run.captured) || run.result?.stale || run.result?.status === "stale"));
    schedule();
  }
  async function review() {
    if (disposed || view.busy || view.review || active(view.run?.result) || view.run?.pending) return false;
    const captured = capture();
    view.error = ""; view.message = "";
    if (!captured) { view.error = "Open an overlay draft first."; publish(); return false; }
    if (drafts.pending() || entry().server.dirty || needsOverlayCreation(entry().server)) {
      view.message = "Save this draft before configuring. Unsaved candidate staging is not supported for this layout.";
      publish(); return false;
    }
    if (entry().flight || entry().uncertain || entry().conflict) {
      view.error = "Finish or reconcile the draft request before reviewing configuration."; publish(); return false;
    }
    release = drafts.pause();
    if (!release) return false;
    const token = ++generation;
    view.busy = true; view.action = "review"; publish();
    try {
      const data = await post("review", guard(captured));
      if (disposed || token !== generation || !matches(captured)) return false;
      if (data.status !== "ready") { view.message = data.message || "Configuration is unavailable for this selection."; return false; }
      const plan = data.plan;
      if (!plan?.id || plan.mode !== "saved-source" || plan.candidate?.path !== captured.path
          || plan.candidate?.text !== captured.text || plan.candidate?.sha256 !== captured.sha256) {
        throw new Error("The configuration review does not match the current saved overlay.");
      }
      view.review = {plan, captured}; return true;
    } catch (error) {
      if (!disposed && token === generation && matches(captured)) view.error = error.message || String(error);
      return false;
    } finally {
      if (token === generation) { view.busy = false; view.action = ""; if (!view.review) unlock(); publish(); }
    }
  }
  async function submit() {
    const reviewed = view.review;
    if (disposed || view.busy || !reviewed || !matches(reviewed.captured)) return false;
    const token = ++generation;
    const run = {plan: reviewed.plan, captured: reviewed.captured, result: null, pending: true};
    const retained = Object.keys(state.validationRuns);
    if (retained.length >= 16 && !state.validationRuns[run.captured.key]) delete state.validationRuns[retained[0]];
    state.validationRuns[run.captured.key] = run;
    view.run = run; view.busy = true; view.action = "submit"; view.error = ""; publish();
    try {
      const result = await post("submit", {...guard(run.captured), plan_id: run.plan.id});
      if (result.plan_id !== run.plan.id || !result.job?.id) throw new Error("The configure job could not be confirmed. Refresh its status before starting another review.");
      acceptResult(run, result);
      return true;
    } catch (error) {
      run.uncertain = true;
      if (!disposed && token === generation) view.error = error.message || String(error);
      return false;
    } finally {
      run.pending = false;
      if (token === generation) {
        view.busy = false; view.action = ""; view.review = null; unlock();
        if (!disposed) { sync(); publish(); }
      }
    }
  }
  async function refresh({cancel = false, explicit = false} = {}) {
    const run = view.run;
    if (disposed || !run) return false;
    if (refreshing && !cancel) {
      if (explicit) { view.message = "A status request is already in progress."; publish(); }
      return false;
    }
    const request = {}; const token = cancel ? ++generation : generation; refreshing = request; stopTimer();
    if (cancel) { view.busy = true; view.action = "cancel"; view.error = ""; }
    if (explicit) view.refreshing = true;
    if (cancel || explicit) publish();
    try {
      const result = await post(cancel ? "cancel" : "status", {workspace: run.captured.workspace, plan_id: run.plan.id});
      acceptResult(run, result); run.uncertain = false;
      if (!disposed && token === generation && view.run === run && workspace() === run.captured.workspace) { view.error = ""; view.message = ""; }
      return true;
    } catch (error) {
      if (!disposed && token === generation && view.run === run && workspace() === run.captured.workspace) view.error = error.message || String(error);
      return false;
    } finally {
      if (refreshing === request) refreshing = null;
      if (!disposed) {
        if (cancel && token === generation && view.run === run) { view.busy = false; view.action = ""; }
        if (explicit) view.refreshing = false;
        sync(); publish();
      }
    }
  }
  async function handoff() {
    const run = view.run;
    if (disposed || !run || view.busy) return null;
    const token = ++generation; view.busy = true; view.action = "handoff"; view.error = ""; publish();
    try {
      const data = await post("handoff", {workspace: run.captured.workspace, plan_id: run.plan.id});
      if (disposed || token !== generation || view.run !== run || workspace() !== run.captured.workspace) return null;
      const route = projectsValidationRoute(data.handoff || data);
      if (!route) throw new Error("The Projects destination could not be verified.");
      return route;
    } catch (error) {
      if (!disposed && token === generation) view.error = error.message || String(error);
      return null;
    } finally {
      if (!disposed && token === generation) { view.busy = false; view.action = ""; publish(); }
    }
  }
  return {review, submit, refresh, cancel: () => refresh({cancel: true}), handoff, closeReview, sync,
    resume() { sync(); if (view.run) void refresh(); },
    dispose() { disposed = true; generation++; stopTimer(); view.review = null; unlock(); },
  };
}
