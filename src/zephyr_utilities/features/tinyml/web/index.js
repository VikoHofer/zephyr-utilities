import {createDemoSession, selectedDemo, selectedBoard, allBoards, filteredBoards, availableSdks, selectionProblem} from "./state.js";
import {mountDrawing} from "./drawing.js";

function mountChooser(root, context) {
  const document = root.ownerDocument;
  const $ = (name) => root.querySelector(`[data-tinyml-${name}]`);
  const state = context.state;
  const lifetime = new AbortController();
  const workspace = () => context.workspace.path;
  let catalogueKey;
  let boardCatalogue;
  let boardSearch;
  let boardSelection;
  let disposed = false;
  let session;
  function node(tag, value) { const element = document.createElement(tag); if (value !== undefined) element.textContent = value; return element; }
  function choices(select, entries, empty, value) {
    select.replaceChildren();
    const initial = node("option", empty); initial.value = ""; select.append(initial);
    for (const [id, label] of entries) { const option = node("option", label); option.value = id; select.append(option); }
    if (value && !entries.some(([id]) => id === value)) { const unavailable = node("option", `Unavailable selection: ${value}`); unavailable.value = value; select.append(unavailable); }
    select.value = value;
  }
  function paragraphs(parent, values) { parent.replaceChildren(...values.map((value) => node("p", value))); }
  function render() {
    if (disposed) return;
    const demo = selectedDemo(state), board = selectedBoard(state);
    if (catalogueKey !== state.catalogue) {
      catalogueKey = state.catalogue;
      choices($("demo"), (state.catalogue?.demos || []).map((item) => [item.demo_id, item.title]), "Choose a local demo", state.form.demo_id);
      paragraphs($("catalogue-diagnostics"), state.catalogue?.diagnostics || []);
      choices($("sdk"), availableSdks(state).map((sdk) => [sdk.id, `SDK ${sdk.version || "unknown version"} · ${sdk.path}`]), "Choose an installed SDK", state.form.sdk_id);
    }
    const boards = filteredBoards(state);
    if (board && !boards.some((item) => item.id === board.id)) boards.unshift(board);
    if (boardCatalogue !== state.catalogue || boardSearch !== state.form.search || boardSelection !== state.form.board_id) {
      boardCatalogue = state.catalogue; boardSearch = state.form.search; boardSelection = state.form.board_id;
      choices($("board"), boards.map((item) => [item.id, `${item.full_name || item.name} · ${item.name}`]), "Choose a board", state.form.board_id);
      choices($("target"), (board?.targets || []).map((target) => [target, target]), "Choose an exact target", state.form.target);
    }
    for (const [name, field] of [["search", "search"], ["board", "board_id"], ["target", "target"], ["sdk", "sdk_id"], ["demo", "demo_id"]]) {
      if ($(name).value !== state.form[field]) $(name).value = state.form[field];
    }
    $("load-status").textContent = state.loading ? "Reading local demos and the saved environment…"
      : state.catalogue ? `${state.catalogue.demos.length} local demo${state.catalogue.demos.length === 1 ? "" : "s"} listed. Browsing creates no application or build.` : "Refresh demos to read the selected workspace.";
    $("load-error").textContent = state.error; $("load-error").hidden = !state.error;
    $("refresh").textContent = state.loading ? "Reading demos…" : "Refresh demos";
    $("refresh").setAttribute("aria-disabled", String(state.loading));
    $("demo-title").textContent = demo?.title || "No local demo selected";
    $("framework").textContent = demo ? `Framework: ${demo.framework} · reference kernels` : "";
    $("description").textContent = demo?.description || "Analyze the workspace and review any missing source or dependency requirements.";
    $("availability").dataset.status = demo?.status || "unknown";
    $("availability").textContent = state.catalogue && !state.catalogue.snapshot ? "Analyze this workspace to inspect the demo source and firmware dependency."
      : demo?.status === "available" ? "Source and dependency ready. Target compatibility, compilation and hardware operation have not been checked."
      : demo?.status === "missing" ? "Required local source or dependency is missing."
      : demo?.status === "unsupported" ? "This local demo format or dependency is unsupported. See the details below." : "No qualified local demo is available.";
    $("requirements").replaceChildren(...(demo?.requirements || []).map((value) => node("li", value)));
    paragraphs($("demo-diagnostics"), demo?.diagnostics || []);
    $("board-count").textContent = `${filteredBoards(state).length} matching boards of ${allBoards(state).length} discovered. A board listing is not a demo support claim.`;
    const problem = selectionProblem(state);
    $("selection-note").textContent = problem || `Selected target: ${state.form.target}. Projects will prepare a fresh file review; Configure and Build remain explicit actions.`;
    $("continue").textContent = state.submitting ? "Preparing Projects…" : "Continue in Project Creator";
    $("continue").setAttribute("aria-disabled", String(Boolean(problem) || state.submitting));
    $("handoff-status").textContent = state.handoffNotice;
    $("handoff-error").textContent = state.handoffError; $("handoff-error").hidden = !state.handoffError;
  }
  session = createDemoSession({api: context.api, state, workspace, navigate: context.navigate, onChange: render});
  function listen(element, event, callback) { element.addEventListener(event, callback, {signal: lifetime.signal}); }
  for (const [name, field, event] of [["search", "search", "input"], ["board", "board_id", "change"], ["target", "target", "change"], ["sdk", "sdk_id", "change"], ["demo", "demo_id", "change"]]) {
    listen($(name), event, () => session.changeField(field, $(name).value));
  }
  listen($("refresh"), "click", () => { if (!state.loading) void session.load(); });
  listen($("continue"), "click", () => { if (!state.submitting) void session.continueToProjects(); });
  listen($("environment"), "click", (event) => { event.preventDefault(); context.navigate("projects/environment"); });
  render();
  return {
    navigate() { return session.navigate(); },
    dispose() { if (disposed) return; disposed = true; lifetime.abort(); session.dispose(); },
  };
}

export function createTinyMLNavigation({chooser, drawing, openChooser, openDrawing}) {
  let active;
  let instance;
  let disposed = false;
  return {
    navigate(view = "overview") {
      if (disposed) return;
      const next = view === "draw" ? "draw" : "overview";
      if (next !== active) {
        instance?.dispose(); active = next;
        chooser.hidden = next !== "overview"; drawing.hidden = next !== "draw";
        instance = next === "draw" ? openDrawing() : openChooser();
      }
      return instance.navigate(next);
    },
    dispose() { if (!disposed) { disposed = true; instance?.dispose(); } },
  };
}

export function mount(host, context) {
  const root = host.querySelector("[data-tinyml-root]");
  const chooser = root.querySelector("[data-tinyml-chooser]");
  const drawing = root.querySelector("[data-tinyml-drawing]");
  return createTinyMLNavigation({chooser, drawing,
    openChooser: () => mountChooser(chooser, context),
    openDrawing: () => mountDrawing(drawing, context)});
}
