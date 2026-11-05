import {mountEnvironment} from "./environment-view.js";
import {mountCreator} from "./creator-view.js";
import {mountCustomBoards} from "./custom-boards-view.js";

export function createProjectsNavigation({environment, creator, customBoards, openEnvironment, openCreator, openCustomBoards}) {
  let current;
  let instance;
  let disposed = false;
  return {
    navigate(view = "") {
      if (disposed) return;
      const next = view === "custom-boards" && customBoards ? "custom-boards" : view === "environment" ? "environment" : view === "create" ? "create" : "applications";
      const owner = next === "environment" ? "environment" : next === "custom-boards" ? "custom-boards" : "creator";
      if (owner !== current) {
        instance?.dispose();
        current = owner;
        environment.hidden = next !== "environment";
        creator.hidden = owner !== "creator";
        if (customBoards) customBoards.hidden = owner !== "custom-boards";
        instance = owner === "environment" ? openEnvironment() : owner === "custom-boards" ? openCustomBoards() : openCreator();
      }
      instance.navigate(next === "applications" && view.startsWith("applications/") ? view : next);
    },
    dispose() { if (!disposed) { disposed = true; instance?.dispose(); } },
  };
}

export function mount(host, context) {
  const root = host.querySelector("[data-projects-root]");
  const environment = root.querySelector("[data-environment-view]");
  const creator = root.querySelector("[data-creator-view]");
  const customBoards = root.querySelector("[data-custom-boards-view]");
  return createProjectsNavigation({environment, creator, customBoards,
    openEnvironment: () => mountEnvironment(environment, context),
    openCreator: () => mountCreator(creator, context),
    openCustomBoards: () => mountCustomBoards(customBoards, context)});
}
