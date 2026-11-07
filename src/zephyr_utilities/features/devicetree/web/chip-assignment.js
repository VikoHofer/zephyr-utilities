/** Pin proposals remain local until one canonical, guarded draft command is applied. */
const array = (value) => Array.isArray(value) ? value : [];
const kinds = new Set(["uart", "i2c", "spi"]);
const label = (controller, signal) => `${controller.label || controller.id} ${signal.label || signal.id.toUpperCase()}`;

export function activationChoices(data, pin) {
  if (!data?.activation || !pin) return [];
  const activation = data.activation; const choices = [];
  const globalReason = activation.available ? "" : activation.reason || "Pin activation is unavailable for this observation.";
  const gpio = activation.gpio;
  if (array(gpio?.pins).includes(pin.id) || Object.hasOwn(gpio?.unavailable || {}, pin.id)) choices.push({id: "gpio", kind: "gpio", label: "Application GPIO",
    available: !globalReason && gpio.available !== false && !gpio.unavailable?.[pin.id],
    reason: globalReason || gpio.unavailable?.[pin.id] || gpio.reason || ""});
  for (const controller of array(activation.controllers)) {
    if (!kinds.has(controller.kind)) continue;
    for (const signal of array(controller.signals)) {
      if (!array(signal.candidates).includes(pin.id)) continue;
      const reason = globalReason || (!controller.available ? controller.reason || "This controller cannot be edited." : "")
        || signal.unavailable?.[pin.id] || "";
      choices.push({id: `${controller.id}:${signal.id}`, kind: "route", controller: controller.id, signal: signal.id,
        label: label(controller, signal), available: !reason, reason});
    }
  }
  return choices;
}

export function createPinAssignmentSession({state, workspace, drafts, onChange}) {
  const view = {proposal: null, busy: false, error: "", message: ""};
  let disposed = false; let generation = 0; let visibleScope;
  const publish = () => { if (!disposed) onChange?.(); };
  const scope = () => JSON.stringify([workspace(), state.data?.id, state.data?.revision,
    state.draftView?.entry?.server?.id, state.data?.editing_document?.id]);
  function identity() {
    const data = state.chipView?.data; const draft = state.draftView?.entry;
    if (disposed || state.loading || state.documentLoading || !state.draftView?.ready || !draft?.server
        || draft.server.syntax?.blocked || drafts.pending() || draft.flight || draft.historyOperation
        || draft.externalOperation || draft.uncertain || draft.conflict || state.chipView?.busy || state.chipView?.stale
        || !data?.activation?.available || !data.activation.observation_id
        || data.context_id !== state.data?.id || data.expected_revision !== state.data?.revision
        || data.draft_id !== draft.server.id || data.draft_revision !== draft.server.revision
        || data.draft_sha256 !== draft.server.sha256 || state.data?.workspace !== workspace()) return null;
    return JSON.stringify([scope(), draft.server.revision, draft.server.sha256, data.activation.observation_id]);
  }
  function sync() {
    const nextScope = scope();
    if (visibleScope !== undefined && visibleScope !== nextScope) {
      generation++; view.proposal = null; view.busy = false; view.error = ""; view.message = "";
    }
    visibleScope = nextScope;
    if (view.proposal && !view.busy && identity() !== view.proposal.identity) {
      view.proposal = null;
      if (!view.error) view.message = "The source or chip observation changed. Choose a function again before applying.";
    }
  }
  function open(pin, choiceId) {
    sync(); const current = identity();
    const data = state.chipView?.data;
    const choice = activationChoices(data, pin).find((item) => item.id === choiceId);
    if (!current || view.busy || !choice?.available) return false;
    const controller = array(data.activation.controllers).find((item) => item.id === choice.controller);
    const signals = {};
    for (const signal of array(controller?.signals)) {
      const existing = controller.current?.[signal.id];
      signals[signal.id] = array(signal.candidates).includes(existing) && !signal.unavailable?.[existing] ? existing : "";
    }
    if (controller) signals[choice.signal] = pin.id;
    view.proposal = {kind: choice.kind, choice: choice.id, pin: pin.id, controller, signals,
      identity: current, scope: scope(), observation_id: data.activation.observation_id};
    view.error = ""; view.message = ""; publish(); return true;
  }
  function setSignal(id, value) {
    sync(); const proposal = view.proposal;
    const signal = array(proposal?.controller?.signals).find((item) => item.id === id);
    if (!proposal || view.busy || !signal || value && (!array(signal.candidates).includes(value) || signal.unavailable?.[value])) return false;
    proposal.signals[id] = value; view.error = ""; publish(); return true;
  }
  function problem() {
    const proposal = view.proposal;
    if (!proposal) return "";
    if (proposal.kind === "gpio") return "";
    const values = [];
    for (const signal of array(proposal.controller.signals)) {
      const value = proposal.signals[signal.id];
      if (!value && signal.required !== false) return `Choose ${signal.label || signal.id.toUpperCase()} to complete this assignment.`;
      if (value && (!array(signal.candidates).includes(value) || signal.unavailable?.[value])) return "A selected pin is unavailable for this signal.";
      if (value) values.push(value);
    }
    if (new Set(values).size !== values.length) return "Each signal needs a different pin.";
    return "";
  }
  async function apply() {
    sync(); const proposal = view.proposal;
    if (!proposal || view.busy || problem() || identity() !== proposal.identity) return false;
    const token = ++generation; const revision = state.draftView.entry.server.revision;
    const command = proposal.kind === "gpio"
      ? {kind: "chip_gpio", observation_id: proposal.observation_id, pin: proposal.pin}
      : {kind: "chip_assign", observation_id: proposal.observation_id, controller: proposal.controller.id,
        signals: Object.fromEntries(Object.entries(proposal.signals).filter(([, value]) => value))};
    view.busy = true; view.error = ""; view.message = "Applying pin assignment…"; publish();
    let accepted = false;
    try {
      accepted = await drafts.command(command);
      if (disposed || token !== generation || scope() !== proposal.scope) return false;
      if (accepted) {
        view.proposal = null;
        view.message = state.draftView.entry.server.revision === revision
          ? "This assignment is already configured. The overlay is unchanged."
          : "Assignment applied to the overlay draft. Undo or Review and Save are available.";
      } else {
        view.error = state.draftView?.entry?.error || state.draftView?.error || "The assignment was not applied. Check the current draft before retrying.";
        view.message = "Assignment was not applied.";
      }
      return accepted;
    } catch (error) {
      if (!disposed && token === generation && scope() === proposal.scope) { view.error = error.message || String(error); view.message = "Assignment was not applied."; }
      return false;
    } finally {
      if (!disposed && token === generation && scope() === proposal.scope) { view.busy = false; sync(); publish(); }
    }
  }
  function cancel({silent = false} = {}) {
    if (view.busy) return false;
    const hadProposal = Boolean(view.proposal);
    view.proposal = null; view.error = ""; view.message = hadProposal ? "Assignment cancelled. The overlay is unchanged." : "";
    if (!silent) publish(); return true;
  }
  return {view, sync, open, setSignal, problem, apply, cancel, ready: () => Boolean(identity()),
    dispose() { disposed = true; generation++; }};
}

export function mountPinAssignment(root, options) {
  const document = root.ownerDocument; const lifetime = new AbortController();
  let disposed = false; let selectedPin; let data; let choices = []; let renderedProposal; let renderedChoices; let renderedNotes;
  const node = (tag, className, text) => { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; };
  const listen = (element, name, fn) => element.addEventListener(name, fn, {signal: lifetime.signal});
  const heading = node("h4", "", "Assign a function"); const chooseLabel = node("label", "", "Function");
  const choose = node("select"); choose.setAttribute("aria-label", "Pin function"); chooseLabel.append(choose);
  const availability = node("p", "dte-muted");
  const unavailableDetails = node("details", "dte-chip-unavailable");
  const unavailableSummary = node("summary"); const unavailableReasons = node("div");
  unavailableDetails.append(unavailableSummary, unavailableReasons);
  const form = node("form", "dte-chip-route"); form.setAttribute("aria-label", "Pin assignment");
  const title = node("h4"); const detail = node("p", "dte-muted"); const fields = node("div", "dte-chip-route-fields");
  const notes = node("div"); const validation = node("p", "dte-muted");
  const apply = node("button", "dte-primary", "Apply assignment"); apply.type = "button";
  const cancel = node("button", "", "Cancel assignment"); cancel.type = "button";
  const actions = node("div", "dte-node-actions"); actions.append(apply, cancel);
  const status = node("p", "dte-status"); status.setAttribute("role", "status");
  const error = node("p", "dte-error"); error.setAttribute("role", "alert");
  form.append(title, detail, fields, notes, validation);
  root.append(heading, chooseLabel, availability, unavailableDetails, form, actions, status, error);
  const session = createPinAssignmentSession({...options, onChange() { render(data, selectedPin); options.onChange?.(); }});
  let controls = [];
  const appendOption = (select, value, text, disabled = false) => { const option = node("option", "", text); option.value = value; option.disabled = disabled; select.append(option); };
  listen(choose, "change", () => { if (choose.value) session.open(selectedPin, choose.value); else session.cancel(); });
  listen(form, "submit", (event) => { event.preventDefault(); void session.apply(); });
  listen(apply, "click", () => { void session.apply(); });
  listen(cancel, "click", () => { if (session.cancel()) choose.focus({preventScroll: true}); });
  listen(root, "keydown", (event) => {
    if (event.key === "Escape" && session.view.proposal && !session.view.busy) {
      event.preventDefault(); event.stopPropagation?.(); session.cancel(); choose.focus({preventScroll: true});
    }
  });
  function render(nextData, pin) {
    if (disposed) return;
    data = nextData; selectedPin = pin; session.sync();
    if (session.view.proposal && session.view.proposal.pin !== pin?.id && !session.view.busy) session.cancel({silent: true});
    const view = session.view; const proposal = view.proposal;
    choices = activationChoices(data, pin);
    const choicesKey = JSON.stringify([pin?.id, choices]);
    if (choicesKey !== renderedChoices) {
      renderedChoices = choicesKey; choose.replaceChildren(); appendOption(choose, "", "Choose a function");
      for (const choice of choices) appendOption(choose, choice.id, `${choice.label}${choice.available ? "" : " — unavailable"}`, !choice.available);
      const unavailable = choices.filter((choice) => !choice.available);
      unavailableDetails.hidden = !unavailable.length; unavailableSummary.textContent = `Unavailable functions (${unavailable.length})`;
      unavailableReasons.replaceChildren();
      for (const choice of unavailable) unavailableReasons.append(node("p", "dte-muted", `${choice.label}: ${choice.reason}`));
    }
    choose.value = proposal && proposal.pin === pin?.id ? proposal.choice : "";
    choose.disabled = view.busy || !session.ready() || !choices.some((choice) => choice.available);
    const unavailable = choices.filter((choice) => !choice.available);
    availability.textContent = !data?.activation?.available ? data?.activation?.reason || "Pin activation is unavailable for this observation."
      : !session.ready() && !view.busy ? "Wait for the current overlay and pin observation before assigning a function."
        : !choices.length ? "No supported GPIO, UART, I2C or SPI assignment is available on this pin."
          : unavailable.length === choices.length ? unavailable[0]?.reason || "No supported assignment is available on this pin." : "";
    availability.hidden = !availability.textContent;
    form.hidden = !proposal; form.setAttribute("aria-busy", String(view.busy));
    if (proposal !== renderedProposal) {
      renderedProposal = proposal; fields.replaceChildren(); controls = [];
      if (proposal) {
        title.textContent = proposal.kind === "gpio" ? `Application GPIO · ${pin?.name || proposal.pin}` : `${proposal.controller.label || proposal.controller.id} · ${proposal.controller.kind.toUpperCase()}`;
        detail.textContent = proposal.kind === "gpio"
          ? "Creates an application GPIO reference in the overlay. Application code controls its direction and value."
          : "Complete the signal set below. Apply enables this controller and updates its pin route as one undoable overlay change.";
        for (const signal of array(proposal.controller?.signals)) {
          const fieldLabel = node("label", "", signal.label || signal.id.toUpperCase()); const select = node("select");
          select.setAttribute("aria-label", signal.label || signal.id.toUpperCase());
          appendOption(select, "", signal.required === false ? "Not used" : "Choose a pin");
          for (const candidate of array(signal.candidates)) {
            const name = array(data?.pins).find((item) => item.id === candidate)?.name || candidate;
            appendOption(select, candidate, `${name}${signal.unavailable?.[candidate] ? " — " + signal.unavailable[candidate] : ""}`, Boolean(signal.unavailable?.[candidate]));
          }
          select.value = proposal.signals[signal.id];
          listen(select, "change", () => session.setSignal(signal.id, select.value));
          fieldLabel.append(select); fields.append(fieldLabel); controls.push({id: signal.id, select});
        }
      }
    }
    const recommendations = new Map();
    const addNote = (note, pinId) => {
      const text = typeof note === "string" ? note : note?.message || note?.reason || "";
      if (!text) return;
      if (!recommendations.has(text)) recommendations.set(text, new Set());
      if (pinId) recommendations.get(text).add(pinId);
    };
    if (proposal) {
      for (const note of array(proposal.kind === "gpio" ? data?.activation?.gpio?.notes : proposal.controller?.notes)) addNote(note);
      const chosenPins = proposal.kind === "gpio" ? [proposal.pin] : Object.values(proposal.signals).filter(Boolean);
      for (const pinId of chosenPins) for (const note of array(data?.activation?.pin_notes?.[pinId])) addNote(note, pinId);
      if (proposal.controller?.kind === "spi") addNote("Existing chip-select settings are preserved. This assignment does not create an external device.");
      addNote("Save publishes the overlay. Driver configuration and firmware execution remain separate.");
    }
    const noteTexts = [...recommendations].map(([text, pins]) => pins.size ? `${[...pins].join(", ")}: ${text}` : text);
    const notesKey = JSON.stringify(noteTexts);
    if (notesKey !== renderedNotes) {
      renderedNotes = notesKey; notes.replaceChildren();
      for (const text of noteTexts) notes.append(node("p", "dte-muted", text));
    }
    for (const {id, select} of controls) { select.value = proposal.signals[id]; select.disabled = view.busy; }
    validation.textContent = session.problem(); validation.hidden = !validation.textContent;
    actions.hidden = !proposal && !view.message && !view.error;
    // Keep the action focusable across submission/completion; session.apply guards every click.
    apply.setAttribute("aria-disabled", String(!proposal || view.busy || !session.ready() || Boolean(validation.textContent)));
    apply.textContent = view.busy ? "Applying…" : !proposal && view.message.startsWith("Assignment applied") ? "Applied to draft" : "Apply assignment";
    cancel.disabled = view.busy; cancel.textContent = proposal ? "Cancel assignment" : "Close assignment";
    status.textContent = view.message; status.hidden = !view.message; error.textContent = view.error; error.hidden = !view.error;
    root.hidden = !pin || !data?.activation;
  }
  return {render, session, dispose() { disposed = true; lifetime.abort(); session.dispose(); }};
}
