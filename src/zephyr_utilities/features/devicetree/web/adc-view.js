import {announceResult} from "./view-feedback.js";
import {retainActionFocus} from "./view-focus.js";
import {createADCSession, adcChannel, adcConfig, adcEntry, adcRows} from "./adc.js";

const array = (value) => Array.isArray(value) ? value : [];

export function mountADCPanel(root, {api, state, workspace, drafts, notify, onChange, onRemove}) {
  const document = root.ownerDocument; const lifetime = new AbortController(); const handlers = new WeakMap();
  let disposed = false; let generation = 0; let rendered; let data; let selectedKey = ""; let feedbackHost; let feedbackForm;
  const make = (tag, text, className = "") => {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className; return node;
  };
  const listen = (node, event, callback) => { if (!handlers.has(node)) handlers.set(node, {}); handlers.get(node)[event] = callback; };
  for (const type of ["click", "change", "input", "submit"]) root.addEventListener(type, (event) => {
    if (type === "submit" || type === "click" && event.target.closest("button")) locateFeedback(event.target);
    for (let node = event.target; node && node !== root; node = node.parentNode) {
      handlers.get(node)?.[type]?.(event); if (event.cancelBubble) break;
    }
  }, {signal: lifetime.signal});
  const button = (title, action, primary = false) => {
    const node = make("button", title, primary ? "dte-primary" : ""); node.type = "button"; node.dataset.focusKey = "button:" + title;
    if (action) listen(node, "click", action); return node;
  };
  const label = (title, control) => { control.dataset.focusKey = "field:" + title; const node = make("label", title); node.append(control); return node; };
  const input = (value = "") => { const node = make("input"); node.type = "text"; node.value = String(value); node.spellcheck = false; node.autocomplete = "off"; return node; };
  const option = (select, value, title) => { const node = make("option", title); node.value = value; select.append(node); };
  const note = (parent, text, className = "dte-muted") => { if (text) parent.append(make("p", text, className)); };
  const permitted = (node, allowed) => { node.dataset.adcAllowed = String(Boolean(allowed)); node.disabled = !allowed; };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load ADC channels", () => run(() => session.load()));
  const cancel = button("Cancel ADC inspection", () => { generation++; session.cancel(); });
  heading.append(make("h3", "ADC channels"), load, cancel);
  const status = make("p", "", "dte-muted"); status.setAttribute("role", "status");
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert");
  const recovery = make("div"); const reconcile = button("Reconcile ADC draft", () => run(() => session.reconcile()));
  recovery.append(reconcile); note(recovery, "Reconcile the canonical draft, then load ADC channels again. The previous action is not repeated.");
  const diagnostics = make("div"); const controls = make("div", undefined, "dte-peripheral-controls dte-adc-controls");
  const topology = make("ul", undefined, "dte-topology"); topology.setAttribute("aria-label", "Local ADC providers, channels and consumers");
  const detail = make("div", undefined, "dte-peripheral-detail"); detail.tabIndex = -1;
  controls.append(topology, detail);
  root.append(heading);
  note(root, "Edit one literal io-channels specifier or add and configure local ADC channels. The ADC API uses channel IDs 0–31; narrower hardware limits, physical inputs and electrical suitability remain unknown. Save and Configure are separate actions.");
  root.append(status, error, recovery, diagnostics, controls);
  const localFeedback = make("p"); localFeedback.setAttribute("role", "status");
  function locateFeedback(target) {
    feedbackHost = null; feedbackForm = null;
    for (let node = target; node && node !== root; node = node.parentNode) {
      if (node.tagName === "FORM") feedbackForm = node.children[0]?.textContent;
      if (node === detail) { feedbackHost = node; break; }
    }
  }
  function renderFeedback(view) {
    if (!feedbackHost) { localFeedback.hidden = true; localFeedback.remove?.(); return; }
    const destination = [...feedbackHost.querySelectorAll("form")].find((form) => form.children[0]?.textContent === feedbackForm) || feedbackHost;
    localFeedback.textContent = view.error || view.message || ""; localFeedback.hidden = !localFeedback.textContent;
    localFeedback.className = view.error ? "dte-error" : "dte-muted";
    if (localFeedback.parentNode !== destination) destination.append(localFeedback);
  }
  function announceFeedback() {
    if (disposed) return;
    announceResult(feedbackHost ? localFeedback : state.adcView.error ? error : status, notify,
      {error: Boolean(state.adcView.error)});
  }
  const session = createADCSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function run(operation, {focusResult = false} = {}) {
    const token = generation; const captured = workspace(); const context = state.data?.id; const draft = state.draftView?.entry?.server?.id;
    const localGeneration = state.draftView?.entry?.generation;
    const current = () => !disposed && token === generation && captured === workspace()
      && context === state.data?.id && draft === state.draftView?.entry?.server?.id
      && localGeneration === state.draftView?.entry?.generation;
    const originalFocus = document.activeElement; const focusLifetime = new AbortController(); let focusMoved = false;
    if (focusResult) {
      document.addEventListener?.("focusin", (event) => {
        if (event.target !== originalFocus && event.target !== document.body) focusMoved = true;
      }, {signal: focusLifetime.signal});
      document.addEventListener?.("pointerdown", () => { focusMoved = true; }, {signal: focusLifetime.signal});
      lifetime.signal.addEventListener("abort", () => focusLifetime.abort(), {once: true, signal: focusLifetime.signal});
    }
    return retainActionFocus(root, async () => {
      try {
        const result = await operation();
        if (current()) {
          const active = document.activeElement;
          if (result && focusResult && !focusMoved && (!document.hasFocus || document.hasFocus())
              && (!active || active === document.body || active === originalFocus)) detail.focus?.({preventScroll: true});
          announceFeedback();
        }
        return result;
      } finally { focusLifetime.abort(); }
    }, current, lifetime.signal);
  }
  function submit(command, key = selectedKey) {
    const focusResult = key !== selectedKey; selectedKey = key;
    return run(() => session.command(command), {focusResult});
  }
  function form(parent, title, action) {
    const node = make("form", undefined, "dte-peripheral-form"); node.append(make("h4", title)); parent.append(node);
    listen(node, "submit", (event) => {
      event.preventDefault();
      try { action(); } catch (error) { session.fail(error.message); announceFeedback(); }
    }); return node;
  }
  function apply(parent, title, allowed) {
    const node = button(title, null, true); node.type = "submit"; permitted(node, allowed); parent.append(node);
  }
  function entryFields(parent, current) {
    const providers = array(data.providers).filter((provider) => provider.supported === true);
    const target = make("select"); option(target, "", "Choose a local ADC provider");
    for (const provider of providers) option(target, provider.reference, `${provider.reference} · ${provider.path || provider.label}`);
    const selected = providers.find((provider) => provider.reference === current?.reference || provider.path && current?.reference === `&{${provider.path}}`
      || array(provider.labels).some((name) => current?.reference === "&" + name));
    if (selected) target.value = selected.reference;
    const channel = input(array(current?.cells)[0] ?? ""); const providerNote = make("p", "", "dte-notice");
    parent.append(label("ADC provider", target), label("Channel ID (0–31)", channel), providerNote);
    const updateProvider = () => {
      const provider = providers.find((item) => item.reference === target.value);
      providerNote.textContent = provider ? `Local provider status: ${provider.status || "unknown"}. Narrower channel limits, physical inputs, pin routing and electrical suitability remain unknown.`
        : "Choose a supported unique local provider. Inherited or unsupported provider layouts require source editing.";
    };
    listen(target, "change", updateProvider); updateProvider();
    return () => adcEntry(providers.find((provider) => provider.reference === target.value), channel.value);
  }
  function configFields(parent, fields, current = {}) {
    const gain = make("select"); const reference = make("select");
    for (const [name, control] of [["gain", gain], ["reference", reference]]) {
      option(control, "", `Choose ${name}`);
      for (const value of array(fields?.[name]?.enum)) option(control, value, value);
      if (array(fields?.[name]?.enum).includes(current[name])) control.value = current[name];
    }
    const acquisition = input(current.acquisition_time ?? "");
    parent.append(label("Gain", gain), label("Reference", reference), label("Encoded acquisition time (0–65535)", acquisition));
    note(parent, "Enter the literal encoded acquisition time; 0 requests the driver's default. Supported acquisition times and gain/reference combinations depend on the driver and hardware. This form does not evaluate macros.");
    return () => adcConfig(fields, {gain: gain.value, reference: reference.value, acquisition_time: acquisition.value});
  }
  function removal(row, title) {
    if (!onRemove) return;
    let reviewing = false;
    const remove = button(title, async () => {
      if (reviewing) return;
      const token = generation; const captured = workspace(); const context = state.data?.id; const draft = state.draftView?.entry?.server?.id;
      reviewing = true; remove.textContent = "Preparing removal review…";
      try { await onRemove(row.node_id, remove); }
      catch (error) {
        if (!disposed && token === generation && captured === workspace() && context === state.data?.id
          && draft === state.draftView?.entry?.server?.id) { session.fail(error.message || String(error)); announceFeedback(); }
      } finally { reviewing = false; remove.textContent = title; }
    });
    detail.append(remove); note(detail, "Review known dependencies before removing this source node. Shared providers are retained.");
  }
  function editConsumer(consumer) {
    if (!consumer.editable) note(detail, consumer.reason || "This ADC consumer requires source editing.", "dte-notice");
    else {
      let read; const node = form(detail, "Edit ADC specifier", () => void submit({kind: "adc_set", node_id: consumer.node_id, entry: read()}));
      read = entryFields(node, consumer.entry); apply(node, "Apply ADC to draft", true);
      note(node, "Only this consumer's io-channels property changes. Existing names, other properties and shared providers are preserved. Channel configuration remains a separate action.");
    }
    removal(consumer, "Review ADC consumer removal");
  }
  function editChannel(channel) {
    note(detail, `Channel ID: ${channel.channel ?? "unknown"}. Channel IDs remain unchanged to preserve existing consumers.`);
    if (!channel.editable) note(detail, channel.reason || "This ADC channel requires source editing.", "dte-notice");
    else {
      let read; const node = form(detail, "Edit ADC channel configuration", () => void submit({kind: "adc_channel_set", node_id: channel.node_id, ...read()}));
      read = configFields(node, channel.fields, channel); apply(node, "Apply ADC channel to draft", true);
      note(node, "Updates gain, reference and acquisition time for every consumer using this provider and channel. Consumers outside this overlay may be unknown. Optional settings remain in source; use binding or source editing for those fields.");
    }
    removal(channel, "Review ADC channel removal");
  }
  function addChannel(provider) {
    note(detail, `Local provider status: ${provider.status || "unknown"}. Channel IDs identify ADC channels, not physical pins.`);
    if (!provider.addable) { note(detail, provider.add_reason || provider.reason || "This provider requires source editing.", "dte-notice"); return; }
    let read; const node = form(detail, "Add ADC channel", () => {
      const value = adcChannel(channel.value);
      void submit({kind: "adc_channel_add", parent_id: provider.id, channel: value, ...read()}, "channel:" + provider.path + "/channel@" + value.toString(16));
    });
    const channel = input(); node.append(label("Channel ID (0–31)", channel));
    read = configFields(node, provider.channel_fields); apply(node, "Add ADC channel to draft", true);
    note(node, "Creates one channel child with explicit required configuration. Consumers, providers, physical inputs, pinctrl and software settings are separate.");
  }
  function renderSelection() {
    for (const item of topology.querySelectorAll("button")) item.setAttribute("aria-pressed", String(item.dataset.adcKey === selectedKey));
    detail.replaceChildren(); const selected = adcRows(data).find((row) => row.key === selectedKey);
    if (!selected) note(detail, "Choose an ADC provider to add a channel, a channel to configure, or a consumer to edit.");
    else {
      detail.append(make("h4", selected.node.path || selected.node.name || selected.node.reference));
      if (selected.kind === "provider") addChannel(selected.node);
      else if (selected.kind === "channel") editChannel(selected.node);
      else editConsumer(selected.node);
    }
    updateDisabled();
  }
  function renderTopology() {
    topology.replaceChildren();
    for (const row of adcRows(data)) {
      const title = {provider: "ADC provider", channel: "ADC channel", consumer: "ADC consumer"}[row.kind];
      const item = make("li"); const select = button(`${title} · ${row.node.path || row.node.name || row.node.reference}`, () => {
        if (selectedKey === row.key) return;
        selectedKey = row.key; renderSelection();
        announceResult(detail, notify, {message: `Details for ${row.node.path || row.node.name || row.node.reference} are ready below.`});
      }); select.dataset.adcKey = row.key; item.append(select); topology.append(item);
    }
    if (!topology.children.length) { const item = make("li"); note(item, "No local ADC providers, channels or consumers were identified. Use source editing to add a supported local declaration."); topology.append(item); }
    renderSelection();
  }
  function updateDisabled() {
    const blocked = state.adcView.busy || !session.ready() || !state.adcView.data;
    for (const node of controls.querySelectorAll("input, select, textarea, button")) node.disabled = blocked || node.dataset.adcAllowed === "false";
    load.disabled = state.adcView.busy || !session.ready(); load.textContent = state.adcView.busy ? "Loading or updating ADC…" : "Load ADC channels";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile(); reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile ADC draft";
  }
  function render() {
    if (disposed) return;
    session.sync(); const view = state.adcView;
    status.textContent = view.message || (view.busy ? "Inspecting current ADC channels…" : "Load ADC channels to inspect this source revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error; controls.hidden = !view.presentation;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnostics.replaceChildren();
      if (data) {
        for (const item of array(data.diagnostics)) note(diagnostics, item.message || item, "dte-notice");
        for (const provider of array(data.providers).filter((item) => item.supported === false)) note(diagnostics, `${provider.reference}: ${provider.reason || "Unsupported ADC cell layout; use source editing."}`, "dte-notice");
        renderTopology();
      }
    }
    renderFeedback(view); updateDisabled();
  }
  render();
  return {render, navigate() { generation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; generation++; lifetime.abort(); session.dispose(); }};
}
