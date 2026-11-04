import {mountEnvironment} from "./environment-view.js";
import {mountCreator} from "./creator-view.js";

export function mount(host, context) {
  const root = host.querySelector("[data-projects-root]");
  const environment = root.querySelector("[data-environment-view]");
  const creator = root.querySelector("[data-creator-view]");
  let current;
  let instance;
  let disposed = false;
  return {
    navigate(view) {
      if (disposed) return;
      const next = view === "create" ? "create" : "environment";
      if (next !== current) {
        instance?.dispose();
        current = next;
        environment.hidden = next !== "environment";
        creator.hidden = next !== "create";
        for (const link of root.querySelectorAll("[data-projects-tab]")) {
          if (link.dataset.projectsTab === next) link.setAttribute("aria-current", "page");
          else link.removeAttribute("aria-current");
        }
        instance = next === "create" ? mountCreator(creator, context) : mountEnvironment(environment, context);
      }
      instance.navigate(next);
    },
    dispose() { disposed = true; instance?.dispose(); },
  };
}
