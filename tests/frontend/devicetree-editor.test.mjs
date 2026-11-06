import test from "node:test";
import assert from "node:assert/strict";
import {applicationDirectory, createEditorSession} from "../../src/zephyr_utilities/features/devicetree/web/state.js";

const workspace = "/workspace";
const projectId = "a".repeat(32);
const configurationId = "b".repeat(32);
const projects = [{id: projectId, name: "Weather station", path: "/workspace/weather", availability: {state: "available"},
  configurations: [{id: null, name: "Original", revision: 0}, {id: configurationId, name: "Debug", revision: 2}]}];
const catalog = () => ({workspace, projects, diagnostics: []});
const overlay = (id = "overlay-a") => ({id, path: `/workspace/weather/${id}.overlay`, relative_path: `${id}.overlay`,
  availability: "available", sha256: `${id}-hash`, origins: ["Application source"], usage: "unknown"});
function context(overrides = {}) {
  return {schema_version: 1, id: "context-a", revision: 1, workspace, application: {path: "/workspace/weather", name: "Weather station", availability: {state: "available"}},
    project: {id: projectId, revision: 1}, configuration: {id: null, revision: 0, name: "Original"},
    target: {requested: "custom_board@1.2/soc/core"}, documents: [overlay(), overlay("overlay-b")], editing_document: null,
    capabilities: {edit: false, save: false}, ...overrides};
}
function document(data) {
  return {context_id: data.id, revision: data.revision, document: {...data.editing_document, text: '&uart0 { status = "okay"; };\n'}};
}
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, failed) => { resolve = done; reject = failed; });
  return {promise, resolve, reject};
}
function harness(api, state = {}) {
  let path = workspace;
  let publications = 0;
  const session = createEditorSession({api, state, workspace: () => path, onChange: () => publications++});
  return {session, state, publications: () => publications, changeWorkspace(value) { path = value; }};
}

test("application directories stay inside the workspace without parent traversal", () => {
  assert.equal(applicationDirectory("applications/weather", workspace), "/workspace/applications/weather");
  assert.equal(applicationDirectory(" /workspace/weather/ ", workspace), "/workspace/weather");
  for (const value of ["", "..", "/workspace", "/workspace-other/app", "/outside/app", "app/../other", "app\0data"]) {
    assert.throws(() => applicationDirectory(value, workspace));
  }
});

test("opening a saved application passes only identifiers and retains its exact target", async () => {
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    return path.endsWith("/catalog") ? catalog() : context();
  });
  await app.session.navigate("editor");
  await app.session.openProject(projectId, null);
  assert.deepEqual(calls, [["/api/devicetree/editor/catalog", {workspace}],
    ["/api/devicetree/editor/open", {workspace, project_id: projectId, configuration_id: null}]]);
  assert.equal(app.state.data.target.requested, "custom_board@1.2/soc/core");
  assert.equal(app.state.document, null);
  app.session.dispose();
});

test("named configurations and unregistered directories use separate explicit selections", async () => {
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push(options.body);
    if (path.endsWith("/catalog")) return catalog();
    return options.body.application_path ? context({id: "directory", project: null, configuration: null,
      application: {path: options.body.application_path}, target: null})
      : context({configuration: {id: configurationId, revision: 2, name: "Debug"}});
  });
  await app.session.navigate("editor");
  await app.session.openProject(projectId, configurationId);
  assert.equal(app.state.data.configuration.id, configurationId);
  await app.session.openApplication("unregistered");
  assert.deepEqual(calls.at(-1), {workspace, application_path: "/workspace/unregistered"});
  assert.equal(app.state.data.project, null);
  assert.equal(app.state.data.target, null);
  app.session.dispose();
});

test("a selected default overlay is read against the returned context revision and hash", async () => {
  const selected = context({editing_document: overlay()});
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    return path.endsWith("/catalog") ? catalog() : path.endsWith("/document") ? document(selected) : selected;
  });
  await app.session.navigate();
  await app.session.openProject(projectId);
  assert.deepEqual(calls.at(-1), ["/api/devicetree/editor/document", {workspace, context_id: selected.id,
    expected_revision: 1, document_id: "overlay-a"}]);
  assert.equal(app.state.document.text, '&uart0 { status = "okay"; };\n');
  app.session.dispose();
});

test("explicit overlay selection advances the context revision before reading source", async () => {
  let selected = context();
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    if (path.endsWith("/catalog")) return catalog();
    if (path.endsWith("/select")) selected = context({revision: 2, editing_document: overlay("overlay-b")});
    return path.endsWith("/document") ? document(selected) : selected;
  });
  await app.session.navigate(); await app.session.openProject(projectId);
  await app.session.selectDocument("overlay-b");
  assert.deepEqual(calls.at(-2), ["/api/devicetree/editor/select", {workspace, context_id: "context-a", expected_revision: 1, document_id: "overlay-b"}]);
  assert.equal(calls.at(-1)[1].expected_revision, 2);
  assert.equal(app.state.document.id, "overlay-b");
  app.session.dispose();
});

test("unavailable contexts and overlays remain visible without attempted source reads", async () => {
  const unavailable = {...overlay(), availability: "missing", sha256: null, reason: "File missing"};
  const calls = [];
  const app = harness(async (path) => {
    calls.push(path);
    return path.endsWith("/catalog") ? catalog() : context({editing_document: unavailable, documents: [unavailable],
      application: {path: "/workspace/weather", availability: {state: "missing", reason: "Application moved"}}});
  });
  await app.session.navigate(); await app.session.openProject(projectId);
  assert.equal(app.state.data.application.availability.state, "missing");
  assert.equal(app.state.document, null);
  assert.equal(calls.some((path) => path.endsWith("/document")), false);
  app.session.dispose();
});

test("late context responses cannot replace a newer selection", async () => {
  const pending = deferred();
  let oldSignal;
  const app = harness(async (path, options) => {
    if (path.endsWith("/catalog")) return catalog();
    if (options.body.configuration_id === null) { oldSignal = options.signal; return pending.promise; }
    return context({id: "context-b", configuration: {id: configurationId, name: "Debug", revision: 2}});
  });
  await app.session.navigate();
  const opening = app.session.openProject(projectId);
  await app.session.openProject(projectId, configurationId);
  pending.resolve(context()); await opening;
  assert.equal(oldSignal.aborted, true);
  assert.equal(app.state.data.id, "context-b");
  app.session.dispose();
});

test("late overlay text cannot appear beneath another context", async () => {
  const pending = deferred();
  const first = context({editing_document: overlay()});
  const app = harness(async (path, options) => {
    if (path.endsWith("/catalog")) return catalog();
    if (path.endsWith("/document")) return pending.promise;
    return options.body.configuration_id === null ? first
      : context({id: "context-b", configuration: {id: configurationId, name: "Debug", revision: 2}});
  });
  await app.session.navigate();
  const opening = app.session.openProject(projectId);
  await Promise.resolve();
  await app.session.openProject(projectId, configurationId);
  pending.resolve(document(first)); await opening;
  assert.equal(app.state.data.id, "context-b");
  assert.equal(app.state.document, null);
  app.session.dispose();
});

test("workspace changes clear prior selections and reject late catalogue and context responses", async () => {
  const pending = deferred();
  const app = harness(async (path, options) => {
    if (options.body.workspace !== workspace) return {workspace: "/other", projects: [], diagnostics: []};
    return path.endsWith("/catalog") ? catalog() : pending.promise;
  });
  await app.session.navigate();
  const opening = app.session.openProject(projectId);
  app.changeWorkspace("/other"); await app.session.navigate();
  pending.resolve(context()); await opening;
  assert.equal(app.state.workspace, "/other");
  assert.equal(app.state.data, null);
  assert.equal(app.state.selection, null);
  assert.equal(app.state.contextRef, null);
  assert.equal(app.state.catalog.workspace, "/other");
  app.session.dispose();
});

test("leaving and reopening hides saved data until the same server context is revalidated", async () => {
  const selected = context({editing_document: overlay()});
  const app = harness(async (path) => path.endsWith("/catalog") ? catalog() : path.endsWith("/document") ? document(selected) : selected);
  await app.session.navigate(); await app.session.openProject(projectId); app.session.dispose();
  const calls = [];
  const reopened = harness(async (path, options) => {
    calls.push([path, options.body]);
    return path.endsWith("/catalog") ? catalog() : path.endsWith("/document") ? document(selected) : selected;
  }, app.state);
  assert.equal(reopened.state.data, null);
  assert.equal(reopened.state.document, null);
  await reopened.session.navigate();
  assert.deepEqual(calls[0], ["/api/devicetree/editor/context", {workspace, context_id: "context-a", expected_revision: 1}]);
  assert.equal(calls.some(([path]) => path.endsWith("/open")), false);
  assert.equal(reopened.state.document.id, "overlay-a");
  reopened.session.dispose();
});

test("expired or stale restored contexts stay hidden and never silently reopen changed source", async () => {
  const saved = {workspace, contextRef: {id: "old", revision: 1}, selection: {kind: "project", project_id: projectId, configuration_id: null}};
  const calls = [];
  const app = harness(async (path) => {
    calls.push(path);
    if (path.endsWith("/context")) throw new Error("This context expired. Open the application again.");
    return catalog();
  }, saved);
  await app.session.navigate();
  assert.equal(app.state.data, null);
  assert.match(app.state.error, /expired/);
  assert.equal(calls.some((path) => path.endsWith("/open")), false);
  app.session.dispose();
});

test("foreign context and source responses are rejected before display", async () => {
  for (const response of [context({workspace: "/foreign"}), context({project: {id: "foreign"}}),
    context({revision: "1"}), context({revision: 0}), context({schema_version: 2}), context({schema_version: null})]) {
    const app = harness(async (path) => path.endsWith("/catalog") ? catalog() : response);
    await app.session.navigate(); await app.session.openProject(projectId);
    assert.equal(app.state.data, null); assert.match(app.state.error, /changed/); app.session.dispose();
  }
  const selected = context({editing_document: overlay()});
  const app = harness(async (path) => path.endsWith("/catalog") ? catalog() : path.endsWith("/document")
    ? {...document(selected), document: {...document(selected).document, sha256: "changed"}} : selected);
  await app.session.navigate(); await app.session.openProject(projectId);
  assert.equal(app.state.document, null);
  assert.match(app.state.documentError, /overlay changed/);
  app.session.dispose();
});

test("same-view navigation does not repeat reads and disposal prevents late publications", async () => {
  const pending = deferred();
  let signal;
  let calls = 0;
  const app = harness(async (path, options) => { calls++; signal = options.signal; return pending.promise; });
  const navigating = app.session.navigate();
  await app.session.navigate();
  assert.equal(calls, 1);
  app.session.dispose();
  const publications = app.publications();
  pending.resolve(catalog()); await navigating;
  assert.equal(signal.aborted, true);
  assert.equal(app.publications(), publications);
  assert.equal(app.state.catalog, null);
});

test("empty and failed catalogues have explicit outcomes and invalid selections launch no reads", async () => {
  let calls = 0;
  const app = harness(async () => { calls++; return {workspace, projects: [], diagnostics: []}; });
  await app.session.navigate();
  await app.session.openProject(projectId);
  assert.equal(calls, 1);
  assert.match(app.state.error, /Choose an application/);
  await app.session.openApplication("../outside");
  assert.equal(calls, 1);
  app.session.dispose();
  const failing = harness(async () => { throw new Error("Catalogue unavailable"); });
  await failing.session.navigate();
  assert.equal(failing.state.catalogLoading, false);
  assert.equal(failing.state.catalog, null);
  assert.equal(failing.state.catalogError, "Catalogue unavailable");
  failing.session.dispose();
});

test("registered application choices include only explicitly available records while retaining unavailable metadata", async () => {
  const available = structuredClone(projects[0]);
  const missing = {...structuredClone(projects[0]), id: "missing", availability: {state: "missing", reason: "Directory moved"}};
  const replaced = {...structuredClone(projects[0]), id: "replaced", availability: {state: "replaced"}};
  const unknown = {...structuredClone(projects[0]), id: "unknown", availability: {state: "unknown"}};
  const undeclared = {...structuredClone(projects[0]), id: "undeclared"}; delete undeclared.availability;
  const response = {workspace, projects: [missing, available, replaced, unknown, undeclared], diagnostics: ["Saved registrations retained."]};
  const app = harness(async () => response);
  await app.session.navigate();
  assert.deepEqual(app.state.catalog.projects, [available]);
  assert.deepEqual(app.state.catalog.unavailableProjects, [missing, replaced, unknown, undeclared]);
  assert.deepEqual(app.state.catalog.diagnostics, response.diagnostics);
  assert.equal(response.projects.length, 5, "normalization does not remove saved records from the response");
  app.session.dispose();
});

test("an all-unavailable catalogue provides no selectable applications or configurations", async () => {
  const unavailable = ["missing", "replaced", "unknown"].map((status) => ({...structuredClone(projects[0]), id: status, availability: {state: status}}));
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return {workspace, projects: unavailable, diagnostics: []}; });
  await app.session.navigate();
  assert.deepEqual(app.state.catalog.projects, []); assert.equal(app.state.catalog.unavailableProjects.length, 3);
  assert.equal(app.state.formProjectId, ""); assert.equal(app.state.formConfigurationId, null);
  for (const project of unavailable) {
    assert.equal(await app.session.openProject(project.id, configurationId), false);
    assert.match(app.state.error, /current available catalogue/);
  }
  assert.deepEqual(calls, ["/api/devicetree/editor/catalog"]);
  app.session.dispose();
});

test("refreshing away a selected registration clears only its picker state and preserves the active draft", async () => {
  let response = catalog(); const selected = context({editing_document: overlay()}); const calls = [];
  const app = harness(async (path) => {
    calls.push(path); return path.endsWith("/catalog") ? response : path.endsWith("/document") ? document(selected) : selected;
  });
  await app.session.navigate(); await app.session.openProject(projectId);
  app.state.formProjectId = projectId; app.state.formConfigurationId = configurationId;
  const retained = {data: app.state.data, document: app.state.document, contextRef: app.state.contextRef, selection: app.state.selection,
    draftView: {entry: {text: "unsaved overlay", history: ["previous source"]}}, draftCache: {draft: {text: "unsaved overlay"}}};
  Object.assign(app.state, retained);
  response = {workspace, projects: [{...structuredClone(projects[0]), availability: {state: "missing"}}], diagnostics: []};
  await app.session.catalog();
  assert.equal(app.state.formProjectId, ""); assert.equal(app.state.formConfigurationId, null);
  for (const [key, value] of Object.entries(retained)) assert.equal(app.state[key], value, key);
  const before = calls.length;
  assert.equal(await app.session.openProject(projectId, configurationId), false);
  assert.equal(calls.length, before, "a stale picker request cannot open an unavailable record");
  for (const [key, value] of Object.entries(retained)) assert.equal(app.state[key], value, key + " after stale open");
  app.session.dispose();
});

test("catalogue refresh clears removed configuration choices without changing an active named context", async () => {
  let response = catalog(); const selected = context({configuration: {id: configurationId, name: "Debug", revision: 2}});
  const app = harness(async (path) => path.endsWith("/catalog") ? response : selected);
  await app.session.navigate(); await app.session.openProject(projectId, configurationId);
  app.state.formProjectId = projectId; app.state.formConfigurationId = configurationId;
  await app.session.catalog();
  assert.equal(app.state.formProjectId, projectId); assert.equal(app.state.formConfigurationId, configurationId);
  response = {workspace, projects: [{...structuredClone(projects[0]), configurations: [{id: null, name: "Original", revision: 0}]}], diagnostics: []};
  await app.session.catalog();
  assert.equal(app.state.formProjectId, projectId); assert.equal(app.state.formConfigurationId, null);
  assert.equal(app.state.data, selected); assert.equal(app.state.selection.configuration_id, configurationId);
  assert.equal(await app.session.openProject(projectId, configurationId), false);
  assert.equal(app.state.data, selected);
  app.session.dispose();
});

test("restored applications return on explicit refresh without silently reopening or reselecting them", async () => {
  let available = false; const calls = [];
  const app = harness(async (path) => {
    calls.push(path);
    return path.endsWith("/catalog") ? {workspace, projects: [{...structuredClone(projects[0]), availability: {state: available ? "available" : "missing"}}], diagnostics: []} : context();
  });
  await app.session.navigate(); assert.deepEqual(app.state.catalog.projects, []);
  available = true; await app.session.catalog();
  assert.equal(app.state.catalog.projects.length, 1); assert.deepEqual(app.state.catalog.unavailableProjects, []);
  assert.equal(app.state.formProjectId, ""); assert.equal(app.state.data, null);
  assert.equal(calls.filter((path) => path.endsWith("/open")).length, 0);
  await app.session.openProject(projectId);
  assert.equal(app.state.data.project.id, projectId);
  app.session.dispose();
});

test("a late catalogue cannot restore records hidden by a newer refresh", async () => {
  const pending = deferred(); let reads = 0; let signal;
  const app = harness(async (_path, options) => {
    if (++reads === 2) { signal = options.signal; return pending.promise; }
    return reads === 1 ? catalog() : {workspace, projects: [{...structuredClone(projects[0]), availability: {state: "missing"}}], diagnostics: []};
  });
  await app.session.navigate(); app.state.formProjectId = projectId; app.state.formConfigurationId = configurationId;
  const older = app.session.catalog(); await app.session.catalog();
  pending.resolve(catalog()); await older;
  assert.equal(signal.aborted, true); assert.deepEqual(app.state.catalog.projects, []);
  assert.equal(app.state.catalog.unavailableProjects.length, 1);
  assert.equal(app.state.formProjectId, ""); assert.equal(app.state.formConfigurationId, null);
  app.session.dispose();
});
