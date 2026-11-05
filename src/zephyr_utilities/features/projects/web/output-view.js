/** Output ownership outlives attempt history and is supplied by the backend. */
const array = (value) => Array.isArray(value) ? value : [];
export function outputActions(output) {
  return {clean: output?.clean_allowed === true, reuse: output?.reuse_allowed === true && typeof output.latest_attempt_id === "string" && Boolean(output.latest_attempt_id)};
}
export function createOutputView({node}) {
  function create(project) {
    const outputs = array(project.build_outputs);
    if (!outputs.length) return null;
    const detail = node("details", "projects-managed-outputs");
    detail.dataset.projectOutputs = project.id; detail.dataset.disclosure = `project:${project.id}:outputs`;
    detail.append(node("summary", "", `Managed build outputs (${outputs.length})`), node("p", "projects-muted", "Output directories stay manageable after removing attempt history. Cleanup deletes reviewed output files and keeps the remaining history."));
    for (const output of outputs) {
      const row = node("article", "projects-output-record"); row.dataset.outputRecord = output.id;
      row.append(node("p", "projects-path", output.path), node("p", "projects-muted", `${output.state || "Unknown"}${output.reason ? ` · ${output.reason}` : ""}`));
      const allowed = outputActions(output);
      const actions = node("div", "projects-actions");
      if (allowed.reuse) for (const [operation, label] of [["configure", "Configure again here"], ["build", "Build again here"]]) {
        const button = node("button", "projects-button", label); button.type = "button";
        button.dataset.reuseOperation = operation; button.dataset.projectId = project.id; button.dataset.attemptId = output.latest_attempt_id; actions.append(button);
      }
      if (allowed.clean) {
        const clean = node("button", "projects-button", "Review cleanup"); clean.type = "button";
        clean.dataset.outputClean = output.id; clean.dataset.projectId = project.id; actions.append(clean);
      }
      if (actions.children.length) row.append(actions);
      detail.append(row);
    }
    return detail;
  }
  return {create};
}
