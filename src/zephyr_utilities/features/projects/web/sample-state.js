/** Local sample choices are observations of one selected checkout. */
const sampleIds = new Set(["hello_world", "basic/blinky", "basic/button"]);
const strings = (value) => Array.isArray(value) && value.every((item) => typeof item === "string");
export const isLocalSample = (form) => form?.template_id === "local-sample";
export function validateSampleCatalogue(data, workspace, zephyrPath) {
  if (data?.workspace !== workspace || !Array.isArray(data.samples) || data.samples.length > sampleIds.size
    || new Set(data.samples.map((item) => item?.id)).size !== data.samples.length
    || !strings(data.diagnostics) || !data.zephyr || typeof data.zephyr.path !== "string"
    || zephyrPath !== undefined && data.zephyr.path !== zephyrPath
    || !data.samples.every((item) => item && sampleIds.has(item.id) && item.path === `samples/${item.id}`
      && typeof item.title === "string" && item.title.trim() && typeof item.description === "string"
      && ["available", "unsupported", "missing"].includes(item.status) && strings(item.requirements) && strings(item.diagnostics)
      && (item.status !== "available" || typeof item.fingerprint === "string" && /^[a-f0-9]{64}$/.test(item.fingerprint))
      && Number.isInteger(item.file_count) && item.file_count >= 0)) {
    throw new Error("The local sample list could not be verified. Refresh samples again.");
  }
  return data;
}
export const selectedSample = (state) => state.sampleCatalogue?.samples?.find((item) => item.id === state.form?.sample_id) || null;
export function readySample(state) {
  const sample = selectedSample(state);
  return state.sampleCatalogue?.ready && !state.sampleCatalogue.loading && sample?.status === "available" ? sample : null;
}
export function sampleSelectionError(state) {
  if (!isLocalSample(state.form)) return "";
  if (state.sampleCatalogue?.loading) return "Wait for the local sample list before previewing.";
  if (!state.sampleCatalogue?.ready) return "Load the local samples before previewing. Use Refresh samples to try again.";
  if (!readySample(state)) return "Choose an available local sample before previewing.";
  if (currentSampleCompatibility(state)?.status === "incompatible") return "This sample is incompatible with the selected target. Choose another sample or target, then check compatibility again.";
  return "";
}
export const sampleLabel = (sample) => `${sample?.title || sample?.id || "Unknown sample"} · local Zephyr sample`;

const selectionFields = ["workspace", "sample_id", "sample_fingerprint", "board_id", "target", "sdk_id", "environment_fingerprint"];
export function sampleCheckSelection(state) {
  const sample = readySample(state);
  if (!isLocalSample(state.form) || !sample || !state.snapshot || state.snapshot.status === "stale") return null;
  const selection = {workspace: state.workspace, sample_id: sample.id, sample_fingerprint: sample.fingerprint,
    board_id: state.form.board_id, target: state.form.target, sdk_id: state.form.sdk_id, environment_fingerprint: state.snapshot.fingerprint};
  return selectionFields.every((key) => typeof selection[key] === "string" && selection[key]) ? selection : null;
}
export const sameSampleSelection = (left, right) => Boolean(left && right && selectionFields.every((key) => left[key] === right[key]));
export function validateSampleCheckResult(result, selection) {
  const compatibility = result?.compatibility;
  if (!sameSampleSelection(compatibility, selection) || !["compatible", "incompatible", "unknown"].includes(compatibility?.status)
    || typeof compatibility.message !== "string" || !compatibility.message.trim() || !strings(compatibility.details)
    || typeof result?.check_id !== "string" || !/^[a-f0-9]{32}$/.test(result.check_id)) {
    throw new Error("The compatibility result does not match this selection. Check compatibility again.");
  }
  const adaptation = compatibility.adaptation;
  if (adaptation !== undefined && (!adaptation || typeof adaptation.id !== "string" || !Number.isInteger(adaptation.version)
    || typeof adaptation.title !== "string" || typeof adaptation.message !== "string" || !Array.isArray(adaptation.files)
    || !adaptation.files.every((file) => file && typeof file.path === "string" && typeof file.sha256 === "string"))) {
    throw new Error("The sample adaptation could not be verified. Check compatibility again.");
  }
  return compatibility;
}
export function currentSampleCompatibility(state) {
  const check = state.sampleCheck;
  return check?.verified && !check.stale && sameSampleSelection(check.selection, sampleCheckSelection(state)) ? check.compatibility : null;
}
export function sampleCompatibilityNotice(state) {
  const check = state.sampleCheck;
  const current = currentSampleCompatibility(state);
  if (current) return {status: current.status, message: current.message, details: current.details, adaptation: current.adaptation};
  if (check?.error && !check.stale) return {status: "unknown", message: check.error, details: []};
  if (check?.stale) return {status: "unknown", message: "Selection changed. Compatibility has not been checked for this sample, target and SDK.", details: []};
  if (check?.submitting || ["queued", "running"].includes(check?.job?.status) || check?.needsVerify) return {status: "checking", message: "Checking sample compatibility with the selected target…", details: []};
  return {status: "unknown", message: sampleCheckSelection(state) ? "Compatibility has not been checked. This sample may need board support or an adaptation." : "Choose a sample, exact target and SDK to check compatibility.", details: []};
}
