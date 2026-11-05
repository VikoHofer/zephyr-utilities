/** Reviewed management changes share the creator session's workspace and request guards. */
const endpoints = {
  import: ["/api/projects/import-preview", "/api/projects/import"],
  reconnect: ["/api/projects/reconnect-preview", "/api/projects/reconnect"],
  configuration: ["/api/projects/configurations/preview", "/api/projects/configurations/apply"],
  clean: ["/api/projects/outputs/preview", "/api/projects/outputs/clean"],
};
const array = (value) => Array.isArray(value) ? value : [];

export function createManagementActions({state, action, publish, acceptProject, acceptConfiguration, acceptJob}) {
  let revision = 0;
  state.management = null;
  function open(kind, fields = {}, options = {}) {
    if (!Object.hasOwn(endpoints, kind) || state.busy || state.loading || array(state.jobs).some((job) => ["queued", "running"].includes(job.status))) return false;
    state.management = {kind, fields: {...fields}, ...options, plan: null, workspace: state.workspace};
    state.error = ""; revision++; publish();
    return true;
  }
  function close() { state.management = null; revision++; publish(); }
  function invalidate() { if (state.management) { state.management.plan = null; revision++; } }
  function edit() { if (!state.busy) { invalidate(); publish(); } }
  function change(key, value) {
    const current = state.management;
    if (!current || !Object.hasOwn(current.fields, key) || state.busy) return;
    current.fields[key] = value;
    if (key === "board_id") current.fields.target = "";
    current.plan = null; state.error = ""; revision++; publish();
  }
  async function preview() {
    const owner = state.management;
    if (!owner || owner.workspace !== state.workspace) return false;
    const captured = revision;
    owner.plan = null;
    let accepted = false;
    const body = {...owner.fields};
    delete body.search;
    for (const key of ["path", "name"]) if (typeof body[key] === "string") body[key] = body[key].trim();
    await action("management-preview", endpoints[owner.kind][0], body, (data) => {
      if (owner !== state.management || captured !== revision) return;
      if (!data.plan || typeof data.plan.id !== "string" || data.plan.workspace !== state.workspace) throw new Error("The operation review could not be verified. Preview it again.");
      owner.plan = data.plan; accepted = true;
    }, {review: () => revision});
    return accepted;
  }
  async function apply() {
    const owner = state.management;
    const plan = owner?.plan;
    if (!plan || owner.workspace !== state.workspace) return false;
    let accepted = false;
    await action("management-apply", endpoints[owner.kind][1], {plan_id: plan.id}, (data) => {
      if (owner !== state.management) return;
      if (owner.kind === "clean") acceptJob(data);
      else if (owner.kind === "configuration") acceptConfiguration(data, owner.fields.project_id);
      else acceptProject(data.project);
      accepted = true;
      state.management = null; revision++;
    });
    if (!accepted && state.management === owner && state.error) { owner.plan = null; publish(); }
    return accepted;
  }
  return {open, close, change, preview, apply, invalidate, edit};
}
