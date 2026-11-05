import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession, creatorTemplateLabel, selectedConfiguration} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";
import {managementSummary} from "../../src/zephyr_utilities/features/projects/web/management-view.js";
import {outputActions} from "../../src/zephyr_utilities/features/projects/web/output-view.js";

const workspace = "/workspace";
const profile = () => ({schema_version: 1, workspace, board_id: "board", target: "board/chip", sdk: {id: "sdk", path: "/sdk"}});
const configuration = () => ({id: "alternate", name: "Debug", project_id: "app", workspace, revision: 3, profile: profile()});
const project = () => ({id: "app", name: "my_app", path: `${workspace}/applications/my_app`, workspace, profile_revision: 2, profile: profile(),
  configurations: [configuration()], attempts: [{id: "attempt", project_id: "app", status: "built", reuse_allowed: true, configuration: {id: null, name: "Original"}, profile: profile()}],
  build_outputs: [{id: "output", path: `${workspace}/applications/my_app/build/output`, latest_attempt_id: "attempt", state: "available", clean_allowed: true, reuse_allowed: true}]});
const snapshot = () => ({workspace: {path: workspace}, fingerprint: "environment", status: "complete", boards: [{id: "board", name: "board", status: "discovered", targets: ["board/chip", "board/other"]}],
  sdks: [{id: "sdk", status: "discovered", path: "/sdk", toolchains: [{family: "GNU", compiler: "/sdk/gcc"}]}]});
const plan = (changes = {}) => ({id: "review", workspace, profile: profile(), files: [], ...changes});
const response = (projects = [project()]) => ({workspace, snapshot: snapshot(), projects, jobs: []});
function harness(api, state = {workspace, projects: [project()], snapshot: snapshot(), jobs: []}) {
  const calls = [];
  let selectedWorkspace = workspace;
  const session = createCreatorSession({api: async (path, options) => { calls.push([path, options.body]); return api(path, options); },
    state, workspace: () => selectedWorkspace, onChange() {}, schedule: () => 1, unschedule() {}});
  return {state, session, calls, switchWorkspace(path) { selectedWorkspace = path; }};
}
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }
const importFields = () => ({path: " applications/existing ", name: " Existing ", search: "board", board_id: "board", target: "board/chip", sdk_id: "sdk"});

test("import first reviews existing inputs, then saves only the accepted plan", async () => {
  const imported = {...project(), id: "imported", name: "Existing", path: `${workspace}/applications/existing`, template: {id: "imported", version: 1}};
  const app = harness(async (path) => path.endsWith("import-preview") ? {plan: plan({path: imported.path, name: imported.name})} : {project: imported});
  app.session.management.open("import", importFields());
  assert.deepEqual(app.calls, []);
  assert.equal(await app.session.management.apply(), false);
  assert.equal(await app.session.management.preview(), true);
  assert.deepEqual(app.calls[0], ["/api/projects/import-preview", {path: "applications/existing", name: "Existing", board_id: "board", target: "board/chip", sdk_id: "sdk", workspace}]);
  assert.equal(app.state.projects.length, 1);
  assert.equal(await app.session.management.apply(), true);
  assert.deepEqual(app.calls[1], ["/api/projects/import", {workspace, plan_id: "review"}]);
  assert.equal(app.state.projects[0].id, "imported");
  assert.equal(app.state.management, null);
  assert.equal(app.state.needsReload, true);
  assert.equal(creatorTemplateLabel(imported.template, []), "Imported existing application");
  app.session.dispose();
});

test("editing import fields invalidates an existing and a delayed review", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise);
  app.session.management.open("import", importFields());
  const preview = app.session.management.preview();
  app.session.management.close();
  pending.resolve({plan: plan()}); await preview;
  assert.equal(app.state.management, null);
  app.session.management.open("import", {...importFields(), path: "another"});
  assert.equal(app.state.management.plan, null);
  app.state.management.plan = plan();
  app.session.management.change("board_id", "other-board");
  assert.equal(app.state.management.plan, null);
  assert.equal(app.state.management.fields.target, "");
  assert.equal(await app.session.management.apply(), false);
  app.session.dispose();
});

test("Back to choices preserves input fields and removes the apply capability", async () => {
  const app = harness(async () => ({plan: plan({path: "/workspace/existing"})}));
  app.session.management.open("import", importFields());
  await app.session.management.preview();
  const fields = {...app.state.management.fields};
  app.session.management.edit();
  assert.deepEqual(app.state.management.fields, fields);
  assert.equal(app.state.management.plan, null);
  assert.equal(await app.session.management.apply(), false);
  assert.equal(app.calls.length, 1);
  app.session.dispose();
});

test("reconnect preserves saved history and configurations while moving unavailable record to the live list", async () => {
  const previous = project();
  const rawUpdated = {id: previous.id, workspace, name: previous.name, path: `${workspace}/moved-app`, profile: previous.profile, revision: 4};
  const app = harness(async (path) => path.endsWith("reconnect-preview") ? {plan: plan({project_id: "app", old_path: previous.path, path: rawUpdated.path})} : {project: rawUpdated},
    {workspace, snapshot: snapshot(), projects: [], unavailableProjects: [previous], jobs: []});
  app.session.management.open("reconnect", {project_id: "app", path: rawUpdated.path});
  await app.session.management.preview(); await app.session.management.apply();
  assert.deepEqual(app.calls.map(([path]) => path), ["/api/projects/reconnect-preview", "/api/projects/reconnect"]);
  assert.equal(app.state.projects[0].path, rawUpdated.path);
  assert.deepEqual(app.state.projects[0].attempts, previous.attempts);
  assert.deepEqual(app.state.projects[0].configurations, previous.configurations);
  assert.deepEqual(app.state.unavailableProjects, []);
  app.session.dispose();
});

test("reconnect review distinguishes a moved original from a replacement and unknown file observations", () => {
  const summary = Object.fromEntries(managementSummary("reconnect", {old_path: "/old", path: "/new", same_directory: false,
    file_changes: {added: ["boards/new.overlay"], removed: ["boards/old.overlay"], changed: ["prj.conf", "src/main.c"]}}));
  assert.equal(summary["Directory identity"], "Different directory");
  assert.equal(summary["Files added"], 1);
  assert.equal(summary["Files removed"], 1);
  assert.equal(summary["Files changed"], 2);
  const moved = Object.fromEntries(managementSummary("reconnect", {same_directory: true}));
  assert.equal(moved["Directory identity"], "Same directory / moved original");
  assert.equal(moved["Files changed"], "Unknown");
  assert.equal(Object.fromEntries(managementSummary("reconnect", {}))["Directory identity"], "Unknown");
});

test("editing Original captures its own revision and requires a review before changing the profile", async () => {
  const newProfile = {...profile(), target: "board/other"};
  const updated = {...project(), profile: newProfile, profile_revision: 3};
  const app = harness(async (path) => path.endsWith("configurations/preview") ? {plan: plan({project_id: "app", mode: "original", configuration: {id: null, name: "Original"}, profile: newProfile})}
    : {project: updated, configuration: {id: null, name: "Original", revision: 3, profile: newProfile}});
  app.session.editConfiguration("app", {editSelected: true});
  assert.equal(app.state.configurationDrafts.app.name, "Original");
  app.session.changeConfigurationField("app", "target", "board/other");
  await app.session.previewConfiguration("app");
  assert.deepEqual(app.calls[0], ["/api/projects/configurations/preview", {workspace, project_id: "app", configuration_id: "", expected_revision: 2,
    name: "Original", board_id: "board", target: "board/other", sdk_id: "sdk", create_default_overlay: false}]);
  assert.equal(app.state.projects[0].profile.target, "board/chip");
  await app.session.management.apply();
  assert.equal(app.state.projects[0].profile.target, "board/other");
  assert.equal(selectedConfiguration(app.state, app.state.projects[0]).revision, 3);
  assert.equal(app.state.configurationEditor, "");
  assert.equal(app.state.configurationDrafts.app, undefined);
  assert.equal(app.state.projects[0].attempts[0].profile.target, "board/chip", "captured attempts are not rewritten by a profile edit");
  app.session.dispose();
});

test("named configurations support rename and optional reviewed default overlay files", async () => {
  const app = harness(async () => ({plan: plan({mode: "update", project_id: "app", configuration: {id: "alternate", name: "Renamed"},
    files: [{path: "boards/demo.overlay", before: null, content: "/* default */\n", sha256: "hash"}]})}));
  app.session.selectConfiguration("app", "alternate");
  app.session.editConfiguration("app", {editSelected: true});
  app.session.changeConfigurationField("app", "name", "Renamed");
  app.session.changeConfigurationField("app", "create_default_overlay", true);
  await app.session.previewConfiguration("app");
  assert.equal(app.calls[0][1].configuration_id, "alternate");
  assert.equal(app.calls[0][1].expected_revision, 3);
  assert.equal(app.calls[0][1].create_default_overlay, true);
  assert.equal(app.state.management.plan.files[0].content, "/* default */\n");
  assert.equal(app.state.projects[0].configurations[0].name, "Debug");
  app.session.management.close();
  assert.equal(app.calls.length, 1, "closing the review never applies its optional source write");
  app.session.dispose();
});

test("adding a configuration omits edit identity and defaults to no source-file creation", async () => {
  const app = harness(async () => ({plan: plan({mode: "create", configuration: {id: "new", name: "Release"}})}));
  app.session.editConfiguration("app");
  app.session.changeConfigurationField("app", "name", "Release");
  await app.session.previewConfiguration("app");
  assert.equal(Object.hasOwn(app.calls[0][1], "configuration_id"), false);
  assert.equal(Object.hasOwn(app.calls[0][1], "expected_revision"), false);
  assert.equal(app.calls[0][1].create_default_overlay, false);
  app.session.dispose();
});

test("fresh and reused builds submit distinct reviewed requests and ineligible reuse clears the old review", async () => {
  const app = harness(async (_path, options) => ({plan: plan({project_id: "app", operation: options.body.operation, reuse: Boolean(options.body.reuse_attempt_id)})}));
  await app.session.previewBuild("app", "build");
  assert.equal(Object.hasOwn(app.calls[0][1], "reuse_attempt_id"), false);
  await app.session.previewBuild("app", "configure", "attempt");
  assert.equal(app.calls[1][1].reuse_attempt_id, "attempt");
  assert.equal(app.state.buildPlan.reuse, true);
  app.state.projects[0].attempts[0].reuse_allowed = false;
  await app.session.previewBuild("app", "build", "attempt");
  assert.equal(app.calls.length, 2);
  assert.equal(app.state.buildPlan, null);
  assert.match(app.state.error, /no longer eligible/);
  app.session.dispose();
});

test("output cleanup supports orphan output IDs, reviews counts and starts a separate shared job", async () => {
  const app = harness(async (path) => path.endsWith("outputs/preview") ? {plan: plan({build_dir: "/workspace/app/build/owned", output_id: "orphan", bytes: 1234, file_count: 5, directory_count: 2, symlink_count: 0, shared_attempt_ids: []})}
    : {id: "cleanup", status: "queued", metadata: {workspace, operation: "clean-output", project_id: "app", output_id: "orphan"}});
  app.state.projects[0].attempts = [];
  app.session.management.open("clean", {project_id: "app", output_id: "orphan"});
  assert.equal(await app.session.management.apply(), false);
  await app.session.management.preview();
  assert.deepEqual(app.calls[0], ["/api/projects/outputs/preview", {workspace, project_id: "app", output_id: "orphan"}]);
  assert.ok(managementSummary("clean", app.state.management.plan).some(([name, value]) => name === "Files" && value === 5));
  await app.session.management.apply();
  assert.deepEqual(app.calls[1], ["/api/projects/outputs/clean", {workspace, plan_id: "review"}]);
  assert.equal(app.state.jobs[0].metadata.operation, "clean-output");
  assert.equal(app.state.projects[0].build_outputs.length, 1, "accepting cleanup is not the same as completed deletion");
  app.session.dispose();
});

test("output controls depend on explicit backend eligibility, not directory-looking names or history", () => {
  assert.deepEqual(outputActions({state: "available", path: "/build", latest_attempt_id: "attempt"}), {reuse: false, clean: false});
  assert.deepEqual(outputActions({clean_allowed: true, reuse_allowed: true}), {reuse: false, clean: true});
  assert.deepEqual(outputActions({clean_allowed: true, reuse_allowed: true, latest_attempt_id: "attempt"}), {reuse: true, clean: true});
  assert.deepEqual(outputActions({clean_allowed: false, reuse_allowed: false, state: "cleaned", latest_attempt_id: "attempt"}), {reuse: false, clean: false});
});

test("preview/apply failures retain form inputs, invalidate review and preserve saved records", async () => {
  let failing = false;
  const app = harness(async () => { if (failing) throw new Error("The folder changed after review."); return {plan: plan({path: "/workspace/new"})}; });
  const original = structuredClone(app.state.projects);
  app.session.management.open("reconnect", {project_id: "app", path: "/workspace/new"});
  await app.session.management.preview();
  failing = true;
  assert.equal(await app.session.management.apply(), false);
  assert.deepEqual(app.state.projects, original);
  assert.equal(app.state.management.plan, null);
  assert.equal(app.state.management.fields.path, "/workspace/new");
  assert.match(app.state.error, /folder changed/);
  assert.equal(app.state.busy, "");
  app.session.dispose();
});

test("malformed or foreign reviews cannot authorize an apply", async () => {
  for (const malformed of [{}, {plan: {id: "foreign", workspace: "/other"}}, {plan: {workspace}}]) {
    const app = harness(async () => malformed);
    app.session.management.open("import", importFields());
    assert.equal(await app.session.management.preview(), false);
    assert.equal(await app.session.management.apply(), false);
    assert.equal(app.calls.length, 1);
    assert.match(app.state.error, /could not be verified/);
    app.session.dispose();
  }
});

test("management actions cannot start during active work or another pending request", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise);
  app.state.jobs = [{id: "busy", status: "running"}];
  assert.equal(app.session.management.open("import", importFields()), false);
  app.state.jobs = [];
  app.session.management.open("import", importFields());
  const preview = app.session.management.preview();
  await app.session.management.preview();
  assert.equal(app.calls.length, 1);
  assert.equal(app.session.management.open("reconnect", {project_id: "app", path: "elsewhere"}), false);
  pending.resolve({plan: plan()}); await preview;
  app.session.dispose();
});

test("a late background read cannot undo a newly reconnected project", async () => {
  const pending = deferred();
  const app = harness(async (path) => path === "/api/projects/creator" ? pending.promise : path.endsWith("reconnect-preview") ? {plan: plan()}
    : {project: {...project(), path: "/workspace/new-location"}});
  const read = app.session.load({background: true});
  app.session.management.open("reconnect", {project_id: "app", path: "/workspace/new-location"});
  await app.session.management.preview(); await app.session.management.apply();
  pending.resolve(response()); await read;
  assert.equal(app.state.projects[0].path, "/workspace/new-location");
  app.session.dispose();
});

test("workspace switches and disposal discard late management results without replaying writes", async () => {
  for (const departure of ["workspace", "dispose"]) {
    const pending = deferred();
    const app = harness(async (path) => path.endsWith("import-preview") ? pending.promise : {workspace: "/other", projects: [], jobs: []});
    app.session.management.open("import", importFields());
    const preview = app.session.management.preview();
    if (departure === "workspace") { app.switchWorkspace("/other"); await app.session.load(); }
    else app.session.dispose();
    pending.resolve({plan: plan()});
    assert.equal(await preview, false);
    assert.equal(app.state.management?.plan || null, null);
    assert.equal(app.calls.some(([path]) => path === "/api/projects/import"), false);
    app.session.dispose();
  }
});
