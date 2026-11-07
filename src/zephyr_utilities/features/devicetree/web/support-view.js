import {filterSupportedBoards, supportCatalog} from "./support-data.js";

/** Informational release evidence; runtime chip discovery remains the authority. */
export function mountSupportPanel(root, context) {
  const document = root.ownerDocument;
  const lifetime = new AbortController();
  const state = context.state;
  const vendors = [...new Set(supportCatalog.verifiedBoards.map((board) => board.vendor))];
  const retained = state.supportFilters ||= {query: "", vendor: "all"};
  retained.query = typeof retained.query === "string" ? retained.query : "";
  if (!vendors.includes(retained.vendor)) retained.vendor = "all";
  root.classList.add("dte-support");

  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = value;
    return element;
  }
  function list(values, ordered = false) {
    const element = node(ordered ? "ol" : "ul", "dte-support-list");
    for (const value of values) element.append(node("li", "", value));
    return element;
  }
  function section(title, description) {
    const element = node("section", "dte-support-section");
    element.append(node("h2", "", title));
    if (description) element.append(node("p", "dte-support-muted", description));
    return element;
  }
  function details(label) {
    const element = node("details", "dte-support-details");
    element.append(node("summary", "", label));
    return element;
  }

  const header = node("header", "dte-support-header");
  header.append(node("p", "dte-eyebrow", "Device Tree Editor"));
  const heading = node("h1", "", "Supported boards");
  heading.dataset.dteSupportHeading = "";
  header.append(heading, node("p", "dte-support-lead", supportCatalog.scope),
    node("p", "dte-support-muted", supportCatalog.evidenceNotice));
  const metrics = node("div", "dte-support-metrics");
  for (const value of [`${supportCatalog.verifiedBoards.length} recorded board workflows`,
    `${vendors.length} implemented vendor adapters`, "GPIO · UART · I2C · SPI"]) {
    metrics.append(node("span", "dte-support-badge", value));
  }
  header.append(metrics);

  const verified = section("Verified board workflows", "These exact targets have recorded overlay-editing evidence. Expand a board for its limits and verification record.");
  const filters = node("div", "dte-support-filters");
  const searchLabel = node("label", "", "Find a board or SoC");
  const search = node("input");
  search.type = "search";
  search.placeholder = "nRF54L15, Nucleo, STM32H7…";
  search.value = retained.query;
  searchLabel.append(search);
  const vendorLabel = node("label", "", "Vendor adapter");
  const vendor = node("select");
  for (const [value, label] of [["all", "All implemented adapters"], ...vendors.map((item) => [item, item])]) {
    const option = node("option", "", label); option.value = value; vendor.append(option);
  }
  vendor.value = retained.vendor;
  vendorLabel.append(vendor);
  const clear = node("button", "", "Clear filters");
  clear.type = "button";
  filters.append(searchLabel, vendorLabel, clear);
  const count = node("p", "dte-support-count");
  count.setAttribute("role", "status"); count.setAttribute("aria-live", "polite"); count.setAttribute("aria-atomic", "true");
  const boards = node("ul", "dte-support-boards");
  boards.setAttribute("aria-label", "Verified boards");
  const empty = node("p", "dte-support-empty", "No recorded board matches these filters. Your board may still work through an automatic adapter. See Automatic family coverage and Contribute support for next steps.");
  verified.append(filters, count, boards, empty);

  function renderBoards() {
    const matches = filterSupportedBoards(retained.query, retained.vendor);
    count.textContent = `${matches.length} of ${supportCatalog.verifiedBoards.length} recorded board workflows shown.`;
    empty.hidden = matches.length !== 0;
    boards.hidden = matches.length === 0;
    const cards = matches.map((board) => {
      const card = node("li", "dte-support-board");
      const title = node("div", "dte-support-board-heading");
      title.append(node("h3", "", board.name), node("span", "dte-support-vendor", board.vendor));
      card.append(title, node("p", "dte-support-soc", board.soc), node("code", "dte-support-target", board.target));
      card.append(node("p", "dte-support-functions", board.features.join(" · ")));
      card.append(node("p", "dte-support-verification", board.verification));
      card.append(node("p", "dte-support-build", `Configure / build: ${board.build}`));
      const record = details("Limits and verification");
      if (board.notes.length) record.append(list(board.notes));
      if (board.evidence) {
        const evidence = node("p", "dte-support-evidence", "Repository record: ");
        evidence.append(node("code", "", board.evidence)); record.append(evidence);
      }
      card.append(record);
      return card;
    });
    boards.replaceChildren(...cards);
  }
  search.addEventListener("input", () => {
    retained.query = search.value; renderBoards();
  }, {signal: lifetime.signal});
  vendor.addEventListener("change", () => {
    retained.vendor = vendor.value; renderBoards();
  }, {signal: lifetime.signal});
  clear.addEventListener("click", () => {
    retained.query = ""; retained.vendor = "all";
    search.value = ""; vendor.value = "all"; renderBoards();
  }, {signal: lifetime.signal});

  const coverage = section("Automatic family coverage", "A board missing from the verified list can still be supported. The editor reads your selected Zephyr sources automatically; each available action still needs sufficient routing and ownership evidence.");
  const families = node("div", "dte-support-family-grid");
  for (const family of supportCatalog.automaticCoverage) {
    const card = node("article", "dte-support-family");
    card.append(node("h3", "", family.title), node("p", "", family.description));
    if (family.limits.length) {
      const limits = details("Coverage limits"); limits.append(list(family.limits)); card.append(limits);
    }
    families.append(card);
  }
  coverage.append(families);

  const unavailable = section("Under construction", "These adapters or configurations do not yet provide the complete pin-activation workflow.");
  const gaps = node("dl", "dte-support-gaps");
  for (const item of supportCatalog.unavailable) {
    const row = node("div"); row.append(node("dt", "", item.name), node("dd", "", item.reason)); gaps.append(row);
  }
  unavailable.append(gaps);

  const contributing = section("Contribute support", supportCatalog.contributing.intro);
  contributing.append(list(supportCatalog.contributing.steps, true));
  if (supportCatalog.contributing.paths?.length) {
    const paths = details("Contributor guide and implementation files");
    const files = node("dl", "dte-support-files");
    for (const entry of supportCatalog.contributing.paths) {
      const row = node("div"); const term = node("dt"); term.append(node("code", "", entry.path));
      row.append(term, node("dd", "", entry.description)); files.append(row);
    }
    paths.append(node("p", "dte-support-muted", "Paths below are relative to a source checkout of Zephyr Utilities."), files);
    contributing.append(paths);
  }

  const shortcuts = node("nav", "dte-support-shortcuts");
  shortcuts.setAttribute("aria-label", "Support catalogue sections");
  for (const [label, destination] of [["Automatic coverage", coverage],
    ["Under construction", unavailable], ["Contribute support", contributing]]) {
    const shortcut = node("button", "", label);
    shortcut.type = "button";
    shortcut.addEventListener("click", () => context.reveal(destination.firstElementChild, {block: "start"}),
      {signal: lifetime.signal});
    shortcuts.append(shortcut);
  }
  header.append(shortcuts);

  root.replaceChildren(header, coverage, verified, unavailable, contributing);
  renderBoards();
  return {dispose() { lifetime.abort(); }};
}
