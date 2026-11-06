import {announceResult} from "./view-feedback.js";
import {createRemovalSession, createResourceSession} from "./resources.js";

const array = (value) => Array.isArray(value) ? value : [];
const names = {error: "Conflict", warning: "Warning", unknown: "Unknown", info: "Information"};
const severityOrder = {error: 0, warning: 1, unknown: 2, info: 3};
const diagnosticTitles = {"gpio-conflict": "GPIO pin use", "bus-address-conflict": "Bus address", "unknown-status": "Enabled status",
  "binding-coverage": "Binding coverage", "unresolved-reference": "Reference target", "ambiguous-reference": "Ambiguous reference",
  "required-property-unknown": "Required property", "pin-conflict": "Pin use", "pin-reserved": "Reserved pin"};

export function mountResourcePanel(root, {api, state, workspace, drafts, onChange, notify, onSource}) {
  const document = root.ownerDocument; const lifetime = new AbortController(); const handlers = new WeakMap();
  let disposed = false; let navigation = 0; let rendered; let shownReview; let trigger; let restoreOnClose = true; let selectedKey = "";
  const sourceButtons = []; const repairInputs = new Map();
  const make = (tag, text, className = "") => {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className; return node;
  };
  const listen = (node, event, callback) => {
    if (!handlers.has(node)) handlers.set(node, {}); handlers.get(node)[event] = callback;
  };
  for (const type of ["click", "change"]) root.addEventListener(type, (event) => {
    for (let node = event.target; node && node !== root; node = node.parentNode) {
      handlers.get(node)?.[type]?.(event); if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (text, action, className = "") => {
    const node = make("button", text, className); node.type = "button";
    listen(node, "click", action); return node;
  };
  const note = (parent, text, className = "dte-muted") => {
    if (text) parent.append(make("p", text, className));
  };
  const option = (select, value, text) => { const node = make("option", text); node.value = value; select.append(node); };
  const stamp = () => JSON.stringify([workspace(), state.data?.id, state.data?.revision,
    state.draftView?.entry?.server?.id, state.draftView?.entry?.server?.revision,
    state.draftView?.entry?.server?.sha256, state.draftView?.entry?.generation]);
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load diagnostics", () => { void resources.load(); });
  const cancel = button("Cancel inspection", () => resources.cancel());
  heading.append(make("h3", "Resources and removal"), load, cancel); root.append(heading);
  note(root, "Inspect known consumers and conflicts before changing resources. Diagnostics do not run project tools, change wiring or block Save.");
  const status = make("p", "", "dte-resource-status dte-muted"); status.setAttribute("role", "status");
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert");
  const reconcile = button("Reconcile resource draft", () => { void resources.reconcile(); });
  const summary = make("p", "", "dte-resource-summary");
  const filter = make("select"); for (const [key, text] of [["all", "All diagnostics"], ["error", "Conflicts"], ["warning", "Warnings"], ["unknown", "Unknown evidence"], ["info", "Information"]]) option(filter, key, text);
  const filterLabel = make("label", "Diagnostic category"); filterLabel.append(filter);
  const list = make("div", undefined, "dte-resource-list");
  const controls = make("div", undefined, "dte-resource-controls"); controls.append(summary, filterLabel, list);
  const removalForm = make("div", undefined, "dte-resource-removal"); const target = make("select");
  const targetLabel = make("label", "Removal target"); targetLabel.append(target);
  const review = button("Review removal", () => { void reviewRemoval(target.value, review); });
  removalForm.append(make("h4", "Plan a removal"), targetLabel, review);
  note(removalForm, "Review the selected source node and its descendants. Shared consumers and references require explicit repairs; nothing is removed by opening a review.");
  controls.append(removalForm); root.append(status, error, reconcile, controls);

  const dialog = make("dialog", undefined, "dte-review dte-removal-review"); dialog.setAttribute("aria-label", "Review removal from draft");
  const dialogHeading = make("div", undefined, "dte-review-heading"); const title = make("h2", "Review removal from draft");
  const close = button("Close removal review", () => removal.cancel()); dialogHeading.append(title, close);
  const body = make("div", undefined, "dte-review-body"); const decision = make("p", "", "dte-removal-decision");
  const modalError = make("p", "", "dte-error"); modalError.setAttribute("role", "alert");
  const fixedSummary = make("div", undefined, "dte-removal-summary"); fixedSummary.append(decision, modalError);
  const contents = make("div"); body.append(contents);
  const footer = make("div", undefined, "dte-review-actions");
  const cancelReview = button("Cancel", () => removal.cancel());
  const refresh = button("Update removal preview", () => { void removal.update(); });
  const apply = button("Apply removal to draft", () => { void removal.apply(); }, "dte-primary");
  footer.append(cancelReview, refresh, apply); dialog.append(dialogHeading, fixedSummary, body, footer); root.append(dialog);
  // Native cancel/close events do not bubble through all browser implementations.
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); if (!state.removalView.applying) removal.cancel(); }, {signal: lifetime.signal});
  dialog.addEventListener("close", () => { if (!dialog.open && (state.removalView.review || state.removalView.outcome)) removal.cancel(); }, {signal: lifetime.signal});

  const resources = createResourceSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  const removal = createRemovalSession({api, state, workspace, drafts, resources, onChange() { render(); onChange?.(); }});
  listen(filter, "change", () => renderDiagnostics());
  listen(target, "change", () => {
    selectedKey = state.resourceView.data?.nodes.find((item) => item.node_id === target.value)?.key || target.value;
    updateDisabled();
  });
  function showSource(location, observation, fromReview = false) {
    if (!location || disposed) return;
    if (fromReview) { restoreOnClose = false; removal.cancel(); }
    onSource?.(location, observation);
  }
  function sourceLink(parent, location, observation, label = "Show source", fromReview = false) {
    if (!location || !Number.isInteger(location.start)) return;
    const node = button(label, () => showSource(location, observation, fromReview));
    node.dataset.resourceSource = "true"; if (!fromReview) sourceButtons.push(node); parent.append(node);
  }
  function renderDiagnostics() {
    const data = state.resourceView.presentation || state.resourceView.data; sourceButtons.length = 0; list.replaceChildren();
    if (!data) return;
    const diagnostics = array(data.diagnostics).filter((item) => filter.value === "all" || item.severity === filter.value)
      .sort((left, right) => (severityOrder[left.severity] ?? 2) - (severityOrder[right.severity] ?? 2));
    if (!diagnostics.length) note(list, filter.value === "all" ? "No known local conflicts were found in this observation. Unknown coverage remains listed separately." : "No diagnostics in this category.");
    for (const item of diagnostics) {
      const row = make("article", undefined, "dte-resource-diagnostic"); row.dataset.severity = item.severity;
      const title = diagnosticTitles[item.code] || String(item.code || "resource").replaceAll("-", " ");
      row.append(make("h4", `${names[item.severity] || "Unknown"}: ${title}`)); note(row, item.message);
      const locations = make("div", undefined, "dte-node-actions");
      const unique = new Map();
      const addLocation = (location) => {
        if (location) unique.set(JSON.stringify([location.node_id, location.property_id, location.start, location.end]), location);
      };
      for (const location of array(item.locations)) addLocation(location);
      for (const choice of array(item.repair_choices)) {
        addLocation(choice.location);
        if (choice.label && !/^Inspect (source|peripherals|pins|bindings)$/.test(choice.label)) note(row, `Suggested repair: ${choice.label}`, "dte-resource-repair dte-muted");
      }
      for (const location of unique.values()) sourceLink(locations, location, data,
        `Inspect source: ${location.path || location.name || "declaration"}${location.name && location.path ? " · " + location.name : ""}`);
      row.append(locations);
      list.append(row);
    }
    updateDisabled();
  }
  function closeDialog() {
    if (!dialog.open) return;
    dialog.close(); shownReview = null;
    const previous = trigger; trigger = null;
    if (restoreOnClose && !disposed) {
      const focus = previous?.isConnected && !previous.disabled && !previous.closest?.("[hidden]") ? previous : load;
      focus.focus?.({preventScroll: true});
    }
    restoreOnClose = true;
  }
  function reviewedSource(parent, location, data, text = "Show source") { sourceLink(parent, location, data, text, true); }
  function renderReview() {
    const view = state.removalView; const current = view.review?.data || view.outcome;
    if (!current) { closeDialog(); return; }
    const outcome = Boolean(view.outcome);
    let focusOutcome = false;
    if (shownReview !== current) {
      const scrollTop = body.scrollTop;
      shownReview = current; contents.replaceChildren(); repairInputs.clear();
      title.textContent = outcome ? "Removal applied to draft" : "Review removal from draft";
      if (!outcome) {
        note(contents, `${current.target.path || current.target.name}. This changes only the working draft; overlay Save remains separate.`);
        reviewedSource(contents, current.target.location, current, "Inspect removal target");
        if (current.descendants.length) {
          const details = make("details"); details.append(make("summary", `Included declarations (${current.descendants.length})`));
          for (const item of current.descendants) note(details, item.path || item.name); contents.append(details);
        }
        const dependants = make("section", undefined, "dte-removal-dependants"); dependants.append(make("h3", "Known dependants"));
        if (!current.dependants.length) note(dependants, "No known local dependant references were found. This does not establish unknown external consumers.");
        for (const item of current.dependants) {
          const row = make("div", undefined, "dte-removal-dependant"); note(row, item.message || `${item.reference || "Reference"} depends on this removal target.`);
          reviewedSource(row, item.location, current, "Inspect dependant source"); dependants.append(row);
        }
        contents.append(dependants);
        if (current.repair_choices.length) {
          const group = make("fieldset", undefined, "dte-removal-repairs"); group.append(make("legend", "Explicit repairs"));
          note(group, "Select only the repairs you intend, then update the removal preview. Unselected repairs are not applied.");
          for (const choice of current.repair_choices) {
            const label = make("label", choice.label); const input = make("input"); input.type = "checkbox"; input.checked = choice.selected;
            label.append(input); group.append(label); note(group, choice.consequence);
            listen(input, "change", () => removal.select(choice.id, input.checked)); repairInputs.set(choice.id, input);
          }
          contents.append(group);
        }
        for (const item of current.blockers) {
          const blocked = make("div", undefined, "dte-notice"); note(blocked, item.message);
          reviewedSource(blocked, item.location, current, "Inspect blocked dependency"); contents.append(blocked);
        }
        if (current.unknowns.length) {
          const unknown = make("details"); unknown.append(make("summary", "Unknown evidence and scope"));
          for (const item of current.unknowns) note(unknown, item); contents.append(unknown);
        }
        const diff = make("details"); diff.open = Boolean(current.can_apply);
        diff.append(make("summary", "Proposed source changes")); note(diff, current.diff.truncated ? "Diff shortened; inspect the complete candidate before applying." : "Compared with the current canonical draft.");
        diff.append(make("pre", current.diff.text || "No applicable source changes in this preview.", "dte-source")); contents.append(diff);
        if (current.candidate_text !== null) {
          const candidate = make("details"); candidate.append(make("summary", "Complete candidate source"), make("pre", current.candidate_text, "dte-source")); contents.append(candidate);
        }
        note(contents, `Review expires ${new Date(current.expires_at).toLocaleTimeString()}.`);
      }
      body.scrollTop = scrollTop;
      if (!dialog.open) { dialog.showModal(); close.focus?.({preventScroll: true}); }
      else if (outcome) focusOutcome = true;
    }
    decision.textContent = outcome ? view.outcome.message : Date.parse(current.expires_at) <= Date.now() ? "This removal review expired. Close it and review the current draft again."
      : view.previewDirty ? "Repair choices changed. Update the preview before applying."
      : current.can_apply ? `Remove ${current.target.name} with exactly the selected repairs shown below.`
        : "Removal is blocked by the dependencies or source limitations below. Repair them explicitly, or keep this resource.";
    modalError.hidden = !view.error; modalError.textContent = view.error;
    cancelReview.textContent = outcome ? "Close" : "Keep resource / cancel";
    cancelReview.disabled = close.disabled = view.applying;
    refresh.hidden = apply.hidden = outcome;
    refresh.disabled = view.busy || !view.previewDirty;
    refresh.textContent = view.busy && !view.applying ? "Updating preview…" : "Update removal preview";
    apply.disabled = !removal.canApply(); apply.textContent = view.applying ? "Applying removal…" : "Apply removal to draft";
    for (const [id, input] of repairInputs) { input.checked = view.selected.includes(id); input.disabled = view.busy; }
    for (const node of dialog.querySelectorAll("button")) if (node.dataset.resourceSource === "true") node.disabled = view.busy;
    if (focusOutcome) close.focus?.({preventScroll: true});
  }
  function updateDisabled() {
    const view = state.resourceView; const current = Boolean(view.data && !view.busy && resources.ready());
    load.disabled = view.busy || !resources.ready() || state.removalView.busy;
    load.textContent = view.busy ? "Inspecting resources…" : "Load diagnostics"; cancel.hidden = !resources.scanning();
    reconcile.hidden = !resources.needsReconcile(); reconcile.disabled = !resources.canReconcile();
    review.disabled = !current || !target.value || !removal.canStart();
    target.disabled = !current; filter.disabled = !view.presentation && !view.data;
    for (const node of sourceButtons) node.disabled = !current;
  }
  function render() {
    if (disposed) return;
    resources.sync(); removal.sync(); const view = state.resourceView; const data = view.presentation || view.data;
    controls.hidden = !data;
    status.textContent = state.removalView.message || view.message || "Load diagnostics to inspect the current draft without running project tools.";
    error.hidden = !view.error && !(state.removalView.error && !state.removalView.review);
    error.textContent = view.error || state.removalView.error;
    if (rendered !== data) {
      rendered = data; target.replaceChildren(); option(target, "", "Choose a local source node");
      if (data) {
        const counts = Object.fromEntries(Object.keys(names).map((key) => [key, data.diagnostics.filter((item) => item.severity === key).length]));
        summary.textContent = `${counts.error} conflicts · ${counts.warning} warnings · ${counts.unknown} unknown · ${counts.info} informational`;
        for (const node of data.nodes) option(target, node.node_id, `${node.path || node.name}${node.unique === false ? " · multiple declarations" : ""}`);
        const selected = data.nodes.find((node) => (node.key || node.node_id) === selectedKey);
        if (selected) target.value = selected.node_id;
      } else { selectedKey = ""; summary.textContent = ""; }
      renderDiagnostics();
    }
    updateDisabled(); renderReview();
  }
  async function reviewRemoval(nodeId, sourceTrigger) {
    const before = stamp(); const token = navigation; trigger = sourceTrigger || review; restoreOnClose = true;
    if (!state.resourceView.data && !await resources.load()) {
      if (!disposed && token === navigation && before === stamp()) announceResult(error.hidden ? status : error, notify, {error: true});
      return false;
    }
    if (disposed || token !== navigation || before !== stamp()) return false;
    const accepted = await removal.begin(nodeId);
    if (!disposed && token === navigation && before === stamp() && !accepted) announceResult(error.hidden ? status : error, notify, {error: true});
    return accepted;
  }
  render();
  return {render, reviewRemoval, navigate() { navigation++; restoreOnClose = false; removal.cancel(); resources.clear(); render(); },
    dispose() { disposed = true; navigation++; restoreOnClose = false; lifetime.abort(); removal.dispose(); resources.dispose(); if (dialog.open) dialog.close(); }};
}
