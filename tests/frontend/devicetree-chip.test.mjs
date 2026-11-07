import test from "node:test";
import assert from "node:assert/strict";
import {activationChoices, createPinAssignmentSession, mountPinAssignment} from "../../src/zephyr_utilities/features/devicetree/web/chip-assignment.js";
import {createChipSession} from "../../src/zephyr_utilities/features/devicetree/web/chip.js";
import {chipFitSize, pinMatches, pinPositions, mountChipPanel} from "../../src/zephyr_utilities/features/devicetree/web/chip-view.js";

const settle = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; };
const payload = () => ({schema_version: 1, supported: true, reason: "", chip: {name: "STM32F446RE", package: "LQFP64"},
  pins: [{id: "gpioa:5", name: "PA5", port: "A", index: 5, assignments: [{controller: "spi1", signal: "SCK", origin: "board", state: "default"}],
    functions: [{label: "SPI1 SCK", available: false, reason: "Pin assignment is under construction."}], restrictions: ["Onboard LED"]},
  {id: "gpiob:8", name: "PB8", port: "B", index: 8, assignments: [], functions: [{controller: "i2c1", signal: "SCL", available: false}], restrictions: []}], diagnostics: []});

function harness({api: wrapper, draft = true, panel = false, view} = {}) {
  let workspace = "/workspace"; let session; let mounted; const calls = []; const timers = [];
  const state = {data: {id: "context", revision: 1, workspace, application: {path: "/workspace/app"}, target: {requested: "nucleo_f446re/stm32f446xx"},
    editing_document: draft ? {id: "document", availability: "available"} : null}, draftView: draft ? {ready: true,
    entry: {server: {id: "draft", document_id: "document", revision: 0, sha256: "hash", text: "", syntax: {blocked: false}}, text: ""}} : null};
  const response = () => ({job_id: "job", status: "completed", result: {...payload(), context_id: state.data.id,
    expected_revision: state.data.revision, draft_id: state.draftView?.entry?.server.id ?? null,
    draft_revision: state.draftView?.entry?.server.revision ?? null, draft_sha256: state.draftView?.entry?.server.sha256 ?? null}});
  const api = async (url, options) => { calls.push({url, ...options}); return wrapper ? wrapper(url, options, response) : response(); };
  const options = {api, state, workspace: () => workspace, drafts: {pending: () => Boolean(state.draftView?.entry && state.draftView.entry.text !== state.draftView.entry.server.text)},
    onChange() { session?.sync(); mounted?.render(); }, setTimer(callback) { timers.push(callback); return callback; }, clearTimer(callback) { const index = timers.indexOf(callback); if (index >= 0) timers.splice(index, 1); }};
  const root = panel ? new Element("section") : null;
  if (root && view) root.ownerDocument.defaultView = view;
  if (panel) mounted = mountChipPanel(root, options);
  else session = createChipSession(options);
  return {state, calls, response, root, session, panel: mounted,
    render() { session?.sync(); mounted?.render(); },
    async tick() { timers.shift()?.(); await settle(); },
    workspace(value) { workspace = value; },
    dispose() { session?.dispose(); mounted?.dispose(); }, timers};
}

test("context and canonical draft automatically trigger one guarded discovery", async () => {
  const app = harness(); app.render(); app.render(); await settle();
  assert.equal(app.calls.length, 1); assert.equal(app.calls[0].url, "/api/devicetree/editor/chip/start");
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash"});
  assert.equal(app.state.chipView.data.pins.length, 2); assert.equal(app.state.chipView.busy, false);
  app.render(); await settle(); assert.equal(app.calls.length, 1); app.dispose();
});

test("missing overlays still load a context-only chip observation automatically", async () => {
  const app = harness({draft: false}); app.render(); await settle();
  assert.deepEqual(app.calls[0].body, {workspace: "/workspace", context_id: "context", expected_revision: 1});
  assert.equal(app.state.chipView.data.chip.name, "STM32F446RE"); app.dispose();
});

test("an automatically staged absent overlay uses canonical draft guards for discovery", async () => {
  const app = harness();
  Object.assign(app.state.data.editing_document, {availability: "absent", can_create: true, baseline_exists: false});
  Object.assign(app.state.draftView.entry.server, {baseline_exists: false, needs_creation: true, dirty: true});
  app.state.draftView.ready = false; app.render(); await settle();
  assert.equal(app.calls.length, 0);
  app.state.draftView.ready = true; app.render(); await settle();
  assert.equal(app.calls.length, 1); assert.equal(app.calls[0].body.draft_id, "draft");
  assert.equal(app.state.chipView.data.draft_id, "draft"); app.dispose();
});

test("loading a selected overlay waits for the canonical draft instead of inspecting old data", async () => {
  const app = harness(); app.state.draftView.ready = false; app.render(); await settle(); assert.equal(app.calls.length, 0);
  app.state.draftView.ready = true; app.state.loading = true; app.render(); await settle(); assert.equal(app.calls.length, 0);
  app.state.loading = false; app.render(); await settle(); assert.equal(app.calls.length, 1); app.dispose();
});

test("typing pauses assignment observations and an acknowledged revision refreshes automatically", async () => {
  const app = harness(); app.render(); await settle(); const prior = app.state.chipView.data;
  app.state.draftView.entry.text = "new source"; app.render(); await settle();
  assert.equal(app.state.chipView.stale, true); assert.equal(app.state.chipView.data, prior); assert.equal(app.calls.length, 1);
  Object.assign(app.state.draftView.entry.server, {revision: 1, sha256: "hash1", text: "new source"}); app.render(); await settle();
  assert.equal(app.calls.length, 2); assert.equal(app.calls[1].body.expected_draft_revision, 1); assert.equal(app.state.chipView.stale, false); app.dispose();
});

test("invalid acknowledged source still requests the backend's board-only fallback", async () => {
  const app = harness(); app.render(); await settle();
  app.state.draftView.entry.server.syntax.blocked = true; app.state.draftView.entry.server.revision++; app.render(); await settle();
  assert.equal(app.calls.length, 2); assert.equal(app.calls[1].body.expected_draft_revision, 1); app.dispose();
});

test("a mismatched result cannot become a current chip observation", async () => {
  for (const field of ["context_id", "expected_revision", "draft_id", "draft_revision", "draft_sha256"]) {
    const app = harness({api: async (_url, _options, response) => { const value = response(); value.result[field] = "other"; return value; }});
    app.render(); await settle(); assert.equal(app.state.chipView.data, null); assert.match(app.state.chipView.error, /another context or draft/); app.dispose();
  }
});

test("queued jobs are polled to completion without additional load actions", async () => {
  const app = harness({api: async (url, _options, response) => url.endsWith("/start") ? {job_id: "job", status: "queued"} : response()});
  app.render(); await settle(); assert.equal(app.state.chipView.busy, true); assert.equal(app.timers.length, 1);
  await app.tick(); assert.equal(app.calls[1].url, "/api/devicetree/editor/chip/result");
  assert.deepEqual(app.calls[1].body, {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash", job_id: "job"});
  assert.equal(app.state.chipView.busy, false); assert.equal(app.timers.length, 0); app.dispose();
});

test("failed jobs offer explicit retry without looping automatically", async () => {
  let failures = true;
  const app = harness({api: async (_url, _options, response) => failures ? {job_id: "job", status: "failed", error: "Missing pin source"} : response()});
  app.render(); await settle(); app.render(); await settle();
  assert.equal(app.calls.length, 1); assert.equal(app.state.chipView.error, "Missing pin source");
  failures = false; assert.equal(await app.session.retry(), true); assert.equal(app.state.chipView.error, ""); app.dispose();
});

test("explicit cancel stops polling while disposal does not cancel a server job", async () => {
  const app = harness({api: async () => ({job_id: "job", status: "running"})});
  app.render(); await settle(); assert.equal(await app.session.cancel(), true);
  assert.equal(app.calls.at(-1).url, "/api/devicetree/editor/chip/cancel"); assert.equal(app.timers.length, 0); assert.match(app.state.chipView.message, /cancelled/); app.dispose();
  assert.equal(app.calls.at(-1).body.expected_draft_revision, 0); assert.equal(app.calls.at(-1).body.expected_draft_sha256, "hash");
  const next = harness({api: async () => ({job_id: "job", status: "running"})}); next.render(); await settle(); next.dispose();
  assert.equal(next.calls.length, 1); assert.equal(next.timers.length, 0);
});

for (const change of ["workspace", "context", "draft", "dispose"]) test(`late chip responses cannot survive a ${change} change`, async () => {
  const waiting = deferred(); const app = harness({api: async (_url, _options, response) => { await waiting.promise; return response(); }});
  app.render(); await settle();
  if (change === "workspace") app.workspace("/other");
  if (change === "context") app.state.data.id = "other-context";
  if (change === "draft") app.state.draftView.entry.server.revision = 1;
  if (change === "dispose") app.dispose();
  waiting.resolve(); await settle(); assert.equal(app.state.chipView.data, null); app.dispose();
});

test("pin search includes configured and alternative functions and logical layout is deterministic", () => {
  const pins = payload().pins;
  assert.equal(pinMatches(pins[0], "spi"), true); assert.equal(pinMatches(pins[1], "I2C1"), true); assert.equal(pinMatches(pins[0], "PB8"), false);
  const positions = pinPositions(Array.from({length: 48}, (_, index) => ({id: String(index)})));
  assert.equal(positions.length, 48); assert.deepEqual(new Set(positions.map((item) => item.side)), new Set([0, 1, 2, 3]));
  assert.deepEqual(positions, pinPositions(Array.from({length: 48}, (_, index) => ({id: String(index)}))));
});

test("logical pin geometry preserves small chips and provides separated badges for larger inventories", () => {
  for (const count of [48, 56, 64, 144, 256]) {
    const positions = pinPositions(Array.from({length: count}, (_, index) => ({id: String(index)})));
    for (const side of [0, 1, 2, 3]) {
      const edge = positions.filter((pin) => pin.side === side);
      for (let index = 1; index < edge.length; index++) {
        const distance = Math.hypot(edge[index].x - edge[index - 1].x, edge[index].y - edge[index - 1].y);
        assert.ok(distance >= 24 - 1e-9, `${count} pins: adjacent badges on side ${side} need a visible gap`);
      }
    }
    if (count <= 56) {
      assert.deepEqual(positions.filter((pin) => pin.side === 0).map(({x}) => x), Array(count / 4).fill(250));
      assert.deepEqual(positions.filter((pin) => pin.side === 2).map(({x}) => x), Array(count / 4).fill(650));
      assert.equal(positions[0].y, 265); assert.equal(positions[count / 4 - 1].y, 595);
    }
  }
});

class Element {
  constructor(tagName, document) {
    this.tagName = tagName; this.children = []; this.attributes = {}; this.dataset = {}; this.listeners = {}; this.style = {}; this.hidden = false; this.clientWidth = 600;
    this.ownerDocument = document || {createElement: (tag) => new Element(tag, this.ownerDocument), createElementNS: (_namespace, tag) => new Element(tag, this.ownerDocument)};
  }
  append(...elements) { for (const child of elements) { child.parentNode = this; this.children.push(child); } }
  replaceChildren(...elements) { this.children.forEach((child) => { child.parentNode = null; }); this.children = []; this._text = ""; this.append(...elements); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatch(name, event = {}) { for (const fn of this.listeners[name] || []) fn({target: this, preventDefault() {}, ...event}); }
  focus() { this.ownerDocument.activeElement = this; }
  contains(element) { return this === element || this.children.some((child) => child.contains(element)); }
  querySelectorAll(selectors) { const tags = selectors.split(/,\s*/); return descendants(this).filter((item) => tags.includes(item.tagName)); }
  get textContent() { return (this._text || "") + this.children.map((child) => child.textContent).join(""); }
  set textContent(text) { this._text = String(text); this.children = []; }
}
function descendants(root) { return root.children.flatMap((child) => [child, ...descendants(child)]); }

test("chip view renders selectable pins, restrictions and disabled function evidence without device controls", async () => {
  const app = harness({panel: true}); app.render(); await settle();
  assert.match(app.root.textContent, /STM32F446RE/); assert.match(app.root.textContent, /PA5/); assert.match(app.root.textContent, /Onboard LED/);
  assert.doesNotMatch(app.root.textContent, /Add BME280|Add LED|Load pins|Load board|Place module/);
  const pins = descendants(app.root).filter((item) => item.attributes.role === "button");
  assert.equal(pins.length, 2); pins[1].dispatch("click");
  assert.equal(pins[1].attributes["aria-pressed"], "true"); assert.match(app.root.textContent, /i2c1 · SCL/);
  const functions = descendants(app.root).filter((item) => item.className === "dte-chip-function");
  assert.equal(functions.length, 1); assert.equal(functions[0].attributes["aria-disabled"], "true");
  assert.match(app.root.textContent, /under construction/); app.dispose();
});

test("DTS GPIO slots are distinguished from package pin evidence without extra discovery actions", async () => {
  const app = harness({panel: true, api: async (_url, _options, response) => {
    const value = response();
    value.result.chip = {name: "nRF5340", package: "QKAA", inventory: "gpio-slots"};
    value.result.coverage = "board+selected-overlay";
    return value;
  }});
  app.render(); await settle();
  assert.equal(app.calls.length, 1);
  assert.match(app.state.chipView.message, /^2 GPIO slots observed from DTS\. Select a slot/);
  const nodes = descendants(app.root);
  assert.equal(nodes.find((item) => item.className === "dte-badge").textContent, "Logical GPIO slots");
  assert.equal(nodes.find((item) => item.tagName === "svg").attributes["aria-label"], "Chip GPIO slots");
  assert.match(nodes.find((item) => item.className?.split(" ").includes("dte-chip-legend")).textContent, /not confirmed package pads or board connections/);
  const evidence = nodes.find((item) => item.className === "dte-chip-evidence");
  assert.match(evidence.textContent, /GPIO controller slots from the selected DTS/);
  assert.match(evidence.textContent, /Package bonding and board wiring are not established/);
  assert.doesNotMatch(app.root.textContent, /Logical pin view|2 pins observed/);
  const search = nodes.find((item) => item.type === "search");
  search.focus(); search.value = "PB8"; search.dispatch("input");
  assert.match(app.root.textContent, /1 matching GPIO slots/);
  assert.equal(app.root.ownerDocument.activeElement, search);
  app.dispose();
});

test("automatic revision evidence distinguishes defaults from compatible requests and clears on target changes", async () => {
  let revision = {requested: null, effective: "0.14.0", match: "default"};
  const app = harness({panel: true, api: async (_url, _options, response) => {
    const value = response(); value.result.selection = {revision, board_overlays: revision ? ["/board/revision.overlay"] : []}; return value;
  }});
  app.render(); await settle();
  const notice = descendants(app.root).find((item) => item.className?.includes("dte-chip-revision"));
  assert.equal(notice.hidden, false); assert.equal(notice.textContent, "Board revision 0.14.0 selected automatically.");
  assert.equal(app.calls.length, 1);
  assert.match(app.root.textContent, /Board revision overlays: \/board\/revision.overlay/);
  revision = {requested: "0.8.0", effective: "0.7.0", match: "compatible"};
  app.state.data.id = "compatible-context"; app.render(); await settle();
  assert.equal(notice.textContent, "Board revision 0.7.0 selected for requested revision 0.8.0.");
  revision = {requested: "0.7.0", effective: "0.7.0", match: "exact"};
  app.state.data.id = "exact-context"; app.render(); await settle();
  assert.equal(notice.textContent, "Board revision 0.7.0.");
  revision = null; app.state.data.id = "unrevisioned-context"; app.render(); await settle();
  assert.equal(notice.hidden, true); assert.equal(notice.textContent, "");
  assert.doesNotMatch(app.root.textContent, /revision.overlay/); app.dispose();
});

test("changing from GPIO slots to a package table refreshes the inventory labels and preserves pin focus", async () => {
  let inventory = "gpio-slots";
  const app = harness({panel: true, api: async (_url, _options, response) => {
    const value = response(); value.result.chip.inventory = inventory; return value;
  }});
  app.render(); await settle();
  const initial = descendants(app.root).filter((item) => item.attributes.role === "button");
  initial[1].dispatch("click"); initial[1].focus();
  inventory = "pin-table"; app.state.draftView.entry.server.revision++;
  app.render(); await settle();
  assert.match(app.state.chipView.message, /^2 pins observed/);
  const nodes = descendants(app.root);
  assert.equal(nodes.find((item) => item.className === "dte-badge").textContent, "Logical pin view");
  assert.equal(nodes.find((item) => item.tagName === "svg").attributes["aria-label"], "Chip pins");
  assert.match(nodes.find((item) => item.className === "dte-chip-evidence").textContent, /selected Zephyr package pin table/);
  assert.doesNotMatch(app.root.textContent, /GPIO slots|Package bonding and board wiring/);
  const selected = nodes.find((item) => item.attributes.role === "button" && item.attributes["aria-pressed"] === "true");
  assert.equal(app.root.ownerDocument.activeElement, selected);
  assert.match(selected.attributes["aria-label"], /PB8/);
  app.dispose();
});

test("common function limitations appear once while unique pin reasons stay visible", async () => {
  const reason = "A shared function limitation.";
  const app = harness({panel: true, api: async (_url, _options, response) => {
    const value = response(); value.result.pins[0].functions = [
      {label: "SPI1 SCK", reason}, {label: "UART2 TX", reason}, {label: "I2C1 SCL", reason: "Requires open-drain configuration."}]; return value;
  }});
  app.render(); await settle();
  assert.equal(app.root.textContent.split(reason).length - 1, 1);
  assert.match(app.root.textContent, /Requires open-drain configuration/); app.dispose();
});

test("keyboard pin navigation retains diagram focus and search keeps its input", async () => {
  const app = harness({panel: true}); app.render(); await settle();
  const pins = descendants(app.root).filter((item) => item.attributes.role === "button"); pins[0].focus();
  pins[0].dispatch("keydown", {key: "ArrowRight"}); assert.equal(app.root.ownerDocument.activeElement, pins[1]);
  const search = descendants(app.root).find((item) => item.type === "search"); search.focus(); search.value = "i2c"; search.dispatch("input");
  assert.equal(pins[0].attributes["data-match"], "false"); assert.equal(pins[1].attributes["data-match"], "true");
  assert.equal(app.root.ownerDocument.activeElement, search); assert.match(app.root.textContent, /1 matching pins/); app.dispose();
});

test("automatic assignment refresh preserves the selected pin and equivalent keyboard focus", async () => {
  const app = harness({panel: true}); app.render(); await settle();
  let pins = descendants(app.root).filter((item) => item.attributes.role === "button");
  pins[1].dispatch("click"); pins[1].focus(); app.state.draftView.entry.server.revision++;
  app.render(); await settle(); pins = descendants(app.root).filter((item) => item.attributes.role === "button");
  assert.equal(pins[1].attributes["aria-pressed"], "true"); assert.equal(app.root.ownerDocument.activeElement, pins[1]); app.dispose();
});

test("unsupported and board-only observations offer retry without making discovery a prerequisite", async () => {
  for (const values of [{supported: false, reason: "Missing package source"}, {coverage: "board-only", assignment_reason: "Application includes are unresolved."}]) {
    const app = harness({panel: true, api: async (_url, _options, response) => {
      const result = response(); Object.assign(result.result, values); return result;
    }});
    app.render(); await settle();
    const retry = descendants(app.root).find((item) => item.tagName === "button" && item.textContent === "Retry discovery");
    assert.equal(retry.hidden, false); assert.equal(app.calls.length, 1);
    if (values.assignment_reason) assert.match(app.root.textContent, /Application includes are unresolved/);
    app.dispose();
  }
});

test("Fit chip respects both desktop height and narrow width while zoom remains available", async () => {
  assert.equal(chipFitSize(1000, 760), 760); assert.equal(chipFitSize(320, 650), 320);
  const app = harness({panel: true}); app.render(); await settle();
  const nodes = descendants(app.root); const viewport = nodes.find((item) => item.className === "dte-chip-viewport");
  const svg = nodes.find((item) => item.tagName === "svg");
  const fit = nodes.find((item) => item.tagName === "button" && item.textContent === "Fit chip");
  const zoom = nodes.find((item) => item.tagName === "button" && item.textContent === "Zoom in");
  app.root.ownerDocument.defaultView = {getComputedStyle: () => ({maxHeight: "760px"})};
  viewport.clientWidth = 1000; viewport.scrollTop = 40; viewport.scrollLeft = 20; fit.dispatch("click");
  assert.equal(svg.style.width, "760px"); assert.equal(svg.style.height, "760px"); assert.equal(viewport.style.height, "760px");
  assert.equal(viewport.scrollTop, 0); assert.equal(viewport.scrollLeft, 0);
  zoom.dispatch("click"); assert.equal(svg.style.width, "950px");
  viewport.clientWidth = 320; fit.dispatch("click"); assert.equal(svg.style.width, "320px"); assert.equal(viewport.style.height, "320px"); app.dispose();
});

for (const count of [144, 256]) test(`${count}-pin diagrams expand their body, fit and zoom while preserving keyboard selection`, async () => {
  const app = harness({panel: true, api: async (_url, _options, response) => {
    const value = response(); value.result.pins = Array.from({length: count}, (_, index) => ({id: `pin:${index}`, name: `Pin ${index}`}));
    return value;
  }});
  app.render(); await settle();
  const nodes = descendants(app.root); const svg = nodes.find((item) => item.tagName === "svg");
  const viewport = nodes.find((item) => item.className === "dte-chip-viewport");
  const body = nodes.find((item) => item.attributes.class === "dte-chip-body");
  const name = nodes.find((item) => item.attributes.class === "dte-chip-name");
  const [, , width, height] = svg.attributes.viewBox.split(" ").map(Number);
  const left = Number(body.attributes.x); const top = Number(body.attributes.y);
  const right = left + Number(body.attributes.width); const bottom = top + Number(body.attributes.height);
  assert.ok(width > 900); assert.equal(width, height);
  assert.equal(Number(name.attributes.x), (left + right) / 2);
  assert.ok(Math.abs(Number(name.attributes.y) - (top + bottom) / 2) <= 40);
  assert.ok(left >= 205 && top >= 205 && right + 205 <= width && bottom + 205 <= height);
  let pins = nodes.filter((item) => item.attributes.role === "button");
  assert.equal(pins.length, count);
  for (const pin of pins) {
    const line = pin.children.find((item) => item.tagName === "line");
    const x = Number(line.attributes.x1); const y = Number(line.attributes.y1);
    assert.ok(x === left || x === right || y === top || y === bottom, "every pin connects to the expanded chip body");
  }
  pins[0].focus(); pins[0].dispatch("keydown", {key: "End"});
  assert.equal(app.root.ownerDocument.activeElement, pins.at(-1));
  assert.equal(pins.at(-1).attributes["aria-pressed"], "true");
  const zoomIn = nodes.find((item) => item.tagName === "button" && item.textContent === "Zoom in");
  viewport.scrollLeft = 45; viewport.scrollTop = 80;
  for (let index = 0; index < 40 && !zoomIn.disabled; index++) zoomIn.dispatch("click");
  assert.equal(zoomIn.disabled, true);
  assert.ok(parseFloat(svg.style.width) / width >= 600 * 2.5 / 900, "maximum zoom retains the original chip's label scale");
  assert.equal(viewport.scrollLeft, 45); assert.equal(viewport.scrollTop, 80);
  const zoomedWidth = svg.style.width;
  app.state.draftView.entry.server.revision++; app.render(); await settle();
  pins = descendants(app.root).filter((item) => item.attributes.role === "button");
  assert.equal(app.root.ownerDocument.activeElement, pins.at(-1));
  assert.equal(pins.at(-1).attributes["aria-pressed"], "true");
  assert.equal(svg.style.width, zoomedWidth); assert.equal(viewport.scrollTop, 80);
  const fit = nodes.find((item) => item.tagName === "button" && item.textContent === "Fit chip");
  viewport.clientWidth = 320; fit.dispatch("click");
  assert.equal(svg.style.width, "320px"); assert.equal(viewport.scrollLeft, 0); assert.equal(viewport.scrollTop, 0);
  pins.at(-1).dispatch("keydown", {key: "ArrowRight"});
  assert.equal(app.root.ownerDocument.activeElement, pins[0]); app.dispose();
});

test("diagram labels deduplicate default and sleep assignments while inspector keeps both states", async () => {
  const app = harness({panel: true, api: async (_url, _options, response) => {
    const value = response(); value.result.pins[0].assignments.push({...value.result.pins[0].assignments[0], state: "sleep"}); return value;
  }});
  app.render(); await settle();
  const first = descendants(app.root).find((item) => item.attributes.role === "button");
  assert.equal(first.attributes["aria-label"].split("spi1 · SCK").length - 1, 1);
  const inspector = descendants(app.root).find((item) => item.className === "dte-chip-inspector");
  assert.match(inspector.textContent, /default/); assert.match(inspector.textContent, /sleep/); app.dispose();
});

test("passive resizing refits without resetting focus or scrolling and disposal disconnects observation", async () => {
  let observer; let disconnected = false;
  const view = {getComputedStyle: () => ({maxHeight: "760px"}),
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; observer = this; }
      observe() {}
      disconnect() { disconnected = true; }
    }};
  const app = harness({panel: true, view}); app.render(); await settle();
  const nodes = descendants(app.root); const viewport = nodes.find((item) => item.className === "dte-chip-viewport");
  const svg = nodes.find((item) => item.tagName === "svg"); const pin = nodes.find((item) => item.attributes.role === "button"); pin.focus();
  viewport.clientWidth = 320; viewport.scrollTop = 20; viewport.scrollLeft = 10;
  observer.callback([{contentRect: {width: 320}}]);
  assert.equal(svg.style.width, "320px"); assert.equal(viewport.scrollTop, 20); assert.equal(viewport.scrollLeft, 10);
  assert.equal(app.root.ownerDocument.activeElement, pin); app.dispose(); assert.equal(disconnected, true);
});

function editableObservation(state) {
  const pins = ["PA2", "PA3", "PB6", "PB7", "PB8", "PB9"].map((id) => ({id, name: id, assignments: [], restrictions: [], functions: []}));
  return {schema_version: 1, supported: true, chip: {name: "STM32F446"}, pins, context_id: state.data.id,
    expected_revision: state.data.revision, draft_id: state.draftView.entry.server.id,
    draft_revision: state.draftView.entry.server.revision, draft_sha256: state.draftView.entry.server.sha256,
    activation: {available: true, observation_id: "activation-1", gpio: {available: true, pins: ["PA2", "PB6"]}, controllers: [
      {id: "&uart0", kind: "uart", label: "UART0", status: "okay", available: true, current: {tx: "PA2", rx: "PA3"},
        signals: [{id: "tx", label: "TX", required: true, candidates: ["PA2", "PB6"]},
          {id: "rx", label: "RX", required: true, candidates: ["PA3", "PB7"]}]},
      {id: "&i2c1", kind: "i2c", label: "I2C1", status: "disabled", available: true, current: {},
        signals: [{id: "scl", label: "SCL", required: true, candidates: ["PB8"]},
          {id: "sda", label: "SDA", required: true, candidates: ["PB9"]}]},
      {id: "&spi1", kind: "spi", label: "SPI1", available: false, reason: "Chip-select state is ambiguous.", current: {},
        signals: [{id: "sck", label: "SCK", required: true, candidates: ["PB6"]}]}]}};
}

function assignmentHarness({command, mount = false} = {}) {
  const app = harness(); const state = app.state; const calls = []; let mounted;
  state.chipView = {data: editableObservation(state), busy: false, stale: false};
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    async command(value) {
      calls.push(value);
      if (command) return command(value, state);
      const current = state.draftView.entry;
      current.flight = {}; notify();
      await settle(); current.server.revision++; current.server.sha256 = "applied-hash";
      current.server.text = "generated route"; current.text = "generated route"; current.flight = null;
      notify(); return true;
    }};
  function notify() { if (mounted) mounted.render(state.chipView.data, selected()); else session.sync(); }
  const selected = () => state.chipView.data.pins.find((pin) => pin.id === chosen);
  let chosen = "PB6"; let session;
  const options = {state, workspace: () => "/workspace", drafts, onChange: notify};
  const root = mount ? new Element("section") : null;
  if (mount) { mounted = mountPinAssignment(root, options); session = mounted.session; }
  else session = createPinAssignmentSession(options);
  notify();
  return {state, session, calls, root, mounted, selected, notify,
    choose(pin) { chosen = pin; notify(); },
    dispose() { mounted?.dispose(); if (!mounted) session.dispose(); app.dispose(); }};
}

test("activation alternatives follow controller and pin capabilities with concrete unavailable reasons", () => {
  const app = assignmentHarness(); const choices = activationChoices(app.state.chipView.data, app.selected());
  assert.deepEqual(choices.map(({id, available}) => [id, available]), [["gpio", true], ["&uart0:tx", true], ["&spi1:sck", false]]);
  assert.match(choices.at(-1).reason, /Chip-select/);
  app.state.chipView.data.activation.controllers[0].signals[0].unavailable = {PB6: "Reserved by another active controller."};
  assert.equal(activationChoices(app.state.chipView.data, app.selected())[1].available, false);
  app.dispose();
});

test("selecting a signal preserves valid companions and changes no source before Apply", () => {
  const app = assignmentHarness();
  assert.equal(app.session.open(app.selected(), "&uart0:tx"), true);
  assert.deepEqual(app.session.view.proposal.signals, {tx: "PB6", rx: "PA3"});
  assert.equal(app.calls.length, 0); assert.equal(app.state.draftView.entry.text, "");
  assert.equal(app.session.cancel(), true); assert.equal(app.session.view.proposal, null); assert.equal(app.calls.length, 0);
  assert.match(app.session.view.message, /unchanged/); app.dispose();
});

test("complete route Apply emits one canonical command and accepts refreshed source history", async () => {
  const app = assignmentHarness(); app.session.open(app.selected(), "&uart0:tx");
  assert.equal(await app.session.apply(), true);
  assert.deepEqual(app.calls, [{kind: "chip_assign", observation_id: "activation-1", controller: "&uart0", signals: {tx: "PB6", rx: "PA3"}}]);
  assert.equal(app.state.draftView.entry.server.revision, 1); assert.equal(app.session.view.proposal, null);
  assert.match(app.session.view.message, /Undo or Review and Save/); app.dispose();
});

test("GPIO emits an application reference command with no invented runtime direction", async () => {
  const app = assignmentHarness(); app.session.open(app.selected(), "gpio");
  assert.equal(await app.session.apply(), true);
  assert.deepEqual(app.calls, [{kind: "chip_gpio", observation_id: "activation-1", pin: "PB6"}]); app.dispose();
});

test("incomplete routes remain local and never choose another pin silently", async () => {
  const app = assignmentHarness(); app.choose("PB8"); app.session.open(app.selected(), "&i2c1:scl");
  assert.deepEqual(app.session.view.proposal.signals, {scl: "PB8", sda: ""});
  assert.match(app.session.problem(), /Choose SDA/); assert.equal(await app.session.apply(), false); assert.equal(app.calls.length, 0);
  assert.equal(app.session.setSignal("sda", "PB6"), false); assert.equal(app.session.setSignal("sda", "PB9"), true);
  assert.equal(await app.session.apply(), true); assert.deepEqual(app.calls[0].signals, {scl: "PB8", sda: "PB9"}); app.dispose();
});

test("ambiguous duplicate pin selections and unavailable companions cannot Apply", async () => {
  const app = assignmentHarness(); const controller = app.state.chipView.data.activation.controllers[0];
  controller.signals[1].candidates.push("PB6"); app.session.open(app.selected(), "&uart0:tx");
  app.session.setSignal("rx", "PB6"); assert.match(app.session.problem(), /different pin/);
  assert.equal(await app.session.apply(), false); assert.equal(app.calls.length, 0); app.dispose();
});

test("invalid existing companions are left for an explicit replacement", () => {
  const app = assignmentHarness(); app.state.chipView.data.activation.controllers[0].current.rx = "Unknown";
  app.session.open(app.selected(), "&uart0:tx"); assert.equal(app.session.view.proposal.signals.rx, ""); app.dispose();
});

test("backend no-op keeps source history unchanged and reports it", async () => {
  const app = assignmentHarness({command: async () => true}); app.session.open(app.selected(), "&uart0:tx");
  assert.equal(await app.session.apply(), true); assert.equal(app.state.draftView.entry.server.revision, 0);
  assert.match(app.session.view.message, /already configured/); app.dispose();
});

for (const change of ["source", "revision", "observation", "context", "stale"]) test(`a ${change} change invalidates a pending pin proposal`, async () => {
  const app = assignmentHarness(); app.session.open(app.selected(), "&uart0:tx");
  if (change === "source") app.state.draftView.entry.text = "new typing";
  if (change === "revision") app.state.draftView.entry.server.revision++;
  if (change === "observation") app.state.chipView.data.activation.observation_id = "new-observation";
  if (change === "context") app.state.data.id = "other-context";
  if (change === "stale") app.state.chipView.stale = true;
  app.notify(); assert.equal(app.session.view.proposal, null); assert.equal(await app.session.apply(), false);
  assert.equal(app.calls.length, 0); app.dispose();
});

test("late assignment completion cannot publish success after a context switch", async () => {
  const waiting = deferred(); const app = assignmentHarness({command: async () => { await waiting.promise; return true; }});
  app.session.open(app.selected(), "gpio"); const pending = app.session.apply();
  app.state.data.id = "other-context"; app.notify(); waiting.resolve();
  assert.equal(await pending, false); assert.equal(app.session.view.message, ""); assert.equal(app.session.view.proposal, null); app.dispose();
});

test("rejected assignment exposes the draft error and preserves local selection without a false success", async () => {
  const app = assignmentHarness({command: async (_value, state) => { state.draftView.entry.error = "Pin is used by the console."; return false; }});
  app.session.open(app.selected(), "gpio"); assert.equal(await app.session.apply(), false);
  assert.match(app.session.view.error, /console/); assert.equal(app.session.view.message, "Assignment was not applied.");
  assert.equal(app.state.draftView.entry.text, ""); app.dispose();
});

test("assignment controls keep companion selections and focus across busy or passive rerenders", async () => {
  const waiting = deferred(); const app = assignmentHarness({mount: true, command: async () => { await waiting.promise; return true; }});
  const selector = descendants(app.root).find((item) => item.attributes["aria-label"] === "Pin function");
  selector.value = "&uart0:tx"; selector.dispatch("change");
  let fields = descendants(app.root).filter((item) => item.tagName === "select" && item !== selector);
  assert.deepEqual(fields.map((item) => item.value), ["PB6", "PA3"]);
  fields[1].focus(); app.notify(); assert.equal(app.root.ownerDocument.activeElement, fields[1]);
  const applying = app.session.apply(); assert.equal(fields[1].disabled, true); assert.equal(app.session.cancel(), false);
  app.notify(); assert.equal(app.root.ownerDocument.activeElement, fields[1]);
  waiting.resolve(); await applying; assert.equal(app.session.view.proposal, null); app.dispose();
});

test("Escape cancels a local assignment and restores function selection focus", () => {
  const app = assignmentHarness({mount: true}); const selector = descendants(app.root).find((item) => item.attributes["aria-label"] === "Pin function");
  selector.value = "gpio"; selector.dispatch("change");
  app.root.dispatch("keydown", {key: "Escape"});
  assert.equal(app.session.view.proposal, null); assert.equal(app.root.ownerDocument.activeElement, selector); assert.equal(app.calls.length, 0); app.dispose();
});

test("switching selected pins cancels the old local assignment form", () => {
  const app = assignmentHarness({mount: true}); app.session.open(app.selected(), "gpio");
  app.choose("PA2"); assert.equal(app.session.view.proposal, null); assert.equal(app.calls.length, 0); app.dispose();
});


test("command failures that require reconciliation retain their concrete error", async () => {
  const app = assignmentHarness({command: async (_value, state) => {
    state.draftView.entry.error = "The board source changed. Reconcile before another action.";
    state.draftView.entry.uncertain = true; return false;
  }});
  app.session.open(app.selected(), "gpio"); assert.equal(await app.session.apply(), false);
  assert.equal(app.session.view.proposal, null); assert.match(app.session.view.error, /board source changed/);
  assert.equal(app.session.view.message, "Assignment was not applied."); app.dispose();
});

test("submitting the visible assignment uses canonical source and keeps its action focus", async () => {
  const app = assignmentHarness({mount: true});
  const selector = descendants(app.root).find((item) => item.attributes["aria-label"] === "Pin function");
  selector.value = "gpio"; selector.dispatch("change");
  const apply = descendants(app.root).find((item) => item.tagName === "button" && item.textContent === "Apply assignment");
  const form = descendants(app.root).find((item) => item.tagName === "form");
  apply.focus(); form.dispatch("submit"); await settle(); await settle();
  assert.deepEqual(app.calls, [{kind: "chip_gpio", observation_id: "activation-1", pin: "PB6"}]);
  assert.equal(app.root.ownerDocument.activeElement, apply); assert.equal(form.hidden, true);
  assert.equal(apply.parentNode.hidden, false);
  assert.match(app.root.textContent, /Assignment applied/); app.dispose();
});

test("GPIO recommendations identify the chosen pin without blocking assignment or repeating notes", () => {
  const app = assignmentHarness({mount: true}); const activation = app.state.chipView.data.activation;
  const recommendation = "Use standard drive and signals up to 10 kHz.";
  activation.gpio.notes = [recommendation, "Enable CONFIG_GPIO."];
  activation.pin_notes = {PB6: [recommendation, recommendation], PA2: ["An unselected pin note."]};
  app.session.open(app.selected(), "gpio");
  assert.match(app.root.textContent, /PB6: Use standard drive/);
  assert.equal(app.root.textContent.split(recommendation).length - 1, 1);
  assert.doesNotMatch(app.root.textContent, /unselected pin note/);
  const apply = descendants(app.root).find((item) => item.tagName === "button" && item.textContent === "Apply assignment");
  assert.equal(apply.attributes["aria-disabled"], "false"); assert.equal(app.session.problem(), ""); app.dispose();
});

test("route recommendations follow all chosen companion pins while preserving control focus", () => {
  const app = assignmentHarness({mount: true}); const activation = app.state.chipView.data.activation;
  const recommendation = "Use standard drive and signals up to 10 kHz.";
  activation.controllers[0].notes = ["Enable CONFIG_SERIAL."];
  activation.pin_notes = {PB6: [recommendation], PA3: [recommendation], PB7: ["Check this pin's load."]};
  app.session.open(app.selected(), "&uart0:tx");
  assert.match(app.root.textContent, /PB6, PA3: Use standard drive/);
  assert.equal(app.root.textContent.split(recommendation).length - 1, 1);
  const rx = descendants(app.root).find((item) => item.attributes["aria-label"] === "RX");
  rx.focus(); rx.value = "PB7"; rx.dispatch("change");
  assert.match(app.root.textContent, /PB6: Use standard drive/);
  assert.match(app.root.textContent, /PB7: Check this pin's load/);
  assert.doesNotMatch(app.root.textContent, /PA3: Use standard drive/);
  assert.equal(app.root.ownerDocument.activeElement, rx); assert.equal(app.session.problem(), "");
  assert.equal(app.calls.length, 0); app.dispose();
});

test("Apply remains focusable through busy and completed states while repeat clicks are guarded", async () => {
  const waiting = deferred(); const app = assignmentHarness({mount: true, command: async (_value, state) => {
    await waiting.promise; state.draftView.entry.server.revision++; return true;
  }});
  app.session.open(app.selected(), "gpio");
  const apply = descendants(app.root).find((item) => item.tagName === "button" && item.textContent === "Apply assignment");
  // Native buttons lose focus when disabled; model that browser behavior for this action.
  Object.defineProperty(apply, "disabled", {get() { return Boolean(this._disabled); }, set(value) {
    this._disabled = value;
    if (value && this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null;
  }});
  apply.focus(); apply.dispatch("click");
  assert.equal(apply.attributes["aria-disabled"], "true"); assert.equal(apply.disabled, false);
  assert.equal(app.root.ownerDocument.activeElement, apply); assert.equal(apply.textContent, "Applying…");
  apply.dispatch("click"); assert.equal(app.calls.length, 1);
  waiting.resolve(); await settle();
  assert.equal(apply.attributes["aria-disabled"], "true"); assert.equal(apply.disabled, false);
  assert.equal(app.root.ownerDocument.activeElement, apply); assert.equal(apply.textContent, "Applied to draft");
  apply.dispatch("click"); assert.equal(app.calls.length, 1); app.dispose();
});

test("focusable Apply rejects incomplete proposals without a source command", () => {
  const app = assignmentHarness({mount: true}); app.choose("PB8"); app.session.open(app.selected(), "&i2c1:scl");
  const apply = descendants(app.root).find((item) => item.tagName === "button" && item.textContent === "Apply assignment");
  assert.equal(apply.attributes["aria-disabled"], "true"); apply.focus(); apply.dispatch("click");
  assert.equal(app.calls.length, 0); assert.equal(app.root.ownerDocument.activeElement, apply);
  assert.match(app.session.problem(), /Choose SDA/); app.dispose();
});
