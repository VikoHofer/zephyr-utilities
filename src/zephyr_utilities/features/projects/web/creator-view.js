import {activeJob} from "./environment-state.js";
import {createCreatorSession, creatorBoards, creatorSdks, currentCreatorJob} from "./creator-state.js";
import {createConfigurationView} from "./configuration-view.js";

export function mountCreator(root, context) {
  const $ = (name) => root.querySelector(`[data-${name}]`);
  const fields = Object.fromEntries([...root.querySelectorAll("[data-create-field]")].map((input) => [input.dataset.createField, input]));
  const lifetime = new AbortController();
  const state = context.state.creator ||= {};
  let session;
  let disposed = false;
  let selectionKey;
  let renderedPlan;
  let renderedProjects;
  let renderedBuildPlan;
  let view = "applications";
  const array = (value) => Array.isArray(value) ? value : [];
  const text = (value, fallback = "Unknown") => value === null || value === undefined || value === "" ? fallback : String(value);
  const time = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "Unknown time";
  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = String(value);
    return element;
  }
  function fact(parent, label, value, path = false) {
    const item = node("div", "projects-fact");
    item.append(node("dt", "", label), node("dd", path ? "projects-path" : "", text(value)));
    parent.append(item);
  }
  function choices(select, entries, prompt) {
    select.replaceChildren();
    for (const [value, label] of [["", prompt], ...entries]) {
      const option = node("option", "", label); option.value = value; select.append(option);
    }
  }
  function disclosure(label, key, ...content) {
    const detail = node("details", "projects-technical-details");
    if (key) detail.dataset.disclosure = key;
    detail.append(node("summary", "", label), ...content);
    return detail;
  }
  const configurations = createConfigurationView({node, fact, choices});
  function recipe(parent, value, operation, key = "recipe") {
    parent.replaceChildren();
    if (!value) { parent.append(node("p", "projects-muted", "Commands are unavailable. Generate a new preview.")); return; }
    const details = node("dl", "projects-identity"); fact(details, "Working directory", value.cwd, true); parent.append(details);
    if (operation !== "build-only") parent.append(disclosure("Configure command", `${key}:configure`, node("pre", "", value.display_configure || array(value.configure).join("\n"))));
    if (operation !== "configure") parent.append(disclosure("Compile command", `${key}:compile`, node("pre", "", value.display_build || array(value.build).join("\n"))));
    parent.append(disclosure("Argument arrays and environment", `${key}:environment`,
      node("pre", "", JSON.stringify({configure: value.configure, build: value.build, environment: value.environment}, null, 2))));
  }
  function renderChoices() {
    const snapshot = state.snapshot;
    const form = state.form;
    const key = JSON.stringify([snapshot?.id, snapshot?.fingerprint, form.search, form.board_id]);
    if (selectionKey !== key) {
      selectionKey = key;
      const allBoards = creatorBoards(snapshot);
      const boards = creatorBoards(snapshot, form.search);
      const selected = allBoards.find((board) => board.id === form.board_id);
      if (selected && !boards.some((board) => board.id === selected.id)) boards.unshift(selected);
      choices(fields.board_id, boards.map((board) => [board.id, `${board.full_name || board.name} · ${board.name}`]), `Choose a board (${boards.length})`);
      choices(fields.target, array(selected?.targets).map((target) => [target, target]), "Choose an exact target");
      choices(fields.sdk_id, creatorSdks(snapshot).map((sdk) => [sdk.id, `SDK ${sdk.version || "unknown version"} · ${sdk.path}`]), "Choose an SDK installation");
      $("creator-target-note").textContent = selected ? `${selected.name} · ${array(selected.socs).join(", ") || "Chip unknown"}. Select the full declared target; configuration validates compatibility and resolves defaults.` : "Choose the full target, including any SoC, CPU, variant and revision. Metadata discovery does not establish build compatibility.";
    }
    for (const [name, input] of Object.entries(fields)) if (input.value !== state.form[name]) input.value = state.form[name];
    fields.destination.placeholder = `applications/${state.form.name || "my_app"}`;
  }
  function renderPlan() {
    const plan = state.plan;
    $("creator-preview").hidden = view !== "create" || !plan;
    if (plan === renderedPlan) return;
    renderedPlan = plan;
    if (!plan) return;
    $("preview-destination").textContent = plan.destination;
    $("preview-count").textContent = `${array(plan.files).length} new files`;
    const profile = $("preview-profile"); profile.replaceChildren();
    fact(profile, "Exact target", plan.profile?.target, true);
    fact(profile, "SDK installation", plan.profile?.sdk?.path, true);
    fact(profile, "Zephyr checkout", plan.profile?.zephyr?.path, true);
    fact(profile, "Template", "Minimal C · version 1");
    const files = $("preview-files"); files.replaceChildren();
    for (const file of array(plan.files)) {
      const detail = node("details", "projects-preview-file");
      detail.append(node("summary", "projects-path", file.path), node("pre", "", file.content));
      files.append(detail);
    }
    recipe($("preview-recipe"), plan.recipe);
  }
  function renderAttempt(parent, attempt) {
    const detail = node("details", "projects-attempt");
    const key = `attempt:${attempt.id}`;
    detail.dataset.disclosure = key;
    detail.append(node("summary", "", `${text(attempt.status)} · ${attempt.operation === "configure" ? "Configure" : "Build"} · ${attempt.configuration?.name || "Original"} · ${time(attempt.finished_at || attempt.created_at || attempt.started_at)}`));
    if (attempt.error) detail.append(node("p", "projects-error", attempt.error));
    if (attempt.status === "interrupted") detail.append(node("p", "projects-banner", "The application stopped before this operation recorded a final result. Its output may be incomplete."));
    const facts = node("dl", "projects-identity");
    if (attempt.profile) {
      fact(facts, "Requested target", attempt.profile.target, true);
      fact(facts, "SDK installation", attempt.profile.sdk?.path, true);
    }
    fact(facts, "Output directory", attempt.build_dir || attempt.context?.build_dir || attempt.context?.build_directory, true);
    fact(facts, "Effective target", attempt.context?.effective_target || attempt.context?.target || attempt.context?.board, true);
    detail.append(facts);
    const buildDirectory = attempt.build_dir || attempt.context?.build_dir || attempt.context?.build_directory;
    if (buildDirectory) {
      const inspect = node("a", "projects-button", "Inspect build");
      inspect.href = `#/inspector/builds/${encodeURIComponent(buildDirectory)}`;
      detail.append(inspect);
    }
    const technical = disclosure("Compiler and output files", `${key}:outputs`);
    const compiler = node("dl", "projects-identity");
    fact(compiler, "Compiler", attempt.context?.compiler || attempt.context?.c_compiler, true);
    technical.append(compiler, node("p", "projects-muted", "These values were captured for this attempt. Live artifact paths may have changed since then."));
    if (array(attempt.artifacts).length) {
      const list = node("ul", "projects-source-list projects-path");
      for (const artifact of attempt.artifacts) list.append(node("li", "", `${artifact.kind || "Artifact"}: ${artifact.path} (${artifact.size ?? artifact.bytes ?? "unknown"} bytes)`));
      technical.append(list);
    }
    detail.append(technical);
    for (const diagnostic of array(attempt.diagnostics)) detail.append(node("p", "projects-muted", diagnostic.message || diagnostic));
    detail.append(disclosure("Captured context and artifact identities", `${key}:context`, node("pre", "", JSON.stringify({context: attempt.context, artifacts: attempt.artifacts}, null, 2))));
    if (array(attempt.logs).length) {
      detail.append(disclosure("Saved operation log", `${key}:logs`, node("pre", "", attempt.logs.join("\n"))));
    }
    const commandBody = node("div"); recipe(commandBody, attempt.recipe, attempt.operation, key);
    detail.append(disclosure("Commands used", `${key}:commands`, commandBody));
    parent.append(detail);
  }
  function renderProjects(busy) {
    const projects = array(state.projects);
    const signature = JSON.stringify(projects);
    const target = $("created-projects");
    let focus;
    if (signature !== renderedProjects) {
      renderedProjects = signature;
      focus = configurations.captureFocus(target);
      const expanded = new Set([...target.querySelectorAll("details[open][data-disclosure]")].map((detail) => detail.dataset.disclosure));
      target.replaceChildren();
      if (!projects.length) {
        target.append(node("p", "projects-muted", "No saved applications were found at their workspace locations. Create an application to get started."));
        const create = node("a", "projects-button primary", "Create application"); create.href = "#/projects/create"; target.append(create);
      }
      for (const project of projects) {
        const card = node("article", "projects-created-app");
        card.append(node("h3", "", project.name), node("p", "projects-path", project.path));
        card.append(configurations.create(project));
        const actions = node("div", "projects-actions");
        for (const [operation, label] of [["configure", "Configure"], ["build", "Build"]]) {
          const button = node("button", `projects-button${operation === "build" ? " primary" : ""}`, label); button.type = "button"; button.dataset.projectId = project.id; button.dataset.buildOperation = operation; actions.append(button);
        }
        const sources = node("a", "projects-button", "Browse sources");
        sources.href = `#/inspector/application/${encodeURIComponent(project.path)}`;
        card.append(actions);
        card.append(disclosure("Advanced details", `project:${project.id}:advanced`,
          node("p", "projects-muted", "Open current source and binding details in Build Inspector."), sources));
        for (const attempt of array(project.attempts)) renderAttempt(card, attempt);
        target.append(card);
      }
      for (const detail of target.querySelectorAll("details[data-disclosure]")) detail.open = expanded.has(detail.dataset.disclosure);
    }
    for (const section of target.querySelectorAll("[data-configuration-project]")) {
      const project = projects.find((item) => item.id === section.dataset.configurationProject);
      configurations.update(section, project, state, busy);
    }
    for (const button of target.querySelectorAll("[data-build-operation]")) button.disabled = busy;
    configurations.restoreFocus(target, focus);
  }
  function renderBuildPlan() {
    const plan = state.buildPlan;
    $("build-preview").hidden = view !== "applications" || !plan;
    if (plan === renderedBuildPlan) return;
    renderedBuildPlan = plan;
    if (!plan) return;
    $("build-preview-title").textContent = plan.operation === "configure" ? "Configure application" : "Configure and build application";
    recipe($("build-preview-recipe"), plan.recipe, plan.operation);
    const facts = node("dl", "projects-identity");
    fact(facts, "Application", array(state.projects).find((project) => project.id === plan.project_id)?.name);
    fact(facts, "Build configuration", plan.configuration?.name || "Original");
    fact(facts, "Requested target", plan.profile?.target, true);
    fact(facts, "SDK installation", plan.profile?.sdk?.path, true);
    $("build-preview-recipe").prepend(facts, node("p", "projects-path", `New output directory: ${plan.build_dir}`));
    $("build-start").textContent = plan.operation === "configure" ? "Run configure" : "Run build";
  }
  function renderJob() {
    const job = currentCreatorJob(state);
    $("creator-job").hidden = !job;
    if (!job) return;
    const operation = {create: "Create application", configure: "Configure", build: "Build"}[job.metadata?.operation] || "Project operation";
    const status = {queued: "queued", running: "running", completed: "complete", failed: "failed", cancelled: "cancelled"}[job.status] || job.status;
    $("creator-job-title").textContent = `${operation} ${status}`;
    $("creator-job-status").textContent = activeJob(job) ? "You can leave this view while the operation continues in its captured workspace." : job.error || (job.status === "completed" ? "Review the saved application or attempt in Applications." : "Partial output, if any, is retained with this attempt.");
    $("creator-job-applications").hidden = view !== "create" || job.status !== "completed";
    $("creator-logs").textContent = array(job.logs).join("\n") || "No log messages yet.";
    $("creator-cancel").hidden = !activeJob(job); $("creator-cancel").disabled = state.cancelling;
    $("creator-cancel").textContent = state.cancelling ? "Cancelling…" : "Cancel operation";
  }
  function render() {
    if (disposed) return;
    const snapshot = state.snapshot;
    const creating = view === "create";
    const busy = Boolean(state.busy || state.loading || array(state.jobs).some(activeJob));
    $("creator-eyebrow").textContent = creating ? "PROJECT CREATOR · NEW APPLICATION" : "PROJECT CREATOR · APPLICATIONS";
    $("creator-title").textContent = creating ? "Create an application." : "Your workspace applications.";
    $("creator-intro").textContent = creating ? "Choose a destination, board and SDK, then review the files before creation." : "Choose an application's build configuration, configure it or start a build.";
    $("creator-page-link").textContent = creating ? "View applications" : "Create application";
    $("creator-page-link").href = creating ? "#/projects/applications" : "#/projects/create";
    $("creator-scope").textContent = creating ? "Creation writes the files you review. Configure or build the new app from Applications when you are ready." : "Configure and Build each use a new output directory and a separate command review. Neither action flashes hardware.";
    $("creator-form").hidden = !creating;
    $("creator-applications").hidden = creating;
    $("creator-workspace").textContent = state.workspace || context.workspace.path;
    $("creator-environment").textContent = state.loading ? "Loading workspace applications…" : snapshot ? `Zephyr ${snapshot.zephyr?.version || "version unknown"} · ${snapshot.freshness === "saved-observation" ? "Saved observation; inputs are rechecked before creation and builds" : "Local environment observation"}` : creating ? "Analyze the environment to choose a board and SDK." : "Applications created here are listed at their saved locations.";
    $("creator-error").textContent = state.error || ""; $("creator-error").hidden = !state.error;
    $("creator-requirement").hidden = !creating || Boolean(snapshot) && snapshot.status !== "stale";
    $("creator-requirement").textContent = snapshot?.status === "stale" ? "Environment inputs changed during the scan. Analyze again before creating an application." : "Analyze this workspace in Environment before choosing a board and SDK.";
    renderChoices();
    for (const input of Object.values(fields)) input.disabled = Boolean(state.busy === "create" || activeJob(currentCreatorJob(state)));
    $("preview-submit").disabled = busy || !snapshot || snapshot.status === "stale" || !state.form.board_id || !state.form.target || !state.form.sdk_id || !state.form.name.trim();
    $("preview-submit").textContent = state.busy === "preview" ? "Preparing preview…" : "Preview application";
    $("create-submit").disabled = busy || !state.plan;
    $("create-submit").textContent = state.busy === "create" ? "Starting creation…" : "Create application";
    $("build-start").disabled = busy || !state.buildPlan;
    $("creator-refresh").disabled = Boolean(state.loading || state.busy);
    $("creator-refresh").textContent = state.loading ? "Refreshing…" : "Refresh list";
    renderPlan(); renderJob(); renderProjects(busy); renderBuildPlan();
    const diagnostics = $("creator-diagnostics"); diagnostics.replaceChildren();
    for (const item of array(state.diagnostics)) diagnostics.append(node("p", "projects-banner", item.message || item));
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  for (const [name, input] of Object.entries(fields)) listen(input, input.tagName === "SELECT" ? "change" : "input", () => session.changeField(name, input.value));
  listen($("creator-form"), "submit", (event) => { event.preventDefault(); session.preview(); });
  listen($("create-submit"), "click", () => session.create());
  listen($("build-start"), "click", () => session.build());
  listen($("creator-cancel"), "click", () => session.cancel());
  listen($("creator-refresh"), "click", () => session.refresh());
  listen(window, "focus", () => session.refresh());
  listen($("created-projects"), "click", (event) => {
    const button = event.target.closest("[data-build-operation]");
    if (button && root.contains(button)) session.previewBuild(button.dataset.projectId, button.dataset.buildOperation);
    const add = event.target.closest("[data-configuration-add]");
    if (add && root.contains(add)) {
      session.editConfiguration(add.dataset.configurationAdd);
      add.closest("[data-configuration-project]").querySelector('[data-configuration-field="name"]').focus();
    }
    const close = event.target.closest("[data-configuration-close]");
    if (close && root.contains(close)) {
      session.closeConfiguration();
      close.closest("[data-configuration-project]").querySelector("[data-configuration-add]").focus();
    }
  });
  listen($("created-projects"), "change", (event) => {
    const input = event.target;
    if (input.matches("[data-configuration-select]")) session.selectConfiguration(input.dataset.configurationSelect, input.value);
    else if (input.matches("select[data-configuration-field]")) session.changeConfigurationField(input.closest("[data-configuration-form]").dataset.configurationForm, input.dataset.configurationField, input.value);
  });
  listen($("created-projects"), "input", (event) => {
    const input = event.target;
    if (input.matches("input[data-configuration-field]")) session.changeConfigurationField(input.closest("[data-configuration-form]").dataset.configurationForm, input.dataset.configurationField, input.value);
  });
  listen($("created-projects"), "submit", async (event) => {
    const form = event.target.closest("[data-configuration-form]");
    if (!form) return;
    event.preventDefault();
    const projectId = form.dataset.configurationForm;
    await session.saveConfiguration(projectId);
    if (!disposed && state.configurationNotice?.project_id === projectId && !state.configurationEditor) {
      [...root.querySelectorAll("[data-configuration-select]")].find((input) => input.dataset.configurationSelect === projectId)?.focus();
    }
  });
  session = createCreatorSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render, pendingBoard: () => {
    const pending = context.state.creatorPendingBoard;
    delete context.state.creatorPendingBoard;
    return pending;
  }});
  render(); session.load();
  return {
    navigate(next = "applications") {
      const changed = view !== next;
      view = next === "create" ? "create" : "applications";
      render();
      if (changed && view === "applications") session.refresh();
      else session.tick();
    },
    dispose() { disposed = true; lifetime.abort(); session.dispose(); },
  };
}
