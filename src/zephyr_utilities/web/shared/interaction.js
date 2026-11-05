/** Reveal a requested result once. Passive refreshes must not call this helper. */
export function revealResult(element, {focus = true, block = "start"} = {}) {
  if (!element?.isConnected || element.closest("[hidden], [inert], dialog:not([open])")) return false;
  // A requested result can live inside an optional details section.
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
  if (!element.getClientRects().length) return false;
  if (focus) {
    if (!element.hasAttribute("tabindex") && element.tabIndex < 0) {
      element.setAttribute("tabindex", "-1");
      element.setAttribute("data-reveal-target", "");
    }
    element.focus({preventScroll: true});
  }
  // Immediate movement also respects reduced-motion preferences and avoids an
  // animation finishing after the user has already started their next action.
  element.scrollIntoView({behavior: "instant", block, inline: "nearest"});
  return true;
}
