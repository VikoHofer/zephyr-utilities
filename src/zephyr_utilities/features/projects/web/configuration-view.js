import {creatorBoards, creatorSdks, selectedConfiguration} from "./creator-state.js";
import {copyFragments, fragmentSelectionError} from "./fragment-state.js";
import {appendFragmentFacts, createFragmentView} from "./fragment-view.js";
import {createOverlayView, selectedOverlayUsage} from "./overlay-view.js";

const array = (value) => Array.isArray(value) ? value : [];

/** Keep configuration controls mounted while drafts and filtered choices change. */
export function createConfigurationView({node, fact, choices}) {
  const fragmentView = createFragmentView({node, choices});
  const overlayView = createOverlayView({node});
  function captureFocus(root) {
    const control = root.ownerDocument.activeElement;
    const section = control?.closest("[data-configuration-project]");
    if (!section || !root.contains(section)) return null;
    const attribute = ["data-configuration-select", "data-configuration-field", "data-configuration-add", "data-configuration-remove", "data-configuration-save", "data-configuration-close", "data-fragment-control"].find((name) => control.hasAttribute(name));
    if (!attribute) return null;
    return {project: section.dataset.configurationProject, attribute, value: control.getAttribute(attribute),
      start: control.selectionStart, end: control.selectionEnd, direction: control.selectionDirection};
  }
  function restoreFocus(root, focus) {
    if (!focus) return;
    const section = [...root.querySelectorAll("[data-configuration-project]")].find((item) => item.dataset.configurationProject === focus.project);
    const control = section && [...section.querySelectorAll(`[${focus.attribute}]`)].find((item) => item.getAttribute(focus.attribute) === focus.value);
    if (!control || control.disabled || control.closest("[hidden]")) return;
    control.focus({preventScroll: true});
    if (typeof focus.start === "number" && typeof control.selectionStart === "number") control.setSelectionRange(focus.start, focus.end, focus.direction);
  }
  function create(project) {
    const section = node("div", "projects-configurations");
    section.dataset.configurationProject = project.id;
    const row = node("div", "projects-configuration-row");
    const label = node("label", "", "Build configuration");
    const select = node("select"); select.dataset.configurationSelect = project.id;
    label.append(select);
    const add = node("button", "projects-button", "Add configuration");
    add.type = "button"; add.dataset.configurationAdd = project.id;
    add.setAttribute("aria-controls", `configuration-form-${project.id}`);
    const remove = node("button", "projects-button", "Remove configuration");
    remove.type = "button"; remove.dataset.configurationRemove = project.id;
    row.append(label, add, remove);
    const facts = node("dl", "projects-identity"); facts.dataset.configurationFacts = "";
    const overlays = node("div"); overlays.dataset.configurationOverlays = "";
    const notice = node("p", "projects-banner"); notice.dataset.configurationNotice = "";
    notice.setAttribute("role", "status"); notice.setAttribute("aria-live", "polite");
    const form = node("form", "projects-configuration-form");
    form.id = `configuration-form-${project.id}`;
    form.dataset.configurationForm = project.id;
    form.setAttribute("aria-label", `New build configuration for ${project.name}`);
    form.append(node("h4", "", "Add a build configuration"), node("p", "projects-muted", "Save a target, SDK and application fragment selection for future Configure and Build actions."));
    const requirement = node("p", "projects-banner"); requirement.dataset.configurationRequirement = "";
    form.append(requirement);
    const grid = node("div", "projects-option-grid");
    for (const [key, title, tag, fullWidth] of [
      ["name", "Configuration name", "input", true],
      ["search", "Find a board", "input", true],
      ["board_id", "Board", "select", false],
      ["target", "Exact target", "select", false],
      ["sdk_id", "Installed GNU SDK", "select", true],
    ]) {
      const label = node("label", fullWidth ? "projects-full-width" : "", title);
      const field = node(tag); field.dataset.configurationField = key;
      if (key !== "search") field.required = true;
      if (tag === "input") { field.autocomplete = "off"; field.spellcheck = false; }
      if (key === "name") { field.maxLength = 64; field.placeholder = "For example: Prototype board"; }
      if (key === "search") { field.type = "search"; field.placeholder = "Board name, chip, vendor or target…"; }
      label.append(field); grid.append(label);
    }
    const targetNote = node("p", "projects-muted"); targetNote.dataset.configurationTargetNote = "";
    const actions = node("div", "projects-actions");
    const save = node("button", "projects-button primary", "Save configuration"); save.type = "submit"; save.dataset.configurationSave = "";
    const close = node("button", "projects-button", "Close"); close.type = "button"; close.dataset.configurationClose = project.id;
    actions.append(save, close); form.append(grid, targetNote, fragmentView.create(), actions);
    section.append(row, facts, overlays, notice, form);
    return section;
  }
  function update(section, project, state, busy) {
    const selected = selectedConfiguration(state, project);
    const select = section.querySelector("[data-configuration-select]");
    const options = [["", "Original"], ...array(project.configurations).map((item) => [item.id, item.name])];
    const optionsKey = JSON.stringify(options);
    if (select.dataset.choices !== optionsKey) {
      select.dataset.choices = optionsKey;
      choices(select, options.slice(1), "Original");
    }
    select.value = selected?.id || ""; select.disabled = busy;
    const remove = section.querySelector("[data-configuration-remove]");
    remove.hidden = !selected?.id;
    remove.disabled = busy || !selected?.id;
    remove.dataset.configurationId = selected?.id || "";
    const facts = section.querySelector("[data-configuration-facts]");
    const factsKey = JSON.stringify(selected?.profile);
    if (facts.dataset.value !== factsKey) {
      facts.dataset.value = factsKey;
      facts.replaceChildren();
      fact(facts, "Requested target", selected?.profile?.target, true);
      fact(facts, "SDK installation", selected?.profile?.sdk?.path, true);
      appendFragmentFacts(facts, selected?.profile, fact);
    }
    overlayView.render(section.querySelector("[data-configuration-overlays]"), selectedOverlayUsage(project, selected?.id),
      {target: selected?.profile?.target, disclosureKey: `project:${project.id}:configuration:${selected?.id || "original"}`});
    const notice = section.querySelector("[data-configuration-notice]");
    notice.hidden = state.configurationNotice?.project_id !== project.id;
    notice.textContent = notice.hidden ? "" : state.configurationNotice.message;
    const form = section.querySelector("[data-configuration-form]");
    const open = state.configurationEditor === project.id;
    form.hidden = !open;
    const add = section.querySelector("[data-configuration-add]");
    add.disabled = busy; add.setAttribute("aria-expanded", String(open));
    if (!open) return;
    const draft = state.configurationDrafts[project.id];
    const snapshot = state.snapshot;
    const inputs = Object.fromEntries([...form.querySelectorAll("[data-configuration-field]")].map((input) => [input.dataset.configurationField, input]));
    const key = JSON.stringify([snapshot?.id, snapshot?.fingerprint, draft.search, draft.board_id]);
    if (form.dataset.choices !== key) {
      form.dataset.choices = key;
      const board = creatorBoards(snapshot).find((item) => item.id === draft.board_id);
      const boards = creatorBoards(snapshot, draft.search);
      if (board && !boards.some((item) => item.id === board.id)) boards.unshift(board);
      choices(inputs.board_id, boards.map((item) => [item.id, `${item.full_name || item.name} · ${item.name}`]), `Choose a board (${boards.length})`);
      choices(inputs.target, array(board?.targets).map((target) => [target, target]), "Choose an exact target");
      choices(inputs.sdk_id, creatorSdks(snapshot).map((sdk) => [sdk.id, `SDK ${sdk.version || "unknown version"} · ${sdk.path}`]), "Choose an SDK installation");
      form.querySelector("[data-configuration-target-note]").textContent = board ? `${board.name} · ${array(board.socs).join(", ") || "Chip unknown"}. Configuration validates compatibility and resolves defaults.` : "Select a board and its full declared target, including any SoC, CPU, variant and revision.";
    }
    for (const [name, input] of Object.entries(inputs)) {
      if (input.value !== draft[name]) input.value = draft[name];
      input.disabled = busy;
    }
    const requirement = form.querySelector("[data-configuration-requirement]");
    requirement.hidden = Boolean(snapshot) && snapshot.status !== "stale";
    requirement.replaceChildren();
    if (!requirement.hidden) {
      requirement.append(node("span", "", snapshot?.status === "stale" ? "Environment inputs changed during analysis. " : "Analyze this workspace to choose a board and SDK. "));
      const link = node("a", "", "Open Environment"); link.href = "#/projects/environment"; requirement.append(link);
    }
    const save = form.querySelector("[data-configuration-save]");
    fragmentView.update(form.querySelector("[data-fragments]"), draft, state.configurationFiles[project.id], busy);
    save.disabled = busy || !snapshot || snapshot.status === "stale" || !draft.name.trim() || !draft.board_id || !draft.target || !draft.sdk_id || Boolean(fragmentSelectionError(copyFragments(draft.fragments), state.configurationFiles[project.id]));
    save.textContent = state.busy === "configuration-save" ? "Saving…" : "Save configuration";
    form.querySelector("[data-configuration-close]").disabled = busy;
  }
  return {create, update, captureFocus, restoreFocus};
}
