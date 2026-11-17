/** Drawings are local inputs; every UART run retains its own captured image identity. */
export const DRAWING_SIZE = 336;
export const BRUSH_WIDTH = 25;
const RUNNING = new Set(["queued", "running"]);
const POLL_RETRY_DELAYS = [600, 1200, 2400];
const hash = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const checksum = (value) => typeof value === "string" && /^[a-f0-9]{8}$/.test(value);
const jobId = (value) => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
const string = (value, limit = 4096) => typeof value === "string" && value.length <= limit;
export const activeRun = (job) => Boolean(job && RUNNING.has(job.status));

export function encodeGray8(pixels) {
  let bytes = "";
  for (let start = 0; start < pixels.length; start += 16384) bytes += String.fromCharCode(...pixels.slice(start, start + 16384));
  return btoa(bytes);
}
export function imageCrc32(pixels) {
  let crc = 0xffffffff;
  for (const value of pixels) {
    crc ^= value;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return ((crc ^ 0xffffffff) >>> 0).toString(16).padStart(8, "0");
}
export async function imageSha256(pixels) {
  const digest = await crypto.subtle.digest("SHA-256", pixels);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

export function paintStroke(pixels, from, to, width = BRUSH_WIDTH) {
  const point = (value) => Math.max(0, Math.min(DRAWING_SIZE - 1, Number.isFinite(value) ? value : 0));
  const x0 = point(from.x), y0 = point(from.y), x1 = point(to.x), y1 = point(to.y);
  const dx = x1 - x0, dy = y1 - y0, length = dx * dx + dy * dy, radius = width / 2;
  let changed = false;
  for (let y = Math.max(0, Math.floor(Math.min(y0, y1) - radius)); y <= Math.min(DRAWING_SIZE - 1, Math.ceil(Math.max(y0, y1) + radius)); y++) {
    for (let x = Math.max(0, Math.floor(Math.min(x0, x1) - radius)); x <= Math.min(DRAWING_SIZE - 1, Math.ceil(Math.max(x0, x1) + radius)); x++) {
      const t = length ? Math.max(0, Math.min(1, ((x - x0) * dx + (y - y0) * dy) / length)) : 0;
      if ((x - x0 - t * dx) ** 2 + (y - y0 - t * dy) ** 2 <= radius * radius) {
        const index = y * DRAWING_SIZE + x;
        if (pixels[index] !== 255) { pixels[index] = 255; changed = true; }
      }
    }
  }
  return changed;
}

export function validatePreparedImage(value, workspace) {
  if (!value || value.schema_version !== 1 || value.workspace !== workspace || !Array.isArray(value.pixels) || value.pixels.length !== 784
    || !value.pixels.every((pixel) => Number.isInteger(pixel) && pixel >= 0 && pixel <= 255) || !value.pixels.some((pixel) => pixel > 0)
    || !hash(value.image_sha256) || !checksum(value.crc32) || imageCrc32(value.pixels) !== value.crc32) {
    throw new Error("The prepared image could not be verified. Draw again or update the preview.");
  }
  return value;
}
export function validateConnection(value, nullable = true) {
  if (value === null && nullable) return value;
  if (!value || !jobId(value.id) || !hash(value.port_id) || !string(value.device) || !string(value.description)
    || !["open", "busy"].includes(value.state) || value.job_id !== undefined && !jobId(value.job_id)) {
    throw new Error("The UART connection could not be verified. Refresh UART ports to reconnect.");
  }
  return value;
}
export function validateInferenceResult(result, metadata) {
  if (!result || result.schema_version !== 1 || result.workspace !== metadata.workspace
    || result.image_sha256 !== metadata.image_sha256 || result.crc32 !== metadata.crc32
    || !result.port || !hash(result.port.id) || !string(result.port.device) || !string(result.port.description)
    || result.port.id !== metadata.port_id
    || !Number.isInteger(result.prediction) || result.prediction < 0 || result.prediction > 9
    || !Number.isInteger(result.inference_us) || result.inference_us < 0 || result.timing !== "invoke-only"
    || !Array.isArray(result.scores) || result.scores.length !== 10
    || !result.scores.every((score) => Number.isFinite(score) && score >= 0 && score <= 1)
    || Math.abs(result.scores.reduce((sum, score) => sum + score, 0) - 1) > 0.00001
    || result.scores.indexOf(Math.max(...result.scores)) !== result.prediction) {
    throw new Error("The received result does not match this image and port, or its scores are invalid.");
  }
  return result;
}
export function validateInferenceJob(job, workspace, id) {
  const metadata = job?.metadata;
  if (!job || !jobId(job.id) || id && job.id !== id || job.feature !== "tinyml"
    || !["queued", "running", "completed", "failed", "cancelled"].includes(job.status)
    || !metadata || metadata.workspace !== workspace || !["mnist-infer", "uart-open", "uart-close"].includes(metadata.operation)
    || !Array.isArray(job.logs) || !job.logs.every((line) => typeof line === "string")) {
    throw new Error("The UART operation could not be verified. Refresh UART ports to reconnect.");
  }
  if (metadata.operation === "mnist-infer") {
    if (!jobId(metadata.connection_id) || !hash(metadata.port_id) || !hash(metadata.image_sha256) || !checksum(metadata.crc32)) {
      throw new Error("The inference image or connection identity could not be verified.");
    }
    if (job.status === "completed") validateInferenceResult(job.result, metadata);
  } else {
    if (metadata.operation === "uart-open" ? !hash(metadata.port_id) : !jobId(metadata.connection_id)) {
      throw new Error("The UART operation's connection identity could not be verified.");
    }
    if (job.status === "completed") {
      if (job.result?.schema_version !== 1 || job.result.workspace !== workspace) throw new Error("The UART operation result could not be verified.");
      const connection = validateConnection(job.result.connection, metadata.operation === "uart-close");
      if (metadata.operation === "uart-open" && (connection.port_id !== metadata.port_id || connection.state !== "open")
        || metadata.operation === "uart-close" && connection !== null) throw new Error("The UART operation returned a different connection.");
    }
  }
  return job;
}
export function validatePorts(value, workspace) {
  if (!value || value.schema_version !== 1 || value.workspace !== workspace || typeof value.available !== "boolean"
    || !string(value.reason) || !Array.isArray(value.ports) || value.ports.length > 256
    || !value.ports.every((port) => port && hash(port.id) && string(port.device) && string(port.description) && string(port.hwid))
    || new Set(value.ports.map((port) => port.id)).size !== value.ports.length || !Array.isArray(value.jobs) || value.jobs.length > 64) {
    throw new Error("The UART port list could not be verified. Refresh ports to try again.");
  }
  validateConnection(value.connection);
  for (const job of value.jobs) validateInferenceJob(job, workspace);
  return value;
}

function emptyDrawing() {
  return {pixels: new Uint8Array(DRAWING_SIZE * DRAWING_SIZE), revision: 0, hasInk: false, cursor: {x: 168, y: 168},
    prepared: null, portId: "", ports: null, connection: null, controlJob: null, job: null, captures: Object.create(null),
    portsError: "", connectionError: "", connectionWatchError: "", runError: "", prepareError: "", connectionNotice: "UART closed.",
    notice: "Draw a single digit in white on the black area."};
}
export const submittedCapture = (state) => state.current?.job ? state.current.captures[state.current.job.id] || null : null;
export function drawingChangedSinceRun(state) {
  const capture = submittedCapture(state);
  return Boolean(capture && capture.revision !== state.current.revision);
}
export function sendProblem(state) {
  const drawing = state.current;
  if (state.submitting) return "Preparing or submitting this image…";
  if (state.opening || state.closing || activeRun(drawing.controlJob)) return "Wait for the UART operation to finish.";
  if (activeRun(drawing.job) || drawing.connection?.state === "busy") return "Wait for inference to finish or cancel it before sending again.";
  if (!drawing.connection) return "Open a UART port before sending.";
  if (!drawing.hasInk) return "Draw a digit first.";
  return "";
}

export function createDrawingSession({api, state, workspace, onChange = () => {}, schedule = setTimeout, unschedule = clearTimeout}) {
  let disposed = false, generation = 0, prepareSequence = 0, portsSequence = 0, pollFailures = 0;
  let connectionWatchKey = "", connectionRefreshes = 0, connectionFailures = 0, connectionWatchStopped = false;
  let prepareRequest, portsRequest, pollRequest, submitRequest, cancelRequest, controlRequest, prepareTimer, pollTimer;
  state.workspaces ||= Object.create(null);
  Object.assign(state, {preparing: false, loadingPorts: false, submitting: false, cancelling: false, opening: false, closing: false});
  function publish() { if (!disposed) onChange(); }
  function cancelTimer(name) { if (name !== undefined) unschedule(name); }
  function stopReads() {
    prepareRequest?.abort(); portsRequest?.abort(); pollRequest?.abort(); cancelTimer(prepareTimer); cancelTimer(pollTimer);
    prepareRequest = null; portsRequest = null; pollRequest = null; prepareTimer = undefined; pollTimer = undefined;
  }
  function followWorkspace() {
    if (disposed) return false;
    const path = workspace();
    if (path === state.workspace && state.current) return false;
    stopReads(); generation++; prepareSequence++; portsSequence++; pollFailures = 0;
    connectionWatchKey = ""; connectionRefreshes = 0; connectionFailures = 0; connectionWatchStopped = false;
    state.workspace = path;
    state.current = Object.hasOwn(state.workspaces, path) ? state.workspaces[path] : (state.workspaces[path] = emptyDrawing());
    state.current.connection = null;
    Object.assign(state, {preparing: false, loadingPorts: false, submitting: false, cancelling: false, opening: false, closing: false});
    return true;
  }
  function current(path, epoch) { return !disposed && workspace() === path && state.workspace === path && generation === epoch; }
  const pollingJob = () => activeRun(state.current.controlJob) ? state.current.controlJob : activeRun(state.current.job) ? state.current.job : null;
  function schedulePoll(delay) {
    cancelTimer(pollTimer); pollTimer = undefined;
    if (disposed || state.cancelling) return;
    if (pollingJob()) { pollTimer = schedule(() => { pollTimer = undefined; return poll(); }, delay ?? 600); return; }
    const connection = state.current.connection;
    const key = connection?.state === "busy" ? `${connection.id}/${connection.job_id || "busy"}` : "";
    if (key !== connectionWatchKey) {
      connectionWatchKey = key; connectionRefreshes = 0; connectionFailures = 0; connectionWatchStopped = false;
      state.current.connectionWatchError = "";
    }
    if (!key || connectionWatchStopped) return;
    if (connectionRefreshes >= 50) {
      state.current.connectionWatchError = "UART is still busy. Refresh UART ports to update the connection status."; return;
    }
    pollTimer = schedule(() => { pollTimer = undefined; connectionRefreshes++; return refreshPorts({automatic: true}); }, delay ?? 1000);
  }
  function acceptJob(job, fromPorts = false) {
    pollFailures = 0;
    const drawing = state.current, operation = job.metadata.operation;
    const prior = operation === "mnist-infer" ? drawing.job : drawing.controlJob;
    if (fromPorts && prior?.id === job.id && !activeRun(prior) && activeRun(job)) return;
    if (operation === "mnist-infer") {
      drawing.job = job;
      drawing.runError = job.status === "failed" ? job.error || "The inference run failed. Review the run log." : "";
      drawing.notice = job.status === "completed" ? "Prediction received for the captured image."
        : job.status === "cancelled" ? "Inference cancelled." : "Sending the captured image and waiting for a result…";
      if (!fromPorts && activeRun(job) && drawing.connection?.id === job.metadata.connection_id) drawing.connection = {...drawing.connection, state: "busy", job_id: job.id};
    } else {
      drawing.controlJob = job;
      drawing.connectionError = job.status === "failed" ? job.error || "The UART operation failed." : "";
      drawing.connectionNotice = activeRun(job) ? operation === "uart-open" ? "Opening UART…" : "Closing UART…"
        : job.status === "completed" ? operation === "uart-open" ? "UART opened." : "UART closed."
        : job.status === "cancelled" ? "UART operation cancelled." : "UART operation failed.";
      if (!fromPorts && job.status === "completed") drawing.connection = job.result.connection;
    }
    schedulePoll(); publish();
    if (!fromPorts && !activeRun(job)) void refreshPorts();
  }
  async function refreshPorts({automatic = false} = {}) {
    if (disposed) return;
    followWorkspace(); portsRequest?.abort();
    if (!automatic) {
      pollFailures = 0; connectionWatchKey = ""; connectionRefreshes = 0; connectionFailures = 0; connectionWatchStopped = false;
      state.current.connectionWatchError = "";
    }
    pollRequest?.abort(); pollRequest = null; cancelTimer(pollTimer); pollTimer = undefined;
    const request = new AbortController(); portsRequest = request;
    const path = state.workspace, epoch = generation, sequence = ++portsSequence;
    state.loadingPorts = true; state.current.portsError = ""; publish();
    try {
      const value = await api(`/api/tinyml/ports?workspace=${encodeURIComponent(path)}`, {signal: request.signal});
      if (!current(path, epoch) || sequence !== portsSequence || request.signal.aborted) return;
      const ports = validatePorts(value, path), drawing = state.current;
      drawing.ports = ports; drawing.connection = ports.connection;
      connectionFailures = 0; drawing.connectionWatchError = "";
      for (const operation of ["mnist-infer", "control"]) {
        const records = ports.jobs.filter((job) => operation === "control" ? job.metadata.operation !== "mnist-infer" : job.metadata.operation === operation);
        const prior = operation === "control" ? drawing.controlJob : drawing.job;
        const job = records.find(activeRun) || records.find((entry) => entry.id === prior?.id) || records[0];
        if (job) acceptJob(job, true);
        else if (activeRun(prior)) {
          if (operation === "control") { drawing.controlJob = null; drawing.connectionError = "The previous UART operation is no longer retained by the server."; }
          else { drawing.job = null; drawing.runError = "The previous run is no longer retained by the server."; }
        }
      }
    } catch (error) {
      if (current(path, epoch) && sequence === portsSequence && !request.signal.aborted) {
        state.current.ports = null;
        const reason = error.message || "UART ports could not be listed.";
        if (!automatic) state.current.portsError = reason;
        else if (connectionFailures < POLL_RETRY_DELAYS.length) {
          const delay = POLL_RETRY_DELAYS[connectionFailures++];
          state.current.connectionWatchError = `${reason} Retrying UART status (${connectionFailures} of ${POLL_RETRY_DELAYS.length})…`;
          schedulePoll(delay);
        } else {
          connectionWatchStopped = true;
          state.current.connectionWatchError = `${reason} Connection status retries stopped. Refresh UART ports to reconnect.`;
        }
      }
    } finally {
      if (current(path, epoch) && portsRequest === request) {
        state.loadingPorts = false; portsRequest = null;
        if (pollTimer === undefined) schedulePoll();
        publish();
      }
    }
  }
  async function connectionAction(operation) {
    if (disposed || state.opening || state.closing || activeRun(state.current.controlJob)) return;
    followWorkspace();
    const drawing = state.current, opening = operation === "uart-open";
    if (opening && drawing.connection) { drawing.connectionError = "Close the current UART connection before opening another port."; publish(); return; }
    if (opening && (!drawing.ports?.available || !drawing.ports.ports.some((port) => port.id === drawing.portId))) {
      drawing.connectionError = drawing.ports?.reason || "Select a listed UART port."; publish(); return;
    }
    if (!opening && !drawing.connection) { drawing.connectionError = "UART is already closed."; publish(); return; }
    const path = state.workspace, epoch = generation, portId = drawing.portId, connectionId = drawing.connection?.id;
    const request = new AbortController(); controlRequest = request;
    state.opening = opening; state.closing = !opening; drawing.connectionError = "";
    drawing.connectionNotice = opening ? "Opening UART…" : "Closing UART…"; publish();
    try {
      const value = await api(opening ? "/api/tinyml/open" : "/api/tinyml/close", {method: "POST", signal: request.signal,
        body: opening ? {workspace: path, port_id: portId} : {workspace: path, connection_id: connectionId}});
      if (!current(path, epoch)) return;
      const job = validateInferenceJob(value, path);
      if (job.metadata.operation !== operation || (opening ? job.metadata.port_id !== portId : job.metadata.connection_id !== connectionId)) {
        throw new Error("The UART operation does not match this connection. Refresh UART ports.");
      }
      acceptJob(job);
    } catch (error) { if (current(path, epoch)) drawing.connectionError = error.message || "The UART operation could not be started."; }
    finally { if (current(path, epoch)) { state.opening = false; state.closing = false; controlRequest = null; publish(); } }
  }
  function changed() {
    prepareSequence++; prepareRequest?.abort(); prepareRequest = null; cancelTimer(prepareTimer); prepareTimer = undefined;
    state.preparing = false; state.current.revision++; state.current.prepared = null; state.current.prepareError = "";
    state.current.notice = activeRun(state.current.job) ? "The drawing changed. The active run still uses its captured image." : "Drawing changed. The next run will use a fresh prepared image.";
    publish();
  }
  function stroke(from, to = from) {
    if (disposed) return;
    followWorkspace();
    if (paintStroke(state.current.pixels, from, to)) { state.current.hasInk = true; changed(); }
  }
  function clear() {
    if (disposed) return;
    followWorkspace(); state.current.pixels.fill(0); state.current.hasInk = false; changed();
    state.current.notice = "Drawing cleared. Draw a digit before sending another image."; publish();
  }
  function endStroke() {
    if (disposed) return;
    cancelTimer(prepareTimer);
    if (state.current.hasInk) prepareTimer = schedule(() => { prepareTimer = undefined; void prepare(); }, 180);
  }
  async function prepare() {
    if (disposed) return null;
    followWorkspace();
    const drawing = state.current;
    if (!drawing.hasInk) { drawing.prepareError = "Draw a digit first."; publish(); return null; }
    if (drawing.prepared?.revision === drawing.revision) return drawing.prepared;
    prepareRequest?.abort(); cancelTimer(prepareTimer); prepareTimer = undefined;
    const request = new AbortController(); prepareRequest = request;
    const path = state.workspace, epoch = generation, revision = drawing.revision, sequence = ++prepareSequence;
    state.preparing = true; drawing.prepareError = ""; publish();
    try {
      const value = await api("/api/tinyml/prepare", {method: "POST", signal: request.signal,
        body: {workspace: path, drawing: {width: DRAWING_SIZE, height: DRAWING_SIZE, gray8: encodeGray8(drawing.pixels)}}});
      if (!current(path, epoch) || sequence !== prepareSequence || request.signal.aborted || drawing.revision !== revision) return null;
      const prepared = validatePreparedImage(value, path);
      if (await imageSha256(new Uint8Array(prepared.pixels)) !== prepared.image_sha256) throw new Error("The prepared image hash does not match its pixels.");
      if (!current(path, epoch) || sequence !== prepareSequence || request.signal.aborted || drawing.revision !== revision) return null;
      drawing.prepared = {...prepared, revision}; return drawing.prepared;
    } catch (error) {
      if (current(path, epoch) && sequence === prepareSequence && !request.signal.aborted) drawing.prepareError = error.message || "The preview could not be prepared.";
      return null;
    } finally {
      if (current(path, epoch) && prepareRequest === request) { state.preparing = false; prepareRequest = null; publish(); }
    }
  }
  async function send() {
    if (disposed || state.submitting) return;
    followWorkspace(); const drawing = state.current;
    const problem = sendProblem(state);
    if (problem) { drawing.runError = problem; publish(); return; }
    const path = state.workspace, epoch = generation, revision = drawing.revision, connectionId = drawing.connection.id, portId = drawing.connection.port_id;
    pollFailures = 0; state.submitting = true; drawing.runError = ""; drawing.notice = "Preparing the image before sending…"; publish();
    try {
      const prepared = await prepare();
      if (!current(path, epoch)) return;
      if (revision !== drawing.revision || drawing.connection?.id !== connectionId || state.closing || activeRun(drawing.controlJob)) {
        drawing.notice = "The drawing or UART connection changed before upload. Review the current preview and send again."; return;
      }
      if (!prepared) { drawing.runError = drawing.prepareError || "The image could not be prepared. Update the preview and try again."; return; }
      const request = new AbortController(); submitRequest = request;
      drawing.notice = "Sending the image over UART…"; publish();
      const value = await api("/api/tinyml/infer", {method: "POST", signal: request.signal,
        body: {workspace: path, connection_id: connectionId, image: {gray8: encodeGray8(prepared.pixels)}}});
      if (!current(path, epoch)) return;
      const job = validateInferenceJob(value, path);
      if (job.metadata.operation !== "mnist-infer" || job.metadata.connection_id !== connectionId || job.metadata.port_id !== portId
        || job.metadata.image_sha256 !== prepared.image_sha256 || job.metadata.crc32 !== prepared.crc32) {
        throw new Error("The submitted run does not match this connection and prepared image. Refresh UART ports to reconnect.");
      }
      drawing.captures[job.id] = {pixels: [...prepared.pixels], image_sha256: prepared.image_sha256, crc32: prepared.crc32, revision, connectionId, portId};
      const retained = Object.keys(drawing.captures);
      for (const old of retained.slice(0, Math.max(0, retained.length - 32))) delete drawing.captures[old];
      acceptJob(job);
    } catch (error) { if (current(path, epoch)) drawing.runError = error.message || "The image could not be sent."; }
    finally { if (current(path, epoch)) { state.submitting = false; submitRequest = null; publish(); } }
  }
  async function poll() {
    if (disposed) return;
    followWorkspace(); const job = pollingJob();
    cancelTimer(pollTimer); pollTimer = undefined;
    if (!job || state.cancelling) return;
    const path = state.workspace, epoch = generation;
    const request = new AbortController(); pollRequest?.abort(); pollRequest = request;
    try {
      const result = await api(`/api/jobs/${job.id}`, {signal: request.signal});
      if (current(path, epoch) && pollingJob()?.id === job.id && !request.signal.aborted) acceptJob(validateInferenceJob(result, path, job.id));
    } catch (error) {
      if (current(path, epoch) && pollingJob()?.id === job.id && !state.cancelling && !request.signal.aborted) {
        const reason = error.message || "The UART operation status could not be read.";
        const field = job.metadata.operation === "mnist-infer" ? "runError" : "connectionError";
        if (pollFailures < POLL_RETRY_DELAYS.length) {
          const delay = POLL_RETRY_DELAYS[pollFailures++];
          state.current[field] = `${reason} Retrying UART status (${pollFailures} of ${POLL_RETRY_DELAYS.length})…`; schedulePoll(delay);
        } else state.current[field] = `${reason} Status retries stopped. Refresh UART ports to reconnect.`;
        publish();
      }
    } finally { if (pollRequest === request) pollRequest = null; }
  }
  async function cancel() {
    if (disposed || state.cancelling || !activeRun(state.current.job)) return;
    followWorkspace(); const job = state.current.job;
    if (!activeRun(job)) return;
    cancelTimer(pollTimer); pollTimer = undefined; pollRequest?.abort(); pollRequest = null;
    const path = state.workspace, epoch = generation;
    const request = new AbortController(); cancelRequest = request; state.cancelling = true; publish();
    try {
      const result = await api(`/api/jobs/${job.id}/cancel`, {method: "POST", body: {}, signal: request.signal});
      if (current(path, epoch) && state.current.job?.id === job.id) {
        acceptJob(validateInferenceJob(result, path, job.id));
        if (activeRun(result)) state.current.notice = "Cancellation requested…";
      }
    } catch (error) { if (current(path, epoch)) state.current.runError = error.message || "Cancellation could not be requested."; }
    finally { if (current(path, epoch)) { state.cancelling = false; cancelRequest = null; schedulePoll(); publish(); } }
  }
  function selectPort(value) {
    if (disposed || typeof value !== "string") return;
    followWorkspace(); state.current.portId = value; state.current.connectionError = ""; publish();
  }
  followWorkspace();
  return {refreshPorts, stroke, endStroke, clear, prepare, send, poll, cancel, selectPort,
    open: () => connectionAction("uart-open"), close: () => connectionAction("uart-close"),
    moveCursor(x, y) { if (disposed) return; followWorkspace(); state.current.cursor = {x: Math.max(0, Math.min(335, x)), y: Math.max(0, Math.min(335, y))}; publish(); },
    async navigate() { if (disposed) return; followWorkspace(); await refreshPorts(); },
    dispose() { if (disposed) return; disposed = true; generation++; stopReads(); submitRequest?.abort(); cancelRequest?.abort(); controlRequest?.abort(); },
  };
}
