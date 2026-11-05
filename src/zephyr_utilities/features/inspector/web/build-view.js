import {buildTextKinds, canOpenDeviceTree, canOpenMemory, canReadBuildArtifact, createBuildSession, selectedBuildDomain} from "./build-state.js";
import {createActionFeedback} from "./action-feedback.js";

const array = (value) => Array.isArray(value) ? value : [];
const labels = {"resolved-dts": "Resolved Device Tree", "preprocessed-dts": "Preprocessed Device Tree", kconfig: "Kconfig output", "cmake-cache": "CMake cache", "build-info": "Build information", elf: "ELF", map: "Linker map"};
const text = (value) => value === undefined || value === null || value === "" ? "Unknown" : String(value);

export function mountBuilds(root, context) {
  const $ = (name) => root.querySelector(`[data-build-${name}]`);
  const lifetime = new AbortController();
  const state = context.state.builds ||= {};
  let session;
  let disposed = false;
  let detailKey;
  let fileKey;
  let lastView;
  const feedback = createActionFeedback({workspace: () => context.workspace.path,
    reveal: context.reveal, notify: context.notify, isDisposed: () => disposed});
  const result = () => state.error ? $("error") : state.observation ? $("results") : null;
  const inspect = (action) => feedback.run(action, result, {pending: "Reading build files…",
    complete: () => state.error || "Build inspection updated."});
  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value);
    return item;
  }
  function fact(parent, label, value, path = false) {
    const item = node("div", "inspection-fact");
    item.append(node("dt", "", label), node("dd", path ? "inspection-path" : "", text(value)));
    parent.append(item);
  }
  function paths(parent, heading, values) {
    const entries = array(values);
    parent.append(node("h3", "", heading));
    if (!entries.length) { parent.append(node("p", "inspection-muted", "Not recorded in this build.")); return; }
    const list = node("ol", "inspection-source-list inspection-path");
    for (const path of entries) list.append(node("li", "", path));
    parent.append(list);
  }
  function render() {
    if (disposed) return;
    const observation = state.observation;
    const domain = selectedBuildDomain(state);
    const busy = Boolean(state.busy);
    $("workspace").textContent = state.workspace || context.workspace.path;
    if ($("path").value !== (state.path || "")) $("path").value = state.path || "";
    $("path").placeholder = `${state.workspace || context.workspace.path}/applications/my_app/build`;
    $("path").disabled = busy;
    $("open").disabled = busy || !state.path?.trim();
    $("open").textContent = state.busy === "open" ? "Reading build…" : "Open build";
    $("error").hidden = !state.error; $("error").textContent = state.error || "";
    $("results").hidden = !observation;
    $("empty").hidden = Boolean(observation) || busy;
    $("progress").hidden = !busy;
    $("progress").textContent = state.busy === "check" ? "Checking the observed build files…" : "Reading existing build files…";
    if (!observation) return;
    const key = `${observation.id}:${state.domain}`;
    if (key !== detailKey) {
      detailKey = key;
      for (const detail of root.querySelectorAll("[data-build-results] details")) detail.open = false;
    }
    const select = $("domain");
    select.replaceChildren();
    for (const image of array(observation.domains)) {
      const option = node("option", "", image.name); option.value = image.name; select.append(option);
    }
    select.value = state.domain || ""; select.disabled = busy || !array(observation.domains).length;
    $("check").disabled = busy;
    $("reinspect").disabled = busy;
    $("tree").disabled = !canOpenDeviceTree(state);
    $("memory").disabled = !canOpenMemory(state);
    const freshness = domain?.freshness;
    $("freshness").textContent = !state.verified ? "This observation needs a fresh check before opening its files." : freshness === "unchanged" ? "The observed build files are unchanged since this inspection." : freshness === "changed" ? "Build files changed since this inspection. Re-inspect to read the current outputs." : "Some observed build files are unavailable. Restore them or re-inspect the directory.";
    $("identity").replaceChildren();
    fact($("identity"), "Effective target", domain?.context?.effective_target);
    fact($("identity"), "Application", domain?.context?.application_source_dir, true);
    fact($("identity"), "Build directory", domain?.build_dir || observation.root_build_dir, true);
    fact($("identity"), "Image / domain", domain?.name);
    const metadata = $("metadata"); metadata.replaceChildren();
    fact(metadata, "Zephyr checkout", domain?.context?.zephyr_base, true);
    fact(metadata, "Zephyr version", domain?.context?.zephyr_version);
    fact(metadata, "Toolchain", domain?.context?.toolchain);
    fact(metadata, "SDK installation", domain?.context?.sdk_path, true);
    fact(metadata, "Compiler", domain?.context?.compiler, true);
    fact(metadata, "Cached board", domain?.context?.cached_board);
    fact(metadata, "Observation", observation.id, true);
    fact(metadata, "Observed at", observation.created_at);
    const inputs = $("inputs"); inputs.replaceChildren();
    paths(inputs, "Device Tree inputs (recorded order)", domain?.context?.devicetree?.files);
    paths(inputs, "Explicit overlays", domain?.context?.devicetree?.user_files);
    paths(inputs, "Extra overlays", domain?.context?.devicetree?.extra_user_files);
    paths(inputs, "Configuration inputs", domain?.context?.configuration?.files);
    paths(inputs, "Explicit configuration fragments", domain?.context?.configuration?.user_files);
    paths(inputs, "Binding directories", domain?.context?.devicetree?.binding_dirs);
    paths(inputs, "Include directories", domain?.context?.devicetree?.include_dirs);
    const artifacts = $("artifacts"); artifacts.replaceChildren();
    if (!array(domain?.artifacts).length) artifacts.append(node("p", "inspection-muted", "No supported generated files were found for this image."));
    for (const artifact of array(domain?.artifacts)) {
      const row = node("div", "inspection-build-artifact");
      const info = node("div");
      info.append(node("strong", "", labels[artifact.kind] || artifact.kind), node("p", "inspection-path", artifact.path), node("p", "inspection-muted", `${text(artifact.availability)} · ${artifact.size ?? "Unknown"} bytes`));
      const hash = node("details", "inspection-technical-details");
      hash.append(node("summary", "", "Recorded SHA-256"), node("p", "inspection-path", artifact.sha256 || "Not recorded"));
      info.append(hash); row.append(info);
      if (buildTextKinds.has(artifact.kind)) {
        const button = node("button", "inspection-button", "View file");
        button.type = "button"; button.dataset.buildFile = artifact.kind;
        button.setAttribute("aria-label", `View ${labels[artifact.kind] || artifact.kind}`);
        button.disabled = !canReadBuildArtifact(state, artifact.kind) || Boolean(state.fileLoading);
        row.append(button);
      }
      artifacts.append(row);
    }
    const file = state.file;
    $("file-panel").hidden = !file && !state.fileLoading;
    const nextFileKey = file || state.fileLoading ? `${key}:${file?.kind || state.fileLoading}` : null;
    if (nextFileKey && nextFileKey !== fileKey) $("file-panel").open = true;
    fileKey = nextFileKey;
    $("file-title").textContent = state.fileLoading ? `Reading ${labels[state.fileLoading] || state.fileLoading}…` : labels[file?.kind] || "File preview";
    $("file-path").textContent = file?.path || "";
    $("file-text").textContent = file?.text || "";
    const diagnostics = $("diagnostics"); diagnostics.replaceChildren();
    for (const item of [...array(observation.diagnostics), ...array(domain?.diagnostics)]) diagnostics.append(node("p", "inspection-banner", item.message || item));
    $("diagnostics").hidden = !diagnostics.childNodes.length;
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  listen($("path"), "input", () => { feedback.invalidate(); session.changePath($("path").value); });
  listen($("form"), "submit", (event) => { event.preventDefault(); inspect(() => session.open()); });
  listen($("domain"), "change", () => { feedback.invalidate(); session.selectDomain($("domain").value); });
  listen($("check"), "click", () => inspect(() => session.check()));
  listen($("reinspect"), "click", () => inspect(() => session.open()));
  listen($("tree"), "click", () => session.openDeviceTree());
  listen($("memory"), "click", () => session.openMemory());
  listen($("artifacts"), "click", (event) => {
    const button = event.target.closest("[data-build-file]");
    if (button && root.contains(button)) {
      const kind = button.dataset.buildFile;
      feedback.run(() => session.readText(kind), () => state.error ? $("error") : state.file?.kind === kind ? $("file-panel") : null,
        {pending: `Reading ${labels[kind] || kind}…`, complete: () => state.error || "File preview ready."});
    }
  });
  session = createBuildSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render, navigate: context.navigate});
  render();
  return {
    navigate(view) {
      const requested = view?.startsWith("builds/");
      const explicit = (!state.workspace || state.workspace === context.workspace.path) && requested &&
        (lastView !== undefined ? lastView !== view : !state.observation);
      lastView = view; feedback.invalidate();
      return explicit ? inspect(() => session.navigate(view)) : session.navigate(view);
    },
    dispose() { disposed = true; feedback.invalidate(); lifetime.abort(); session.dispose(); },
  };
}
