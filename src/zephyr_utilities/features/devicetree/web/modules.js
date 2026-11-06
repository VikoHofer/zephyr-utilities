import {createDraftObservationSession, draftPresentationScope} from "./draft-observation.js";

export function createModuleSession(options) {
  return createDraftObservationSession({...options, route: "modules", stateKey: "moduleView",
    validResponse: (data) => ["recipes", "parents", "instances", "targets", "gpio_providers", "diagnostics"].every((key) => Array.isArray(data[key]))
      && Array.isArray(data.graph?.nodes) && Array.isArray(data.graph?.edges),
    labels: {
      changed: "The draft changed. Load modules to inspect the current source before applying another change.",
      cancelled: "Module inspection cancelled. The working draft is unchanged.",
      refreshing: "The overlay draft was updated. Refreshing its module graph; Save remains a separate action.",
      blocked: "Finish or reconcile the draft request before loading modules. Incomplete source remains editable in the source panel.",
      mismatch: "The module response does not match the current draft. Load modules again.",
      updated: "Module changes are in the working draft. Review and Save the overlay separately.",
      loaded: "Module graph loaded from the current overlay. No hardware or software defaults were applied.",
      rejected: "The module command was not accepted. Reconcile the draft or load modules again.",
      pending: "Draft reconciled. Sync retained source before loading modules.",
      reconciled: "Draft reconciled. Load modules again before applying a change.",
    }});
}

export function modulePosition(value, fallback = {x: 0, y: 0}) {
  return value && Number.isInteger(value.x) && Number.isInteger(value.y)
    && value.x >= 0 && value.x <= 31 && value.y >= 0 && value.y <= 31
    ? {x: value.x, y: value.y} : {...fallback};
}

/** A logical grid is presentation only; coordinates never become a draft command. */
export function modulePositions(nodes, saved = {}) {
  const result = {}; const occupied = new Set();
  for (const node of nodes) {
    if (!Object.hasOwn(saved, node.key)) continue;
    const position = modulePosition(saved[node.key], null);
    if (!Object.hasOwn(position, "x")) continue;
    const slot = `${position.x}:${position.y}`;
    if (!occupied.has(slot)) { result[node.key] = position; occupied.add(slot); }
  }
  const columns = nodes.length > 96 ? 8 : 3;
  const slots = [];
  for (const [start, end] of [[0, columns], [columns, 32]]) {
    for (let y = 0; y < 32; y++) for (let x = start; x < end; x++) slots.push({x, y});
  }
  let next = 0;
  for (const node of nodes) {
    if (Object.hasOwn(result, node.key)) continue;
    while (next < slots.length && occupied.has(`${slots[next].x}:${slots[next].y}`)) next++;
    if (next === slots.length) break;
    result[node.key] = {...slots[next]}; occupied.add(`${slots[next].x}:${slots[next].y}`); next++;
  }
  return result;
}

export function moveModule(positions, key, dx, dy) {
  if (!Object.hasOwn(positions, key)) throw new Error("Select a graph node before moving its card.");
  const current = positions[key]; const next = {x: current.x + dx, y: current.y + dy};
  if (next.x < 0 || next.x > 31 || next.y < 0 || next.y > 31) throw new Error("The card is at the edge of the logical canvas.");
  const result = {...positions, [key]: next};
  const other = Object.keys(positions).find((item) => item !== key && positions[item].x === next.x && positions[item].y === next.y);
  if (other) result[other] = {...current};
  return result;
}

export function createModuleLayoutSession({api, state, workspace, ready, onChange}) {
  const view = state.moduleLayoutView = {busy: false, loaded: false, revision: 0, layout_id: "", positions: {}, dirty: false, error: "", message: ""};
  let disposed = false; let request; let generation = 0; let identity = "";
  const publish = () => { if (!disposed) onChange(); };
  function capture() {
    const module = state.moduleView; const captured = module?.captured;
    if (!ready() || !module?.data || !captured || captured.workspace !== workspace()) return null;
    const {local_generation: _local, ...guard} = captured;
    return {...guard, schema_id: module.data.schema_id};
  }
  const currentIdentity = () => draftPresentationScope(state, workspace());
  function clear() {
    generation++; request?.abort(); request = null; identity = "";
    Object.assign(view, {busy: false, loaded: false, revision: 0, layout_id: "", positions: {}, dirty: false, error: "", message: ""});
  }
  function sync() { if (identity && identity !== currentIdentity()) clear(); }
  async function run(save = false) {
    sync(); const guard = capture(); if (disposed || request || !guard || save && !view.loaded) return false;
    const key = currentIdentity(); identity = key; const token = ++generation; const controller = new AbortController(); request = controller;
    view.busy = true; view.error = ""; view.message = save ? "Saving canvas layout…" : "Loading canvas layout…"; publish();
    const sameRequest = () => !disposed && token === generation && key === currentIdentity();
    const matches = () => sameRequest() && JSON.stringify(capture()) === JSON.stringify(guard);
    try {
      const result = await api("/api/devicetree/editor/drafts/module-layout" + (save ? "/save" : ""), {
        method: "POST", body: {...guard, ...(save ? {expected_layout_revision: view.revision, positions: view.positions} : {})}, signal: controller.signal,
      });
      if (!matches()) return false;
      if (result?.schema_version !== 1 || typeof result.layout_id !== "string" || !Number.isInteger(result.revision) || result.revision < 0
          || !result.positions || Array.isArray(result.positions) || typeof result.positions !== "object"
          || Object.entries(result.positions).some(([key, value]) => !key.startsWith("node:/") || !Object.hasOwn(modulePosition(value, null), "x"))) {
        throw new Error("The layout response is invalid. Your overlay source is unchanged.");
      }
      Object.assign(view, {loaded: true, revision: result.revision, layout_id: result.layout_id, positions: result.positions, dirty: false,
        message: save ? "Canvas layout saved separately. Overlay source is unchanged." : "Canvas layout loaded. Move a selected card using the layout controls."});
      return true;
    } catch (error) {
      if (matches()) { view.error = error.message || String(error); view.message = ""; }
      return false;
    } finally {
      if (request === controller) request = null;
      if (sameRequest()) {
        view.busy = false;
        if (!matches()) view.message = "The source changed during the layout request. Load modules and reload layout before saving positions.";
        publish();
      }
    }
  }
  return {load: () => run(), save: () => run(true), clear, sync,
    canSave: () => Boolean(view.loaded && view.dirty && !view.busy && capture()),
    set(positions) {
      if (!view.loaded || view.busy) return false;
      view.positions = positions; view.dirty = true; view.error = "";
      view.message = "Card moved. Save layout to keep these positions; overlay source is unchanged."; publish(); return true;
    },
    dispose() { disposed = true; clear(); },
  };
}
