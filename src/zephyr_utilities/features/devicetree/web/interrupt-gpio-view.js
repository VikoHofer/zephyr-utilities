import {retainActionFocus} from "./view-focus.js";
import {createInterruptGPIOSession, interruptGPIOEntry, interruptGPIOFlags, interruptGPIORows} from "./interrupt-gpio.js";

const array = (value) => Array.isArray(value) ? value : [];

export function mountInterruptGPIOPanel(root, {api, state, workspace, drafts, reveal, onChange, onRemove}) {
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
  const permitted = (node, allowed) => { node.dataset.interruptGPIOAllowed = String(Boolean(allowed)); node.disabled = !allowed; };
  const heading = make("div", undefined, "dte-section-heading");
  const load = button("Load interrupt GPIOs", () => run(() => session.load()));
  const cancel = button("Cancel interrupt GPIO inspection", () => { generation++; session.cancel(); });
  heading.append(make("h3", "Interrupt GPIOs"), load, cancel);
  const status = make("p", "", "dte-muted"); status.setAttribute("role", "status");
  const error = make("p", "", "dte-error"); error.setAttribute("role", "alert");
  const recovery = make("div"); const reconcile = button("Reconcile interrupt GPIO draft", () => run(() => session.reconcile()));
  recovery.append(reconcile); note(recovery, "Reconcile the canonical draft, then load interrupt GPIOs again. The previous action is not repeated.");
  const diagnostics = make("div"); const controls = make("div", undefined, "dte-peripheral-controls dte-interrupt-gpio-controls");
  const topology = make("ul", undefined, "dte-topology"); topology.setAttribute("aria-label", "Local interrupt GPIO consumer properties");
  const detail = make("div", undefined, "dte-peripheral-detail"); detail.tabIndex = -1;
  controls.append(topology, detail);
  root.append(heading);
  note(root, "Edit one literal GPIO specifier for a supported local interrupt signal. GPIO polarity is its logical active level, not an interrupt edge or level selection. Driver and Kconfig trigger behavior, interrupt enablement and physical routing remain unknown. Save and Configure are separate actions.");
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
  function revealFeedback() {
    if (disposed || !feedbackHost || localFeedback.hidden || !localFeedback.parentNode) return;
    const bounds = localFeedback.getBoundingClientRect?.(); const viewport = document.defaultView;
    if (bounds && viewport && bounds.top >= 0 && bounds.bottom <= viewport.innerHeight
        && bounds.left >= 0 && bounds.right <= viewport.innerWidth) return;
    reveal?.(localFeedback, {focus: false, block: "nearest"});
  }
  const session = createInterruptGPIOSession({api, state, workspace, drafts, onChange() { render(); onChange?.(); }});
  function run(operation) {
    const token = generation; const captured = workspace(); const context = state.data?.id; const draft = state.draftView?.entry?.server?.id;
    const localGeneration = state.draftView?.entry?.generation;
    const current = () => !disposed && token === generation && captured === workspace()
      && context === state.data?.id && draft === state.draftView?.entry?.server?.id
      && localGeneration === state.draftView?.entry?.generation;
    return retainActionFocus(root, async () => {
      const result = await operation();
      if (current()) revealFeedback();
      return result;
    }, current, lifetime.signal);
  }
  function submit(command) { return run(() => session.command(command)); }
  function form(parent, title, action) {
    const node = make("form", undefined, "dte-peripheral-form"); node.append(make("h4", title)); parent.append(node);
    listen(node, "submit", (event) => {
      event.preventDefault();
      try { action(); } catch (error) { session.fail(error.message); revealFeedback(); }
    }); return node;
  }
  function apply(parent, title, allowed) {
    const node = button(title, null, true); node.type = "submit"; permitted(node, allowed); parent.append(node);
  }
  function entryFields(parent, current) {
    const providers = array(data.providers).filter((provider) => provider.supported === true);
    const target = make("select"); option(target, "", "Choose a local GPIO provider");
    for (const provider of providers) option(target, provider.reference, `${provider.reference} · ${provider.path || provider.label}`);
    const selected = providers.find((provider) => provider.reference === current?.reference || provider.path && current?.reference === `&{${provider.path}}`
      || array(provider.labels).some((name) => current?.reference === "&" + name));
    if (selected) target.value = selected.reference;
    const values = array(current?.cells); const pin = input(values[0] ?? ""); const flags = input(values[1] ?? "");
    const polarity = make("select"); option(polarity, "", "Enter flags first"); option(polarity, "0", "Active high"); option(polarity, "1", "Active low");
    const providerNote = make("p", "", "dte-notice");
    parent.append(label("GPIO provider", target), label("Pin (0–31)", pin), label("Flags (0–65535)", flags), label("Logical active level", polarity), providerNote);
    note(parent, "Changing the logical active level changes only flag bit 0 and preserves other numeric bits. It does not select interrupt edge/level or enable a driver trigger. Enter all GPIO flags explicitly.");
    const updateProvider = () => {
      const provider = providers.find((item) => item.reference === target.value);
      if (!provider) {
        providerNote.textContent = "Choose a supported unique local provider. Inherited or unsupported provider declarations require source editing.";
        return;
      }
      const count = Number.isInteger(provider.pin_count) ? `Declared pin count: ${provider.pin_count}. ` : "The local pin count is unknown. ";
      const reserved = Array.isArray(provider.reserved_ranges)
        ? provider.reserved_ranges.length ? "Locally reserved pins: " + provider.reserved_ranges.map(([start, size]) => size === 1 ? String(start) : `${start}–${start + size - 1}`).join(", ") + ". " : "No reserved pins are declared locally. "
        : "Local reserved pin ranges are unknown. ";
      providerNote.textContent = `Local provider status: ${provider.status || "unknown"}. ` + count + reserved
        + "Physical routing, electrical suitability and other users outside this overlay remain unknown.";
    };
    const updatePolarity = () => {
      try { polarity.value = String(interruptGPIOFlags(flags.value, "0") === Number(flags.value) ? 0 : 1); permitted(polarity, true); }
      catch { polarity.value = ""; permitted(polarity, false); }
      updateDisabled();
    };
    listen(target, "change", updateProvider); listen(flags, "input", updatePolarity); listen(flags, "change", updatePolarity);
    listen(polarity, "change", () => {
      try { flags.value = String(interruptGPIOFlags(flags.value, polarity.value)); }
      catch (error) { locateFeedback(polarity); session.fail(error.message); revealFeedback(); }
    });
    updateProvider(); updatePolarity();
    return () => interruptGPIOEntry(providers.find((provider) => provider.reference === target.value), {pin: pin.value, flags: flags.value});
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
          && draft === state.draftView?.entry?.server?.id) { session.fail(error.message || String(error)); revealFeedback(); }
      } finally { reviewing = false; remove.textContent = title; }
    });
    detail.append(remove); note(detail, "This review covers the whole consumer node, including its other signals. Shared providers are retained.");
  }
  function editConsumer(consumer) {
    note(detail, `${consumer.property} · ${consumer.signal || "Interrupt signal"}`);
    note(detail, consumer.description);
    if (!consumer.editable) note(detail, consumer.reason || "This interrupt GPIO property requires source editing.", "dte-notice");
    else {
      let read; const node = form(detail, "Edit interrupt GPIO specifier", () => void submit({kind: "interrupt_gpio_set", node_id: consumer.node_id, property: consumer.property, entry: read()}));
      read = entryFields(node, consumer.entry); apply(node, "Apply interrupt GPIO to draft", true);
      note(node, `Only this consumer's ${consumer.property} property changes. Other properties, shared providers and software configuration are preserved.`);
    }
    removal(consumer, "Review consumer node removal");
  }
  function renderSelection() {
    for (const item of topology.querySelectorAll("button")) item.setAttribute("aria-pressed", String(item.dataset.interruptGPIOKey === selectedKey));
    detail.replaceChildren(); const selected = interruptGPIORows(data).find((row) => row.key === selectedKey);
    if (!selected) note(detail, "Choose an interrupt GPIO property to edit its provider, pin and literal flags.");
    else { detail.append(make("h4", selected.node.path || selected.node.name)); editConsumer(selected.node); }
    updateDisabled();
  }
  function renderTopology() {
    topology.replaceChildren();
    for (const row of interruptGPIORows(data)) {
      const item = make("li"); const select = button(`${row.node.path || row.node.name} · ${row.node.property}`, () => {
        if (selectedKey === row.key) return;
        selectedKey = row.key; renderSelection(); reveal?.(detail);
      }); select.dataset.interruptGPIOKey = row.key; item.append(select); topology.append(item);
    }
    if (!topology.children.length) {
      const item = make("li"); note(item, "No supported local interrupt GPIO consumers were identified. Use binding or source editing for other signals."); topology.append(item);
    }
    renderSelection();
  }
  function updateDisabled() {
    const blocked = state.interruptGPIOView.busy || !session.ready() || !state.interruptGPIOView.data;
    for (const node of controls.querySelectorAll("input, select, textarea, button")) node.disabled = blocked || node.dataset.interruptGPIOAllowed === "false";
    load.disabled = state.interruptGPIOView.busy || !session.ready(); load.textContent = state.interruptGPIOView.busy ? "Loading or updating interrupt GPIO…" : "Load interrupt GPIOs";
    cancel.hidden = !session.scanning(); recovery.hidden = !session.needsReconcile() && !session.reconciling();
    reconcile.disabled = !session.canReconcile(); reconcile.textContent = session.reconciling() ? "Reconciling draft…" : "Reconcile interrupt GPIO draft";
  }
  function render() {
    if (disposed) return;
    session.sync(); const view = state.interruptGPIOView;
    status.textContent = view.message || (view.busy ? "Inspecting current interrupt GPIOs…" : "Load interrupt GPIOs to inspect this source revision. No project tools run.");
    error.hidden = !view.error; error.textContent = view.error; controls.hidden = !view.presentation;
    if (rendered !== view.presentation) {
      rendered = data = view.presentation; diagnostics.replaceChildren();
      if (data) {
        for (const item of array(data.diagnostics)) note(diagnostics, item.message || item, "dte-notice");
        for (const provider of array(data.providers).filter((item) => item.supported === false)) note(diagnostics, `${provider.reference}: ${provider.reason || "Unsupported interrupt GPIO cell layout; use source editing."}`, "dte-notice");
        renderTopology();
      }
    }
    renderFeedback(view); updateDisabled();
  }
  render();
  return {render, navigate() { generation++; feedbackHost = null; session.clear(); render(); },
    dispose() { disposed = true; generation++; lifetime.abort(); session.dispose(); }};
}
