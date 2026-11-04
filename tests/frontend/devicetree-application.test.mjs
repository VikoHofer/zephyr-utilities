import test from "node:test";
import assert from "node:assert/strict";
import {applicationPath, applicationRoute, createTreeSession} from "../../src/zephyr_utilities/features/inspector/web/state.js";
import {createSourceNavigation} from "../../src/zephyr_utilities/features/inspector/web/navigation-state.js";

const workspace = "/workspace";
const application_path = `${workspace}/apps/my app`;
const selection = {workspace, application_path};
const data = () => ({...selection, mode: "application"});
const route = `application/${encodeURIComponent(application_path)}`;
const buildId = "a".repeat(32);
const build = () => ({workspace, inspection: {id: buildId, domains: [{name: "app"}]}, domain: {name: "app"}, tree: {nodes: [{path: "/"}]}});
const file = (id = "app.overlay", kind = "source") => ({id, kind, path: `${application_path}/${id}`, name: id, sha256: "hash", availability: "available"});
const catalog = (catalog_id = "first") => ({...data(), catalog_id, files: [file(), file("device.yaml", "binding")], diagnostics: []});
const document = (entry, catalog_id = "first") => ({...data(), catalog_id, file: entry, document: {path: entry.path, text: "current text", sha256: "hash"}, includes: [{name: "base.yaml", files: [file("base.yaml", "binding")]}], properties: [], diagnostics: []});
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; }
function treeHarness(api = async () => build(), state = {}) {
  let path = workspace;
  const navigated = [];
  const session = createTreeSession({api, state, workspace: () => path, onChange() {}, navigate: (view) => navigated.push(view)});
  return {session, state, navigated, changeWorkspace(value) { path = value; }};
}
function sourceHarness(api, state = {}) {
  let path = workspace, publications = 0;
  const session = createSourceNavigation({api, state, workspace: () => path, onChange() { publications++; }});
  const selected = data(); session.sync(selected, null);
  return {session, state, selected, publications: () => publications, changeWorkspace(value) { path = value; }};
}

test("application paths resolve beneath the workspace and reject malformed or escaping links", () => {
  assert.equal(applicationPath("apps/my app", workspace), application_path);
  assert.equal(applicationPath("./apps//my app/", workspace), application_path);
  assert.equal(applicationPath("apps/my app", "/"), "/apps/my app");
  assert.equal(applicationRoute(route, workspace), application_path);
  for (const value of ["", ".", workspace, "../escape", "/outside/app", "/workspace-other/app", "apps/../app", "apps/\napp", "a".repeat(4097)]) assert.throws(() => applicationPath(value, workspace));
  for (const value of ["application", "application/%", "application/a/b", "application/%2Foutside%2Fapp", "application/%00"]) assert.throws(() => applicationRoute(value, workspace));
});

test("application selection carries no build identity and the form navigates using an encoded absolute path", async () => {
  let reads = 0;
  const app = treeHarness(async () => { reads++; return build(); });
  app.session.chooseApplication("apps/my app");
  assert.deepEqual(app.navigated, [`inspector/${route}`]);
  await app.session.open(route);
  assert.deepEqual(app.state.data, data());
  assert.equal(app.state.mode, "application");
  assert.equal(app.state.inspectionId, null);
  assert.equal(app.state.domainName, null);
  assert.equal(app.state.selectedPath, null);
  assert.equal(reads, 0, "selecting sources never reads or creates build output");
  const original = app.state.data;
  await app.session.open(route);
  assert.equal(app.state.data, original, "same-route notifications do not restart source scanning");
  app.session.chooseApplication(application_path);
  assert.notEqual(app.state.data, original, "explicitly submitting the same application revalidates it");
  app.session.dispose();
});

test("switching from a pending build to an application aborts the old result and switching back reads the build", async () => {
  const pending = deferred(); let signal, reads = 0;
  const app = treeHarness(async (path, options) => { reads++; signal = options.signal; return reads === 1 ? pending.promise : build(); });
  const reading = app.session.open(`inspect/${buildId}/app`);
  await app.session.open(route);
  assert.equal(signal.aborted, true);
  pending.resolve(build()); await reading;
  assert.deepEqual(app.state.data, data());
  await app.session.open(`inspect/${buildId}/app`);
  assert.equal(reads, 2);
  assert.equal(app.state.mode, "build");
  assert.equal(app.state.applicationPath, null);
  assert.equal(app.state.data.domain.name, "app");
  app.session.dispose();
});

test("application selections revalidate when reopened and clear when the workspace changes", async () => {
  const app = treeHarness(); await app.session.open(route);
  app.session.dispose();
  const reopened = treeHarness(undefined, app.state);
  assert.equal(reopened.state.data, null);
  await reopened.session.open("");
  assert.deepEqual(reopened.state.data, data());
  reopened.changeWorkspace("/other"); await reopened.session.open(route);
  assert.equal(reopened.state.data, null);
  assert.equal(reopened.state.applicationPath, null);
  assert.match(reopened.state.error, /selected workspace/);
  reopened.session.dispose();
});

test("a malformed application route clears current content and a corrected route recovers", async () => {
  const app = treeHarness(); await app.session.open(route);
  await app.session.open("application/%");
  assert.equal(app.state.data, null); assert.match(app.state.error, /link is invalid/);
  await app.session.open(route);
  assert.deepEqual(app.state.data, data()); assert.equal(app.state.error, "");
  app.session.dispose();
});

test("application catalogs and included binding files use only application endpoints and identities", async () => {
  const calls = [];
  const app = sourceHarness(async (path, options) => {
    calls.push([path, options.body]);
    return path.endsWith("application-sources") ? catalog() : document(file(options.body.file_id, "binding"));
  });
  await app.session.browse();
  await app.session.findBindings("/");
  await app.session.openFile("device.yaml");
  await app.session.openFile("base.yaml");
  assert.deepEqual(calls, [["/api/inspector/application-sources", selection],
    ["/api/inspector/application-source-file", {...selection, catalog_id: "first", file_id: "device.yaml"}],
    ["/api/inspector/application-source-file", {...selection, catalog_id: "first", file_id: "base.yaml"}]]);
  assert.equal(app.state.file.file.id, "base.yaml");
  assert.equal(app.state.bindings, null);
  app.session.dispose();
});

test("foreign application and missing-mode results cannot appear under the selected application", async () => {
  for (const alter of [value => { value.application_path = `${workspace}/other`; }, value => { value.workspace = "/other"; }, value => { delete value.mode; }, value => { value.mode = "build"; }]) {
    const app = sourceHarness(async () => { const value = catalog(); alter(value); return value; });
    await app.session.browse();
    assert.equal(app.state.catalog, null); assert.match(app.state.error, /does not belong to this application/);
    app.session.dispose();
  }
  for (const alter of [value => { value.application_path = `${workspace}/other`; }, value => { delete value.mode; }, value => { value.document.sha256 = "changed"; }]) {
    const app = sourceHarness(async (path) => {
      if (path.endsWith("application-sources")) return catalog();
      const value = document(file()); alter(value); return value;
    });
    await app.session.browse(); await app.session.openFile("app.overlay");
    assert.equal(app.state.file, null); assert.match(app.state.error, /does not match/);
    app.session.dispose();
  }
});

test("application replacement, workspace changes and disposal reject late file results", async () => {
  for (const action of [app => app.session.sync({...data(), application_path: `${workspace}/other`}, null),
    app => { app.changeWorkspace("/other"); app.session.sync(null, null); }, app => app.session.sync(build(), "/"), app => app.session.dispose()]) {
    const pending = deferred(); let signal;
    const app = sourceHarness(async (path, options) => {
      if (path.endsWith("application-sources")) return catalog();
      signal = options.signal; return pending.promise;
    });
    await app.session.browse(); const reading = app.session.openFile("app.overlay");
    action(app); const before = app.publications();
    pending.resolve(document(file())); await reading;
    assert.equal(signal.aborted, true); assert.equal(app.state.file, null); assert.equal(app.state.catalog, null);
    assert.equal(app.publications(), before);
    app.session.dispose();
  }
});

test("missing apps and stale files show errors and a new catalog clears previous content", async () => {
  let missing = false, count = 0;
  const app = sourceHarness(async (path) => {
    if (missing) throw new Error("This application changed or is missing. Open the sources again.");
    return path.endsWith("application-sources") ? catalog(`catalog-${++count}`) : document(file(), `catalog-${count}`);
  });
  await app.session.browse(); await app.session.openFile("app.overlay");
  assert.ok(app.state.file);
  missing = true; await app.session.openFile("app.overlay");
  assert.equal(app.state.file, null); assert.match(app.state.error, /application changed/);
  await app.session.refresh();
  assert.equal(app.state.catalog, null); assert.equal(app.state.file, null); assert.equal(app.state.loading, "");
  missing = false; await app.session.refresh();
  assert.equal(app.state.catalog.catalog_id, "catalog-2"); assert.equal(app.state.error, "");
  app.session.dispose();
});

test("reopening application source navigation starts with a fresh catalog and no retained text", async () => {
  let count = 0;
  const api = async (path) => path.endsWith("application-sources") ? catalog(`catalog-${++count}`) : document(file(), `catalog-${count}`);
  const app = sourceHarness(api); await app.session.browse(); await app.session.openFile("app.overlay");
  app.session.dispose();
  const reopened = sourceHarness(api, app.state);
  assert.equal(reopened.state.catalog, null); assert.equal(reopened.state.file, null);
  await reopened.session.browse(); assert.equal(reopened.state.catalog.catalog_id, "catalog-2");
  reopened.session.dispose();
});
