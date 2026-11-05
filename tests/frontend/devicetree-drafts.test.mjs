import test from "node:test";
import assert from "node:assert/strict";
import {createDraftSession, draftKey, editorText, sourceFromEditor, sourceOffset, sourceSelection,
  nodeSelection, propertySelection, restoreNodeSelection, restorePropertySelection,
  utf16ToUtf8, utf8ToUtf16} from "../../src/zephyr_utilities/features/devicetree/web/drafts.js";

const baseText = '&uart0 { status = "disabled"; };\r\n';
function context(suffix = "a") { return {id: `context-${suffix}`, revision: 1, workspace: "/workspace",
  fingerprint: `fingerprint-${suffix}`, configuration: {name: suffix}, target: {requested: "board/chip"}}; }
function document(suffix = "a") { return {id: `overlay-${suffix}`, path: `/workspace/app/${suffix}.overlay`,
  text: baseText, sha256: `base-${suffix}`, availability: "available"}; }
function response(suffix = "a", extra = {}) {
  return {schema_version: 1, id: `draft-${suffix}`, context_fingerprint: `fingerprint-${suffix}`,
    document_id: `overlay-${suffix}`, path: `/workspace/app/${suffix}.overlay`, revision: 0,
    sha256: `base-${suffix}`, base_sha256: `base-${suffix}`, text: baseText, dirty: false,
    can_undo: false, can_redo: false, syntax: {state: "complete", blocked: false, diagnostics: []},
    nodes: [{id: "node", name: "&uart0", start: 0, end: 31, properties: []}], diff: {text: "", truncated: false},
    ...extra};
}
function deferred() {
  let resolve; let reject;
  const promise = new Promise((done, failed) => { resolve = done; reject = failed; });
  return {promise, resolve, reject};
}
function server() {
  const records = new Map();
  const calls = [];
  async function api(path, {body}) {
    calls.push([path.split("/").at(-1), body]);
    const suffix = body.context_id.slice("context-".length);
    if (!records.has(suffix)) records.set(suffix, {value: response(suffix), undo: [], redo: []});
    const entry = records.get(suffix);
    const action = path.split("/").at(-1);
    if (action === "open" || action === "get") return structuredClone(entry.value);
    assert.equal(body.expected_draft_revision, entry.value.revision);
    assert.equal(body.expected_draft_sha256, entry.value.sha256);
    let text;
    if (action === "source") text = body.text;
    if (action === "command") text = entry.value.text.replace('"disabled"', '"okay"');
    if (action === "undo") { entry.redo.push(entry.value.text); text = entry.undo.pop(); }
    if (action === "redo") { entry.undo.push(entry.value.text); text = entry.redo.pop(); }
    if (action === "source" || action === "command") {
      if (text !== entry.value.text) { entry.undo.push(entry.value.text); entry.redo = []; }
    }
    const revision = entry.value.revision + Number(text !== entry.value.text);
    entry.value = {...entry.value, text, revision, sha256: `hash-${suffix}-${revision}`, dirty: text !== baseText,
      can_undo: Boolean(entry.undo.length), can_redo: Boolean(entry.redo.length),
      syntax: {state: text.includes("BROKEN") ? "incomplete" : "complete", blocked: text.includes("BROKEN"), diagnostics: []},
      nodes: text.includes("BROKEN") ? [] : response(suffix).nodes};
    return structuredClone(entry.value);
  }
  return {api, calls, records};
}
function harness(api, state = {}) {
  let path = "/workspace";
  let publications = 0;
  let nextId = 0;
  const timers = new Map();
  const session = createDraftSession({api, state, workspace: () => path, onChange: () => publications++,
    setTimer(fn) { const id = ++nextId; timers.set(id, fn); return id; }, clearTimer(id) { timers.delete(id); }});
  return {session, state, timers, publications: () => publications, changeWorkspace(value) { path = value; },
    current: () => state.draftView?.entry};
}

test("source mapping preserves mixed newlines and Unicode byte positions", () => {
  const source = "/* 😀 温度 */\r\n&a {\n status = \"disabled\";\r\n};";
  assert.equal(sourceFromEditor(source, editorText(source)), source);
  assert.equal(sourceFromEditor(source, editorText(source).replace("disabled", "okay")), source.replace("disabled", "okay"));
  const start = source.indexOf("&a");
  const byte = utf16ToUtf8(source, start);
  assert.equal(utf8ToUtf16(source, byte), start);
  const [normalized] = sourceSelection(source, byte, byte + 2);
  assert.equal(sourceOffset(source, normalized), byte);
  const emoji = source.indexOf("😀");
  assert.equal(utf16ToUtf8(source, emoji + 1), utf16ToUtf8(source, emoji));
  assert.equal(sourceFromEditor("a\r\nb\r\n", "a\nb\nc\n"), "a\r\nb\r\nc\r\n");
});

test("opening and rebinding one context does not duplicate reads", async () => {
  const backend = server(); const app = harness(backend.api);
  assert.equal(await app.session.bind(context(), document()), true);
  await app.session.bind(context(), document());
  assert.deepEqual(backend.calls.map(([action]) => action), ["open"]);
  assert.equal(app.current().text, baseText);
  assert.equal(app.state.draftView.ready, true);
  app.session.dispose();
});

test("new typing during source submission is retained and serialized into the next revision", async () => {
  const backend = server(); const pending = deferred(); let sourceCalls = 0;
  const app = harness(async (path, options) => {
    if (path.endsWith("/source") && ++sourceCalls === 1) { await pending.promise; }
    return backend.api(path, options);
  });
  await app.session.bind(context(), document());
  app.session.type(baseText.replace("disabled", "okay"));
  const syncing = app.session.flush();
  app.session.type(baseText.replace("disabled", "reserved"));
  const concurrent = app.session.flush();
  pending.resolve();
  assert.equal(await syncing, true); assert.equal(await concurrent, true);
  assert.equal(app.current().text, baseText.replace("disabled", "reserved"));
  assert.equal(app.current().server.text, app.current().text);
  assert.equal(app.current().server.revision, 2);
  assert.equal(sourceCalls, 2);
  app.session.dispose();
});

test("a lost accepted source response reconciles before sending newer local text", async () => {
  const backend = server(); let lose = true;
  const app = harness(async (path, options) => {
    const result = await backend.api(path, options);
    if (path.endsWith("/source") && lose) { lose = false; throw new Error("Lost reply"); }
    return result;
  });
  await app.session.bind(context(), document());
  app.session.type(baseText.replace("disabled", "okay"));
  assert.equal(await app.session.flush(), false);
  app.session.type(baseText.replace("disabled", "reserved"));
  assert.equal(await app.session.flush(), true);
  assert.deepEqual(backend.calls.map(([action]) => action), ["open", "source", "get", "source"]);
  assert.equal(app.current().server.revision, 2);
  assert.equal(app.current().text, app.current().server.text);
  app.session.dispose();
});

test("an uncertain visual command is reconciled without replaying it", async () => {
  const backend = server();
  const app = harness(async (path, options) => {
    const result = await backend.api(path, options);
    if (path.endsWith("/command")) throw new Error("Lost reply");
    return result;
  });
  await app.session.bind(context(), document());
  assert.equal(await app.session.command({kind: "set_status", node_id: "node", status: "okay"}), false);
  assert.equal(await app.session.reconcile(), true);
  assert.equal(app.current().text, baseText.replace("disabled", "okay"));
  assert.equal(backend.calls.filter(([action]) => action === "command").length, 1);
  app.session.dispose();
});

test("typing while a reconciliation request is pending cannot be overwritten by its response", async () => {
  const backend = server(); const pending = deferred();
  const app = harness(async (path, options) => { if (path.endsWith("/get")) await pending.promise; return backend.api(path, options); });
  await app.session.bind(context(), document());
  const checking = app.session.reconcile();
  app.session.type("new local text");
  pending.resolve(); await checking;
  assert.equal(app.current().text, "new local text");
  assert.equal(app.session.pending(), true);
  app.session.dispose();
});

test("conflicting canonical text is not overwritten until the user chooses retained text", async () => {
  const backend = server(); const app = harness(backend.api);
  await app.session.bind(context(), document());
  app.session.type("my local text");
  const record = backend.records.get("a");
  record.value = {...record.value, text: "other editor text", revision: 1, sha256: "foreign"};
  assert.equal(await app.session.reconcile(), false);
  assert.equal(app.current().conflict, true);
  assert.equal(app.current().text, "my local text");
  assert.equal(await app.session.flush(), false);
  assert.equal(backend.calls.filter(([action]) => action === "source").length, 0);
  assert.equal(await app.session.resolveConflict("local"), true);
  assert.equal(app.current().server.text, "my local text");
  app.session.dispose();
});

test("choosing the server draft is explicit and sends no source mutation", async () => {
  const backend = server(); const app = harness(backend.api);
  await app.session.bind(context(), document()); app.session.type("my text");
  const record = backend.records.get("a"); record.value = {...record.value, text: "other", revision: 1, sha256: "foreign"};
  await app.session.reconcile();
  assert.equal(await app.session.resolveConflict("server"), true);
  assert.equal(app.current().text, "other"); assert.equal(app.session.pending(), false);
  assert.equal(backend.calls.some(([action]) => action === "source"), false);
  app.session.dispose();
});

test("switching context keeps pending text separate and resumes only its original binding", async () => {
  const backend = server(); const app = harness(backend.api);
  await app.session.bind(context(), document()); app.session.type("draft A pending");
  await app.session.bind(context("b"), document("b"));
  assert.equal(app.current().text, baseText);
  await app.session.command({kind: "set_status", node_id: "node", status: "okay"});
  await app.session.bind(context(), document());
  assert.equal(app.current().text, "draft A pending");
  await app.session.flush();
  assert.equal(backend.records.get("a").value.text, "draft A pending");
  assert.equal(backend.records.get("b").value.text, baseText.replace("disabled", "okay"));
  app.session.dispose();
});

test("workspace changes and disposal retain pending text without hidden follow-up mutations", async () => {
  const backend = server(); const pending = deferred();
  const app = harness(async (path, options) => { if (path.endsWith("/source")) await pending.promise; return backend.api(path, options); });
  await app.session.bind(context(), document()); app.session.type("first");
  const syncing = app.session.flush(); app.session.type("latest");
  app.changeWorkspace("/other"); app.session.bind(null, null);
  const publications = app.publications(); app.session.dispose();
  pending.resolve(); await syncing;
  assert.equal(app.publications(), publications);
  assert.equal(app.state.draftCache[draftKey(context(), document())].text, "latest");
  assert.equal(backend.calls.filter(([action]) => action === "source").length, 1);
  const reopened = harness(backend.api, app.state);
  await reopened.session.bind(context(), document()); await reopened.session.flush();
  assert.equal(reopened.current().server.text, "latest");
  reopened.session.dispose();
});

test("invalid typing remains canonical and one history restores source and projection", async () => {
  const backend = server(); const app = harness(backend.api);
  await app.session.bind(context(), document());
  app.session.type("BROKEN {");
  assert.equal(await app.session.undo(), true);
  assert.deepEqual(backend.calls.map(([action]) => action), ["open", "source", "undo"]);
  assert.equal(app.current().server.text, baseText);
  assert.equal(await app.session.redo(), true);
  assert.equal(app.current().server.text, "BROKEN {");
  assert.deepEqual(app.current().server.nodes, []);
  assert.equal(app.current().server.syntax.blocked, true);
  app.session.dispose();
});

test("simultaneous undo attempts do not submit stale duplicate history requests", async () => {
  const backend = server(); const app = harness(backend.api);
  await app.session.bind(context(), document()); app.session.type("new");
  await Promise.all([app.session.undo(), app.session.undo()]);
  assert.equal(backend.calls.filter(([action]) => action === "undo").length, 1);
  app.session.dispose();
});

test("oversized or NUL typing is retained for correction without a source request", async () => {
  const backend = server(); const app = harness(backend.api);
  await app.session.bind(context(), document());
  for (const value of ["温".repeat(90000), "bad\0value"]) {
    app.session.type(value); assert.equal(await app.session.flush(), false);
    assert.equal(app.current().text, value); assert.match(app.current().error, /retained/);
  }
  assert.equal(backend.calls.some(([action]) => action === "source"), false);
  app.session.dispose();
});

test("foreign draft responses never become an editable projection", async () => {
  for (const invalid of [response("b"), response("a", {schema_version: 2}), response("a", {revision: -1})]) {
    const app = harness(async () => invalid);
    assert.equal(await app.session.bind(context(), document()), false);
    assert.equal(app.state.draftView.ready, false); assert.equal(app.current().server, null);
    app.session.dispose();
  }
});

test("opening a new context fingerprint cannot reuse an old local draft", async () => {
  const backend = server(); const app = harness(backend.api);
  await app.session.bind(context(), document()); app.session.type("old configuration draft");
  await app.session.bind(context("b"), document("b"));
  assert.equal(app.current().text, baseText);
  assert.equal(Object.keys(app.state.draftCache).length, 2);
  app.session.dispose();
});

test("failed untouched opens can be evicted without exhausting browser retention", async () => {
  const app = harness(async () => { throw new Error("Temporary failure"); });
  for (let index = 0; index < 20; index++) await app.session.bind(context(String(index)), document(String(index)));
  assert.equal(Object.keys(app.state.draftCache).length, 16);
  assert.equal(app.state.draftView.error, "Temporary failure");
  assert.equal(Boolean(app.state.draftView.entry), true);
  app.session.dispose();
});

test("retention capacity never silently evicts pending typing", async () => {
  const backend = server(); const app = harness(backend.api);
  for (let index = 0; index < 16; index++) {
    await app.session.bind(context(String(index)), document(String(index)));
    app.session.type(`pending ${index}`);
  }
  assert.equal(await app.session.bind(context("overflow"), document("overflow")), false);
  assert.match(app.state.draftView.error, /16 drafts/);
  for (let index = 0; index < 16; index++) {
    assert.equal(app.state.draftCache[draftKey(context(String(index)), document(String(index)))].text, `pending ${index}`);
  }
  app.session.dispose();
});

test("property and repeated-node selections survive value and offset changes without guessing removed occurrences", () => {
  const properties = [{id: "a", name: "status"}, {id: "b", name: "rate"}, {id: "c", name: "status"}];
  const hint = propertySelection(properties, 2);
  assert.equal(restorePropertySelection(properties.map((value) => ({...value, id: value.id + "new"})), hint), 2);
  assert.equal(restorePropertySelection(properties.slice(0, 2), hint), -1);
  assert.equal(restorePropertySelection([...properties, {name: "status"}], hint), -1);
  assert.equal(restorePropertySelection(properties, null), -1);
  const nodes = [{name: "&uart0", start: 0}, {name: "&uart0", start: 30}];
  const nodeHint = nodeSelection(nodes, 1);
  assert.equal(restoreNodeSelection([{name: "&uart0", start: 0}, {name: "&uart0", start: 1000}], nodeHint), 1);
  assert.equal(restoreNodeSelection([{name: "&uart0", start: 0}], nodeHint), -1);
  assert.equal(restoreNodeSelection([], nodeHint), -1);
  assert.equal(restoreNodeSelection(nodes, nodeHint), 1);
});

test("unexpected typing during a visual mutation is retained as a conflict across rebind", async () => {
  const backend = server(); const pending = deferred();
  const app = harness(async (path, options) => { if (path.endsWith("/command")) await pending.promise; return backend.api(path, options); });
  await app.session.bind(context(), document());
  const changing = app.session.command({kind: "set_status", node_id: "node", status: "okay"});
  assert.equal(app.current().flightKind, "command");
  app.session.type("newer local typing");
  pending.resolve(); await changing;
  assert.equal(app.current().text, "newer local typing");
  assert.equal(app.current().conflict, true);
  assert.equal(await app.session.flush(), false);
  await app.session.bind({...context(), revision: 2}, document());
  assert.equal(app.current().conflict, true);
  assert.equal(app.current().text, "newer local typing");
  assert.equal(backend.calls.some(([action]) => action === "source"), false);
  app.session.dispose();
});

test("late input during a history flush is retained without undoing the newer typing", async () => {
  const backend = server(); const pending = deferred(); let first = true;
  const app = harness(async (path, options) => {
    if (path.endsWith("/source") && first) { first = false; await pending.promise; }
    return backend.api(path, options);
  });
  await app.session.bind(context(), document()); app.session.type("initial typing");
  const undoing = app.session.undo();
  assert.equal(app.current().historyOperation, true);
  app.session.type("later input");
  pending.resolve(); assert.equal(await undoing, false);
  assert.equal(app.current().text, "later input");
  assert.equal(app.current().server.text, "later input");
  assert.equal(backend.calls.some(([action]) => action === "undo"), false);
  assert.match(app.current().error, /retained/);
  app.session.dispose();
});
