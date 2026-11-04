import test from "node:test";
import assert from "node:assert/strict";
import {createProjectsNavigation} from "../../src/zephyr_utilities/features/projects/web/index.js";

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
