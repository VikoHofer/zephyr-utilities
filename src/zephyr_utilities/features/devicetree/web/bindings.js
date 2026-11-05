/** Explicit, revision-bound binding inspection on the canonical draft command path. */
const PREFIX = "/api/devicetree/editor/drafts/";

export const bindingTypes = new Set(["boolean", "int", "string", "string-array", "array", "uint8-array",
  "phandle", "path", "phandles", "phandle-array"]);

export function unsignedCells(text, maximum = 0xffffffff) {
  const values = String(text).trim().split(/[\s,]+/).filter(Boolean);
  if (!values.length || values.length > 64 || values.some((value) => !/^(?:0[xX][\da-fA-F]+|\d+)$/.test(value)
      || !Number.isSafeInteger(Number(value)) || Number(value) > maximum)) {
    throw new Error(`Enter one to 64 integers from 0 to ${maximum}, separated by spaces. Decimal and 0x hexadecimal are accepted.`);
  }
  return values.map(Number);
}

export function localBindingValue(property) {
  if (!property?.local) return undefined;
  if (Object.hasOwn(property.local, "parsed")) return property.local.parsed;
  const value = property.local.value;
  if (!value) return undefined;
  if (property.type === "boolean") return true;
  if (property.type === "int") return Array.isArray(value.value) ? value.value[0] : value.value;
  return value.value;
}

export function bindingEnumValue(property, value) {
  const list = ["array", "string-array"].includes(property.type);
  const values = list ? value : [value];
  if (!Array.isArray(values) || !values.length || values.length > 64
      || values.some((item) => !property.enum.some((allowed) => allowed === item))) {
    throw new Error(list ? "Choose one to 64 entries from the binding enumeration."
      : "Choose a value from the binding enumeration.");
  }
  return value;
}

export function bindingValue(type, value) {
  if (type === "boolean") {
    if (typeof value !== "boolean") throw new Error("Choose whether this boolean property is present.");
    return value;
  }
  if (type === "int") {
    const cells = unsignedCells(value);
    if (cells.length !== 1) throw new Error("Enter one unsigned 32-bit integer.");
    return cells[0];
  }
  if (type === "array" || type === "uint8-array") return unsignedCells(value, type === "uint8-array" ? 255 : 0xffffffff);
  if (type === "string") return String(value);
  if (type === "string-array") {
    const values = String(value).split(/\r?\n/);
    if (!values.length || values.length > 64 || values.some((item) => !item.length)) throw new Error("Enter one nonempty string per line, up to 64 strings.");
    return values;
  }
  if (type === "phandle" || type === "path") {
    if (!value) throw new Error("Choose an existing local target.");
    return value;
  }
  if (type === "phandles") {
    if (!Array.isArray(value) || !value.length || value.length > 64 || value.some((item) => !item)) throw new Error("Choose one or more existing local targets.");
    return value;
  }
  if (type === "phandle-array") {
    if (!Array.isArray(value) || !value.length || value.length > 64) throw new Error("Add at least one provider reference, up to 64 entries.");
    return value.map((entry) => {
      if (!entry.reference || !Array.isArray(entry.cells)) throw new Error("Choose a provider and complete its specifier cells.");
      return {reference: entry.reference, cells: entry.cells.map((cell) => {
        const parsed = unsignedCells(cell);
        if (parsed.length !== 1) throw new Error("Each provider cell needs one integer.");
        return parsed[0];
      })};
    });
  }
  throw new Error("This property type is not supported by the binding form. Edit it in source.");
}

export function createBindingSession({api, state, workspace, drafts, onChange}) {
  const view = state.bindingView = {busy: false, data: null, error: "", message: "", captured: null};
  let disposed = false;
  let generation = 0;
  let loading;
  let applying;
  let reconciling;
  const publish = () => { if (!disposed) onChange(); };
  const entry = () => state.draftView?.entry;
  function capture() {
    const current = entry();
    if (!state.draftView?.ready || !current?.server || state.data?.workspace !== workspace()) return null;
    return {workspace: workspace(), context_id: state.data.id, expected_revision: state.data.revision,
      draft_id: current.server.id, expected_draft_revision: current.server.revision,
      expected_draft_sha256: current.server.sha256, local_generation: current.generation};
  }
  function matches(captured) {
    const current = capture();
    return Boolean(captured && current && !drafts.pending()
      && Object.keys(current).every((key) => captured[key] === current[key]));
  }
  function ready() {
    const current = entry();
    return Boolean(capture() && !drafts.pending() && !current.flight && !current.historyOperation
      && !current.externalOperation && !current.uncertain && !current.conflict && !current.server.syntax?.blocked);
  }
  function needsReconcile() { return Boolean(capture() && entry()?.uncertain && !entry()?.conflict); }
  function canReconcile() {
    const current = entry();
    return !disposed && !view.busy && needsReconcile() && !current.flight && !current.historyOperation && !current.externalOperation;
  }
  function sync() {
    if (disposed) return;
    if (view.captured && (!matches(view.captured) || entry()?.uncertain || entry()?.conflict)) {
      view.data = null; view.captured = null;
      if (!applying) view.message = "The draft or context changed. Load binding properties for the current source.";
      view.error = "";
    }
  }
  function clear() {
    generation++; loading?.abort(); loading = null; applying = null; reconciling = null;
    view.busy = false; view.data = null; view.captured = null;
    view.error = ""; view.message = "";
  }
  function cancel() {
    if (!loading || applying) return;
    generation++; loading.abort(); loading = null; view.busy = false;
    view.error = ""; view.message = "Binding inspection cancelled. The working draft is unchanged."; publish();
  }
  async function load({afterCommand = false} = {}) {
    if (disposed || loading || !afterCommand && view.busy) return false;
    view.error = "";
    view.message = afterCommand ? "The overlay draft was updated. Refreshing its binding properties; Save remains a separate action." : "";
    if (!ready()) { view.error = "Finish or reconcile the current draft request before loading bindings. Incomplete source stays editable in the source panel."; publish(); return false; }
    const captured = capture(); const token = ++generation; const request = new AbortController();
    loading = request; view.busy = true; publish();
    const {local_generation: _local, ...guard} = captured;
    try {
      const data = await api(PREFIX + "bindings", {method: "POST", body: guard, signal: request.signal});
      if (disposed || token !== generation || !matches(captured)) return false;
      if (!data || typeof data.schema_id !== "string" || data.draft_id !== captured.draft_id
          || data.revision !== captured.expected_draft_revision || data.sha256 !== captured.expected_draft_sha256
          || !Array.isArray(data.nodes) || !Array.isArray(data.targets)) {
        throw new Error("The binding response does not match the current draft. Load binding properties again.");
      }
      view.data = data; view.captured = captured;
      view.message = afterCommand ? "Draft updated. Binding properties now describe this source revision. Save remains a separate action."
        : "Binding properties loaded for this draft. Defaults and required values have not been added.";
      return true;
    } catch (error) {
      if (!disposed && token === generation && matches(captured)) view.error = error.message || String(error);
      return false;
    } finally {
      if (loading === request) loading = null;
      if (!disposed && token === generation) { view.busy = false; publish(); }
    }
  }
  async function command(command) {
    sync();
    if (disposed || view.busy || !view.data || !matches(view.captured) || !ready()) return false;
    const captured = view.captured; const token = generation; const schema = view.data.schema_id; const request = {};
    applying = request; view.busy = true; view.error = ""; view.message = "Updating the overlay draft…"; publish();
    try {
      const accepted = await drafts.command({...command, schema_id: schema});
      if (disposed || token !== generation || workspace() !== captured.workspace
          || state.data?.id !== captured.context_id || state.draftView?.entry?.server?.id !== captured.draft_id) return false;
      if (!accepted) {
        view.error = entry()?.error || "The draft command was not accepted. Reconcile the draft or reload binding properties.";
        view.message = ""; return false;
      }
      view.data = null; view.captured = null;
      view.busy = false;
      return await load({afterCommand: true});
    } catch (error) {
      if (!disposed && token === generation && workspace() === captured.workspace) {
        view.error = error.message || String(error); view.message = "";
      }
      return false;
    } finally {
      if (applying === request) {
        applying = null;
        if (!disposed) { view.busy = Boolean(loading); sync(); publish(); }
      }
    }
  }
  async function reconcile() {
    if (!canReconcile()) return false;
    const captured = capture(); const token = ++generation; const request = {};
    reconciling = request; view.busy = true; view.data = null; view.captured = null;
    view.error = ""; view.message = "Reconciling the canonical draft…"; publish();
    const current = () => !disposed && token === generation && workspace() === captured.workspace
      && state.data?.id === captured.context_id && state.data?.revision === captured.expected_revision
      && entry()?.server?.id === captured.draft_id;
    try {
      const accepted = await drafts.reconcile();
      if (!current()) return false;
      if (!accepted) {
        view.error = entry()?.error || "The draft could not be reconciled. Retained source remains available.";
        view.message = ""; return false;
      }
      view.message = drafts.pending() ? "Draft reconciled. Sync retained source before loading binding properties."
        : "Draft reconciled. Load binding properties again before applying a change.";
      return true;
    } catch (error) {
      if (current()) { view.error = error.message || String(error); view.message = ""; }
      return false;
    } finally {
      if (reconciling === request) {
        reconciling = null;
        if (current()) { view.busy = false; sync(); publish(); }
      }
    }
  }
  return {load, command, sync, ready, clear, cancel, reconcile, needsReconcile, canReconcile,
    scanning: () => Boolean(loading && !applying), reconciling: () => Boolean(reconciling),
    fail(message) { view.error = message; view.message = ""; publish(); },
    dispose() { disposed = true; clear(); },
  };
}
