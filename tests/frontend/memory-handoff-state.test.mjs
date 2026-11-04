import test from "node:test";
import assert from "node:assert/strict";
import {createMemoryHandoff, inspectionSelection} from "../../src/zephyr_utilities/features/memory/web/handoff-state.js";

const workspace = "/workspace";
const id = "a".repeat(32);
const route = (domain = "application") => `inspect/${id}/${encodeURIComponent(domain)}`;
const preview = (domain = "application") => ({workspace, inspection_id: id, domain,
  build: {path: `/workspace/build/${domain}`, elf_path: `/workspace/build/${domain}/zephyr/zephyr.elf`, ready: true},
  elf: {path: `/workspace/build/${domain}/zephyr/zephyr.elf`, sha256: "e".repeat(64)},
  context: {effective_target: "board/soc/cpu"},
});
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; }
function harness(api, state = {}) {
  let path = workspace;
  let publications = 0;
  const session = createMemoryHandoff({api, state, workspace: () => path, onChange: () => publications++});
  return {session, state, changeWorkspace(value) { path = value; }, publications: () => publications};
}

test("inspection links validate IDs and decode the exact image name", () => {
  assert.equal(inspectionSelection("overview"), null);
  assert.deepEqual(inspectionSelection(route("boot loader")), {inspection_id: id, domain: "boot loader"});
  assert.deepEqual(inspectionSelection(route("remote/cpu")), {inspection_id: id, domain: "remote/cpu"});
  for (const view of ["inspect", "inspect/invalid/app", `inspect/${id}`, `inspect/${id}/a/b`, `inspect/${id}/%`, route(""), route("\u0000")]) {
    assert.throws(() => inspectionSelection(view));
  }
});

test("opening only previews the exact inspected image; explicit generation carries its selection", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options]); return preview("boot loader"); });
  await app.session.open(route("boot loader"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/memory/inspection");
  assert.deepEqual(calls[0][1].body, {workspace, inspection_id: id, domain: "boot loader"});
  assert.equal(app.session.canGenerate(), true);
  const body = app.session.jobBody("baseline");
  assert.deepEqual(body, {build_path: "/workspace/build/boot loader", name: "baseline", selection: {workspace, inspection_id: id, domain: "boot loader"}});
  body.selection.domain = "modified";
  assert.equal(app.session.jobBody().selection.domain, "boot loader");
  assert.equal(calls.length, 1, "building a submission body never starts a job");
  app.session.dispose();
});

test("loading a new image disables generation and ignores a late prior response", async () => {
  const first = deferred();
  let oldSignal;
  const app = harness(async (path, options) => {
    if (options.body.domain === "application") { oldSignal = options.signal; return first.promise; }
    return preview("boot");
  });
  const opening = app.session.open(route());
  assert.equal(app.session.canGenerate(), false);
  assert.throws(() => app.session.jobBody());
  await app.session.open(route("boot"));
  assert.equal(oldSignal.aborted, true);
  first.resolve(preview()); await opening;
  assert.equal(app.state.preview.domain, "boot");
  assert.equal(app.session.jobBody().selection.domain, "boot");
  app.session.dispose();
});

test("manual selection clears the inspection and blocks a late preview", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise);
  const opening = app.session.open(route());
  app.session.clear();
  pending.resolve(preview()); await opening;
  assert.equal(app.state.active, false);
  assert.equal(app.state.preview, null);
  assert.equal(app.state.selection, null);
  assert.equal(app.session.canGenerate(), false);
  assert.throws(() => app.session.jobBody());
  app.session.dispose();
});

test("workspace changes immediately make a prepared handoff unusable", async () => {
  const app = harness(async () => preview());
  await app.session.open(route());
  app.changeWorkspace("/other");
  assert.equal(app.session.canGenerate(), false);
  assert.throws(() => app.session.jobBody());
  await app.session.open("overview");
  assert.equal(app.state.preview, null);
  assert.equal(app.state.selection, null);
  app.session.dispose();
});

test("the same route revalidates when the shell announces another workspace", async () => {
  const app = harness(async (path, options) => {
    if (options.body.workspace !== workspace) throw new Error("This inspection belongs to another workspace.");
    return preview();
  });
  await app.session.open(route());
  app.changeWorkspace("/other");
  await app.session.open(route());
  assert.equal(app.state.preview, null);
  assert.equal(app.state.workspace, "/other");
  assert.match(app.state.error, /another workspace/);
  assert.equal(app.session.canGenerate(), false);
  app.session.dispose();
});

test("late workspace results cannot appear after workspace reconciliation", async () => {
  const pending = deferred();
  const app = harness(async () => pending.promise);
  const opening = app.session.open(route());
  app.changeWorkspace("/other");
  await app.session.open("overview");
  pending.resolve(preview()); await opening;
  assert.equal(app.state.preview, null);
  assert.equal(app.state.loading, false);
  assert.equal(app.state.workspace, "/other");
  app.session.dispose();
});

test("unavailable tooling is shown without enabling report generation", async () => {
  const result = preview();
  result.build.ready = false;
  result.build.error = "The configured Python interpreter is missing.";
  const app = harness(async () => result);
  await app.session.open(route());
  assert.match(app.state.preview.build.error, /interpreter is missing/);
  assert.equal(app.session.canGenerate(), false);
  assert.throws(() => app.session.jobBody());
  app.session.dispose();
});

test("foreign identities and alternate ELF responses cannot be submitted", async () => {
  for (const alter of [
    (value) => { value.workspace = "/other"; },
    (value) => { value.inspection_id = "b".repeat(32); },
    (value) => { value.domain = "boot"; },
    (value) => { value.build.elf_path = "/workspace/build/another.elf"; },
  ]) {
    const result = preview(); alter(result);
    const app = harness(async () => result);
    await app.session.open(route());
    assert.equal(app.state.preview, null);
    assert.match(app.state.error, /image changed/);
    assert.equal(app.session.canGenerate(), false);
    app.session.dispose();
  }
});

test("failed revalidation removes old readiness and preserves the actionable error", async () => {
  let stale = false;
  const app = harness(async () => {
    if (stale) throw new Error("Build outputs changed. Inspect the build again.");
    return preview();
  });
  await app.session.open(route());
  stale = true;
  await app.session.refresh();
  assert.equal(app.state.preview, null);
  assert.equal(app.session.canGenerate(), false);
  assert.match(app.state.error, /Inspect the build again/);
  app.session.dispose();
});

test("a rejected job requires another successful preview before retrying", async () => {
  const app = harness(async () => preview());
  await app.session.open(route());
  app.session.invalidate("The inspected ELF changed.");
  assert.equal(app.session.canGenerate(), false);
  assert.equal(app.state.preview, null);
  assert.match(app.state.error, /ELF changed/);
  await app.session.refresh();
  assert.equal(app.session.canGenerate(), true);
  app.session.dispose();
});

test("leaving aborts preview work and reopening must revalidate before enabling generation", async () => {
  const pending = deferred();
  let signal;
  const app = harness(async (path, options) => { signal = options.signal; return pending.promise; });
  const opening = app.session.open(route());
  app.session.dispose();
  const publications = app.publications();
  pending.resolve(preview()); await opening;
  assert.equal(signal.aborted, true);
  assert.equal(app.state.preview, null);
  assert.equal(app.publications(), publications);
  let reads = 0;
  const reopened = harness(async () => { reads++; return preview(); }, app.state);
  assert.equal(reopened.session.canGenerate(), false);
  await reopened.session.open(route());
  assert.equal(reads, 1);
  assert.equal(reopened.session.canGenerate(), true);
  reopened.session.dispose();
});

test("malformed handoffs show an error without reading or starting a job", async () => {
  let calls = 0;
  const app = harness(async () => { calls++; return preview(); });
  await app.session.open("inspect/invalid/app");
  assert.equal(calls, 0);
  assert.equal(app.state.active, true);
  assert.match(app.state.error, /invalid/);
  assert.equal(app.session.canGenerate(), false);
  app.session.dispose();
});
