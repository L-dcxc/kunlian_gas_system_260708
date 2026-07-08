from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repositories.base import EntityRepository

PLAN_TYPES = frozenset({"sensor_life", "calibration", "custom"})
PLAN_STATUSES = frozenset({"active", "completed", "cancelled"})
MAX_NOTES_LENGTH = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MaintenanceRepository(EntityRepository):
    table_name = "maintenance_plans"
    allowed_sort_columns = frozenset({"id", "detector_id", "plan_type", "due_at", "status", "created_at", "updated_at"})
    default_sort = "due_at"

    def find_active_by_id(self, plan_id: int):
        return self.fetch_one("SELECT * FROM maintenance_plans WHERE id = ? AND deleted_at IS NULL", (_positive_int(plan_id, "plan_id"),))

    def find_active_with_detector(self, plan_id: int):
        return self.fetch_one(
            _PLAN_WITH_DETECTOR_SQL + " WHERE mp.id = ? AND mp.deleted_at IS NULL",
            (_positive_int(plan_id, "plan_id"),),
        )

    def list_active(self, *, status: str | None = None):
        clauses = ["deleted_at IS NULL"]
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(_status(status))
        return self.fetch_all(
            f"SELECT * FROM maintenance_plans WHERE {' AND '.join(clauses)} ORDER BY due_at ASC, id ASC",
            tuple(params),
        )

    def list_active_with_detectors(self, *, status: str | None = None):
        clauses = ["mp.deleted_at IS NULL"]
        params: list[object] = []
        if status is not None:
            clauses.append("mp.status = ?")
            params.append(_status(status))
        return self.fetch_all(
            _PLAN_WITH_DETECTOR_SQL + f" WHERE {' AND '.join(clauses)} ORDER BY mp.due_at ASC, mp.id ASC",
            tuple(params),
        )

    def create(self, values: dict[str, Any], *, actor_id: int | None = None) -> int:
        normalized = _plan_values(values)
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO maintenance_plans(
                detector_id, plan_type, due_at, remind_days_before, status, notes,
                created_by, updated_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["detector_id"],
                normalized["plan_type"],
                normalized["due_at"],
                normalized["remind_days_before"],
                normalized["status"],
                normalized["notes"],
                actor_id,
                actor_id,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update(self, plan_id: int, values: dict[str, Any], *, actor_id: int | None = None) -> None:
        normalized = _plan_values(values)
        now = _now()
        self.execute(
            """
            UPDATE maintenance_plans
            SET detector_id = ?, plan_type = ?, due_at = ?, remind_days_before = ?, status = ?, notes = ?,
                updated_by = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                normalized["detector_id"],
                normalized["plan_type"],
                normalized["due_at"],
                normalized["remind_days_before"],
                normalized["status"],
                normalized["notes"],
                actor_id,
                now,
                _positive_int(plan_id, "plan_id"),
            ),
        )


MaintenancePlanRepository = MaintenanceRepository


_PLAN_WITH_DETECTOR_SQL = """
SELECT
    mp.*,
    d.position_code AS detector_position_code,
    d.name AS detector_name,
    d.is_enabled AS detector_is_enabled,
    d.deleted_at AS detector_deleted_at
FROM maintenance_plans mp
LEFT JOIN detectors d ON d.id = mp.detector_id
"""


def row_to_dict(row) -> dict[str, object]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def _plan_values(values: dict[str, Any]) -> dict[str, object]:
    return {
        "detector_id": _positive_int(values.get("detector_id"), "detector_id"),
        "plan_type": _plan_type(values.get("plan_type")),
        "due_at": _iso_datetime(values.get("due_at"), "due_at"),
        "remind_days_before": _int_range(values.get("remind_days_before", 7), 0, 3650, "remind_days_before"),
        "status": _status(values.get("status", "active")),
        "notes": _notes(values.get("notes", "")),
    }


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field}:必须为正整数")
    return int(value)


def _int_range(value: object, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{field}:必须在 {minimum}..{maximum} 范围内")
    return int(value)


def _plan_type(value: object) -> str:
    if not isinstance(value, str) or value not in PLAN_TYPES:
        raise ValueError("plan_type:取值不受支持")
    return value


def _status(value: object) -> str:
    if not isinstance(value, str) or value not in PLAN_STATUSES:
        raise ValueError("status:取值不受支持")
    return value


def _notes(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("notes:必须为文本")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if len(normalized) > MAX_NOTES_LENGTH:
        raise ValueError("notes:长度超出限制")
    return normalized


def _iso_datetime(value: object, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field}:不能为空")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field}:日期格式无效") from exc
    else:
        raise ValueError(f"{field}:日期格式无效")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
