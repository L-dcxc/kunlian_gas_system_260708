from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from app.db.connection import Database
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.record_repository import MAX_EXPORT_ROWS, RecordRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.export_service import ExportPayload, ExportService
from app.services.models import Page, Pagination, ServiceResult
from app.services.permissions import Permission

RecordType = Literal["alarm", "running", "operation"]


@dataclass(frozen=True, slots=True)
class RecordQuery:
    record_type: RecordType
    filters: dict[str, object] = field(default_factory=dict)
    page: int = 1
    per_page: int = 20
    sort_by: str | None = None
    sort_direction: str = "DESC"


@dataclass(frozen=True, slots=True)
class ClearRecordsCommand:
    record_type: RecordType
    filters: dict[str, object] = field(default_factory=dict)
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ExportRecordsCommand:
    record_type: RecordType
    filters: dict[str, object] = field(default_factory=dict)
    export_format: Literal["xlsx", "pdf", "print"] = "xlsx"
    sort_by: str | None = None
    sort_direction: str = "DESC"
    max_rows: int = MAX_EXPORT_ROWS


@dataclass(frozen=True, slots=True)
class ClearResult:
    record_type: RecordType
    deleted_count: int


class RecordService:
    def __init__(
        self,
        database: Database,
        session_store: SessionStore | None = None,
        export_service: ExportService | None = None,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._export_service = export_service or ExportService()

    def query_records(self, session_or_id: Session | str, query: RecordQuery) -> ServiceResult[Page[dict[str, object]]]:
        actor = self._require_view(session_or_id, f"查询{_record_label(query.record_type)}")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            with UnitOfWork(self._database) as uow:
                rows, repo_page, total = RecordRepository(uow).list_records(
                    _record_type(query.record_type),
                    filters=_safe_filters(query.filters),
                    page=query.page,
                    per_page=query.per_page,
                    sort_by=query.sort_by,
                    sort_direction=query.sort_direction,
                )
                uow.commit()
            page = Pagination(page=repo_page.page, per_page=repo_page.per_page)
            return ServiceResult.ok(Page(tuple(_row_dict(row) for row in rows), page, total))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="记录查询失败")

    def query_alarm_records(self, session_or_id: Session | str, **kwargs: object) -> ServiceResult[Page[dict[str, object]]]:
        return self.query_records(session_or_id, RecordQuery(record_type="alarm", **kwargs))

    def query_running_records(self, session_or_id: Session | str, **kwargs: object) -> ServiceResult[Page[dict[str, object]]]:
        return self.query_records(session_or_id, RecordQuery(record_type="running", **kwargs))

    def query_operation_records(self, session_or_id: Session | str, **kwargs: object) -> ServiceResult[Page[dict[str, object]]]:
        return self.query_records(session_or_id, RecordQuery(record_type="operation", **kwargs))

    def delete_record(
        self,
        session_or_id: Session | str,
        *,
        record_type: RecordType,
        record_id: int,
        confirmed: bool = False,
    ) -> ServiceResult[None]:
        if not confirmed:
            return _validation("删除记录需要显式确认")
        actor = self._require_delete(session_or_id, f"删除{_record_label(record_type)}")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            with UnitOfWork(self._database) as uow:
                repo = RecordRepository(uow)
                deleted = repo.delete_record(_record_type(record_type), record_id)
                if deleted == 0:
                    return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="记录不存在")
                _add_log(
                    uow,
                    actor,
                    "records.delete",
                    _record_type(record_type),
                    record_id,
                    "删除记录。",
                    {"record_type": _record_type(record_type), "deleted_count": deleted},
                )
                uow.commit()
            return ServiceResult.ok(None)
        except ValueError as exc:
            return _validation(str(exc))
        except sqlite3.DatabaseError:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="删除记录失败")

    def clear_records(self, session_or_id: Session | str, command: ClearRecordsCommand) -> ServiceResult[ClearResult]:
        if not command.confirmed:
            return _validation("清空记录需要显式确认")
        actor = self._require_clear(session_or_id, f"清空{_record_label(command.record_type)}")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            filters = _safe_filters(command.filters)
            with UnitOfWork(self._database) as uow:
                deleted = RecordRepository(uow).clear_records(_record_type(command.record_type), filters=filters)
                _add_log(
                    uow,
                    actor,
                    "records.clear",
                    _record_type(command.record_type),
                    None,
                    "清空记录。",
                    {
                        "record_type": _record_type(command.record_type),
                        "deleted_count": deleted,
                        "filter_fields": ",".join(sorted(str(key) for key in filters)),
                    },
                )
                uow.commit()
            return ServiceResult.ok(ClearResult(_record_type(command.record_type), deleted))
        except ValueError as exc:
            return _validation(str(exc))
        except sqlite3.DatabaseError:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="清空记录失败")

    def export_records(self, session_or_id: Session | str, command: ExportRecordsCommand) -> ServiceResult[ExportPayload]:
        actor = self._require_view(session_or_id, f"导出{_record_label(command.record_type)}")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            with UnitOfWork(self._database) as uow:
                rows = RecordRepository(uow).export_records(
                    _record_type(command.record_type),
                    filters=_safe_filters(command.filters),
                    sort_by=command.sort_by,
                    sort_direction=command.sort_direction,
                    limit=command.max_rows,
                )
                uow.commit()
            return self._export_service.build_record_export(
                record_type=_record_type(command.record_type),
                rows=tuple(_row_dict(row) for row in rows),
                export_format=command.export_format,
            )
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="导出数据准备失败")

    def _require_view(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        return self._require_permission(session_or_id, Permission.RECORD_VIEW.value, target_summary)

    def _require_delete(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        return self._require_permission(session_or_id, Permission.RECORD_DELETE.value, target_summary)

    def _require_clear(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        return self._require_permission(session_or_id, Permission.RECORD_CLEAR.value, target_summary)

    def _require_permission(self, session_or_id: Session | str, action: str, target_summary: str) -> Session | ServiceResult:
        if self._session_store is None:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="权限校验未配置")
        try:
            return self._session_store.require_permission(self._database, session_or_id, action, target_summary)
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))


def _add_log(
    uow: UnitOfWork,
    actor: Session,
    action_type: str,
    target_type: str,
    target_id: int | str | None,
    summary: str,
    details: dict[str, object] | None = None,
) -> None:
    OperationLogRepository(uow).add(
        action_type=action_type,
        result="success",
        actor_id=actor.user_id,
        actor_name=actor.username,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        summary=summary,
        details=details or {},
    )


def _safe_filters(filters: dict[str, object]) -> dict[str, object]:
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    if len(filters) > 20:
        raise ValueError("too many filters")
    return dict(filters)


def _row_dict(row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def _record_type(value: str) -> RecordType:
    if value not in {"alarm", "running", "operation"}:
        raise ValueError("unsupported record type")
    return value  # type: ignore[return-value]


def _record_label(record_type: str) -> str:
    labels = {"alarm": "报警记录", "running": "运行记录", "operation": "操作记录"}
    return labels.get(record_type, "记录")


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=message)
