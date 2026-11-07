import {parseRoute, routeHash, featureAssets, activeJobRoute, navigationEntries, activeNavigationEntry} from "/assets/shell/shared/routes.js";
import {createApi} from "/assets/shell/shared/api.js";
import {revealResult} from "/assets/shell/shared/interaction.js";

const $ = (id) => document.getElementById(id);
const state = {session: null, workspace: {path: ""}, features: [], instances: new Map(), mounted: null, routeRevision: 0, routePath: null, resultRoute: null, folder: null, activeJobs: []};
const stylesheets = new Map();
const icons = {memory: "▥", devicetree: "⑂", projects: "＋", inspector: "⌕", tinyml: "◈"};
const toolDetails = {
  memory: ["RAM & ROM reports", "Symbol-level exploration", "Snapshot comparisons"],
  devicetree: ["Automatic chip and pin discovery", "Pin diagram and source inspector", "Reviewed Save, Undo and recovery"],
  projects: ["Workspace applications", "Create, configure and build", "Environment, board and SDK discovery"],
  inspector: ["Existing build outputs and image context", "Resolved nodes, sources and bindings", "Read-only advanced inspection"],
  tinyml: ["Model and experiment metadata", "Memory, latency and accuracy", "Reproducible framework comparisons"],
};
let api;
let noticeTimer;
let navigationFeature;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function notify(message, error = false) {
  clearTimeout(noticeTimer);
  $("app-notice-text").textContent = message;
  $("app-notice").classList.toggle("error", error);
  $("app-notice").hidden = false;
  if (!error) noticeTimer = setTimeout(() => { $("app-notice").hidden = true; }, 7000);
}

async function run(action) {
  try { await action(); } catch (error) { notify(error.message || String(error), true); }
}

function navigate(path) {
  const hash = routeHash(path);
  if (location.hash !== hash) location.hash = hash;
  else run(renderRoute);
}

function workspaceLabel(path) {
  return path.replaceAll("\\", "/").split("/").filter(Boolean).at(-1) || path || "Select a folder";
}

function renderWorkspace() {
  $("workspace-name").textContent = workspaceLabel(state.workspace.path);
  $("workspace-open").title = state.workspace.path;
  const path = $("home-workspace-path");
  if (path) path.textContent = state.workspace.path;
}

async function refreshWorkspace() {
  Object.assign(state.workspace, await api("/api/workspace"));
  renderWorkspace();
  return state.workspace;
}

function renderNavigation() {
  const nav = $("app-navigation");
  nav.replaceChildren();
  const advanced = node("details", "app-nav-advanced"); advanced.id = "app-nav-advanced";
  advanced.append(node("summary", "", "Advanced"));
  for (const feature of [{id: "home", title: "Home", entry_path: "home", status: "available"}, ...state.features]) {
    const group = node("div", "app-nav-feature"); group.dataset.feature = feature.id;
    const link = node("a", "app-nav");
    link.href = routeHash(feature.entry_path || feature.id);
    const icon = node("span", "app-nav-icon", icons[feature.id] || "◫"); icon.setAttribute("aria-hidden", "true");
    link.append(icon, node("span", "app-nav-label", feature.title));
    link.dataset.feature = feature.id;
    if (feature.status === "planned") link.append(node("small", "app-nav-status", "Planned"));
    group.append(link);
    const entries = navigationEntries(feature);
    if (entries.length) {
      const children = node("div", "app-subnav"); children.hidden = true;
      children.setAttribute("role", "group"); children.setAttribute("aria-label", `${feature.title} views`);
      for (const entry of entries) {
        const child = node("a", "app-subnav-link", entry.label);
        child.href = routeHash(entry.path); child.dataset.view = entry.view;
        children.append(child);
      }
      group.append(children);
    }
    (feature.navigation_group === "advanced" ? advanced : nav).append(group);
  }
  if (advanced.querySelector(".app-nav-feature")) nav.append(advanced);
}

function updateNavigation(route, feature, selected) {
  const nav = $("app-navigation");
  for (const group of nav.querySelectorAll(".app-nav-feature")) {
    const active = group.dataset.feature === route.feature;
    const link = group.querySelector(".app-nav"); link.classList.toggle("active", active);
    if (active && !selected) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    const children = group.querySelector(".app-subnav");
    if (children) children.hidden = !active;
    for (const child of group.querySelectorAll(".app-subnav-link")) {
      const current = active && child.dataset.view === selected?.view;
      child.classList.toggle("active", current);
      if (current) child.setAttribute("aria-current", "page"); else child.removeAttribute("aria-current");
    }
  }
  // Opening an advanced destination reveals it; later workspace refreshes respect a manual collapse.
  if (feature?.navigation_group === "advanced" && navigationFeature !== route.feature) $("app-nav-advanced").open = true;
  navigationFeature = route.feature;
}

function renderHome(root) {
  const home = node("div", "app-home");
  const heading = node("div", "home-heading");
  heading.append(node("span", "app-caption", "BUILD. UNDERSTAND. IMPROVE."), node("h1", "", "Your Zephyr workbench."), node("p", "", "Explore firmware today. Bring your next tools into the same workspace."));
  home.append(heading);
  const workspace = node("section", "home-workspace");
  const text = node("div"); text.append(node("span", "app-caption", "CURRENT WORKSPACE"));
  const path = node("p", "", state.workspace.path); path.id = "home-workspace-path"; text.append(path);
  const change = node("button", "app-button", "Switch workspace"); change.addEventListener("click", () => run(openWorkspace));
  workspace.append(text, change); home.append(workspace);
  const label = node("div", "home-section-heading"); label.append(node("h2", "", "Your tools"), node("span", "", "One application · Shared workspace")); home.append(label);
  const grid = node("div", "tool-grid");
  const advancedGrid = node("div", "tool-grid");
  for (const feature of state.features) {
    const available = feature.status === "available";
    const card = node("article", `tool-card${available ? " available" : " planned"}`);
    const top = node("div", "tool-card-top");
    const icon = node("span", "tool-icon", icons[feature.id] || "◇"); icon.setAttribute("aria-hidden", "true");
    top.append(icon, node("span", `tool-status ${available ? "available" : ""}`, available ? "AVAILABLE" : "PLANNED"));
    card.append(top, node("h3", "", feature.title), node("p", "tool-description", feature.description));
    const capabilities = node("ul", "tool-capabilities");
    for (const detail of toolDetails[feature.id] || []) capabilities.append(node("li", "", detail));
    card.append(capabilities);
    const link = node("a", available ? "app-button primary tool-open" : "tool-scope", available ? `Open ${feature.title} →` : "View planned scope →");
    link.href = routeHash(feature.entry_path || feature.id); card.append(link);
    (feature.navigation_group === "advanced" ? advancedGrid : grid).append(card);
  }
  home.append(grid);
  if (advancedGrid.childElementCount) {
    const advanced = node("details", "home-advanced");
    advanced.append(node("summary", "", "Advanced tools"), advancedGrid); home.append(advanced);
  }
  const note = node("div", "home-note");
  note.append(node("span", "app-dot"), node("p", "", "Your reports, snapshots and workspace stay on this computer. Planned tools will open here as they become available."));
  home.append(note); root.append(home);
}

function renderPlanned(root, feature) {
  const page = node("section", "app-home planned-page");
  page.append(node("span", "tool-status", "PLANNED"), node("h1", "", feature.title), node("p", "planned-description", feature.description));
  const panel = node("div", "planned-panel");
  panel.append(node("h2", "", "Planned scope"), node("p", "", "This tool is on the roadmap. Its editor and workflows are not implemented yet."));
  const list = node("ul");
  for (const detail of toolDetails[feature.id] || []) list.append(node("li", "", detail));
  panel.append(list, node("p", "", "It will use this application's workspace and navigation, with no separate launcher.")); page.append(panel);
  const back = node("a", "app-button", "← Back to all tools"); back.href = "#/home"; page.append(back); root.append(page);
}

function loadStylesheet(path) {
  if (!stylesheets.has(path)) {
    const pending = new Promise((resolve, reject) => {
      const link = document.createElement("link"); link.rel = "stylesheet"; link.href = path;
      link.addEventListener("load", resolve, {once: true});
      link.addEventListener("error", () => { link.remove(); stylesheets.delete(path); reject(new Error("Could not load this tool's stylesheet.")); }, {once: true});
      document.head.append(link);
    });
    stylesheets.set(path, pending);
  }
  return stylesheets.get(path);
}

async function renderRoute() {
  const revision = ++state.routeRevision;
  const route = parseRoute(location.hash);
  const changedRoute = state.routePath !== route.path;
  const requestedResult = state.resultRoute === route.path;
  state.resultRoute = null;
  state.routePath = route.path;
  const revealDestination = () => {
    if (!changedRoute || revision !== state.routeRevision) return;
    // A feature may reveal a specific result immediately after navigate(), before
    // hashchange runs, or while its navigate handler runs. Keep that destination.
    if (requestedResult || state.resultRoute === route.path) { state.resultRoute = null; return; }
    const root = $("app-main");
    const heading = [...root.querySelectorAll("h1")].find((item) => item.getClientRects().length);
    revealResult(heading || root, {block: "start"});
  };
  if (location.hash !== routeHash(route.path)) history.replaceState(null, "", routeHash(route.path));
  const feature = state.features.find((item) => item.id === route.feature);
  const selected = activeNavigationEntry(feature, route);
  const title = route.feature === "home" ? "Home" : feature ? [feature.title, selected?.label].filter(Boolean).join(" · ") : "Tool not found";
  $("app-title").textContent = title; document.title = `${title} · Zephyr Utilities`;
  updateNavigation(route, feature, selected);
  if (state.mounted?.id === route.feature) { state.mounted.instance.navigate(route.view); revealDestination(); return; }
  state.mounted?.instance.dispose(); state.mounted = null;
  const root = $("app-main"); root.replaceChildren();
  if (route.feature === "home") { renderHome(root); revealDestination(); return; }
  if (!feature) {
    const panel = node("section", "app-home"); panel.append(node("h1", "", "Tool not found"), node("p", "", "This tool is not registered in this installation."));
    const back = node("a", "app-button", "Back to Home"); back.href = "#/home"; panel.append(back); root.append(panel); revealDestination(); return;
  }
  if (feature.status !== "available") { renderPlanned(root, feature); revealDestination(); return; }
  root.append(node("div", "app-loading", `Opening ${feature.title}…`));
  try {
    const assets = featureAssets(feature);
    const [template, module] = await Promise.all([
      fetch(assets.template).then((response) => { if (!response.ok) throw new Error("Could not load this tool's interface."); return response.text(); }),
      import(assets.module),
      loadStylesheet(assets.stylesheet),
    ]);
    if (revision !== state.routeRevision) return;
    if (typeof module.mount !== "function") throw new Error("This tool does not provide a mount function.");
    const host = node("div", "feature-host");
    // Only trusted, local feature templates are HTML. API data always uses textContent.
    host.innerHTML = template;
    root.replaceChildren(host);
    if (!state.instances.has(feature.id)) state.instances.set(feature.id, {});
    const instance = await module.mount(host, {api, session: state.session, workspace: state.workspace, features: state.features, state: state.instances.get(feature.id), navigate, notify, refreshWorkspace,
      reveal(element, options) {
        const destination = parseRoute(location.hash);
        const revealed = destination.feature === feature.id && host.contains(element) && revealResult(element, options);
        if (revealed) state.resultRoute = destination.path;
        return revealed;
      }});
    if (!instance || typeof instance.navigate !== "function" || typeof instance.dispose !== "function") throw new Error("This tool does not implement the feature lifecycle.");
    if (revision !== state.routeRevision) { instance.dispose(); return; }
    state.mounted = {id: feature.id, instance};
    instance.navigate(route.view);
    revealDestination();
  } catch (error) {
    if (revision !== state.routeRevision) return;
    root.replaceChildren();
    const panel = node("section", "app-home"); panel.append(node("h1", "", "Could not open this tool"), node("p", "", error.message));
    const retry = node("button", "app-button", "Try again"); retry.addEventListener("click", () => run(renderRoute)); panel.append(retry); root.append(panel);
    revealDestination();
  }
}

async function browseWorkspace(path) {
  $("workspace-error").hidden = true;
  $("workspace-save").disabled = true;
  const response = await api(`/api/folders?path=${encodeURIComponent(path)}`);
  state.folder = response; $("workspace-path").value = response.path;
  $("workspace-parent").disabled = !response.parent;
  const folders = $("workspace-folders"); folders.replaceChildren();
  for (const folder of response.folders || []) {
    const button = node("button", "", `▱  ${folder.name}`);
    button.addEventListener("click", () => workspaceAction(() => browseWorkspace(folder.path))); folders.append(button);
  }
  if (!response.folders?.length) folders.append(node("p", "", "No subfolders in this directory."));
  $("workspace-save").disabled = false;
}

async function workspaceAction(action) {
  try { await action(); } catch (error) { $("workspace-error").textContent = error.message; $("workspace-error").hidden = false; revealResult($("workspace-error")); }
}

async function openWorkspace() {
  $("workspace-dialog").showModal();
  $("workspace-path").value = state.workspace.path;
  $("workspace-path").focus();
  await workspaceAction(() => browseWorkspace(state.workspace.path));
}

async function refreshJobs() {
  try {
    const response = await api("/api/jobs");
    const active = (response.jobs || []).filter((job) => !["completed", "failed", "cancelled"].includes(job.status));
    state.activeJobs = active;
    $("app-jobs").hidden = !active.length;
    $("app-jobs").textContent = `${active.length} ${active.length === 1 ? "job" : "jobs"} running`;
  } catch (_) { /* A tool shows its own actionable job errors. */ }
}

async function init() {
  const response = await fetch("/api/session");
  state.session = await response.json();
  if (!response.ok) throw new Error(state.session.error || "Could not connect to the local service.");
  api = createApi(state.session);
  const [registry] = await Promise.all([api("/api/features"), refreshWorkspace()]);
  state.features = registry.features || [];
  $("app-version").textContent = state.session.version ? `v${state.session.version}` : "";
  // Listen before the sidebar becomes interactive; the initial feature may load slowly.
  window.addEventListener("hashchange", () => run(renderRoute));
  renderNavigation(); await renderRoute();
  $("app-notice-close").addEventListener("click", () => { $("app-notice").hidden = true; });
  $("workspace-open").addEventListener("click", () => run(openWorkspace));
  $("workspace-close").addEventListener("click", () => $("workspace-dialog").close());
  $("workspace-form").addEventListener("submit", (event) => { event.preventDefault(); workspaceAction(() => browseWorkspace($("workspace-path").value.trim())); });
  $("workspace-parent").addEventListener("click", () => workspaceAction(() => browseWorkspace(state.folder.parent)));
  $("workspace-path").addEventListener("input", () => { $("workspace-save").disabled = false; });
  $("workspace-save").addEventListener("click", () => workspaceAction(async () => {
    $("workspace-save").disabled = true;
    try {
      await api("/api/workspace", {method: "PATCH", body: {path: $("workspace-path").value.trim()}});
      await refreshWorkspace(); $("workspace-dialog").close();
      await renderRoute(); notify("Workspace updated for all tools.");
    } finally { $("workspace-save").disabled = false; }
  }));
  $("app-jobs").addEventListener("click", () => navigate(activeJobRoute(state.activeJobs, state.features)));
  // Skip-link focus must not turn into a route change.
  document.querySelector(".app-skip").addEventListener("click", (event) => { event.preventDefault(); $("app-main").focus(); });
  await refreshJobs();
  const jobsTimer = setInterval(refreshJobs, 5000);
  window.addEventListener("pagehide", () => { state.mounted?.instance.dispose(); clearInterval(jobsTimer); clearTimeout(noticeTimer); }, {once: true});
}

init().catch((error) => {
  $("app-main").replaceChildren(node("div", "app-loading", "Could not connect. Start zephyr-utils, then reload this page."));
  notify(error.message || String(error), true);
});
