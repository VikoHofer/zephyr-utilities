import {DRAWING_SIZE, createDrawingSession, activeRun, sendProblem, submittedCapture, drawingChangedSinceRun} from "./drawing-state.js";

/** The drawing view owns requests and timers; server inference jobs outlive it. */
export function mountDrawing(root, context) {
  const document = root.ownerDocument;
  const lifetime = new AbortController();
  const state = context.state.drawing ||= {};
  let disposed = false, pointer = null, session, portsKey, paintedPixels, paintedRevision, previewKey, captureKey;
  const node = (tag, className = "", text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const text = (element, value) => { if (element.textContent !== value) element.textContent = value; };
  const button = (label) => { const element = node("button", "", label); element.type = "button"; return element; };
  const notice = (className = "tinyml-muted") => {
    const element = node("p", className); element.setAttribute("role", "status"); element.setAttribute("aria-live", "polite"); return element;
  };
  const error = () => { const element = node("p", "tinyml-error"); element.setAttribute("role", "alert"); element.hidden = true; return element; };
  const field = (label, control) => { const element = node("label", "tinyml-field"); element.append(node("span", "", label), control); return element; };
  function canvas(size, label, zoom) {
    const element = node("canvas", size === DRAWING_SIZE ? "tinyml-drawing-canvas" : "tinyml-drawing-preview");
    element.width = size; element.height = size; element.setAttribute("aria-label", label);
    Object.assign(element.style, {display: "block", background: "black", width: zoom ? `${zoom}px` : "100%", maxWidth: `${zoom || size}px`, height: "auto"});
    if (zoom) element.style.imageRendering = "pixelated";
    return element;
  }
  const heading = node("h1", "", "Draw a digit and send it over UART"); heading.tabIndex = -1;
  const intro = node("p", "tinyml-muted", "Select a port and open UART. Draw a digit, send it, and view the prediction and inference time returned by the device.");
  const portSelect = node("select"); portSelect.setAttribute("aria-label", "UART port");
  const refresh = button("Refresh UART ports");
  const open = button("Open UART"), close = button("Close UART");
  const connectionActions = node("div", "tinyml-drawing-actions"); connectionActions.append(open, close, refresh);
  const portRow = node("div", "tinyml-drawing-port-row"); portRow.append(field("UART port · 115200 baud · 8N1", portSelect), connectionActions);
  const portsStatus = notice(), portsError = error(), connectionStatus = notice(), connectionError = error();
  const raw = canvas(DRAWING_SIZE, "Digit drawing area. Use pointer, touch, or arrow keys with Shift to draw.");
  raw.tabIndex = 0; Object.assign(raw.style, {touchAction: "none", cursor: "crosshair"});
  const instructions = node("p", "tinyml-muted", "Use white strokes on black. Keyboard: arrows move the cursor; hold Shift while moving to draw; Space or Enter stamps a point. The brush is 25 pixels wide.");
  instructions.id = "tinyml-drawing-instructions"; raw.setAttribute("aria-describedby", instructions.id);
  const cursorStatus = notice();
  const clear = button("Clear drawing"), updatePreview = button("Update image preview");
  const drawActions = node("div", "tinyml-drawing-actions"); drawActions.append(clear, updatePreview);
  const preparedCanvas = canvas(28, "Prepared 28 by 28 grayscale image for upload", 140);
  const previewStatus = notice(), previewError = error();
  const preview = node("section", "tinyml-drawing-preview-panel"); preview.append(node("h3", "", "Image to send"), preparedCanvas,
    node("p", "tinyml-muted", "28 × 28 pixels, scaled and centered before upload."), previewStatus, previewError);
  const drawing = node("section"); drawing.append(node("h2", "", "Your drawing"), raw, instructions, cursorStatus, drawActions);
  const layout = node("div", "tinyml-drawing-layout"); layout.append(drawing, preview);
  const send = button("Send drawing"); send.className = "tinyml-primary";
  const cancel = button("Cancel inference");
  const runActions = node("div", "tinyml-drawing-actions"); runActions.append(send, cancel);
  const sendNote = node("p", "tinyml-muted"), runStatus = notice(), runError = error();
  const result = node("section", "tinyml-drawing-result");
  const prediction = node("p", "tinyml-drawing-prediction", "No result yet");
  const resultStatus = node("p", "tinyml-muted"), timing = node("p");
  const submittedCanvas = canvas(28, "Captured image used by the displayed inference run", 140);
  const submittedStatus = node("p", "tinyml-muted");
  const scoreList = node("ol", "tinyml-drawing-scores"); scoreList.setAttribute("aria-label", "Class scores");
  const scores = Array.from({length: 10}, (_, digit) => {
    const row = node("li", "tinyml-drawing-score-row"); const bar = node("meter");
    bar.min = 0; bar.max = 1; bar.value = 0; bar.setAttribute("aria-label", `Class ${digit} score`);
    const value = node("span", "", "—"); row.append(node("span", "", String(digit)), bar, value); scoreList.append(row); return {bar, value};
  });
  const evidence = node("details"), evidenceText = node("pre"); evidence.append(node("summary", "", "Run details"), evidenceText);
  const logs = node("details"), logText = node("pre"); logs.append(node("summary", "", "Run log"), logText);
  result.append(node("h2", "", "Received prediction"), prediction, resultStatus, timing,
    submittedCanvas, submittedStatus, node("h3", "", "Class scores"), scoreList, evidence, logs);
  const panel = node("section", "tinyml-panel tinyml-drawing-panel");
  panel.append(heading, intro, portRow, portsStatus, portsError, connectionStatus, connectionError, layout, sendNote, runActions, runStatus, runError, result);
  root.replaceChildren(panel);

  function paint(element, pixels, cursor) {
    const painter = element.getContext("2d");
    if (!painter) return;
    const size = element.width, image = painter.createImageData(size, size);
    for (let index = 0; index < size * size; index++) {
      const value = pixels?.[index] || 0, offset = index * 4;
      image.data[offset] = value; image.data[offset + 1] = value; image.data[offset + 2] = value; image.data[offset + 3] = 255;
    }
    painter.putImageData(image, 0, 0);
    if (cursor && document.activeElement === raw) {
      painter.strokeStyle = document.defaultView?.getComputedStyle(root).getPropertyValue("--app-blue").trim() || "#0070c5"; painter.lineWidth = 1;
      painter.beginPath(); painter.moveTo(cursor.x - 6, cursor.y); painter.lineTo(cursor.x + 6, cursor.y);
      painter.moveTo(cursor.x, cursor.y - 6); painter.lineTo(cursor.x, cursor.y + 6); painter.stroke();
    }
  }
  function render() {
    if (disposed) return;
    const current = state.current, job = current.job, capture = submittedCapture(state), response = job?.status === "completed" ? job.result : null;
    if (portsKey !== current.ports) {
      portsKey = current.ports; portSelect.replaceChildren();
      const empty = node("option", "", "Choose a UART port"); empty.value = ""; portSelect.append(empty);
      for (const port of current.ports?.ports || []) { const option = node("option", "", `${port.device} · ${port.description || "Serial device"}`); option.value = port.id; portSelect.append(option); }
      if (current.portId && !current.ports?.ports.some((port) => port.id === current.portId)) {
        const missing = node("option", "", "Previously selected port is unavailable"); missing.value = current.portId; portSelect.append(missing);
      }
    }
    if (portSelect.value !== current.portId) portSelect.value = current.portId;
    text(refresh, state.loadingPorts ? "Reading UART ports…" : "Refresh UART ports"); refresh.setAttribute("aria-disabled", String(state.loadingPorts));
    text(portsStatus, state.loadingPorts ? "Listing UART ports without opening them…" : !current.ports ? "Refresh UART ports to list available devices."
      : !current.ports.available ? current.ports.reason || "Serial support is unavailable in the selected environment."
      : `${current.ports.ports.length} UART port${current.ports.ports.length === 1 ? "" : "s"} listed.`);
    text(portsError, current.portsError); portsError.hidden = !current.portsError;
    const controlBusy = state.opening || state.closing || activeRun(current.controlJob);
    text(open, state.opening || activeRun(current.controlJob) && current.controlJob.metadata.operation === "uart-open" ? "Opening UART…" : "Open UART");
    open.setAttribute("aria-disabled", String(Boolean(current.connection) || controlBusy || !current.ports?.available || !current.ports?.ports.some((port) => port.id === current.portId)));
    text(close, state.closing || activeRun(current.controlJob) && current.controlJob.metadata.operation === "uart-close" ? "Closing UART…" : "Close UART");
    close.setAttribute("aria-disabled", String(!current.connection || controlBusy));
    text(connectionStatus, controlBusy ? current.controlJob?.logs.at(-1) || current.connectionNotice
      : current.connection ? `UART ${current.connection.state}: ${current.connection.device}` : "UART closed.");
    const connectionMessage = [current.connectionError, current.connectionWatchError].filter(Boolean).join(" ");
    text(connectionError, connectionMessage); connectionError.hidden = !connectionMessage;
    if (paintedPixels !== current.pixels || paintedRevision !== current.revision) {
      paintedPixels = current.pixels; paintedRevision = current.revision; paint(raw, current.pixels, current.cursor);
    }
    text(cursorStatus, `Drawing cursor: ${current.cursor.x}, ${current.cursor.y} of 335.`);
    if (previewKey !== current.prepared) { previewKey = current.prepared; paint(preparedCanvas, current.prepared?.pixels); }
    text(previewStatus, state.preparing ? "Preparing the current drawing…" : current.prepared ? "The current image is ready for upload." : current.hasInk ? "The preview will update after your stroke." : "Draw a digit to prepare its image.");
    text(previewError, current.prepareError); previewError.hidden = !current.prepareError;
    updatePreview.setAttribute("aria-disabled", String(state.preparing || !current.hasInk));
    const problem = sendProblem(state);
    text(sendNote, problem || "Send the prepared image over the open UART connection.");
    text(send, state.submitting ? "Preparing upload…" : activeRun(job) ? "Waiting for result…" : "Send drawing"); send.setAttribute("aria-disabled", String(Boolean(problem)));
    text(cancel, state.cancelling ? "Requesting cancellation…" : "Cancel inference"); cancel.setAttribute("aria-disabled", String(state.cancelling || !activeRun(job)));
    const progress = activeRun(job) ? (job.logs.at(-1) || (job.status === "queued" ? "Inference is queued." : "Sending the captured image and waiting for a result…")) : current.notice;
    text(runStatus, progress); text(runError, current.runError); runError.hidden = !current.runError;
    text(prediction, response ? String(response.prediction) : activeRun(job) ? "Waiting for result" : "No result yet");
    text(resultStatus, !job ? "Send a drawing to receive a prediction from the connected device."
      : drawingChangedSinceRun(state) ? "The drawing has changed. This run and any prediction belong to the captured image shown below."
      : !capture ? "This restored run is identified by its recorded image hash; its drawing is not retained in this browser."
      : "This run belongs to the captured image shown below.");
    text(timing, response ? `${response.inference_us} µs inference time reported by the device.` : "");
    if (captureKey !== capture) { captureKey = capture; paint(submittedCanvas, capture?.pixels); }
    submittedCanvas.hidden = !capture; text(submittedStatus, capture ? "Captured 28 × 28 image used by this run." : job ? "Captured image unavailable; see the recorded image SHA-256 below." : "");
    for (let digit = 0; digit < 10; digit++) {
      scores[digit].bar.value = response?.scores[digit] || 0;
      text(scores[digit].value, response ? `${(response.scores[digit] * 100).toFixed(2)}%` : "—");
    }
    text(evidenceText, job ? JSON.stringify({run_id: job.id, status: job.status, connection_id: job.metadata.connection_id,
      image_sha256: job.metadata.image_sha256, crc32: job.metadata.crc32, timing: response?.timing || null, port: response?.port || null}, null, 2) : "No run has been submitted.");
    text(logText, job?.logs.join("\n") || "No inference log yet.");
  }
  session = createDrawingSession({api: context.api, state, workspace: () => context.workspace.path, onChange: render});
  function listen(element, event, callback, options = {}) { element.addEventListener(event, callback, {...options, signal: lifetime.signal}); }
  function point(event) {
    const bounds = raw.getBoundingClientRect();
    return {x: Math.max(0, Math.min(335, (event.clientX - bounds.left) * DRAWING_SIZE / bounds.width)),
      y: Math.max(0, Math.min(335, (event.clientY - bounds.top) * DRAWING_SIZE / bounds.height))};
  }
  listen(raw, "pointerdown", (event) => {
    if (event.button !== 0 || pointer) return;
    event.preventDefault(); const at = point(event); pointer = {id: event.pointerId, point: at};
    raw.focus({preventScroll: true}); raw.setPointerCapture?.(event.pointerId); session.stroke(at);
  });
  listen(raw, "pointermove", (event) => {
    if (!pointer || pointer.id !== event.pointerId) return;
    event.preventDefault(); const at = point(event); session.stroke(pointer.point, at); pointer.point = at;
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) listen(raw, event, (ending) => {
    if (pointer && (ending.pointerId === undefined || ending.pointerId === pointer.id)) { pointer = null; session.endStroke(); }
  });
  listen(raw, "keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const steps = {ArrowLeft: [-8, 0], ArrowRight: [8, 0], ArrowUp: [0, -8], ArrowDown: [0, 8]};
    const from = {...state.current.cursor};
    if (Object.hasOwn(steps, event.key)) {
      event.preventDefault(); const [dx, dy] = steps[event.key]; session.moveCursor(from.x + dx, from.y + dy);
      if (event.shiftKey) { session.stroke(from, state.current.cursor); session.endStroke(); }
      paint(raw, state.current.pixels, state.current.cursor);
    } else if (event.key === " " || event.key === "Enter") {
      event.preventDefault(); session.stroke(from); session.endStroke(); paint(raw, state.current.pixels, state.current.cursor);
    }
  });
  for (const event of ["focus", "blur"]) listen(raw, event, () => paint(raw, state.current.pixels, state.current.cursor));
  listen(clear, "click", () => { pointer = null; session.clear(); });
  listen(updatePreview, "click", () => { if (!state.preparing) void session.prepare(); });
  listen(portSelect, "change", () => session.selectPort(portSelect.value));
  listen(refresh, "click", () => { if (!state.loadingPorts) void session.refreshPorts(); });
  listen(open, "click", () => { if (!state.opening && !state.closing) void session.open(); });
  listen(close, "click", () => { if (!state.opening && !state.closing) void session.close(); });
  listen(send, "click", () => { if (!state.submitting) void session.send(); });
  listen(cancel, "click", () => { if (!state.cancelling && activeRun(state.current.job)) void session.cancel(); });
  render();
  return {navigate() { pointer = null; paintedPixels = null; previewKey = undefined; captureKey = undefined; return session.navigate(); },
    dispose() { if (disposed) return; disposed = true; pointer = null; lifetime.abort(); session.dispose(); },
  };
}
