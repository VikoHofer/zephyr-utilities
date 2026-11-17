import test from "node:test";
import assert from "node:assert/strict";
import {createProjectsNavigation} from "../../src/zephyr_utilities/features/projects/web/index.js";
import {applicationHandoff, resolveApplicationHandoff} from "../../src/zephyr_utilities/features/projects/web/creator-view.js";
import {createCreatorSession} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";

function harness() {
  const environment = {hidden: false};
  const creator = {hidden: true};
  const opened = [];
  function open(kind) {
    const instance = {kind, views: [], disposals: 0,
      navigate(view) { this.views.push(view); },
      dispose() { this.disposals++; }};
    opened.push(instance);
    return instance;
  }
  const navigation = createProjectsNavigation({environment, creator,
    openEnvironment: () => open("environment"), openCreator: () => open("creator")});
  return {navigation, environment, creator, opened};
}

test("Project Creator starts with saved applications", () => {
  const app = harness();
  app.navigation.navigate();
  assert.equal(app.environment.hidden, true);
  assert.equal(app.creator.hidden, false);
  assert.equal(app.opened.length, 1);
  assert.deepEqual(app.opened[0].views, ["applications"]);
  app.navigation.dispose();
});

test("applications and creation share the same session when changing sidebar views", () => {
  const app = harness();
  app.navigation.navigate("applications");
  app.navigation.navigate("create");
  app.navigation.navigate("applications");
  assert.equal(app.opened.length, 1, "switching creator pages must not add polling sessions");
  assert.equal(app.opened[0].disposals, 0);
  assert.deepEqual(app.opened[0].views, ["applications", "create", "applications"]);
  app.navigation.dispose();
});

test("environment transitions dispose the prior session and reopening gets a fresh session", () => {
  const app = harness();
  app.navigation.navigate("create");
  app.navigation.navigate("environment");
  assert.equal(app.opened[0].disposals, 1);
  assert.equal(app.environment.hidden, false);
  assert.equal(app.creator.hidden, true);
  app.navigation.navigate("applications");
  assert.equal(app.opened[1].disposals, 1);
  assert.equal(app.opened[2].kind, "creator");
  assert.equal(app.environment.hidden, true);
  assert.equal(app.creator.hidden, false);
  app.navigation.dispose();
  assert.equal(app.opened[2].disposals, 1);
});

test("same-view workspace notifications reach the live session, but disposal stops navigation", () => {
  const app = harness();
  app.navigation.navigate("applications");
  app.navigation.navigate("applications");
  assert.deepEqual(app.opened[0].views, ["applications", "applications"]);
  app.navigation.dispose();
  app.navigation.dispose();
  app.navigation.navigate("environment");
  assert.equal(app.opened.length, 1);
  assert.equal(app.opened[0].disposals, 1);
});

test("editor handoff routes retain the selected application, configuration and attempt", () => {
  const app = harness(); const project = "a".repeat(32); const configuration = "b".repeat(32); const attempt = "c".repeat(32);
  const route = `applications/${project}/${configuration}/${attempt}`;
  app.navigation.navigate(route);
  assert.deepEqual(app.opened[0].views, [route]);
  assert.deepEqual(applicationHandoff(route), {project_id: project, configuration_id: configuration, attempt_id: attempt});
  assert.deepEqual(applicationHandoff(`applications/${project}/original`), {project_id: project, configuration_id: null, attempt_id: null});
  assert.equal(applicationHandoff("applications/../../environment"), null); app.navigation.dispose();
});

test("demo creation handoff routes retain their capability within the creator session", () => {
  const app = harness();
  const route = `create/${"a".repeat(32)}`;
  app.navigation.navigate("applications");
  app.navigation.navigate(route);
  assert.equal(app.opened.length, 1);
  assert.deepEqual(app.opened[0].views, ["applications", route]);
  assert.equal(app.creator.hidden, false);
  assert.equal(app.environment.hidden, true);
  app.navigation.dispose();
});

function delayedCreator() {
  const pending = []; const state = {}; const workspace = "/workspace";
  const session = createCreatorSession({state, workspace: () => workspace, onChange: () => {},
    schedule: () => 1, unschedule: () => {}, api: async () => new Promise((resolve, reject) => pending.push({resolve, reject}))});
  const handoff = {project_id: "a".repeat(32), configuration_id: "b".repeat(32), attempt_id: "c".repeat(32)};
  const project = {id: handoff.project_id, workspace, profile: {},
    configurations: [{id: handoff.configuration_id, profile: {}, name: "Debug"}],
    attempts: [{id: handoff.attempt_id, configuration: {id: handoff.configuration_id}}]};
  const response = (projects = [project]) => ({workspace, projects, jobs: [], templates: [], snapshot: null});
  return {session, state, pending, handoff, project, response};
}

test("handoff awaits its own catalogue load when initial mount is still loading", async () => {
  const app = delayedCreator();
  const initialLoad = app.session.load();
  assert.equal(app.state.loading, true);
  let settled = false;
  const navigation = resolveApplicationHandoff({...app, current: () => true}).then((value) => { settled = true; return value; });
  assert.equal(app.pending.length, 2, "the handoff must not rely on a skipped polling refresh");
  app.pending[0].resolve(app.response([])); await initialLoad;
  assert.equal(settled, false); assert.equal(app.state.loading, true);
  app.pending[1].resolve(app.response());
  const resolved = await navigation;
  assert.equal(resolved.project.id, app.handoff.project_id);
  assert.equal(resolved.attempt.id, app.handoff.attempt_id);
  assert.equal(app.state.configurationSelections[app.handoff.project_id], app.handoff.configuration_id);
  assert.equal(app.state.buildPlan, null); app.session.dispose();
});

test("an older initial response cannot erase the handoff's loaded selection", async () => {
  const app = delayedCreator(); const initialLoad = app.session.load();
  const navigation = resolveApplicationHandoff({...app, current: () => true});
  app.pending[1].resolve(app.response()); await navigation;
  app.pending[0].resolve(app.response([])); await initialLoad;
  assert.equal(app.state.projects[0].id, app.handoff.project_id);
  assert.equal(app.state.configurationSelections[app.handoff.project_id], app.handoff.configuration_id);
  app.session.dispose();
});

test("leaving during handoff load suppresses selection of a late destination", async () => {
  const app = delayedCreator(); let current = true;
  const navigation = resolveApplicationHandoff({...app, current: () => current});
  current = false; app.pending[0].resolve(app.response());
  assert.equal(await navigation, null);
  assert.equal(app.state.configurationSelections[app.handoff.project_id], undefined); app.session.dispose();
});

test("failed handoff refresh does not use an older cached application list", async () => {
  const app = delayedCreator(); const initialLoad = app.session.load();
  app.pending[0].resolve(app.response()); await initialLoad;
  const navigation = resolveApplicationHandoff({...app, current: () => true});
  app.pending[1].reject(new Error("Catalogue unavailable"));
  await assert.rejects(navigation, /Catalogue unavailable/);
  assert.equal(app.state.configurationSelections[app.handoff.project_id], undefined); app.session.dispose();
});
