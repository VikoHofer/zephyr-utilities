/** Restore an explicit action's keyboard position after a same-view form refresh. */
export async function retainActionFocus(root, operation, current, signal, {returnTo} = {}) {
  const document = root.ownerDocument; const startingActive = document.activeElement;
  const intended = returnTo || startingActive;
  const contains = (element) => {
    for (let item = element; item; item = item.parentNode) if (item === root) return true;
    return false;
  };
  const formName = (element) => element.closest?.("form")?.children[0]?.textContent || "";
  const key = intended?.dataset?.focusKey;
  if ((!key && !returnTo) || !contains(intended)) return operation();
  const name = formName(intended);
  const candidates = () => [...root.querySelectorAll("input, select, textarea, button")]
    .filter((item) => key && item.dataset.focusKey === key && formName(item) === name);
  const index = candidates().indexOf(intended);
  const listeners = new AbortController(); let moved = false;
  signal?.addEventListener("abort", () => listeners.abort(), {once: true, signal: listeners.signal});
  document.addEventListener?.("focusin", (event) => {
    if (event.target !== startingActive && event.target !== document.body) moved = true;
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
      && (active === startingActive || !active || active === document.body) && !(active === intended && contains(intended))) {
      const replacement = contains(intended) ? intended : candidates()[index];
      if (replacement && !replacement.disabled && !replacement.closest?.("[hidden]")
          && replacement.isConnected !== false && replacement.getClientRects?.().length !== 0) replacement.focus?.({preventScroll: true});
    }
  }
}
