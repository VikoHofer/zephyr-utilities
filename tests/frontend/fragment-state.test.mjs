import test from "node:test";
import assert from "node:assert/strict";
import {createCreatorSession, configurationBody} from "../../src/zephyr_utilities/features/projects/web/creator-state.js";
import {automaticFragments, fragmentSelectionError, fragmentSummary, profileFragments} from "../../src/zephyr_utilities/features/projects/web/fragment-state.js";

const workspace = "/workspace";
const fragments = () => ({schema_version: 1, kconfig: {mode: "append", files: ["configs/debug.conf", "configs/logging.conf"]}, overlays: {mode: "replace", files: ["boards/sensor.overlay"]}});
const profile = (selection) => ({schema_version: selection ? 2 : 1, board_id: "board", target: "board/chip", sdk: {id: "sdk"}, ...(selection ? {fragments: selection} : {})});
const project = (id = "app", selection) => ({id, workspace, name: id, path: `${workspace}/${id}`, profile: profile(selection), configurations: [], attempts: []});
const snapshot = {id: "snapshot", fingerprint: "stable", workspace: {path: workspace}, boards: [{id: "board", status: "discovered", targets: ["board/chip"]}], sdks: [{id: "sdk", status: "discovered", toolchains: [{family: "GNU", compiler: "/sdk/gcc"}]}]};
const catalogue = (id = "app") => ({workspace, project_id: id, files: {kconfig: ["prj.conf", "configs/debug.conf", "configs/logging.conf"], overlays: ["app.overlay", "boards/sensor.overlay"]}, diagnostics: []});
const response = (projects) => ({workspace, projects, snapshot, jobs: []});
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
function harness(api, projects = [project()], state) {
  let selectedWorkspace = workspace;
  state ||= {workspace, projects, snapshot, jobs: []};
  const session = createCreatorSession({api, state, workspace: () => selectedWorkspace, onChange: () => {}, schedule: () => 1, unschedule: () => {}});
  return {state, session, switchWorkspace: (path) => { selectedWorkspace = path; }};
}

test("automatic legacy configurations do not load files or introduce override arguments", async () => {
  const calls = [];
  const app = harness(async (path, options) => { calls.push([path, options.body]); return {configuration: {id: "saved", project_id: "app", workspace}}; });
  app.session.editConfiguration("app"); app.session.changeConfigurationField("app", "name", "Automatic");
  await app.session.saveConfiguration("app");
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/projects/configurations");
  assert.equal(calls[0][1].fragments, undefined);
  assert.equal(fragmentSummary(undefined, "kconfig"), "Automatic application selection");
  app.session.dispose();
});

test("fragment editing preserves order, separates categories and saves explicit metadata only", async () => {
  const calls = [];
  const app = harness(async (path, options) => {
    calls.push([path, options.body]);
    return path.endsWith("configuration-files") ? catalogue() : {configuration: {id: "saved", project_id: "app", workspace, profile: profile(options.body.fragments)}};
  });
  app.session.editConfiguration("app"); app.session.changeConfigurationField("app", "name", "Debug sensor");
  app.session.changeFragment("app", "kconfig", "mode", "append"); await flush();
  app.session.changeFragment("app", "kconfig", "add", "configs/logging.conf");
  app.session.changeFragment("app", "kconfig", "add", "configs/debug.conf");
  app.session.changeFragment("app", "kconfig", "up", "configs/debug.conf");
  app.session.changeFragment("app", "overlays", "mode", "replace");
  app.session.changeFragment("app", "overlays", "add", "boards/sensor.overlay");
  assert.deepEqual(app.state.configurationDrafts.app.fragments, fragments());
  assert.match(fragmentSummary(fragments(), "kconfig"), /1\. configs\/debug.conf → 2\. configs\/logging.conf/);
  const submitted = configurationBody(app.state.configurationDrafts.app);
  submitted.fragments.kconfig.files.reverse();
  assert.deepEqual(app.state.configurationDrafts.app.fragments, fragments(), "request snapshots cannot mutate the draft");
  await app.session.saveConfiguration("app");
  assert.deepEqual(calls.map(([path]) => path), ["/api/projects/configuration-files", "/api/projects/configurations"]);
  assert.deepEqual(calls[1][1].fragments, fragments());
  assert.equal(app.state.jobs.length, 0);
  app.session.dispose();
});

test("copying a saved configuration keeps fragment order without mutating the saved profile", async () => {
  const source = project("app", fragments());
  const app = harness(async () => catalogue(), [source]);
  app.session.editConfiguration("app"); await flush();
  app.session.changeFragment("app", "kconfig", "down", "configs/debug.conf");
  app.session.changeFragment("app", "overlays", "remove", "boards/sensor.overlay");
  assert.deepEqual(source.profile.fragments, fragments());
  assert.deepEqual(app.state.configurationDrafts.app.fragments.kconfig.files, ["configs/logging.conf", "configs/debug.conf"]);
  assert.deepEqual(app.state.configurationDrafts.app.fragments.overlays.files, []);
  assert.match(fragmentSelectionError(app.state.configurationDrafts.app.fragments, app.state.configurationFiles.app), /at least one/);
  app.session.changeFragment("app", "kconfig", "mode", "auto");
  assert.deepEqual(app.state.configurationDrafts.app.fragments.kconfig, {mode: "auto", files: []});
  app.session.dispose();
});

test("pending, failed and incomplete file catalogues block explicit fragment saves", async () => {
  const pending = deferred(); const calls = [];
  const app = harness(async (path) => { calls.push(path); return pending.promise; }, [project("app", fragments())]);
  app.session.editConfiguration("app");
  await app.session.saveConfiguration("app");
  assert.match(app.state.error, /finish loading/);
  pending.reject(new Error("Source scan exceeded its directory limit.")); await flush();
  await app.session.saveConfiguration("app");
  assert.match(app.state.error, /directory limit/);
  assert.deepEqual(calls, ["/api/projects/configuration-files"]);
  assert.deepEqual(app.state.configurationDrafts.app.fragments, fragments());
  app.session.dispose();
});

test("missing selected files remain visible and block save until restored or removed", async () => {
  let current = catalogue();
  const app = harness(async () => current, [project("app", fragments())]);
  app.session.editConfiguration("app"); await flush();
  current = {...catalogue(), files: {...catalogue().files, kconfig: ["prj.conf", "configs/logging.conf"]}};
  await app.session.loadConfigurationFiles("app");
  assert.deepEqual(app.state.configurationDrafts.app.fragments.kconfig.files, fragments().kconfig.files);
  await app.session.saveConfiguration("app");
  assert.match(app.state.error, /no longer available/);
  current = catalogue(); await app.session.loadConfigurationFiles("app");
  assert.equal(fragmentSelectionError(app.state.configurationDrafts.app.fragments, app.state.configurationFiles.app), "");
  app.session.dispose();
});

test("duplicate, unavailable and overflowing file additions do not change selection", async () => {
  const app = harness(async () => catalogue());
  app.session.editConfiguration("app"); app.session.changeFragment("app", "kconfig", "mode", "replace"); await flush();
  app.session.changeFragment("app", "kconfig", "add", "prj.conf");
  app.session.changeFragment("app", "kconfig", "add", "prj.conf");
  app.session.changeFragment("app", "kconfig", "add", "../outside.conf");
  assert.deepEqual(app.state.configurationDrafts.app.fragments.kconfig.files, ["prj.conf"]);
  const paths = Array.from({length: 33}, (_, i) => `configs/${i}.conf`);
  app.state.configurationFiles.app.files.kconfig = paths;
  app.state.configurationDrafts.app.fragments.kconfig.files = paths.slice(0, 32);
  app.session.changeFragment("app", "kconfig", "add", paths[32]);
  assert.equal(app.state.configurationDrafts.app.fragments.kconfig.files.length, 32);
  app.session.dispose();
});

test("late catalogues cannot replace a newer refresh or another application's list", async () => {
  const first = deferred(); const second = deferred(); let reads = 0;
  const app = harness(async () => ++reads === 1 ? first.promise : second.promise, [project("app", fragments()), project("other", fragments())]);
  app.session.editConfiguration("app"); app.session.editConfiguration("other");
  second.resolve(catalogue("other")); await flush();
  first.resolve(catalogue()); await flush();
  assert.equal(app.state.configurationFiles.app, undefined);
  assert.equal(app.state.configurationFiles.other.ready, true);
  assert.equal(app.state.configurationEditor, "other");
  app.session.dispose();
});

test("workspace change and disposal discard pending file catalogues", async () => {
  const pending = deferred();
  const app = harness(async (path) => path.endsWith("configuration-files") ? pending.promise : {workspace: "/other", projects: [], jobs: []}, [project("app", fragments())]);
  app.session.editConfiguration("app"); app.switchWorkspace("/other"); await app.session.load();
  pending.resolve(catalogue()); await flush();
  assert.deepEqual(app.state.configurationFiles, {});
  assert.deepEqual(app.state.configurationDrafts, {});
  app.session.dispose();
  const late = deferred(); const leaving = harness(async () => late.promise, [project("app", fragments())]);
  leaving.session.editConfiguration("app"); leaving.session.dispose();
  late.resolve(catalogue()); await flush();
  assert.equal(leaving.state.configurationFiles.app.ready, false);
});

test("reopening a draft preserves choices but refreshes the catalogue before saving", async () => {
  const app = harness(async () => catalogue(), [project("app", fragments())]);
  app.session.editConfiguration("app"); await flush();
  app.session.changeFragment("app", "kconfig", "remove", "configs/logging.conf");
  app.session.dispose();
  const pending = deferred();
  const reopened = harness(async (path) => path.endsWith("configuration-files") ? pending.promise : response([project("app", fragments())]), [], app.state);
  await reopened.session.load();
  assert.deepEqual(reopened.state.configurationDrafts.app.fragments.kconfig.files, ["configs/debug.conf"]);
  assert.equal(reopened.state.configurationFiles.app.loading, true);
  pending.resolve(catalogue()); await flush();
  assert.equal(fragmentSelectionError(reopened.state.configurationDrafts.app.fragments, reopened.state.configurationFiles.app), "");
  reopened.session.dispose();
});

test("foreign or malformed catalogues are rejected without dropping selected paths", async () => {
  for (const data of [{...catalogue(), workspace: "/other"}, {...catalogue(), project_id: "other"}, {...catalogue(), files: {kconfig: [], overlays: null}}]) {
    const app = harness(async () => data, [project("app", fragments())]);
    app.session.editConfiguration("app"); await flush();
    assert.equal(app.state.configurationFiles.app.ready, false);
    assert.match(app.state.configurationFiles.app.error, /could not be verified/);
    assert.deepEqual(app.state.configurationDrafts.app.fragments, fragments());
    app.session.dispose();
  }
});

test("unsupported saved profile and fragment versions cannot silently build or save automatic defaults", async () => {
  for (const changed of [{...profile(fragments()), schema_version: 99}, {...profile(fragments()), fragments: {...fragments(), schema_version: 99}}, {...profile(), schema_version: 2}, {...profile(fragments()), schema_version: 1}, {...profile(fragments()), schema_version: undefined}]) {
    const source = {...project(), profile: changed}; const calls = [];
    const app = harness(async (path) => { calls.push(path); return catalogue(); }, [source]);
    app.session.editConfiguration("app"); await app.session.saveConfiguration("app"); await app.session.previewBuild("app", "build");
    assert.match(app.state.error, /unsupported/);
    assert.deepEqual(calls, []);
    assert.equal(app.state.buildPlan, null);
    app.session.dispose();
  }
  assert.equal(profileFragments(profile()), undefined);
  assert.deepEqual(automaticFragments().overlays, {mode: "auto", files: []});
});

test("closing while files load allows a clean catalogue request when reopening", async () => {
  const pending = deferred(); let count = 0;
  const app = harness(async () => ++count === 1 ? pending.promise : catalogue(), [project("app", fragments())]);
  app.session.editConfiguration("app"); app.session.closeConfiguration(); app.session.editConfiguration("app"); await flush();
  pending.resolve({...catalogue(), files: {kconfig: [], overlays: []}}); await flush();
  assert.equal(app.state.configurationFiles.app.ready, true);
  assert.deepEqual(app.state.configurationFiles.app.files, catalogue().files);
  app.session.dispose();
});
