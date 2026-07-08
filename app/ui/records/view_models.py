from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from app.services.models import ServiceResult
from app.services.record_service import RecordQuery
from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import normalize_plain_text
from app.ui.records.record_filters import (
    DEFAULT_RECORDS_PER_PAGE,
    FILTER_WHITELISTS,
    MAX_RECORDS_PER_PAGE,
    RECORD_TYPE_LABELS,
    RecordFilterValues,
    RecordType,
)

QUERY_FAILED_TEXT = "记录查询失败"


@dataclass(frozen=True, slots=True)
class PaginationState:
    page: int = 1
    per_page: int = DEFAULT_RECORDS_PER_PAGE
    total: int = 0

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be greater than or equal to 1")
        object.__setattr__(self, "per_page", _bounded_per_page(self.per_page))
        if self.total < 0:
            raise ValueError("total must be greater than or equal to 0")


@dataclass(frozen=True, slots=True)
class RecordRowsPage:
    rows: tuple[dict[str, object], ...] = ()
    pagination: PaginationState = field(default_factory=PaginationState)


class RecordQueryViewModel:
    def __init__(self, record_service: object, session: object | None, record_type: RecordType) -> None:
        self.record_service = record_service
        self.session = session
        self.record_type = _record_type(record_type)
        self.current_filters: dict[str, object] = {}
        self.pagination = PaginationState()
        self.last_error = ""
        self.last_result: object | None = None
        self.is_querying = False

    def build_query(self, values: RecordFilterValues, *, page: int | None = None, per_page: int | None = None) -> RecordQuery:
        if values.record_type != self.record_type:
            raise ValueError("record type mismatch")
        safe_page = _positive_page(values.page if page is None else page)
        safe_per_page = _bounded_per_page(values.per_page if per_page is None else per_page)
        return RecordQuery(
            record_type=self.record_type,
            filters=self.whitelist_filters(values.filters),
            page=safe_page,
            per_page=safe_per_page,
            sort_by=_default_sort(self.record_type),
            sort_direction="DESC",
        )

    def whitelist_filters(self, filters: Mapping[str, object]) -> dict[str, object]:
        allowed = FILTER_WHITELISTS[self.record_type]
        safe: dict[str, object] = {}
        for key, value in filters.items():
            if key not in allowed or value in {None, ""}:
                continue
            if key in {"keyword", "username", "position_code"}:
                safe[key] = normalize_plain_text(value, max_chars=120)
            else:
                safe[key] = value
        return safe

    def query(self, values: RecordFilterValues) -> ServiceResult[RecordRowsPage]:
        if self.is_querying:
            return ServiceResult.fail(400, "正在查询，请勿重复提交")
        try:
            command = self.build_query(values)
        except ValueError as exc:
            message = controlled_error_text(str(exc), fallback="输入内容校验失败，请检查后重试。")
            return ServiceResult.fail(400, message)
        self.is_querying = True
        self.last_error = ""
        self.current_filters = dict(command.filters)
        self.pagination = PaginationState(command.page, command.per_page, self.pagination.total)
        try:
            result = self._call_query(command)
        except Exception:
            result = ServiceResult.fail(500, QUERY_FAILED_TEXT)
        finally:
            self.is_querying = False
        self.last_result = result
        if not _result_success(result):
            self.last_error = controlled_error_text(_result_message(result), fallback=QUERY_FAILED_TEXT)
            return ServiceResult.fail(int(getattr(result, "code", 500) or 500), self.last_error)
        rows, pagination = _extract_page(_result_data(result), command.page, command.per_page)
        self.pagination = pagination
        return ServiceResult.ok(RecordRowsPage(rows, pagination))

    def query_page(self, *, page: int, per_page: int | None = None) -> ServiceResult[RecordRowsPage]:
        values = RecordFilterValues(
            self.record_type,
            dict(self.current_filters),
            page=_positive_page(page),
            per_page=_bounded_per_page(self.pagination.per_page if per_page is None else per_page),
        )
        return self.query(values)

    def export_command_filters(self) -> dict[str, object]:
        return dict(self.current_filters)

    def _call_query(self, command: RecordQuery) -> object:
        query_records = getattr(self.record_service, "query_records", None)
        if query_records is not None:
            return query_records(self.session, command)
        method_name = {
            "alarm": "query_alarm_records",
            "running": "query_running_records",
            "operation": "query_operation_records",
        }[self.record_type]
        query_method = getattr(self.record_service, method_name)
        return query_method(self.session, filters=command.filters, page=command.page, per_page=command.per_page)


def table_columns(record_type: RecordType) -> tuple[dict[str, object], ...]:
    normalized = _record_type(record_type)
    if normalized == "alarm":
        return (
            {"key": "id", "title": "ID", "width": 64},
            {"key": "start_time", "title": "开始时间", "width": 170},
            {"key": "end_time", "title": "恢复时间", "width": 170},
            {"key": "position_code", "title": "位置编号", "width": 110},
            {"key": "detector_name", "title": "探测器", "width": 140},
            {"key": "controller_name", "title": "控制器", "width": 120},
            {"key": "alarm_type", "title": "报警类型", "width": 100},
            {"key": "alarm_level", "title": "级别", "width": 70},
            {"key": "trigger_value", "title": "触发值", "width": 90},
            {"key": "status", "title": "状态", "width": 90},
        )
    if normalized == "running":
        return (
            {"key": "id", "title": "ID", "width": 64},
            {"key": "recorded_at", "title": "记录时间", "width": 170},
            {"key": "position_code", "title": "位置编号", "width": 110},
            {"key": "detector_name", "title": "探测器", "width": 140},
            {"key": "controller_name", "title": "控制器", "width": 120},
            {"key": "status", "title": "状态", "width": 100},
            {"key": "concentration", "title": "浓度", "width": 90},
            {"key": "gas_type", "title": "气体类型", "width": 100},
            {"key": "unit", "title": "单位", "width": 80},
        )
    return (
        {"key": "id", "title": "ID", "width": 64},
        {"key": "created_at", "title": "时间", "width": 170},
        {"key": "actor_name", "title": "用户", "width": 120},
        {"key": "action_type", "title": "日志类型", "width": 150},
        {"key": "result", "title": "结果", "width": 90},
        {"key": "target_type", "title": "对象类型", "width": 110},
        {"key": "target_id", "title": "对象 ID", "width": 90},
        {"key": "summary", "title": "内容", "width": 260},
    )


def rows_for_table(items: Sequence[object]) -> tuple[dict[str, object], ...]:
    return tuple(_row_dict(item) for item in items)


def _row_dict(item: object) -> dict[str, object]:
    if isinstance(item, Mapping):
        return {str(key): value for key, value in item.items()}
    if hasattr(item, "keys") and hasattr(item, "__getitem__"):
        try:
            return {str(key): item[key] for key in item.keys()}
        except Exception:
            pass
    data: dict[str, object] = {}
    for key in (
        "id",
        "start_time",
        "end_time",
        "recorded_at",
        "created_at",
        "position_code",
        "detector_name",
        "controller_name",
        "alarm_type",
        "alarm_level",
        "trigger_value",
        "status",
        "concentration",
        "gas_type",
        "unit",
        "actor_name",
        "action_type",
        "result",
        "target_type",
        "target_id",
        "summary",
    ):
        if hasattr(item, key):
            data[key] = getattr(item, key)
    return data


def _extract_page(data: object, fallback_page: int, fallback_per_page: int) -> tuple[tuple[dict[str, object], ...], PaginationState]:
    if data is None:
        return (), PaginationState(fallback_page, fallback_per_page, 0)
    items = getattr(data, "items", None)
    total = getattr(data, "total", None)
    pagination = getattr(data, "pagination", None)
    if items is None and isinstance(data, tuple) and len(data) == 2:
        items, total = data
    if items is None and isinstance(data, list):
        items = data
        total = len(data)
    rows = rows_for_table(tuple(items or ()))
    page = int(getattr(pagination, "page", fallback_page)) if pagination is not None else fallback_page
    per_page = int(getattr(pagination, "per_page", fallback_per_page)) if pagination is not None else fallback_per_page
    return rows, PaginationState(page, per_page, int(total if total is not None else len(rows)))


def _positive_page(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("page must be greater than or equal to 1") from exc
    return max(parsed, 1)


def _bounded_per_page(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("per_page must be between 1 and 100") from exc
    return min(max(parsed, 1), MAX_RECORDS_PER_PAGE)


def _result_success(result: object) -> bool:
    return bool(getattr(result, "success", False))


def _result_data(result: object) -> object:
    return getattr(result, "data", None)


def _result_message(result: object) -> object:
    return getattr(result, "message", "")


def _default_sort(record_type: RecordType) -> str:
    return {"alarm": "start_time", "running": "recorded_at", "operation": "created_at"}[_record_type(record_type)]


def _record_type(value: str) -> RecordType:
    if value not in RECORD_TYPE_LABELS:
        raise ValueError("unsupported record type")
    return value  # type: ignore[return-value]
