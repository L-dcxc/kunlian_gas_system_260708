from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from app.core.logging import get_logger, user_safe_error

T = TypeVar("T")
WorkerTask = Callable[["CancellationToken"], T]
SuccessCallback = Callable[[T], None]
ErrorCallback = Callable[["WorkerError"], None]


@dataclass(frozen=True)
class WorkerError:
    job_name: str
    message: str


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CancelledError("worker cancelled")


class CancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerHandle(Generic[T]):
    name: str
    token: CancellationToken
    future: concurrent.futures.Future[T]

    def cancel(self) -> None:
        self.token.cancel()
        self.future.cancel()


class WorkerPool:
    def __init__(self, max_workers: int = 4) -> None:
        if max_workers < 1 or max_workers > 32:
            raise ValueError("max_workers must be between 1 and 32")
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="app-worker")
        self._logger = get_logger("workers")

    def submit(
        self,
        name: str,
        task: WorkerTask[T],
        on_success: SuccessCallback[T] | None = None,
        on_error: ErrorCallback | None = None,
    ) -> WorkerHandle[T]:
        if not name:
            raise ValueError("worker name is required")
        token = CancellationToken()
        future = self._executor.submit(self._run_task, name, task, token)
        future.add_done_callback(lambda item: self._handle_done(name, item, on_success, on_error))
        return WorkerHandle(name=name, token=token, future=future)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run_task(self, name: str, task: WorkerTask[T], token: CancellationToken) -> T:
        token.raise_if_cancelled()
        return task(token)

    def _handle_done(
        self,
        name: str,
        future: concurrent.futures.Future[T],
        on_success: SuccessCallback[T] | None,
        on_error: ErrorCallback | None,
    ) -> None:
        try:
            result = future.result()
        except concurrent.futures.CancelledError:
            return
        except CancelledError:
            return
        except Exception as exc:
            error_message = user_safe_error(exc)
            self._logger.error("worker failed name=%s error=%s", name, error_message)
            if on_error is not None:
                on_error(WorkerError(job_name=name, message=error_message))
            return
        if on_success is not None:
            on_success(result)
