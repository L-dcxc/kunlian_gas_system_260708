from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.runtime_repository import RealtimeSnapshotRepository, RunningRecordRepository
from app.db.unit_of_work import UnitOfWork
from app.services.errors import ErrorCode
from app.services.models import Page, Pagination, ServiceResult


@dataclass(frozen=True, slots=True)
class MonitoringReadingView:
    detector_id: int
    protocol: str
    source_type: str
    port_id: int
    controller_id: int | None
    status: str
    concentration: float | None
    gas_type: str | None
    unit: str | None
    alarm_level: int | None
    timestamp: str
    quality: str


class MonitoringReadService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_realtime(
        self,
        *,
        port_id: int | None = None,
        controller_id: int | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> ServiceResult[Page[MonitoringReadingView]]:
        try:
            with UnitOfWork(self._database) as uow:
                repo = RealtimeSnapshotRepository(uow)
                rows, pagination = repo.list_current(
                    port_id=port_id,
                    controller_id=controller_id,
                    status=status,
                    page=page,
                    per_page=per_page,
                )
                total = repo.count_current(port_id=port_id, controller_id=controller_id, status=status)
                uow.commit()
            return ServiceResult.ok(Page(tuple(_reading_view(row) for row in rows), _service_pagination(pagination), total))
        except ValueError as exc:
            return _validation(str(exc))

    def get_realtime(self, detector_id: int) -> ServiceResult[MonitoringReadingView]:
        if not _valid_id(detector_id):
            return _validation("探测器 ID 无效")
        with UnitOfWork(self._database) as uow:
            row = RealtimeSnapshotRepository(uow).find_by_detector_id(detector_id)
            uow.commit()
        if row is None:
            return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="实时数据不存在")
        return ServiceResult.ok(_reading_view(row))

    def list_running_records(
        self,
        *,
        detector_id: int | None = None,
        port_id: int | None = None,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> ServiceResult[Page[dict[str, object]]]:
        try:
            with UnitOfWork(self._database) as uow:
                rows, pagination, total = RunningRecordRepository(uow).list_records(
                    detector_id=detector_id,
                    port_id=port_id,
                    start_time=_time_text(start_time),
                    end_time=_time_text(end_time),
                    page=page,
                    per_page=per_page,
                )
                uow.commit()
            return ServiceResult.ok(Page(tuple(_row_dict(row) for row in rows), _service_pagination(pagination), total))
        except ValueError as exc:
            return _validation(str(exc))

    def list_active_alarms(self) -> ServiceResult[tuple[dict[str, object], ...]]:
        with UnitOfWork(self._database) as uow:
            rows = AlarmRepository(uow).list_active()
            uow.commit()
        return ServiceResult.ok(tuple(_row_dict(row) for row in rows))


def _reading_view(row) -> MonitoringReadingView:
    return MonitoringReadingView(
        detector_id=int(row["detector_id"]),
        protocol=str(row["protocol"]),
        source_type=str(row["source_type"]),
        port_id=int(row["port_id"]),
        controller_id=None if row["controller_id"] is None else int(row["controller_id"]),
        status=str(row["status"]),
        concentration=None if row["concentration"] is None else float(row["concentration"]),
        gas_type=row["gas_type"],
        unit=row["unit"],
        alarm_level=None if row["alarm_level"] is None else int(row["alarm_level"]),
        timestamp=str(row["timestamp"]),
        quality=str(row["quality"]),
    )


def _service_pagination(pagination) -> Pagination:
    return Pagination(page=pagination.page, per_page=pagination.per_page)


def _row_dict(row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def _time_text(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    datetime.fromisoformat(value)
    return value


def _valid_id(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=message)
