import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession, creatorBody, creatorBoards, creatorSdks, creatorTemplates, creatorTemplateId, creatorTemplateLabel, selectedCreatorTemplate, currentCreatorJob} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";

const one = "/workspace/one";
const two = "/workspace/two";
const templates = [
  {id: "minimal-c", version: 1, title: "Minimal C", description: "A small C application.", language: "C"},
  {id: "minimal-cpp", version: 1, title: "Minimal C++", description: "A small C++ application.", language: "C++"},
];
const snapshot = (path = one, fingerprint = "baseline") => ({id: "observation", fingerprint, workspace: {path}, status: "complete", boards: [
  {id: "board-one", name: "alpha_kit", full_name: "Alpha Kit", status: "discovered", socs: ["alpha33"], targets: ["alpha_kit/alpha33", "alpha_kit@2/alpha33/ns"]},
  {id: "conflict", name: "duplicate", status: "conflict", targets: ["duplicate/beta"]},
], socs: [{name: "alpha33", family: "Alpha"}], sdks: [
  {id: "sdk-one", status: "discovered", path: "/sdk/one", toolchains: [{family: "GNU", name: "arm-zephyr-eabi", compiler: "/sdk/one/gcc"}]},
  {id: "incomplete", status: "incomplete", toolchains: [{family: "GNU", name: "arm-zephyr-eabi", compiler: "/sdk/missing-config/gcc"}]},
  {id: "llvm", status: "discovered", toolchains: [{family: "LLVM", name: "llvm", compiler: "/sdk/clang"}]},
]});
const project = (path = one) => ({id: "project-one", name: "demo", workspace: path, path: `${path}/applications/demo`, attempts: []});
const response = (path = one, jobs = []) => ({workspace: path, snapshot: snapshot(path), projects: [project(path)], jobs, templates});
const form = {name: "demo", destination: "applications/demo", search: "", board_id: "board-one", target: "alpha_kit@2/alpha33/ns", sdk_id: "sdk-one"};
function harness(api, state = {}, pendingBoard) {
  if (!Object.hasOwn(state, "templates")) state.templates = templates;
  let path = one;
  const scheduled = new Map();
  let nextTimer = 0;
  let publications = 0;
  const session = createCreatorSession({api, state, workspace: () => path, pendingBoard, onChange: () => publications++, schedule: (callback) => { const id = ++nextTimer; scheduled.set(id, callback); return id; }, unschedule: (id) => scheduled.delete(id)});
  return {session, state, scheduled, changeWorkspace(value) { path = value; }, publications: () => publications};
}
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }

test("creator offers discovered targets and installed GNU SDKs without inventing compatibility", () => {
  assert.deepEqual(creatorBoards(snapshot(), "KIT @2").map((board) => board.id), ["board-one"]);
  assert.deepEqual(creatorSdks(snapshot()).map((sdk) => sdk.id), ["sdk-one"]);
  assert.deepEqual(creatorBody({...form, name: " demo ", search: "UI only"}), {name: "demo", destination: "applications/demo", board_id: "board-one", target: "alpha_kit@2/alpha33/ns", sdk_id: "sdk-one"});
});

test("legacy forms keep the C default while explicit C++ reaches the preview request", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push(options.body); return {plan: {id: "review", workspace: one}}; }, {workspace: one, snapshot: snapshot(), form: {...form}});
  assert.equal(creatorTemplateId(app.state.form), "minimal-c");
  assert.equal(selectedCreatorTemplate(app.state).language, "C");
  assert.deepEqual(creatorBody({...form, template_id: "minimal-c"}), creatorBody(form));
  app.session.changeField("template_id", "minimal-cpp");
  await app.session.preview();
  assert.equal(calls[0].template_id, "minimal-cpp");
  assert.equal(selectedCreatorTemplate(app.state).language, "C++");
  app.session.dispose();
});

test("switching template clears the review and discards a late previous-template preview", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise, {workspace: one, snapshot: snapshot(), form: {...form}});
  app.state.plan = {id: "old", workspace: one};
  const preview = app.session.preview();
  app.session.changeField("template_id", "minimal-cpp");
  assert.equal(app.state.plan, null);
  pending.resolve({plan: {id: "late-c", workspace: one, template: {id: "minimal-c", version: 1}}});
  await preview;
  assert.equal(app.state.plan, null);
  app.session.dispose();
});

test("template choice survives workspace round trips and remount without restoring a review", async () => {
  let returnedPath = one;
  const app = harness(async () => response(returnedPath), {workspace: one, form: {...form, template_id: "minimal-cpp"}});
  app.changeWorkspace(two); returnedPath = two; await app.session.load();
  assert.equal(creatorTemplateId(app.state.form), "minimal-c");
  app.changeWorkspace(one); returnedPath = one; await app.session.load();
  assert.equal(creatorTemplateId(app.state.form), "minimal-cpp");
  app.state.plan = {id: "old", workspace: one};
  app.session.dispose();
  const reopened = harness(async () => response(), app.state);
  await reopened.session.load();
  assert.equal(selectedCreatorTemplate(reopened.state).id, "minimal-cpp");
  assert.equal(reopened.state.plan, null);
  reopened.session.dispose();
});

test("unavailable or malformed template catalogues never silently retarget a saved choice", async () => {
  for (const catalogue of [[], undefined, null, [{}], [templates[0]], [templates[1], templates[1]], [{...templates[1], version: 2}], [{...templates[1], language: "C"}]]) {
    let writes = 0;
    const app = harness(async (path, options) => {
      if (options.method) writes++;
      return {...response(), templates: catalogue};
    }, {workspace: one, snapshot: snapshot(), form: {...form, template_id: "minimal-cpp"}});
    app.state.plan = {id: "old", workspace: one};
    await app.session.load();
    assert.equal(app.state.plan, null);
    assert.equal(creatorTemplateId(app.state.form), "minimal-cpp");
    assert.equal(selectedCreatorTemplate(app.state), null);
    await app.session.preview();
    assert.equal(writes, 0);
    assert.match(app.state.error, /Choose an available application template/);
    app.session.dispose();
  }
  assert.deepEqual(creatorTemplates([{...templates[0], id: "other"}]), []);
});

test("unknown and empty template selections cannot submit a preview", async () => {
  for (const template_id of ["other", "", null]) {
    let calls = 0;
    const app = harness(async () => { calls++; }, {workspace: one, snapshot: snapshot(), form: {...form, template_id}});
    await app.session.preview();
    assert.equal(calls, 0);
    assert.equal(app.state.form.template_id, template_id);
    assert.equal(app.state.plan, null);
    app.session.dispose();
  }
});

test("catalogue changes invalidate an in-flight template review", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("preview") ? pending.promise : {...response(), templates: [templates[0]]},
    {workspace: one, snapshot: snapshot(), form: {...form, template_id: "minimal-cpp"}});
  const preview = app.session.preview();
  await app.session.load();
  pending.resolve({plan: {id: "late-cpp", workspace: one}}); await preview;
  assert.equal(app.state.plan, null);
  assert.equal(app.state.form.template_id, "minimal-cpp");
  app.session.dispose();
});

test("template labels use recorded identity for C and C++ and retain unknown identities", () => {
  assert.equal(creatorTemplateLabel({id: "minimal-cpp", version: 1}, templates), "Minimal C++ · version 1");
  assert.equal(creatorTemplateLabel({id: "minimal-c", version: 1}, []), "Minimal C · version 1");
  assert.equal(creatorTemplateLabel({id: "future-template", version: 2}, templates), "future-template · version 2");
  assert.equal(creatorTemplateLabel(null, templates), "Template unknown");
});

test("opening creator reads saved projects and jobs without creating or building", async () => {
  const calls = [];
  const job = {id: "old-job", status: "running", metadata: {workspace: one, operation: "build"}};
  const app = harness(async (path, options) => { calls.push([path, options.method]); return response(one, [job]); });
  await app.session.load();
  assert.deepEqual(calls, [["/api/projects/creator", undefined]]);
  assert.equal(app.state.projects[0].id, "project-one");
  assert.equal(currentCreatorJob(app.state).id, "old-job");
  assert.equal(app.state.form.sdk_id, "", "SDK selection must be explicit");
  assert.equal(app.state.form.target, "", "target selection must be explicit");
  app.session.dispose();
});

test("refresh removes missing applications and their build review while preserving form inputs", async () => {
  const calls = [];
  let projects = [project()];
  const app = harness(async (path, options) => {
    calls.push([path, options.method]);
    return {...response(), projects};
  }, {workspace: one, snapshot: snapshot(), projects, form: {...form}});
  app.state.buildPlan = {id: "review", project_id: "project-one", workspace: one};
  await app.session.tick();
  assert.equal(calls.length, 0, "idle polling does not repeatedly read the catalogue");
  projects = [];
  await app.session.refresh();
  assert.deepEqual(app.state.projects, []);
  assert.equal(app.state.buildPlan, null);
  assert.deepEqual(app.state.form, form);
  await app.session.build();
  assert.deepEqual(calls, [["/api/projects/creator", undefined]], "the missing app cannot use its old build review");
  projects = [project()];
  await app.session.refresh();
  assert.equal(app.state.projects[0].id, "project-one", "a restored folder can reappear");
  app.session.dispose();
});

test("refreshes do not overlap or continue after leaving the feature", async () => {
  const pending = deferred();
  let reads = 0;
  const app = harness(async () => { reads++; return pending.promise; });
  const refresh = app.session.refresh();
  assert.equal(app.state.loading, true);
  await app.session.refresh(); await app.session.tick();
  assert.equal(reads, 1);
  app.session.dispose();
  const before = app.publications();
  pending.resolve(response()); await refresh;
  await app.session.refresh();
  assert.equal(reads, 1);
  assert.equal(app.publications(), before);
});

test("refresh preserves new reviews after a historical job failure", async () => {
  const failed = {id: "old-failure", status: "failed", metadata: {workspace: one, operation: "build"}};
  const app = harness(async (path) => {
    if (path.endsWith("/preview")) return {plan: {id: "new-app-review", workspace: one}};
    if (path.endsWith("/build-preview")) return {plan: {id: "new-build-review", workspace: one, project_id: "project-one"}};
    return response(one, [failed]);
  }, {workspace: one, snapshot: snapshot(), form: {...form}});
  await app.session.load();
  await app.session.preview();
  await app.session.previewBuild("project-one", "build");
  await app.session.refresh();
  assert.equal(app.state.plan.id, "new-app-review");
  assert.equal(app.state.buildPlan.id, "new-build-review");
  app.session.dispose();
});

test("preview is bound to the reviewed form and create submits its plan once", async () => {
  const calls = [];
  const pending = deferred();
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    if (path.endsWith("preview")) return {plan: {id: "plan", workspace: one}};
    return pending.promise;
  }, {workspace: one, snapshot: snapshot(), form: {...form}});
  await app.session.preview();
  assert.deepEqual(calls[0], ["/api/projects/preview", {...creatorBody(form), workspace: one}]);
  const create = app.session.create(); await app.session.create();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1], ["/api/projects/create", {plan_id: "plan", workspace: one}]);
  pending.resolve({id: "create", status: "queued", metadata: {workspace: one, operation: "create"}}); await create;
  assert.equal(app.state.plan, null);
  assert.equal(currentCreatorJob(app.state).id, "create");
  app.session.dispose();
});

test("editing during preview discards the late review and changing board clears exact target", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise, {workspace: one, snapshot: snapshot(), form: {...form}});
  const preview = app.session.preview();
  app.session.changeField("name", "changed");
  pending.resolve({plan: {id: "stale", workspace: one}}); await preview;
  assert.equal(app.state.plan, null);
  app.session.changeField("board_id", "another-board");
  assert.equal(app.state.form.target, "");
  assert.equal(app.state.form.sdk_id, "sdk-one");
  app.session.dispose();
});

test("workspace switch before a create click discards old plan and never submits it", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.method]); return response(two); }, {workspace: one, form: {...form}});
  app.state.plan = {id: "old-plan", workspace: one};
  app.changeWorkspace(two);
  await app.session.create();
  assert.deepEqual(calls, [["/api/projects/creator", undefined]]);
  assert.equal(app.state.workspace, two);
  assert.equal(app.state.form.sdk_id, "");
  assert.equal(app.state.plan, null);
  app.session.dispose();
});

test("forms are workspace-specific but reviewed plans are never restored", async () => {
  let returnedPath = one;
  const app = harness(async () => response(returnedPath), {workspace: one, form: {...form}});
  app.state.plan = {id: "old-plan", workspace: one};
  app.changeWorkspace(two); returnedPath = two; await app.session.tick();
  app.session.changeField("name", "second");
  app.changeWorkspace(one); returnedPath = one; await app.session.tick();
  assert.equal(app.state.form.name, "demo");
  assert.equal(app.state.form.sdk_id, "sdk-one");
  assert.equal(app.state.plan, null);
  app.session.dispose();
});

test("late old-workspace and foreign records cannot replace the current workspace", async () => {
  const old = deferred();
  let reads = 0;
  const app = harness(async () => ++reads === 1 ? old.promise : {...response(two), projects: [project(one)], snapshot: snapshot(one)});
  const load = app.session.load();
  app.changeWorkspace(two); await app.session.load();
  old.resolve(response(one)); await load;
  assert.equal(app.state.workspace, two);
  assert.equal(app.state.snapshot, null);
  assert.deepEqual(app.state.projects, []);
  assert.equal(app.state.loading, false);
  app.session.dispose();
});

test("build requires a separate exact command preview and submits its identity", async () => {
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    if (path.endsWith("build-preview")) return {plan: {id: "build-plan", workspace: one, project_id: "project-one", operation: "configure"}};
    return {id: "build-job", status: "queued", metadata: {workspace: one, operation: "configure"}};
  }, {workspace: one, projects: [project()], form: {...form}});
  await app.session.build(); assert.equal(calls.length, 0);
  await app.session.previewBuild("project-one", "configure");
  assert.deepEqual(calls[0], ["/api/projects/build-preview", {project_id: "project-one", operation: "configure", workspace: one}]);
  await app.session.build();
  assert.deepEqual(calls[1], ["/api/projects/build", {build_plan_id: "build-plan", workspace: one}]);
  assert.equal(app.state.buildPlan, null);
  assert.equal(currentCreatorJob(app.state).id, "build-job");
  app.session.dispose();
});

test("immediately completed operations still refresh persisted projects and attempts", async () => {
  const calls = [];
  const app = harness(async (path) => {
    calls.push(path);
    if (path.endsWith("create")) return {id: "fast", status: "completed", metadata: {workspace: one, operation: "create"}};
    return response(one, [{id: "fast", status: "completed", metadata: {workspace: one, operation: "create"}}]);
  }, {workspace: one, form: {...form}});
  app.state.plan = {id: "plan", workspace: one};
  await app.session.create(); await app.session.tick();
  assert.deepEqual(calls, ["/api/projects/create", "/api/projects/creator"]);
  assert.equal(app.state.projects[0].id, "project-one");
  assert.equal(app.state.needsReload, false);
  app.session.dispose();
});

test("cancellation uses shared job API and disposal leaves server jobs running", async () => {
  const calls = [];
  let signal;
  const app = harness(async (path, options) => {
    signal = options.signal; calls.push([path, options.body]);
    return {id: "running", status: "cancelled", metadata: {workspace: one, operation: "build"}};
  }, {workspace: one, jobs: [{id: "running", status: "running", metadata: {workspace: one}}]});
  await app.session.cancel();
  assert.deepEqual(calls, [["/api/jobs/running/cancel", {}]]);
  assert.equal(app.state.cancelling, false);
  app.session.dispose();
  assert.equal(signal.aborted, true);
  assert.equal(app.scheduled.size, 0);
  assert.equal(calls.length, 1, "unmount does not send cancellation");
});

test("leaving and reopening recovers persisted failure evidence without replaying actions", async () => {
  const pending = deferred();
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return pending.promise; });
  const load = app.session.load(); const before = app.publications();
  app.session.dispose(); pending.resolve(response()); await load;
  assert.equal(app.publications(), before);
  const reopened = harness(async (path) => { calls.push(path); return response(one, [{id: "failed", status: "failed", error: "Compiler exited", metadata: {workspace: one}}]); }, app.state);
  await reopened.session.load();
  assert.equal(currentCreatorJob(reopened.state).status, "failed");
  assert.equal(reopened.state.plan, null);
  assert.deepEqual(calls, ["/api/projects/creator", "/api/projects/creator"]);
  reopened.session.dispose();
});

test("changed environment evidence and server errors invalidate prior reviews", async () => {
  let failed = false;
  const app = harness(async () => {
    if (failed) throw new Error("Environment changed; analyze again");
    return {...response(), snapshot: snapshot(one, "changed")};
  }, {workspace: one, snapshot: snapshot(), form: {...form}});
  app.state.plan = {id: "old", workspace: one}; app.state.buildPlan = {id: "old-build", workspace: one};
  await app.session.load();
  assert.equal(app.state.plan, null); assert.equal(app.state.buildPlan, null);
  failed = true; await app.session.preview();
  assert.equal(app.state.error, "Environment changed; analyze again");
  assert.equal(app.state.busy, "");
  app.session.dispose();
});

test("board handoff preserves the board but requires an exact target and SDK choice", async () => {
  const app = harness(async () => response(), {}, () => ({workspace: one, board_id: "board-one"}));
  await app.session.load();
  assert.equal(app.state.form.board_id, "board-one");
  assert.equal(app.state.form.target, ""); assert.equal(app.state.form.sdk_id, "");
  app.session.dispose();
});
