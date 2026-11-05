import {copyFragments, fragmentFormatError, fragmentKinds, fragmentSummary, fragmentTitles, profileFragments} from "./fragment-state.js";

const labels = {kconfig: "Kconfig", overlays: "overlay"};
const help = {
  auto: "Zephyr chooses the application's files automatically.",
  append: "Keep automatic application selection, then apply these files in the order below.",
  replace: "Use this ordered list instead of automatically selected application files. Board defaults still apply.",
};

export function appendFragmentFacts(parent, profile, fact) {
  for (const kind of fragmentKinds) fact(parent, fragmentTitles[kind], fragmentSummary(profileFragments(profile), kind));
}

/** File choices are application data; all labels and paths are rendered as text. */
export function createFragmentView({node, choices}) {
  function create() {
    const section = node("div", "projects-fragments");
    section.dataset.fragments = "";
    const heading = node("div", "projects-fragment-heading");
    const refresh = node("button", "projects-button", "Refresh files");
    refresh.type = "button"; refresh.dataset.fragmentRefresh = ""; refresh.dataset.fragmentControl = "refresh";
    heading.append(node("h4", "", "Application fragments"), refresh);
    section.append(heading, node("p", "projects-muted", "Select existing .conf and .overlay files from this application. A listed overlay is available, not necessarily used by the selected target. Saving keeps the selected paths and order; Configure records the actual inputs. Refresh after adding or removing files."));
    const status = node("div"); status.dataset.fragmentStatus = ""; status.setAttribute("role", "status");
    section.append(status);
    for (const kind of fragmentKinds) {
      const group = node("fieldset", "projects-fragment-group"); group.dataset.fragmentKind = kind;
      group.append(node("legend", "", fragmentTitles[kind]));
      const modeLabel = node("label", "", `${labels[kind] === "overlay" ? "Overlay" : "Kconfig"} selection`);
      const mode = node("select"); mode.dataset.fragmentMode = kind; mode.dataset.fragmentControl = `${kind}:mode`;
      for (const [value, title] of [["auto", "Automatic"], ["append", "Append files"], ["replace", "Replace application files"]]) {
        const option = node("option", "", title); option.value = value; mode.append(option);
      }
      modeLabel.append(mode);
      const description = node("p", "projects-muted"); description.dataset.fragmentHelp = "";
      const selection = node("div"); selection.dataset.fragmentSelection = "";
      const row = node("div", "projects-fragment-picker");
      const fileLabel = node("label", "", `Available ${labels[kind]} file`);
      const file = node("select"); file.dataset.fragmentAvailable = kind; file.dataset.fragmentControl = `${kind}:available`;
      fileLabel.append(file);
      const add = node("button", "projects-button", `Add ${labels[kind]} file`);
      add.type = "button"; add.dataset.fragmentAdd = kind; add.dataset.fragmentControl = `${kind}:add`;
      row.append(fileLabel, add);
      const empty = node("p", "projects-muted"); empty.dataset.fragmentEmpty = "";
      const list = node("ol", "projects-fragment-list"); list.dataset.fragmentList = "";
      selection.append(row, empty, list);
      group.append(modeLabel, description, selection); section.append(group);
    }
    return section;
  }
  function update(section, draft, catalogue, busy) {
    const fragments = copyFragments(draft.fragments);
    const formatError = fragmentFormatError(fragments);
    const status = section.querySelector("[data-fragment-status]"); status.replaceChildren();
    if (formatError) status.append(node("p", "projects-error", formatError));
    if (catalogue?.loading) status.append(node("p", "projects-muted", "Loading application files…"));
    else if (catalogue?.error) status.append(node("p", "projects-error", catalogue.error));
    for (const message of catalogue?.diagnostics || []) status.append(node("p", "projects-muted", message.message || message));
    const refresh = section.querySelector("[data-fragment-refresh]"); refresh.disabled = busy || catalogue?.loading;
    for (const group of section.querySelectorAll("[data-fragment-kind]")) {
      const kind = group.dataset.fragmentKind;
      const part = fragments?.[kind];
      const mode = group.querySelector("[data-fragment-mode]");
      mode.disabled = busy || Boolean(formatError);
      if (!formatError) mode.value = part.mode;
      group.querySelector("[data-fragment-help]").textContent = formatError ? "" : help[part.mode];
      const selection = group.querySelector("[data-fragment-selection]");
      selection.hidden = Boolean(formatError) || part.mode === "auto";
      if (selection.hidden) continue;
      const files = catalogue?.ready ? catalogue.files[kind] : [];
      const available = group.querySelector("[data-fragment-available]");
      const remaining = files.filter((path) => !part.files.includes(path));
      const optionsKey = JSON.stringify(remaining);
      if (available.dataset.choices !== optionsKey) {
        const previous = available.value;
        available.dataset.choices = optionsKey;
        choices(available, remaining.map((path) => [path, path]), "Choose a file");
        if (remaining.includes(previous)) available.value = previous;
      }
      available.disabled = busy || !catalogue?.ready || catalogue.loading || remaining.length === 0;
      const add = group.querySelector("[data-fragment-add]");
      add.disabled = available.disabled || !available.value || part.files.length >= 32;
      const empty = group.querySelector("[data-fragment-empty]");
      empty.textContent = !catalogue ? "Refresh files to choose application fragments." : catalogue.ready && !files.length ? `No selectable ${kind === "kconfig" ? ".conf" : ".overlay"} files were found in this application.` : !part.files.length ? "Choose at least one file, or use Automatic." : "Files are applied from top to bottom.";
      const list = group.querySelector("[data-fragment-list]");
      const key = JSON.stringify([part.files, files, catalogue?.ready, busy]);
      if (list.dataset.value !== key) {
        const focusedControl = list.contains(list.ownerDocument.activeElement) ? list.ownerDocument.activeElement : null;
        const focused = focusedControl?.dataset.fragmentControl;
        const focusedPath = focusedControl?.dataset.fragmentPath;
        list.dataset.value = key; list.replaceChildren();
        part.files.forEach((path, index) => {
          const item = node("li");
          const name = node("span", "projects-path", path);
          if (catalogue?.ready && !files.includes(path)) name.append(node("strong", "projects-error", " · Missing or unavailable"));
          const actions = node("span", "projects-fragment-actions");
          for (const [action, title] of [["up", "Move up"], ["down", "Move down"], ["remove", "Remove"]]) {
            const button = node("button", "projects-button", title); button.type = "button";
            button.dataset.fragmentAction = action; button.dataset.fragmentPath = path;
            button.dataset.fragmentControl = `${kind}:${action}:${path}`;
            button.setAttribute("aria-label", `${title} ${labels[kind]} file ${path}`);
            button.disabled = busy || action === "up" && index === 0 || action === "down" && index === part.files.length - 1;
            actions.append(button);
          }
          item.append(name, actions); list.append(item);
        });
        if (focused) {
          const controls = [...list.querySelectorAll("[data-fragment-control]")];
          const control = controls.find((item) => item.dataset.fragmentControl === focused && !item.disabled) || controls.find((item) => item.dataset.fragmentPath === focusedPath && !item.disabled);
          if (control) control.focus({preventScroll: true});
          else if (!available.disabled) available.focus({preventScroll: true});
        }
      }
    }
  }
  return {create, update};
}
