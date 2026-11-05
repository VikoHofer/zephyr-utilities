/** One polling owner spans both the active request and its scheduled retry. */
export function createReportPoller({jobId, read, onUpdate, onError, schedule, isDisposed}) {
  let owner = null;
  const current = (candidate) => owner === candidate && !isDisposed() && jobId() === candidate.id;

  async function tick(candidate) {
    if (!current(candidate)) return;
    let delay = null;
    try {
      const job = await read(candidate.id);
      if (!current(candidate)) return;
      if (job.id !== candidate.id) throw new Error("The report status belongs to another job.");
      await onUpdate(job);
      delay = 700;
    } catch (error) {
      if (!current(candidate) || error.name === "AbortError") return;
      onError(error);
      delay = 3000;
    } finally {
      if (delay !== null && current(candidate)) schedule(() => tick(candidate), delay);
      else if (owner === candidate) owner = null;
    }
  }

  return function poll(id) {
    if (isDisposed() || !id || jobId() !== id || owner?.id === id) return Promise.resolve();
    const candidate = {id};
    owner = candidate;
    return tick(candidate);
  };
}
