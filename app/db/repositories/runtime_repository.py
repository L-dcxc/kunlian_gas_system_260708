from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repositories.base import EntityRepository, build_time_range_clause, validate_pagination
from app.services.models import DeviceReading, DeviceStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(value: datetime) -> str:
    return value.isoformat()


class RealtimeSnapshotRepository(EntityRepository):
    table_name = "realtime_snapshots"
    allowed_sort_columns = frozenset({"id", "detector_id", "status", "timestamp", "updated_at"})
    default_sort = "detector_id"

    def upsert_reading(self, reading: DeviceReading, *, quality: str = "valid") -> int:
        _validate_quality(quality)
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO realtime_snapshots(
                detector_id, protocol, source_type, port_id, controller_id, controller_address, detector_address,
                status, concentration, gas_type, unit, alarm_level, raw_status, raw_value, quality, timestamp, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(detector_id) DO UPDATE SET
                protocol = excluded.protocol,
                source_type = excluded.source_type,
                port_id = excluded.port_id,
                controller_id = excluded.controller_id,
                controller_address = excluded.controller_address,
                detector_address = excluded.detector_address,
                status = excluded.status,
                concentration = excluded.concentration,
                gas_type = excluded.gas_type,
                unit = excluded.unit,
                alarm_level = excluded.alarm_level,
                raw_status = excluded.raw_status,
                raw_value = excluded.raw_value,
                quality = excluded.quality,
                timestamp = excluded.timestamp,
                updated_at = excluded.updated_at
            """,
            _reading_params(reading, quality, _dt(reading.timestamp), now),
        )
        row = self.find_by_detector_id(reading.detector_id)
        return int(row["id"] if row is not None else cursor.lastrowid)

    def find_by_detector_id(self, detector_id: int):
        _positive_int(detector_id, "detector_id")
        return self.fetch_one("SELECT * FROM realtime_snapshots WHERE detector_id = ?", (detector_id,))

    def list_current(
        self,
        *,
        port_id: int | None = None,
        controller_id: int | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ):
        clauses, params = _snapshot_filters(port_id=port_id, controller_id=controller_id, status=status)
        return self.list_page(
            page=page,
            per_page=per_page,
            sort_by="detector_id",
            sort_direction="ASC",
            where_clause=" AND ".join(clauses),
            parameters=tuple(params),
        )

    def count_current(
        self,
        *,
        port_id: int | None = None,
        controller_id: int | None = None,
        status: str | None = None,
    ) -> int:
        clauses, params = _snapshot_filters(port_id=port_id, controller_id=controller_id, status=status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.fetch_one(f"SELECT COUNT(*) AS total FROM realtime_snapshots {where}", tuple(params))
        return int(row["total"] if row is not None else 0)


class RunningRecordRepository(EntityRepository):
    table_name = "running_records"
    allowed_sort_columns = frozenset({"id", "detector_id", "recorded_at", "status", "created_at"})
    default_sort = "recorded_at"

    def add_reading(self, reading: DeviceReading, *, quality: str = "valid") -> int:
        _validate_quality(quality)
        cursor = self.execute(
            """
            INSERT INTO running_records(
                detector_id, protocol, source_type, port_id, controller_id, status, concentration, gas_type, unit,
                alarm_level, raw_status, raw_value, quality, recorded_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading.detector_id,
                reading.protocol.value,
                reading.source_type.value,
                reading.port_id,
                reading.controller_id,
                reading.status.value,
                reading.concentration,
                reading.gas_type,
                reading.unit,
                reading.alarm_level,
                None if reading.raw_status is None else str(reading.raw_status),
                reading.raw_value,
                quality,
                _dt(reading.timestamp),
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def list_records(
        self,
        *,
        detector_id: int | None = None,
        port_id: int | None = None,
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
        if port_id is not None:
            clauses.append("port_id = ?")
            params.append(_positive_int(port_id, "port_id"))
        time_clause, time_params = build_time_range_clause("recorded_at", start_time, end_time, {"recorded_at"})
        if time_clause:
            clauses.append(time_clause)
            params.extend(time_params)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total_row = self.fetch_one(f"SELECT COUNT(*) AS total FROM running_records {where}", tuple(params))
        rows = self.fetch_all(
            f"""
            SELECT * FROM running_records {where}
            ORDER BY recorded_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (pagination.limit, pagination.offset),
        )
        return rows, pagination, int(total_row["total"] if total_row is not None else 0)


def _snapshot_filters(
    *,
    port_id: int | None = None,
    controller_id: int | None = None,
    status: str | None = None,
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if port_id is not None:
        clauses.append("port_id = ?")
        params.append(_positive_int(port_id, "port_id"))
    if controller_id is not None:
        clauses.append("controller_id = ?")
        params.append(_positive_int(controller_id, "controller_id"))
    if status is not None:
        clauses.append("status = ?")
        params.append(DeviceStatus(status).value)
    return clauses, params


def _reading_params(reading: DeviceReading, quality: str, timestamp: str, updated_at: str) -> tuple[object, ...]:
    return (
        reading.detector_id,
        reading.protocol.value,
        reading.source_type.value,
        reading.port_id,
        reading.controller_id,
        reading.controller_address,
        reading.detector_address,
        reading.status.value,
        reading.concentration,
        reading.gas_type,
        reading.unit,
        reading.alarm_level,
        None if reading.raw_status is None else str(reading.raw_status),
        reading.raw_value,
        quality,
        timestamp,
        updated_at,
    )


def _validate_quality(value: str) -> None:
    if value not in {"valid", "offline"}:
        raise ValueError("unsupported reading quality")


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value
