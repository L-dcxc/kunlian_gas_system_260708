from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator


class RuntimeLockError(RuntimeError):
    pass


class RuntimeLockManager:
    CONFLICTS: dict[str, set[str]] = {
        "acquisition": {"restore", "migration"},
        "backup": {"restore", "migration"},
        "restore": {"acquisition", "backup", "migration"},
        "migration": {"acquisition", "backup", "restore"},
    }

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._active: set[str] = set()

    @contextmanager
    def acquire(self, operation: str, timeout: float | None = 0) -> Iterator[None]:
        self.acquire_operation(operation, timeout=timeout)
        try:
            yield
        finally:
            self.release_operation(operation)

    def acquire_operation(self, operation: str, timeout: float | None = 0) -> None:
        if operation not in self.CONFLICTS:
            raise ValueError("unsupported runtime operation")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._has_conflict(operation):
                if timeout == 0:
                    raise RuntimeLockError("当前操作与正在运行的任务冲突，请稍后再试。")
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise RuntimeLockError("等待运行锁超时，请稍后再试。")
                self._condition.wait(remaining)
            self._active.add(operation)

    def release_operation(self, operation: str) -> None:
        with self._condition:
            self._active.discard(operation)
            self._condition.notify_all()

    def is_active(self, operation: str) -> bool:
        with self._condition:
            return operation in self._active

    def _has_conflict(self, operation: str) -> bool:
        conflicts = self.CONFLICTS[operation]
        return bool(self._active.intersection(conflicts))
