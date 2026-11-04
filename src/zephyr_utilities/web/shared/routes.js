/** Keep URL compatibility here; feature modules never own the browser history. */
const memoryViews = new Set(["overview", "explorer", "compare", "snapshots"]);

export function parseRoute(hash = "") {
  const path = String(hash).replace(/^\/?#\/?/, "").replace(/^\/+|\/+$/g, "");
  if (!path || path === "app-main") return {feature: "home", view: "", path: "home"};
  if (memoryViews.has(path)) return {feature: "memory", view: path, path: `memory/${path}`};
  const [feature, ...parts] = path.split("/");
  if (feature === "home") return {feature, view: "", path: "home"};
  const view = feature === "memory" ? (memoryViews.has(parts[0]) ? parts[0] : "overview") : parts.join("/");
  return {feature, view, path: [feature, view].filter(Boolean).join("/")};
}

export function routeHash(path) {
  return `#/${parseRoute(path).path}`;
}

export function activeJobRoute(jobs, features) {
  for (const job of jobs) {
    const feature = features.find((item) => item.id === (job.feature || job.feature_id) && item.status === "available");
    if (feature) {
      const view = job.metadata?.view;
      if (typeof view === "string" && /^[a-z0-9_-]+(?:\/[a-z0-9_-]+)*$/.test(view)) return `${feature.id}/${view}`;
      return feature.entry_path || feature.id;
    }
  }
  return "home";
}

/** An explicit asset allowlist prevents a registry descriptor loading remote code. */
export function featureAssets(feature) {
  if (!feature || feature.status !== "available" || !feature.frontend) throw new Error("This tool is not available yet.");
  if (!/^[a-z][a-z0-9_-]*$/.test(feature.id)) throw new Error("Invalid tool identifier.");
  const prefix = `/assets/${feature.id}/`;
  const assets = {};
  for (const kind of ["module", "template", "stylesheet"]) {
    const path = feature.frontend[kind];
    if (typeof path !== "string" || !path.startsWith(prefix) || path.includes("..") || /[?#\\%]/.test(path)) {
      throw new Error(`Invalid local ${kind} asset for ${feature.id}.`);
    }
    assets[kind] = path;
  }
  return assets;
}
