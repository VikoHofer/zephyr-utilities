"""One bounded job queue and resource coordinator for all features."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, RLock
import uuid


class JobManager:
    def __init__(self, max_workers=2, max_pending=8, max_completed=20):
        self._jobs = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="zephyr-job")
        self._closed = False
        self.max_pending = max_pending
        self.max_completed = max_completed

    def submit(self, feature, task, *, resource_key=None, metadata=None):
        """task(emit, cancel) returns feature-owned result data; never holds the queue lock."""
        if not isinstance(feature, str) or not feature or not callable(task):
            raise ValueError("Jobs require a feature identity and a callable task.")
        with self._lock:
            if self._closed:
                raise ValueError("The application is shutting down.")
            active = [job for job in self._jobs.values() if job["status"] in ("queued", "running")]
            if resource_key is not None and any(job["resource_key"] == resource_key for job in active):
                raise ValueError("A job is already using this resource. Wait for it to finish or cancel it.")
            if len(active) >= self.max_pending:
                raise ValueError("The job queue is full. Wait for an active job to finish.")
            completed = [key for key, job in self._jobs.items() if job["status"] not in ("queued", "running")]
            discard_count = max(0, len(completed) - self.max_completed + 1)
            for key in completed[:discard_count]:
                del self._jobs[key]
            identifier = uuid.uuid4().hex
            self._jobs[identifier] = {
                "id": identifier, "feature": feature, "status": "queued", "logs": [],
                "created_at": datetime.now(timezone.utc).isoformat(), "result": None,
                "metadata": dict(metadata or {}), "resource_key": resource_key, "cancel": Event(),
            }
            self._executor.submit(self._run, identifier, task)
            return self.get(identifier)

    def _run(self, identifier, task):
        with self._lock:
            job = self._jobs[identifier]

        def emit(message):
            with self._lock:
                job["logs"].append(str(message)[:2000])
                del job["logs"][:-500]

        try:
            if job["cancel"].is_set():
                raise CancelledError()
            with self._lock:
                job["status"] = "running"
            result = task(emit, job["cancel"])
            with self._lock:
                job.update(status="completed", result=result)
        except CancelledError:
            with self._lock:
                job["status"] = "cancelled"
            emit("Job cancelled.")
        except Exception as exc:
            with self._lock:
                job.update(status="failed", error=str(exc) or type(exc).__name__)
            emit(job["error"])

    def get(self, identifier):
        with self._lock:
            if identifier not in self._jobs:
                raise FileNotFoundError("Job not found.")
            job = self._jobs[identifier]
            result = {key: value for key, value in job.items() if key not in {"cancel", "resource_key"}}
            result["logs"] = list(job["logs"])
            result["metadata"] = dict(job["metadata"])
            # Public feature fields remain convenient for clients of older job APIs.
            for fields in (job["metadata"], job["result"]):
                if isinstance(fields, dict):
                    for key, value in fields.items():
                        if key not in job:
                            result.setdefault(key, value)
            return result

    def list(self, feature=None):
        with self._lock:
            return [self.get(key) for key, job in reversed(self._jobs.items())
                    if feature is None or job["feature"] == feature]

    def cancel(self, identifier):
        with self._lock:
            self.get(identifier)
            self._jobs[identifier]["cancel"].set()
            return self.get(identifier)

    def close(self):
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                job["cancel"].set()
        # Let queued jobs observe cancellation too, so every job reaches a terminal state.
        self._executor.shutdown(wait=True)
