/** Ordered application fragment choices; absence preserves Zephyr's automatic rules. */
export const fragmentKinds = ["kconfig", "overlays"];
export const fragmentModes = ["auto", "append", "replace"];
export const fragmentTitles = {kconfig: "Kconfig fragments", overlays: "Device Tree overlays"};
export const automaticFragments = () => ({schema_version: 1, kconfig: {mode: "auto", files: []}, overlays: {mode: "auto", files: []}});
export const copyFragments = (value) => value === undefined ? automaticFragments() : structuredClone(value);
export function profileFragments(profile) {
  if (profile?.schema_version !== undefined && ![1, 2].includes(profile.schema_version) ||
      profile?.schema_version === 2 && profile.fragments === undefined ||
      profile?.fragments !== undefined && profile.schema_version !== 2) return {schema_version: "unsupported"};
  return profile?.fragments;
}

export function fragmentFormatError(value) {
  if (!value || value.schema_version !== 1 || Object.keys(value).some((key) => !["schema_version", ...fragmentKinds].includes(key))) return "This fragment selection format is unsupported. Choose another configuration.";
  for (const kind of fragmentKinds) {
    const part = value[kind];
    if (!part || !fragmentModes.includes(part.mode) || !Array.isArray(part.files) || part.files.length > 32 ||
        part.files.some((path) => typeof path !== "string" || !path) || new Set(part.files).size !== part.files.length ||
        Object.keys(part).some((key) => !["mode", "files"].includes(key)) || part.mode === "auto" && part.files.length) return `The saved ${fragmentTitles[kind].toLowerCase()} selection is unsupported. Choose another configuration.`;
  }
  return "";
}

export function needsFragmentFiles(value) {
  return !fragmentFormatError(value) && fragmentKinds.some((kind) => value[kind].mode !== "auto");
}

export function fragmentSelectionError(value, catalogue) {
  const formatError = fragmentFormatError(value);
  if (formatError) return formatError;
  if (catalogue?.loading) return "Wait for the application file list to finish loading.";
  if (!needsFragmentFiles(value)) return "";
  if (catalogue?.error) return catalogue.error;
  if (!catalogue?.ready) return "Refresh the application file list before saving selected fragments.";
  for (const kind of fragmentKinds) {
    const part = value[kind];
    if (part.mode === "auto") continue;
    if (!part.files.length) return `Choose at least one file for ${fragmentTitles[kind].toLowerCase()}, or use Automatic.`;
    if (part.files.some((path) => !catalogue.files[kind].includes(path))) return "A selected fragment is no longer available. Remove it or restore the file, then refresh the list.";
  }
  return "";
}

export function fragmentSummary(value, kind) {
  const selected = value === undefined ? automaticFragments() : value;
  if (fragmentFormatError(selected)) return "Unsupported fragment selection";
  const part = selected[kind];
  if (part.mode === "auto") return "Automatic application selection";
  const description = part.mode === "append" ? "Append after automatic selection" : "Replace automatic application selection";
  return `${description}: ${part.files.map((path, index) => `${index + 1}. ${path}`).join(" → ")}`;
}
