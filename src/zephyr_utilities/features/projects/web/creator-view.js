import {activeJob} from "./environment-state.js";
import {createCreatorSession, creationHandoff, creatorBoards, creatorSdks, creatorTemplates, creatorTemplateId, creatorTemplateLabel, selectedCreatorTemplate, currentCreatorJob} from "./creator-state.js";
import {createConfigurationView} from "./configuration-view.js";
import {appendFragmentFacts} from "./fragment-view.js";
import {isLocalSample, selectedSample, sampleSelectionError, sampleCheckSelection, sampleCompatibilityNotice, currentSampleCompatibility} from "./sample-state.js";
import {createOverlayView} from "./overlay-view.js";
import {createManagementView} from "./management-view.js";
import {createOutputView} from "./output-view.js";
import {createReviewDialog} from "./review-dialog.js";

export {createReviewDialog} from "./review-dialog.js";

export function applicationHandoff(view) {
  const match = /^applications\/([a-f0-9]{32})\/(original|[a-f0-9]{32})(?:\/([a-f0-9]{32}))?$/.exec(view);
  return match ? {project_id: match[1], configuration_id: match[2] === "original" ? null : match[2], attempt_id: match[3] || null} : null;
}

export async function resolveApplicationHandoff({session, state, handoff, current}) {
  // An explicit load has its own awaited response. A polling refresh can return
  // immediately while the initial mount is still loading its application list.
  const loaded = await session.load();
  if (!current()) return null;
  if (!loaded) throw new Error(state.error || "The application list could not be refreshed. Open the Projects destination again.");
  const project = (state.projects || []).find((item) => item.id === handoff.project_id);
  if (!project || handoff.configuration_id && !(project.configurations || []).some((item) => item.id === handoff.configuration_id)) {
    throw new Error("The requested application or configuration is no longer available. Choose a current configuration before building.");
  }
  session.selectConfiguration(handoff.project_id, handoff.configuration_id || "");
  const attempt = handoff.attempt_id && (project.attempts || []).find((item) => item.id === handoff.attempt_id
    && (item.configuration?.id || null) === handoff.configuration_id);
  return {project, attempt};
}

/** Bound a requested result reveal to its navigation and one refresh. */
export function createResultNavigation({workspace, view, disposed, find, reveal, refresh, error, notify}) {
  let pending;
  const current = (request) => request && pending === request && !disposed() && workspace() === request.workspace && view() === "applications";
  function show() {
    if (!pending) return;
    if (workspace() !== pending.workspace || disposed()) { pending = null; return; }
    if (view() !== "applications") return;
    const target = find(pending.id);
    if (!target) return;
    pending = null;
    reveal(target);
  }
  return {
    request(id) { pending = {id, workspace: workspace()}; },
    hasPending: () => Boolean(pending),
    show,
    navigate(next) { if (next !== "applications" || pending && workspace() !== pending.workspace) pending = null; },
    async refresh() {
      const request = pending;
      await refresh();
      if (!current(request)) return;
      show();
      if (!current(request)) return;
      pending = null;
      const message = error();
      notify(message || "The saved result is no longer available in this application list.", Boolean(message));
    },
  };
}

export function mountCreator(root, context) {
  const $ = (name) => root.querySelector(`[data-${name}]`);
  const fields = Object.fromEntries([...root.querySelectorAll("[data-create-field]")].map((input) => [input.dataset.createField, input]));
  const lifetime = new AbortController();
  const state = context.state.creator ||= {};
  let session;
  let disposed = false;
  let selectionKey;
  let sampleKey;
  let renderedPlan;
  let renderedProjects;
  let renderedBuildPlan;
  let watchedJob;
  let navigationRevision = 0;
  let route = "applications";
  let view = "applications";
  const creationReview = createReviewDialog($("creator-preview"));
  const buildReview = createReviewDialog($("build-preview"));
  const array = (value) => Array.isArray(value) ? value : [];
  const text = (value, fallback = "Unknown") => value === null || value === undefined || value === "" ? fallback : String(value);
  const time = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "Unknown time";
  const resultNavigation = createResultNavigation({
    workspace: () => context.workspace.path, view: () => view, disposed: () => disposed,
    find: (id) => {
      if (state.workspace !== context.workspace.path) return null;
      const job = array(state.jobs).find((item) => item.id === id);
      const attemptId = job?.metadata?.attempt_id;
      const projectId = job?.result?.project?.id || job?.project?.id;
      const outputId = job?.result?.output_id || job?.metadata?.output_id;
      if (outputId) return [...root.querySelectorAll("[data-output-record]")].find((item) => item.dataset.outputRecord === outputId);
      return attemptId
        ? [...root.querySelectorAll("[data-disclosure]")].find((item) => item.dataset.disclosure === `attempt:${attemptId}`)
        : [...root.querySelectorAll("[data-saved-project]")].find((item) => item.dataset.savedProject === projectId);
    },
    reveal: (target) => {
      if (target.tagName === "DETAILS") target.open = true;
      for (let parent = target.parentElement; parent && parent !== root; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true;
      context.reveal?.(target);
    },
    refresh: () => session.refresh(), error: () => state.error,
    notify: (message, error) => { context.notify?.(message, error); if (error) context.reveal?.($("creator-error")); },
  });
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
  const overlays = createOverlayView({node});
  const outputs = createOutputView({node});
  const management = createManagementView({root, context, state, session: () => session, node, fact, choices,
    onApplied: (kind, fields) => {
      if (kind === "clean") { showProgress(); return; }
      const projectId = fields.project_id || state.projects?.[0]?.id;
      const card = [...root.querySelectorAll("[data-saved-project]")].find((item) => item.dataset.savedProject === projectId);
      if (card) context.reveal?.(card);
    }});
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
    const key = JSON.stringify([snapshot?.id, snapshot?.fingerprint, form.search, form.board_id, state.templates, creatorTemplateId(form)]);
    if (selectionKey !== key) {
      selectionKey = key;
      const allBoards = creatorBoards(snapshot);
      const boards = creatorBoards(snapshot, form.search);
      const selected = allBoards.find((board) => board.id === form.board_id);
      if (selected && !boards.some((board) => board.id === selected.id)) boards.unshift(selected);
      choices(fields.board_id, boards.map((board) => [board.id, `${board.full_name || board.name} · ${board.name}`]), `Choose a board (${boards.length})`);
      choices(fields.target, array(selected?.targets).map((target) => [target, target]), "Choose an exact target");
      choices(fields.sdk_id, creatorSdks(snapshot).map((sdk) => [sdk.id, `SDK ${sdk.version || "unknown version"} · ${sdk.path}`]), "Choose an SDK installation");
      const templates = creatorTemplates(state.templates);
      const templateId = creatorTemplateId(form);
      const template = selectedCreatorTemplate(state);
      const entries = templates.map((entry) => [entry.id, entry.title]);
      if (templateId && !template) entries.unshift([templateId, `Unavailable template: ${templateId}`]);
      choices(fields.template_id, entries, "Choose an application template");
      $("creator-template-note").textContent = template?.description || (templates.length ? "This template is unavailable. Choose a listed template before previewing." : "No application templates are available. Refresh the list to load them.");
      $("creator-target-note").textContent = selected ? `${selected.name} · ${array(selected.socs).join(", ") || "Chip unknown"}. Select the full declared target; configuration validates compatibility and resolves defaults.` : "Choose the full target, including any SoC, CPU, variant and revision. Metadata discovery does not establish build compatibility.";
    }
    for (const [name, input] of Object.entries(fields)) {
      const value = name === "template_id" ? creatorTemplateId(form) : form[name];
      if (input.value !== value) input.value = value ?? "";
    }
    fields.destination.placeholder = `applications/${state.form.name || "my_app"}`;
  }
  function renderPlan() {
    const plan = state.plan;
    creationReview.sync(plan, view === "create");
    if (plan === renderedPlan) return;
    renderedPlan = plan;
    if (!plan) return;
    $("preview-destination").textContent = plan.destination;
    $("preview-count").textContent = `${array(plan.files).length} new files`;
    const profile = $("preview-profile"); profile.replaceChildren();
    fact(profile, "Exact target", plan.profile?.target, true);
    fact(profile, "SDK installation", plan.profile?.sdk?.path, true);
    fact(profile, "Zephyr checkout", plan.profile?.zephyr?.path, true);
    fact(profile, "Template", creatorTemplateLabel(plan.template, state.templates, plan.sample));
    overlays.render($("preview-overlays"), plan.overlay_usage, {target: plan.profile?.target, disclosureKey: "creation-preview"});
    const compatibility = $("preview-compatibility"); compatibility.replaceChildren(); compatibility.hidden = !plan.sample;
    if (plan.sample) {
      const result = plan.sample_compatibility || {status: "unknown", message: "Compatibility has not been checked for this sample and target.", details: []};
      compatibility.dataset.status = result.status;
      compatibility.append(node("h3", "", compatibilityTitle(result.status)), node("p", "", result.message));
      const details = node("ul", "projects-source-list");
      for (const message of array(result.details)) details.append(node("li", "", message));
      if (details.childElementCount) compatibility.append(details);
      compatibility.append(node("p", "projects-muted", result.status === "compatible" ? "This checks configuration support. Compilation and hardware operation still require validation." : "Creating this copy does not establish that it will configure, build or work on this board."));
    }
    renderAdaptation($("preview-adaptation"), plan.sample?.adaptation);
    const sample = $("preview-sample"); sample.replaceChildren(); sample.hidden = !plan.sample;
    if (plan.sample) {
      sample.append(node("summary", "", "Sample requirements and origin"));
      sample.append(node("p", "projects-muted", "Runtime files, source provenance and license notices are retained. Any adaptations are recorded below. Source changes before creation require a new preview."));
      const requirements = node("ul", "projects-sample-requirements");
      for (const requirement of array(plan.sample.requirements)) requirements.append(node("li", "", requirement));
      if (requirements.childElementCount) sample.append(requirements);
      sample.append(disclosure("Sample provenance", "preview:sample", node("pre", "", JSON.stringify(plan.sample, null, 2))));
    }
    const files = $("preview-files"); files.replaceChildren();
    for (const file of array(plan.files)) {
      const detail = node("details", "projects-preview-file");
      detail.append(node("summary", "projects-path", file.path), node("pre", "", file.content));
      files.append(detail);
    }
    recipe($("preview-recipe"), plan.recipe);
  }
  function compatibilityTitle(status) {
    return {compatible: "Configuration supported", incompatible: "Incompatible with selected target", checking: "Checking compatibility", unknown: "Compatibility unknown"}[status] || "Compatibility unknown";
  }
  function renderAdaptation(target, adaptation) {
    const expanded = target.querySelector("details")?.open || false;
    target.replaceChildren(); target.hidden = !adaptation;
    if (!adaptation) return;
    target.dataset.status = "adapted";
    target.append(node("h3", "", `Board adaptation: ${adaptation.title || adaptation.id}`), node("p", "", adaptation.message || "This copy includes reviewed changes for the selected target."));
    const paths = array(adaptation.files).map((file) => file.path || file);
    const runnable = paths.filter((path) => ["CMakeLists.txt", "prj.conf", "app.overlay", "src/main.c", "src/main.cpp"].includes(path));
    if (runnable.length) target.append(node("p", "projects-muted", `Changed application files: ${runnable.join(", ")}.`));
    const files = node("ul", "projects-source-list projects-path");
    for (const path of paths) files.append(node("li", "", path));
    if (files.childElementCount) {
      const details = disclosure(`Adaptation files and preserved originals (${paths.length})`, null, files);
      details.open = expanded;
      target.append(details);
    }
    target.append(node("p", "projects-muted", "Review the full contents of these files under Review files to create before applying this copy."));
  }
  function renderSampleCompatibility(busy) {
    const visible = isLocalSample(state.form);
    $("sample-compatibility").hidden = !visible;
    if (!visible) return;
    const check = state.sampleCheck;
    const notice = sampleCompatibilityNotice(state);
    const active = activeJob(check?.job);
    $("sample-compatibility").dataset.status = notice.status;
    $("sample-compatibility-summary").textContent = `${compatibilityTitle(notice.status)}. ${notice.message}`;
    $("sample-compatibility-summary").dataset.status = notice.status;
    $("sample-check-status").textContent = compatibilityTitle(notice.status);
    $("sample-check-message").textContent = notice.message;
    const details = $("sample-check-details"); details.replaceChildren();
    for (const message of array(notice.details)) details.append(node("li", "", message));
    details.hidden = !details.childElementCount;
    renderAdaptation($("sample-check-adaptation"), notice.adaptation);
    const overlayReport = currentSampleCompatibility(state)?.overlay_usage;
    $("sample-check-overlays").hidden = !overlayReport;
    if (overlayReport) overlays.render($("sample-check-overlays"), overlayReport, {target: state.form.target, disclosureKey: "sample-check"});
    $("sample-check").disabled = busy || !sampleCheckSelection(state) || check?.submitting || active;
    $("sample-check").textContent = check?.submitting ? "Starting check…" : active ? check.stale ? "Waiting for previous check…" : "Checking compatibility…" : check?.verified ? "Check again" : "Check compatibility";
    $("sample-check-cancel").hidden = !active;
    $("sample-check-cancel").disabled = Boolean(check?.cancelling);
    $("sample-check-cancel").textContent = check?.cancelling ? "Cancelling…" : check?.stale ? "Cancel previous check" : "Cancel check";
    $("sample-check-progress").hidden = !check?.job;
    $("sample-check-logs").textContent = array(check?.job?.logs).join("\n") || "No log messages yet.";
  }
  function renderSamples(busy) {
    const visible = isLocalSample(state.form);
    const catalogue = state.sampleCatalogue;
    $("creator-samples").hidden = !visible;
    fields.sample_id.required = visible;
    fields.sample_id.disabled = !visible || busy || !catalogue?.ready || catalogue.loading;
    $("samples-refresh").disabled = busy || !state.snapshot || state.snapshot.status === "stale" || Boolean(catalogue?.loading);
    $("samples-refresh").textContent = catalogue?.loading ? "Reading samples…" : "Refresh samples";
    if (!visible) return;
    const key = JSON.stringify([catalogue, state.form.sample_id, state.snapshot?.status]);
    if (sampleKey === key) return;
    sampleKey = key;
    const sample = selectedSample(state);
    const entries = array(catalogue?.samples).map((item) => [item.id, `${item.title}${item.status === "available" ? "" : ` · ${item.status}`}`]);
    if (state.form.sample_id && !sample) entries.unshift([state.form.sample_id, `Unavailable sample: ${state.form.sample_id}`]);
    choices(fields.sample_id, entries, "Choose a local sample");
    fields.sample_id.value = state.form.sample_id || "";
    $("samples-status").textContent = !state.snapshot || state.snapshot.status === "stale" ? "Analyze the selected workspace before loading local samples."
      : catalogue?.loading ? "Reading the reviewed local samples and demos…"
      : catalogue?.error || (catalogue?.ready ? `Available samples in this checkout: ${catalogue.samples.filter((item) => item.status === "available").length}. Select one to see its requirements.` : "Refresh samples to read this checkout.");
    $("sample-description").textContent = sample?.description || "";
    const requirements = $("sample-requirements"); requirements.replaceChildren();
    for (const requirement of array(sample?.requirements)) requirements.append(node("li", "", requirement));
    requirements.hidden = !requirements.childElementCount;
    const diagnostics = $("sample-diagnostics"); diagnostics.replaceChildren();
    if (catalogue?.ready && state.form.sample_id && !sample) diagnostics.append(node("p", "projects-banner", "This sample is no longer listed in the selected checkout. Choose a listed sample."));
    for (const diagnostic of [...array(catalogue?.diagnostics), ...array(sample?.diagnostics)]) diagnostics.append(node("p", "projects-banner", diagnostic));
    if (sample?.status === "available") diagnostics.append(node("p", "projects-muted", `Base sample copy: ${sample.file_count} files, including provenance and licenses. Board adaptations may add files; preview the exact copy before creation.`));
  }
  function removalButton(projectId) {
    const remove = node("button", "projects-button", "Remove saved application");
    remove.type = "button"; remove.dataset.projectRemove = projectId;
    return remove;
  }
  function reconnectButton(projectId) {
    const button = node("button", "projects-button", "Reconnect folder"); button.type = "button"; button.dataset.projectReconnect = projectId;
    return button;
  }
  function renderAttempt(parent, attempt, projectId, overlayReport) {
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
      appendFragmentFacts(facts, attempt.profile, fact);
    }
    fact(facts, "Output directory", attempt.build_dir || attempt.context?.build_dir || attempt.context?.build_directory, true);
    fact(facts, "Effective target", attempt.context?.effective_target || attempt.context?.target || attempt.context?.board, true);
    fact(facts, "Output files", attempt.output_state || "Unknown");
    detail.append(facts);
    if (attempt.reason || attempt.output_reason) detail.append(node("p", "projects-muted", attempt.reason || attempt.output_reason));
    const overlayDetails = node("div");
    overlays.render(overlayDetails, overlayReport, {target: attempt.profile?.target, freshness: attempt.context?.application_freshness, disclosureKey: key});
    detail.append(overlayDetails);
    const buildDirectory = attempt.build_dir || attempt.context?.build_dir || attempt.context?.build_directory;
    if (buildDirectory) {
      const inspect = node("a", "projects-button", "Inspect build");
      inspect.href = `#/inspector/builds/${encodeURIComponent(buildDirectory)}`;
      detail.append(inspect);
    }
    const technical = disclosure("Compiler and output files", `${key}:outputs`);
    const compiler = node("dl", "projects-identity");
    fact(compiler, "C compiler", attempt.context?.compiler || attempt.context?.c_compiler, true);
    if (attempt.context?.cxx_compiler) fact(compiler, "C++ compiler", attempt.context.cxx_compiler, true);
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
    if (!activeJob(attempt)) {
      const remove = node("button", "projects-button", "Remove history entry");
      remove.type = "button"; remove.dataset.attemptRemove = attempt.id; remove.dataset.projectId = projectId;
      detail.append(remove);
    }
    parent.append(detail);
  }
  function renderProjects(busy) {
    const projects = array(state.projects);
    const unavailable = array(state.unavailableProjects);
    const signature = JSON.stringify([projects, unavailable, state.templates]);
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
        card.dataset.savedProject = project.id;
        card.append(node("h3", "", project.name), node("p", "projects-path", project.path));
        card.append(node("p", "projects-muted", `Created ${time(project.created_at)}`));
        card.append(node("p", "projects-muted", creatorTemplateLabel(project.template, state.templates, project.sample)));
        if (project.sample) card.append(disclosure("Sample provenance", `project:${project.id}:sample`, node("pre", "", JSON.stringify(project.sample, null, 2))));
        card.append(configurations.create(project));
        const actions = node("div", "projects-actions");
        for (const [operation, label] of [["configure", "Configure fresh"], ["build", "Build fresh"]]) {
          const button = node("button", `projects-button${operation === "build" ? " primary" : ""}`, label); button.type = "button"; button.dataset.projectId = project.id; button.dataset.buildOperation = operation; actions.append(button);
        }
        const sources = node("a", "projects-button", "Browse sources");
        sources.href = `#/inspector/application/${encodeURIComponent(project.path)}`;
        if (array(project.build_outputs).length) {
          const manage = node("button", "projects-button", "Manage outputs"); manage.type = "button"; manage.dataset.outputsOpen = project.id; actions.append(manage);
        }
        actions.append(reconnectButton(project.id), removalButton(project.id));
        card.append(actions);
        card.append(disclosure("Advanced details", `project:${project.id}:advanced`,
          node("p", "projects-muted", "Open current source and binding details in Build Inspector."), sources));
        for (const attempt of array(project.attempts)) renderAttempt(card, attempt, project.id, project.overlay_usage_by_attempt?.[attempt.id]);
        const outputList = outputs.create(project); if (outputList) card.append(outputList);
        target.append(card);
      }
      if (unavailable.length) {
        const hidden = disclosure(`Unavailable saved applications (${unavailable.length})`, "unavailable-applications",
          node("p", "projects-muted", "These saved locations are missing or now contain a different folder. Reconnect a folder to its current location, restore the original folder, or remove its saved entry and history here."));
        hidden.classList.add("projects-unavailable");
        for (const project of unavailable) {
          const card = node("article", "projects-created-app");
          card.dataset.savedProject = project.id;
          card.append(node("h3", "", project.name), node("p", "projects-path", project.path),
            node("p", "projects-muted", `Created ${time(project.created_at)}`),
            node("p", "projects-banner", project.availability_reason || (project.availability === "replaced" ? "This folder was replaced." : "This folder is missing or was moved.")),
            reconnectButton(project.id), removalButton(project.id));
          for (const attempt of array(project.attempts)) renderAttempt(card, attempt, project.id, project.overlay_usage_by_attempt?.[attempt.id]);
          const outputList = outputs.create(project); if (outputList) card.append(outputList);
          hidden.append(card);
        }
        target.append(hidden);
      }
      for (const detail of target.querySelectorAll("details[data-disclosure]")) detail.open = expanded.has(detail.dataset.disclosure);
    }
    for (const section of target.querySelectorAll("[data-configuration-project]")) {
      const project = projects.find((item) => item.id === section.dataset.configurationProject);
      configurations.update(section, project, state, busy);
    }
    for (const button of target.querySelectorAll("[data-build-operation]")) button.disabled = busy;
    for (const button of target.querySelectorAll("[data-project-remove], [data-attempt-remove]")) button.disabled = busy;
    for (const button of target.querySelectorAll("[data-project-reconnect], [data-reuse-operation], [data-output-clean]")) button.disabled = busy;
    configurations.restoreFocus(target, focus);
  }
  function renderBuildPlan() {
    const plan = state.buildPlan;
    buildReview.sync(plan, view === "applications");
    if (plan === renderedBuildPlan) return;
    renderedBuildPlan = plan;
    if (!plan) return;
    $("build-preview-title").textContent = plan.operation === "configure" ? "Configure application" : "Configure and build application";
    $("build-preview-note").textContent = `This runs the selected local application and Zephyr CMake code. ${plan.reuse ? "This updates the selected managed output directory. Other build directories are preserved." : "Existing build directories are preserved."}`;
    recipe($("build-preview-recipe"), plan.recipe, plan.operation);
    const facts = node("dl", "projects-identity");
    fact(facts, "Application", array(state.projects).find((project) => project.id === plan.project_id)?.name);
    fact(facts, "Build configuration", plan.configuration?.name || "Original");
    fact(facts, "Requested target", plan.profile?.target, true);
    fact(facts, "SDK installation", plan.profile?.sdk?.path, true);
    appendFragmentFacts(facts, plan.profile, fact);
    overlays.render($("build-preview-overlays"), plan.overlay_usage, {target: plan.profile?.target, disclosureKey: "build-preview"});
    $("build-preview-summary").replaceChildren(facts, node("p", "projects-path", `${plan.reuse ? "Reuse output directory" : "New output directory"}: ${plan.build_dir}`));
    if (plan.reuse) $("build-preview-summary").append(node("p", "projects-banner", "This reuses and updates existing managed outputs. Earlier attempts retain their recorded evidence but share the live output files."));
    if (plan.sample_compatibility) {
      const compatibility = node("div", "projects-compatibility"); compatibility.dataset.status = plan.sample_compatibility.status;
      compatibility.append(node("h3", "", compatibilityTitle(plan.sample_compatibility.status)), node("p", "", plan.sample_compatibility.message));
      for (const message of array(plan.sample_compatibility.details)) compatibility.append(node("p", "projects-muted", message));
      $("build-preview-summary").append(compatibility);
    }
    $("build-start").textContent = plan.operation === "configure" ? "Run configure" : "Run build";
  }
  function renderJob() {
    const job = currentCreatorJob(state);
    $("creator-job").hidden = !job;
    if (!job) return;
    const operation = {create: "Create application", configure: "Configure", build: "Build", "clean-output": "Output cleanup"}[job.metadata?.operation] || "Project operation";
    const status = {queued: "queued", running: "running", completed: "complete", failed: "failed", cancelled: "cancelled"}[job.status] || job.status;
    $("creator-job-title").textContent = `${operation} ${status}`;
    $("creator-job-status").textContent = activeJob(job) ? "You can leave this view while the operation continues in its captured workspace." : job.error || (job.metadata?.operation === "clean-output" ? "Review the managed output state below. Recorded attempt history is retained." : job.status === "completed" ? "Review the saved application or attempt in Applications." : "Partial output, if any, is retained with this attempt.");
    $("creator-job-applications").hidden = job.metadata?.operation !== "create" || job.status !== "completed";
    $("creator-job-result").hidden = job.metadata?.operation === "create" || activeJob(job) || !job.metadata?.attempt_id && job.metadata?.operation !== "clean-output";
    $("creator-job-result").textContent = job.metadata?.operation === "clean-output" ? "View output state" : job.status === "completed" ? "View build result" : "View attempt details";
    $("creator-logs").textContent = array(job.logs).join("\n") || "No log messages yet.";
    $("creator-cancel").hidden = !activeJob(job); $("creator-cancel").disabled = state.cancelling;
    $("creator-cancel").textContent = state.cancelling ? "Cancelling…" : "Cancel operation";
    announceCompletion();
  }
  function announceCompletion() {
    if (!watchedJob || watchedJob.workspace !== state.workspace || state.workspace !== context.workspace.path) return;
    const job = array(state.jobs).find((entry) => entry.id === watchedJob.id);
    if (!job || activeJob(job)) return;
    watchedJob = null;
    context.notify?.(job.status === "completed" ? "Operation complete. Use the progress panel to view the result."
      : job.error || (job.status === "cancelled" ? "Operation cancelled. Any partial output is retained." : "Operation failed. Open the attempt details."), job.status === "failed");
  }
  function render() {
    if (disposed) return;
    const snapshot = state.snapshot;
    const creating = view === "create";
    const busy = Boolean(state.busy || state.loading || array(state.jobs).some(activeJob));
    const pending = state.creationHandoff;
    $("creation-handoff").hidden = !creating || !route.startsWith("create/") || !pending;
    if (pending) {
      const messages = {
        loading: "Checking the demo selection…",
        review: "Your creation form has changes. Keep the current draft or apply the proposed demo, board and SDK below.",
        applied: "MNIST, board and SDK selected. Review the application name and destination, then preview the files.",
        kept: "Your current creation draft was kept.",
        error: pending.error,
      };
      $("creation-handoff-status").textContent = messages[pending.status] || "";
      const sdk = creatorSdks(snapshot).find((entry) => entry.id === pending.selection?.selection.sdk_id);
      $("creation-handoff-details").textContent = pending.selection
        ? `${pending.selection.title} · ${pending.selection.selection.target} · SDK ${sdk?.version || "selected installation"}. Your application name and destination are retained.` : "";
      $("creation-handoff-choices").hidden = !pending.choiceShown;
      for (const name of ["creation-handoff-use", "creation-handoff-keep"]) {
        $(name).setAttribute("aria-disabled", String(busy || pending.status !== "review"));
      }
    }
    $("creator-eyebrow").textContent = creating ? "PROJECT CREATOR · NEW APPLICATION" : "PROJECT CREATOR · APPLICATIONS";
    $("creator-title").textContent = creating ? "Create an application." : "Your workspace applications.";
    $("creator-intro").textContent = creating ? "Choose a template, destination, board and SDK, then review the files before creation." : "Choose an application's build configuration, configure it or start a build.";
    $("creator-page-link").textContent = creating ? "View applications" : "Create application";
    $("creator-page-link").href = creating ? "#/projects/applications" : "#/projects/create";
    $("creator-scope").textContent = creating ? "Creation writes the files you review. Configure or build the new app from Applications when you are ready." : "Use fresh outputs or review reuse of a managed output directory. Configure, Build and output cleanup each require a separate review. No action flashes hardware.";
    $("creator-form").hidden = !creating;
    $("creator-applications").hidden = creating;
    $("creator-workspace").textContent = state.workspace || context.workspace.path;
    $("creator-environment").textContent = state.loading ? "Loading workspace applications…" : snapshot ? `Zephyr ${snapshot.zephyr?.version || "version unknown"} · ${snapshot.freshness === "saved-observation" ? "Saved observation; inputs are rechecked before creation and builds" : "Local environment observation"}` : creating ? "Analyze the environment to choose a board and SDK." : "Applications created here are listed at their saved locations.";
    $("creator-error").textContent = state.error || ""; $("creator-error").hidden = !state.error;
    $("creator-removal-notice").textContent = state.removalNotice || "";
    $("creator-removal-notice").hidden = !state.removalNotice;
    $("creator-requirement").hidden = !creating || Boolean(snapshot) && snapshot.status !== "stale";
    $("creator-requirement").textContent = snapshot?.status === "stale" ? "Environment inputs changed during the scan. Analyze again before creating an application." : "Analyze this workspace in Environment before choosing a board and SDK.";
    renderChoices();
    for (const input of Object.values(fields)) input.disabled = Boolean(state.busy === "create" || activeJob(currentCreatorJob(state)));
    renderSamples(busy);
    renderSampleCompatibility(busy);
    $("preview-submit").disabled = busy || !selectedCreatorTemplate(state) || Boolean(sampleSelectionError(state)) || !snapshot || snapshot.status === "stale" || !state.form.board_id || !state.form.target || !state.form.sdk_id || !state.form.name.trim();
    $("preview-submit").textContent = state.busy === "preview" ? "Preparing preview…" : "Preview application";
    $("create-submit").disabled = busy || !state.plan || state.plan?.sample_compatibility?.status === "incompatible";
    $("create-submit").textContent = state.busy === "create" ? "Starting creation…" : "Create application";
    $("build-start").disabled = busy || !state.buildPlan;
    $("build-start").textContent = state.busy === "build" ? "Starting operation…" : state.buildPlan?.operation === "configure" ? "Run configure" : "Run build";
    for (const button of root.querySelectorAll("[data-review-close]")) button.disabled = state.busy === "create" || state.busy === "build";
    $("creator-refresh").disabled = Boolean(state.loading || state.busy);
    $("creator-refresh").textContent = state.loading ? "Refreshing…" : "Refresh list";
    $("creator-import").disabled = busy;
    renderPlan(); renderJob(); renderProjects(busy); renderBuildPlan();
    const diagnostics = $("creator-diagnostics"); diagnostics.replaceChildren();
    for (const item of array(state.diagnostics)) diagnostics.append(node("p", "projects-banner", item.message || item));
    resultNavigation.show();
    management.render();
  }
  const listen = (element, event, callback) => element.addEventListener(event, callback, {signal: lifetime.signal});
  async function perform(action, {pending, after} = {}) {
    const workspace = context.workspace.path;
    const revision = navigationRevision;
    const promise = action();
    if (pending && state.busy) context.notify?.(pending);
    const result = await promise;
    if (disposed || context.workspace.path !== workspace || state.workspace !== workspace || revision !== navigationRevision) return result;
    if (state.error) {
      context.notify?.(state.error, true);
      context.reveal?.($("creator-error"));
    } else after?.(result);
    return result;
  }
  function showProgress(previousId) {
    const job = currentCreatorJob(state);
    if (!job || job.id === previousId) return;
    watchedJob = {id: job.id, workspace: state.workspace};
    context.reveal?.($("creator-job"));
    announceCompletion();
  }
  function launch(action) {
    const previousId = currentCreatorJob(state)?.id;
    return perform(action, {pending: "Starting the reviewed operation…", after: () => showProgress(previousId)});
  }
  for (const button of root.querySelectorAll("[data-review-close]")) listen(button, "click", () => session.dismissReview(button.dataset.reviewClose));
  for (const [name, kind] of [["creator-preview", "create"], ["build-preview", "build"]]) {
    listen($(name), "cancel", (event) => { event.preventDefault(); if (!state.busy) session.dismissReview(kind); });
  }
  for (const [name, input] of Object.entries(fields)) listen(input, input.tagName === "SELECT" ? "change" : "input", () => session.changeField(name, input.value));
  listen($("creation-handoff-use"), "click", () => {
    if ($("creation-handoff-use").getAttribute("aria-disabled") !== "true") session.useCreationHandoff();
  });
  listen($("creation-handoff-keep"), "click", () => {
    if ($("creation-handoff-keep").getAttribute("aria-disabled") !== "true") session.keepCreationDraft();
  });
  listen($("creator-form"), "submit", (event) => { event.preventDefault(); perform(() => session.preview(), {pending: "Preparing application preview…", after: () => creationReview.show(state.plan, () => $("preview-submit"))}); });
  listen($("create-submit"), "click", () => launch(() => session.create()));
  listen($("build-start"), "click", () => launch(() => session.build()));
  listen($("creator-cancel"), "click", () => perform(() => session.cancel(), {after: () => context.notify?.("Cancellation requested.")}));
  listen($("creator-refresh"), "click", () => perform(() => session.refresh(), {after: () => context.notify?.("Application list refreshed.")}));
  listen($("creator-import"), "click", () => management.open("import", {path: "", name: "", search: "", board_id: state.form.board_id || "", target: state.form.target || "", sdk_id: state.form.sdk_id || ""}, {}, () => $("creator-import")));
  listen($("samples-refresh"), "click", () => perform(() => session.loadSamples(), {after: () => {
    const error = state.sampleCatalogue?.error;
    context.notify?.(error || "Local sample list refreshed.", Boolean(error));
    context.reveal?.($("samples-status"), {focus: false});
  }}));
  listen($("sample-check"), "click", () => perform(() => session.checkSample(), {after: () => {
    if (state.sampleCheck?.error) context.notify?.(state.sampleCheck.error, true);
    context.reveal?.($("sample-compatibility"), {focus: false, block: "nearest"});
  }}));
  listen($("sample-check-cancel"), "click", () => perform(() => session.cancelSampleCheck(), {after: () => {
    if (state.sampleCheck?.error) context.notify?.(state.sampleCheck.error, true);
  }}));
  function openJobResult(event) {
    event.preventDefault();
    const job = currentCreatorJob(state);
    if (!job) return;
    resultNavigation.request(job.id);
    if (view !== "applications") context.navigate("projects/applications");
    else {
      resultNavigation.show();
      if (resultNavigation.hasPending()) resultNavigation.refresh();
    }
  }
  listen($("creator-job-result"), "click", openJobResult);
  listen($("creator-job-applications"), "click", openJobResult);
  listen(window, "focus", () => session.refresh());
  listen($("created-projects"), "click", async (event) => {
    const remove = event.target.closest("[data-project-remove], [data-configuration-remove], [data-attempt-remove]");
    if (remove && root.contains(remove) && !remove.disabled) {
      const workspace = state.workspace;
      const projectId = remove.dataset.projectRemove || remove.dataset.configurationRemove || remove.dataset.projectId;
      const project = [...array(state.projects), ...array(state.unavailableProjects)].find((item) => item.id === projectId);
      if (!project) return;
      let removed = false;
      if (remove.hasAttribute("data-project-remove")) {
        if (!window.confirm(`Remove saved application “${project.name}” at ${project.path}, including its saved configurations, build history and managed-output records?\n\nApplication and build files remain on disk. Use Manage outputs first if you want to clean build files before removing the saved application.`)) return;
        removed = await perform(() => session.removeProject(projectId));
      } else if (remove.hasAttribute("data-configuration-remove")) {
        const configuration = array(project.configurations).find((item) => item.id === remove.dataset.configurationId);
        if (!configuration || !window.confirm(`Remove configuration “${configuration.name}” from “${project.name}”?\n\nBuild history and application files are preserved.`)) return;
        removed = await perform(() => session.removeConfiguration(projectId, configuration.id));
      } else {
        const attempt = array(project.attempts).find((item) => item.id === remove.dataset.attemptRemove);
        if (!attempt || !window.confirm(`Remove the ${attempt.operation === "configure" ? "configure" : "build"} history entry for “${project.name}” from ${time(attempt.finished_at || attempt.started_at)}?\n\nIts saved commands and logs are removed. Output files remain on disk.`)) return;
        removed = await perform(() => session.removeAttempt(projectId, attempt.id));
      }
      if (removed && !disposed && state.workspace === workspace && view === "applications") {
        context.notify?.(state.removalNotice);
        const select = [...root.querySelectorAll("[data-configuration-select]")].find((item) => item.dataset.configurationSelect === projectId);
        (select || $("creator-refresh")).focus();
      }
      return;
    }
    const button = event.target.closest("[data-build-operation]");
    if (button && root.contains(button)) {
      const projectId = button.dataset.projectId;
      const operation = button.dataset.buildOperation;
      const trigger = () => [...root.querySelectorAll("[data-build-operation]")].find((item) => item.dataset.projectId === projectId && item.dataset.buildOperation === operation);
      perform(() => session.previewBuild(projectId, operation), {pending: "Preparing operation review…", after: () => buildReview.show(state.buildPlan, trigger)});
    }
    const reconnect = event.target.closest("[data-project-reconnect]");
    if (reconnect && !reconnect.disabled) {
      const projectId = reconnect.dataset.projectReconnect;
      const project = [...array(state.projects), ...array(state.unavailableProjects)].find((item) => item.id === projectId);
      if (project) management.open("reconnect", {project_id: projectId, path: project.path}, {}, () => [...root.querySelectorAll("[data-project-reconnect]")].find((item) => item.dataset.projectReconnect === projectId));
    }
    const showOutputs = event.target.closest("[data-outputs-open]");
    if (showOutputs) {
      const detail = [...root.querySelectorAll("[data-project-outputs]")].find((item) => item.dataset.projectOutputs === showOutputs.dataset.outputsOpen);
      if (detail) { detail.open = true; context.reveal?.(detail); }
    }
    const clean = event.target.closest("[data-output-clean]");
    if (clean && !clean.disabled) management.open("clean", {project_id: clean.dataset.projectId, output_id: clean.dataset.outputClean}, {},
      () => [...root.querySelectorAll("[data-output-clean]")].find((item) => item.dataset.outputClean === clean.dataset.outputClean));
    const reuse = event.target.closest("[data-reuse-operation]");
    if (reuse && !reuse.disabled) {
      const project = array(state.projects).find((item) => item.id === reuse.dataset.projectId);
      const attempt = array(project?.attempts).find((item) => item.id === reuse.dataset.attemptId);
      if (!attempt || attempt.configuration?.id && !array(project.configurations).some((item) => item.id === attempt.configuration.id)) {
        context.notify?.("This output has no matching saved configuration and attempt for reuse. Refresh the list or review a fresh build.", true);
        return;
      }
      if (attempt) {
        session.selectConfiguration(project.id, attempt.configuration?.id || "");
        const trigger = () => [...root.querySelectorAll("[data-reuse-operation]")].find((item) => item.dataset.attemptId === attempt.id && item.dataset.reuseOperation === reuse.dataset.reuseOperation);
        perform(() => session.previewBuild(project.id, reuse.dataset.reuseOperation, attempt.id), {pending: "Preparing reuse review…", after: () => buildReview.show(state.buildPlan, trigger)});
      }
    }
    const add = event.target.closest("[data-configuration-add]");
    if (add && root.contains(add)) {
      session.editConfiguration(add.dataset.configurationAdd);
      context.reveal?.(add.closest("[data-configuration-project]").querySelector("[data-configuration-form]"), {focus: false});
      add.closest("[data-configuration-project]").querySelector('[data-configuration-field="name"]').focus({preventScroll: true});
    }
    const edit = event.target.closest("[data-configuration-edit]");
    if (edit && !edit.disabled) {
      session.editConfiguration(edit.dataset.configurationEdit, {editSelected: true});
      const form = edit.closest("[data-configuration-project]").querySelector("[data-configuration-form]");
      context.reveal?.(form, {focus: false});
      form.querySelector('[data-configuration-field="name"]:not(:disabled), [data-configuration-field="search"]')?.focus({preventScroll: true});
    }
    const close = event.target.closest("[data-configuration-close]");
    if (close && root.contains(close)) {
      session.closeConfiguration();
      close.closest("[data-configuration-project]").querySelector("[data-configuration-add]").focus();
    }
    const refresh = event.target.closest("[data-fragment-refresh]");
    if (refresh) perform(() => session.loadConfigurationFiles(refresh.closest("[data-configuration-form]").dataset.configurationForm), {after: () => {
      const error = state.configurationFiles[refresh.closest("[data-configuration-form]").dataset.configurationForm]?.error;
      context.notify?.(error || "Application file list refreshed.", Boolean(error));
    }});
    const fragmentAdd = event.target.closest("[data-fragment-add]");
    if (fragmentAdd) {
      const group = fragmentAdd.closest("[data-fragment-kind]");
      const available = group.querySelector("[data-fragment-available]");
      session.changeFragment(fragmentAdd.closest("[data-configuration-form]").dataset.configurationForm, group.dataset.fragmentKind, "add", available.value);
      if (!available.disabled) available.focus();
    }
    const fragmentAction = event.target.closest("[data-fragment-action]");
    if (fragmentAction) session.changeFragment(fragmentAction.closest("[data-configuration-form]").dataset.configurationForm,
      fragmentAction.closest("[data-fragment-kind]").dataset.fragmentKind, fragmentAction.dataset.fragmentAction, fragmentAction.dataset.fragmentPath);
  });
  listen($("created-projects"), "change", (event) => {
    const input = event.target;
    if (input.matches("[data-configuration-select]")) session.selectConfiguration(input.dataset.configurationSelect, input.value);
    else if (input.matches("select[data-configuration-field], input[type='checkbox'][data-configuration-field]")) session.changeConfigurationField(input.closest("[data-configuration-form]").dataset.configurationForm, input.dataset.configurationField, input.type === "checkbox" ? input.checked : input.value);
    else if (input.matches("[data-fragment-mode]")) session.changeFragment(input.closest("[data-configuration-form]").dataset.configurationForm, input.dataset.fragmentMode, "mode", input.value);
    else if (input.matches("[data-fragment-available]")) render();
  });
  listen($("created-projects"), "input", (event) => {
    const input = event.target;
    if (input.matches("input[data-configuration-field]:not([type='checkbox'])")) session.changeConfigurationField(input.closest("[data-configuration-form]").dataset.configurationForm, input.dataset.configurationField, input.value);
  });
  listen($("created-projects"), "submit", async (event) => {
    const form = event.target.closest("[data-configuration-form]");
    if (!form) return;
    event.preventDefault();
    const projectId = form.dataset.configurationForm;
    await management.previewConfiguration(projectId, () => [...root.querySelectorAll("[data-configuration-form]")].find((item) => item.dataset.configurationForm === projectId)?.querySelector("[data-configuration-save]"));
  });
  session = createCreatorSession({api: context.api, state, workspace: () => context.workspace.path, isCreating: () => view === "create", onChange: render, pendingBoard: () => {
    const pending = context.state.creatorPendingBoard;
    delete context.state.creatorPendingBoard;
    return pending;
  }});
  render(); session.load();
  return {
    navigate(next = "applications") {
      const changed = route !== next;
      route = next;
      view = next === "create" || next.startsWith("create/") ? "create" : "applications";
      if (changed) navigationRevision++;
      resultNavigation.navigate(view);
      render();
      if (next.startsWith("create/")) {
        const revision = navigationRevision; const workspace = context.workspace.path;
        const isCurrent = () => !disposed && route === next && revision === navigationRevision && context.workspace.path === workspace;
        return session.resolveCreationHandoff(creationHandoff(next) || next.slice(7), {isCurrent});
      }
      const handoff = applicationHandoff(next);
      if (handoff) {
        const revision = navigationRevision; const workspace = context.workspace.path;
        const current = () => !disposed && revision === navigationRevision && context.workspace.path === workspace && state.workspace === workspace;
        return resolveApplicationHandoff({session, state, handoff, current}).then((resolved) => {
          if (!resolved) return;
          const {project, attempt} = resolved;
          const target = attempt
            ? [...root.querySelectorAll("[data-disclosure]")].find((item) => item.dataset.disclosure === `attempt:${attempt.id}`)
            : [...root.querySelectorAll("[data-saved-project]")].find((item) => item.dataset.savedProject === project.id);
          if (target) { if (target.tagName === "DETAILS") target.open = true; context.reveal?.(target); }
          context.notify?.(attempt ? "Configuration attempt opened. Build remains a separate reviewed action."
            : "Application and configuration selected. The requested attempt is unavailable; review a new Build when ready.");
        }).catch((error) => { if (current()) context.notify?.(error.message || String(error), true); });
      }
      if (changed && view === "applications") {
        if (resultNavigation.hasPending()) resultNavigation.refresh();
        else session.refresh();
      }
      else if (view === "create") session.tick().then(() => session.ensureSamples());
      else session.tick();
    },
    dispose() { disposed = true; lifetime.abort(); creationReview.close({restoreFocus: false}); buildReview.close({restoreFocus: false}); management.dispose(); session.dispose(); },
  };
}
