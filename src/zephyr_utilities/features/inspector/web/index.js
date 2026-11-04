import {mountBuilds} from "./build-view.js";
import {mount as mountTree} from "./tree-view.js";

/** Each Inspector view owns its requests; switching views disposes the old reader. */
export function mount(host, context) {
  const root = host.querySelector("[data-inspector-root]");
  const builds = root.querySelector("[data-builds-view]");
  const tree = root.querySelector("[data-tree-view]");
  let current;
  let instance;
  let disposed = false;
  context.state.tree ||= {};
  return {
    navigate(view = "builds") {
      if (disposed) return;
      const next = !view || view === "builds" || view.startsWith("builds/") ? "builds" : "sources";
      if (next !== current) {
        instance?.dispose();
        current = next;
        builds.hidden = next !== "builds";
        tree.hidden = next !== "sources";
        instance = next === "builds" ? mountBuilds(builds, context)
          : mountTree(tree, {...context, state: context.state.tree});
      }
      return instance.navigate(next === "builds" ? view || "builds" : view === "sources" ? "" : view);
    },
    dispose() { disposed = true; instance?.dispose(); },
  };
}
