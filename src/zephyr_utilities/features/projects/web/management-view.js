import {creatorBoards, creatorSdks} from "./creator-state.js";
import {createOverlayView} from "./overlay-view.js";
import {createReviewDialog} from "./review-dialog.js";

const array = (value) => Array.isArray(value) ? value : [];
const labels = {
  import: ["Import existing application", "Import application", "Review import"],
  reconnect: ["Reconnect application folder", "Reconnect folder", "Review connection"],
  configuration: ["Review build configuration", "Apply configuration", "Review changes"],
  clean: ["Clean managed build output", "Delete reviewed output files", "Review cleanup"],
};
export function managementSummary(kind, plan) {
  if (kind === "clean") return [["Output directory", plan.build_dir], ["Files", plan.file_count ?? "Unknown"], ["Directories", plan.directory_count ?? "Unknown"],
    ["Bytes on disk", plan.bytes ?? "Unknown"], ["Related attempts", array(plan.shared_attempt_ids).length]];
  if (kind === "reconnect") return [["Previous folder", plan.old_path], ["New folder", plan.path],
    ["Directory identity", plan.same_directory === true ? "Same directory / moved original" : plan.same_directory === false ? "Different directory" : "Unknown"],
    ...["added", "removed", "changed"].map((kind) => [`Files ${kind}`, Array.isArray(plan.file_changes?.[kind]) ? plan.file_changes[kind].length : "Unknown"])];
  if (kind === "import") return [["Existing application", plan.path], ["Saved name", plan.name || plan.project?.name], ["Source files inspected", array(plan.sources?.files).length],
    ["Target", plan.profile?.target], ["SDK", plan.profile?.sdk?.path]];
  return [["Configuration", plan.configuration?.name], ["Change", {create: "Add configuration", update: "Update saved configuration", original: "Update Original"}[plan.mode] || plan.mode],
    ["Target", plan.profile?.target], ["SDK", plan.profile?.sdk?.path], ["Application files to change", array(plan.files).length]];
}

export function createManagementView({root, context, state, session, node, fact, choices, onApplied}) {
  const $ = (name) => root.querySelector(`[data-management-${name}]`);
  const dialog = $("dialog");
  const review = createReviewDialog(dialog);
  const lifetime = new AbortController();
  const overlays = createOverlayView({node});
  let owner;
  let trigger;
  let renderedPlan;
  let choicesKey;
  let serial = 0;
  let disposed = false;
  const inputs = {};
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  function buildForm(current) {
    $("form").replaceChildren();
    for (const key of Object.keys(inputs)) delete inputs[key];
    const fields = current.kind === "import" ? [["path", "Existing application folder", "input"], ["name", "Saved application name", "input"],
      ["search", "Find a board", "input"], ["board_id", "Board", "select"], ["target", "Exact target", "select"], ["sdk_id", "Installed GNU SDK", "select"]]
      : current.kind === "reconnect" ? [["path", "Application's current folder", "input"]] : [];
    const grid = node("div", "projects-option-grid");
    for (const [key, title, tag] of fields) {
      const label = node("label", ["path", "name", "search", "sdk_id"].includes(key) ? "projects-full-width" : "", title);
      const field = node(tag); field.dataset.managementField = key;
      field.required = key !== "search";
      if (tag === "input") { field.autocomplete = "off"; field.spellcheck = false; }
      if (key === "search") field.type = "search";
      if (key === "path") field.placeholder = "Path inside the selected workspace";
      label.append(field); grid.append(label); inputs[key] = field;
    }
    $("form").append(grid);
    $("form").hidden = fields.length === 0;
    $("intro").textContent = current.kind === "import" ? "Register an existing application without changing its source files. Choose the target and SDK to use for future reviewed builds."
      : current.kind === "reconnect" ? "Update the saved folder location while preserving this application's configurations and attempt history. Source files are not moved or changed."
      : current.kind === "configuration" ? "Review the target, SDK and file changes. Application files change only when they are listed below."
      : "Review exactly which managed output directory will be deleted. Source files, configurations and attempt history remain.";
  }
  function renderPlan(current) {
    const plan = current.plan;
    $("review").hidden = !plan;
    if (plan === renderedPlan) return;
    renderedPlan = plan;
    $("review").replaceChildren();
    if (!plan) return;
    const summary = node("dl", "projects-identity");
    for (const [label, value] of managementSummary(current.kind, plan)) fact(summary, label, value, typeof value === "string" && (label.includes("folder") || label.includes("directory") || label === "SDK"));
    $("review").append(summary);
    if (plan.overlay_usage) { const target = node("div"); overlays.render(target, plan.overlay_usage, {target: plan.profile?.target}); $("review").append(target); }
    for (const warning of array(plan.warnings)) $("review").append(node("p", "projects-banner", typeof warning === "string" ? warning : warning.message));
    if (current.kind === "configuration") {
      const files = array(plan.files);
      $("review").append(node("p", "projects-management-effect", files.length ? `${files.length} application file${files.length === 1 ? "" : "s"} will be written after Apply.` : "This changes saved configuration metadata only. No application files will be written."));
      for (const file of files) {
        const details = node("details", "projects-preview-file");
        details.append(node("summary", "projects-path", `${file.before === null ? "Create" : "Update"} ${file.path}`));
        if (file.before !== null && file.before !== undefined) details.append(node("h4", "", "Before"), node("pre", "", file.before));
        details.append(node("h4", "", "After"), node("pre", "", file.content)); $("review").append(details);
      }
    } else if (current.kind === "clean") {
      $("review").append(node("p", "projects-management-effect", "This deletes the reviewed output files from disk. Attempts sharing these outputs will be marked cleaned; their recorded logs and commands remain."));
      if (plan.symlink_count) $("review").append(node("p", "projects-muted", `${plan.symlink_count} symbolic link entries are included in the reviewed directory.`));
    } else {
      $("review").append(node("p", "projects-management-effect", "Only the saved application record changes. No source files are written, moved or deleted."));
      if (current.kind === "reconnect") {
        const changes = ["added", "removed", "changed"].flatMap((kind) => array(plan.file_changes?.[kind]).map((path) => [kind, path]));
        if (changes.length) {
          const details = node("details", "projects-preview-file");
          details.append(node("summary", "", `Source differences from the saved observation (${changes.length})`));
          const list = node("ul");
          for (const [kind, path] of changes) list.append(node("li", "projects-path", `${kind[0].toUpperCase()}${kind.slice(1)}: ${path}`));
          details.append(list); $("review").append(details);
        }
      }
    }
  }
  function render() {
    if (disposed) return;
    const current = state.management;
    if (!current || current.workspace !== context.workspace.path) {
      review.close(); owner = null; renderedPlan = null; return;
    }
    if (current !== owner) {
      owner = current; renderedPlan = null; choicesKey = null;
      buildForm(current);
      $("title").textContent = labels[current.kind][0];
      review.show({id: String(++serial)}, trigger);
    }
    const busy = Boolean(state.busy || state.loading);
    const key = JSON.stringify([state.snapshot?.fingerprint, current.fields.search, current.fields.board_id]);
    if (current.kind === "import" && choicesKey !== key) {
      choicesKey = key;
      const board = creatorBoards(state.snapshot).find((item) => item.id === current.fields.board_id);
      const boards = creatorBoards(state.snapshot, current.fields.search);
      if (board && !boards.some((item) => item.id === board.id)) boards.unshift(board);
      choices(inputs.board_id, boards.map((item) => [item.id, item.full_name || item.name]), "Choose a board");
      choices(inputs.target, array(board?.targets).map((item) => [item, item]), "Choose an exact target");
      choices(inputs.sdk_id, creatorSdks(state.snapshot).map((item) => [item.id, `SDK ${item.version || "unknown"} · ${item.path}`]), "Choose an SDK");
    }
    for (const [key, input] of Object.entries(inputs)) { if (input.value !== current.fields[key]) input.value = current.fields[key] || ""; input.disabled = busy; }
    $("error").hidden = !state.error; $("error").textContent = state.error || "";
    $("pending").hidden = !busy; $("pending").textContent = state.busy === "management-apply" ? "Applying the reviewed change…" : "Preparing the operation review…";
    $("preview").hidden = Boolean(current.plan); $("preview").textContent = labels[current.kind][2];
    $("preview").disabled = busy || Object.values(inputs).some((input) => input.required && !input.value.trim());
    $("apply").hidden = !current.plan; $("apply").disabled = busy || !current.plan; $("apply").textContent = labels[current.kind][1];
    $("form").hidden = Boolean(current.plan) || Object.keys(inputs).length === 0;
    $("edit").hidden = !current.plan || !["import", "reconnect"].includes(current.kind); $("edit").disabled = busy;
    for (const button of root.querySelectorAll("[data-management-close]")) button.disabled = state.busy === "management-apply";
    renderPlan(current);
  }
  async function preview() {
    const current = state.management;
    if (await session().management.preview() && !disposed && current === state.management) {
      dialog.querySelector(".projects-review-body").scrollTop = 0;
      $("title").focus({preventScroll: true});
    }
  }
  listen($("form"), "submit", (event) => { event.preventDefault(); preview(); });
  listen($("preview"), "click", preview);
  listen($("edit"), "click", () => {
    session().management.edit();
    dialog.querySelector(".projects-review-body").scrollTop = 0;
    (inputs.path || Object.values(inputs)[0])?.focus({preventScroll: true});
  });
  listen($("apply"), "click", async () => {
    const current = state.management;
    const workspace = context.workspace.path;
    if (await session().management.apply() && !disposed && workspace === context.workspace.path) {
      context.notify?.({import: "Application imported.", reconnect: "Application folder reconnected.", configuration: "Build configuration updated.", clean: "Output cleanup started."}[current.kind]);
      onApplied?.(current.kind, current.fields);
    }
  });
  for (const button of root.querySelectorAll("[data-management-close]")) listen(button, "click", () => session().management.close());
  listen(dialog, "cancel", (event) => { event.preventDefault(); if (state.busy !== "management-apply") session().management.close(); });
  listen($("form"), "input", (event) => { if (event.target.matches("input[data-management-field]")) session().management.change(event.target.dataset.managementField, event.target.value); });
  listen($("form"), "change", (event) => { if (event.target.matches("select[data-management-field]")) session().management.change(event.target.dataset.managementField, event.target.value); });
  return {render,
    open(kind, fields, options, restoreFocus) { trigger = restoreFocus; if (session().management.open(kind, fields, options) && kind === "clean") preview(); },
    previewConfiguration(projectId, restoreFocus) { trigger = restoreFocus; return session().previewConfiguration(projectId); },
    dispose() { disposed = true; lifetime.abort(); review.close({restoreFocus: false}); },
  };
}
