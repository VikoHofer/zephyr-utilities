/** Workspace-scoped custom hardware choices and reviewed mutations. */
const array = (value) => Array.isArray(value) ? value : [];
export const boardJobActive = (job) => ["queued", "running"].includes(job?.status);
const createFields = () => ({mode: "reference", search: "", soc: "", reference_id: "", name: "", full_name: "", vendor: "custom", path: ""});

export function boardReferences(catalogue, fields) {
  const terms = String(fields.search || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  return array(catalogue?.references).filter((reference) =>
    (fields.mode !== "soc" || fields.soc && array(reference.socs).includes(fields.soc)) &&
    terms.every((term) => [reference.name, reference.full_name, reference.vendor, ...array(reference.socs)].join(" ").toLowerCase().includes(term)));
}

export function boardRequest(editor) {
  const fields = editor.fields;
  if (editor.operation === "remove") return {operation: "remove", id: editor.id};
  if (editor.operation !== "create") return {operation: editor.operation, ...(editor.id ? {id: editor.id} : {}),
    name: fields.name.trim(), path: fields.path.trim()};
  return {operation: "create", name: fields.name.trim(), full_name: fields.full_name.trim(), vendor: fields.vendor.trim(),
    reference_id: fields.reference_id, ...(fields.mode === "soc" ? {soc: fields.soc} : {}), ...(fields.path.trim() ? {path: fields.path.trim()} : {})};
}

export function createCustomBoardsSession({api, state, workspace, onChange, schedule = setTimeout, unschedule = clearTimeout}) {
  const lifetime = new AbortController();
  let disposed = false, generation = 0, readSequence = 0, editorRevision = 0, polling = false, timer;
  Object.assign(state, {loading: false, busy: "", cancelling: false, error: "", editor: null});
  state.drafts ||= {};
  const publish = () => { if (!disposed) onChange(); };
  const current = (path, epoch) => !disposed && state.workspace === path && workspace() === path && generation === epoch;
  const request = (path, options = {}) => api(path, {...options, signal: lifetime.signal});
  function followWorkspace() {
    const path = workspace();
    if (state.workspace === path) return false;
    generation++; readSequence++; editorRevision++;
    Object.assign(state, {workspace: path, registrations: [], catalogue: null, snapshot: null, job: null, diagnostics: [],
      editor: null, error: "", notice: "", busy: "", loading: false, cancelling: false, needsAnalysis: false});
    publish(); return true;
  }
  async function load() {
    if (disposed) return false;
    followWorkspace();
    const path = state.workspace, epoch = generation, sequence = ++readSequence;
    state.loading = true; state.error = ""; publish();
    try {
      const data = await request("/api/projects/custom-boards");
      if (!current(path, epoch) || sequence !== readSequence) return false;
      if (data.workspace !== path) throw new Error("The selected workspace changed. Refresh custom boards.");
      state.registrations = array(data.registrations); state.catalogue = data.catalogue || null;
      state.snapshot = data.snapshot || null; state.job = data.job || null; state.diagnostics = array(data.diagnostics);
      state.needsAnalysis = !state.snapshot || state.snapshot.status === "stale";
      return true;
    } catch (error) {
      if (current(path, epoch) && sequence === readSequence && error.name !== "AbortError") state.error = error.message || String(error);
      return false;
    } finally { if (current(path, epoch) && sequence === readSequence) { state.loading = false; publish(); } }
  }
  function open(operation, id) {
    if (disposed) return false;
    followWorkspace();
    if (state.busy || state.loading || boardJobActive(state.job)) return false;
    const registration = array(state.registrations).find((item) => item.id === id);
    if (!["register", "create", "update", "remove"].includes(operation) || ["update", "remove"].includes(operation) && !registration) return false;
    if (operation === "create" && (!state.snapshot || state.snapshot.status === "stale")) {
      state.error = "Analyze the workspace before choosing a reference board."; publish(); return false;
    }
    const drafts = state.drafts[state.workspace] || {};
    state.editor = {operation, id, fields: registration ? {name: registration.name, path: registration.path}
      : {...(operation === "create" ? createFields() : {name: "", path: ""}), ...drafts[operation]}, plan: null};
    state.error = ""; editorRevision++; publish(); return true;
  }
  function change(key, value) {
    if (!state.editor || state.busy || !Object.hasOwn(state.editor.fields, key)) return;
    const fields = state.editor.fields;
    fields[key] = value;
    if (["mode", "soc"].includes(key)) fields.reference_id = "";
    state.editor.plan = null; state.error = ""; editorRevision++;
    state.drafts[state.workspace] ||= {};
    state.drafts[state.workspace][state.editor.operation] = {...fields};
    publish();
  }
  function close() {
    if (state.busy === "apply") return false;
    state.editor = null; editorRevision++; publish(); return true;
  }
  function edit() { if (!state.busy && state.editor) { state.editor.plan = null; editorRevision++; publish(); } }
  async function preview() {
    if (disposed || !state.editor || state.busy || boardJobActive(state.job)) return false;
    const path = state.workspace, epoch = generation, owner = state.editor, revision = editorRevision;
    owner.plan = null; state.error = ""; state.busy = "preview"; publish();
    try {
      const data = await request("/api/projects/custom-boards/preview", {method: "POST", body: {workspace: path, ...boardRequest(owner)}});
      if (!current(path, epoch) || state.editor !== owner || revision !== editorRevision) return false;
      if (!data.plan || typeof data.plan.id !== "string" || data.plan.workspace !== path || data.plan.operation !== owner.operation) throw new Error("The custom board review could not be verified. Review again.");
      owner.plan = data.plan; return true;
    } catch (error) {
      if (current(path, epoch) && state.editor === owner && revision === editorRevision && error.name !== "AbortError") state.error = error.message || String(error);
      return false;
    } finally { if (current(path, epoch)) { state.busy = ""; publish(); } }
  }
  async function apply() {
    if (disposed || !state.editor?.plan || state.busy) return false;
    const path = state.workspace, epoch = generation, owner = state.editor;
    state.busy = "apply"; state.error = ""; publish();
    try {
      const data = await request("/api/projects/custom-boards/apply", {method: "POST", body: {workspace: path, plan_id: owner.plan.id}});
      if (!current(path, epoch) || state.editor !== owner) return false;
      if (owner.operation === "remove") {
        if (data.removed !== owner.id) throw new Error("Removal could not be verified. Refresh custom boards.");
        state.registrations = array(state.registrations).filter((entry) => entry.id !== data.removed);
      } else {
        if (!data.registration?.id || data.registration.workspace && data.registration.workspace !== path) throw new Error("The saved registration could not be verified. Refresh custom boards.");
        state.registrations = [...array(state.registrations).filter((entry) => entry.id !== data.registration.id), data.registration];
      }
      state.needsAnalysis = true;
      if (state.snapshot) state.snapshot = {...state.snapshot, status: "stale"};
      state.notice = owner.operation === "remove" ? "Registration removed. Board source files remain on disk. Analyze the workspace to refresh its targets. Independent discovery paths or module settings may still include these boards."
        : owner.operation === "create" ? "Custom board files created and registered. Analyze the workspace, then create an application to configure and build it."
          : "Custom board location saved. Analyze the workspace to refresh its targets.";
      if (state.drafts[path]) delete state.drafts[path][owner.operation];
      state.editor = null; editorRevision++; return true;
    } catch (error) {
      if (current(path, epoch) && error.name !== "AbortError") {
        state.error = error.message || String(error);
        if (state.editor === owner) { owner.plan = null; editorRevision++; }
      }
      return false;
    } finally { if (current(path, epoch)) { state.busy = ""; publish(); } }
  }
  async function analyze() {
    if (disposed) return false;
    followWorkspace();
    if (state.busy || boardJobActive(state.job)) return false;
    const path = state.workspace, epoch = generation;
    const previous = state.snapshot?.request || {};
    const body = {force: true};
    for (const key of ["board_roots", "soc_roots", "sdk_paths", "zephyr_base"]) if (previous[key] !== undefined) body[key] = previous[key];
    state.busy = "analyze"; state.error = ""; state.notice = ""; state.editor = null; editorRevision++; publish();
    try {
      const job = await request("/api/projects/analyze", {method: "POST", body});
      if (!current(path, epoch)) return false;
      state.job = job; return true;
    } catch (error) {
      if (current(path, epoch) && error.name !== "AbortError") state.error = error.message || String(error);
      return false;
    } finally { if (current(path, epoch)) { state.busy = ""; publish(); } }
  }
  async function cancel() {
    if (disposed || !boardJobActive(state.job) || state.cancelling) return false;
    const path = state.workspace, epoch = generation, id = state.job.id;
    state.cancelling = true; publish();
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}/cancel`, {method: "POST", body: {}});
      if (!current(path, epoch) || state.job?.id !== id) return false;
      state.job = job; return true;
    } catch (error) {
      if (current(path, epoch) && error.name !== "AbortError") state.error = error.message || String(error);
      return false;
    } finally { if (current(path, epoch)) { state.cancelling = false; publish(); } }
  }
  async function tick() {
    if (disposed || polling) return;
    if (followWorkspace()) { await load(); return; }
    if (!boardJobActive(state.job)) return;
    const path = state.workspace, epoch = generation, id = state.job.id;
    polling = true;
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}`);
      if (!current(path, epoch) || state.job?.id !== id) return;
      state.job = job;
      if (job.status === "completed") {
        await load();
        if (current(path, epoch) && !state.error) state.notice = "Analysis complete. Available custom targets can now be used to create an application.";
      } else if (job.status === "failed") state.error = job.error || "Environment analysis failed. Open the scan log.";
      publish();
    } catch (error) {
      if (current(path, epoch) && error.name !== "AbortError") { state.error = error.message || String(error); publish(); }
    } finally { polling = false; }
  }
  function queue() { timer = schedule(async () => { await tick(); if (!disposed) queue(); }, 1500); }
  followWorkspace(); queue();
  return {load, open, change, close, edit, preview, apply, analyze, cancel, tick,
    dispose() { disposed = true; generation++; lifetime.abort(); if (timer !== undefined) unschedule(timer); }};
}
