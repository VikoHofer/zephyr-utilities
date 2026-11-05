/** A review opens only after an explicit request; refreshes can only invalidate it. */
export function createReviewDialog(dialog) {
  let shown;
  let returnFocus;
  function close({restoreFocus = true} = {}) {
    const wasOpen = dialog.open;
    if (wasOpen) dialog.close();
    shown = null;
    const control = typeof returnFocus === "function" ? returnFocus() : returnFocus;
    returnFocus = null;
    if (wasOpen && restoreFocus && control?.isConnected && !control.disabled && !control.closest("[hidden]")) control.focus({preventScroll: true});
  }
  return {
    show(plan, trigger) {
      if (!plan || dialog.open) return;
      shown = plan.id;
      returnFocus = trigger;
      for (const details of dialog.querySelectorAll("details")) details.open = false;
      dialog.showModal();
    },
    sync(plan, visible) { if (!plan || !visible || shown && shown !== plan.id) close(); },
    close,
  };
}
