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
  return "";
}
export const sampleLabel = (sample) => `${sample?.title || sample?.id || "Unknown sample"} · local Zephyr sample`;
