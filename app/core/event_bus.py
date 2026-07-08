from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass
from typing import Any, Callable

from app.core.logging import get_logger, user_safe_error

EventCallback = Callable[[str, Any], None]


@dataclass(frozen=True)
class Subscription:
    event_type: str
    token: int
    unsubscribe_callback: Callable[[str, int], None]

    def unsubscribe(self) -> None:
        self.unsubscribe_callback(self.event_type, self.token)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._callbacks: dict[str, dict[int, EventCallback]] = {}
        self._tokens = itertools.count(1)
        self._logger = get_logger("event_bus")

    def subscribe(self, event_type: str, callback: EventCallback) -> Subscription:
        if not event_type:
            raise ValueError("event_type is required")
        token = next(self._tokens)
        with self._lock:
            self._callbacks.setdefault(event_type, {})[token] = callback
        return Subscription(event_type=event_type, token=token, unsubscribe_callback=self.unsubscribe)

    def unsubscribe(self, event_type: str, token: int) -> None:
        with self._lock:
            callbacks = self._callbacks.get(event_type)
            if callbacks is None:
                return
            callbacks.pop(token, None)
            if not callbacks:
                self._callbacks.pop(event_type, None)

    def publish(self, event_type: str, payload: Any = None) -> None:
        with self._lock:
            callbacks = list(self._callbacks.get(event_type, {}).values())
        for callback in callbacks:
            try:
                callback(event_type, payload)
            except Exception as exc:
                self._logger.error("event callback failed event_type=%s error=%s", event_type, user_safe_error(exc))
