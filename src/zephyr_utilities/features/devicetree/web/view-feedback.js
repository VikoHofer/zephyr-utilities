export function announceResult(element, notify, {message, error = false} = {}) {
  if (!element || element.isConnected === false || element.hidden || element.closest?.("[hidden]")
      || element.getClientRects?.().length === 0) return;
  const bounds = element.getBoundingClientRect?.(); const viewport = element.ownerDocument?.defaultView;
  if (bounds && viewport && bounds.top >= 0 && bounds.bottom <= viewport.innerHeight
      && bounds.left >= 0 && bounds.right <= viewport.innerWidth) return;
  const text = String(message ?? element.textContent ?? "").trim();
  if (text) notify?.(text, error);
}
