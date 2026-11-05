import test from "node:test";
import assert from "node:assert/strict";
import {createResultNavigation, createReviewDialog} from "../../src/zephyr_utilities/features/projects/web/creator-view.js";
import {createCreatorSession} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";

function dialogHarness() {
  const disclosures = [{open: true}, {open: true}];
  const dialog = {open: false, openings: 0, closings: 0,
    querySelectorAll() { return disclosures; },
    showModal() { this.open = true; this.openings++; },
    close() { this.open = false; this.closings++; }};
  return {dialog, disclosures, review: createReviewDialog(dialog)};
}

test("review opens only after an explicit show and resets technical disclosures", () => {
  const app = dialogHarness();
  const plan = {id: "reviewed"};
  app.review.sync(plan, true);
  assert.equal(app.dialog.open, false, "background state alone must not open or focus a modal");
  app.review.show(plan);
  assert.equal(app.dialog.open, true);
  assert.deepEqual(app.disclosures.map((item) => item.open), [false, false]);
  app.disclosures[0].open = true;
  app.review.sync(plan, true);
  app.review.show(plan);
  assert.equal(app.dialog.openings, 1, "rerendering must not reopen or reset focus");
  assert.equal(app.disclosures[0].open, true, "polling preserves the user's disclosure selection");
});

test("expired, replaced and hidden reviews close without reopening", () => {
  for (const [plan, visible] of [[null, true], [{id: "replacement"}, true], [{id: "reviewed"}, false]]) {
    const app = dialogHarness();
    app.review.show({id: "reviewed"});
    app.review.sync(plan, visible);
    assert.equal(app.dialog.open, false);
    assert.equal(app.dialog.closings, 1);
    app.review.sync({id: "reviewed"}, true);
    assert.equal(app.dialog.open, false);
    app.review.close();
    assert.equal(app.dialog.closings, 1, "closing during disposal is idempotent");
  }
});

test("closing a review restores its initiating control after asynchronous disabling and rerendering", () => {
  const app = dialogHarness();
  const focused = [];
  const control = (id) => ({isConnected: true, disabled: false, closest: () => null, focus: (options) => focused.push([id, options])});
  let trigger = control("original");
  trigger.disabled = true;
  app.review.show({id: "reviewed"}, () => trigger);
  trigger.isConnected = false;
  trigger = control("replacement");
  app.review.sync(null, true);
  assert.deepEqual(focused, [["replacement", {preventScroll: true}]], "resolve the current card button without jumping the page");
  app.review.close();
  assert.equal(focused.length, 1, "a later rerender must not steal focus again");
});

test("closing during navigation, execution or disposal does not focus an unavailable trigger", () => {
  for (const reason of ["disconnected", "disabled", "hidden", "dispose"]) {
    const app = dialogHarness();
    const focused = [];
    const trigger = {isConnected: reason !== "disconnected", disabled: reason === "disabled", closest: () => reason === "hidden" ? {} : null,
      focus: () => focused.push(true)};
    app.review.show({id: "reviewed"}, trigger);
    app.review.close({restoreFocus: reason !== "dispose"});
    assert.deepEqual(focused, [], reason);
  }
});

const workspace = "/workspace";
function harness(api) {
  const state = {workspace, templates: [{id: "minimal-c", version: 1, title: "Minimal C", description: "Minimal app", language: "C"}],
    projects: [{id: "app", workspace, profile: {}, configurations: []}], jobs: []};
  const session = createCreatorSession({api, state, workspace: () => workspace, onChange: () => {}, schedule: () => 1, unschedule: () => {}});
  return {session, state};
}
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }

function resultHarness() {
  const pending = deferred();
  const state = {workspace, view: "create", disposed: false, target: null, error: ""};
  const shown = [], notices = [];
  let reads = 0;
  const navigation = createResultNavigation({workspace: () => state.workspace, view: () => state.view, disposed: () => state.disposed,
    find: (id) => state.target?.id === id ? state.target : null, reveal: (target) => shown.push(target),
    refresh: () => { reads++; return pending.promise; }, error: () => state.error, notify: (...args) => notices.push(args)});
  return {state, shown, notices, navigation, pending, reads: () => reads,
    navigate(view) { state.view = view; navigation.navigate(view); navigation.show(); }};
}

test("a requested result survives its destination navigation and reveals once after refresh", async () => {
  const app = resultHarness();
  app.navigation.request("job");
  app.navigate("applications");
  const refresh = app.navigation.refresh();
  app.state.target = {id: "job"};
  app.pending.resolve(); await refresh;
  app.navigation.show();
  assert.equal(app.reads(), 1);
  assert.deepEqual(app.shown, [{id: "job"}]);
  assert.equal(app.navigation.hasPending(), false);
  assert.deepEqual(app.notices, []);
});

test("an unavailable destination result clears after one refresh and reports why", async () => {
  for (const error of ["", "Could not read application records."]) {
    const app = resultHarness();
    app.navigation.request("deleted-job");
    app.navigate("applications");
    const refresh = app.navigation.refresh();
    app.state.error = error;
    app.pending.resolve(); await refresh;
    assert.equal(app.navigation.hasPending(), false);
    assert.equal(app.notices.length, 1);
    assert.match(app.notices[0][0], error ? /Could not read/ : /no longer available/);
    assert.equal(app.notices[0][1], Boolean(error));
    app.state.target = {id: "deleted-job"};
    app.navigation.show();
    assert.deepEqual(app.shown, [], "a later background load cannot revive a finished reveal request");
  }
});

test("leaving during a result refresh cancels both delayed focus and missing-result feedback", async () => {
  const app = resultHarness();
  app.navigation.request("job");
  app.navigate("applications");
  const refresh = app.navigation.refresh();
  app.navigate("create");
  app.state.target = {id: "job"};
  app.pending.resolve(); await refresh;
  app.navigate("applications");
  assert.equal(app.navigation.hasPending(), false);
  assert.deepEqual(app.shown, []);
  assert.deepEqual(app.notices, []);
});

test("workspace changes discard a requested result instead of revealing matching foreign IDs", async () => {
  const app = resultHarness();
  app.navigation.request("job");
  app.navigate("applications");
  const refresh = app.navigation.refresh();
  app.state.workspace = "/another-workspace";
  app.state.target = {id: "job"};
  app.navigate("applications");
  app.pending.resolve(); await refresh;
  assert.equal(app.navigation.hasPending(), false);
  assert.deepEqual(app.shown, []);
  assert.deepEqual(app.notices, []);
});

test("cancelling either review invalidates its capability without submitting an operation", async () => {
  const calls = [];
  const app = harness(async (path) => { calls.push(path); });
  app.state.plan = {id: "creation", workspace};
  app.state.buildPlan = {id: "build", workspace, project_id: "app"};
  app.session.dismissReview("create");
  app.session.dismissReview("build");
  await app.session.create();
  await app.session.build();
  assert.equal(app.state.plan, null);
  assert.equal(app.state.buildPlan, null);
  assert.deepEqual(calls, []);
  app.session.dispose();
});

test("a late preview cannot restore a dismissed review", async () => {
  for (const kind of ["create", "build"]) {
    const pending = deferred();
    const app = harness(async () => pending.promise);
    const preview = kind === "create" ? app.session.preview() : app.session.previewBuild("app", "configure");
    app.session.dismissReview(kind);
    pending.resolve({plan: {id: "late", workspace, project_id: "app"}});
    await preview;
    assert.equal(kind === "create" ? app.state.plan : app.state.buildPlan, null);
    assert.equal(app.state.busy, "");
    app.session.dispose();
  }
});
