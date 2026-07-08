from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal, Sequence

from app.db.repositories.base import EntityRepository, Pagination, validate_pagination
from app.db.repositories.operation_log_repository import OperationLogRepository

RecordType = Literal["alarm", "running", "operation"]

DEFAULT_QUERY_LOOKBACK_DAYS = 7
MAX_QUERY_SPAN_DAYS = 31
MAX_EXPORT_ROWS = 1000


@dataclass(frozen=True, slots=True)
class TimeRange:
    start_time: str
    end_time: str


class RecordRepository(OperationLogRepository):
    table_name = "operation_logs"

    def list_alarm_records(
        self,
        *,
        detector_id: int | None = None,
        controller_id: int | None = None,
        position_code: str | None = None,
        alarm_type: str | None = None,
        status: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "start_time",
        sort_direction: str = "DESC",
    ) -> tuple[list[Any], Pagination, int]:
        pagination = validate_pagination(page, per_page)
        clauses, params, _ = _record_filters(
            "alarm",
            detector_id=detector_id,
            controller_id=controller_id,
            position_code=position_code,
            alarm_type=alarm_type,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        order = _order_clause(sort_by, sort_direction, _ALARM_SORTS, "r.id DESC")
        return self._list("alarm", clauses, params, pagination, order)

    def list_running_records(
        self,
        *,
        detector_id: int | None = None,
        controller_id: int | None = None,
        port_id: int | None = None,
        position_code: str | None = None,
        status: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "recorded_at",
        sort_direction: str = "DESC",
    ) -> tuple[list[Any], Pagination, int]:
        pagination = validate_pagination(page, per_page)
        clauses, params, _ = _record_filters(
            "running",
            detector_id=detector_id,
            controller_id=controller_id,
            port_id=port_id,
            position_code=position_code,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        order = _order_clause(sort_by, sort_direction, _RUNNING_SORTS, "r.id DESC")
        return self._list("running", clauses, params, pagination, order)

    def list_operation_records(
        self,
        *,
        actor_id: int | None = None,
        username: str | None = None,
        action_type: str | None = None,
        result: str | None = None,
        keyword: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "created_at",
        sort_direction: str = "DESC",
    ) -> tuple[list[Any], Pagination, int]:
        pagination = validate_pagination(page, per_page)
        clauses, params, _ = _record_filters(
            "operation",
            actor_id=actor_id,
            username=username,
            action_type=action_type,
            result=result,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        )
        order = _order_clause(sort_by, sort_direction, _OPERATION_SORTS, "r.id DESC")
        return self._list("operation", clauses, params, pagination, order)

    def list_records(
        self,
        record_type: str,
        *,
        filters: dict[str, object] | None = None,
        page: int = 1,
        per_page: int = 20,
        sort_by: str | None = None,
        sort_direction: str = "DESC",
    ) -> tuple[list[Any], Pagination, int]:
        filters = filters or {}
        normalized = _record_type(record_type)
        if normalized == "alarm":
            return self.list_alarm_records(
                page=page,
                per_page=per_page,
                sort_by=sort_by or "start_time",
                sort_direction=sort_direction,
                **filters,
            )
        if normalized == "running":
            return self.list_running_records(
                page=page,
                per_page=per_page,
                sort_by=sort_by or "recorded_at",
                sort_direction=sort_direction,
                **filters,
            )
        return self.list_operation_records(
            page=page,
            per_page=per_page,
            sort_by=sort_by or "created_at",
            sort_direction=sort_direction,
            **filters,
        )

    def delete_record(self, record_type: str, record_id: int) -> int:
        spec = _SPECS[_record_type(record_type)]
        cursor = self.execute(f"DELETE FROM {spec.table} WHERE id = ?", (_positive_int(record_id, "record_id"),))
        return int(cursor.rowcount)

    def clear_records(self, record_type: str, *, filters: dict[str, object] | None = None) -> int:
        normalized = _record_type(record_type)
        clauses, params, _ = _record_filters(normalized, **(filters or {}))
        where = _where(clauses)
        spec = _SPECS[normalized]
        # SQLite DELETE cannot use the read-query alias directly; the subquery keeps joins and whitelisted filters consistent.
        cursor = self.execute(
            f"DELETE FROM {spec.table} WHERE id IN (SELECT r.id FROM {spec.from_sql} {where})",
            tuple(params),
        )
        return int(cursor.rowcount)

    def export_records(
        self,
        record_type: str,
        *,
        filters: dict[str, object] | None = None,
        sort_by: str | None = None,
        sort_direction: str = "DESC",
        limit: int = MAX_EXPORT_ROWS,
    ) -> list[Any]:
        limit = _export_limit(limit)
        normalized = _record_type(record_type)
        rows: list[Any] = []
        page = 1
        while len(rows) < limit:
            batch, _, _ = self.list_records(
                normalized,
                filters=filters,
                page=page,
                per_page=min(100, limit - len(rows)),
                sort_by=sort_by,
                sort_direction=sort_direction,
            )
            rows.extend(batch)
            if len(batch) < min(100, limit - len(rows) + len(batch)):
                break
            page += 1
        return rows[:limit]

    def _list(
        self,
        record_type: RecordType,
        clauses: Sequence[str],
        params: Sequence[object],
        pagination: Pagination,
        order_clause: str,
    ) -> tuple[list[Any], Pagination, int]:
        spec = _SPECS[record_type]
        where = _where(clauses)
        total = self.fetch_one(f"SELECT COUNT(*) AS total FROM {spec.from_sql} {where}", tuple(params))
        rows = self.fetch_all(
            f"SELECT {spec.select_sql} FROM {spec.from_sql} {where} {order_clause} LIMIT ? OFFSET ?",
            tuple(params) + (pagination.limit, pagination.offset),
        )
        return rows, pagination, int(total["total"] if total is not None else 0)


@dataclass(frozen=True, slots=True)
class _Spec:
    table: str
    time_column: str
    from_sql: str
    select_sql: str


_SPECS: dict[RecordType, _Spec] = {
    "alarm": _Spec(
        table="alarm_records",
        time_column="r.start_time",
        from_sql="alarm_records r LEFT JOIN detectors d ON d.id = r.detector_id LEFT JOIN controllers c ON c.id = d.controller_id",
        select_sql="r.*, d.position_code, d.name AS detector_name, d.port_id, d.controller_id, c.name AS controller_name",
    ),
    "running": _Spec(
        table="running_records",
        time_column="r.recorded_at",
        from_sql="running_records r LEFT JOIN detectors d ON d.id = r.detector_id LEFT JOIN controllers c ON c.id = r.controller_id",
        select_sql="r.*, d.position_code, d.name AS detector_name, c.name AS controller_name",
    ),
    "operation": _Spec(
        table="operation_logs",
        time_column="r.created_at",
        from_sql="operation_logs r",
        select_sql="r.*",
    ),
}

_ALARM_SORTS = {
    "id": "r.id",
    "detector_id": "r.detector_id",
    "alarm_type": "r.alarm_type",
    "status": "r.status",
    "start_time": "r.start_time",
    "end_time": "r.end_time",
    "created_at": "r.created_at",
    "position_code": "d.position_code",
}
_RUNNING_SORTS = {
    "id": "r.id",
    "detector_id": "r.detector_id",
    "controller_id": "r.controller_id",
    "port_id": "r.port_id",
    "status": "r.status",
    "recorded_at": "r.recorded_at",
    "created_at": "r.created_at",
    "position_code": "d.position_code",
}
_OPERATION_SORTS = {
    "id": "r.id",
    "created_at": "r.created_at",
    "actor_id": "r.actor_id",
    "actor_name": "r.actor_name",
    "action_type": "r.action_type",
    "result": "r.result",
}
_ALLOWED_ALARM_TYPES = frozenset({"alarm_low", "alarm_high", "over_range", "fault", "offline", "disabled", "warming"})
_ALLOWED_ALARM_STATUS = frozenset({"active", "recovered"})
_ALLOWED_QUALITY_STATUS = frozenset({"normal", "alarm_low", "alarm_high", "fault", "offline", "disabled", "over_range", "warming", "invalid"})


def _record_filters(record_type: str, **filters: object) -> tuple[list[str], list[object], TimeRange]:
    normalized = _record_type(record_type)
    clauses: list[str] = []
    params: list[object] = []
    time_range = _bounded_time_range(filters.get("start_time"), filters.get("end_time"))
    clauses.append(f"{_SPECS[normalized].time_column} >= ?")
    clauses.append(f"{_SPECS[normalized].time_column} <= ?")
    params.extend([time_range.start_time, time_range.end_time])
    if normalized in {"alarm", "running"}:
        _add_optional_int(clauses, params, "r.detector_id", filters.get("detector_id"), "detector_id")
        controller_column = "d.controller_id" if normalized == "alarm" else "r.controller_id"
        _add_optional_int(clauses, params, controller_column, filters.get("controller_id"), "controller_id")
        if normalized == "running":
            _add_optional_int(clauses, params, "r.port_id", filters.get("port_id"), "port_id")
            _add_optional_code(clauses, params, "r.status", filters.get("status"), _ALLOWED_QUALITY_STATUS, "status")
        if filters.get("position_code") is not None:
            clauses.append("d.position_code LIKE ? ESCAPE '\\'")
            params.append(_like(str(filters["position_code"]), 80))
    if normalized == "alarm":
        _add_optional_code(clauses, params, "r.alarm_type", filters.get("alarm_type"), _ALLOWED_ALARM_TYPES, "alarm_type")
        _add_optional_code(clauses, params, "r.status", filters.get("status"), _ALLOWED_ALARM_STATUS, "status")
    if normalized == "operation":
        _add_optional_int(clauses, params, "r.actor_id", filters.get("actor_id"), "actor_id")
        if filters.get("username") is not None:
            clauses.append("r.actor_name LIKE ? ESCAPE '\\'")
            params.append(_like(str(filters["username"]), 80))
        if filters.get("action_type") is not None:
            clauses.append("r.action_type = ?")
            params.append(_code(str(filters["action_type"]), 80, "action_type"))
        if filters.get("result") is not None:
            clauses.append("r.result = ?")
            params.append(_code(str(filters["result"]), 40, "result"))
        if filters.get("keyword") is not None:
            clauses.append("(r.summary LIKE ? ESCAPE '\\' OR r.details_json LIKE ? ESCAPE '\\')")
            value = _like(str(filters["keyword"]), 120)
            params.extend([value, value])
    return clauses, params, time_range


def _bounded_time_range(start_value: object, end_value: object) -> TimeRange:
    start = _optional_time(start_value, "start_time")
    end = _optional_time(end_value, "end_time")
    now = datetime.now(timezone.utc)
    # [待确认] Historical retention and default query window are not fixed, so unbounded record queries use a conservative recent window.
    if start is None and end is None:
        end = now
        start = end - timedelta(days=DEFAULT_QUERY_LOOKBACK_DAYS)
    elif start is None:
        start = end - timedelta(days=MAX_QUERY_SPAN_DAYS)  # type: ignore[union-attr]
    elif end is None:
        end = min(now, start + timedelta(days=MAX_QUERY_SPAN_DAYS))
    if start > end:
        raise ValueError("start_time must not be after end_time")
    if end - start > timedelta(days=MAX_QUERY_SPAN_DAYS):
        raise ValueError(f"time range must not exceed {MAX_QUERY_SPAN_DAYS} days")
    return TimeRange(start.isoformat(), end.isoformat())


def _optional_time(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _record_type(value: str) -> RecordType:
    if value not in _SPECS:
        raise ValueError("unsupported record type")
    return value  # type: ignore[return-value]


def _order_clause(sort_by: str, direction: str, mapping: dict[str, str], tie_breaker: str) -> str:
    column = mapping.get(sort_by)
    if column is None:
        raise ValueError("unsupported sort field")
    normalized_direction = direction.upper()
    if normalized_direction not in {"ASC", "DESC"}:
        raise ValueError("unsupported sort direction")
    # The SQL identifier comes only from mapping above; user input never reaches this fragment directly.
    return f"ORDER BY {column} {normalized_direction}, {tie_breaker}"


def _where(clauses: Sequence[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _add_optional_int(clauses: list[str], params: list[object], column: str, value: object, field: str) -> None:
    if value is not None:
        clauses.append(f"{column} = ?")
        params.append(_positive_int(value, field))


def _add_optional_code(
    clauses: list[str],
    params: list[object],
    column: str,
    value: object,
    allowed: Iterable[str],
    field: str,
) -> None:
    if value is None:
        return
    text = str(value)
    if text not in set(allowed):
        raise ValueError(f"unsupported {field}")
    clauses.append(f"{column} = ?")
    params.append(text)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _export_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > MAX_EXPORT_ROWS:
        raise ValueError(f"export limit must be between 1 and {MAX_EXPORT_ROWS}")
    return value


def _like(value: str, max_length: int) -> str:
    text = _text(value, max_length)
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _code(value: str, max_length: int, field: str) -> str:
    text = _text(value, max_length)
    if not text or not text.replace("_", "").replace(":", "").replace(".", "").replace("-", "").isalnum():
        raise ValueError(f"unsupported {field}")
    return text


def _text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value must be a string")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return normalized[:max_length]
