/** Creation and build requests stay bound to the workspace and reviewed inputs. */
import {activeJob, filterBoards} from "./environment-state.js";
import {copyFragments, fragmentFormatError, fragmentKinds, fragmentModes, fragmentSelectionError, needsFragmentFiles, profileFragments} from "./fragment-state.js";
import {isLocalSample, validateSampleCatalogue, readySample, sampleSelectionError, sampleLabel, sampleCheckSelection, sameSampleSelection, validateSampleCheckResult, currentSampleCompatibility} from "./sample-state.js";
import {createManagementActions} from "./management-state.js";

const defaults = () => ({name: "my_app", destination: "", search: "", board_id: "", target: "", sdk_id: "", template_id: "minimal-c", sample_id: ""});
const configurationDefaults = () => ({name: "", search: "", board_id: "", target: "", sdk_id: "", create_default_overlay: false});
const array = (value) => Array.isArray(value) ? value : [];
const templateLanguages = {"minimal-c": "C", "minimal-cpp": "C++", "local-sample": "C"};
const templateVersion = (entry) => entry?.id === "local-sample" ? entry.version === 1 : [1, 2].includes(entry?.version);
export function creatorTemplates(value) {
  if (!Array.isArray(value) || value.length > 3 || new Set(value.map((entry) => entry?.id)).size !== value.length) return [];
  if (!value.every((entry) => entry && Object.hasOwn(templateLanguages, entry.id) && templateVersion(entry) && entry.language === templateLanguages[entry.id]
    && typeof entry.title === "string" && entry.title.trim() && typeof entry.description === "string" && entry.description.trim())) return [];
  return value;
}
export const creatorTemplateId = (form) => form.template_id === undefined ? "minimal-c" : form.template_id;
export const selectedCreatorTemplate = (state) => creatorTemplates(state.templates).find((entry) => entry.id === creatorTemplateId(state.form)) || null;
export function creatorTemplateLabel(template, templates, sample) {
  if (template?.id === "imported") return "Imported existing application";
  if (template?.id === "local-sample" && template.version === 1) return sampleLabel(sample);
  const descriptor = creatorTemplates(templates).find((entry) => entry.id === template?.id && entry.version === template?.version);
  const title = descriptor?.title || (templateVersion(template) && Object.hasOwn(templateLanguages, template?.id) ? `Minimal ${templateLanguages[template.id]}` : template?.id);
  return title ? `${title} · version ${template.version ?? "unknown"}` : "Template unknown";
}
export const creatorBoards = (snapshot, query = "") => filterBoards(array(snapshot?.boards).filter((board) => board.status === "discovered" && array(board.targets).length), array(snapshot?.socs), {query});
export const creatorSdks = (snapshot) => array(snapshot?.sdks).filter((sdk) => sdk.status === "discovered" && array(sdk.toolchains).some((toolchain) => toolchain.family === "GNU" && toolchain.compiler));
export function creatorBody(form, sample, checkId) {
  return {...Object.fromEntries(["name", "destination", "board_id", "target", "sdk_id"].map((key) => [key, String(form[key] || "").trim()])),
    ...(creatorTemplateId(form) === "minimal-c" ? {} : {template_id: form.template_id}),
    ...(isLocalSample(form) ? {sample_id: form.sample_id, sample_fingerprint: sample?.fingerprint, ...(checkId ? {sample_check_id: checkId} : {})} : {})};
}
export function configurationBody(form) {
  return {...Object.fromEntries(["name", "board_id", "target", "sdk_id"].map((key) => [key, String(form[key] || "").trim()])),
    ...(form.fragments === undefined ? {} : {fragments: copyFragments(form.fragments)})};
}
export function selectedConfiguration(state, project) {
  const id = state.configurationSelections?.[project.id];
  return id ? array(project.configurations).find((entry) => entry.id === id) || null : {id: null, name: "Original", profile: project.profile, revision: project.profile_revision || 0};
}
export function currentCreatorJob(state) {
  return array(state.jobs).find(activeJob) || array(state.jobs).find((job) => job.id === state.jobId) || array(state.jobs)[0] || null;
}
export function createCreatorSession({api, state, workspace, onChange, pendingBoard, isCreating = () => false, schedule = setTimeout, unschedule = clearTimeout}) {
  const lifetime = new AbortController();
  let disposed = false;
  let epoch = 0;
  let formRevision = 0;
  let buildRevision = 0;
  let configurationRevision = 0;
  let timer;
  let reading = false;
  let readSequence = 0;
  let fileSequence = 0;
  let sampleSequence = 0;
  let management;
  Object.assign(state, {loading: false, busy: "", error: "", cancelling: false, removalNotice: ""});
  // Review plans are server-session capabilities; fetch new ones after remount.
  state.plan = null;
  state.buildPlan = null;
  state.form ||= defaults();
  state.configurationSelections ||= {};
  state.configurationDrafts ||= {};
  state.configurationEditor ||= "";
  state.configurationNotice = null;
  state.configurationFiles = {};
  state.sampleCatalogue = null;
  state.sampleCheck = state.sampleCheck?.job?.id ? {...state.sampleCheck, compatibility: null, checkId: null, verified: false,
    submitting: false, cancelling: false, polling: false, needsVerify: true} : null;
  const publish = () => { if (!disposed) onChange(); };
  const current = (path, captured) => !disposed && workspace() === path && state.workspace === path && epoch === captured;
  const request = (path, options = {}) => api(path, {...options, signal: lifetime.signal});
  function followWorkspace() {
    const path = workspace();
    if (path === state.workspace) return false;
    if (state.workspace) {
      state.formsByWorkspace ||= {};
      state.formsByWorkspace[state.workspace] = {...state.form};
    }
    epoch++; formRevision++; buildRevision++; configurationRevision++; fileSequence++; sampleSequence++;
    Object.assign(state, {workspace: path, snapshot: null, templates: [], projects: [], unavailableProjects: [], jobs: [], diagnostics: [], jobId: null, plan: null, buildPlan: null, error: "", removalNotice: "", loading: false, busy: "", cancelling: false, form: {...defaults(), ...state.formsByWorkspace?.[path]}, configurationSelections: {}, configurationDrafts: {}, configurationEditor: "", configurationNotice: null, configurationFiles: {}, sampleCatalogue: null, sampleCheck: null});
    state.management = null;
    return true;
  }
  function choosePending() {
    const value = pendingBoard?.();
    if (value?.workspace !== state.workspace) return;
    const board = creatorBoards(state.snapshot).find((entry) => entry.id === value.board_id);
    if (!board) return;
    if (state.form.board_id !== board.id) {
      state.form.board_id = board.id; state.form.target = ""; state.form.search = "";
      state.plan = null; formRevision++;
      invalidateSampleCheck();
    }
  }
  function acceptData(data) {
    if (data.workspace !== state.workspace) return;
    const templates = creatorTemplates(data.templates);
    if (JSON.stringify(templates) !== JSON.stringify(state.templates)) { state.plan = null; formRevision++; }
    state.templates = templates;
    const snapshot = data.snapshot?.workspace?.path === state.workspace ? data.snapshot : null;
    if (snapshot?.fingerprint !== state.snapshot?.fingerprint || snapshot?.zephyr?.path !== state.snapshot?.zephyr?.path) {
      state.plan = null; state.buildPlan = null; formRevision++; buildRevision++; configurationRevision++;
      sampleSequence++; state.sampleCatalogue = null;
      invalidateSampleCheck();
      management?.invalidate();
    }
    const previousProjects = array(state.projects);
    state.snapshot = snapshot;
    state.projects = array(data.projects).filter((project) => project.workspace === state.workspace);
    state.unavailableProjects = array(data.unavailable_projects).filter((project) => project.workspace === state.workspace);
    if (state.buildPlan && !state.projects.some((project) => project.id === state.buildPlan.project_id)) state.buildPlan = null;
    for (const previous of previousProjects) {
      const project = state.projects.find((item) => item.id === previous.id);
      if (!project || JSON.stringify(selectedConfiguration(state, previous)) !== JSON.stringify(selectedConfiguration(state, project))) {
        buildRevision++;
        if (state.buildPlan?.project_id === previous.id) state.buildPlan = null;
      }
    }
    for (const projectId of new Set([...Object.keys(state.configurationSelections), ...Object.keys(state.configurationDrafts)])) {
      const project = state.projects.find((item) => item.id === projectId);
      if (!project) {
        delete state.configurationSelections[projectId]; delete state.configurationDrafts[projectId]; delete state.configurationFiles[projectId];
        if (state.configurationEditor === projectId) state.configurationEditor = "";
      } else if (!selectedConfiguration(state, project)) {
        state.configurationSelections[projectId] = "";
        state.configurationNotice = {project_id: projectId, message: "The selected configuration is no longer available. Original is selected; review it before building."};
      }
    }
    state.jobs = array(data.jobs).filter((job) => job.metadata?.operation !== "sample-check" && (!job.metadata?.workspace || job.metadata.workspace === state.workspace));
    state.diagnostics = array(data.diagnostics);
    state.needsReload = false;
    choosePending();
    for (const form of [state.form, ...Object.values(state.configurationDrafts)]) {
      const board = creatorBoards(snapshot).find((entry) => entry.id === form.board_id);
      if (!board) { form.board_id = ""; form.target = ""; }
      else if (!array(board.targets).includes(form.target)) form.target = "";
      if (!creatorSdks(snapshot).some((sdk) => sdk.id === form.sdk_id)) form.sdk_id = "";
    }
    if (state.sampleCheck && ["sample_id", "board_id", "target", "sdk_id"].some((key) => state.sampleCheck.selection?.[key] !== state.form[key])) invalidateSampleCheck();
    const editor = state.configurationEditor;
    if (editor && needsFragmentFiles(copyFragments(state.configurationDrafts[editor]?.fragments)) && !state.configurationFiles[editor]) loadConfigurationFiles(editor);
  }
  async function load({background = false} = {}) {
    if (disposed) return;
    followWorkspace();
    const path = state.workspace;
    const captured = epoch;
    const sequence = ++readSequence;
    let accepted = false;
    if (!background) { state.loading = true; publish(); }
    try {
      const data = await request("/api/projects/creator");
      if (current(path, captured) && sequence === readSequence) { acceptData(data); accepted = data.workspace === path; }
    } catch (error) {
      if (current(path, captured) && sequence === readSequence && error.name !== "AbortError") {
        state.error = error.message || String(error); state.plan = null; state.buildPlan = null;
      }
    } finally {
      if (current(path, captured) && sequence === readSequence) { state.loading = false; publish(); }
    }
    if (!background && current(path, captured) && sequence === readSequence) await ensureSamples();
    return accepted && current(path, captured) && sequence === readSequence;
  }
  async function loadSamples({restore = false} = {}) {
    if (disposed) return;
    followWorkspace();
    if (!isLocalSample(state.form) || !selectedCreatorTemplate(state) || !state.snapshot || state.snapshot.status === "stale") return;
    const path = state.workspace;
    const captured = epoch;
    const sequence = ++sampleSequence;
    const catalogue = {loading: true, ready: false, error: "", samples: [], diagnostics: []};
    if (!restore) invalidateSampleCheck();
    state.sampleCatalogue = catalogue;
    formRevision++; state.plan = null;
    publish();
    try {
      const data = await request("/api/projects/samples", {method: "POST", body: {workspace: path}});
      if (!current(path, captured) || sequence !== sampleSequence) return;
      Object.assign(catalogue, validateSampleCatalogue(data, path, state.snapshot?.zephyr?.path), {ready: true});
    } catch (error) {
      if (current(path, captured) && sequence === sampleSequence && error.name !== "AbortError") catalogue.error = error.message || String(error);
    } finally {
      if (current(path, captured) && sequence === sampleSequence) { catalogue.loading = false; publish(); }
    }
    if (current(path, captured) && sequence === sampleSequence && catalogue.ready) {
      if (state.sampleCheck && !sameSampleSelection(state.sampleCheck.selection, sampleCheckSelection(state))) invalidateSampleCheck();
      await pollSampleCheck();
    }
  }
  const ensureSamples = () => isCreating() && isLocalSample(state.form) && !state.sampleCatalogue ? loadSamples({restore: true}) : Promise.resolve();
  function changeField(key, value) {
    if (disposed) return;
    followWorkspace();
    if (!(key in defaults())) return;
    if (state.form[key] === value) return;
    state.form[key] = value;
    if (key === "board_id") state.form.target = "";
    if (["template_id", "sample_id", "board_id", "target", "sdk_id"].includes(key)) invalidateSampleCheck();
    formRevision++; state.plan = null; state.error = "";
    publish();
    if (key === "template_id") ensureSamples();
  }
  function invalidateSampleCheck() {
    if (!state.sampleCheck) return;
    Object.assign(state.sampleCheck, {stale: true, verified: false, compatibility: null, checkId: null, needsVerify: false});
  }
  function acceptSampleCheckJob(check, job) {
    if (!job || typeof job.id !== "string" || !["queued", "running", "completed", "failed", "cancelled"].includes(job.status)
      || check.job?.id && job.id !== check.job.id) throw new Error("The compatibility job could not be verified. Check compatibility again.");
    check.job = job;
    check.needsVerify = false;
    check.error = "";
    if (job.status === "failed") check.error = `${job.error || "Compatibility check failed."} Check compatibility again after resolving the problem.`;
    if (job.status === "cancelled") check.error = "Compatibility check cancelled. You can check again or preview with compatibility unknown.";
    if (job.status === "completed" && !check.stale && sameSampleSelection(check.selection, sampleCheckSelection(state))) {
      check.compatibility = validateSampleCheckResult(job.result, check.selection);
      check.checkId = job.result.check_id;
      check.verified = true;
      formRevision++; state.plan = null;
    }
  }
  async function checkSample() {
    if (disposed) return;
    if (followWorkspace()) { publish(); await load(); return; }
    const selection = sampleCheckSelection(state);
    if (!selection || state.busy || state.loading || state.sampleCheck?.submitting || activeJob(state.sampleCheck?.job)) return;
    const path = state.workspace;
    const captured = epoch;
    const check = {selection, job: null, submitting: true, cancelling: false, polling: false, needsVerify: false,
      verified: false, stale: false, compatibility: null, checkId: null, error: ""};
    state.sampleCheck = check;
    formRevision++; state.plan = null;
    publish();
    try {
      const {environment_fingerprint: _fingerprint, ...body} = selection;
      const job = await request("/api/projects/sample-check", {method: "POST", body});
      if (current(path, captured) && state.sampleCheck === check) acceptSampleCheckJob(check, job);
    } catch (error) {
      if (current(path, captured) && state.sampleCheck === check && error.name !== "AbortError") check.error = error.message || String(error);
    } finally {
      if (current(path, captured) && state.sampleCheck === check) { check.submitting = false; publish(); }
    }
  }
  async function pollSampleCheck() {
    const check = state.sampleCheck;
    if (disposed || !check?.job?.id || check.polling || check.submitting || (!check.needsVerify && !activeJob(check.job))) return;
    if (check.needsVerify && !check.stale && !sampleCheckSelection(state)) return;
    const path = state.workspace;
    const captured = epoch;
    check.polling = true;
    const sequence = check.requestRevision = (check.requestRevision || 0) + 1;
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(check.job.id)}`);
      if (current(path, captured) && state.sampleCheck === check && check.requestRevision === sequence) acceptSampleCheckJob(check, job);
    } catch (error) {
      if (current(path, captured) && state.sampleCheck === check && check.requestRevision === sequence && error.name !== "AbortError") {
        check.error = `${error.message || String(error)} Use Check compatibility to try again.`;
        check.needsVerify = false; check.verified = false; check.checkId = null; check.compatibility = null;
        check.job = {...check.job, status: "failed"};
      }
    } finally {
      check.polling = false;
      if (current(path, captured) && state.sampleCheck === check) publish();
    }
  }
  async function cancelSampleCheck() {
    const check = state.sampleCheck;
    if (disposed || !activeJob(check?.job) || check.cancelling) return;
    const path = state.workspace;
    const captured = epoch;
    const sequence = check.requestRevision = (check.requestRevision || 0) + 1;
    check.cancelling = true; publish();
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(check.job.id)}/cancel`, {method: "POST", body: {}});
      if (current(path, captured) && state.sampleCheck === check && check.requestRevision === sequence) acceptSampleCheckJob(check, job);
    } catch (error) {
      if (current(path, captured) && state.sampleCheck === check && check.requestRevision === sequence && error.name !== "AbortError") check.error = error.message || String(error);
    } finally {
      if (current(path, captured) && state.sampleCheck === check) { check.cancelling = false; publish(); }
    }
  }
  function selectConfiguration(projectId, configurationId) {
    if (disposed) return;
    followWorkspace();
    const project = array(state.projects).find((item) => item.id === projectId);
    if (!project || configurationId && !array(project.configurations).some((item) => item.id === configurationId)) return;
    if ((state.configurationSelections[projectId] || "") === configurationId) return;
    state.configurationSelections[projectId] = configurationId;
    buildRevision++; configurationRevision++; state.buildPlan = null; state.configurationNotice = null; state.error = "";
    publish();
  }
  function editConfiguration(projectId, {editSelected = false} = {}) {
    if (disposed) return;
    followWorkspace();
    const project = array(state.projects).find((item) => item.id === projectId);
    if (!project) return;
    const selected = selectedConfiguration(state, project);
    const profile = selected?.profile;
    if (state.configurationEditor !== projectId && state.configurationFiles[state.configurationEditor]?.loading) delete state.configurationFiles[state.configurationEditor];
    if (!state.configurationDrafts[projectId] || editSelected || state.configurationDrafts[projectId]._editing) {
      const board = creatorBoards(state.snapshot).find((item) => item.id === profile?.board_id);
      state.configurationDrafts[projectId] = {...configurationDefaults(), name: editSelected ? selected?.name || "" : "", board_id: board?.id || "",
        target: array(board?.targets).includes(profile?.target) ? profile.target : "",
        sdk_id: creatorSdks(state.snapshot).some((item) => item.id === profile?.sdk?.id) ? profile.sdk.id : "",
        ...(editSelected ? {_editing: true, _configuration_id: selected?.id || "", _expected_revision: selected?.revision || 0} : {}),
        ...(profileFragments(profile) === undefined ? {} : {fragments: copyFragments(profileFragments(profile))})};
    }
    state.configurationEditor = projectId;
    configurationRevision++;
    state.configurationNotice = null;
    publish();
    if (needsFragmentFiles(copyFragments(state.configurationDrafts[projectId].fragments))) loadConfigurationFiles(projectId);
  }
  function closeConfiguration() {
    if (disposed) return;
    if (state.configurationFiles[state.configurationEditor]?.loading) delete state.configurationFiles[state.configurationEditor];
    fileSequence++;
    state.configurationEditor = "";
    configurationRevision++;
    publish();
  }
  async function loadConfigurationFiles(projectId) {
    if (disposed) return;
    followWorkspace();
    if (state.configurationEditor !== projectId || !array(state.projects).some((item) => item.id === projectId)) return;
    const path = state.workspace;
    const captured = epoch;
    const sequence = ++fileSequence;
    const catalogue = {loading: true, ready: false, error: "", files: {kconfig: [], overlays: []}, diagnostics: []};
    state.configurationFiles[projectId] = catalogue;
    publish();
    try {
      const data = await request("/api/projects/configuration-files", {method: "POST", body: {workspace: path, project_id: projectId}});
      if (!current(path, captured) || sequence !== fileSequence || state.configurationEditor !== projectId || state.configurationFiles[projectId] !== catalogue) return;
      if (data.workspace !== path || data.project_id !== projectId || !fragmentKinds.every((kind) => Array.isArray(data.files?.[kind]) && data.files[kind].every((item) => typeof item === "string"))) throw new Error("The application file list could not be verified. Refresh files again.");
      catalogue.files = data.files;
      catalogue.diagnostics = array(data.diagnostics);
      catalogue.ready = true;
    } catch (error) {
      if (current(path, captured) && sequence === fileSequence && state.configurationEditor === projectId && error.name !== "AbortError") catalogue.error = error.message || String(error);
    } finally {
      if (current(path, captured) && sequence === fileSequence && state.configurationEditor === projectId && state.configurationFiles[projectId] === catalogue) { catalogue.loading = false; publish(); }
    }
  }
  function changeFragment(projectId, kind, action, value) {
    if (disposed) return;
    followWorkspace();
    const form = state.configurationDrafts[projectId];
    if (!form || !fragmentKinds.includes(kind)) return;
    const fragments = copyFragments(form.fragments);
    if (fragmentFormatError(fragments)) return;
    const part = fragments[kind];
    if (action === "mode") {
      if (!fragmentModes.includes(value) || part.mode === value) return;
      part.mode = value;
      if (value === "auto") part.files = [];
    } else if (action === "add") {
      const catalogue = state.configurationFiles[projectId];
      if (part.mode === "auto" || !catalogue?.ready || catalogue.loading || !catalogue.files[kind].includes(value) || part.files.includes(value) || part.files.length >= 32) return;
      part.files.push(value);
    } else {
      const index = part.files.indexOf(value);
      if (index < 0) return;
      if (action === "remove") part.files.splice(index, 1);
      else if (action === "up" && index > 0) [part.files[index - 1], part.files[index]] = [part.files[index], part.files[index - 1]];
      else if (action === "down" && index < part.files.length - 1) [part.files[index + 1], part.files[index]] = [part.files[index], part.files[index + 1]];
      else return;
    }
    form.fragments = fragments;
    configurationRevision++; state.error = ""; state.configurationNotice = null;
    publish();
    if (needsFragmentFiles(fragments) && !state.configurationFiles[projectId]) loadConfigurationFiles(projectId);
  }
  function changeConfigurationField(projectId, key, value) {
    if (disposed) return;
    followWorkspace();
    const form = state.configurationDrafts[projectId];
    if (!form || !(key in configurationDefaults()) || form[key] === value) return;
    form[key] = value;
    if (key === "board_id") form.target = "";
    configurationRevision++; state.error = ""; state.configurationNotice = null;
    publish();
  }
  async function action(kind, endpoint, body, accept, {review = null} = {}) {
    if (disposed) return;
    if (followWorkspace()) { publish(); await load(); return; }
    if (state.busy || state.loading || array(state.jobs).some(activeJob)) return;
    const path = state.workspace;
    const captured = epoch;
    const reviewed = review?.();
    let submitted = false;
    state.busy = kind; state.error = "";
    if (kind === "preview") state.plan = null;
    if (kind === "build-preview") state.buildPlan = null;
    publish();
    try {
      const payload = typeof body === "function" ? body() : body;
      submitted = true;
      const data = await request(endpoint, {method: "POST", body: {...payload, workspace: path}});
      if (current(path, captured) && (!review || reviewed === review())) accept(data);
    } catch (error) {
      if (current(path, captured) && error.name !== "AbortError") {
        state.error = error.message || String(error); state.plan = null; state.buildPlan = null;
        if (submitted && kind === "preview" && isLocalSample(state.form) && state.sampleCheck?.checkId) {
          invalidateSampleCheck(); state.error += " Check compatibility again to refresh the saved result.";
        }
      }
    } finally {
      if (current(path, captured)) { state.busy = ""; publish(); }
    }
  }
  function acceptJob(job) {
    state.jobs = [job, ...array(state.jobs).filter((entry) => entry.id !== job.id)];
    state.jobId = job.id; state.plan = null; state.buildPlan = null;
    state.needsReload = true;
  }
  const preview = () => action("preview", "/api/projects/preview", () => {
    if (!selectedCreatorTemplate(state)) throw new Error("Choose an available application template before previewing. Refresh the list if no templates are available.");
    const sampleError = sampleSelectionError(state);
    if (sampleError) throw new Error(sampleError);
    return creatorBody(state.form, readySample(state), currentSampleCompatibility(state) ? state.sampleCheck.checkId : null);
  }, (data) => {
    if (data.plan?.workspace === state.workspace) state.plan = data.plan;
  }, {review: () => formRevision});
  const create = () => {
    if (!state.plan) return Promise.resolve();
    return action("create", "/api/projects/create", () => ({plan_id: state.plan.id}), acceptJob);
  };
  const previewBuild = (projectId, operation, reuseAttemptId = null) => {
    if (!["configure", "build"].includes(operation) || !array(state.projects).some((project) => project.id === projectId)) return Promise.resolve();
    const project = state.projects.find((item) => item.id === projectId);
    const formatError = fragmentFormatError(copyFragments(profileFragments(selectedConfiguration(state, project)?.profile)));
    if (formatError) { state.error = formatError; state.buildPlan = null; publish(); return Promise.resolve(); }
    const configurationId = state.configurationSelections[projectId];
    if (reuseAttemptId && !array(project.attempts).some((attempt) => attempt.id === reuseAttemptId && attempt.reuse_allowed)) {
      state.error = "This output is no longer eligible for reuse. Review a fresh build instead."; state.buildPlan = null; publish(); return Promise.resolve();
    }
    return action("build-preview", "/api/projects/build-preview", {project_id: projectId, operation, ...(configurationId ? {configuration_id: configurationId} : {}), ...(reuseAttemptId ? {reuse_attempt_id: reuseAttemptId} : {})}, (data) => {
      if (data.plan?.workspace === state.workspace) state.buildPlan = data.plan;
    }, {review: () => buildRevision});
  };
  const saveConfiguration = (projectId) => {
    const form = state.configurationDrafts[projectId];
    if (!form || !array(state.projects).some((project) => project.id === projectId)) return Promise.resolve();
    const fragmentError = fragmentSelectionError(copyFragments(form.fragments), state.configurationFiles[projectId]);
    if (fragmentError) { state.error = fragmentError; publish(); return Promise.resolve(); }
    const revision = configurationRevision;
    return action("configuration-save", "/api/projects/configurations", {project_id: projectId, ...configurationBody(form)}, (data) => {
      const configuration = data.configuration;
      const project = array(state.projects).find((item) => item.id === projectId);
      if (!project || configuration?.project_id !== projectId || configuration.workspace !== state.workspace) return;
      project.configurations = [...array(project.configurations).filter((item) => item.id !== configuration.id), configuration];
      readSequence++;
      if (revision === configurationRevision) {
        state.configurationSelections[projectId] = configuration.id;
        state.configurationEditor = "";
        delete state.configurationDrafts[projectId];
      }
      buildRevision++; state.buildPlan = null;
      state.configurationNotice = {project_id: projectId, message: `Configuration “${configuration.name}” saved. Choose Configure or Build when ready.`};
      state.needsReload = true;
    });
  };
  const build = () => {
    if (!state.buildPlan) return Promise.resolve();
    return action("build", "/api/projects/build", () => ({build_plan_id: state.buildPlan.id}), acceptJob);
  };
  function acceptProject(project) {
    if (!project || typeof project.id !== "string" || project.workspace !== state.workspace || typeof project.path !== "string") throw new Error("The updated application record could not be verified. Refresh applications.");
    const previous = [...array(state.projects), ...array(state.unavailableProjects)].find((item) => item.id === project.id);
    const updated = {...previous, ...project, configurations: project.configurations || previous?.configurations || [], attempts: project.attempts || previous?.attempts || []};
    state.projects = [updated, ...array(state.projects).filter((item) => item.id !== project.id)];
    state.unavailableProjects = array(state.unavailableProjects).filter((item) => item.id !== project.id);
    readSequence++; buildRevision++; state.buildPlan = null; state.needsReload = true;
    delete state.configurationFiles[project.id];
  }
  function acceptManagedConfiguration(data, projectId) {
    const configuration = data.configuration;
    if (!data.project || data.project.id !== projectId || !configuration || !configuration.profile || configuration.id !== null && typeof configuration.id !== "string") throw new Error("The updated configuration could not be verified. Refresh applications.");
    acceptProject(data.project);
    const project = state.projects.find((item) => item.id === projectId);
    if (configuration.id) project.configurations = [...array(project.configurations).filter((item) => item.id !== configuration.id), configuration];
    state.configurationSelections[projectId] = configuration.id || "";
    clearConfigurationDraft(projectId);
    state.configurationNotice = {project_id: projectId, message: `Configuration “${configuration.name}” saved. Review Configure or Build to use it.`};
  }
  async function previewConfiguration(projectId) {
    const form = state.configurationDrafts[projectId];
    if (!form || !array(state.projects).some((project) => project.id === projectId)) return false;
    const fragmentError = fragmentSelectionError(copyFragments(form.fragments), state.configurationFiles[projectId]);
    if (fragmentError) { state.error = fragmentError; publish(); return false; }
    const body = {project_id: projectId, ...configurationBody(form), create_default_overlay: Boolean(form.create_default_overlay),
      ...(form._editing ? {configuration_id: form._configuration_id, expected_revision: form._expected_revision} : {})};
    if (!management.open("configuration", body)) return false;
    return management.preview();
  }
  function dismissReview(kind) {
    if (disposed) return;
    if (kind === "create") { state.plan = null; formRevision++; }
    else if (kind === "build") { state.buildPlan = null; buildRevision++; }
    else return;
    publish();
  }
  const allProjects = () => [...array(state.projects), ...array(state.unavailableProjects)];
  function clearConfigurationDraft(projectId) {
    delete state.configurationDrafts[projectId]; delete state.configurationFiles[projectId];
    if (state.configurationEditor === projectId) state.configurationEditor = "";
    configurationRevision++; fileSequence++;
  }
  async function removeRecord(endpoint, body, id, accept) {
    let removed = false;
    await action("remove", endpoint, body, (data) => {
      if (data.removed !== id) throw new Error("Removal could not be verified. Refresh the application list.");
      readSequence++; buildRevision++;
      if (state.buildPlan?.project_id === body.project_id) state.buildPlan = null;
      accept();
      state.needsReload = true;
      removed = true;
    });
    return removed;
  }
  function removeProject(projectId) {
    const project = allProjects().find((item) => item.id === projectId);
    if (!project) return Promise.resolve(false);
    return removeRecord("/api/projects/remove", {project_id: projectId}, projectId, () => {
      state.projects = array(state.projects).filter((item) => item.id !== projectId);
      state.unavailableProjects = array(state.unavailableProjects).filter((item) => item.id !== projectId);
      delete state.configurationSelections[projectId]; clearConfigurationDraft(projectId);
      if (state.configurationNotice?.project_id === projectId) state.configurationNotice = null;
      state.jobs = array(state.jobs).filter((job) => job.metadata?.project_id !== projectId && job.result?.project?.id !== projectId && job.project?.id !== projectId);
      state.removalNotice = `Saved application “${project.name}”, its configurations, build history and output records removed. Application and build files remain on disk.`;
    });
  }
  function removeConfiguration(projectId, configurationId) {
    const project = array(state.projects).find((item) => item.id === projectId);
    const configuration = array(project?.configurations).find((item) => item.id === configurationId);
    if (!configurationId || !configuration) return Promise.resolve(false);
    return removeRecord("/api/projects/configurations/remove", {project_id: projectId, configuration_id: configurationId}, configurationId, () => {
      const currentProject = array(state.projects).find((item) => item.id === projectId);
      if (currentProject) currentProject.configurations = array(currentProject.configurations).filter((item) => item.id !== configurationId);
      if (state.configurationSelections[projectId] === configurationId) state.configurationSelections[projectId] = "";
      clearConfigurationDraft(projectId);
      state.configurationNotice = {project_id: projectId, message: `Configuration “${configuration.name}” removed. Review the selected configuration before building.`};
      state.removalNotice = "Build history and application files were preserved.";
    });
  }
  function removeAttempt(projectId, attemptId) {
    const project = allProjects().find((item) => item.id === projectId);
    const attempt = array(project?.attempts).find((item) => item.id === attemptId);
    if (!attempt || activeJob(attempt)) return Promise.resolve(false);
    return removeRecord("/api/projects/attempts/remove", {project_id: projectId, attempt_id: attemptId}, attemptId, () => {
      const currentProject = allProjects().find((item) => item.id === projectId);
      if (currentProject) currentProject.attempts = array(currentProject.attempts).filter((item) => item.id !== attemptId);
      state.jobs = array(state.jobs).filter((job) => job.metadata?.attempt_id !== attemptId && job.result?.attempt?.id !== attemptId && job.attempt?.id !== attemptId);
      state.removalNotice = "Build history entry removed. Its output files remain on disk.";
    });
  }
  async function cancel() {
    if (disposed) return;
    if (followWorkspace()) { publish(); await load(); return; }
    const job = currentCreatorJob(state);
    if (!activeJob(job) || state.cancelling) return;
    const path = state.workspace;
    const captured = epoch;
    state.cancelling = true; publish();
    try {
      const updated = await request(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {method: "POST", body: {}});
      if (current(path, captured)) acceptJob(updated);
    } catch (error) {
      if (current(path, captured) && error.name !== "AbortError") { state.error = error.message || String(error); state.plan = null; state.buildPlan = null; }
    } finally { if (current(path, captured)) { state.cancelling = false; publish(); } }
  }
  async function tickCreator({force = false} = {}) {
    if (disposed || reading) return;
    const switched = followWorkspace();
    if (switched) publish();
    if (!switched && (state.busy || state.loading || (!force && !state.needsReload && !array(state.jobs).some(activeJob)))) return;
    reading = true;
    try { await load({background: !switched && !force}); } finally { reading = false; }
  }
  const tick = (options) => Promise.all([tickCreator(options), pollSampleCheck()]);
  function queue() { timer = schedule(async () => { await tick(); if (!disposed) queue(); }, 1200); }
  management = createManagementActions({state, action, publish, acceptProject, acceptConfiguration: acceptManagedConfiguration, acceptJob});
  followWorkspace(); queue();
  return {load, loadSamples, ensureSamples, refresh: () => tick({force: true}), preview, create, previewBuild, build, cancel, changeField,
    removeProject, removeConfiguration, removeAttempt, dismissReview, checkSample, cancelSampleCheck,
    management, previewConfiguration,
    selectConfiguration, editConfiguration, closeConfiguration, changeConfigurationField, saveConfiguration, loadConfigurationFiles, changeFragment,
    tick, dispose() { disposed = true; epoch++; lifetime.abort(); if (timer !== undefined) unschedule(timer); }};
}
