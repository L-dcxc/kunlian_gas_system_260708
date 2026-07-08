from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from app.core.logging import get_logger, user_safe_error

ScheduledCallback = Callable[[], None]


@dataclass
class ScheduledJob:
    name: str
    interval_seconds: float
    callback: ScheduledCallback
    run_immediately: bool = False
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def cancel(self) -> None:
        self._cancelled.set()


class Scheduler:
    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}
        self._lock = threading.RLock()
        self._logger = get_logger("scheduler")

    def every(self, name: str, interval_seconds: float, callback: ScheduledCallback, run_immediately: bool = False) -> ScheduledJob:
        if not name:
            raise ValueError("job name is required")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        job = ScheduledJob(name=name, interval_seconds=interval_seconds, callback=callback, run_immediately=run_immediately)
        with self._lock:
            if name in self._jobs:
                raise ValueError("job already exists")
            self._jobs[name] = job
        job._thread = threading.Thread(target=self._run_job, args=(job,), name=f"scheduler-{name}", daemon=True)
        job._thread.start()
        return job

    def cancel(self, name: str) -> None:
        with self._lock:
            job = self._jobs.pop(name, None)
        if job is not None:
            job.cancel()

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.cancel()
        for job in jobs:
            if job._thread is not None:
                job._thread.join(timeout=2)

    def _run_job(self, job: ScheduledJob) -> None:
        if job.run_immediately:
            self._invoke(job)
        while not job._cancelled.wait(job.interval_seconds):
            self._invoke(job)

    def _invoke(self, job: ScheduledJob) -> None:
        try:
            job.callback()
        except Exception as exc:
            self._logger.error("scheduled job failed name=%s error=%s", job.name, user_safe_error(exc))
