import test from "node:test";
import assert from "node:assert/strict";
import {DRAWING_SIZE, paintStroke, encodeGray8, imageCrc32, imageSha256, validatePreparedImage, validateInferenceResult,
  createDrawingSession, sendProblem, drawingChangedSinceRun, submittedCapture} from "../../src/zephyr_utilities/features/tinyml/web/drawing-state.js";
import {mountDrawing} from "../../src/zephyr_utilities/features/tinyml/web/drawing.js";

const workspace = "/workspace", connectionId = "d".repeat(32), portId = "a".repeat(64), id = "b".repeat(32), model = "c".repeat(64);
const pixels = Array(784).fill(0); pixels[14 * 28 + 14] = 255;
const imageHash = await imageSha256(new Uint8Array(pixels)), crc = imageCrc32(pixels);
const prepared = (path = workspace, changes = {}) => ({schema_version: 1, workspace: path, pixels: [...pixels], image_sha256: imageHash, crc32: crc, ...changes});
const metadata = (path = workspace) => ({workspace: path, operation: "mnist-infer", port_id: portId, connection_id: connectionId, image_sha256: imageHash, crc32: crc});
const result = (path = workspace, changes = {}) => ({schema_version: 1, workspace: path, port: {id: portId, device: "/dev/ttyACM0", description: "Debug UART"},
  board: null, image_sha256: imageHash, crc32: crc,
  prediction: 7, inference_us: 1234, scores: Array.from({length: 10}, (_, digit) => digit === 7 ? 1 : 0), timing: "invoke-only", ...changes});
const job = (status = "queued", path = workspace, changes = {}) => ({id, feature: "tinyml", status, metadata: metadata(path), logs: [],
  result: status === "completed" ? result(path) : null, ...changes});
const connection = () => ({id: connectionId, port_id: portId, device: "/dev/ttyACM0", description: "Debug UART", state: "open"});
const control = (operation, status = "queued", path = workspace) => ({id: (operation === "uart-open" ? "e" : "f").repeat(32), feature: "tinyml",
  status, logs: [], metadata: {workspace: path, operation, ...(operation === "uart-open" ? {port_id: portId} : {connection_id: connectionId})},
  result: status === "completed" ? {schema_version: 1, workspace: path, connection: operation === "uart-open" ? connection() : null} : null});
const ports = (path = workspace, changes = {}) => ({schema_version: 1, workspace: path, available: true, reason: "", connection: connection(),
  ports: [{id: portId, device: "/dev/ttyACM0", description: "Debug UART", hwid: "synthetic"}], jobs: [], ...changes});
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
function harness(custom, existing) {
  const calls = [], timers = new Map(); let timerId = 0, path = workspace, updates = 0;
  const state = existing || {};
  const api = async (url, options = {}) => {
    calls.push([url, options]);
    if (custom) return custom(url, options, path);
    if (url.startsWith("/api/tinyml/ports")) return ports(path);
    if (url === "/api/tinyml/prepare") return prepared(path);
    if (url === "/api/tinyml/infer") return job("queued", path);
    if (url.endsWith("/cancel")) return job("running", path);
    if (url.startsWith("/api/jobs/")) return job("completed", path);
    throw new Error(`Unexpected API: ${url}`);
  };
  const session = createDrawingSession({api, state, workspace: () => path, onChange: () => updates++,
    schedule: (callback, delay) => { const key = ++timerId; timers.set(key, {callback, delay}); return key; }, unschedule: (key) => timers.delete(key)});
  return {api, state, session, calls, timers, updates: () => updates, changeWorkspace(next) { path = next; }};
}
async function ready(app) { await app.session.navigate(); app.session.selectPort(portId); app.session.stroke({x: 100, y: 80}, {x: 120, y: 200}); }
async function runTimer(app, expectedDelay) {
  const entry = [...app.timers.entries()].find(([, timer]) => timer.delay === expectedDelay);
  assert.ok(entry, `A ${expectedDelay} ms timer should be scheduled.`);
  app.timers.delete(entry[0]); await entry[1].callback();
}
async function settleUntil(predicate) {
  for (let turn = 0; turn < 30 && !predicate(); turn++) await Promise.resolve();
  assert.ok(predicate(), "The requested asynchronous observation should settle.");
}

test("the 25-pixel white brush has round ends, clips safely and preserves the black field", () => {
  const drawing = new Uint8Array(DRAWING_SIZE ** 2);
  assert.equal(paintStroke(drawing, {x: 100, y: 100}, {x: 100, y: 100}), true);
  assert.equal(drawing[100 * 336 + 88], 255); assert.equal(drawing[100 * 336 + 112], 255);
  assert.equal(drawing[100 * 336 + 87], 0); assert.equal(drawing[87 * 336 + 87], 0);
  assert.equal(paintStroke(drawing, {x: 100, y: 100}, {x: 100, y: 100}), false);
  paintStroke(drawing, {x: -40, y: -10}, {x: 500, y: 500});
  assert.equal(drawing[0], 255); assert.equal(drawing.at(-1), 255); assert.equal(drawing.length, 112896);
  assert.ok(drawing.every((value) => value === 0 || value === 255));
});

test("base64 keeps raw gray8 bytes and CRC agrees with the upload protocol", () => {
  const raw = Uint8Array.from({length: 112896}, (_, index) => index % 256);
  assert.deepEqual(Uint8Array.from(atob(encodeGray8(raw)), (character) => character.charCodeAt(0)), raw);
  assert.equal(imageCrc32(new Uint8Array(784)), "fbc49a13");
  assert.equal(validatePreparedImage(prepared(), workspace).pixels.length, 784);
});

test("prepared responses must match workspace, exact byte dimensions and checksum", () => {
  for (const value of [prepared("/other"), prepared(workspace, {pixels: [1]}), prepared(workspace, {pixels: Array(784).fill(256)}),
    prepared(workspace, {pixels: Array(784).fill(0), crc32: "fbc49a13"}),
    prepared(workspace, {crc32: "ffffffff"}), prepared(workspace, {image_sha256: "short"}), prepared(workspace, {schema_version: 2})]) {
    assert.throws(() => validatePreparedImage(value, workspace), /could not be verified/);
  }
});

test("opening and listing ports never opens UART or submits a run", async () => {
  const app = harness(); await app.session.navigate();
  assert.deepEqual(app.calls.map(([url]) => url), [`/api/tinyml/ports?workspace=${encodeURIComponent(workspace)}`]);
  assert.equal(app.state.current.portId, "", "ports are not automatically selected");
  assert.equal(app.state.current.job, null); assert.equal(app.timers.size, 0);
  app.session.dispose();
});

test("Open UART needs only a listed port and remains open until explicit Close", async () => {
  let actualConnection = null, operation = null;
  const app = harness(async (url) => {
    if (url.startsWith("/api/tinyml/ports")) return ports(workspace, {connection: actualConnection, jobs: operation ? [operation] : []});
    if (url === "/api/tinyml/open") { operation = control("uart-open"); return operation; }
    if (url === `/api/jobs/${"e".repeat(32)}`) { actualConnection = connection(); operation = control("uart-open", "completed"); return operation; }
    if (url === "/api/tinyml/close") { actualConnection = null; operation = control("uart-close", "completed"); return operation; }
    throw new Error(`No firmware command should be needed to open UART: ${url}`);
  });
  await app.session.navigate(); app.session.selectPort(portId);
  assert.equal(app.state.current.hasInk, false); await app.session.open();
  assert.equal(app.state.current.controlJob.metadata.operation, "uart-open"); assert.equal(app.state.current.connection, null);
  assert.deepEqual(app.calls.find(([url]) => url === "/api/tinyml/open")[1].body, {workspace, port_id: portId});
  await app.session.poll(); await Promise.resolve();
  assert.equal(app.state.current.connection.id, connectionId); assert.equal(app.state.current.connection.state, "open");
  assert.equal(app.calls.some(([url]) => url === "/api/tinyml/prepare" || url === "/api/tinyml/infer"), false);
  await app.session.close(); await Promise.resolve();
  assert.deepEqual(app.calls.find(([url]) => url === "/api/tinyml/close")[1].body, {workspace, connection_id: connectionId});
  assert.equal(app.state.current.connection, null); assert.equal(app.state.current.controlJob.status, "completed"); app.session.dispose();
});

test("Close remains available during inference and refreshes the actual connection after the close job", async () => {
  let actualConnection = {...connection(), state: "busy", job_id: id}, closing = null, inference = job("running");
  const app = harness(async (url) => {
    if (url.startsWith("/api/tinyml/ports")) return ports(workspace, {connection: actualConnection, jobs: [...(closing ? [closing] : []), inference]});
    if (url === "/api/tinyml/close") { closing = control("uart-close"); return closing; }
    if (url === `/api/jobs/${"f".repeat(32)}`) { actualConnection = null; inference = job("cancelled"); closing = control("uart-close", "completed"); return closing; }
    throw new Error(`Unexpected UART request: ${url}`);
  });
  await app.session.navigate(); await app.session.close();
  assert.equal(app.state.current.controlJob.metadata.operation, "uart-close");
  await app.session.poll(); await settleUntil(() => app.state.current.job.status === "cancelled");
  assert.equal(app.state.current.connection, null); assert.equal(app.state.current.job.status, "cancelled");
  assert.equal(app.calls.some(([url]) => url === "/api/tinyml/open" || url === "/api/tinyml/infer"), false); app.session.dispose();
});

test("opening failures are ordinary UART errors and leaving never closes a persistent connection", async () => {
  const app = harness(async (url) => {
    if (url.startsWith("/api/tinyml/ports")) return ports(workspace, {connection: null});
    if (url === "/api/tinyml/open") return {...control("uart-open", "failed"), error: "Port is busy in another terminal."};
    throw new Error(`Unexpected API: ${url}`);
  });
  await app.session.navigate(); app.session.selectPort(portId); await app.session.open(); await Promise.resolve();
  assert.equal(app.state.current.connection, null); assert.equal(app.state.current.connectionError, "Port is busy in another terminal.");
  app.session.dispose(); assert.equal(app.calls.some(([url]) => url === "/api/tinyml/close"), false);
  const open = harness(); await open.session.navigate(); open.session.dispose();
  assert.equal(open.calls.some(([url]) => url === "/api/tinyml/close"), false);
});

test("received inference results need no board metadata and successful runs preserve the open connection", async () => {
  const app = harness(); await ready(app); await app.session.send(); await app.session.poll(); await Promise.resolve();
  assert.equal(app.state.current.job.result.board, null); assert.equal(app.state.current.job.result.prediction, 7);
  assert.equal(app.state.current.connection.id, connectionId); assert.equal(app.state.current.connection.state, "open");
  assert.equal(app.calls.some(([url]) => url === "/api/tinyml/close"), false);
  const noBoard = result(); delete noBoard.board; assert.equal(validateInferenceResult(noBoard, metadata()), noBoard); app.session.dispose();
});

test("a busy connection from another workspace refreshes its global status without adopting that image", async () => {
  let reads = 0;
  const app = harness(async () => ports(workspace, {connection: ++reads < 4 ? {...connection(), state: "busy", job_id: id} : connection()}));
  await app.session.navigate();
  for (let turn = 0; turn < 3; turn++) await runTimer(app, 1000);
  assert.equal(app.state.current.connection.state, "open"); assert.equal(app.state.current.job, null);
  assert.equal(app.timers.size, 0); assert.ok(app.calls.every(([url]) => url.startsWith("/api/tinyml/ports"))); app.session.dispose();
});

test("foreign busy connection refreshes stop after fifty reads and explicit Refresh restarts observation", async () => {
  const app = harness(async () => ports(workspace, {connection: {...connection(), state: "busy", job_id: id}}));
  await app.session.navigate();
  for (let turn = 0; turn < 50; turn++) await runTimer(app, 1000);
  assert.equal(app.calls.length, 51); assert.equal(app.timers.size, 0);
  assert.match(app.state.current.connectionWatchError, /Refresh UART ports/);
  await app.session.refreshPorts(); assert.equal(app.state.current.connectionWatchError, "");
  assert.deepEqual([...app.timers.values()].map((timer) => timer.delay), [1000]); app.session.dispose();
});

test("foreign connection network failures use bounded retries and disposal stops observation", async () => {
  let reads = 0;
  const app = harness(async () => { if (++reads > 1) throw new Error("Connection status unavailable."); return ports(workspace, {connection: {...connection(), state: "busy", job_id: id}}); });
  await app.session.navigate(); await runTimer(app, 1000);
  for (const delay of [600, 1200, 2400]) await runTimer(app, delay);
  assert.equal(reads, 5); assert.equal(app.timers.size, 0);
  assert.match(app.state.current.connectionWatchError, /retries stopped.*Refresh UART ports/); app.session.dispose();
  const leaving = harness(async () => ports(workspace, {connection: {...connection(), state: "busy", job_id: id}}));
  await leaving.session.navigate(); assert.equal(leaving.timers.size, 1); leaving.session.dispose(); assert.equal(leaving.timers.size, 0);
});

test("stroke completion schedules preparation and edits invalidate older previews", async () => {
  const pending = deferred();
  const app = harness(async (url) => url.startsWith("/api/tinyml/ports") ? ports() : pending.promise);
  await ready(app); app.session.endStroke();
  assert.deepEqual([...app.timers.values()].map((timer) => timer.delay), [180]);
  const preparing = app.session.prepare();
  assert.equal(app.calls.at(-1)[1].body.drawing.width, 336);
  assert.equal(atob(app.calls.at(-1)[1].body.drawing.gray8).length, 112896);
  app.session.stroke({x: 180, y: 80}); pending.resolve(prepared()); await preparing;
  assert.equal(app.state.current.prepared, null); assert.equal(app.state.preparing, false);
  assert.equal(app.calls.filter(([url]) => url === "/api/tinyml/infer").length, 0); app.session.dispose();
});

test("Send needs an open idle connection and a drawing without any board or SDK selection", async () => {
  for (const missing of ["connection", "busy", "ink"]) {
    const app = harness(); await app.session.navigate();
    if (missing !== "ink") app.session.stroke({x: 100, y: 100});
    if (missing === "connection") app.state.current.connection = null;
    if (missing === "busy") app.state.current.connection = {...connection(), state: "busy"};
    assert.ok(sendProblem(app.state));
    await app.session.send(); assert.ok(app.state.current.runError);
    assert.equal(app.calls.length, 1); app.session.dispose();
  }
});

test("an already open connection can send even when port discovery is unavailable or its selection changes", async () => {
  const app = harness(); await ready(app);
  app.state.current.ports = null; app.session.selectPort("");
  assert.equal(sendProblem(app.state), ""); await app.session.send();
  const body = app.calls.find(([url]) => url === "/api/tinyml/infer")[1].body;
  assert.equal(body.connection_id, connectionId); assert.equal(Object.hasOwn(body, "port_id"), false);
  app.session.dispose();
});

test("Send uploads the verified preview through the open connection without a board selection", async () => {
  const app = harness(); await ready(app); await app.session.send();
  const sent = app.calls.find(([url]) => url === "/api/tinyml/infer")[1].body;
  assert.deepEqual(Object.keys(sent).sort(), ["connection_id", "image", "workspace"]);
  assert.equal(sent.connection_id, connectionId); assert.equal(Object.hasOwn(sent, "expected_target"), false);
  assert.equal(atob(sent.image.gray8).length, 784);
  assert.deepEqual([...atob(sent.image.gray8)].map((character) => character.charCodeAt(0)), pixels);
  assert.deepEqual(submittedCapture(app.state).pixels, pixels); assert.equal(app.state.current.job.id, id);
  assert.ok([...app.timers.values()].some((timer) => timer.delay === 600));
  await app.session.poll(); assert.equal(app.state.current.job.result.prediction, 7);
  assert.equal(app.state.current.job.result.board, null); app.session.dispose();
});

test("a prepared image with a forged hash cannot be submitted", async () => {
  const app = harness(async (url) => url.startsWith("/api/tinyml/ports") ? ports() : prepared(workspace, {image_sha256: model}));
  await ready(app); await app.session.send();
  assert.match(app.state.current.prepareError, /hash does not match/);
  assert.equal(app.calls.some(([url]) => url === "/api/tinyml/infer"), false); app.session.dispose();
});

test("drawing or connection changes during preparation require another explicit Send", async () => {
  for (const changed of ["drawing", "connection"]) {
    const pending = deferred();
    const app = harness(async (url) => url.startsWith("/api/tinyml/ports") ? ports() : pending.promise);
    await ready(app); const sending = app.session.send();
    if (changed === "drawing") app.session.clear();
    if (changed === "connection") app.state.current.connection = null;
    pending.resolve(prepared()); await sending;
    assert.equal(app.calls.some(([url]) => url === "/api/tinyml/infer"), false, changed);
    assert.match(app.state.current.notice, /changed before upload/); app.session.dispose();
  }
});

test("editing after submission keeps the run's captured image separate from the new drawing", async () => {
  const pending = deferred(), submitted = deferred();
  const app = harness(async (url) => {
    if (url.startsWith("/api/tinyml/ports")) return ports();
    if (url === "/api/tinyml/prepare") return prepared();
    if (url === "/api/tinyml/infer") { submitted.resolve(); return pending.promise; }
    return job("completed");
  });
  await ready(app); const sending = app.session.send(); await submitted.promise;
  app.session.clear(); pending.resolve(job()); await sending;
  assert.equal(app.state.current.hasInk, false); assert.equal(drawingChangedSinceRun(app.state), true);
  assert.deepEqual(submittedCapture(app.state).pixels, pixels); await app.session.poll();
  assert.equal(app.state.current.job.result.image_sha256, imageHash); assert.equal(drawingChangedSinceRun(app.state), true);
  app.session.dispose();
});

test("malformed or foreign image and port results cannot become a displayed prediction", async () => {
  const invalid = [result(workspace, {image_sha256: model}), result(workspace, {crc32: "ffffffff"}),
    result(workspace, {port: {...result().port, id: model}}), result(workspace, {prediction: 2}),
    result(workspace, {scores: Array(10).fill(NaN)}), result(workspace, {scores: Array(10).fill(0.5)}),
    result(workspace, {inference_us: -1}), result(workspace, {timing: "total"})];
  for (const value of invalid) assert.throws(() => validateInferenceResult(value, metadata()), /does not match/);
  const app = harness(async (url) => url.startsWith("/api/tinyml/ports") ? ports(workspace, {jobs: [job()]}) : job("completed", workspace, {result: invalid[0]}));
  await app.session.navigate(); await app.session.poll();
  assert.equal(app.state.current.job.status, "queued"); assert.match(app.state.current.runError, /does not match/); app.session.dispose();
});

test("restored jobs retain observed identities without pretending the current drawing was sent", async () => {
  const app = harness(async () => ports(workspace, {jobs: [job("completed")]})); await app.session.navigate();
  assert.equal(app.state.current.job.result.prediction, 7); assert.equal(submittedCapture(app.state), null);
  assert.equal(app.state.current.hasInk, false); assert.equal(app.state.current.job.metadata.image_sha256, imageHash); app.session.dispose();
});

test("workspace changes ignore late reads and retain separate drawings and port selections", async () => {
  const pending = deferred(); let first = true;
  const app = harness(async (_url, _options, path) => { if (first) { first = false; return pending.promise; } return ports(path); });
  const reading = app.session.navigate(); app.changeWorkspace("/other"); await app.session.navigate();
  pending.resolve(ports()); await reading;
  assert.equal(app.state.workspace, "/other"); assert.equal(app.state.current.ports.workspace, "/other");
  app.session.stroke({x: 20, y: 20}); app.session.selectPort(portId);
  app.changeWorkspace(workspace); await app.session.navigate(); assert.equal(app.state.current.hasInk, false); assert.equal(app.state.current.portId, "");
  app.changeWorkspace("/other"); await app.session.navigate(); assert.equal(app.state.current.hasInk, true); assert.equal(app.state.current.portId, portId); app.session.dispose();
});

test("leaving clears readers and poll timers without cancelling the server inference job", async () => {
  const pending = deferred();
  const app = harness(async (url) => url.startsWith("/api/tinyml/ports") ? ports(workspace, {jobs: [job()]}) : pending.promise);
  await app.session.navigate(); const polling = app.session.poll();
  const changes = app.updates(); app.session.dispose();
  assert.equal(app.timers.size, 0); assert.equal(app.calls.at(-1)[1].signal.aborted, true);
  pending.resolve(job("completed")); await polling;
  assert.equal(app.updates(), changes); assert.equal(app.state.current.job.status, "queued");
  assert.equal(app.calls.some(([url]) => url.endsWith("/cancel")), false);
});

test("a transient status failure stays visible and the bounded retry observes completion", async () => {
  let reads = 0;
  const app = harness(async (url) => {
    if (url.startsWith("/api/tinyml/ports")) return ports(workspace, {jobs: [job()]});
    if (++reads === 1) throw new Error("Temporary connection loss.");
    return job("completed");
  });
  await app.session.navigate(); await app.session.poll();
  assert.match(app.state.current.runError, /Temporary connection loss.*Retrying UART status \(1 of 3\)/);
  assert.equal(app.state.current.job.status, "queued");
  await runTimer(app, 600);
  assert.equal(app.state.current.job.status, "completed"); assert.equal(app.state.current.runError, "");
  assert.equal(reads, 2); assert.equal(app.timers.size, 0);
  assert.ok(app.calls.every(([url]) => url.startsWith("/api/tinyml/ports") || url === `/api/jobs/${id}`)); app.session.dispose();
});

test("network failures and invalid responses exhaust three retries without resubmitting inference", async () => {
  for (const failure of ["network", "invalid"]) {
    let reads = 0;
    const app = harness(async (url) => {
      if (url.startsWith("/api/tinyml/ports")) return ports(workspace, {jobs: [job()]});
      reads++;
      if (failure === "network") throw new Error("Status endpoint unavailable.");
      return job("completed", workspace, {result: result(workspace, {image_sha256: model})});
    });
    await app.session.navigate(); await app.session.poll();
    for (const delay of [600, 1200, 2400]) await runTimer(app, delay);
    assert.equal(reads, 4, failure); assert.equal(app.timers.size, 0);
    assert.equal(app.state.current.job.status, "queued"); assert.match(app.state.current.runError, /Refresh UART ports to reconnect/);
    assert.equal(app.calls.some(([url]) => url === "/api/tinyml/infer"), false);
    await app.session.refreshPorts(); await app.session.poll();
    assert.match(app.state.current.runError, /Retrying UART status \(1 of 3\)/, "explicit refresh resets the retry budget");
    assert.deepEqual([...app.timers.values()].map((timer) => timer.delay), [600]); app.session.dispose();
  }
});

test("cancellation and disposal stop pending retry timers and late reads", async () => {
  for (const leave of ["cancel", "dispose"]) {
    const cancelling = deferred(); let reads = 0;
    const app = harness(async (url) => {
      if (url.startsWith("/api/tinyml/ports")) return ports(workspace, {jobs: [job()]});
      if (url.endsWith("/cancel")) return cancelling.promise;
      reads++; throw new Error("Temporary status failure.");
    });
    await app.session.navigate(); await app.session.poll(); assert.equal(app.timers.size, 1);
    if (leave === "dispose") { app.session.dispose(); assert.equal(app.timers.size, 0); }
    else {
      const request = app.session.cancel(); assert.equal(app.timers.size, 0, "no retry can run while cancellation is pending");
      cancelling.resolve(job("cancelled")); await request;
      assert.equal(app.state.current.job.status, "cancelled"); assert.equal(app.timers.size, 0); app.session.dispose();
    }
    assert.equal(reads, 1);
  }
});

test("switching workspace and remounting reset status retry budgets", async () => {
  const api = async (url, _options, path) => {
    if (url.startsWith("/api/tinyml/ports")) return ports(path, {jobs: [job("queued", path)]});
    throw new Error("Temporary status failure.");
  };
  const app = harness(api); await app.session.navigate(); await app.session.poll(); await runTimer(app, 600);
  assert.match(app.state.current.runError, /\(2 of 3\)/);
  app.changeWorkspace("/other"); await app.session.navigate(); await app.session.poll();
  assert.match(app.state.current.runError, /\(1 of 3\)/); app.session.dispose();
  const reopened = harness(api, app.state); await reopened.session.navigate(); await reopened.session.poll();
  assert.match(reopened.state.current.runError, /\(1 of 3\)/); reopened.session.dispose();
});

test("cancel is explicit and uses the shared job route while the server retains port ownership", async () => {
  const app = harness(async (url) => url.startsWith("/api/tinyml/ports") ? ports(workspace, {jobs: [job()]}) : job("running"));
  await app.session.navigate(); await app.session.cancel();
  assert.deepEqual(app.calls.at(-1)[0], `/api/jobs/${id}/cancel`); assert.equal(app.calls.at(-1)[1].method, "POST");
  assert.match(app.state.current.notice, /Cancellation requested/); assert.equal(app.state.cancelling, false); app.session.dispose();
});

test("a completion racing with Cancel keeps the completed result and final port status", async () => {
  const app = harness(async (url) => url.startsWith("/api/tinyml/ports") ? ports(workspace, {jobs: [job()]}) : job("completed"));
  await app.session.navigate(); await app.session.cancel();
  assert.equal(app.state.current.job.status, "completed");
  assert.match(app.state.current.notice, /Prediction received/); assert.doesNotMatch(app.state.current.notice, /waiting/); app.session.dispose();
});

test("repeated Send cannot create duplicate submissions while upload is starting", async () => {
  const pending = deferred(), starting = deferred();
  const app = harness(async (url) => {
    if (url.startsWith("/api/tinyml/ports")) return ports();
    if (url === "/api/tinyml/prepare") return prepared();
    starting.resolve(); return pending.promise;
  });
  await ready(app); const sending = app.session.send(); await starting.promise; await app.session.send();
  assert.equal(app.calls.filter(([url]) => url === "/api/tinyml/infer").length, 1);
  pending.resolve(job()); await sending; app.session.dispose();
});

class Element extends EventTarget {
  constructor(tag, document) { super(); Object.assign(this, {tagName: tag, ownerDocument: document, children: [], style: {}, attributes: {}, value: "", hidden: false, disabled: false}); }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(""); }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ""; this.children = children; }
  focus(options) { this.ownerDocument.activeElement = this; this.focusOptions = options; }
  getContext() { return {createImageData: (width, height) => ({data: new Uint8ClampedArray(width * height * 4)}),
    putImageData: (image) => { this.image = image; }, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}}; }
  getBoundingClientRect() { return {left: 0, top: 0, width: 168, height: 168}; }
  setPointerCapture(id) { this.capturedPointer = id; }
}
function event(name, properties) { return Object.assign(new Event(name, {cancelable: true}), properties); }
const descendants = (element) => [element, ...element.children.flatMap(descendants)];
function viewHarness() {
  const document = {activeElement: null, createElement(tag) { return new Element(tag, document); }};
  const root = new Element("div", document), calls = [], navigation = [];
  const context = {state: {}, workspace: {path: workspace}, navigate: (route) => navigation.push(route),
    api: async (url, options = {}) => { calls.push([url, options]); return url.startsWith("/api/tinyml/ports") ? ports() : prepared(); }};
  const view = mountDrawing(root, context);
  const find = (predicate) => descendants(root).find(predicate);
  return {view, context, root, calls, document, navigation, find, canvas: find((element) => element.tagName === "canvas" && element.width === 336),
    button: (label) => find((element) => element.tagName === "button" && element.textContent === label)};
}

test("keyboard users can draw and clear while controls retain focus", async () => {
  const app = viewHarness(); await app.view.navigate(); app.canvas.focus();
  app.canvas.dispatchEvent(event("keydown", {key: " ", shiftKey: false}));
  assert.equal(app.context.state.drawing.current.pixels[168 * 336 + 168], 255);
  app.canvas.dispatchEvent(event("keydown", {key: "ArrowRight", shiftKey: true}));
  assert.equal(app.context.state.drawing.current.cursor.x, 176); assert.equal(app.context.state.drawing.current.pixels[168 * 336 + 176], 255);
  const clear = app.button("Clear drawing"); clear.focus(); clear.dispatchEvent(new Event("click"));
  assert.equal(app.context.state.drawing.current.hasInk, false); assert.equal(app.document.activeElement, clear); assert.equal(clear.disabled, false);
  assert.equal(app.calls.length, 1, "drawing and clearing cannot open UART"); app.view.dispose();
});

test("the drawing viewer exposes independent Open and Close controls without board or firmware prerequisites", async () => {
  const app = viewHarness(); await app.view.navigate();
  assert.ok(app.button("Open UART")); assert.ok(app.button("Close UART")); assert.ok(app.button("Send drawing"));
  const content = app.root.textContent;
  assert.doesNotMatch(content, /Choose a board|SDK|Project Creator|firmware|selected target|model identity/i);
  assert.equal(descendants(app.root).some((element) => element.tagName === "a"), false); app.view.dispose();
});

test("pointer coordinates scale to the canonical raster and touch capture ends cleanly", async () => {
  const app = viewHarness(); await app.view.navigate();
  app.canvas.dispatchEvent(event("pointerdown", {button: 0, pointerId: 1, clientX: 42, clientY: 30}));
  assert.equal(app.context.state.drawing.current.pixels[60 * 336 + 84], 255); assert.equal(app.canvas.capturedPointer, 1);
  assert.deepEqual(app.canvas.focusOptions, {preventScroll: true});
  app.canvas.dispatchEvent(event("pointermove", {pointerId: 1, clientX: 60, clientY: 60}));
  app.canvas.dispatchEvent(event("pointercancel", {pointerId: 1}));
  const revision = app.context.state.drawing.current.revision;
  app.canvas.dispatchEvent(event("pointermove", {pointerId: 1, clientX: 100, clientY: 100}));
  assert.equal(app.context.state.drawing.current.revision, revision);
  app.view.dispose(); const calls = app.calls.length; app.button("Refresh UART ports").dispatchEvent(new Event("click")); assert.equal(app.calls.length, calls);
});
