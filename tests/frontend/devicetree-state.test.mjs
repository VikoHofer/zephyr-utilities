import test from "node:test";
import assert from "node:assert/strict";
import {createTreeSession, filterNodes, inspectionRoute} from "../../src/zephyr_utilities/features/devicetree/web/state.js";

const workspace = "/workspace";
const id = "a".repeat(32);
const otherId = "b".repeat(32);
const nodes = [{path: "/", name: "/", labels: [], properties: []}, {path: "/soc/uart@1000", name: "uart@1000", labels: ["console"], properties: [{name: "compatible", value: '"vendor,serial"'}]}];
const result = (domain = "app", observationId = id) => ({workspace, inspection: {id: observationId, domains: [{name: "app"}, {name: "boot loader"}]}, domain: {name: domain}, tree: {status: "parsed", nodes}, document: {text: "/dts-v1/; / {};"}});
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; }
function harness(api, state = {}) {
  let path = workspace;
  let publications = 0;
  const navigation = [];
  const session = createTreeSession({api, state, workspace: () => path, onChange: () => publications++, navigate: (view) => navigation.push(view)});
  return {session, state, navigation, changeWorkspace(value) { path = value; }, publications: () => publications};
}

test("handoff routes validate identities and preserve encoded domain names", () => {
  assert.equal(inspectionRoute(""), null);
  assert.deepEqual(inspectionRoute(`inspect/${id}/boot%20loader`), {id, domain: "boot loader"});
  for (const route of [`inspect/${id}`, `inspect/not-an-id/app`, `inspect/${id}/%`, `inspect/${id}/a/b`]) assert.throws(() => inspectionRoute(route));
});

test("node search matches paths, labels and compatible strings together", () => {
  assert.deepEqual(filterNodes(nodes, "CONSOLE vendor").map((node) => node.path), ["/soc/uart@1000"]);
  assert.deepEqual(filterNodes(nodes, "missing"), []);
  assert.equal(filterNodes(nodes, "").length, 2);
});

test("opening an image reads its shared observation without creating or building", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options]); return result(); });
  await app.session.open(`inspect/${id}/app`);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/devicetree/inspect");
  assert.deepEqual(calls[0][1].body, {workspace, inspection_id: id, domain: "app"});
  assert.equal(app.state.selectedPath, "/");
  app.session.select("/soc/uart@1000"); app.session.search("uart");
  await app.session.refresh();
  assert.equal(app.state.selectedPath, "/soc/uart@1000");
  assert.equal(app.state.query, "uart");
  app.session.switchDomain("boot loader");
  assert.deepEqual(app.navigation, [`devicetree/inspect/${id}/boot%20loader`]);
  app.session.dispose();
});

test("late image results cannot replace the selected image and previous requests are aborted", async () => {
  const old = deferred();
  let signal;
  const app = harness(async (path, options) => {
    if (options.body.domain === "app") { signal = options.signal; return old.promise; }
    return result("boot loader");
  });
  const first = app.session.open(`inspect/${id}/app`);
  await app.session.open(`inspect/${id}/boot%20loader`);
  assert.equal(signal.aborted, true);
  old.resolve(result()); await first;
  assert.equal(app.state.data.domain.name, "boot loader");
  app.session.dispose();
});

test("different observations keep independent node selections", async () => {
  const app = harness(async (path, options) => result(options.body.domain, options.body.inspection_id));
  await app.session.open(`inspect/${id}/app`);
  app.session.select("/soc/uart@1000");
  await app.session.open(`inspect/${otherId}/app`);
  assert.equal(app.state.selectedPath, "/");
  await app.session.open(`inspect/${id}/app`);
  assert.equal(app.state.selectedPath, "/soc/uart@1000");
  app.session.dispose();
});

test("workspace switches clear selection and reject late foreign data", async () => {
  const old = deferred();
  const app = harness(async () => old.promise);
  const first = app.session.open(`inspect/${id}/app`);
  app.changeWorkspace("/other");
  await app.session.open("");
  old.resolve(result()); await first;
  assert.equal(app.state.data, null);
  assert.equal(app.state.inspectionId, null);
  assert.equal(app.state.workspace, "/other");
  assert.equal(app.state.loading, false);
  app.session.dispose();
});

test("the current view is reconciled when the shell announces a workspace change", async () => {
  const app = harness(async (path, options) => {
    if (options.body.workspace !== workspace) throw new Error("This build inspection belongs to another workspace.");
    return result();
  });
  const view = `inspect/${id}/app`;
  await app.session.open(view);
  assert.ok(app.state.data);
  app.changeWorkspace("/other");
  await app.session.open(view);
  assert.equal(app.state.data, null);
  assert.match(app.state.error, /another workspace/);
  assert.equal(app.state.workspace, "/other");
  app.session.dispose();
});

test("disposal aborts reads and reopening revalidates while restoring node choice", async () => {
  let signal;
  const app = harness(async (path, options) => { signal = options.signal; return result(); });
  await app.session.open(`inspect/${id}/app`);
  app.session.select("/soc/uart@1000"); app.session.search("serial");
  app.session.dispose();
  assert.equal(signal.aborted, true);
  let reads = 0;
  const reopened = harness(async () => { reads++; return result(); }, app.state);
  assert.equal(reopened.state.data, null, "saved tree is hidden until revalidated");
  await reopened.session.open("");
  assert.equal(reads, 1);
  assert.equal(reopened.state.selectedPath, "/soc/uart@1000");
  assert.equal(reopened.state.query, "serial");
  reopened.session.dispose();
});

test("stale artifacts hide the old tree and show a useful error", async () => {
  let stale = false;
  const app = harness(async () => {
    if (stale) throw new Error("Artifact changed; inspect the build again.");
    return result();
  });
  await app.session.open(`inspect/${id}/app`);
  stale = true; await app.session.refresh();
  assert.equal(app.state.data, null);
  assert.match(app.state.error, /Artifact changed/);
  assert.equal(app.state.loading, false);
  app.session.dispose();
});

test("leaving during a read prevents late tree data and notifications", async () => {
  const pending = deferred();
  let signal;
  const app = harness(async (path, options) => { signal = options.signal; return pending.promise; });
  const reading = app.session.open(`inspect/${id}/app`);
  app.session.dispose();
  const before = app.publications();
  pending.resolve(result()); await reading;
  assert.equal(signal.aborted, true);
  assert.equal(app.state.data, null);
  assert.equal(app.publications(), before);
});

test("foreign inspection responses are not displayed under the requested identity", async () => {
  const app = harness(async () => result("app", otherId));
  await app.session.open(`inspect/${id}/app`);
  assert.equal(app.state.data, null);
  assert.match(app.state.error, /inspection changed/);
  app.session.dispose();
});
