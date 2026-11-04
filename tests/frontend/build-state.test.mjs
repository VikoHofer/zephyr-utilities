import test from "node:test";
import assert from "node:assert/strict";
import {canOpenDeviceTree, canOpenMemory, createBuildSession, selectedBuildDomain} from "../../src/zephyr_utilities/features/inspector/web/build-state.js";

const one = "/workspace/one";
const two = "/workspace/two";
const observation = (workspace = one, id = "observation-one") => ({id, workspace, root_build_dir: `${workspace}/build`, default_domain: "boot", domains: [
  {name: "app", build_dir: `${workspace}/build/app`, freshness: "unchanged", context: {effective_target: "board/chip"}, artifacts: [{kind: "resolved-dts", path: `${workspace}/build/app/zephyr/zephyr.dts`, sha256: "app-hash", availability: "live"}]},
  {name: "boot", build_dir: `${workspace}/build/boot`, freshness: "unchanged", context: {effective_target: "board/chip"}, artifacts: [{kind: "resolved-dts", path: `${workspace}/build/boot/zephyr/zephyr.dts`, sha256: "boot-hash", availability: "live"}]},
]});
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
function harness(api, state = {}) {
  let workspace = one;
  let publications = 0;
  const routes = [];
  const session = createBuildSession({api, state, workspace: () => workspace, onChange: () => publications++, navigate: (route) => routes.push(route)});
  return {session, state, routes, publications: () => publications, switchWorkspace(path) { workspace = path; }};
}

test("Memory handoff carries the selected ELF image without starting a report job", async () => {
  const value = observation();
  value.domains[0].name = "app / network";
  value.domains[0].artifacts.push({kind: "elf", path: `${one}/build/app/zephyr/zephyr.elf`, sha256: "elf-hash", availability: "live"});
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return value; });
  await app.session.open(`${one}/build`);
  assert.equal(canOpenMemory(app.state), false, "default boot image has no ELF");
  app.session.openMemory();
  assert.deepEqual(app.routes, []);
  app.session.selectDomain("app / network");
  assert.equal(canOpenMemory(app.state), true);
  app.session.openMemory();
  assert.deepEqual(app.routes, ["memory/inspect/observation-one/app%20%2F%20network"]);
  assert.deepEqual(calls, ["/api/inspector/inspect"]);
  selectedBuildDomain(app.state).artifacts[1].availability = "missing";
  assert.equal(canOpenMemory(app.state), false);
  selectedBuildDomain(app.state).artifacts[1].availability = "live";
  selectedBuildDomain(app.state).freshness = "changed";
  assert.equal(canOpenMemory(app.state), false);
  selectedBuildDomain(app.state).freshness = "unchanged";
  app.switchWorkspace(two); app.session.openMemory();
  assert.equal(app.routes.length, 1);
  assert.equal(canOpenMemory(app.state), false);
  app.session.dispose();
});

test("opening the inspector is idle until a build is selected", async () => {
  const calls = [];
  const app = harness(async (...args) => { calls.push(args); return observation(); });
  await app.session.navigate("builds");
  assert.equal(app.state.workspace, one);
  assert.equal(app.state.observation, null);
  assert.deepEqual(calls, []);
  await app.session.open();
  assert.match(app.state.error, /Enter an existing build/);
  app.session.dispose();
});

test("opening an existing build selects its declared default domain and hands off its observation", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.method, options.body]); return observation(); });
  await app.session.navigate(`builds/${encodeURIComponent(`${one}/build`)}`);
  assert.deepEqual(calls, [["/api/inspector/inspect", "POST", {workspace: one, build_dir: `${one}/build`}]]);
  assert.equal(selectedBuildDomain(app.state).name, "boot");
  app.session.selectDomain("app"); app.session.openDeviceTree();
  assert.deepEqual(app.routes, ["inspector/inspect/observation-one/app"]);
  app.session.dispose();
});

test("checking retains the observation identity and blocks changed files until explicit reinspection", async () => {
  const calls = [];
  const changed = observation(); changed.domains[1].freshness = "changed"; changed.domains[1].artifacts[0].availability = "changed";
  const app = harness(async (path, options) => {
    calls.push([path, options.method]);
    return options.method === "POST" ? observation(one, calls.length > 2 ? "new-observation" : "observation-one") : changed;
  });
  await app.session.navigate("builds"); await app.session.open(`${one}/build`);
  assert.equal(canOpenDeviceTree(app.state), true);
  await app.session.check();
  assert.equal(app.state.observation.id, "observation-one");
  assert.equal(canOpenDeviceTree(app.state), false);
  app.session.openDeviceTree(); await app.session.readText("resolved-dts");
  assert.deepEqual(app.routes, []);
  assert.equal(calls.length, 2);
  assert.equal(calls[1][0], `/api/inspector/inspections/observation-one?workspace=${encodeURIComponent(one)}`);
  await app.session.open();
  assert.equal(app.state.observation.id, "new-observation");
  assert.equal(canOpenDeviceTree(app.state), true);
  app.session.dispose();
});

test("text previews use the selected image and discard late data after changing image", async () => {
  const pending = deferred();
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    return path.endsWith("inspection-text") ? pending.promise : observation();
  });
  await app.session.navigate("builds"); await app.session.open(`${one}/build`);
  const read = app.session.readText("resolved-dts");
  assert.deepEqual(calls[1], ["/api/inspector/inspection-text", {workspace: one, inspection_id: "observation-one", domain: "boot", kind: "resolved-dts"}]);
  app.session.selectDomain("app");
  pending.resolve({path: "boot.dts", text: "/ { boot; };", sha256: "boot-hash"}); await read;
  assert.equal(app.state.file, null);
  assert.equal(app.state.fileLoading, "");
  await app.session.readText("elf");
  assert.equal(calls.length, 2, "binary artifacts are never offered as text");
  app.session.dispose();
});

test("a rejected file read clears verification and requires a new check", async () => {
  const app = harness(async (path) => {
    if (path.endsWith("inspection-text")) throw new Error("Artifact changed; re-inspect the directory.");
    return observation();
  });
  await app.session.navigate("builds"); await app.session.open(`${one}/build`);
  await app.session.readText("resolved-dts");
  assert.match(app.state.error, /Artifact changed/);
  assert.equal(canOpenDeviceTree(app.state), false);
  assert.equal(app.state.file, null);
  await app.session.check();
  assert.equal(app.state.error, "");
  assert.equal(canOpenDeviceTree(app.state), true);
  app.session.dispose();
});

test("workspace switches discard late responses and preserve only that workspace's selection", async () => {
  const pending = deferred();
  const app = harness(async (path, options) => options.body?.workspace === one ? pending.promise : observation(two));
  await app.session.navigate("builds");
  const first = app.session.open(`${one}/build`);
  app.switchWorkspace(two); await app.session.navigate("builds");
  assert.equal(app.state.observation, null);
  await app.session.open(`${two}/build`);
  pending.resolve(observation()); await first;
  assert.equal(app.state.observation.workspace, two);
  assert.equal(app.state.path, `${two}/build`);
  assert.equal(app.state.busy, "");
  app.session.dispose();
});

test("switching workspace does not reopen the old route but accepts a new workspace handoff", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push(options.body); return observation(options.body.workspace); });
  const oldRoute = `builds/${encodeURIComponent(`${one}/build`)}`;
  await app.session.navigate(oldRoute);
  app.switchWorkspace(two); await app.session.navigate(oldRoute);
  assert.equal(app.state.observation, null);
  assert.equal(calls.length, 1);
  await app.session.navigate(`builds/${encodeURIComponent(`${two}/build`)}`);
  assert.equal(app.state.observation.workspace, two);
  assert.equal(calls[1].build_dir, `${two}/build`);
  app.session.dispose();
});

test("newest opened build wins if an older inspection finishes later", async () => {
  const older = deferred();
  const app = harness(async (path, options) => options.body?.build_dir.endsWith("/older") ? older.promise : {...observation(), root_build_dir: `${one}/newer`, id: "newer"});
  await app.session.navigate("builds");
  const first = app.session.open(`${one}/older`);
  await app.session.open(`${one}/newer`);
  older.resolve({...observation(), root_build_dir: `${one}/older`, id: "older"}); await first;
  assert.equal(app.state.observation.id, "newer");
  assert.equal(app.state.path, `${one}/newer`);
  app.session.dispose();
});

test("reopening keeps the selected image but revalidates before handoff", async () => {
  const first = harness(async () => observation());
  await first.session.navigate("builds"); await first.session.open(`${one}/build`);
  first.session.selectDomain("app"); first.session.dispose();
  const pending = deferred();
  const calls = [];
  const reopened = harness(async (path, options) => { calls.push([path, options.method]); return pending.promise; }, first.state);
  assert.equal(canOpenDeviceTree(reopened.state), false);
  const check = reopened.session.navigate("builds");
  pending.resolve(observation()); await check;
  assert.equal(reopened.state.domain, "app");
  assert.equal(canOpenDeviceTree(reopened.state), true);
  assert.deepEqual(calls, [[`/api/inspector/inspections/observation-one?workspace=${encodeURIComponent(one)}`, undefined]]);
  reopened.session.dispose();
});

test("disposal aborts requests and prevents late renders without cancelling server jobs", async () => {
  const pending = deferred();
  let signal;
  const calls = [];
  const app = harness(async (path, options) => { calls.push(path); signal = options.signal; return pending.promise; });
  await app.session.navigate("builds");
  const open = app.session.open(`${one}/build`);
  app.session.dispose();
  const before = app.publications();
  pending.resolve(observation()); await open;
  assert.equal(signal.aborted, true);
  assert.equal(app.publications(), before);
  assert.deepEqual(calls, ["/api/inspector/inspect"]);
});

test("malformed and foreign handoffs leave an actionable error rather than a usable observation", async () => {
  const calls = [];
  const app = harness(async (path) => { calls.push(path); return observation(two); });
  await app.session.navigate("builds/%invalid");
  assert.match(app.state.error, /invalid path/);
  assert.equal(calls.length, 0);
  await app.session.open(`${one}/build`);
  assert.match(app.state.error, /does not belong/);
  assert.equal(canOpenDeviceTree(app.state), false);
  app.session.dispose();
});
