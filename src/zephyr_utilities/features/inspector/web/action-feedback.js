/** Reveal only the latest explicit action, while it still belongs to this workspace. */
export function createActionFeedback({workspace, reveal, notify, isDisposed}) {
  let revision = 0;
  return {
    invalidate() { revision++; },
    async run(action, target, {pending, complete} = {}) {
      const captured = ++revision;
      const path = workspace();
      if (pending) notify?.(pending);
      await action();
      if (isDisposed() || revision !== captured || workspace() !== path) return;
      const result = target();
      if (!result) return;
      if (complete) notify?.(complete());
      reveal?.(result);
    },
  };
}
