/** Data selection and request lifetime for the read-only Environment view. */
export const activeJob = (job) => ["queued", "running"].includes(job?.status);

export function lines(value) {
  return [...new Set(String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean))];
}

export function filterBoards(boards, socs, {query = "", chip = "", vendor = ""} = {}) {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const chipTerm = chip.toLocaleLowerCase().trim();
  const chips = new Map(socs.map((soc) => [soc.name, [soc.name, soc.family, soc.series].filter(Boolean).join(" ").toLocaleLowerCase()]));
  return boards.filter((board) => {
    if (vendor && board.vendor !== vendor) return false;
    const boardChips = board.socs || [];
    if (chipTerm && !boardChips.some((name) => (chips.get(name) || String(name).toLocaleLowerCase()).includes(chipTerm))) return false;
    const text = [board.name, board.full_name, board.vendor, ...boardChips, ...(board.qualifiers || []), ...(board.targets || [])].join(" ").toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
}

export function filterSocs(socs, query) {
  const term = String(query || "").trim().toLocaleLowerCase();
  return socs.filter((soc) => [soc.name, soc.family, soc.series].filter(Boolean).join(" ").toLocaleLowerCase().includes(term));
}

export function createEnvironmentSession({api, state, workspace, onChange, schedule = setTimeout, unschedule = clearTimeout}) {
  const lifetime = new AbortController();
  let disposed = false;
  let revision = 0;
  let timer = null;
  let polling = false;
  state.loading = false;
  state.submitting = false;
  state.cancelling = false;
  state.error = "";

  const publish = () => { if (!disposed) onChange(); };
  const current = (path, requestRevision) => !disposed && workspace() === path && state.workspace === path && revision === requestRevision;
  async function request(path, options = {}) {
    return api(path, {...options, signal: lifetime.signal});
  }
  function followWorkspace() {
    const path = workspace();
    if (state.workspace === path) return false;
    revision++;
    state.optionsByWorkspace ||= {};
    if (state.workspace) state.optionsByWorkspace[state.workspace] = {...state.options};
    state.options = {zephyr_base: "", board_roots: "", soc_roots: "", sdk_paths: "", ...state.optionsByWorkspace[path]};
    Object.assign(state, {workspace: path, snapshot: null, job: null, error: "", loading: false, submitting: false, cancelling: false});
    publish();
    return true;
  }
  function acceptJob(job, useResult = true) {
    state.job = job;
    if (useResult && job?.status === "completed" && job.result?.snapshot?.workspace?.path === state.workspace) state.snapshot = job.result.snapshot;
    if (job?.status === "failed") state.error = job.error || "Environment analysis failed. See the scan log.";
  }
  async function load() {
    if (disposed) return;
    followWorkspace();
    const path = state.workspace;
    const requestRevision = ++revision;
    state.loading = true;
    state.error = "";
    publish();
    try {
      const data = await request("/api/projects/environment");
      if (!current(path, requestRevision)) return;
      if (data.workspace !== path) return;
      state.snapshot = data.snapshot?.workspace?.path === path ? data.snapshot : null;
      // The saved observation is authoritative on mount; a retained job can be older,
      // and its result must not discard the server's saved-observation freshness label.
      acceptJob(data.job || null, false);
    } catch (error) {
      if (current(path, requestRevision) && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (current(path, requestRevision)) { state.loading = false; publish(); }
    }
  }
  async function start(options = {}) {
    if (disposed) return;
    followWorkspace();
    if (state.submitting || activeJob(state.job)) return;
    const path = state.workspace;
    const requestRevision = ++revision;
    state.submitting = true;
    state.loading = false;
    state.error = "";
    publish();
    try {
      // Read form options after workspace reconciliation, which may reset paths.
      const body = typeof options === "function" ? options() : options;
      const job = await request("/api/projects/analyze", {method: "POST", body});
      if (current(path, requestRevision)) acceptJob(job);
    } catch (error) {
      if (current(path, requestRevision) && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (current(path, requestRevision)) { state.submitting = false; publish(); }
    }
  }
  async function cancel() {
    if (disposed) return;
    if (!activeJob(state.job) || state.cancelling) return;
    const path = state.workspace;
    const requestRevision = revision;
    const id = state.job.id;
    state.cancelling = true;
    publish();
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}/cancel`, {method: "POST", body: {}});
      if (current(path, requestRevision) && state.job?.id === id) acceptJob(job);
    } catch (error) {
      if (current(path, requestRevision) && error.name !== "AbortError") state.error = error.message || String(error);
    } finally {
      if (current(path, requestRevision)) { state.cancelling = false; publish(); }
    }
  }
  async function tick() {
    if (disposed || polling) return;
    if (followWorkspace()) { await load(); return; }
    if (!activeJob(state.job)) return;
    const path = state.workspace;
    const requestRevision = revision;
    const id = state.job.id;
    polling = true;
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}`);
      if (current(path, requestRevision) && state.job?.id === id) { acceptJob(job); state.error = job.status === "failed" ? state.error : ""; publish(); }
    } catch (error) {
      if (current(path, requestRevision) && error.name !== "AbortError") { state.error = error.message || String(error); publish(); }
    } finally { polling = false; }
  }
  function queue() {
    timer = schedule(async () => { await tick(); if (!disposed) queue(); }, 1200);
  }
  followWorkspace();
  queue();
  return {load, start, cancel, tick, dispose() { disposed = true; revision++; lifetime.abort(); if (timer !== null) unschedule(timer); }};
}
