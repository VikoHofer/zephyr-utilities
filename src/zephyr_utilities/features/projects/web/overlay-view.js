/** Present saved overlay evidence without treating copied files as active inputs. */
const states = new Set(["active", "expected", "not_used", "unknown"]);
const roles = new Set(["application", "board_specific", "preserved", "default"]);
const statusLabels = {active: "Used", expected: "Expected", not_used: "Not used", unknown: "Unknown"};
const roleLabels = {application: "Application overlay", board_specific: "Board-specific overlay", preserved: "Preserved original", default: "Default overlay", board_source: "Board source", external_overlay: "External overlay"};

export function selectedOverlayUsage(project, configurationId = "") {
  const reports = project?.overlay_usage_by_configuration;
  return reports && Object.hasOwn(reports, configurationId || "") ? reports[configurationId || ""] : null;
}

export function overlayUsagePresentation(report, {target, freshness} = {}) {
  const validFile = (file) => file && typeof file.path === "string" && file.path && typeof file.reason === "string" && states.has(file.state) && roles.has(file.role);
  const validBoard = (file) => file && typeof file.path === "string" && file.path && typeof file.reason === "string" && file.state === "active" && ["board_source", "external_overlay"].includes(file.role);
  if (!report || report.schema_version !== 1 || typeof report.target !== "string" || !["expected", "confirmed", "unknown"].includes(report.status)
    || target && report.target !== target || typeof report.message !== "string" || !Array.isArray(report.files) || !report.files.every(validFile)
    || !Array.isArray(report.board_files) || !report.board_files.every(validBoard)) {
    return {status: "unknown", title: "Overlay usage unknown", target: target || "", message: "No matching overlay evidence is available for this target.",
      note: "Run Configure to record the files actually used.", main: [], other: [], boards: [], evidence: null};
  }
  const evidence = report.evidence && typeof report.evidence === "object" ? report.evidence : null;
  const convert = (file) => {
    const state = file.state === "active" && report.status !== "confirmed" ? report.status === "expected" ? "expected" : "unknown" : file.state;
    return {...file, state, stateLabel: statusLabels[state], roleLabel: roleLabels[file.role]};
  };
  const files = report.files.map(convert);
  const boardFiles = report.board_files.map(convert);
  const primary = (file) => ["active", "expected"].includes(file.state) || file.role === "default";
  const main = files.filter(primary).concat(boardFiles.filter((file) => file.role === "external_overlay"));
  let title = report.status === "expected" ? "Expected overlays" : "Overlay usage unknown";
  let note = report.status === "expected" ? "Expected from this profile and file list. Configure records the actual inputs." : "Available overlay files are not proof of use. Run Configure to record the actual inputs.";
  if (report.status === "confirmed") {
    title = evidence?.kind === "compatibility-check" ? "Overlays used in compatibility check" : evidence?.kind === "attempt" ? "Overlays used in this attempt" : "Recorded overlay usage";
    note = "Captured for this operation. Configure again after editing inputs; these are not live file checks.";
  }
  if (typeof evidence?.note === "string" && evidence.note.trim()) note = evidence.note.trim();
  if (freshness === "stale") note = `Application inputs changed during this attempt. ${note}`;
  return {status: report.status, title, target: report.target, message: report.message, note, main,
    other: files.filter((file) => !primary(file)), boards: boardFiles.filter((file) => file.role === "board_source"), evidence};
}

export function createOverlayView({node}) {
  function list(rows) {
    const result = node("ul", "projects-overlay-files");
    for (const file of rows) {
      const item = node("li");
      const heading = node("div", "projects-overlay-file-heading");
      heading.append(node("span", "projects-path", file.path), node("span", "projects-overlay-state", `${file.stateLabel} · ${file.roleLabel}`));
      item.append(heading);
      if (file.reason) item.append(node("p", "projects-muted", file.reason));
      result.append(item);
    }
    return result;
  }
  function render(target, report, options = {}) {
    const signature = JSON.stringify([report, options]);
    if (target.dataset.overlayValue === signature) return;
    const expanded = new Set([...target.querySelectorAll("details[open][data-overlay-group]")].map((item) => item.dataset.overlayGroup));
    target.dataset.overlayValue = signature;
    target.classList.add("projects-overlay-usage");
    target.replaceChildren();
    const data = overlayUsagePresentation(report, options);
    target.dataset.overlayStatus = data.status;
    const heading = node("div", "projects-overlay-heading");
    heading.append(node("h4", "", data.title));
    if (data.target) heading.append(node("span", "projects-path", data.target));
    target.append(heading, node("p", "projects-muted", data.message));
    if (data.main.length) target.append(list(data.main));
    else if (data.status === "confirmed") target.append(node("p", "projects-muted", "No application overlays were recorded for this operation."));
    if (data.evidence?.checked_at) {
      const date = Date.parse(data.evidence.checked_at);
      const label = Number.isFinite(date) ? new Date(date).toLocaleString() : "Unknown time";
      target.append(node("p", "projects-muted", `Recorded ${label}${data.evidence.configuration_name ? ` · ${data.evidence.configuration_name}` : ""}`));
    }
    target.append(node("p", "projects-overlay-note", data.note));
    for (const [kind, title, rows] of [["other", "Other overlay files", data.other], ["boards", "Board source files", data.boards]]) {
      if (!rows.length) continue;
      const detail = node("details"); detail.dataset.overlayGroup = kind;
      if (options.disclosureKey) detail.dataset.disclosure = `${options.disclosureKey}:overlays:${kind}`;
      detail.open = expanded.has(kind);
      detail.append(node("summary", "", `${title} (${rows.length})`));
      if (kind === "other") detail.append(node("p", "projects-muted", "Copied or available files are not automatically selected for this target."));
      detail.append(list(rows));
      target.append(detail);
    }
  }
  return {render};
}
