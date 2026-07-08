from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from app.core.state_store import StateStore
from app.db.connection import Database
from app.db.repositories.record_repository import MAX_QUERY_SPAN_DAYS, RecordRepository
from app.db.unit_of_work import UnitOfWork
from app.services.errors import ErrorCode
from app.services.models import Page, Pagination, ServiceResult

REALTIME_DEFAULT_LOOKBACK_MINUTES = 10
HISTORY_MAX_POINTS = 1000


@dataclass(frozen=True, slots=True)
class RealtimeSeriesPoint:
    detector_id: int
    status: str | None
    concentration: float | None
    unit: str | None
    timestamp: str | None


@dataclass(frozen=True, slots=True)
class RealtimeSeriesView:
    detector_id: int
    points: tuple[RealtimeSeriesPoint, ...]


@dataclass(frozen=True, slots=True)
class HistoryCurveQuery:
    detector_ids: tuple[int, ...]
    start_time: str
    end_time: str
    page: int = 1
    per_page: int = 100
    sort_direction: str = "ASC"

    def __post_init__(self) -> None:
        object.__setattr__(self, "detector_ids", tuple(_positive_int(item, "detector_id") for item in self.detector_ids))
        if not self.detector_ids:
            raise ValueError("detector_ids are required")
        if len(set(self.detector_ids)) > 20:
            raise ValueError("detector_ids must not exceed 20")
        _validate_history_range(self.start_time, self.end_time)
        Pagination(page=self.page, per_page=self.per_page)
        if self.per_page > HISTORY_MAX_POINTS:
            raise ValueError(f"history points must not exceed {HISTORY_MAX_POINTS}")
        if self.sort_direction.upper() not in {"ASC", "DESC"}:
            raise ValueError("unsupported sort direction")


@dataclass(frozen=True, slots=True)
class HistoryPointView:
    id: int
    detector_id: int
    recorded_at: str
    status: str
    concentration: float | None
    gas_type: str | None
    unit: str | None
    position_code: str | None = None
    detector_name: str | None = None


class ChartService:
    def __init__(self, database: Database, state_store: StateStore) -> None:
        self._database = database
        self._state_store = state_store

    def get_realtime_series(
        self,
        detector_ids: Sequence[int],
        *,
        lookback_minutes: int = REALTIME_DEFAULT_LOOKBACK_MINUTES,
    ) -> ServiceResult[tuple[RealtimeSeriesView, ...]]:
        try:
            safe_ids = tuple(dict.fromkeys(_positive_int(item, "detector_id") for item in detector_ids))
            if not safe_ids:
                return _validation("detector_ids are required")
            if isinstance(lookback_minutes, bool) or not isinstance(lookback_minutes, int) or lookback_minutes < 1 or lookback_minutes > 1440:
                return _validation("lookback_minutes must be between 1 and 1440")
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
            views: list[RealtimeSeriesView] = []
            for detector_id in safe_ids:
                # Realtime chart data comes only from StateStore's in-memory short cache, never from protocol/channel layers.
                points = tuple(
                    point
                    for point in (_series_point(item) for item in self._state_store.get_curve_cache(detector_id))
                    if point.timestamp is None or _parse_time(point.timestamp) >= cutoff
                )
                views.append(RealtimeSeriesView(detector_id=detector_id, points=points))
            return ServiceResult.ok(tuple(views))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="实时曲线查询失败")

    def query_history(self, command: HistoryCurveQuery) -> ServiceResult[Page[HistoryPointView]]:
        try:
            _validate_history_range(command.start_time, command.end_time)
            all_rows: list[Any] = []
            total = 0
            with UnitOfWork(self._database) as uow:
                repo = RecordRepository(uow)
                for detector_id in command.detector_ids:
                    first_rows, _, detector_total = repo.list_running_records(
                        detector_id=detector_id,
                        start_time=command.start_time,
                        end_time=command.end_time,
                        page=1,
                        per_page=100,
                        sort_by="recorded_at",
                        sort_direction=command.sort_direction,
                    )
                    total += detector_total
                    if total > HISTORY_MAX_POINTS:
                        return _validation(f"history points must not exceed {HISTORY_MAX_POINTS}")
                    all_rows.extend(first_rows)
                    page = 2
                    while len(all_rows) < total:
                        rows, _, _ = repo.list_running_records(
                            detector_id=detector_id,
                            start_time=command.start_time,
                            end_time=command.end_time,
                            page=page,
                            per_page=100,
                            sort_by="recorded_at",
                            sort_direction=command.sort_direction,
                        )
                        if not rows:
                            break
                        all_rows.extend(rows)
                        page += 1
                uow.commit()
            reverse = command.sort_direction.upper() == "DESC"
            ordered = sorted(all_rows, key=lambda row: (str(row["recorded_at"]), int(row["id"])), reverse=reverse)
            pagination = Pagination(page=command.page, per_page=command.per_page)
            start = pagination.offset
            end = start + pagination.limit
            return ServiceResult.ok(Page(tuple(_history_point(row) for row in ordered[start:end]), pagination, total))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="历史曲线查询失败")


def _series_point(reading: Any) -> RealtimeSeriesPoint:
    return RealtimeSeriesPoint(
        detector_id=_positive_int(_value(reading, "detector_id"), "detector_id"),
        status=_optional_text(_value(reading, "status")),
        concentration=_optional_float(_value(reading, "concentration")),
        unit=_optional_text(_value(reading, "unit")),
        timestamp=_timestamp(reading),
    )


def _history_point(row: Any) -> HistoryPointView:
    return HistoryPointView(
        id=int(row["id"]),
        detector_id=int(row["detector_id"]),
        recorded_at=str(row["recorded_at"]),
        status=str(row["status"]),
        concentration=None if row["concentration"] is None else float(row["concentration"]),
        gas_type=row["gas_type"],
        unit=row["unit"],
        position_code=row["position_code"],
        detector_name=row["detector_name"],
    )


def _validate_history_range(start_time: str, end_time: str) -> None:
    start = _parse_time(start_time)
    end = _parse_time(end_time)
    if start > end:
        raise ValueError("start_time must not be after end_time")
    if end - start > timedelta(days=MAX_QUERY_SPAN_DAYS):
        raise ValueError(f"time range must not exceed {MAX_QUERY_SPAN_DAYS} days")


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _timestamp(reading: Any) -> str | None:
    value = _value(reading, "timestamp")
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    parsed = _parse_time(str(value))
    return parsed.isoformat()


def _value(reading: Any, field: str) -> Any:
    if isinstance(reading, dict):
        return reading.get(field)
    return getattr(reading, field, None)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("concentration must be numeric")
    return float(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())[:120]


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=message)
