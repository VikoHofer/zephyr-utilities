import test from "node:test";
import assert from "node:assert/strict";
import {boardReferences, boardRequest, createCustomBoardsSession} from "../../src/zephyr_utilities/features/projects/web/custom-boards-state.js";
import {customBoardSummary} from "../../src/zephyr_utilities/features/projects/web/custom-boards-view.js";
import {createProjectsNavigation} from "../../src/zephyr_utilities/features/projects/web/index.js";

const workspace = "/workspace";
const registration = () => ({id: "hardware", workspace, name: "Prototype", path: "/workspace/hardware", availability: "available", boards: [], dependencies: []});
const snapshot = () => ({workspace: {path: workspace}, id: "environment", status: "complete", request: {board_roots: ["/manual"], soc_roots: [], sdk_paths: ["/sdk"]}});
const catalogue = () => ({references: [{id: "nordic", name: "nrf_board", full_name: "Nordic board", vendor: "nordic", socs: ["nrf52840"], eligible: true},
  {id: "esp", name: "esp_board", full_name: "ESP board", vendor: "espressif", socs: ["esp32c3"], eligible: true},
  {id: "unsupported", name: "legacy", socs: ["nrf52840"], eligible: false, reason: "Legacy layout"}], socs: [{name: "nrf52840"}, {name: "esp32c3"}]});
const current = () => ({workspace, registrations: [registration()], snapshot: snapshot(), catalogue: catalogue(), diagnostics: [], job: null});
const plan = (operation, extra = {}) => ({id: "review", workspace, operation, files: [], ...extra});
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return {promise, resolve}; }
function harness(api, initial = {}) {
  const state = {...current(), ...initial}, calls = [];
  let selectedWorkspace = workspace;
  const session = createCustomBoardsSession({api: async (path, options) => { calls.push([path, options?.body]); return api(path, options); },
    state, workspace: () => selectedWorkspace, onChange() {}, schedule: () => 1, unschedule() {}});
  return {state, session, calls, switchWorkspace(path) { selectedWorkspace = path; }};
}

test("registering an existing root requires a reviewed plan and saves metadata before prompting analysis", async () => {
  const saved = {...registration(), id: "new", name: "Other boards", path: "/workspace/other"};
  const app = harness(async (path) => path.endsWith("preview") ? {plan: plan("register")} : {registration: saved, needs_analysis: true});
  assert.equal(app.session.open("register"), true);
  app.session.change("name", " Other boards "); app.session.change("path", " other ");
  assert.equal(await app.session.apply(), false);
  assert.equal(app.calls.length, 0);
  assert.equal(await app.session.preview(), true);
  assert.deepEqual(app.calls[0][1], {workspace, operation: "register", name: "Other boards", path: "other"});
  assert.equal(await app.session.apply(), true);
  assert.deepEqual(app.calls[1][1], {workspace, plan_id: "review"});
  assert.equal(app.state.registrations.length, 2);
  assert.equal(app.state.snapshot.status, "stale");
  assert.equal(app.state.needsAnalysis, true);
  assert.match(app.state.notice, /Analyze the workspace/);
  assert.equal(app.state.editor, null);
  assert.equal(app.calls.length, 2, "saving never starts an analysis or build implicitly");
  app.session.dispose();
});

test("reconnect and rename use the same saved identity while removal leaves source deletion outside the request", async () => {
  const app = harness(async (path, options) => path.endsWith("preview") ? {plan: plan(options.body.operation)} : {removed: "hardware", needs_analysis: true});
  app.session.open("update", "hardware");
  app.session.change("name", "Moved prototype"); app.session.change("path", "/workspace/moved");
  assert.deepEqual(boardRequest(app.state.editor), {operation: "update", id: "hardware", name: "Moved prototype", path: "/workspace/moved"});
  app.session.close();
  app.session.open("remove", "hardware"); await app.session.preview(); await app.session.apply();
  assert.deepEqual(app.calls[0][1], {workspace, operation: "remove", id: "hardware"});
  assert.deepEqual(app.state.registrations, []);
  assert.match(app.state.notice, /source files remain/i);
  app.session.dispose();
});

test("SoC-first filters candidates and still sends an explicit reference without UI-only fields", () => {
  const fields = {mode: "soc", soc: "nrf52840", search: "nordic", reference_id: "nordic", name: " my_board ", full_name: " My board ", vendor: " custom ", path: ""};
  assert.deepEqual(boardReferences(catalogue(), fields).map((entry) => entry.id), ["nordic"]);
  assert.deepEqual(boardReferences(catalogue(), {...fields, soc: ""}), []);
  assert.deepEqual(boardRequest({operation: "create", fields}), {operation: "create", name: "my_board", full_name: "My board", vendor: "custom", reference_id: "nordic", soc: "nrf52840"});
  assert.equal(boardRequest({operation: "create", fields: {...fields, mode: "reference"}}).soc, undefined);
  assert.equal(boardRequest({operation: "create", fields: {...fields, path: " hardware/new "}}).path, "hardware/new");
});

test("guided creator preserves draft fields but changing the selected SoC clears its reference", async () => {
  const app = harness(async () => ({plan: plan("create", {files: [{path: "boards/custom/new/board.yml", content: "board: {}"}]})}));
  app.session.open("create");
  app.session.change("name", "new_board"); app.session.change("reference_id", "nordic");
  await app.session.preview();
  app.session.edit();
  assert.equal(app.state.editor.plan, null);
  assert.equal(app.state.editor.fields.name, "new_board");
  assert.equal(await app.session.apply(), false);
  app.session.change("mode", "soc"); app.session.change("soc", "esp32c3");
  assert.equal(app.state.editor.fields.reference_id, "");
  app.session.close(); app.session.open("create");
  assert.equal(app.state.editor.fields.name, "new_board");
  assert.equal(app.state.editor.fields.soc, "esp32c3");
  app.session.dispose();
});

test("a delayed preview cannot reopen a closed editor or authorize a later operation", async () => {
  const pending = deferred(); const app = harness(async () => pending.promise);
  app.session.open("register"); const preview = app.session.preview();
  assert.equal(app.state.busy, "preview");
  app.session.close(); pending.resolve({plan: plan("register")});
  assert.equal(await preview, false); assert.equal(app.state.editor, null);
  app.session.open("register"); assert.equal(app.state.editor.plan, null);
  app.session.dispose();
});

test("a failed apply retains choices, removes the old apply capability and does not alter registrations", async () => {
  const app = harness(async (path) => { if (path.endsWith("apply")) throw new Error("Board files changed since review"); return {plan: plan("update")}; });
  app.session.open("update", "hardware"); app.session.change("path", "/workspace/moved");
  await app.session.preview();
  assert.equal(await app.session.apply(), false);
  assert.equal(app.state.editor.plan, null); assert.equal(app.state.editor.fields.path, "/workspace/moved");
  assert.equal(app.state.registrations[0].path, "/workspace/hardware");
  assert.match(app.state.error, /changed since review/);
  assert.equal(await app.session.apply(), false);
  app.session.dispose();
});

test("a mismatched review workspace or operation cannot be applied", async () => {
  for (const malformed of [{}, {id: "review", workspace: "/other", operation: "register"}, plan("remove")]) {
    const app = harness(async () => ({plan: malformed}));
    app.session.open("register"); assert.equal(await app.session.preview(), false);
    assert.equal(await app.session.apply(), false);
    assert.match(app.state.error, /could not be verified/); app.session.dispose();
  }
});

test("workspace changes isolate forms and reject late operation responses", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("preview") ? pending.promise : {...current(), workspace: "/other", snapshot: null, registrations: []});
  app.session.open("register"); app.session.change("path", "old-hardware");
  const preview = app.session.preview();
  app.switchWorkspace("/other"); await app.session.tick();
  pending.resolve({plan: plan("register")}); await preview;
  assert.equal(app.state.workspace, "/other"); assert.equal(app.state.editor, null);
  app.session.open("register"); assert.equal(app.state.editor.fields.path, "");
  assert.equal(app.state.drafts[workspace].register.path, "old-hardware");
  app.session.dispose();
});

test("leaving the view aborts local requests without cancelling the shared analysis job", async () => {
  const pending = deferred(); let signal;
  const app = harness(async (_path, options) => { signal = options.signal; return pending.promise; });
  const loading = app.session.load(); app.session.dispose();
  assert.equal(signal.aborted, true);
  pending.resolve({...current(), registrations: []}); await loading;
  assert.equal(app.state.registrations.length, 1);
  assert.equal(app.calls.length, 1);
});

test("an apply already in flight cannot be dismissed or applied twice", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("preview") ? {plan: plan("register")} : pending.promise);
  app.session.open("register"); await app.session.preview();
  const applying = app.session.apply();
  assert.equal(app.session.close(), false);
  assert.equal(await app.session.apply(), false);
  assert.equal(app.state.busy, "apply");
  pending.resolve({registration: registration()}); assert.equal(await applying, true);
  assert.equal(app.calls.length, 2); app.session.dispose();
});

test("an older refresh cannot replace a later catalogue response", async () => {
  const first = deferred(), second = deferred(); let count = 0;
  const app = harness(async () => (++count === 1 ? first.promise : second.promise));
  const oldRead = app.session.load(), newRead = app.session.load();
  second.resolve({...current(), registrations: [{...registration(), name: "New name"}]}); await newRead;
  first.resolve(current()); await oldRead;
  assert.equal(app.state.registrations[0].name, "New name"); app.session.dispose();
});

test("late analysis submission cannot attach a job to another workspace", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("analyze") ? pending.promise : {...current(), workspace: "/other", snapshot: null, registrations: []});
  const starting = app.session.analyze(); app.switchWorkspace("/other"); await app.session.tick();
  pending.resolve({id: "old-analysis", status: "queued"});
  assert.equal(await starting, false); assert.equal(app.state.job, null); assert.equal(app.state.workspace, "/other"); app.session.dispose();
});

test("unexpected removal responses preserve the registration and require a fresh review", async () => {
  const app = harness(async (path) => path.endsWith("preview") ? {plan: plan("remove")} : {removed: "different-root"});
  app.session.open("remove", "hardware"); await app.session.preview();
  assert.equal(await app.session.apply(), false);
  assert.equal(app.state.registrations[0].id, "hardware"); assert.equal(app.state.editor.plan, null);
  assert.match(app.state.error, /could not be verified/); app.session.dispose();
});

test("analysis keeps manual options and relies on the server to add registered roots", async () => {
  const app = harness(async () => ({id: "analysis", status: "queued", metadata: {workspace, operation: "analyze"}}));
  assert.equal(await app.session.analyze(), true);
  assert.deepEqual(app.calls[0], ["/api/projects/analyze", {force: true, board_roots: ["/manual"], soc_roots: [], sdk_paths: ["/sdk"]}]);
  assert.equal(app.session.open("register"), false);
  assert.equal(await app.session.analyze(), false);
  app.session.dispose();
});

test("completed analysis refreshes authoritative registration and catalogue state", async () => {
  const job = {id: "analysis", status: "running"};
  const app = harness(async (path) => path.includes("/api/jobs/") ? {...job, status: "completed"} : {...current(), job: {...job, status: "completed"}}, {job, needsAnalysis: true});
  await app.session.tick();
  assert.deepEqual(app.calls.map(([path]) => path), ["/api/jobs/analysis", "/api/projects/custom-boards"]);
  assert.equal(app.state.needsAnalysis, false);
  assert.match(app.state.notice, /Analysis complete/);
  app.session.dispose();
});

test("cancellation is explicit and retained failures remain visible", async () => {
  const job = {id: "analysis", status: "running"};
  const app = harness(async () => ({...job, status: "cancelled"}), {job});
  assert.equal(await app.session.cancel(), true);
  assert.deepEqual(app.calls, [["/api/jobs/analysis/cancel", {}]]);
  assert.equal(app.state.job.status, "cancelled"); app.session.dispose();
  const failed = harness(async () => ({...job, status: "failed", error: "Invalid board metadata"}), {job});
  await failed.session.tick(); assert.equal(failed.state.error, "Invalid board metadata"); failed.session.dispose();
});

test("fresh discovery is required for creating a board but missing roots can still be reconnected", () => {
  const app = harness(async () => current(), {snapshot: {...snapshot(), status: "stale"}, registrations: [{...registration(), availability: "missing"}]});
  assert.equal(app.session.open("create"), false);
  assert.match(app.state.error, /Analyze/);
  assert.equal(app.session.open("update", "hardware"), true);
  assert.equal(app.state.editor.fields.path, "/workspace/hardware"); app.session.dispose();
});

test("review summaries make source writes and configuration dependencies explicit", () => {
  const summary = Object.fromEntries(customBoardSummary(plan("create", {path: "/workspace/hardware/new", name: "new", files: [{path: "board.yml"}], dependencies: [{project_name: "app"}]})));
  assert.equal(summary["Root directory"], "/workspace/hardware/new");
  assert.equal(summary["Source files"], "1 new files");
  assert.equal(summary["Affected saved configurations"], 1);
  assert.equal(Object.fromEntries(customBoardSummary(plan("remove")))["Source files"], "Unchanged");
});

test("custom boards owns one mounted session and disposes cleanly across sidebar navigation", () => {
  const environment = {}, creator = {}, customBoards = {}, instances = [];
  const open = (kind) => { const instance = {kind, views: [], disposed: 0, navigate(view) { this.views.push(view); }, dispose() { this.disposed++; }}; instances.push(instance); return instance; };
  const navigation = createProjectsNavigation({environment, creator, customBoards, openEnvironment: () => open("environment"), openCreator: () => open("creator"), openCustomBoards: () => open("custom")});
  navigation.navigate("custom-boards"); navigation.navigate("custom-boards");
  assert.equal(instances.length, 1); assert.equal(customBoards.hidden, false); assert.equal(creator.hidden, true); assert.equal(environment.hidden, true);
  navigation.navigate("create"); assert.equal(instances[0].disposed, 1); assert.equal(customBoards.hidden, true); assert.equal(creator.hidden, false);
  navigation.navigate("custom-boards"); assert.equal(instances[1].disposed, 1); assert.equal(instances[2].kind, "custom");
  navigation.dispose(); assert.equal(instances[2].disposed, 1);
});
