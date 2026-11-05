import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession, selectedConfiguration} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";

const workspace = "/workspace/one";
const project = (id = "app") => ({id, name: "my_app", workspace, path: `${workspace}/applications/my_app`,
  profile: {target: "board/chip"}, configurations: [{id: "alternate", name: "Prototype", project_id: id, workspace, profile: {target: "other/chip"}}],
  attempts: [{id: "attempt", project_id: id, status: "built", configuration: {id: "alternate", name: "Prototype"}, logs: ["Saved evidence"]}]});
const response = (projects = [project()], unavailable = []) => ({workspace, projects, unavailable_projects: unavailable, jobs: []});
function harness(api, state = {workspace, projects: [project()], jobs: []}) {
  let selectedWorkspace = workspace;
  const session = createCreatorSession({api, state, workspace: () => selectedWorkspace, onChange: () => {}, schedule: () => 1, unschedule: () => {}});
  return {session, state, switchWorkspace: (path) => { selectedWorkspace = path; }};
}
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }

test("missing and replaced records stay separate and cannot receive a build preview", async () => {
  const calls = [];
  const app = harness(async (path) => {
    calls.push(path);
    return response([project("current")], [{...project("old"), availability: "replaced"}, {...project("missing"), availability: "missing"}, {...project("foreign"), workspace: "/elsewhere"}]);
  });
  await app.session.load();
  assert.deepEqual(app.state.projects.map((item) => item.id), ["current"]);
  assert.deepEqual(app.state.unavailableProjects.map((item) => item.id), ["old", "missing"]);
  await app.session.previewBuild("old", "build");
  assert.deepEqual(calls, ["/api/projects/creator"]);
  app.session.dispose();
});

test("removing a saved application clears its selections, draft, history and job display", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.body]); return {removed: "app"}; });
  app.session.selectConfiguration("app", "alternate");
  app.session.editConfiguration("app");
  app.state.form.name = "unfinished_app";
  app.state.configurationFiles.app = {ready: true};
  app.state.buildPlan = {id: "review", project_id: "app"};
  app.state.jobs = [
    {id: "build", status: "completed", metadata: {project_id: "app", attempt_id: "attempt"}},
    {id: "create", status: "completed", result: {project: {id: "app"}}},
    {id: "other", status: "completed", metadata: {project_id: "another"}},
  ];
  assert.equal(await app.session.removeProject("app"), true);
  assert.deepEqual(calls, [["/api/projects/remove", {project_id: "app", workspace}]]);
  assert.deepEqual(app.state.projects, []);
  assert.deepEqual(app.state.configurationSelections, {});
  assert.deepEqual(app.state.configurationDrafts, {});
  assert.deepEqual(app.state.configurationFiles, {});
  assert.equal(app.state.configurationEditor, "");
  assert.equal(app.state.buildPlan, null);
  assert.equal(app.state.form.name, "unfinished_app");
  assert.deepEqual(app.state.jobs.map((item) => item.id), ["other"]);
  assert.equal(app.state.needsReload, true);
  assert.match(app.state.removalNotice, /files remain on disk/);
  app.session.dispose();
});

test("unavailable saved entries can be removed without affecting the current same-path app", async () => {
  const app = harness(async () => ({removed: "old"}), {workspace, projects: [project("current")], unavailableProjects: [{...project("old"), availability: "replaced"}], jobs: []});
  assert.equal(await app.session.removeProject("old"), true);
  assert.deepEqual(app.state.unavailableProjects, []);
  assert.deepEqual(app.state.projects.map((item) => item.id), ["current"]);
  app.session.dispose();
});

test("removing a selected configuration falls back to Original while preserving captured attempts", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.body]); return {removed: "alternate"}; });
  app.session.selectConfiguration("app", "alternate");
  app.session.editConfiguration("app");
  const captured = structuredClone(app.state.projects[0].attempts);
  app.state.buildPlan = {project_id: "app", configuration: {id: "alternate"}};
  assert.equal(await app.session.removeConfiguration("app", "alternate"), true);
  assert.deepEqual(calls, [["/api/projects/configurations/remove", {project_id: "app", configuration_id: "alternate", workspace}]]);
  assert.deepEqual(app.state.projects[0].configurations, []);
  assert.equal(selectedConfiguration(app.state, app.state.projects[0]).name, "Original");
  assert.deepEqual(app.state.projects[0].attempts, captured);
  assert.equal(app.state.buildPlan, null);
  assert.equal(app.state.configurationEditor, "");
  assert.deepEqual(app.state.configurationDrafts, {});
  app.session.dispose();
});

test("removing terminal build history removes only that entry and its completed job", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.body]); return {removed: "attempt"}; });
  app.state.projects[0].attempts.push({id: "other-attempt", status: "interrupted"});
  app.state.jobs = [{id: "completed-build", status: "completed", metadata: {attempt_id: "attempt"}}];
  assert.equal(await app.session.removeAttempt("app", "attempt"), true);
  assert.deepEqual(calls, [["/api/projects/attempts/remove", {project_id: "app", attempt_id: "attempt", workspace}]]);
  assert.deepEqual(app.state.projects[0].attempts.map((item) => item.id), ["other-attempt"]);
  assert.equal(app.state.projects[0].configurations.length, 1);
  assert.deepEqual(app.state.jobs, []);
  assert.match(app.state.removalNotice, /output files remain/);
  app.session.dispose();
});

test("original configuration, unknown records and active work cannot be removed", async () => {
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return {removed: "app"}; });
  assert.equal(await app.session.removeConfiguration("app", ""), false);
  assert.equal(await app.session.removeConfiguration("app", "unknown"), false);
  assert.equal(await app.session.removeProject("unknown"), false);
  assert.equal(await app.session.removeAttempt("app", "unknown"), false);
  app.state.projects[0].attempts[0].status = "running";
  assert.equal(await app.session.removeAttempt("app", "attempt"), false);
  app.state.jobs = [{id: "running", status: "running", metadata: {project_id: "app"}}];
  assert.equal(await app.session.removeProject("app"), false);
  assert.equal(await app.session.removeConfiguration("app", "alternate"), false);
  assert.deepEqual(calls, []);
  app.session.dispose();
});

test("failed or mismatched removal responses preserve local entries and drafts", async () => {
  for (const result of ["failure", "mismatch"]) {
    const app = harness(async () => {
      if (result === "failure") throw new Error("A build is still running.");
      return {removed: "another-app"};
    });
    app.session.editConfiguration("app");
    app.session.changeConfigurationField("app", "name", "Unfinished");
    app.state.buildPlan = {project_id: "app"};
    assert.equal(await app.session.removeProject("app"), false);
    assert.equal(app.state.projects.length, 1);
    assert.equal(app.state.configurationDrafts.app.name, "Unfinished");
    assert.equal(app.state.buildPlan, null);
    assert.equal(app.state.busy, "");
    assert.ok(app.state.error);
    app.session.dispose();
  }
});

test("a stale background response cannot restore a removed application or configuration", async () => {
  for (const kind of ["project", "configuration", "attempt"]) {
    const pending = deferred();
    const id = {project: "app", configuration: "alternate", attempt: "attempt"}[kind];
    const app = harness(async (path) => path.endsWith("/remove") ? {removed: id} : pending.promise);
    const old = app.session.load({background: true});
    const remove = kind === "project" ? app.session.removeProject("app") : kind === "configuration"
      ? app.session.removeConfiguration("app", "alternate") : app.session.removeAttempt("app", "attempt");
    assert.equal(await remove, true);
    pending.resolve(response()); await old;
    assert.equal(kind === "project" ? app.state.projects.length : kind === "configuration" ? app.state.projects[0].configurations.length : app.state.projects[0].attempts.length, 0);
    app.session.dispose();
  }
});

test("late removals cannot change another workspace or a disposed session", async () => {
  for (const departure of ["workspace", "dispose"]) {
    const pending = deferred();
    const app = harness(async (path) => path.endsWith("/remove") ? pending.promise : {workspace: "/workspace/two", projects: [project("other")], jobs: []});
    const removal = app.session.removeProject("app");
    if (departure === "workspace") { app.switchWorkspace("/workspace/two"); await app.session.load(); }
    else app.session.dispose();
    const before = structuredClone(app.state);
    pending.resolve({removed: "app"});
    assert.equal(await removal, false);
    assert.deepEqual(app.state, before);
    app.session.dispose();
  }
});

test("reopening reloads cleanup state without replaying a removal", async () => {
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return {removed: "app"}; });
  await app.session.removeProject("app");
  app.session.dispose();
  const reopened = harness(async (path) => { calls.push(path); return response([], [{...project("old"), availability: "missing"}]); }, app.state);
  await reopened.session.load();
  assert.deepEqual(reopened.state.projects, []);
  assert.equal(reopened.state.unavailableProjects[0].id, "old");
  assert.deepEqual(calls, ["/api/projects/remove", "/api/projects/creator"]);
  assert.equal(reopened.state.removalNotice, "");
  reopened.session.dispose();
});
