/** Restore an explicit action's keyboard position after a same-view form refresh. */
export async function retainActionFocus(root, operation, current, signal) {
  const document = root.ownerDocument; const original = document.activeElement;
  const contains = (element) => {
    for (let item = element; item; item = item.parentNode) if (item === root) return true;
    return false;
  };
  const formName = (element) => element.closest?.("form")?.children[0]?.textContent || "";
  const key = original?.dataset?.focusKey;
  if (!key || !contains(original)) return operation();
  const name = formName(original);
  const candidates = () => [...root.querySelectorAll("input, select, textarea, button")]
    .filter((item) => item.dataset.focusKey === key && formName(item) === name);
  const index = candidates().indexOf(original);
  const listeners = new AbortController(); let moved = false;
  signal?.addEventListener("abort", () => listeners.abort(), {once: true, signal: listeners.signal});
  document.addEventListener?.("focusin", (event) => {
    if (event.target !== original && event.target !== document.body) moved = true;
  }, {signal: listeners.signal});
  document.addEventListener?.("pointerdown", () => { moved = true; }, {signal: listeners.signal});
  try {
    return await operation();
  } finally {
    listeners.abort();
    const active = document.activeElement;
    // Do not move focus after navigation, another user action, or a background
    // update. A removed focused node normally leaves focus on the document body.
    if (!signal?.aborted && current() && !moved && (!document.hasFocus || document.hasFocus())
      && (active === original || !active || active === document.body) && !(active === original && contains(original))) {
      const replacement = candidates()[index];
      if (replacement && !replacement.disabled && !replacement.closest?.("[hidden]")) replacement.focus?.({preventScroll: true});
    }
  }
}
