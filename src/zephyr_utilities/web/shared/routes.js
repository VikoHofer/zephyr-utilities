/** Keep URL compatibility here; feature modules never own the browser history. */
const memoryViews = new Set(["overview", "explorer", "compare", "snapshots"]);

export function parseRoute(hash = "") {
  const path = String(hash).replace(/^\/?#\/?/, "").replace(/^\/+|\/+$/g, "");
  if (!path || path === "app-main") return {feature: "home", view: "", path: "home"};
  if (memoryViews.has(path)) return {feature: "memory", view: path, path: `memory/${path}`};
  let [feature, ...parts] = path.split("/");
  if ((feature === "projects" && parts[0] === "builds") ||
      (feature === "devicetree" && ["inspect", "application"].includes(parts[0]))) feature = "inspector";
  if (feature === "home") return {feature, view: "", path: "home"};
  const view = feature === "memory" && parts[0] !== "inspect" ? (memoryViews.has(parts[0]) ? parts[0] : "overview") : parts.join("/");
  return {feature, view, path: [feature, view].filter(Boolean).join("/")};
}

export function routeHash(path) {
  return `#/${parseRoute(path).path}`;
}

/** Optional feature-owned views let the shell guide navigation without mounting tools. */
export function navigationEntries(feature) {
  if (feature?.status !== "available" || !/^[a-z][a-z0-9_-]*$/.test(feature.id)) return [];
  const local = (view) => typeof view === "string" && /^[a-z0-9_-]+(?:\/[a-z0-9_-]+)*$/.test(view);
  return (Array.isArray(feature.navigation) ? feature.navigation : []).filter((entry) =>
    typeof entry?.label === "string" && entry.label.trim() && local(entry.view)
  ).map((entry) => ({...entry, path: `${feature.id}/${entry.view}`,
    match_views: [...new Set([entry.view, ...(Array.isArray(entry.match_views) ? entry.match_views.filter(local) : [])])]}));
}

export function activeNavigationEntry(feature, route) {
  if (feature?.id !== route.feature) return null;
  const view = route.view || parseRoute(feature.entry_path || feature.id).view;
  let selected = null;
  let matched = -1;
  for (const entry of navigationEntries(feature)) {
    for (const prefix of entry.match_views) {
      if ((view === prefix || view.startsWith(`${prefix}/`)) && prefix.length > matched) {
        selected = entry; matched = prefix.length;
      }
    }
  }
  return selected;
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
