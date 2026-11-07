import {createEditorSession} from "./state.js";
import {createDraftSession, editableOverlay, editorText, needsOverlayCreation, sourceSelection} from "./drafts.js";
import {createSaveSession} from "./saving.js";
import {createValidationSession, diagnosticSelection} from "./validation.js";
import {mountChipPanel} from "./chip-view.js";
import {retainActionFocus} from "./view-focus.js";
import {announceResult} from "./view-feedback.js";

const array = (value) => Array.isArray(value) ? value : [];
const text = (value, fallback = "Unknown") => value == null || value === "" ? fallback : String(value);

export function mount(host, context) {
  const root = host.querySelector("[data-dte-root]");
  const $ = (name) => root.querySelector(`[data-dte-${name}]`);
  const document = root.ownerDocument;
  const lifetime = new AbortController();
  const state = context.state;
  let disposed = false;
  let actionGeneration = 0;
  let renderedWorkspace;
  let renderedCatalog;
  let renderedData;
  let presentedDraft = false;
  let session;
  let drafts;
  let saving;
  let validation;
  let chip;
  let shownReview;
  let reviewTrigger;
  let shownConfigure;
  let configureTrigger;
  let renderedValidation;
  const downloadUrls = new Set();

  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = String(value);
    return element;
  }
  function option(select, value, label) {
    const element = node("option", "", label); element.value = value; select.append(element);
  }
  for (const name of ["open-project", "open-directory", "recheck", "undo", "redo", "reconcile", "use-local", "use-server", "source", "save", "registration-retry", "compare", "reload", "refresh-recovery", "review-recovery", "validate", "validation-refresh", "validation-cancel"]) {
    $(name).dataset.focusKey = name;
  }
  const feedback = (target, options) => announceResult(target, context.notify, options);
  function clearOpenFeedback() {
    for (const name of ["project", "directory", "overlay"]) {
      $(name + "-feedback").hidden = true;
    }
  }
  function fact(parent, label, value, path = false) {
    const item = node("div");
    item.append(node("dt", "", label), node("dd", path ? "dte-path" : "", text(value)));
    parent.append(item);
  }
  function notice(parent, message) { parent.append(node("p", "dte-notice", message)); }
  function list(parent, values, ordered = false) {
    const element = node(ordered ? "ol" : "ul", "dte-list");
    for (const value of values) element.append(node("li", "", value));
    parent.append(element);
  }
  function configurations() {
    const project = array(state.catalog?.projects).find((item) => item.id === $("project").value);
    const select = $("configuration"); select.replaceChildren();
    for (const entry of array(project?.configurations)) option(select, entry.id || "", entry.name || "Original");
    const desired = state.formConfigurationId || "";
    if (array(project?.configurations).some((item) => (item.id || "") === desired)) select.value = desired;
    select.disabled = !project || state.catalogLoading;
    $("open-project").disabled = !project || !select.options.length || state.loading;
  }
  function renderCatalog() {
    if (renderedCatalog !== state.catalog) {
      renderedCatalog = state.catalog;
      const select = $("project"); select.replaceChildren();
      option(select, "", "Choose an application");
      for (const project of array(state.catalog?.projects)) {
        option(select, project.id, project.name || project.path);
      }
      const desired = state.formProjectId || "";
      if (array(state.catalog?.projects).some((item) => item.id === desired)) select.value = desired;
      configurations();
      const diagnostics = $("catalog-diagnostics"); diagnostics.replaceChildren();
      for (const entry of array(state.catalog?.diagnostics)) notice(diagnostics, entry.message || entry);
    }
    $("project").disabled = state.catalogLoading || !array(state.catalog?.projects).length;
    $("configuration").disabled = state.catalogLoading || !$("project").value;
    $("open-project").disabled = state.loading || state.catalogLoading || !$("project").value || !$("configuration").options.length;
    $("open-project").textContent = state.loading && state.selection?.kind === "project" ? "Opening…" : "Open application";
    $("refresh-catalog").disabled = state.catalogLoading;
    $("refresh-catalog").textContent = state.catalogLoading ? "Refreshing…" : "Refresh list";
    const count = array(state.catalog?.projects).length;
    $("catalog-status").textContent = state.catalogLoading ? "Loading registered applications…"
      : !state.catalogError ? count ? `${count} available registered application${count === 1 ? "" : "s"} in this workspace.`
        : "No available registered applications in this workspace. You can open an application directory below." : "";
    const unavailableCount = array(state.catalog?.unavailableProjects).length;
    $("unavailable-projects").hidden = !unavailableCount;
    $("unavailable-projects-summary").textContent = `${unavailableCount} unavailable saved application${unavailableCount === 1 ? " is" : "s are"} hidden from this picker. Reconnect or remove saved entries in Projects.`;
    $("catalog-error").hidden = !state.catalogError;
    $("catalog-error").textContent = state.catalogError || "";
  }
  function renderEvidence(data) {
    const container = $("evidence"); container.replaceChildren();
    for (const [kind, title] of [["predicted", "Predicted inputs"], ["captured", "Captured inputs"]]) {
      const records = array(data.input_evidence?.[kind]);
      if (!records.length) {
        container.append(node("p", "dte-muted", `${title}: not recorded.`));
        continue;
      }
      for (const record of records) {
        const group = node("section", "dte-evidence");
        group.append(node("h4", "", title));
        if (record.message) group.append(node("p", "dte-muted", record.message));
        if (record.target) group.append(node("p", "dte-path", `Target: ${record.target}`));
        const provenance = record.provenance || {};
        const provenanceText = [provenance.kind, provenance.attempt_id ? `Attempt ${provenance.attempt_id}` : "",
          provenance.captured_at, provenance.configuration_revision != null ? `Configuration revision ${provenance.configuration_revision}` : ""].filter(Boolean).join(" · ");
        if (provenanceText) group.append(node("p", "dte-muted", provenanceText));
        const files = array(record.files).map((file) => typeof file === "string" ? file
          : [file.path, file.state, file.role, file.reason].filter(Boolean).join(" · "));
        if (files.length) list(group, files);
        else group.append(node("p", "dte-muted", "No overlay paths recorded in this evidence."));
        container.append(group);
      }
    }
    container.append(node("p", "dte-muted", `Input coverage: ${text(data.input_evidence?.coverage, "unknown")}.`));
  }
  function renderEnvironment(data) {
    const container = $("environment"); container.replaceChildren();
    const facts = node("dl", "dte-facts dte-small-facts");
    const environment = data.environment || {};
    fact(facts, "Environment observation", environment.observation_id || environment.id);
    fact(facts, "Environment freshness", environment.freshness || environment.state);
    fact(facts, "Zephyr checkout", environment.zephyr_base || environment.zephyr?.path, true);
    fact(facts, "Selected SDK", [environment.sdk?.path, environment.sdk?.version].filter(Boolean).join(" · "), true);
    fact(facts, "Toolchain", environment.toolchain?.name || environment.sdk?.toolchain_variant || environment.toolchain_variant);
    container.append(facts);
    if (!data.environment) container.append(node("p", "dte-muted", "No selected environment is known for this directory."));
    const roots = array(data.roots);
    if (roots.length) list(container, roots.map((entry) => [entry.kind, entry.path, entry.origin,
      typeof entry.availability === "string" ? entry.availability : entry.availability?.state].filter(Boolean).join(" · ")));
    else container.append(node("p", "dte-muted", "No root selection recorded."));
  }
  function renderContext(data) {
    if (renderedData === data) return;
    renderedData = data;
    if (!data) return;
    $("context-title").textContent = [data.application?.name || data.application?.path, data.configuration?.name,
      data.target?.requested].filter(Boolean).join(" · ");
    const facts = $("facts"); facts.replaceChildren();
    fact(facts, "Application", data.application?.name || data.application?.path, true);
    fact(facts, "Exact target", data.target?.requested, true);
    fact(facts, "Configuration", data.configuration?.name, false);
    fact(facts, "Application directory", data.application?.path, true);
    fact(facts, "Availability", data.application?.availability?.state);
    fact(facts, "Image / domain", data.image?.domain || data.build_evidence?.domain);
    fact(facts, "Selected Zephyr", [data.environment?.zephyr?.path || data.environment?.zephyr_base,
      data.environment?.zephyr?.version].filter(Boolean).join(" · "), true);
    fact(facts, "Selected SDK", [data.environment?.sdk?.path, data.environment?.sdk?.version].filter(Boolean).join(" · "), true);
    fact(facts, "Environment evidence", data.environment?.freshness || data.environment?.state);
    const diagnostics = $("diagnostics"); diagnostics.replaceChildren();
    if (data.application?.availability?.reason) notice(diagnostics, data.application.availability.reason);
    for (const entry of array(data.diagnostics)) notice(diagnostics, entry.message || entry);

    const availableDocuments = array(data.documents).filter(editableOverlay);
    const select = $("overlay"); select.replaceChildren(); option(select, "", "Choose an overlay");
    for (const entry of array(data.documents)) option(select, entry.id,
      `${entry.relative_path || entry.path}${needsOverlayCreation(entry) && editableOverlay(entry) ? " · New overlay" : entry.availability !== "available" ? ` · ${entry.availability}` : ""}`);
    select.value = data.editing_document?.id || "";
    select.disabled = !availableDocuments.length || availableDocuments.length === 1 && Boolean(data.editing_document);
    const selected = data.editing_document;
    $("context-panel").open = !selected && availableDocuments.length > 1;
    $("overlay-status").textContent = data.application?.availability?.state !== "available"
      ? data.application?.availability?.reason || "Application sources are unavailable. Restore the selected source directory and reopen it."
      : !availableDocuments.length ? data.overlay_creation?.reason || "No writable overlay destination is available for this selection. Chip inspection uses board sources; see the context details for missing target or source evidence."
      : needsOverlayCreation(selected) ? "A new overlay draft is ready for this target. Review and Save creates the displayed file; opening and editing do not write application source."
      : selected ? "Save changes only this overlay. Selecting or saving it does not activate it in the build recipe."
        : "Choose the application overlay to edit; it opens automatically. A file being present does not prove that the selected build uses it.";
    const documentFacts = $("document-facts"); documentFacts.replaceChildren();
    if (selected) {
      fact(documentFacts, "Selected overlay", selected.relative_path || selected.path, true);
      fact(documentFacts, "Selection evidence", selected.usage);
      fact(documentFacts, "Availability", needsOverlayCreation(selected) ? "New file, not saved" : selected.availability);
      if (selected.reason) notice(documentFacts, selected.reason);
      if (array(selected.origins).length) fact(documentFacts, "Origin", selected.origins.join(" · "));
    }
    const fragments = $("fragments"); fragments.replaceChildren();
    const overlays = data.fragments?.overlays;
    if (!overlays) fragments.append(node("p", "dte-muted", "Unknown for this source-only context."));
    else {
      const modes = {auto: "Automatic discovery", append: "Append selected overlays", replace: "Replace automatic overlays"};
      fragments.append(node("p", "", modes[overlays.mode] || text(overlays.mode)));
      if (array(overlays.files).length) list(fragments, overlays.files, true);
      else fragments.append(node("p", "dte-muted", "No explicit overlay files requested."));
    }
    renderEvidence(data); renderEnvironment(data);
  }

  function draftEntry() { return state.draftView?.entry; }
  function renderDraft() {
    const view = state.draftView;
    const entry = view?.entry;
    const server = entry?.server;
    const pending = drafts?.pending();
    const creating = needsOverlayCreation(server);
    const busy = Boolean(view?.loading || entry?.flight || entry?.historyOperation || entry?.externalOperation);
    const error = state.saveView?.review ? view?.error || entry?.error || "" : state.saveView?.error || view?.error || entry?.error || "";
    $("draft-status").textContent = view?.loading ? "Opening and revalidating the draft…"
      : entry?.uncertain ? "The last request needs reconciliation. Your local text is retained."
        : entry?.externalOperation ? "Reviewing the current draft. Source editing resumes when the review closes."
          : busy ? "Updating the draft…" : pending ? "Local typing is waiting to sync. Visual controls are paused."
            : server ? `Draft revision ${server.revision}.${creating ? " New overlay draft. No application file has been created; Review and Save creates it." : server.dirty ? " Changes have not been saved to the overlay." : " Draft matches its observed file."}${server.history_truncated ? " Earlier undo history reached its limit." : ""}`
              : "No writable overlay is selected. Chip inspection remains available.";
    $("dirty").textContent = creating ? "New overlay · Unsaved" : pending || server?.dirty ? "Unsaved changes" : "Unchanged";
    $("new-overlay-path").hidden = !creating;
    $("new-overlay-path").textContent = creating ? `Save destination: ${server.path}` : "";
    $("draft-error").hidden = !error; $("draft-error").textContent = error;
    $("conflict").hidden = !entry?.conflict;
    $("server-source").textContent = entry?.conflict ? server?.text || "" : "";
    $("use-local").disabled = busy; $("use-server").disabled = busy;
    $("reconcile").hidden = !entry?.uncertain || entry?.conflict;
    $("reconcile").disabled = busy;
    $("undo").disabled = false; $("redo").disabled = false;
    $("undo").setAttribute("aria-disabled", String(!view?.ready || busy || Boolean(entry?.conflict) || (!pending && !server?.can_undo)));
    $("redo").setAttribute("aria-disabled", String(!view?.ready || busy || Boolean(entry?.conflict) || (!pending && !server?.can_redo)));
    $("save").disabled = !view?.ready || busy || entry?.conflict;
    $("save").textContent = state.saveView?.busy && !state.saveView?.review && !state.saveView?.retryingRegistration ? "Preparing review…" : "Review and Save";
    $("compare").disabled = !server || busy;
    $("reload").disabled = !server || busy;
    $("reload").textContent = creating ? "Reset new draft" : "Reload / discard draft";
    $("export").disabled = !entry;
    $("save-status").hidden = !state.saveView?.message;
    $("save-status").textContent = state.saveView?.message || "";
    $("recovery-status").textContent = server?.recovery?.message || (server
      ? "Acknowledged changes are retained for recovery. Unsent typing is kept only in this open page." : "");
    $("source").disabled = !view?.ready;
    $("source").readOnly = Boolean(entry?.externalOperation || entry?.historyOperation || (entry?.flight && entry.flightKind && entry.flightKind !== "source"));
    const value = entry ? editorText(entry.text) : state.document ? editorText(state.document.text) : "";
    if ($("source").value !== value) {
      const start = $("source").selectionStart; const end = $("source").selectionEnd;
      $("source").value = value;
      if (document.activeElement === $("source")) $("source").setSelectionRange(Math.min(start, value.length), Math.min(end, value.length));
    }
    $("source-path").textContent = entry?.path || state.document?.path || state.data?.editing_document?.path || "";
    $("source-error").hidden = !state.documentError; $("source-error").textContent = state.documentError || "";
    $("source-status").textContent = server ? "Typing updates this draft after a short pause. Acknowledged source changes share Undo/Redo."
      : state.documentLoading ? "Reading current overlay source…" : "Choose an existing application overlay to edit its source.";
    const diagnostics = $("syntax-diagnostics"); diagnostics.replaceChildren();
    if (!pending) for (const item of array(server?.syntax?.diagnostics)) notice(diagnostics, item.message || item.code);
    $("diff").textContent = server?.diff?.text || "No acknowledged source changes.";
    $("diff-status").textContent = pending ? "The diff reflects the last acknowledged draft; your latest typing is still pending."
      : server?.diff?.truncated ? "The diff is shortened to keep this view responsive. The complete draft remains in the source editor."
        : creating ? "Proposed source for a new overlay. No file has been created."
          : "Compared with the original overlay bytes. This does not write the file.";
    renderRetained();
  }
  function renderRetained() {
    const entries = Object.entries(state.draftCache || {});
    $("retained-panel").hidden = !entries.length;
    $("retained-summary").textContent = `Retained drafts (${entries.length})`;
    const selected = $("retained").value;
    const select = $("retained"); select.replaceChildren();
    for (const [key, entry] of entries) option(select, key, `${entry.label} · ${entry.path}${needsOverlayCreation(entry.server) ? " · New overlay" : entry.text !== entry.server?.text || entry.server?.dirty ? " · Changed" : ""}`);
    if (entries.some(([key]) => key === selected)) select.value = selected;
    if (!$("retained-source").hidden) $("retained-source").value = editorText(state.draftCache[select.value]?.text || "");
  }
  function exportSource(source, path) {
    if (typeof source !== "string") return;
    const url = URL.createObjectURL(new Blob([source], {type: "text/plain;charset=utf-8"}));
    downloadUrls.add(url);
    const link = document.createElement("a");
    link.href = url; link.download = `${String(path || "draft.overlay").split("/").at(-1)}.draft.txt`;
    link.hidden = true; root.append(link); link.click(); link.remove();
    state.saveView.message = "Draft export requested. The overlay on disk was not changed.";
    context.notify?.(state.saveView.message);
    renderSaving();
  }
  function closeReview({restoreFocus = true} = {}) {
    const dialog = $("review");
    const open = dialog.open;
    if (!open && !shownReview) return;
    if (open) dialog.close();
    shownReview = null;
    const trigger = reviewTrigger; reviewTrigger = null;
    if (open && restoreFocus && !disposed && trigger?.isConnected && !trigger.disabled && !trigger.closest("[hidden]")) trigger.focus({preventScroll: true});
  }
  function renderSaving() {
    const view = state.saveView;
    if (!view) return;
    $("save-status").hidden = !view.message; $("save-status").textContent = view.message;
    $("registration-retry-panel").hidden = !view.registrationRetry;
    const canRetry = saving.canRetryRegistration();
    $("registration-retry").setAttribute("aria-disabled", String(!canRetry));
    $("registration-retry").textContent = view.retryingRegistration ? "Retrying inclusion…" : "Retry configuration inclusion";
    $("registration-retry-status").textContent = view.registrationRetry ? view.registrationRetry.message
      || "The overlay file is saved, but its configuration inclusion is pending." : "";
    if (view.registrationRetry && !canRetry && !view.busy && !view.review) {
      $("registration-retry-status").textContent += " The draft or context changed; review Save again to retry with current evidence.";
    }
    const recoveries = view.recoveries?.workspace === context.workspace.path ? view.recoveries : null;
    $("refresh-recovery").disabled = view.recoveryLoading;
    $("refresh-recovery").textContent = view.recoveryLoading ? "Finding drafts…" : "Find recoverable drafts";
    $("recoveries-error").hidden = !view.recoveryError; $("recoveries-error").textContent = view.recoveryError;
    $("recoveries-status").textContent = view.recoveryLoading ? "Reading recovery records…" : recoveries
      ? `${recoveries.records.length} recoverable draft${recoveries.records.length === 1 ? "" : "s"}; ${array(recoveries.publications).length} publication records.${recoveries.diagnostics.length ? " " + recoveries.diagnostics.map((item) => item.message || String(item)).join(" ") : ""}`
      : "Select Find recoverable drafts to check saved recovery data.";
    $("recoveries-controls").hidden = !recoveries?.records.length;
    const select = $("recoveries"); const selected = select.value;
    select.replaceChildren();
    for (const record of recoveries?.records || []) option(select, record.id, `${record.path} · revision ${record.revision}${needsOverlayCreation(record) ? " · New overlay" : ""}${record.status ? " · " + record.status : ""}`);
    if (recoveries?.records.some((record) => record.id === selected)) select.value = selected;
    $("review-recovery").disabled = view.busy || !select.value;
    $("publications-panel").hidden = !array(recoveries?.publications).length;
    const publications = $("publications"); publications.replaceChildren();
    for (const receipt of array(recoveries?.publications)) {
      const group = node("div", "dte-evidence");
      group.append(node("p", "dte-path", receipt.path || "Unknown destination"), node("p", "dte-muted", `${receipt.status}: ${receipt.message || "Compare with the current file before continuing."}`));
      if (receipt.recovery?.journal) group.append(node("p", "dte-path", `Recovery record: ${receipt.recovery.journal}`));
      if (receipt.recovery?.staged_path) group.append(node("p", "dte-path", `Retained source: ${receipt.recovery.staged_path}`));
      publications.append(group);
    }
    const review = view.review;
    if (!review) { closeReview(); return; }
    const data = review.data;
    const recovery = ["recovery", "discard-recovery"].includes(review.kind);
    const deleting = review.kind === "discard-recovery";
    const creating = review.kind === "save" && data.operation === "create";
    const absent = recovery ? needsOverlayCreation(data.record) : needsOverlayCreation(review.captured?.entry?.server);
    const comparison = recovery ? data.comparison : data;
    const resetAbsent = absent && comparison.current_exists === false;
    const candidate = recovery ? data.record.text : review.kind === "save" ? data.candidate_text : data.text;
    $("review-title").textContent = review.kind === "save" ? creating ? "Create and Save overlay" : "Review and Save overlay" : deleting ? "Delete recoverable draft" : recovery ? "Review recoverable draft"
      : review.kind === "outcome" ? "Save outcome needs reconciliation"
      : review.kind === "reload" ? resetAbsent ? "Reset new overlay draft" : "Discard draft and reload file" : review.blocked ? "Save needs attention" : "Compare draft with disk";
    $("review-summary").textContent = review.kind === "save" ? creating
      ? "Create this new overlay with the exact source below. Save will stop if a file has appeared at this destination."
      : "Save the exact candidate below to this existing overlay."
      : deleting ? "Permanently delete this recovery record. Export its source first if you need a copy. This does not change the overlay file or an open session draft."
        : recovery ? `Restore this retained source as an unsaved draft for the matching ${absent ? "new overlay destination" : "open overlay"}. Discard any existing edited session draft first. Restoration does not write the file.`
        : review.kind === "reload" ? resetAbsent
          ? "Clear this new draft and its edit history, including unsent typing. The empty draft remains available; no application file is created."
          : "Discard this draft and its edit history, including unsent typing, and load the reviewed file. The file on disk is not changed."
          : review.message || comparison.message || "Compare the draft against the current file. Continuing retains this draft as unsaved source based on the reviewed file.";
    $("review-path").textContent = data.path || data.record?.path || comparison.path || "";
    const notices = $("review-notices"); notices.replaceChildren();
    if (review.kind === "save") {
      notice(notices, data.validation?.message || "Build and hardware validation have not run.");
      notice(notices, `Overlay usage: ${data.usage?.state || "unknown"}. ${data.usage?.message || "Saving does not change overlay selection."}`);
      notice(notices, data.effects?.message || "Other configurations may share this overlay.");
      if (data.registration?.message && data.registration.message !== data.usage?.message) notice(notices, data.registration.message);
      notice(notices, `This review expires ${new Date(data.expires_at * 1000).toLocaleTimeString()}.`);
    } else if (review.kind === "outcome") {
      notice(notices, "This plan will not be repeated. Close this review to compare the current file or inspect recoverable drafts. Keep all recovery files until the result is reconciled.");
      if (review.receipt?.status) notice(notices, `Publication outcome: ${review.receipt.status}.`);
      if (review.receipt?.recovery?.journal) notice(notices, `Recovery record: ${review.receipt.recovery.journal}`);
      if (review.receipt?.recovery?.staged_path) notice(notices, `Retained source: ${review.receipt.recovery.staged_path}`);
    } else if (recovery && (!state.data || data.record.path !== state.document?.path)) {
      notice(notices, "Open the original application/configuration and this overlay destination, then review recovery again to restore it. Export remains available.");
    } else if (comparison.message) notice(notices, comparison.message);
    if (!recovery && review.captured.entry.text !== review.captured.entry.server.text) {
      notice(notices, "This comparison describes acknowledged source. Newer local typing is retained and can be exported before discarding it.");
    }
    const diff = review.kind === "save" ? data.diff : comparison.diff;
    $("review-diff").textContent = diff?.text || "No source differences in this comparison.";
    $("review-diff-status").textContent = diff?.truncated ? "The diff is shortened. Review complete candidate and current source below."
      : creating ? "Complete source proposed for a new file." : absent && comparison.current_exists === false
        ? "The destination is still absent; no application file has been created." : "Compared with the observed file.";
    $("review-source").textContent = candidate || "";
    $("review-disk-panel").hidden = review.kind === "save" || comparison.current_text == null;
    $("review-disk").textContent = comparison.current_text || "";
    $("review-error").hidden = !view.error; $("review-error").textContent = view.error;
    $("review-confirm").textContent = view.busy ? "Working…" : review.kind === "save" ? review.uncertain ? "Check save result" : creating ? "Create overlay" : "Save overlay"
      : deleting ? "Delete recovery record" : recovery ? "Restore as unsaved draft" : review.kind === "reload" ? resetAbsent ? "Reset draft" : "Discard and reload" : "Continue draft against current file";
    $("review-confirm").hidden = review.kind === "outcome";
    $("review-confirm").disabled = view.busy || (review.kind === "save" || deleting ? false
      : !comparison.can_reload || !comparison.comparison_id || (recovery && (!review.captured || data.record.path !== state.document?.path))
        || (review.blocked && comparison.status !== "conflict"));
    $("review-close").disabled = $("review-cancel").disabled = view.busy;
    $("review-delete").hidden = review.kind !== "recovery";
    $("review-delete").disabled = view.busy;
    $("review-export").disabled = typeof candidate !== "string";
    if (shownReview !== review) {
      shownReview = review;
      for (const details of $("review").querySelectorAll("details")) details.open = false;
      if (!$("review").open) $("review").showModal();
    }
  }
  function closeConfigure({restoreFocus = true} = {}) {
    const dialog = $("configure-review"); const open = dialog.open;
    if (!open && !shownConfigure) return;
    if (open) dialog.close();
    shownConfigure = null;
    const trigger = configureTrigger; configureTrigger = null;
    if (open && restoreFocus && !disposed && trigger?.isConnected && !trigger.disabled) trigger.focus({preventScroll: true});
  }
  function renderValidation() {
    const view = state.validationView;
    if (!view) return;
    const entry = draftEntry(); const run = view.run; const result = run?.result;
    const active = ["queued", "running"].includes(result?.job?.status) || run?.pending;
    $("validate").disabled = !state.draftView?.ready || view.busy || Boolean(entry?.externalOperation || entry?.flight || active);
    $("validate").textContent = view.action === "review" ? "Preparing Configure…" : "Review Configure";
    $("validation-status").textContent = view.message || (run ? view.stale
      ? "This result belongs to an earlier draft or changed inputs. Save the current source and review Configure again."
      : run.uncertain ? "The start reply was lost. Refresh status to reconcile this reviewed operation before starting another."
        : active ? `${result?.job?.status === "queued" ? "Configure queued" : "Configure running"}. Leaving this view does not cancel it. Later source changes can make this result stale.`
          : result?.status === "ready" && !result.job ? "Configuration has not started. Review Configure again when ready."
          : result?.message || `Configuration status: ${result?.status || "awaiting result"}.`
      : "Save the overlay before reviewing configuration. Opening and saving do not run Configure, builds or application CMake.");
    $("validation-error").hidden = !view.error; $("validation-error").textContent = view.error;
    $("validation-actions").hidden = !run;
    $("validation-refresh").disabled = view.busy || view.refreshing;
    $("validation-refresh").textContent = view.refreshing ? "Refreshing…" : "Refresh status";
    $("validation-cancel").hidden = !active;
    $("validation-cancel").disabled = view.busy;
    $("validation-cancel").textContent = view.busy && active ? "Requesting cancellation…" : "Cancel Configure";
    $("validation-projects").disabled = view.busy || active || !result?.handoff;
    $("validation-projects").textContent = view.action === "handoff" ? "Opening Projects…" : "Open in Projects";
    $("validation-evidence").hidden = !run;
    const signature = JSON.stringify([run?.plan.id, result, view.stale]);
    if (signature !== renderedValidation) {
      renderedValidation = signature;
      const facts = $("validation-facts"); facts.replaceChildren();
      if (run) {
        fact(facts, "Candidate SHA-256", run.plan.candidate.sha256, true);
        fact(facts, "Input fingerprint", result?.input_fingerprint || run.plan.input_fingerprint, true);
        fact(facts, "Dependency coverage", typeof result?.coverage === "object" ? JSON.stringify(result.coverage) : result?.coverage || "Not established");
        fact(facts, "Overlay use", result?.overlay_used === true ? "Observed" : "Not established");
        fact(facts, "Output directory", run.plan.build_dir, true);
        fact(facts, "Freshness", view.stale ? "Stale" : typeof result?.freshness === "string" ? result.freshness : "See configuration result");
      }
      $("validation-log").textContent = array(result?.job?.logs).join("\n") || "No configuration log received.";
      const diagnostics = $("validation-diagnostics"); diagnostics.replaceChildren();
      for (const item of array(result?.diagnostics)) {
        const group = node("div", "dte-validation-diagnostic");
        group.append(node("p", "dte-muted", `${item.severity ? item.severity + ": " : ""}${item.message || "Configuration diagnostic"}`));
        if (item.path) group.append(node("p", "dte-path", `${item.path}${item.line ? ":" + item.line : ""}`));
        if (!view.stale && item.path === run?.plan.candidate.path && item.sha256 === entry?.server?.sha256
            && (item.source_span || diagnosticSelection(entry.text, item))) {
          const button = node("button", "", `Show source${item.line ? " line " + item.line : ""}`); button.type = "button";
          listen(button, "click", () => {
            if (state.validationView.stale || drafts.pending() || item.sha256 !== draftEntry()?.server?.sha256) return;
            const range = item.source_span ? sourceSelection(draftEntry().text, item.source_span.start, item.source_span.end)
              : diagnosticSelection(draftEntry().text, item);
            if (!range) return;
            context.reveal?.($("source")); $("source").setSelectionRange(...range);
          });
          group.append(button);
        }
        diagnostics.append(group);
      }
    }
    const reviewed = view.review;
    if (!reviewed) { closeConfigure(); return; }
    $("configure-error").hidden = !view.error; $("configure-error").textContent = view.error;
    $("configure-confirm").disabled = $("configure-close").disabled = $("configure-dismiss").disabled = view.busy;
    $("configure-confirm").textContent = view.busy ? "Starting Configure…" : "Run Configure";
    if (shownConfigure !== reviewed) {
      shownConfigure = reviewed;
      const plan = reviewed.plan; const facts = $("configure-facts"); facts.replaceChildren();
      fact(facts, "Overlay", plan.candidate.path, true);
      fact(facts, "Configuration", plan.configuration?.name);
      fact(facts, "Target", typeof plan.target === "string" ? plan.target : plan.target?.requested, true);
      fact(facts, "Output directory", plan.build_dir, true);
      fact(facts, "Candidate SHA-256", plan.candidate.sha256, true);
      fact(facts, "Environment", plan.environment?.zephyr?.path || plan.environment?.id || plan.recipe?.environment?.ZEPHYR_BASE, true);
      const limitations = $("configure-limitations"); limitations.replaceChildren();
      for (const item of array(plan.limitations)) notice(limitations, typeof item === "string" ? item : item.message);
      $("configure-command").textContent = plan.recipe?.display_configure || JSON.stringify(plan.recipe, null, 2);
      $("configure-source").textContent = plan.candidate.text;
      for (const details of $("configure-review").querySelectorAll("details")) details.open = false;
      if (!$("configure-review").open) $("configure-review").showModal();
    }
  }
  function render() {
    if (disposed) return;
    saving?.sync();
    validation?.sync();
    if (renderedWorkspace !== state.workspace) {
      renderedWorkspace = state.workspace; renderedCatalog = undefined; renderedData = undefined;
      presentedDraft = false;
      clearOpenFeedback();
      if (state.selection?.kind === "project") {
        state.formProjectId = state.selection.project_id; state.formConfigurationId = state.selection.configuration_id;
      } else { state.formProjectId = ""; state.formConfigurationId = null; }
      $("directory").value = state.selection?.kind === "application" ? state.selection.application_path : "";
    }
    renderCatalog();
    // Keep the previous layout visible but inert during a context read. Hiding
    // the long draft would clamp the browser's scroll position before it returns.
    const reading = state.loading || state.documentLoading;
    $("open-directory").disabled = reading;
    $("open-directory").textContent = reading && state.selection?.kind === "application" ? "Opening…" : "Open directory";
    $("results").inert = reading;
    $("results").setAttribute("aria-busy", String(reading));
    $("recheck").textContent = reading ? "Checking…" : "Recheck context";
    if (reading && renderedData) return;
    renderContext(state.data);
    void drafts?.bind(state.data, state.document);
    $("loading").hidden = !state.loading;
    $("error").hidden = !state.error; $("error").textContent = state.error || "";
    $("empty").hidden = Boolean(state.data || state.loading || state.error);
    $("results").hidden = !state.data;
    root.setAttribute("data-has-project", String(Boolean(state.data)));
    $("open-directory").disabled = state.loading;
    $("open-directory").textContent = state.loading && state.selection?.kind === "application" ? "Opening…" : "Open directory";
    $("recheck").disabled = state.loading || state.documentLoading;
    $("draft-panel").inert = Boolean(state.draftView?.loading);
    $("draft-panel").setAttribute("aria-busy", String(Boolean(state.draftView?.loading)));
    if (state.draftView?.loading && presentedDraft) return;
    renderDraft();
    presentedDraft = Boolean(state.draftView?.ready);
    renderSaving();
    renderValidation();
    chip?.render();
  }
  async function action(operation, destination, options = {}) {
    const captured = ++actionGeneration;
    const workspace = context.workspace.path;
    const current = () => !disposed && captured === actionGeneration && workspace === context.workspace.path;
    if (options.open) {
      if (options.open !== "overlay") {
        $("overlay-feedback").hidden = true;
      }
      const status = $(options.open + "-feedback"); status.textContent = "Opening…"; status.className = "dte-muted"; status.hidden = false;
    }
    const accepted = await retainActionFocus(root, async () => {
      const accepted = await operation();
      if (current() && accepted && options.open && state.data && state.document) {
        await drafts.bind(state.data, state.document);
      }
      return accepted;
    }, current, lifetime.signal, {fallbackTo: options.fallbackTo});
    if (!current()) return;
    if (!options.open && !accepted && !state.error && !state.documentError && !state.draftView?.error && !draftEntry()?.error) return;
    const target = state.error ? $("error") : state.documentError ? $("source-error")
      : state.draftView?.error || draftEntry()?.error ? $("draft-error")
        : state.data && state.data.application?.availability?.state !== "available" ? $("overlay-status") : destination();
    if (options.open) {
      const status = $(options.open + "-feedback");
      const failed = Boolean(!accepted || state.error || state.documentError || state.draftView?.error || draftEntry()?.error
        || options.open === "overlay" && !state.draftView?.ready
        || state.data?.application?.availability?.state !== "available");
      status.textContent = failed ? target?.textContent || "The application could not be opened."
        : options.open === "overlay" ? "Overlay ready. Chip assignments refresh automatically."
          : "Application opened. Chip discovery starts automatically.";
      status.className = failed ? "dte-error" : "dte-muted"; status.hidden = false;
      feedback(status, {error: failed});
    } else if (target) feedback(target, {message: options.message, error: Boolean(state.error || state.documentError || state.draftView?.error || draftEntry()?.error || state.catalogError)});
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  const openSelectedProject = () => {
    if ($("project").value && $("configuration").options.length) {
      void action(() => session.openProject($("project").value, $("configuration").value || null), () => $("overlay-status"), {open: "project"});
    }
  };
  listen($("project"), "change", () => { state.formProjectId = $("project").value; state.formConfigurationId = null; configurations(); openSelectedProject(); });
  listen($("configuration"), "change", () => { state.formConfigurationId = $("configuration").value || null; openSelectedProject(); });
  listen($("project-form"), "submit", (event) => {
    event.preventDefault();
    void action(() => session.openProject($("project").value, $("configuration").value || null), () => $("overlay-status"), {open: "project"});
  });
  listen($("directory-form"), "submit", (event) => {
    event.preventDefault(); void action(() => session.openApplication($("directory").value), () => $("overlay-status"), {open: "directory"});
  });
  listen($("overlay"), "change", () => {
    if ($("overlay").value && !state.loading && !state.documentLoading) {
      void action(() => session.selectDocument($("overlay").value), () => $("draft-status"), {open: "overlay"});
    }
  });
  listen($("overlay-form"), "submit", (event) => {
    event.preventDefault(); void action(() => session.selectDocument($("overlay").value), () => $("draft-status"), {open: "overlay"});
  });
  listen($("refresh-catalog"), "click", () => {
    void action(() => session.catalog(), () => state.catalogError ? $("catalog-error") : null);
  });
  listen($("recheck"), "click", () => { void action(() => session.revalidate(), () => $("overlay-status")); });
  listen($("source"), "input", () => { drafts.type($("source").value); });
  const history = (kind) => {
    if ($(kind).getAttribute("aria-disabled") === "true") return Promise.resolve(false);
    return action(() => drafts[kind](), () => $("draft-status"));
  };
  listen($("source"), "keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === "z" || key === "y") { event.preventDefault(); void history(key === "y" || event.shiftKey ? "redo" : "undo"); }
  });
  listen($("undo"), "click", () => { void history("undo"); });
  listen($("redo"), "click", () => { void history("redo"); });
  listen($("registration-retry"), "click", () => {
    if (saving.canRetryRegistration()) void action(() => saving.retryRegistration(), () => $("save-status"), {fallbackTo: $("save")});
  });
  listen($("reconcile"), "click", () => { void action(() => drafts.reconcile(), () => $("draft-status")); });
  listen($("use-local"), "click", () => { void action(() => drafts.resolveConflict("local"), () => $("draft-status")); });
  listen($("use-server"), "click", () => { void action(() => drafts.resolveConflict("server"), () => $("draft-status")); });
  listen($("view-retained"), "click", () => {
    $("retained-source").hidden = false; $("retained-source").value = editorText(state.draftCache[$("retained").value]?.text || "");
    context.reveal?.($("retained-source"));
  });
  listen($("retained"), "change", () => { $("retained-source").hidden = true; });
  async function savingAction(operation, trigger, {recovery = false} = {}) {
    const captured = ++actionGeneration; const workspace = context.workspace.path;
    const current = () => !disposed && captured === actionGeneration && workspace === context.workspace.path;
    reviewTrigger = trigger;
    const accepted = await retainActionFocus(root, operation, current, lifetime.signal, {returnTo: trigger});
    if (!current()) return;
    if (state.saveView.review) return;
    if (state.saveView.error) feedback($("draft-error"), {error: true});
    else if (recovery) feedback($(state.saveView.recoveryError ? "recoveries-error" : "recoveries-status"), {error: Boolean(state.saveView.recoveryError)});
    else if (accepted && state.saveView.message) feedback($("save-status"));
  }
  listen($("save"), "click", () => { void savingAction(() => saving.reviewSave(), $("save")); });
  listen($("compare"), "click", () => { void savingAction(() => saving.compare(), $("compare")); });
  listen($("reload"), "click", () => { void savingAction(() => saving.reviewReload(), $("reload")); });
  listen($("export"), "click", () => { const entry = draftEntry(); exportSource(entry?.text, entry?.path); });
  listen($("export-retained"), "click", () => {
    const entry = state.draftCache[$("retained").value]; exportSource(entry?.text, entry?.path);
  });
  listen($("refresh-recovery"), "click", () => {
    void savingAction(() => saving.listRecoveries(), $("refresh-recovery"), {recovery: true});
  });
  listen($("review-recovery"), "click", () => {
    void savingAction(() => saving.reviewRecovery($("recoveries").value), $("review-recovery"), {recovery: true});
  });
  listen($("review-confirm"), "click", () => {
    void savingAction(() => saving.confirm(), reviewTrigger);
  });
  for (const name of ["review-close", "review-cancel"]) listen($(name), "click", () => saving.cancel());
  listen($("review"), "cancel", (event) => { event.preventDefault(); if (!state.saveView.busy) saving.cancel(); });
  listen($("review-export"), "click", () => {
    const review = state.saveView.review; if (!review) return;
    exportSource(review.kind === "save" ? review.data.candidate_text : ["recovery", "discard-recovery"].includes(review.kind) ? review.data.record.text
      : review.captured.entry.text, review.data.path || review.data.record?.path);
  });
  listen($("review-delete"), "click", () => { saving.reviewRecoveryDiscard(); });
  async function validationAction(operation, {review = false} = {}) {
    const captured = ++actionGeneration; const workspace = context.workspace.path;
    const current = () => !disposed && captured === actionGeneration && workspace === context.workspace.path;
    if (review) configureTrigger = $("validate");
    await retainActionFocus(root, operation, current, lifetime.signal, {returnTo: configureTrigger});
    if (!current() || state.validationView.review) return;
    feedback($(state.validationView.error ? "validation-error" : "validation-status"), {error: Boolean(state.validationView.error)});
  }
  listen($("validate"), "click", () => { void validationAction(() => validation.review(), {review: true}); });
  listen($("configure-confirm"), "click", () => { void validationAction(() => validation.submit()); });
  for (const name of ["configure-close", "configure-dismiss"]) listen($(name), "click", () => validation.closeReview());
  listen($("configure-review"), "cancel", (event) => { event.preventDefault(); if (!state.validationView.busy) validation.closeReview(); });
  listen($("validation-refresh"), "click", () => { void validationAction(() => validation.refresh({explicit: true})); });
  listen($("validation-cancel"), "click", () => { void validationAction(() => validation.cancel()); });
  listen($("validation-projects"), "click", () => {
    void validationAction(async () => { const route = await validation.handoff(); if (route && !disposed) context.navigate(route); });
  });
  drafts = createDraftSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  session = createEditorSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  saving = createSaveSession({api: context.api, state, workspace: () => context.workspace.path, drafts,
    adopt: (data, document) => session.adopt(data, document), onChange: render});
  validation = createValidationSession({api: context.api, state, workspace: () => context.workspace.path, drafts, onChange: render});
  chip = mountChipPanel($("chip"), {api: context.api, state, workspace: () => context.workspace.path,
    drafts, notify: context.notify, onChange: render});
  render();
  return {
    navigate(view) { actionGeneration++; saving.cancel(); validation.closeReview(); validation.resume(); chip.navigate(); return session.navigate(view); },
    dispose() {
      disposed = true; actionGeneration++; lifetime.abort(); saving.dispose(); closeReview({restoreFocus: false});
      validation.dispose(); closeConfigure({restoreFocus: false}); chip.dispose(); session.dispose(); drafts.dispose();
      for (const url of downloadUrls) URL.revokeObjectURL(url);
    },
  };
}
