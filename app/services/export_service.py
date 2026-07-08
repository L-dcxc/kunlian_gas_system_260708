from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Sequence

from app.core.logging import get_logger
from app.services.errors import ErrorCode
from app.services.models import ServiceResult

ExportFormat = Literal["xlsx", "pdf", "print"]


@dataclass(frozen=True, slots=True)
class ExportColumn:
    key: str
    title: str


@dataclass(frozen=True, slots=True)
class ExportPayload:
    format: ExportFormat
    filename: str
    title: str
    columns: tuple[ExportColumn, ...]
    rows: tuple[dict[str, str], ...]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ExportService:
    def __init__(self) -> None:
        self._logger = get_logger("services.export_service")

    def build_table_export(
        self,
        *,
        title: str,
        filename_prefix: str,
        columns: Sequence[ExportColumn],
        rows: Sequence[dict[str, object]],
        export_format: ExportFormat,
    ) -> ServiceResult[ExportPayload]:
        try:
            safe_format = _format(export_format)
            safe_columns = _columns(columns)
            payload_rows = tuple(_export_row(row, safe_columns, safe_format) for row in rows)
            payload = ExportPayload(
                format=safe_format,
                filename=_filename(filename_prefix, safe_format),
                title=_text(title, 120),
                columns=safe_columns,
                rows=payload_rows,
            )
            return ServiceResult.ok(payload)
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=_text(str(exc), 200))
        except Exception as exc:
            self._logger.warning("export payload build failed: %s", exc.__class__.__name__)
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="导出数据准备失败")

    def build_record_export(
        self,
        *,
        record_type: str,
        rows: Sequence[dict[str, object]],
        export_format: ExportFormat,
    ) -> ServiceResult[ExportPayload]:
        config = _record_export_config(record_type)
        return self.build_table_export(
            title=config["title"],
            filename_prefix=config["filename_prefix"],
            columns=config["columns"],
            rows=rows,
            export_format=export_format,
        )

    def build_chart_export(
        self,
        *,
        rows: Sequence[dict[str, object]],
        export_format: ExportFormat,
    ) -> ServiceResult[ExportPayload]:
        return self.build_table_export(
            title="历史曲线明细",
            filename_prefix="chart_history",
            columns=_CHART_COLUMNS,
            rows=rows,
            export_format=export_format,
        )


_ALARM_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("start_time", "开始时间"),
    ExportColumn("end_time", "恢复时间"),
    ExportColumn("position_code", "位置编号"),
    ExportColumn("detector_name", "探测器"),
    ExportColumn("alarm_type", "报警类型"),
    ExportColumn("alarm_level", "报警级别"),
    ExportColumn("trigger_value", "触发值"),
    ExportColumn("status", "状态"),
)
_RUNNING_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("recorded_at", "记录时间"),
    ExportColumn("position_code", "位置编号"),
    ExportColumn("detector_name", "探测器"),
    ExportColumn("status", "状态"),
    ExportColumn("concentration", "浓度"),
    ExportColumn("gas_type", "气体类型"),
    ExportColumn("unit", "单位"),
)
_OPERATION_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("created_at", "时间"),
    ExportColumn("actor_name", "用户"),
    ExportColumn("action_type", "日志类型"),
    ExportColumn("result", "结果"),
    ExportColumn("target_type", "对象类型"),
    ExportColumn("target_id", "对象 ID"),
    ExportColumn("summary", "内容"),
)
_CHART_COLUMNS = (
    ExportColumn("recorded_at", "记录时间"),
    ExportColumn("detector_id", "探测器 ID"),
    ExportColumn("position_code", "位置编号"),
    ExportColumn("status", "状态"),
    ExportColumn("concentration", "浓度"),
    ExportColumn("unit", "单位"),
)


def _record_export_config(record_type: str) -> dict[str, object]:
    if record_type == "alarm":
        return {"title": "报警记录", "filename_prefix": "alarm_records", "columns": _ALARM_COLUMNS}
    if record_type == "running":
        return {"title": "运行记录", "filename_prefix": "running_records", "columns": _RUNNING_COLUMNS}
    if record_type == "operation":
        return {"title": "操作记录", "filename_prefix": "operation_records", "columns": _OPERATION_COLUMNS}
    raise ValueError("unsupported record type")


def _export_row(row: dict[str, object], columns: tuple[ExportColumn, ...], export_format: ExportFormat) -> dict[str, str]:
    safe: dict[str, str] = {}
    for column in columns:
        value = _cell(row.get(column.key))
        if export_format == "xlsx":
            value = _neutralize_formula(value)
        else:
            # PDF/print renderers receive escaped text only; template code must not reinterpret it as markup.
            value = html.escape(value, quote=True)
        safe[column.key] = value
    return safe


def _cell(value: object) -> str:
    if value is None:
        return ""
    return _text(str(value), 1000)


def _neutralize_formula(value: str) -> str:
    # Spreadsheet tools may evaluate formulas from imported text; prefixing a quote preserves text intent.
    if value and (value[0] in {"=", "+", "-", "@"} or value.lstrip()[:1] in {"=", "+", "-", "@"}):
        return "'" + value
    return value


def _format(value: str) -> ExportFormat:
    if value not in {"xlsx", "pdf", "print"}:
        raise ValueError("unsupported export format")
    return value  # type: ignore[return-value]


def _columns(columns: Sequence[ExportColumn]) -> tuple[ExportColumn, ...]:
    if not columns:
        raise ValueError("export columns are required")
    safe: list[ExportColumn] = []
    for column in columns:
        key = _code(column.key, 80)
        title = _text(column.title, 120)
        safe.append(ExportColumn(key, title))
    return tuple(safe)


def _filename(prefix: str, export_format: ExportFormat) -> str:
    safe_prefix = _code(prefix, 80).replace(".", "_")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = "html" if export_format == "print" else export_format
    return f"{safe_prefix}_{timestamp}.{suffix}"


def _code(value: str, max_length: int) -> str:
    text = _text(value, max_length)
    if not text or not text.replace("_", "").replace(":", "").replace(".", "").replace("-", "").isalnum():
        raise ValueError("unsupported export identifier")
    return text


def _text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value must be a string")
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:max_length]
