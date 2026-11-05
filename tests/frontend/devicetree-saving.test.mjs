import test from "node:test";
import assert from "node:assert/strict";
import {createDraftSession, draftKey} from "../../src/zephyr_utilities/features/devicetree/web/drafts.js";
import {createSaveSession} from "../../src/zephyr_utilities/features/devicetree/web/saving.js";

const original = '&uart0 { status = "disabled"; };\r\n';
const edited = original.replace("disabled", "okay");
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; }
function initial() {
  const document = {id: "overlay", path: "/workspace/app/board.overlay", sha256: "base", text: original, availability: "available"};
  const context = {schema_version: 1, id: "context", revision: 1, workspace: "/workspace", fingerprint: "fingerprint",
    application: {path: "/workspace/app"}, project: {id: "project"}, configuration: {id: null, name: "Original"},
    target: {requested: "board/chip"}, editing_document: {...document}, documents: [{...document}]};
  return {context, document};
}
function backend() {
  const start = initial();
  const calls = []; const undo = []; const receipts = new Map();
  let context = start.context; let document = start.document; let disk = original; let revision = 0; let source = original;
  let digest = "base"; let baseDigest = "base";
  const payload = () => ({schema_version: 1, id: "draft", context_fingerprint: context.fingerprint, document_id: document.id,
    path: document.path, revision, sha256: digest, base_sha256: baseDigest, text: source, dirty: source !== disk,
    can_undo: Boolean(undo.length), can_redo: false, syntax: {state: "complete", blocked: false, diagnostics: []}, nodes: [],
    diff: {text: source === disk ? "" : "changed", truncated: false}, capabilities: {save: true}});
  function envelope(status) {
    const nextContext = {...context, id: `context-${revision}`, fingerprint: `fingerprint-${revision}`, revision: 2};
    document = {...document, text: disk, sha256: baseDigest};
    context = {...nextContext, editing_document: {...document}, documents: [{...document}]};
    return {status, context: structuredClone(context), document: {...document}, draft: payload(), receipt: {status}};
  }
  const comparison = () => ({status: source === disk ? "current" : "conflict", path: document.path,
    base_text: original, text: source, current_text: disk, current_sha256: baseDigest, comparison_id: "comparison",
    can_reload: true, diff: {text: "comparison diff", truncated: false}, message: "Current source observed."});
  const record = {id: "recovery", path: document.path, revision: 8, text: edited, base_text: original};
  async function api(path, {body}) {
    const route = path.replace("/api/devicetree/editor/drafts/", ""); calls.push([route, structuredClone(body)]);
    if (route === "recovery/list") return {records: [record], diagnostics: []};
    if (route === "recovery/read") return {record, comparison: comparison()};
    if (route === "recovery/discard") return {status: "discarded", id: body.recovery_id};
    if (route === "recovery/restore") { source = record.text; digest = "recovered"; revision++; return envelope("restored"); }
    if (route === "open" || route === "get") return payload();
    if (route === "save" && receipts.has(body.plan_id)) return structuredClone(receipts.get(body.plan_id));
    assert.equal(body.expected_draft_revision, revision); assert.equal(body.expected_draft_sha256, digest);
    if (route === "source") { undo.push(source); source = body.text; revision++; digest = `hash-${revision}`; return payload(); }
    if (route === "undo") { source = undo.pop(); revision++; digest = `hash-${revision}`; return payload(); }
    if (route === "review") return {status: "ready", plan: {id: "plan", path: document.path, candidate_text: source,
      candidate_sha256: digest, base_sha256: baseDigest, expires_at: Date.now() / 1000 + 600,
      diff: {text: "review diff", truncated: false}}};
    if (route === "compare") return comparison();
    if (route === "reload") {
      if (body.mode === "discard") { source = disk; undo.length = 0; }
      revision++; digest = `hash-${revision}`; return envelope("reloaded");
    }
    if (route === "save") {
      disk = source; baseDigest = digest; revision++;
      const result = envelope("saved"); receipts.set(body.plan_id, result); return structuredClone(result);
    }
    throw new Error(`Unexpected route ${route}`);
  }
  return {api, calls, payload, comparison, record, disk: () => disk};
}
async function harness(wrap = (api) => api) {
  const server = backend(); const first = initial(); let workspace = "/workspace";
  const state = {data: first.context, document: first.document}; let saving;
  const publish = () => saving?.sync();
  const drafts = createDraftSession({api: wrap(server.api), state, workspace: () => workspace, onChange: publish,
    setTimer: () => 1, clearTimer: () => {}});
  await drafts.bind(first.context, first.document);
  saving = createSaveSession({api: wrap(server.api), state, workspace: () => workspace, drafts,
    adopt(context, document) { state.data = context; state.document = document; publish(); }, onChange: publish});
  return {state, server, drafts, saving, current: () => state.draftView.entry,
    workspace(value) { workspace = value; saving.sync(); }, dispose() { saving.dispose(); drafts.dispose(); }};
}

test("Save flushes pending typing, adopts new context, and Undo becomes an unsaved inverse", async () => {
  const app = await harness(); const oldKey = draftKey(app.state.data, app.state.document);
  app.drafts.type(edited);
  assert.equal(await app.saving.reviewSave(), true);
  assert.deepEqual(app.server.calls.map(([route]) => route), ["open", "source", "review"]);
  assert.equal(app.state.saveView.review.data.candidate_text, edited);
  assert.equal(app.server.disk(), original);
  assert.equal(await app.saving.confirm(), true);
  assert.equal(app.server.disk(), edited); assert.equal(app.current().server.dirty, false);
  assert.equal(app.state.draftCache[oldKey], undefined);
  assert.equal(app.current(), app.state.draftCache[draftKey(app.state.data, app.state.document)]);
  assert.match(app.state.saveView.message, /Overlay saved/);
  assert.equal(await app.drafts.undo(), true);
  assert.equal(app.current().text, original); assert.equal(app.current().server.dirty, true);
  assert.equal(app.state.saveView.message, "");
  assert.equal(app.server.disk(), edited);
  app.dispose();
});

test("success notices expire when source typing or the selected binding changes", async () => {
  const app = await harness(); app.drafts.type(edited); await app.saving.reviewSave(); await app.saving.confirm();
  assert.match(app.state.saveView.message, /Overlay saved/);
  app.drafts.type(edited + "/* next change */\n");
  assert.equal(app.state.saveView.message, "");
  app.state.saveView.message = "The overlay already matches this draft. No replacement was needed.";
  app.state.data = {...app.state.data, id: "another-context"}; app.saving.sync();
  assert.equal(app.state.saveView.message, ""); app.dispose();
});

test("new typing invalidates a shown Save review without writing", async () => {
  const app = await harness(); app.drafts.type(edited); await app.saving.reviewSave();
  app.drafts.type(edited + "/* later */\n");
  assert.equal(app.state.saveView.review, null); assert.equal(await app.saving.confirm(), false);
  assert.equal(app.server.disk(), original); assert.match(app.current().text, /later/);
  app.dispose();
});

test("a delayed review cannot open after workspace navigation", async () => {
  const wait = deferred();
  const app = await harness((api) => async (path, options) => { if (path.endsWith("/review")) await wait.promise; return api(path, options); });
  const opening = app.saving.reviewSave(); app.workspace("/other"); wait.resolve();
  assert.equal(await opening, false); assert.equal(app.state.saveView.review, null);
  assert.equal(app.server.disk(), original); app.dispose();
});

test("a lost Save response permits only explicit same-plan receipt retrieval", async () => {
  let lost = false;
  const app = await harness((api) => async (path, options) => {
    const result = await api(path, options);
    if (path.endsWith("/save") && !lost) { lost = true; throw new Error("Reply lost"); }
    return result;
  });
  app.drafts.type(edited); await app.saving.reviewSave();
  assert.equal(await app.saving.confirm(), false); assert.equal(app.server.disk(), edited);
  assert.equal(app.state.saveView.review.uncertain, true);
  assert.equal(await app.saving.confirm(), true);
  const saves = app.server.calls.filter(([route]) => route === "save");
  assert.deepEqual(saves[0][1], saves[1][1]); assert.equal(app.current().server.dirty, false);
  app.dispose();
});

test("known uncertain publication displays its receipt and cannot be replayed", async () => {
  const app = await harness((api) => async (path, options) => path.endsWith("/save")
    ? {status: "conflict", receipt: {status: "uncertain", recovery: {journal: "/data/recovery.json", staged_path: "/app/stage"}},
      message: "Candidate visibility is uncertain", comparison: {path: "/workspace/app/board.overlay", text: edited, can_reload: false}}
    : api(path, options));
  app.drafts.type(edited); await app.saving.reviewSave();
  assert.equal(await app.saving.confirm(), false);
  assert.equal(app.state.saveView.review.kind, "outcome");
  assert.equal(app.state.saveView.review.receipt.status, "uncertain");
  assert.equal(await app.saving.confirm(), false);
  assert.equal(app.current().text, edited); assert.equal(app.current().server.dirty, true);
  app.dispose();
});

test("external conflict preserves draft and exposes a fresh comparison", async () => {
  const app = await harness((api) => async (path, options) => path.endsWith("/save")
    ? {status: "conflict", message: "External writer changed the file", comparison: {path: "/workspace/app/board.overlay",
      text: edited, current_text: "external", current_sha256: "external-hash", comparison_id: "new-comparison", can_reload: true, status: "conflict"}}
    : api(path, options));
  app.drafts.type(edited); await app.saving.reviewSave(); assert.equal(await app.saving.confirm(), false);
  assert.equal(app.state.saveView.review.kind, "compare");
  assert.equal(app.state.saveView.review.data.current_text, "external"); assert.equal(app.current().text, edited);
  app.dispose();
});

test("discard uses the reviewed comparison token and does not write the file", async () => {
  const app = await harness(); app.drafts.type(edited);
  await app.saving.reviewReload(); assert.equal(app.current().text, edited);
  assert.equal(await app.saving.confirm(), true);
  const reload = app.server.calls.find(([route]) => route === "reload")[1];
  assert.equal(reload.mode, "discard"); assert.equal(reload.comparison_id, "comparison");
  assert.equal(app.current().text, original); assert.equal(app.current().server.can_undo, false);
  assert.equal(app.server.disk(), original); app.dispose();
});

test("keep after comparison retains pending local typing against the refreshed base", async () => {
  const app = await harness(); app.drafts.type(edited);
  await app.saving.compare(); assert.equal(await app.saving.confirm(), true);
  assert.equal(app.current().text, edited); assert.equal(app.drafts.pending(), true);
  assert.equal(app.server.calls.find(([route]) => route === "reload")[1].mode, "keep");
  assert.equal(app.server.disk(), original); app.dispose();
});

test("typing during Save is retained as an explicit conflict after acknowledgement", async () => {
  const wait = deferred();
  const app = await harness((api) => async (path, options) => { if (path.endsWith("/save")) await wait.promise; return api(path, options); });
  app.drafts.type(edited); await app.saving.reviewSave(); const saving = app.saving.confirm();
  // Defensive input can arrive despite a rendered read-only field.
  app.drafts.type(edited + "/* newer */\n"); wait.resolve();
  assert.equal(await saving, true); assert.match(app.current().text, /newer/);
  assert.equal(app.current().conflict, true);
  assert.equal(app.server.disk(), edited); assert.equal(app.state.saveView.review, null);
  app.dispose();
});

test("recovery list results do not leak across workspace switches", async () => {
  const wait = deferred();
  const app = await harness((api) => async (path, options) => { if (path.endsWith("recovery/list")) await wait.promise; return api(path, options); });
  const listing = app.saving.listRecoveries(); app.workspace("/other"); wait.resolve();
  assert.equal(await listing, false); assert.equal(app.state.saveView.recoveries, null);
  assert.equal(app.state.saveView.recoveryLoading, false); app.dispose();
});

test("recovery restore requires an explicit reviewed comparison and leaves source unsaved", async () => {
  const app = await harness(); await app.saving.listRecoveries();
  assert.equal(await app.saving.reviewRecovery("recovery"), true); assert.equal(app.server.disk(), original);
  assert.equal(await app.saving.confirm(), true);
  const restore = app.server.calls.find(([route]) => route === "recovery/restore")[1];
  assert.equal(restore.comparison_id, "comparison"); assert.equal(restore.expected_recovery_revision, 8);
  assert.equal(restore.mode, "keep"); assert.equal(app.current().text, edited);
  assert.equal(app.current().server.dirty, true); assert.equal(app.server.disk(), original); app.dispose();
});

test("recovery removal has a separate confirmation and leaves open source unchanged", async () => {
  const app = await harness(); await app.saving.listRecoveries(); await app.saving.reviewRecovery("recovery");
  assert.equal(app.saving.reviewRecoveryDiscard(), true);
  assert.equal(app.server.calls.some(([route]) => route === "recovery/discard"), false);
  assert.equal(await app.saving.confirm(), true);
  assert.equal(app.state.saveView.recoveries.records.length, 0);
  assert.equal(app.current().text, original); assert.equal(app.server.disk(), original); app.dispose();
});

test("foreign save envelopes cannot mutate a retained draft", async () => {
  const app = await harness((api) => async (path, options) => {
    const result = await api(path, options);
    if (path.endsWith("/save")) result.context.project.id = "another-project";
    return result;
  });
  app.drafts.type(edited); await app.saving.reviewSave(); const before = app.current().server;
  assert.equal(await app.saving.confirm(), false);
  assert.equal(app.current().server, before); assert.equal(app.current().text, edited);
  assert.match(app.state.saveView.error, /another context/); app.dispose();
});

test("expired review cannot submit a save", async () => {
  const app = await harness((api) => async (path, options) => {
    const result = await api(path, options);
    if (path.endsWith("/review")) result.plan.expires_at = 1;
    return result;
  });
  await app.saving.reviewSave(); assert.equal(await app.saving.confirm(), false);
  assert.match(app.state.saveView.error, /expired/);
  assert.equal(app.server.calls.some(([route]) => route === "save"), false); app.dispose();
});

test("incomplete-source rejection retains typing and gives its explicit explanation", async () => {
  const app = await harness((api) => async (path, options) => path.endsWith("/review")
    ? {status: "blocked", message: "Repair incomplete source before saving."} : api(path, options));
  app.drafts.type("&uart0 {"); assert.equal(await app.saving.reviewSave(), false);
  assert.equal(app.current().text, "&uart0 {"); assert.match(app.state.saveView.error, /Repair incomplete/);
  assert.equal(app.current().externalOperation, null); app.dispose();
});

test("failed recovery reads are visible in the recovery section", async () => {
  const app = await harness((api) => async (path, options) => {
    if (path.endsWith("recovery/read")) throw new Error("Unknown recovery schema; record preserved");
    return api(path, options);
  });
  assert.equal(await app.saving.reviewRecovery("recovery"), false);
  assert.match(app.state.saveView.recoveryError, /record preserved/);
  assert.equal(app.state.saveView.review, null); app.dispose();
});

test("leaving the feature while Save finishes does not adopt the late result", async () => {
  const wait = deferred();
  const app = await harness((api) => async (path, options) => { if (path.endsWith("/save")) await wait.promise; return api(path, options); });
  app.drafts.type(edited); await app.saving.reviewSave(); const contextBefore = app.state.data;
  const saving = app.saving.confirm(); app.saving.dispose(); wait.resolve();
  assert.equal(await saving, false); assert.equal(app.state.data, contextBefore);
  assert.equal(app.server.disk(), edited); assert.equal(app.state.saveView.review, null); app.dispose();
});

test("late typing cannot hide an uncertain publication receipt", async () => {
  const wait = deferred();
  const app = await harness((api) => async (path, options) => {
    if (path.endsWith("/save")) {
      await wait.promise;
      return {status: "conflict", receipt: {status: "uncertain", recovery: {journal: "/data/evidence"}},
        message: "The candidate may already be visible", comparison: {path: "/workspace/app/board.overlay", text: edited, can_reload: false}};
    }
    return api(path, options);
  });
  app.drafts.type(edited); await app.saving.reviewSave(); const saving = app.saving.confirm();
  app.drafts.type(edited + "/* later */\n"); wait.resolve(); await saving;
  assert.equal(app.state.saveView.review.kind, "outcome");
  assert.match(app.state.saveView.review.message, /already be visible/);
  assert.match(app.current().text, /later/); app.dispose();
});

test("lost Save reply with newer typing keeps an explicit unknown-outcome review", async () => {
  const wait = deferred();
  const app = await harness((api) => async (path, options) => {
    if (path.endsWith("/save")) { await wait.promise; await api(path, options); throw new Error("Reply lost"); }
    return api(path, options);
  });
  app.drafts.type(edited); await app.saving.reviewSave(); const saving = app.saving.confirm();
  app.drafts.type(edited + "/* later */\n"); wait.resolve(); await saving;
  assert.equal(app.state.saveView.review.kind, "outcome");
  assert.equal(app.state.saveView.review.receipt.status, "unknown");
  assert.match(app.current().text, /later/); assert.equal(await app.saving.confirm(), false); app.dispose();
});
