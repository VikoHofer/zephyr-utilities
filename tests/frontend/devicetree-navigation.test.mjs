import test from "node:test";
import assert from "node:assert/strict";
import {createSourceNavigation, filterSourceFiles} from "../../src/zephyr_utilities/features/devicetree/web/navigation-state.js";

const workspace = "/workspace";
const inspection_id = "a".repeat(32);
const selection = {workspace, inspection_id, domain: "app"};
const tree = () => ({workspace, inspection: {id: inspection_id}, domain: {name: "app"}, tree: {nodes: [{path: "/"}, {path: "/uart"}]}});
const file = (id = "source", kind = "source", availability = "available") => ({id, kind, availability, path: `${workspace}/${id}`, name: id, sha256: "expected-hash", origins: ["Recorded input"]});
const catalogue = (catalog_id = "catalog-one") => ({...selection, catalog_id, files: [file(), file("blocked", "source", "blocked")], diagnostics: [], source_freshness: "unknown"});
const bindings = (catalog_id = "catalog-one", node_path = "/uart") => ({...selection, catalog_id, node_path, compatibles: ["test,uart"], bindings: [
  {file: file("uart.yaml", "binding"), compatible: "test,uart", evidence: "compatible-candidate", on_bus: "test", description: "Serial binding"},
], diagnostics: []});
const document = (opened = file(), catalog_id = "catalog-one") => ({...selection, catalog_id, file: opened,
  document: {path: opened.path, text: "current source text", sha256: "expected-hash"}, includes: [{name: "child.dtsi", files: [file("child.dtsi")]}], properties: [], diagnostics: []});
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; }
function harness(api, state = {}) {
  let path = workspace;
  let publications = 0;
  const data = tree();
  const session = createSourceNavigation({api, state, workspace: () => path, onChange: () => publications++});
  session.sync(data, "/uart");
  return {session, state, data, changeWorkspace(value) { path = value; }, publications: () => publications};
}
function standardApi(calls) {
  return async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith("/sources")) return catalogue();
    if (path.endsWith("/bindings")) return bindings(options.body.catalog_id, options.body.node_path);
    return document(file(options.body.file_id, options.body.file_id.endsWith(".yaml") ? "binding" : "source"), options.body.catalog_id);
  };
}

test("source search matches filename, path, kind and origin without executing data", () => {
  assert.deepEqual(filterSourceFiles([file("board.dts"), file("device.yaml", "binding")], "BOARD recorded").map((entry) => entry.id), ["board.dts"]);
  assert.deepEqual(filterSourceFiles([file()], "missing"), []);
});

test("source navigation is idle until explicitly opened and uses the selected image", async () => {
  const calls = [];
  const app = harness(standardApi(calls));
  assert.deepEqual(calls, []);
  await app.session.browse();
  assert.deepEqual(calls.map(([path]) => path), ["/api/devicetree/sources"]);
  assert.deepEqual(calls[0][1].body, selection);
  assert.equal(app.state.catalog.catalog_id, "catalog-one");
  await app.session.browse();
  assert.equal(calls.length, 1, "reopening the list does not silently recatalog files");
  app.session.dispose();
});

test("binding lookup obtains a catalogue and asks for the exact selected node", async () => {
  const calls = [];
  const app = harness(standardApi(calls));
  await app.session.findBindings();
  assert.deepEqual(calls.map(([path]) => path), ["/api/devicetree/sources", "/api/devicetree/bindings"]);
  assert.deepEqual(calls[1][1].body, {...selection, catalog_id: "catalog-one", node_path: "/uart"});
  assert.equal(app.state.bindings.bindings[0].evidence, "compatible-candidate");
  await app.session.findBindings("/unknown");
  assert.equal(calls.length, 2);
  app.session.dispose();
});

test("only available known files can be opened, including returned include references", async () => {
  const calls = [];
  const app = harness(standardApi(calls));
  await app.session.browse();
  await app.session.openFile("not-in-catalogue"); await app.session.openFile("blocked");
  assert.equal(calls.length, 1);
  await app.session.openFile("source");
  assert.deepEqual(calls[1][1].body, {...selection, catalog_id: "catalog-one", file_id: "source"});
  assert.equal(app.state.file.document.text, "current source text");
  await app.session.openFile("child.dtsi");
  assert.equal(app.state.file.file.id, "child.dtsi");
  assert.deepEqual(calls.map(([path]) => path), ["/api/devicetree/sources", "/api/devicetree/source-file", "/api/devicetree/source-file"]);
  app.session.dispose();
});

test("a binding candidate opens by catalogue ID without choosing a default binding", async () => {
  const calls = [];
  const app = harness(standardApi(calls));
  await app.session.findBindings();
  assert.equal(app.state.file, null);
  await app.session.openFile("uart.yaml");
  assert.deepEqual(calls[2][1].body, {...selection, catalog_id: "catalog-one", file_id: "uart.yaml"});
  assert.equal(app.state.file.file.kind, "binding");
  app.session.dispose();
});

test("refreshing source files creates a new catalogue and discards old bindings and text", async () => {
  let count = 0;
  const app = harness(async (path, options) => {
    if (path.endsWith("/sources")) return catalogue(`catalog-${++count}`);
    if (path.endsWith("/bindings")) return bindings(options.body.catalog_id);
    return document(file("uart.yaml", "binding"), options.body.catalog_id);
  });
  await app.session.findBindings(); await app.session.openFile("uart.yaml");
  await app.session.refresh();
  assert.equal(app.state.catalog.catalog_id, "catalog-2");
  assert.equal(app.state.bindings, null); assert.equal(app.state.file, null);
  assert.equal(app.state.panel, "sources");
  app.session.dispose();
});

test("changing node cancels binding lookup without reusing results for another node", async () => {
  const pending = deferred();
  let signal;
  const app = harness(async (path, options) => {
    if (path.endsWith("/sources")) return catalogue();
    signal = options.signal; return pending.promise;
  });
  await app.session.browse();
  const finding = app.session.findBindings();
  await Promise.resolve();
  app.session.sync(app.data, "/");
  pending.resolve(bindings()); await finding;
  assert.equal(signal.aborted, true);
  assert.equal(app.state.bindings, null); assert.equal(app.state.file, null);
  assert.equal(app.state.catalog.catalog_id, "catalog-one");
  app.session.dispose();
});

test("refreshing the tree discards current source evidence even for the same image", async () => {
  const app = harness(standardApi([]));
  await app.session.browse(); await app.session.openFile("source");
  app.session.sync(null, "/uart");
  assert.equal(app.state.catalog, null); assert.equal(app.state.file, null);
  app.session.sync(tree(), "/uart");
  assert.equal(app.state.catalog, null);
  app.session.dispose();
});

test("a new source list wins over late file content and aborts the superseded request", async () => {
  const pending = deferred();
  let count = 0, signal;
  const app = harness(async (path, options) => {
    if (path.endsWith("/sources")) return catalogue(`catalog-${++count}`);
    signal = options.signal; return pending.promise;
  });
  await app.session.browse();
  const reading = app.session.openFile("source");
  await app.session.refresh();
  pending.resolve(document(file(), "catalog-1")); await reading;
  assert.equal(signal.aborted, true);
  assert.equal(app.state.catalog.catalog_id, "catalog-2");
  assert.equal(app.state.file, null);
  app.session.dispose();
});

test("workspace changes reject late reads and prevent new reads from the old tree", async () => {
  const pending = deferred();
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return pending.promise; });
  const browsing = app.session.browse();
  app.changeWorkspace("/other");
  app.session.sync(null, null);
  pending.resolve(catalogue()); await browsing;
  await app.session.browse();
  assert.equal(app.state.catalog, null); assert.equal(app.state.loading, "");
  assert.deepEqual(calls, ["/api/devicetree/sources"]);
  app.session.dispose();
});

test("foreign catalogues, node results and file bytes never appear under the requested selection", async () => {
  for (const alter of [value => { value.workspace = "/other"; }, value => { value.inspection_id = "other"; }, value => { value.domain = "boot"; }]) {
    const app = harness(async () => { const value = catalogue(); alter(value); return value; });
    await app.session.browse();
    assert.equal(app.state.catalog, null); assert.match(app.state.error, /does not belong/);
    app.session.dispose();
  }
  for (const alter of [value => { value.catalog_id = "other"; }, value => { value.node_path = "/"; }]) {
    const app = harness(async (path) => { if (path.endsWith("/sources")) return catalogue(); const value = bindings(); alter(value); return value; });
    await app.session.findBindings();
    assert.equal(app.state.bindings, null); assert.match(app.state.error, /do not belong/);
    app.session.dispose();
  }
  for (const alter of [value => { value.file.id = "other"; }, value => { value.document.path = "/other"; }, value => { value.document.sha256 = ""; },
    value => { value.document.sha256 = value.file.sha256 = "different-bytes"; }, value => { value.file.sha256 = "different-reference"; }, value => { value.catalog_id = "other"; }]) {
    const app = harness(async (path) => { if (path.endsWith("/sources")) return catalogue(); const value = document(); alter(value); return value; });
    await app.session.browse(); await app.session.openFile("source");
    assert.equal(app.state.file, null); assert.match(app.state.error, /does not match/);
    app.session.dispose();
  }
});

test("changed-source errors remove old file text and recover only through a new read", async () => {
  let changed = false;
  const app = harness(async (path) => {
    if (path.endsWith("/sources")) return catalogue(changed ? "fresh" : "catalog-one");
    if (changed) throw new Error("Current source changed. Refresh the source list.");
    return document();
  });
  await app.session.browse(); await app.session.openFile("source");
  changed = true; await app.session.openFile("source");
  assert.equal(app.state.file, null); assert.match(app.state.error, /source changed/);
  await app.session.refresh();
  assert.equal(app.state.error, ""); assert.equal(app.state.catalog.catalog_id, "fresh");
  app.session.dispose();
});

test("leaving aborts reads, stops notifications and reopening starts without saved source bytes", async () => {
  const pending = deferred();
  let signal;
  const app = harness(async (path, options) => { signal = options.signal; return pending.promise; });
  const browsing = app.session.browse();
  app.session.dispose(); const before = app.publications();
  pending.resolve(catalogue()); await browsing;
  assert.equal(signal.aborted, true); assert.equal(app.publications(), before);
  const reopened = harness(standardApi([]), app.state);
  assert.equal(reopened.state.catalog, null); assert.equal(reopened.state.file, null);
  await reopened.session.browse();
  assert.equal(reopened.state.catalog.catalog_id, "catalog-one");
  reopened.session.dispose();
});

test("closing source navigation cancels reads and clears file content", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("/sources") ? catalogue() : pending.promise);
  await app.session.browse();
  const reading = app.session.openFile("source");
  app.session.close(); pending.resolve(document()); await reading;
  assert.equal(app.state.panel, ""); assert.equal(app.state.file, null); assert.equal(app.state.loading, "");
  app.session.dispose();
});
