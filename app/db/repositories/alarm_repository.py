from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repositories.base import EntityRepository, build_time_range_clause, validate_pagination

ACTIVE_ALARM_STATUS = "active"
RECOVERED_ALARM_STATUS = "recovered"
ALARM_TYPES = frozenset({"alarm_low", "alarm_high", "over_range", "fault", "offline", "disabled", "warming"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlarmRepository(EntityRepository):
    table_name = "alarm_records"
    allowed_sort_columns = frozenset({"id", "detector_id", "alarm_type", "status", "start_time", "end_time", "created_at"})
    default_sort = "start_time"

    def find_active(self, detector_id: int, alarm_type: str):
        return self.fetch_one(
            "SELECT * FROM alarm_records WHERE detector_id = ? AND alarm_type = ? AND status = ?",
            (_positive_int(detector_id, "detector_id"), _alarm_type(alarm_type), ACTIVE_ALARM_STATUS),
        )

    def active_for_detector(self, detector_id: int):
        return self.fetch_all(
            "SELECT * FROM alarm_records WHERE detector_id = ? AND status = ? ORDER BY start_time ASC",
            (_positive_int(detector_id, "detector_id"), ACTIVE_ALARM_STATUS),
        )

    def create_active(
        self,
        *,
        detector_id: int,
        alarm_type: str,
        alarm_level: int | None,
        trigger_value: float | None,
        start_time: str,
        source_reading_id: int | None = None,
    ) -> tuple[int, bool]:
        existing = self.find_active(detector_id, alarm_type)
        if existing is not None:
            return int(existing["id"]), False
        cursor = self.execute(
            """
            INSERT INTO alarm_records(
                detector_id, alarm_type, alarm_level, trigger_value, start_time, status, source_reading_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _positive_int(detector_id, "detector_id"),
                _alarm_type(alarm_type),
                _optional_non_negative_int(alarm_level, "alarm_level"),
                _optional_float(trigger_value, "trigger_value"),
                _iso_text(start_time, "start_time"),
                ACTIVE_ALARM_STATUS,
                source_reading_id,
                _now(),
                _now(),
            ),
        )
        return int(cursor.lastrowid), True

    def recover_active(self, detector_id: int, alarm_type: str, end_time: str) -> bool:
        cursor = self.execute(
            """
            UPDATE alarm_records
            SET status = ?, end_time = ?, updated_at = ?
            WHERE detector_id = ? AND alarm_type = ? AND status = ?
            """,
            (
                RECOVERED_ALARM_STATUS,
                _iso_text(end_time, "end_time"),
                _now(),
                _positive_int(detector_id, "detector_id"),
                _alarm_type(alarm_type),
                ACTIVE_ALARM_STATUS,
            ),
        )
        return cursor.rowcount > 0

    def list_active(self):
        return self.fetch_all("SELECT * FROM alarm_records WHERE status = ? ORDER BY start_time DESC", (ACTIVE_ALARM_STATUS,))

    def list_history(
        self,
        *,
        detector_id: int | None = None,
        alarm_type: str | None = None,
        status: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Any], Any, int]:
        pagination = validate_pagination(page, per_page)
        clauses: list[str] = []
        params: list[object] = []
        if detector_id is not None:
            clauses.append("detector_id = ?")
            params.append(_positive_int(detector_id, "detector_id"))
        if alarm_type is not None:
            clauses.append("alarm_type = ?")
            params.append(_alarm_type(alarm_type))
        if status is not None:
            clauses.append("status = ?")
            params.append(_alarm_status(status))
        time_clause, time_params = build_time_range_clause("start_time", start_time, end_time, {"start_time"})
        if time_clause:
            clauses.append(time_clause)
            params.extend(time_params)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self.fetch_one(f"SELECT COUNT(*) AS total FROM alarm_records {where}", tuple(params))
        rows = self.fetch_all(
            f"""
            SELECT * FROM alarm_records {where}
            ORDER BY start_time DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (pagination.limit, pagination.offset),
        )
        return rows, pagination, int(total["total"] if total is not None else 0)


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _optional_non_negative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return value


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def _alarm_type(value: str) -> str:
    if value not in ALARM_TYPES:
        raise ValueError("unsupported alarm_type")
    return value


def _alarm_status(value: str) -> str:
    if value not in {ACTIVE_ALARM_STATUS, RECOVERED_ALARM_STATUS}:
        raise ValueError("unsupported alarm status")
    return value


def _iso_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be ISO-8601 text")
    datetime.fromisoformat(value)
    return value
