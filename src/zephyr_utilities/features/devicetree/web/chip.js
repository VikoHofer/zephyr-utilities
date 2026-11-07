/** Automatic read-only chip discovery follows the selected context and canonical draft. */
import {editableOverlay} from "./drafts.js";

const PREFIX = "/api/devicetree/editor/chip/";
const finalStates = new Set(["completed", "failed", "cancelled"]);

export function createChipSession({api, state, workspace, drafts, onChange,
  setTimer = setTimeout, clearTimer = clearTimeout, pollDelay = 250}) {
  const view = state.chipView = {busy: false, data: null, stale: false, error: "", message: "", jobId: null};
  let disposed = false;
  let generation = 0;
  let controller;
  let timer;
  let attempted;
  let displayedScope;
  let scheduled = false;
  const publish = () => { if (!disposed) onChange(); };
  const scope = () => state.data && JSON.stringify([workspace(), state.data.application?.path,
    state.data.project?.id, state.data.configuration?.id, state.data.target?.requested]);

  function capture() {
    const context = state.data;
    if (!context || context.workspace !== workspace() || state.loading || state.documentLoading) return null;
    const binding = {workspace: workspace(), context_id: context.id, expected_revision: context.revision};
    if (!editableOverlay(context.editing_document)) return binding;
    const current = state.draftView?.entry;
    if (!state.draftView?.ready || !current?.server || current.server.document_id !== context.editing_document.id
        || drafts.pending() || current.flight || current.historyOperation || current.externalOperation
        || current.uncertain || current.conflict) return null;
    return {...binding, draft_id: current.server.id, expected_draft_revision: current.server.revision,
      expected_draft_sha256: current.server.sha256};
  }

  const key = (value) => value ? JSON.stringify(value) : null;
  const current = (token, captured) => !disposed && generation === token && key(capture()) === key(captured);
  function stopPolling() {
    controller?.abort(); controller = null;
    if (timer !== undefined) clearTimer(timer);
    timer = undefined;
  }
  function receive(envelope, captured, token) {
    if (!current(token, captured)) return false;
    if (!envelope || typeof envelope.job_id !== "string" || !["queued", "running", ...finalStates].includes(envelope.status)) {
      throw new Error("Chip discovery returned an invalid job. Retry discovery.");
    }
    view.jobId = envelope.job_id;
    if (envelope.status === "completed") {
      const data = envelope.result;
      if (data?.schema_version !== 1 || !data.chip || !Array.isArray(data.pins) || typeof data.supported !== "boolean") {
        throw new Error("Chip discovery did not return a supported pin observation. Retry discovery.");
      }
      if (data.context_id !== captured.context_id || data.expected_revision !== captured.expected_revision
          || data.draft_id !== (captured.draft_id ?? null)
          || data.draft_revision !== (captured.expected_draft_revision ?? null)
          || data.draft_sha256 !== (captured.expected_draft_sha256 ?? null)) {
        throw new Error("Chip discovery belongs to another context or draft. Retry discovery.");
      }
      view.data = data; view.stale = false; view.busy = false;
      const slots = data.chip.inventory === "gpio-slots";
      view.message = data.supported ? `${data.pins.length} ${slots ? "GPIO slots observed from DTS" : "pins observed"}. Select a ${slots ? "slot" : "pin"} to inspect its assignments and functions.`
        : data.reason || "Pin information is unavailable for this target.";
      if (data.assignment_reason) view.message += ` ${data.assignment_reason}`;
      return false;
    }
    if (envelope.status === "failed") throw new Error(typeof envelope.error === "string" ? envelope.error
      : envelope.error?.message || "Chip discovery failed. Retry after checking the selected project.");
    if (envelope.status === "cancelled") {
      view.busy = false; view.message = "Chip discovery cancelled. Source files are unchanged."; return false;
    }
    view.message = envelope.status === "queued" ? "Chip discovery queued…" : "Reading chip pins and current assignments…";
    return true;
  }

  async function poll(captured, token) {
    if (!current(token, captured)) return;
    try {
      const envelope = await api(PREFIX + "result", {method: "POST", signal: controller.signal,
        body: {...captured, job_id: view.jobId}});
      if (receive(envelope, captured, token)) timer = setTimer(() => { timer = undefined; void poll(captured, token); }, pollDelay);
    } catch (error) {
      if (current(token, captured) && error.name !== "AbortError") {
        view.busy = false; view.error = error.message || String(error); view.message = "";
      }
    } finally { if (current(token, captured)) publish(); }
  }

  async function load({retry = false} = {}) {
    const captured = capture();
    if (disposed || !captured || !retry && attempted === key(captured)) return false;
    stopPolling(); const token = ++generation;
    attempted = key(captured); controller = new AbortController();
    view.busy = true; view.stale = Boolean(view.data); view.error = ""; view.jobId = null;
    view.message = "Reading chip pins and current assignments…"; publish();
    try {
      const envelope = await api(PREFIX + "start", {method: "POST", body: captured, signal: controller.signal});
      if (!current(token, captured)) return false;
      if (receive(envelope, captured, token)) timer = setTimer(() => { timer = undefined; void poll(captured, token); }, pollDelay);
      return true;
    } catch (error) {
      if (current(token, captured) && error.name !== "AbortError") {
        view.busy = false; view.error = error.message || String(error); view.message = "";
      }
      return false;
    } finally { if (current(token, captured)) publish(); }
  }

  function sync() {
    if (disposed) return;
    const nextScope = scope();
    if (nextScope !== displayedScope) {
      generation++; stopPolling(); displayedScope = nextScope; attempted = null;
      view.busy = false; view.jobId = null;
      view.data = null; view.error = ""; view.message = ""; view.stale = false;
    }
    const captured = capture();
    if (attempted && attempted !== key(captured)) {
      generation++; stopPolling(); view.busy = false; view.jobId = null; view.stale = Boolean(view.data);
      attempted = null;
      view.message = state.draftView?.entry?.server?.syntax?.blocked
        ? "The overlay source is incomplete. Correct it to refresh pin assignments."
        : "Waiting for the current overlay draft…";
    }
    if (!captured || key(captured) === attempted || scheduled) return;
    scheduled = true;
    queueMicrotask(() => { scheduled = false; if (!disposed) void load(); });
  }

  async function cancel() {
    const captured = capture(); const jobId = view.jobId;
    if (disposed || !captured || !view.busy || !jobId) return false;
    const token = generation;
    try {
      await api(PREFIX + "cancel", {method: "POST", body: {...captured, job_id: jobId}});
      if (!current(token, captured)) return false;
      generation++; stopPolling(); view.busy = false; view.message = "Chip discovery cancelled. Source files are unchanged.";
      publish(); return true;
    } catch (error) {
      if (current(token, captured)) { view.error = error.message || String(error); publish(); }
      return false;
    }
  }

  return {sync, retry: () => load({retry: true}), cancel,
    navigate() { attempted = null; sync(); },
    dispose() { disposed = true; generation++; stopPolling(); },
  };
}
