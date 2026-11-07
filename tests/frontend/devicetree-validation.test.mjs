import test from "node:test";
import assert from "node:assert/strict";
import {createValidationSession, diagnosticSelection, projectsValidationRoute} from "../../src/zephyr_utilities/features/devicetree/web/validation.js";

const source = '&uart0 {\r\n  status = "okay";\r\n};\r\n';
const projectId = "a".repeat(32); const configurationId = "b".repeat(32); const attemptId = "c".repeat(32);
const handoff = {feature: "projects", project_id: projectId, configuration_id: configurationId, attempt_id: attemptId};
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; }
function harness(wrap = (api) => api, retained = null) {
  const state = retained || {data: {id: "context", revision: 1, workspace: "/workspace", project: {id: projectId},
    application: {path: "/workspace/app"}, configuration: {id: configurationId}},
  document: {path: "/workspace/app/board.overlay"}, draftView: {ready: true, entry: {generation: 0, text: source,
    server: {id: "draft", revision: 0, sha256: "hash", text: source, dirty: false}}}};
  let workspace = "/workspace"; let session; let publications = 0;
  const calls = []; const timers = new Map(); let timerId = 0;
  const plan = {id: "plan", mode: "saved-source", candidate: {path: state.document.path, text: source, sha256: "hash"},
    recipe: {display_configure: "cmake -S app -B output"}, limitations: ["Save first"], build_dir: "/workspace/app/build/output"};
  let result = {plan_id: plan.id, status: "running", job: {id: "job", status: "running", logs: ["Configuring"]}, candidate: plan.candidate};
  const publish = () => { publications++; session?.sync(); };
  const drafts = {pending: () => state.draftView.entry.text !== state.draftView.entry.server.text,
    pause() { if (state.draftView.entry.externalOperation) return null; state.draftView.entry.externalOperation = true;
      publish(); return () => { state.draftView.entry.externalOperation = false; publish(); }; }};
  const api = wrap(async (url, {body}) => {
    const route = url.split("/").at(-1); calls.push([route, structuredClone(body)]);
    if (route === "review") return {status: "ready", plan: structuredClone(plan)};
    if (route === "submit") return {plan_id: plan.id, job: {id: "job", status: "queued"}};
    if (route === "status") return structuredClone(result);
    if (route === "cancel") return {...structuredClone(result), job: {...result.job, status: "cancelled"}, status: "cancelled"};
    if (route === "handoff") return {handoff};
    throw new Error("Unexpected request");
  });
  session = createValidationSession({api, state, drafts, workspace: () => workspace, onChange: publish,
    setTimer(callback) { timers.set(++timerId, callback); return timerId; }, clearTimer(id) { timers.delete(id); }});
  session.sync();
  return {session, state, calls, timers, plan, drafts, publications: () => publications,
    workspace(value) { workspace = value; session.sync(); },
    result(value) { result = {...result, ...value}; },
    type(value) { state.draftView.entry.text = value; state.draftView.entry.generation++; session.sync(); },
    dispose() { session.dispose(); }};
}

test("opening stays idle; review alone cannot execute tools", async () => {
  const app = harness(); assert.deepEqual(app.calls, []);
  assert.equal(await app.session.review(), true);
  assert.deepEqual(app.calls.map(([route]) => route), ["review"]);
  assert.equal(app.state.draftView.entry.externalOperation, true);
  app.session.closeReview();
  assert.equal(app.state.draftView.entry.externalOperation, false);
  assert.equal(app.state.validationView.review, null);
  app.dispose();
});

test("pending and saved-server dirty drafts require Save without creating a plan", async () => {
  const app = harness(); app.type(source + "/* pending */");
  assert.equal(await app.session.review(), false); assert.match(app.state.validationView.message, /Save this draft/);
  app.state.draftView.entry.server.text = app.state.draftView.entry.text;
  app.state.draftView.entry.server.dirty = true;
  assert.equal(await app.session.review(), false); assert.deepEqual(app.calls, []); app.dispose();
});

test("an empty new overlay must be created before Configure can be reviewed", async () => {
  const app = harness();
  Object.assign(app.state.draftView.entry.server, {text: "", dirty: false, baseline_exists: false, needs_creation: true});
  app.state.draftView.entry.text = "";
  assert.equal(await app.session.review(), false);
  assert.match(app.state.validationView.message, /Save this draft/); assert.deepEqual(app.calls, []); app.dispose();
});

test("confirmation sends exact guards and starts polling only the reviewed plan", async () => {
  const app = harness(); await app.session.review(); await app.session.submit();
  assert.deepEqual(app.calls.map(([route]) => route), ["review", "submit"]);
  assert.deepEqual(app.calls[1][1], {workspace: "/workspace", context_id: "context", expected_revision: 1,
    draft_id: "draft", expected_draft_revision: 0, expected_draft_sha256: "hash", plan_id: "plan"});
  assert.equal(app.state.validationView.review, null); assert.equal(app.state.draftView.entry.externalOperation, false);
  assert.equal(app.timers.size, 1);
  await app.session.refresh(); assert.equal(app.state.validationView.run.result.job.status, "running");
  app.dispose(); assert.equal(app.timers.size, 0);
});

test("new typing invalidates review and prevents stale submission", async () => {
  const app = harness(); await app.session.review(); app.type(source + "/* next */");
  assert.equal(app.state.validationView.review, null); assert.equal(await app.session.submit(), false);
  assert.deepEqual(app.calls.map(([route]) => route), ["review"]); app.dispose();
});

test("delayed review is ignored after a workspace switch", async () => {
  const wait = deferred(); const app = harness((api) => async (...args) => { await wait.promise; return api(...args); });
  const pending = app.session.review(); app.workspace("/elsewhere"); wait.resolve();
  assert.equal(await pending, false); assert.equal(app.state.validationView.review, null); app.dispose();
});

test("candidate mismatch cannot become an executable review", async () => {
  const app = harness((api) => async (...args) => { const data = await api(...args); data.plan.candidate.text = "another source"; return data; });
  assert.equal(await app.session.review(), false); assert.match(app.state.validationView.error, /does not match/);
  assert.equal(app.state.draftView.entry.externalOperation, false); app.dispose();
});

test("unsupported selections show their reason and never submit", async () => {
  const app = harness(() => async () => ({status: "blocked", message: "Register this application in Projects first."}));
  assert.equal(await app.session.review(), false); assert.match(app.state.validationView.message, /Register/);
  assert.equal(await app.session.submit(), false); app.dispose();
});

test("editing while Configure runs retains evidence with a stale label", async () => {
  const app = harness(); await app.session.review(); await app.session.submit(); app.type(source + "/* newer */");
  app.result({status: "configured", job: {id: "job", status: "completed"}, handoff});
  await app.session.refresh();
  assert.equal(app.state.validationView.stale, true);
  assert.equal(app.state.validationView.run.result.status, "configured");
  assert.equal(app.state.draftView.entry.text, source + "/* newer */"); app.dispose();
});

test("backend changed-input evidence stays stale even when local source is unchanged", async () => {
  const app = harness(); await app.session.review(); await app.session.submit();
  app.result({status: "stale", stale: true, message: "Relative include changed.", job: {id: "job", status: "completed"}});
  await app.session.refresh(); assert.equal(app.state.validationView.stale, true); app.dispose();
});

test("leaving stops polling without cancelling; reopening resumes original job", async () => {
  const app = harness(); await app.session.review(); await app.session.submit(); app.dispose();
  assert.equal(app.calls.some(([route]) => route === "cancel"), false);
  const reopened = harness((api) => api, app.state); reopened.session.resume();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(reopened.state.validationView.run.plan.id, "plan");
  assert.deepEqual(reopened.calls.map(([route]) => route), ["status"]); reopened.dispose();
});

test("late job completion cannot replace the view after workspace change", async () => {
  const wait = deferred(); const app = harness((api) => async (path, options) => {
    if (path.endsWith("/status")) await wait.promise; return api(path, options);
  });
  await app.session.review(); await app.session.submit(); const pending = app.session.refresh();
  app.workspace("/another"); wait.resolve(); await pending;
  assert.equal(app.state.validationView.run, null); app.dispose();
});

test("a late running reply from a disposed view cannot regress a completed result", async () => {
  const wait = deferred(); const app = harness((api) => async (path, options) => {
    const result = await api(path, options); if (path.endsWith("/status")) await wait.promise; return result;
  });
  await app.session.review(); await app.session.submit(); const pending = app.session.refresh(); app.dispose();
  const reopened = harness((api) => api, app.state);
  reopened.result({status: "inconclusive", job: {id: "job", status: "completed"}});
  await reopened.session.refresh(); wait.resolve(); await pending;
  assert.equal(reopened.state.validationView.run.result.job.status, "completed"); reopened.dispose();
});

test("explicit cancellation addresses the captured plan and keeps the draft", async () => {
  const app = harness(); await app.session.review(); await app.session.submit(); await app.session.cancel();
  assert.deepEqual(app.calls.at(-1), ["cancel", {workspace: "/workspace", plan_id: "plan"}]);
  assert.equal(app.state.validationView.run.result.status, "cancelled");
  assert.equal(app.state.draftView.entry.text, source); assert.equal(app.timers.size, 0); app.dispose();
});

test("Cancel Configure is not dropped while a status poll is in flight", async () => {
  const wait = deferred(); const app = harness((api) => async (path, options) => {
    const result = await api(path, options); if (path.endsWith("/status")) await wait.promise; return result;
  });
  await app.session.review(); await app.session.submit(); const pending = app.session.refresh();
  assert.equal(await app.session.cancel(), true); wait.resolve(); await pending;
  assert.equal(app.state.validationView.run.result.job.status, "cancelled");
  assert.equal(app.calls.filter(([route]) => route === "cancel").length, 1); app.dispose();
});

test("failed configuration preserves its diagnostic and log", async () => {
  const app = harness(); await app.session.review(); await app.session.submit();
  app.result({status: "failed", diagnostics: [{message: "Unknown label", line: 2, path: app.plan.candidate.path}],
    job: {id: "job", status: "completed", logs: ["devicetree error: unknown label"]}});
  await app.session.refresh(); assert.equal(app.state.validationView.run.result.diagnostics[0].line, 2);
  assert.equal(app.timers.size, 0); app.dispose();
});

test("lost submit acknowledgement is reconciled by status without replaying execution", async () => {
  const app = harness((api) => async (path, options) => {
    const data = await api(path, options); if (path.endsWith("/submit")) throw new Error("Response lost"); return data;
  });
  await app.session.review(); assert.equal(await app.session.submit(), false);
  assert.equal(app.state.validationView.run.uncertain, true);
  await app.session.refresh(); assert.equal(app.state.validationView.run.uncertain, false);
  assert.equal(app.calls.filter(([route]) => route === "submit").length, 1); app.dispose();
});

test("an unsubmitted review result ends uncertain status polling and permits a new review", async () => {
  const app = harness((api) => async (path, options) => {
    if (path.endsWith("/submit")) throw new Error("Input guard rejected submission"); return api(path, options);
  });
  await app.session.review(); await app.session.submit();
  app.result({status: "ready", job: null}); await app.session.refresh();
  assert.equal(app.state.validationView.run.uncertain, false); assert.equal(app.timers.size, 0);
  assert.equal(await app.session.review(), true); app.dispose();
});

test("Projects handoff carries exact project, configuration and attempt without execution", async () => {
  const app = harness(); await app.session.review(); await app.session.submit();
  assert.equal(await app.session.handoff(), `projects/applications/${projectId}/${configurationId}/${attemptId}`);
  assert.equal(app.calls.at(-1)[0], "handoff"); app.dispose();
});

test("handoff navigation rejects non-project destinations and malformed identifiers", () => {
  assert.equal(projectsValidationRoute({...handoff, feature: "inspector"}), null);
  assert.equal(projectsValidationRoute({...handoff, project_id: "../elsewhere"}), null);
  assert.equal(projectsValidationRoute({...handoff, configuration_id: null, attempt_id: null}), `projects/applications/${projectId}/original`);
});

test("diagnostic line selection handles CRLF and Unicode without mapping invalid lines", () => {
  const value = "// 𐐀\r\nstatus = 1;\r\n";
  assert.deepEqual(diagnosticSelection(value, {line: 2, column: 9}), [6, 17]);
  assert.equal(diagnosticSelection(value, {line: 0}), null);
  assert.equal(diagnosticSelection(value, {line: 4}), null);
});
