import {announceResult} from "./view-feedback.js";
import {retainActionFocus} from "./view-focus.js";
import {peripheralLabel, peripheralName} from "./peripherals.js";
import {createPWMSession, pwmEntry, pwmFlags, pwmRows} from "./pwm.js";

const array = (value) => Array.isArray(value) ? value : [];

export function mountPWMPanel(root, {api, state, workspace, drafts, notify, onChange, onRemove}) {
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
  const permitted = (node, allowed) => { node.dataset.pwmAllowed = String(Boolean(allowed)); node.disabled = !allowed; };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load PWM consumers", () => run(() => session.load()));
  const cancel = button("Cancel PWM inspection", () => { generation++; session.cancel(); });
  heading.append(make("h3", "PWM consumers"), load, cancel);
  const status = make("p", "", "dte-muted"); status.setAttribute("role", "status");
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert");
  const recovery = make("div"); const reconcile = button("Reconcile PWM draft", () => run(() => session.reconcile()));
  recovery.append(reconcile); note(recovery, "Reconcile the canonical draft, then load PWM consumers again. The previous action is not repeated.");
  const diagnostics = make("div"); const controls = make("div", undefined, "dte-peripheral-controls dte-pwm-controls");
  const topology = make("ul", undefined, "dte-topology"); topology.setAttribute("aria-label", "Local PWM consumers and groups");
  const detail = make("div", undefined, "dte-peripheral-detail"); detail.tabIndex = -1;
  const groups = make("div"); controls.append(topology, detail, groups);
  root.append(heading);
  note(root, "Edit one literal PWM specifier or add PWM LEDs using local bindings. Provider channel numbering, shared timing limits and physical routing may remain unknown. Save and Configure are separate actions.");
  root.append(status, error, recovery, diagnostics, controls);
  const localFeedback = make("p"); localFeedback.setAttribute("role", "status");
  function locateFeedback(target) {
    feedbackHost = null; feedbackForm = null;
    for (let node = target; node && node !== root; node = node.parentNode) {
      if (node.tagName === "FORM") feedbackForm = node.children[0]?.textContent;
      if (node === detail || node === groups) { feedbackHost = node; break; }
    }
  }
  function renderFeedback(view) {
    if (!feedbackHost) { localFeedback.hidden = true; localFeedback.remove?.(); return; }
    const destination = [...feedbackHost.querySelectorAll("form")].find((form) => form.children[0]?.textContent === feedbackForm) || feedbackHost;
    localFeedback.textContent = view.error || view.message || ""; localFeedback.hidden = !localFeedback.textContent;
    localFeedback.className = view.error ? "dte-error" : "dte-muted";
    if (localFeedback.parentNode !== destination) destination.append(localFeedback);
  }
  const session = createPWMSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function announceFeedback() {
    if (disposed) return;
    announceResult(feedbackHost ? localFeedback : state.pwmView.error ? error : status, notify,
      {error: Boolean(state.pwmView.error)});
  }
  function run(operation) {
    const token = generation; const captured = workspace(); const context = state.data?.id;
    const draft = state.draftView?.entry?.server?.id; const localGeneration = state.draftView?.entry?.generation;
    const current = () => !disposed && token === generation && captured === workspace()
      && context === state.data?.id && draft === state.draftView?.entry?.server?.id
      && localGeneration === state.draftView?.entry?.generation;
    return retainActionFocus(root, async () => {
      const result = await operation();
      if (current()) announceFeedback();
      return result;
    }, current, lifetime.signal);
  }
  function submit(command, key = selectedKey) { selectedKey = key; return run(() => session.command(command)); }
  function form(parent, title, action) {
    const node = make("form", undefined, "dte-peripheral-form"); node.append(make("h4", title)); parent.append(node);
    listen(node, "submit", (event) => { event.preventDefault(); try { action(); } catch (error) { session.fail(error.message); announceFeedback(); } }); return node;
  }
  function apply(parent, title, allowed) {
    const node = button(title, null, true); node.type = "submit"; permitted(node, allowed); parent.append(node);
  }
  function entryFields(parent, current) {
    const providers = array(data.providers).filter((provider) => provider.supported !== false);
    const target = make("select"); option(target, "", "Choose a local PWM provider");
    for (const provider of providers) option(target, provider.reference, `${provider.reference} · ${provider.path || provider.label}`);
    const selected = providers.find((provider) => provider.reference === current?.reference || provider.path && current?.reference === `&{${provider.path}}`
      || array(provider.labels).some((name) => current?.reference === "&" + name));
    if (selected) target.value = selected.reference;
    const values = array(current?.cells); const channel = input(values[0] ?? ""); const period = input(values[1] ?? "");
    const flags = input(values[2] ?? ""); const polarity = make("select"); option(polarity, "", "Enter flags first");
    option(polarity, "0", "Normal"); option(polarity, "1", "Inverted");
    const flagFields = make("div", undefined, "dte-binding-cells"); flagFields.append(label("Flags (unsigned integer)", flags), label("Polarity", polarity));
    const providerNote = make("p", "", "dte-notice");
    parent.append(label("PWM provider", target), label("Channel", channel), label("Period (nanoseconds)", period), flagFields, providerNote);
    note(parent, "Use literal unsigned integers. Polarity changes only flag bit 0; all other numeric flag bits are retained. This form does not infer a channel range or configure pins.");
    const updatePolarity = () => {
      try { polarity.value = String(pwmFlags(flags.value, "0") === Number(flags.value) ? 0 : 1); }
      catch { polarity.value = ""; }
    };
    const updateProvider = () => {
      const provider = providers.find((item) => item.reference === target.value); const hasFlags = provider?.cells?.length === 3;
      flagFields.hidden = !hasFlags; permitted(flags, hasFlags); permitted(polarity, hasFlags);
      providerNote.textContent = provider ? `Local provider status: ${provider.status || "not specified"}. `
        + (Number.isInteger(provider.channel_count) ? `Declared channel count: ${provider.channel_count}. ` : "")
        + "Valid channel numbering, cross-channel period constraints and hardware timing remain unknown."
        : "Choose a supported unique local provider. Inherited or unsupported provider layouts require source editing.";
      updateDisabled();
    };
    listen(target, "change", updateProvider); listen(flags, "input", updatePolarity); listen(flags, "change", updatePolarity);
    listen(polarity, "change", () => {
      try { flags.value = String(pwmFlags(flags.value, polarity.value)); }
      catch (error) { locateFeedback(polarity); session.fail(error.message); announceFeedback(); }
    });
    updatePolarity(); updateProvider();
    return () => pwmEntry(providers.find((provider) => provider.reference === target.value), {channel: channel.value, period: period.value, flags: flags.value});
  }
  function editConsumer(consumer) {
    if (!consumer.editable) note(detail, consumer.reason || "This PWM consumer requires source editing.", "dte-notice");
    else {
      let read; const node = form(detail, "Edit PWM specifier", () => void submit({kind: "pwm_set", node_id: consumer.node_id, entry: read()}));
      read = entryFields(node, consumer.entry); apply(node, "Apply PWM to draft", true);
      note(node, "Only this consumer's pwms property changes. Other properties, shared providers and pin configuration are preserved.");
    }
    if (onRemove) {
      let reviewing = false;
      const remove = button("Review PWM consumer removal", async () => {
        if (reviewing) return;
        const token = generation; const captured = workspace(); const context = state.data?.id; const draft = state.draftView?.entry?.server?.id;
        reviewing = true; remove.textContent = "Preparing removal review…";
        try { await onRemove(consumer.node_id, remove); }
        catch (error) {
          if (!disposed && token === generation && captured === workspace() && context === state.data?.id
            && draft === state.draftView?.entry?.server?.id) { session.fail(error.message || String(error)); announceFeedback(); }
        } finally { reviewing = false; remove.textContent = "Review PWM consumer removal"; }
      });
      detail.append(remove); note(detail, "Review known dependencies before removing this source node. Shared providers are retained.");
    }
  }
  function addLED(group) {
    if (!group.addable) { note(detail, group.reason || "This PWM LED group requires source editing.", "dte-notice"); return; }
    let read; const node = form(detail, "Add PWM LED", () => {
      const value = peripheralName(name.value);
      void submit({kind: "pwm_led_add", parent_id: group.node_id, name: value, label: peripheralLabel(nodeLabel.value), entry: read()}, "consumer:" + group.path + "/" + value);
    });
    const name = input("led"); const nodeLabel = input(); node.append(label("LED node name", name), label("Node label (optional)", nodeLabel));
    read = entryFields(node); apply(node, "Add PWM LED to draft", true);
    note(node, "Creates one leaf LED with one explicit PWM specifier. No provider, pinctrl or software configuration is added.");
  }
  function renderSelection() {
    for (const item of topology.querySelectorAll("button")) item.setAttribute("aria-pressed", String(item.dataset.pwmKey === selectedKey));
    detail.replaceChildren(); const selected = pwmRows(data).find((row) => row.key === selectedKey);
    if (!selected) note(detail, "Choose a PWM consumer to edit, or a PWM LED group to add a child.");
    else { detail.append(make("h4", selected.node.path || selected.node.name)); if (selected.kind === "group") addLED(selected.node); else editConsumer(selected.node); }
    updateDisabled();
  }
  function renderTopology() {
    topology.replaceChildren();
    for (const row of pwmRows(data)) {
      const item = make("li"); const select = button(`${row.kind === "group" ? "PWM LEDs" : "PWM"} · ${row.node.path || row.node.name}`, () => {
        if (selectedKey === row.key) return;
        selectedKey = row.key; renderSelection();
        announceResult(detail, notify, {message: `Details for ${row.node.path || row.node.name} are ready below.`});
      }); select.dataset.pwmKey = row.key; item.append(select); topology.append(item);
    }
    if (!topology.children.length) { const item = make("li"); note(item, "No local PWM consumers or PWM LED groups were identified. Add a supported group or use source editing."); topology.append(item); }
    renderSelection();
  }
  function renderGroups() {
    groups.replaceChildren(); const available = data.group_option?.available;
    const node = form(groups, "Add a PWM LED group", () => {
      if (!available) throw new Error(data.group_option?.reason || "A resolved PWM LED binding and unique local root are required.");
      const value = peripheralName(name.value); void submit({kind: "pwm_group_add", name: value}, "group:/" + value);
    });
    const name = input(data.group_option?.name || "pwm_leds"); node.append(label("PWM group node name", name));
    apply(node, "Add PWM group to draft", available); note(node, data.group_option?.reason, "dte-notice");
    note(node, "Creates only the local pwm-leds group. Add its LED consumers separately.");
  }
  function updateDisabled() {
    const blocked = state.pwmView.busy || !session.ready() || !state.pwmView.data;
    for (const node of controls.querySelectorAll("input, select, textarea, button")) node.disabled = blocked || node.dataset.pwmAllowed === "false";
    load.disabled = state.pwmView.busy || !session.ready(); load.textContent = state.pwmView.busy ? "Loading or updating PWM…" : "Load PWM consumers";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile(); reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile PWM draft";
  }
  function render() {
    if (disposed) return;
    session.sync(); const view = state.pwmView;
    status.textContent = view.message || (view.busy ? "Inspecting current PWM consumers…" : "Load PWM consumers to inspect this source revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error; controls.hidden = !view.presentation;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnostics.replaceChildren();
      if (data) {
        for (const item of array(data.diagnostics)) note(diagnostics, item.message || item, "dte-notice");
        for (const provider of array(data.providers).filter((item) => item.supported === false)) note(diagnostics, `${provider.reference}: ${provider.reason || "Unsupported PWM cell layout; use source editing."}`, "dte-notice");
        renderTopology(); renderGroups();
      }
    }
    renderFeedback(view); updateDisabled();
  }
  render();
  return {render, navigate() { generation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; generation++; lifetime.abort(); session.dispose(); }};
}
