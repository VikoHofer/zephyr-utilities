import {boardJobActive, boardReferences, createCustomBoardsSession} from "./custom-boards-state.js";
import {createReviewDialog} from "./review-dialog.js";

const array = (value) => Array.isArray(value) ? value : [];
const titles = {register: "Register existing custom boards", update: "Edit custom board location", remove: "Remove board registration", create: "Create a custom board"};

export function customBoardSummary(plan) {
  return [["Operation", titles[plan.operation] || "Custom board change"], ["Name", plan.name || plan.registration?.name || "—"],
    ["Root directory", plan.path || plan.registration?.path || plan.previous?.path || "—"],
    ["Source files", array(plan.files).length ? `${plan.files.length} new files` : "Unchanged"],
    ["Affected saved configurations", array(plan.dependencies).length]];
}

export function mountCustomBoards(root, context) {
  const lifetime = new AbortController();
  const state = context.state.customBoards ||= {};
  let disposed = false, session, renderedRegistrations, renderedEditor, renderedPlan, watchedJob;
  const $ = (name) => root.querySelector(`[data-custom-${name}]`);
  function node(tag, className, text) {
    const element = root.ownerDocument.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function button(text, action, id) {
    const element = node("button", "projects-button", text); element.type = "button";
    element.dataset.customAction = action; if (id) element.dataset.id = id;
    return element;
  }
  function fact(parent, label, text) {
    const item = node("div", "projects-fact"); item.append(node("dt", "", label), node("dd", "projects-path", text)); parent.append(item);
  }
  function choices(select, entries, placeholder) {
    const signature = JSON.stringify(entries);
    if (select.dataset.choices === signature) return;
    const previous = select.value;
    select.replaceChildren();
    if (placeholder) { const option = node("option", "", placeholder); option.value = ""; select.append(option); }
    for (const [value, label, disabled] of entries) { const option = node("option", "", label); option.value = value; option.disabled = Boolean(disabled); select.append(option); }
    select.dataset.choices = signature; select.value = previous;
  }
  function detailList(parent, title, entries) {
    if (!entries.length) return;
    const details = node("details"); details.append(node("summary", "", title));
    const list = node("ul", "projects-source-list");
    for (const entry of entries) list.append(node("li", "", entry));
    details.append(list); parent.append(details);
  }
  const dialog = $("dialog"), reviewDialog = createReviewDialog(dialog);
  function renderRegistrations() {
    if (renderedRegistrations === state.registrations) return;
    renderedRegistrations = state.registrations;
    const list = $("registrations"), active = root.ownerDocument.activeElement?.closest("[data-custom-action][data-id]");
    const focus = active && root.contains(active) ? [active.dataset.customAction, active.dataset.id] : null;
    list.replaceChildren();
    const entries = array(state.registrations);
    $("count").textContent = `${entries.length} registered ${entries.length === 1 ? "location" : "locations"}`;
    if (!entries.length) list.append(node("p", "projects-empty-list", "No custom board locations are registered. Register an existing folder or create a board from your local reference ports."));
    for (const entry of entries) {
      const card = node("article", "projects-custom-board-card");
      const heading = node("div", "projects-section-heading");
      const title = node("div"); title.append(node("h3", "", entry.name), node("p", "projects-path", entry.path));
      const status = node("span", `projects-label${entry.availability !== "available" ? " warning" : ""}`, entry.availability || "Saved; refresh to check");
      heading.append(title, status); card.append(heading);
      if (entry.availability_reason) card.append(node("p", "projects-banner", entry.availability_reason));
      const boards = array(entry.boards);
      card.append(node("p", "projects-muted", `${boards.length} ${boards.length === 1 ? "board definition" : "board definitions"} · ${array(entry.dependencies).length} saved configuration references`));
      const targets = node("div", "projects-custom-targets");
      for (const board of boards) {
        const item = node("div"); item.append(node("strong", "", board.full_name || board.name));
        const discovered = array(state.snapshot?.boards).find((candidate) => candidate.id === board.id && candidate.status === "discovered");
        item.append(node("p", "projects-path", array(board.targets).join(", ") || board.status || "No selectable targets"));
        if (!state.needsAnalysis && discovered) {
          const use = button("Create application", "use", board.id); use.classList.add("primary"); item.append(use);
        }
        targets.append(item);
      }
      card.append(targets);
      detailList(card, "Saved configurations using this location", array(entry.dependencies).map((dependency) =>
        `${dependency.project_name || dependency.project_id} · ${dependency.name || "Original"} · ${dependency.target || "Unknown target"}`));
      detailList(card, "Location diagnostics", array(entry.diagnostics));
      const actions = node("div", "projects-actions");
      actions.append(button("Rename or reconnect", "update", entry.id), button("Remove registration", "remove", entry.id));
      card.append(actions); list.append(card);
    }
    if (focus) [...list.querySelectorAll("[data-custom-action]")].find((item) => item.dataset.customAction === focus[0] && item.dataset.id === focus[1])?.focus({preventScroll: true});
  }
  function renderForm(editor) {
    const creating = editor.operation === "create", removing = editor.operation === "remove";
    $("registration-fields").hidden = creating || removing;
    $("creation-fields").hidden = !creating;
    $("remove-note").hidden = !removing;
    $("remove-note").textContent = removing ? `Remove “${editor.fields.name}” from Utilities? Source files at ${editor.fields.path} remain on disk. The review will list affected saved configurations. Independent discovery paths or module settings may still make these boards available after removal.` : "";
    if (creating) {
      const socFirst = editor.fields.mode === "soc";
      $("soc-field").hidden = !socFirst;
      const socSelect = root.querySelector('[data-custom-field="soc"]');
      choices(socSelect, array(state.catalogue?.socs).filter((soc) => soc.status === "discovered").map((soc) => [soc.name, soc.name]), "Choose a supported SoC");
      const references = boardReferences(state.catalogue, editor.fields);
      const selected = array(state.catalogue?.references).find((reference) => reference.id === editor.fields.reference_id);
      const visible = selected && !references.includes(selected) ? [selected, ...references] : references;
      choices(root.querySelector('[data-custom-field="reference_id"]'), visible.map((reference) =>
        [reference.id, `${reference.full_name || reference.name} (${reference.name})${reference.eligible === false ? ` — ${reference.reason || "Unsupported layout"}` : ""}`, reference.eligible === false]),
      socFirst && !editor.fields.soc ? "Choose a SoC first" : "Choose a reference board");
      $("reference-count").textContent = `${references.filter((reference) => reference.eligible !== false).length} candidate reference ports. File and dependency checks run when you review.`;
      $("reference-note").textContent = selected
        ? `Reference: ${selected.name}. SoC targets: ${array(selected.socs).join(", ")}. All supported targets in this reference are retained. ${selected.reason || ""}`
        : socFirst ? "Choose a matching reference port for the selected SoC. Its existing hardware settings provide the starting point." : "Choose a reference port from the analyzed Zephyr checkout or registered board locations.";
      root.querySelector('[data-custom-create-path]').placeholder = `hardware/${editor.fields.name || "my_board"}`;
    }
    for (const control of root.querySelectorAll("[data-custom-field]")) {
      const key = control.dataset.customField;
      if (Object.hasOwn(editor.fields, key) && control.value !== editor.fields[key]) control.value = editor.fields[key];
      control.disabled = Boolean(state.busy) || Boolean(control.closest("[hidden]"));
    }
  }
  function renderReview(plan) {
    if (renderedPlan === plan) return;
    renderedPlan = plan;
    const summary = $("summary"); summary.replaceChildren();
    for (const [label, value] of customBoardSummary(plan)) fact(summary, label, value);
    const evidence = $("evidence"); evidence.replaceChildren();
    for (const warning of array(plan.warnings)) evidence.append(node("p", "projects-banner", warning));
    const targets = array(plan.targets).length ? plan.targets : array(plan.boards).flatMap((board) => array(board.targets));
    detailList(evidence, "Board targets", targets);
    detailList(evidence, "Affected saved configurations", array(plan.dependencies).map((dependency) => `${dependency.project_name || dependency.project_id} · ${dependency.name || "Original"} · ${dependency.target || "Unknown target"}`));
    detailList(evidence, "Discovery diagnostics", array(plan.diagnostics));
    const files = array(plan.files);
    if (files.length) {
      const details = node("details"); details.append(node("summary", "", `Review all ${files.length} generated files`));
      for (const file of files) {
        const item = node("details", "projects-preview-file"); item.append(node("summary", "projects-path", file.path), node("pre", "", file.content)); details.append(item);
      }
      evidence.append(details);
    }
    if (plan.previous?.path && plan.previous.path !== (plan.path || plan.registration?.path)) evidence.append(node("p", "projects-muted", `Previously registered at ${plan.previous.path}. Existing application configurations keep their captured paths until explicitly edited.`));
  }
  function renderEditor() {
    const editor = state.editor;
    if (!editor) { reviewDialog.close(); renderedEditor = null; renderedPlan = null; return; }
    if (renderedEditor !== editor) { renderedEditor = editor; renderedPlan = null; }
    $("dialog-title").textContent = titles[editor.operation];
    const reviewing = Boolean(editor.plan);
    $("form").hidden = reviewing;
    $("review").hidden = !reviewing;
    $("dialog-error").hidden = !state.error; $("dialog-error").textContent = state.error || "";
    $("dialog-progress").hidden = !state.busy;
    $("dialog-progress").textContent = state.busy === "preview" ? "Checking the selected files and preparing the review…" : state.busy === "apply" ? "Applying the reviewed change…" : "";
    if (reviewing) renderReview(editor.plan); else renderForm(editor);
    $("back").hidden = !reviewing || editor.operation === "remove"; $("back").disabled = Boolean(state.busy);
    $("preview").hidden = reviewing; $("preview").disabled = Boolean(state.busy);
    $("preview").textContent = state.busy === "preview" ? "Preparing review…" : "Review changes";
    $("apply").hidden = !reviewing; $("apply").disabled = Boolean(state.busy);
    $("apply").textContent = state.busy === "apply" ? "Applying…" : editor.operation === "create" ? "Create board files" : editor.operation === "remove" ? "Remove registration" : "Save registration";
    $("close").disabled = state.busy === "apply";
  }
  function render() {
    if (disposed) return;
    $("workspace").textContent = state.workspace || context.workspace.path;
    const busy = Boolean(state.loading || state.busy || boardJobActive(state.job));
    for (const action of ["register", "create", "refresh", "analyze"]) $(action).disabled = busy;
    $("create").disabled ||= !state.snapshot || state.snapshot.status === "stale";
    $("refresh").textContent = state.loading ? "Refreshing…" : "Refresh locations";
    $("analyze").textContent = state.busy === "analyze" ? "Starting analysis…" : boardJobActive(state.job) ? "Analyzing workspace…" : "Analyze workspace";
    $("error").hidden = !state.error || Boolean(state.editor); $("error").textContent = state.error || "";
    $("notice").hidden = !state.notice; $("notice").textContent = state.notice || "";
    $("requirement").hidden = !state.needsAnalysis;
    $("requirement").textContent = "Analyze the workspace to refresh custom targets before creating a board or choosing one for an application.";
    const job = state.job;
    $("job").hidden = !job && state.busy !== "analyze";
    $("job-status").textContent = state.busy === "analyze" ? "Starting local environment analysis…" : job
      ? ({queued: "Analysis queued", running: "Analyzing workspace", completed: "Analysis complete", cancelled: "Analysis cancelled", failed: "Analysis failed"}[job.status] || "Environment analysis") : "";
    $("job-description").textContent = boardJobActive(job) ? "You can leave this view while discovery continues." : job?.error || "Select Create application beside an available board to continue with a configure or build.";
    $("logs").textContent = array(job?.logs).join("\n") || "No scan messages yet.";
    $("cancel").hidden = !boardJobActive(job); $("cancel").disabled = state.cancelling;
    $("cancel").textContent = state.cancelling ? "Cancelling…" : "Cancel scan";
    renderRegistrations();
    for (const control of $("registrations").querySelectorAll("button")) control.disabled = busy;
    const diagnostics = $("diagnostics"); diagnostics.replaceChildren();
    for (const message of array(state.diagnostics)) diagnostics.append(node("p", "projects-banner", typeof message === "string" ? message : message.message || "Discovery diagnostic"));
    renderEditor();
    if (watchedJob && state.workspace === watchedJob.workspace && context.workspace.path === watchedJob.workspace && job?.id === watchedJob.id && !boardJobActive(job)) {
      watchedJob = null;
      context.notify?.(job.status === "completed" ? "Custom board analysis complete. Open Custom boards to use the discovered targets." : job.error || `Analysis ${job.status}.`, job.status === "failed");
    }
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  function validAction(path) { return !disposed && state.workspace === path && context.workspace.path === path; }
  function reportError(path) {
    if (validAction(path) && state.error) {
      context.notify?.(state.error, true);
      context.reveal?.(state.editor ? $("dialog-error") : $("error"));
    }
  }
  async function review() {
    const path = state.workspace;
    await session.preview(); reportError(path);
    if (validAction(path) && state.editor?.plan) {
      $("dialog-title").focus({preventScroll: true});
      context.reveal?.($("summary"), {focus: false});
    }
  }
  function open(operation, id, trigger) {
    const path = context.workspace.path;
    if (!session.open(operation, id)) { reportError(path); return; }
    reviewDialog.show({id: `${operation}:${id || "new"}`}, () => id
      ? [...root.querySelectorAll("[data-custom-action]")].find((element) => element.dataset.id === id && element.dataset.customAction === operation) || $("register") : trigger);
    if (operation === "remove") review();
  }
  listen($("register"), "click", (event) => open("register", null, event.currentTarget));
  listen($("create"), "click", (event) => open("create", null, event.currentTarget));
  listen($("registrations"), "click", (event) => {
    const control = event.target.closest("[data-custom-action]"); if (!control || !root.contains(control)) return;
    const action = control.dataset.customAction;
    if (action === "use") { context.state.creatorPendingBoard = {workspace: state.workspace, board_id: control.dataset.id}; context.navigate("projects/create"); }
    else open(action, control.dataset.id, control);
  });
  listen($("form"), "submit", (event) => { event.preventDefault(); review(); });
  for (const control of root.querySelectorAll("[data-custom-field]")) listen(control, control.tagName === "SELECT" ? "change" : "input", () => session.change(control.dataset.customField, control.value));
  listen($("back"), "click", () => { session.edit(); $("dialog-title").focus({preventScroll: true}); context.reveal?.($("form"), {focus: false}); });
  listen($("close"), "click", () => session.close());
  listen(dialog, "cancel", (event) => { event.preventDefault(); session.close(); });
  listen($("apply"), "click", async () => {
    const path = state.workspace;
    if (await session.apply()) { if (validAction(path)) { context.notify?.(state.notice); context.reveal?.($("next")); } }
    else reportError(path);
  });
  listen($("refresh"), "click", async () => {
    const path = context.workspace.path;
    if (await session.load()) { if (validAction(path)) context.notify?.("Custom board locations refreshed."); }
    else reportError(path);
  });
  listen($("analyze"), "click", async () => {
    const path = context.workspace.path, pending = session.analyze();
    if (state.busy === "analyze") context.reveal?.($("job"));
    if (await pending && validAction(path) && state.job) watchedJob = {id: state.job.id, workspace: path};
    reportError(path);
  });
  listen($("cancel"), "click", async () => { const path = state.workspace; await session.cancel(); reportError(path); });
  session = createCustomBoardsSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  render(); session.load();
  return {navigate() { session.tick(); }, dispose() { disposed = true; lifetime.abort(); session.dispose(); reviewDialog.close({restoreFocus: false}); }};
}
