/** One acknowledged draft plus retained local typing, bound to exact source context. */
const PREFIX = "/api/devicetree/editor/drafts/";
const encoder = new TextEncoder();

export const editorText = (source) => source.replace(/\r\n?/g, "\n");

export function utf16ToUtf8(text, position) {
  let index = Math.max(0, Math.min(text.length, position));
  if (index > 0 && index < text.length && /[\uD800-\uDBFF]/.test(text[index - 1]) && /[\uDC00-\uDFFF]/.test(text[index])) index--;
  return encoder.encode(text.slice(0, index)).length;
}

export function utf8ToUtf16(text, position) {
  let bytes = 0;
  let units = 0;
  for (const character of text) {
    const size = encoder.encode(character).length;
    if (bytes + size > position) break;
    bytes += size; units += character.length;
  }
  return units;
}

function rawPosition(source, normalizedPosition) {
  let raw = 0;
  let normalized = 0;
  while (raw < source.length && normalized < normalizedPosition) {
    raw += source[raw] === "\r" && source[raw + 1] === "\n" ? 2 : 1;
    normalized++;
  }
  return raw;
}

export function sourceFromEditor(previous, value) {
  const normalized = editorText(previous);
  const next = editorText(value);
  if (normalized === next) return previous;
  let prefix = 0;
  while (prefix < normalized.length && prefix < next.length && normalized[prefix] === next[prefix]) prefix++;
  let suffix = 0;
  while (suffix < normalized.length - prefix && suffix < next.length - prefix
      && normalized[normalized.length - suffix - 1] === next[next.length - suffix - 1]) suffix++;
  const start = rawPosition(previous, prefix);
  const end = rawPosition(previous, normalized.length - suffix);
  const before = previous.slice(0, start);
  const endings = before.match(/\r\n|\r|\n/g);
  const newline = endings?.at(-1) || previous.match(/\r\n|\r|\n/)?.[0] || "\n";
  return before + next.slice(prefix, next.length - suffix).replace(/\n/g, newline) + previous.slice(end);
}

export function sourceSelection(text, start, end = start) {
  const rawStart = utf8ToUtf16(text, start);
  const rawEnd = utf8ToUtf16(text, end);
  return [editorText(text.slice(0, rawStart)).length, editorText(text.slice(0, rawEnd)).length];
}

export function sourceOffset(text, editorPosition) {
  return utf16ToUtf8(text, rawPosition(text, editorPosition));
}

export function draftKey(context, document) {
  return context?.fingerprint && document?.id ? `${context.fingerprint}:${document.id}` : null;
}

export function propertySelection(properties, index) {
  const selected = properties[index];
  if (!selected) return null;
  const matches = properties.map((property, position) => ({property, position})).filter(({property}) => property.name === selected.name);
  return {name: selected.name, ordinal: matches.findIndex(({position}) => position === index), count: matches.length};
}

export function restorePropertySelection(properties, hint) {
  if (!hint) return -1;
  const matches = properties.map((property, index) => ({property, index})).filter(({property}) => property.name === hint.name);
  return matches.length === hint.count ? matches[hint.ordinal]?.index ?? -1 : -1;
}

export const nodeSelection = propertySelection;
export const restoreNodeSelection = restorePropertySelection;

export function createDraftSession({api, state, workspace, onChange, delay = 350,
  setTimer = setTimeout, clearTimer = clearTimeout}) {
  state.draftCache ||= {};
  state.draftView = null;
  let disposed = false;
  let generation = 0;
  let timer;
  let binding;
  let bindingTag = "";
  let opening;
  const publish = () => { if (!disposed) onChange(); };
  const active = (captured = binding) => !disposed && captured === binding && captured?.workspace === workspace();
  const entry = () => state.draftView?.entry;
  const cancelTimer = () => { if (timer !== undefined) clearTimer(timer); timer = undefined; };
  const contextBody = (value) => ({workspace: value.workspace, context_id: value.context_id, expected_revision: value.expected_revision});
  const isPending = (value) => Boolean(value && (!value.server || value.text !== value.server.text));

  function verify(response, captured, value, expectedId = null) {
    if (response?.schema_version !== 1 || typeof response.id !== "string" || !response.id
        || response.context_fingerprint !== captured.fingerprint || response.document_id !== captured.document_id
        || response.path !== captured.path || response.base_sha256 !== captured.base_sha256
        || !Number.isInteger(response.revision) || response.revision < 0 || typeof response.sha256 !== "string"
        || typeof response.text !== "string" || !Array.isArray(response.nodes)
        || !response.syntax || typeof response.syntax.blocked !== "boolean" || (expectedId && response.id !== expectedId)) {
      throw new Error("The draft response does not match the selected context. Reopen it to reconcile.");
    }
    if (value.server?.id === response.id && response.revision < value.server.revision) {
      throw new Error("A stale draft response was rejected. Reconcile the current draft.");
    }
  }

  function receive(value, response, preserveLocal) {
    value.server = response;
    if (!preserveLocal) value.text = response.text;
    value.uncertain = false;
    value.error = "";
  }

  function hasConflict(value, response) {
    if (!value.server || !isPending(value)) return false;
    if (response.text === value.text) return false;
    if (response.revision === value.server.revision && response.sha256 === value.server.sha256) return false;
    const attempted = value.attempt;
    return !(attempted?.kind === "source" && response.revision === attempted.revision + 1 && response.text === attempted.text);
  }

  function conflict(value) {
    value.conflict = true;
    value.error = "Another request changed this draft. Your local text is retained. Choose which text to continue with.";
  }

  function schedule() {
    cancelTimer();
    const current = entry();
    if (!active() || !current || current.error || current.uncertain || current.conflict || !isPending(current) || !state.draftView.ready) return;
    timer = setTimer(() => { timer = undefined; void flush(); }, delay);
  }

  function detach() {
    cancelTimer(); generation++; binding = null; bindingTag = ""; opening = null;
    state.draftView = null;
  }

  function bind(context, document) {
    const key = draftKey(context, document);
    if (!key || context.workspace !== workspace() || document.availability !== "available") {
      if (bindingTag) { detach(); publish(); }
      return Promise.resolve(false);
    }
    const tag = `${key}:${context.id}:${context.revision}`;
    if (tag === bindingTag) return opening || Promise.resolve(Boolean(state.draftView?.ready));
    detach(); bindingTag = tag;
    const captured = binding = {key, workspace: context.workspace, context_id: context.id,
      expected_revision: context.revision, fingerprint: context.fingerprint, document_id: document.id,
      path: document.path, base_sha256: document.sha256};
    const token = ++generation;
    let value = state.draftCache[key];
    if (!value) {
      const entries = Object.entries(state.draftCache);
      if (entries.length >= 16) {
        const clean = entries.find(([, candidate]) => !candidate.flight && !candidate.uncertain
          && ((!candidate.server && !candidate.localEdited && !candidate.generation && !candidate.attempt)
            || (!candidate.server?.dirty && !candidate.server?.can_undo && !candidate.server?.can_redo && !isPending(candidate))));
        if (clean) delete state.draftCache[clean[0]];
        else {
          state.draftView = {entry: null, ready: false, loading: false, error: "The browser already retains 16 drafts. Existing changed drafts remain available below."};
          publish(); return Promise.resolve(false);
        }
      }
      value = state.draftCache[key] = {text: document.text, server: null, generation: 0, error: "", uncertain: false,
        path: document.path, label: `${context.configuration?.name || "Directory"} · ${context.target?.requested || "Unknown target"}`};
    }
    state.draftView = {entry: value, ready: false, loading: true, error: ""}; publish();
    opening = (async () => {
      try {
        if (value.flight) await value.flight;
        if (!active(captured) || token !== generation) return false;
        const hadPending = isPending(value);
        const response = await api(PREFIX + "open", {method: "POST", body: {...contextBody(captured), document_id: captured.document_id}});
        if (!active(captured) || token !== generation) return false;
        verify(response, captured, value);
        const changedElsewhere = value.conflict && isPending(value) || hasConflict(value, response);
        receive(value, response, hadPending && value.server !== null || value.localEdited === true);
        value.localEdited = isPending(value);
        if (changedElsewhere) conflict(value);
        else { value.conflict = false; value.attempt = null; }
        state.draftView.ready = true;
        return true;
      } catch (error) {
        if (active(captured) && token === generation) {
          value.error = error.message || String(error);
          state.draftView.error = value.error;
        }
        return false;
      } finally {
        if (active(captured) && token === generation) {
          state.draftView.loading = false; publish(); schedule();
        }
      }
    })();
    return opening;
  }

  async function reconcile() {
    cancelTimer();
    const captured = binding;
    const value = entry();
    if (!active(captured) || !value?.server || value.flight) return false;
    const preserveLocal = value.localEdited || isPending(value);
    const localGeneration = value.generation;
    value.error = "";
    const operation = (async () => {
      try {
        const response = await api(PREFIX + "get", {method: "POST", body: {...contextBody(captured), draft_id: value.server.id}});
        verify(response, captured, value, value.server.id);
        const changedElsewhere = value.conflict && isPending(value) || hasConflict(value, response);
        receive(value, response, preserveLocal || value.generation !== localGeneration);
        value.localEdited = isPending(value);
        if (changedElsewhere) { conflict(value); return false; }
        value.attempt = null;
        if (active(captured)) state.draftView.error = "";
        return true;
      } catch (error) {
        value.uncertain = true; value.error = error.message || String(error);
        return false;
      } finally { if (active(captured)) publish(); }
    })();
    value.flight = operation;
    publish();
    try { return await operation; }
    finally { if (value.flight === operation) value.flight = null; if (active(captured)) publish(); }
  }

  async function mutate(kind, extra, value, captured) {
    const base = value.server;
    const localGeneration = value.generation;
    value.attempt = {kind, revision: base.revision, sha256: base.sha256, text: kind === "source" ? extra.text : null};
    value.error = "";
    const operation = (async () => {
      try {
        const response = await api(PREFIX + kind, {method: "POST", body: {...contextBody(captured), draft_id: base.id,
          expected_draft_revision: base.revision, expected_draft_sha256: base.sha256, ...extra}});
        verify(response, captured, value, base.id);
        if (response.revision > base.revision + 1) throw new Error("The draft changed in another request. Reconcile it before continuing.");
        receive(value, response, value.generation !== localGeneration);
        value.localEdited = isPending(value);
        if (kind !== "source" && value.generation !== localGeneration && isPending(value)) conflict(value);
        value.attempt = null;
        return true;
      } catch (error) {
        value.uncertain = true;
        value.error = `${error.message || String(error)} Your local text is retained. Reconcile before another action.`;
        return false;
      }
    })();
    value.flight = operation; value.flightKind = kind; publish();
    try { return await operation; }
    finally { if (value.flight === operation) { value.flight = null; value.flightKind = null; } if (active(captured)) publish(); }
  }

  async function flush() {
    cancelTimer();
    const captured = binding;
    const value = entry();
    if (!active(captured) || !state.draftView?.ready || !value?.server) return false;
    if (value.flushing) return value.flushing;
    const operation = drain(value, captured);
    value.flushing = operation;
    try { return await operation; }
    finally { if (value.flushing === operation) value.flushing = null; }
  }

  async function drain(value, captured) {
    if (value.flight) await value.flight;
    if (!active(captured)) return false;
    if (value.uncertain && !await reconcile()) return false;
    if (!active(captured) || value.conflict) return false;
    while (isPending(value)) {
      if (encoder.encode(value.text).length > 256 * 1024 || value.text.includes("\0")) {
        value.error = "Draft source must be at most 256 KiB of UTF-8 without NUL characters. Your typing is retained for correction.";
        publish(); return false;
      }
      if (!await mutate("source", {text: value.text}, value, captured)) return false;
      if (!active(captured)) return false;
    }
    return true;
  }

  function type(value) {
    const current = entry();
    if (!active() || !state.draftView?.ready || !current) return false;
    const changed = sourceFromEditor(current.text, value);
    if (changed === current.text) return true;
    current.text = changed; current.localEdited = true; current.generation++;
    if (!current.uncertain) current.error = "";
    publish(); schedule();
    return true;
  }

  async function command(value) {
    const current = entry();
    const captured = binding;
    if (!active(captured) || !current?.server || !state.draftView?.ready || current.flight
        || isPending(current) || current.uncertain || current.server.syntax.blocked) return false;
    return mutate("command", {command: value}, current, captured);
  }

  async function history(kind) {
    const captured = binding;
    const current = entry();
    if (!active(captured) || !current || current.historyOperation) return false;
    const localGeneration = current.generation;
    current.historyOperation = true;
    publish();
    try {
      if (!await flush() || !active(captured)) return false;
      if (current.generation !== localGeneration) {
        current.error = "New typing arrived while the draft was syncing. It has been retained; use Undo or Redo again when ready.";
        return false;
      }
      if (!current.server[kind === "undo" ? "can_undo" : "can_redo"]) return false;
      return await mutate(kind, {}, current, captured);
    } finally { current.historyOperation = false; if (active(captured)) publish(); }
  }

  return {bind, detach, type, command, flush, reconcile,
    async resolveConflict(choice) {
      const current = entry();
      if (!active() || !current?.conflict || current.flight || !["local", "server"].includes(choice)) return false;
      current.conflict = false; current.uncertain = false; current.attempt = null; current.error = "";
      if (choice === "server") { current.text = current.server.text; current.localEdited = false; current.generation++; publish(); return true; }
      if (choice === "local") { publish(); return flush(); }
      return false;
    },
    undo: () => history("undo"), redo: () => history("redo"),
    pending: () => isPending(entry()),
    dispose() { disposed = true; cancelTimer(); generation++; binding = null; state.draftView = null; },
  };
}
