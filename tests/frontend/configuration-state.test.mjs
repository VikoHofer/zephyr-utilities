import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession, selectedConfiguration} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";

const workspace = "/workspace/one";
const profile = (target = "board/chip", sdk = "sdk-one") => ({target, board_id: "board-one", sdk: {id: sdk, path: `/sdk/${sdk}`}});
const configuration = (id = "alternate", name = "Prototype", selectedProfile = profile("board/chip/ns", "sdk-two")) => ({id, name, workspace, project_id: "app", profile: selectedProfile});
const project = (configurations = [configuration()]) => ({id: "app", name: "demo", workspace, path: `${workspace}/applications/demo`, profile: profile(), configurations, attempts: []});
const snapshot = () => ({id: "snapshot", fingerprint: "first", workspace: {path: workspace}, status: "complete", boards: [
  {id: "board-one", name: "board", status: "discovered", targets: ["board/chip", "board/chip/ns"]},
  {id: "board-two", name: "other", status: "discovered", targets: ["other/chip"]},
], sdks: ["sdk-one", "sdk-two"].map((id) => ({id, status: "discovered", path: `/sdk/${id}`, toolchains: [{family: "GNU", compiler: `/sdk/${id}/gcc`}]}))});
const response = (projects = [project()]) => ({workspace, snapshot: snapshot(), projects, jobs: []});
const review = (id = "alternate") => ({id: "review", workspace, project_id: "app", configuration: {id, name: "Prototype"}, profile: profile("board/chip/ns", "sdk-two")});
function harness(api, state = {}) {
  let selectedWorkspace = workspace;
  const session = createCreatorSession({api, state, workspace: () => selectedWorkspace, onChange: () => {}, schedule: () => 1, unschedule: () => {}});
  return {session, state, switchWorkspace: (path) => { selectedWorkspace = path; }};
}
function readyState() { return {workspace, snapshot: snapshot(), projects: [project()], jobs: []}; }
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }

test("configuration selection chooses exact saved target and SDK without changing the original", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.body]); return {plan: review()}; }, readyState());
  assert.equal(selectedConfiguration(app.state, app.state.projects[0]).name, "Original");
  app.session.selectConfiguration("app", "alternate");
  assert.equal(selectedConfiguration(app.state, app.state.projects[0]).profile.target, "board/chip/ns");
  assert.equal(app.state.projects[0].profile.target, "board/chip");
  assert.deepEqual(calls, [], "selection alone has no server side effect");
  await app.session.previewBuild("app", "build");
  assert.deepEqual(calls, [["/api/projects/build-preview", {workspace, project_id: "app", operation: "build", configuration_id: "alternate"}]]);
  assert.equal(app.state.buildPlan.configuration.id, "alternate");
  app.session.selectConfiguration("app", "");
  assert.equal(app.state.buildPlan, null);
  app.session.dispose();
});

test("saving a named configuration persists only explicit metadata and selects the result", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.body]); return {configuration: configuration("saved", "Prototype")}; }, readyState());
  app.session.selectConfiguration("app", "alternate");
  app.session.editConfiguration("app");
  assert.equal(app.state.configurationDrafts.app.target, "board/chip/ns");
  assert.equal(app.state.configurationDrafts.app.sdk_id, "sdk-two");
  app.session.changeConfigurationField("app", "name", " Prototype ");
  app.session.changeConfigurationField("app", "search", "chip");
  app.state.buildPlan = review();
  await app.session.saveConfiguration("app");
  assert.deepEqual(calls, [["/api/projects/configurations", {workspace, project_id: "app", name: "Prototype", board_id: "board-one", target: "board/chip/ns", sdk_id: "sdk-two"}]]);
  assert.equal(app.state.configurationSelections.app, "saved");
  assert.equal(app.state.configurationEditor, "");
  assert.equal(app.state.configurationDrafts.app, undefined);
  assert.equal(app.state.buildPlan, null);
  assert.equal(app.state.jobs.length, 0);
  assert.equal(app.state.needsReload, true);
  assert.match(app.state.configurationNotice.message, /saved/);
  app.session.dispose();
});

test("changing board clears a draft target and configuration drafts never modify the create form", () => {
  const app = harness(async () => response(), readyState());
  const initialForm = {...app.state.form};
  app.session.editConfiguration("app");
  app.session.changeConfigurationField("app", "board_id", "board-two");
  assert.equal(app.state.configurationDrafts.app.target, "");
  assert.equal(app.state.configurationDrafts.app.sdk_id, "sdk-one");
  assert.deepEqual(app.state.form, initialForm);
  app.session.dispose();
});

test("a late build review cannot restore the previous configuration after selection changes", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise, readyState());
  app.session.selectConfiguration("app", "alternate");
  const preview = app.session.previewBuild("app", "configure");
  app.session.selectConfiguration("app", "");
  pending.resolve({plan: review()}); await preview;
  assert.equal(app.state.buildPlan, null);
  assert.equal(app.state.configurationSelections.app, "");
  app.session.dispose();
});

test("changed or missing saved configurations invalidate their reviewed command", async () => {
  let returned = response();
  const app = harness(async () => returned, readyState());
  app.session.selectConfiguration("app", "alternate");
  app.state.buildPlan = review();
  returned = response([project([configuration("alternate", "Prototype", profile("other/chip"))])]);
  await app.session.refresh();
  assert.equal(app.state.buildPlan, null, "a modified persisted profile requires a fresh review");
  app.state.buildPlan = review();
  returned = response([project([])]);
  await app.session.refresh();
  assert.equal(app.state.buildPlan, null);
  assert.equal(app.state.configurationSelections.app, "");
  assert.match(app.state.configurationNotice.message, /no longer available/);
  app.session.dispose();
});

test("refreshing a removed app clears its configuration draft and selection", async () => {
  const app = harness(async () => response([]), readyState());
  app.session.selectConfiguration("app", "alternate"); app.session.editConfiguration("app");
  await app.session.refresh();
  assert.deepEqual(app.state.configurationSelections, {});
  assert.deepEqual(app.state.configurationDrafts, {});
  assert.equal(app.state.configurationEditor, "");
  app.session.dispose();
});

test("configuration selection and draft survive remount but clear across workspaces", async () => {
  const app = harness(async () => response(), readyState());
  app.session.selectConfiguration("app", "alternate"); app.session.editConfiguration("app");
  app.session.changeConfigurationField("app", "name", "Unfinished name");
  app.session.dispose();
  const reopened = harness(async () => response(), app.state);
  await reopened.session.load();
  assert.equal(reopened.state.configurationSelections.app, "alternate");
  assert.equal(reopened.state.configurationDrafts.app.name, "Unfinished name");
  assert.equal(reopened.state.configurationEditor, "app");
  reopened.switchWorkspace("/workspace/two");
  await reopened.session.load();
  assert.deepEqual(reopened.state.configurationSelections, {});
  assert.deepEqual(reopened.state.configurationDrafts, {});
  assert.equal(reopened.state.configurationEditor, "");
  reopened.session.dispose();
});

test("saved metadata from a late response does not replace a newer draft or selection", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise, readyState());
  app.session.editConfiguration("app"); app.session.changeConfigurationField("app", "name", "First");
  const save = app.session.saveConfiguration("app");
  app.session.changeConfigurationField("app", "name", "Second");
  app.session.selectConfiguration("app", "alternate");
  pending.resolve({configuration: configuration("saved", "First")}); await save;
  assert.equal(app.state.configurationSelections.app, "alternate");
  assert.equal(app.state.configurationDrafts.app.name, "Second");
  assert.equal(app.state.configurationEditor, "app");
  assert.equal(app.state.projects[0].configurations.length, 2, "successful metadata save remains discoverable");
  app.session.dispose();
});

test("a late save from another workspace cannot insert or select its configuration", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("configurations") ? pending.promise : {workspace: "/workspace/two", projects: [], jobs: []}, readyState());
  app.session.editConfiguration("app"); app.session.changeConfigurationField("app", "name", "First");
  const save = app.session.saveConfiguration("app");
  app.switchWorkspace("/workspace/two"); await app.session.load();
  pending.resolve({configuration: configuration("saved", "First")}); await save;
  assert.deepEqual(app.state.projects, []);
  assert.deepEqual(app.state.configurationSelections, {});
  assert.equal(app.state.configurationNotice, null);
  app.session.dispose();
});

test("configuration failures preserve the draft and clear unsafe review actions", async () => {
  const app = harness(async () => { throw new Error("This name is already used."); }, readyState());
  app.session.editConfiguration("app"); app.session.changeConfigurationField("app", "name", "Prototype");
  app.state.buildPlan = review();
  await app.session.saveConfiguration("app");
  assert.equal(app.state.configurationDrafts.app.name, "Prototype");
  assert.equal(app.state.configurationEditor, "app");
  assert.equal(app.state.buildPlan, null);
  assert.equal(app.state.busy, "");
  assert.match(app.state.error, /name is already used/);
  app.session.dispose();
});

test("refreshing environment choices clears unavailable draft inputs and reviews", async () => {
  const changed = snapshot(); changed.fingerprint = "second"; changed.boards = []; changed.sdks = [];
  const app = harness(async () => ({...response(), snapshot: changed}), readyState());
  app.session.editConfiguration("app"); app.session.changeConfigurationField("app", "name", "Prototype");
  app.state.buildPlan = review();
  await app.session.refresh();
  assert.equal(app.state.configurationDrafts.app.name, "Prototype");
  assert.equal(app.state.configurationDrafts.app.board_id, "");
  assert.equal(app.state.configurationDrafts.app.target, "");
  assert.equal(app.state.configurationDrafts.app.sdk_id, "");
  assert.equal(app.state.buildPlan, null);
  app.session.dispose();
});

test("an old background read cannot discard a newly saved configuration", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("configurations") ? {configuration: configuration("saved", "Prototype")} : pending.promise, readyState());
  app.session.editConfiguration("app"); app.session.changeConfigurationField("app", "name", "Prototype");
  const load = app.session.load({background: true});
  await app.session.saveConfiguration("app");
  pending.resolve(response()); await load;
  assert.equal(app.state.configurationSelections.app, "saved");
  assert.equal(app.state.projects[0].configurations.length, 2);
  app.session.dispose();
});
