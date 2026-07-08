from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.event_bus import EventBus, Subscription

STATE_READINGS_UPDATED = "state.readings_updated"
STATE_KEY_CHANGED = "state.key_changed"


@dataclass(frozen=True)
class RealtimeFilter:
    detector_ids: tuple[int, ...] | None = None
    status: str | None = None


class StateStore:
    def __init__(self, event_bus: EventBus | None = None, curve_cache_size: int = 600, publish_interval_ms: int = 200) -> None:
        self._event_bus = event_bus or EventBus()
        self._lock = threading.RLock()
        self._readings: dict[int, Any] = {}
        self._values: dict[str, Any] = {}
        self._curve_cache: dict[int, deque[Any]] = {}
        self._curve_cache_size = curve_cache_size
        self._publish_interval = publish_interval_ms / 1000
        self._last_publish_at = 0.0

    def update_readings(self, readings: Iterable[Any]) -> None:
        changed: list[Any] = []
        with self._lock:
            for reading in readings:
                detector_id = _detector_id(reading)
                if detector_id is None:
                    continue
                self._readings[detector_id] = reading
                self._curve_cache.setdefault(detector_id, deque(maxlen=self._curve_cache_size)).append(reading)
                changed.append(reading)
            should_publish = self._should_publish_locked()
        if changed and should_publish:
            self._event_bus.publish(STATE_READINGS_UPDATED, tuple(changed))

    def get_realtime_snapshot(self, filters: RealtimeFilter | None = None) -> list[Any]:
        filters = filters or RealtimeFilter()
        with self._lock:
            readings = list(self._readings.values())
        if filters.detector_ids is not None:
            allowed = set(filters.detector_ids)
            readings = [reading for reading in readings if _detector_id(reading) in allowed]
        if filters.status is not None:
            readings = [reading for reading in readings if _status(reading) == filters.status]
        return readings

    def get_curve_cache(self, detector_id: int) -> list[Any]:
        with self._lock:
            return list(self._curve_cache.get(detector_id, ()))

    def set_value(self, key: str, value: Any) -> None:
        if not key:
            raise ValueError("state key is required")
        with self._lock:
            self._values[key] = value
        self._event_bus.publish(STATE_KEY_CHANGED, {"key": key, "value": value})

    def get_value(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, default)

    def subscribe(self, event_type: str, callback) -> Subscription:
        return self._event_bus.subscribe(event_type, callback)

    def _should_publish_locked(self) -> bool:
        now = time.monotonic()
        if now - self._last_publish_at < self._publish_interval:
            return False
        self._last_publish_at = now
        return True


def _detector_id(reading: Any) -> int | None:
    if isinstance(reading, dict):
        value = reading.get("detector_id")
    else:
        value = getattr(reading, "detector_id", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _status(reading: Any) -> str | None:
    if isinstance(reading, dict):
        value = reading.get("status")
    else:
        value = getattr(reading, "status", None)
    return value if isinstance(value, str) else None
