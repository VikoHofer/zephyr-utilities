import test from "node:test";
import assert from "node:assert/strict";
import {createOverlayView, overlayUsagePresentation, selectedOverlayUsage} from "../../src/zephyr_utilities/features/projects/web/overlay-view.js";

const target = "demo/chip";
const file = (path, state, role = "application", reason = "Recorded selection reason.") => ({path, state, role, reason});
const expected = () => ({schema_version: 1, target, status: "expected", message: "Expected from automatic discovery.",
  files: [file("boards/demo_chip.overlay", "expected", "default"), file("boards/other_chip.overlay", "not_used", "board_specific"),
    file("upstream/basic-blinky/boards/demo_chip.overlay", "not_used", "preserved")], board_files: [], evidence: {kind: "creation"}});
const confirmed = () => ({...expected(), status: "confirmed", message: "Captured Devicetree inputs.",
  files: [file("boards/demo_chip.overlay", "active", "default"), file("boards/other_chip.overlay", "not_used", "board_specific")],
  board_files: [file("/zephyr/boards/vendor/demo/demo_chip.dts", "active", "board_source"), file("/shared/hardware.overlay", "active", "external_overlay")],
  evidence: {kind: "attempt", attempt_id: "attempt", configuration_name: "Debug", checked_at: "2026-11-05T09:00:00Z"}});

test("expected overlay selection separates the generated default from other-board and preserved files", () => {
  const report = expected();
  const before = structuredClone(report);
  const view = overlayUsagePresentation(report, {target});
  assert.equal(view.title, "Expected overlays");
  assert.deepEqual(view.main.map((item) => [item.path, item.stateLabel, item.roleLabel]), [["boards/demo_chip.overlay", "Expected", "Default overlay"]]);
  assert.deepEqual(view.other.map((item) => [item.stateLabel, item.roleLabel]), [["Not used", "Board-specific overlay"], ["Not used", "Preserved original"]]);
  assert.match(view.note, /Configure records the actual/);
  assert.deepEqual(report, before, "presentation must not rewrite captured evidence");
});

test("confirmed inputs distinguish application overlays, external overlays and base board source", () => {
  const view = overlayUsagePresentation(confirmed());
  assert.equal(view.title, "Overlays used in this attempt");
  assert.deepEqual(view.main.map((item) => [item.path, item.stateLabel]), [["boards/demo_chip.overlay", "Used"], ["/shared/hardware.overlay", "Used"]]);
  assert.equal(view.main[1].roleLabel, "External overlay");
  assert.equal(view.boards[0].roleLabel, "Board source");
  assert.match(view.note, /Captured for this operation/);
  assert.match(view.note, /Configure again after editing inputs/);
  assert.equal(view.evidence.configuration_name, "Debug");
});

test("attempts marked stale retain historical usage and explicitly require current reconfiguration", () => {
  const view = overlayUsagePresentation(confirmed(), {freshness: "stale"});
  assert.equal(view.main[0].state, "active");
  assert.match(view.note, /inputs changed during this attempt/);
  assert.match(view.note, /Configure again after editing inputs/);
});

test("specific evidence notes replace generic notes without duplicating reconfiguration advice", () => {
  for (const status of ["expected", "confirmed", "unknown"]) {
    const report = {...confirmed(), status};
    const note = status === "confirmed" ? "Captured for this attempt. Configure again after changing application or board files."
      : status === "expected" ? "Based on files recorded at creation. Configure confirms the current selection."
      : "This attempt did not capture overlay inputs. Configure again to record them.";
    report.evidence.note = ` ${note} `;
    assert.equal(overlayUsagePresentation(report).note, note);
    assert.equal(overlayUsagePresentation(report, {freshness: "stale"}).note, `Application inputs changed during this attempt. ${note}`);
  }
});

test("other-board files with unknown transitive use retain their mismatch explanation", () => {
  const report = expected();
  const reason = "Board-specific file does not match the selected target. Transitive use needs configuration evidence.";
  report.files[1] = file("boards/other_chip.overlay", "unknown", "board_specific", reason);
  const row = overlayUsagePresentation(report).other.find((item) => item.path === "boards/other_chip.overlay");
  assert.equal(row.stateLabel, "Unknown");
  assert.equal(row.roleLabel, "Board-specific overlay");
  assert.equal(row.reason, reason);
});

test("compatibility-check capture does not become a normal application's current build evidence", () => {
  const report = confirmed(); report.evidence.kind = "compatibility-check";
  const view = overlayUsagePresentation(report);
  assert.equal(view.title, "Overlays used in compatibility check");
  assert.match(view.note, /not live file checks/);
});

test("a default explicitly excluded from a profile stays visible as not used", () => {
  const report = expected();
  report.files[0] = file("boards/demo_chip.overlay", "not_used", "default", "The replacement selection omits this default.");
  report.files.push(file("custom.overlay", "expected"));
  const view = overlayUsagePresentation(report);
  assert.deepEqual(view.main.map((item) => item.stateLabel), ["Not used", "Expected"]);
  assert.match(view.main[0].reason, /replacement selection/);
});

test("selected configuration reports never fall back to another target's original evidence", () => {
  const original = expected();
  const alternate = {...expected(), target: "other/chip"};
  const project = {overlay_usage_by_configuration: {"": original, alternate}};
  assert.equal(selectedOverlayUsage(project, null), original);
  assert.equal(selectedOverlayUsage(project, "alternate"), alternate);
  assert.equal(selectedOverlayUsage(project, "deleted"), null);
  assert.equal(selectedOverlayUsage(project, "toString"), null);
  assert.equal(overlayUsagePresentation(selectedOverlayUsage(project, "alternate"), {target}).status, "unknown");
});

test("missing, mismatched and malformed evidence stays unknown without invented file usage", () => {
  const reports = [null, {...expected(), schema_version: 99}, {...expected(), target: "other/chip"}, {...expected(), status: "built"},
    {...expected(), files: [{path: "app.overlay"}]}, {...expected(), board_files: "board.dts"}];
  for (const report of reports) {
    const view = overlayUsagePresentation(report, {target});
    assert.equal(view.status, "unknown");
    assert.deepEqual(view.main, []);
    assert.match(view.note, /Run Configure/);
  }
});

test("unconfirmed reports cannot label any input as actually used", () => {
  const report = confirmed(); report.status = "expected";
  const predicted = overlayUsagePresentation(report);
  assert.equal(predicted.main.some((item) => item.state === "active"), false);
  assert.equal(predicted.boards.some((item) => item.state === "active"), false);
  report.status = "unknown";
  const unknown = overlayUsagePresentation(report);
  assert.equal(unknown.main[0].stateLabel, "Unknown");
  assert.equal(unknown.boards[0].stateLabel, "Unknown");
});

class Element {
  constructor(tag, className, text) {
    this.tagName = tag.toUpperCase(); this.className = className || ""; this.textContent = text === undefined ? "" : String(text);
    this.children = []; this.dataset = {}; this.open = false;
    this.classList = {add: (name) => { this.className += ` ${name}`; }};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll(selector) {
    assert.equal(selector, "details[open][data-overlay-group]");
    return descendants(this).filter((item) => item.tagName === "DETAILS" && item.open && item.dataset.overlayGroup);
  }
  set innerHTML(_value) { throw new Error("Imported paths must be rendered as text."); }
}
function descendants(element) { return element.children.flatMap((child) => [child, ...descendants(child)]); }
const node = (tag, className, text) => new Element(tag, className, text);

test("main overlay stays visible while other copied overlays and board sources start collapsed", () => {
  const root = node("div");
  const view = createOverlayView({node});
  view.render(root, confirmed(), {target, disclosureKey: "attempt:one"});
  const details = descendants(root).filter((item) => item.tagName === "DETAILS");
  assert.deepEqual(details.map((item) => [item.dataset.overlayGroup, item.open]), [["other", false], ["boards", false]]);
  const visibleList = root.children.find((item) => item.tagName === "UL");
  assert.equal(visibleList.children.length, 2);
  assert.equal(descendants(visibleList).some((item) => item.textContent === "boards/other_chip.overlay"), false);
  assert.equal(details[0].dataset.disclosure, "attempt:one:overlays:other");
  details[0].open = true;
  view.render(root, {...confirmed(), message: "Updated observation message."}, {target, disclosureKey: "attempt:one"});
  assert.equal(descendants(root).find((item) => item.dataset.overlayGroup === "other").open, true);
});

test("overlay paths, target names and reasons remain literal text", () => {
  const report = expected();
  report.target = "<script>target</script>";
  report.files[0].path = "<img src=x onerror=alert(1)>.overlay";
  report.files[0].reason = "<b>not markup</b>";
  const root = node("div");
  createOverlayView({node}).render(root, report);
  const text = descendants(root).map((item) => item.textContent);
  assert.ok(text.includes(report.target));
  assert.ok(text.includes(report.files[0].path));
  assert.ok(text.includes(report.files[0].reason));
});
