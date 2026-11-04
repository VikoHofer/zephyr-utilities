import {mountEnvironment} from "./environment-view.js";
import {mountCreator} from "./creator-view.js";

export function createProjectsNavigation({environment, creator, openEnvironment, openCreator}) {
  let current;
  let instance;
  let disposed = false;
  return {
    navigate(view = "") {
      if (disposed) return;
      const next = view === "environment" ? "environment" : view === "create" ? "create" : "applications";
      const owner = next === "environment" ? "environment" : "creator";
      if (owner !== current) {
        instance?.dispose();
        current = owner;
        environment.hidden = next !== "environment";
        creator.hidden = next === "environment";
        instance = owner === "environment" ? openEnvironment() : openCreator();
      }
      instance.navigate(next);
    },
    dispose() { if (!disposed) { disposed = true; instance?.dispose(); } },
  };
}

export function mount(host, context) {
  const root = host.querySelector("[data-projects-root]");
  const environment = root.querySelector("[data-environment-view]");
  const creator = root.querySelector("[data-creator-view]");
  return createProjectsNavigation({environment, creator,
    openEnvironment: () => mountEnvironment(environment, context),
    openCreator: () => mountCreator(creator, context)});
}
