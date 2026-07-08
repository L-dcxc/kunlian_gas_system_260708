from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Literal

from app.core.event_bus import EventBus
from app.core.logging import get_logger, user_safe_error
from app.core.scheduler import Scheduler
from app.db.connection import Database
from app.db.repositories.device_config_repository import DetectorRepository
from app.db.repositories.maintenance_repository import MaintenanceRepository, row_to_dict
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Permission

MaintenancePlanType = Literal["sensor_life", "calibration", "custom"]
MaintenancePlanStatus = Literal["active", "completed", "cancelled"]
ReminderStatus = Literal["due_soon", "overdue"]

MAINTENANCE_REMINDERS_DUE_EVENT = "maintenance.reminders_due"
MAINTENANCE_REMINDER_JOB = "maintenance.reminders"
DEFAULT_SENSOR_LIFE_REMIND_DAYS = 30
DEFAULT_CALIBRATION_REMIND_DAYS = 7
DEFAULT_SCHEDULE_INTERVAL_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class MaintenancePlanCommand:
    detector_id: int
    plan_type: MaintenancePlanType
    due_at: str | datetime
    remind_days_before: int = 7
    status: MaintenancePlanStatus = "active"
    notes: str = ""


@dataclass(frozen=True, slots=True)
class MaintenancePlanView:
    id: int
    detector_id: int
    plan_type: str
    due_at: str
    remind_days_before: int
    status: str
    notes: str
    detector_position_code: str | None = None
    detector_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceReminderView:
    source: str
    detector_id: int
    detector_position_code: str | None
    detector_name: str | None
    plan_type: str
    due_at: str
    remind_days_before: int
    status: ReminderStatus
    days_until_due: int
    plan_id: int | None = None
    notes: str = ""


class MaintenanceService:
    def __init__(
        self,
        database: Database,
        session_store: SessionStore | None = None,
        *,
        scheduler: Scheduler | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._scheduler = scheduler
        self._event_bus = event_bus
        self._schedule_lock = threading.RLock()
        self._scheduled_registered = False
        self._logger = get_logger("services.maintenance")

    def list_plans(self, session_or_id: Session | str, *, status: str | None = None) -> ServiceResult[tuple[MaintenancePlanView, ...]]:
        actor = self._require_view(session_or_id, "查看维护计划")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            with UnitOfWork(self._database) as uow:
                rows = MaintenanceRepository(uow).list_active_with_detectors(status=status)
                uow.commit()
            return ServiceResult.ok(tuple(_plan_view(row_to_dict(row)) for row in rows))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception as exc:
            self._logger.error("maintenance plan list failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="维护计划读取失败")

    def get_plan(self, session_or_id: Session | str, plan_id: int) -> ServiceResult[MaintenancePlanView]:
        actor = self._require_view(session_or_id, f"查看维护计划 {plan_id}")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            with UnitOfWork(self._database) as uow:
                row = MaintenanceRepository(uow).find_active_with_detector(plan_id)
                uow.commit()
            if row is None:
                return _not_found("维护计划不存在")
            return ServiceResult.ok(_plan_view(row_to_dict(row)))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception as exc:
            self._logger.error("maintenance plan get failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="维护计划读取失败")

    def create_plan(self, session_or_id: Session | str, command: MaintenancePlanCommand) -> ServiceResult[MaintenancePlanView]:
        actor = self._require_manage(session_or_id, "新增维护计划")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = _command_values(command)
            with UnitOfWork(self._database) as uow:
                if DetectorRepository(uow).find_active_by_id(int(values["detector_id"])) is None:
                    return _validation("detector_id:探测器不存在")
                repo = MaintenanceRepository(uow)
                plan_id = repo.create(values, actor_id=actor.user_id)
                row = repo.find_active_with_detector(plan_id)
                _add_log(
                    uow,
                    actor,
                    "maintenance.plan.create",
                    plan_id,
                    "新增维护计划。",
                    {"detector_id": values["detector_id"], "plan_type": values["plan_type"]},
                )
                uow.commit()
            return ServiceResult.ok(_plan_view(row_to_dict(row)))
        except ValueError as exc:
            return _validation(str(exc))
        except sqlite3.IntegrityError:
            return _validation("维护计划数据校验失败")
        except Exception as exc:
            self._logger.error("maintenance plan create failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="维护计划保存失败")

    def update_plan(
        self,
        session_or_id: Session | str,
        plan_id: int,
        command: MaintenancePlanCommand,
    ) -> ServiceResult[MaintenancePlanView]:
        actor = self._require_manage(session_or_id, f"修改维护计划 {plan_id}")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = _command_values(command)
            with UnitOfWork(self._database) as uow:
                repo = MaintenanceRepository(uow)
                if repo.find_active_by_id(plan_id) is None:
                    return _not_found("维护计划不存在")
                if DetectorRepository(uow).find_active_by_id(int(values["detector_id"])) is None:
                    return _validation("detector_id:探测器不存在")
                repo.update(plan_id, values, actor_id=actor.user_id)
                row = repo.find_active_with_detector(plan_id)
                _add_log(
                    uow,
                    actor,
                    "maintenance.plan.update",
                    plan_id,
                    "修改维护计划。",
                    {"detector_id": values["detector_id"], "plan_type": values["plan_type"], "status": values["status"]},
                )
                uow.commit()
            return ServiceResult.ok(_plan_view(row_to_dict(row)))
        except ValueError as exc:
            return _validation(str(exc))
        except sqlite3.IntegrityError:
            return _validation("维护计划数据校验失败")
        except Exception as exc:
            self._logger.error("maintenance plan update failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="维护计划保存失败")

    def view_due_reminders(
        self,
        session_or_id: Session | str,
        now: datetime | None = None,
    ) -> ServiceResult[tuple[MaintenanceReminderView, ...]]:
        actor = self._require_view(session_or_id, "查看维护提醒")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            return ServiceResult.ok(self.list_due_reminders(now=now))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception as exc:
            self._logger.error("maintenance reminders view failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="维护提醒读取失败")

    def list_due_reminders(self, now: datetime | None = None) -> tuple[MaintenanceReminderView, ...]:
        checked_at = _aware_utc(now or datetime.now(timezone.utc))
        with UnitOfWork(self._database) as uow:
            detector_rows = DetectorRepository(uow).list_active()
            plan_rows = MaintenanceRepository(uow).list_active_with_detectors(status="active")
            uow.commit()
        reminders: list[MaintenanceReminderView] = []
        for row in detector_rows:
            reminders.extend(_detector_reminders(row_to_dict(row), checked_at))
        for row in plan_rows:
            reminder = _plan_reminder(row_to_dict(row), checked_at)
            if reminder is not None:
                reminders.append(reminder)
        return tuple(sorted(reminders, key=lambda item: (item.due_at, item.detector_id, item.plan_id or 0, item.source)))

    def register_scheduled_reminders(
        self,
        *,
        interval_seconds: float = DEFAULT_SCHEDULE_INTERVAL_SECONDS,
        run_immediately: bool = False,
    ) -> ServiceResult[None]:
        if self._scheduler is None:
            return ServiceResult.fail(code=int(ErrorCode.SERVICE_UNAVAILABLE), message="维护提醒调度器未配置")
        try:
            with self._schedule_lock:
                self._scheduler.cancel(MAINTENANCE_REMINDER_JOB)
                self._scheduled_registered = False
                self._scheduler.every(
                    MAINTENANCE_REMINDER_JOB,
                    interval_seconds,
                    self._scheduled_callback,
                    run_immediately=run_immediately,
                )
                self._scheduled_registered = True
            return ServiceResult.ok(None)
        except Exception as exc:
            self._logger.error("maintenance reminder schedule register failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="维护提醒调度注册失败")

    def register_scheduled_reminder(self, **kwargs: object) -> ServiceResult[None]:
        return self.register_scheduled_reminders(**kwargs)

    def cancel_scheduled_reminders(self) -> None:
        with self._schedule_lock:
            if self._scheduler is not None:
                self._scheduler.cancel(MAINTENANCE_REMINDER_JOB)
            self._scheduled_registered = False

    def trigger_scheduled_reminders(self) -> ServiceResult[tuple[MaintenanceReminderView, ...]]:
        try:
            reminders = self.list_due_reminders()
            if reminders and self._event_bus is not None:
                # Scheduler integration is intentionally read-only: it publishes
                # reminders for UI consumers and never touches acquisition/alarm state.
                self._event_bus.publish(MAINTENANCE_REMINDERS_DUE_EVENT, reminders)
            return ServiceResult.ok(reminders)
        except Exception as exc:
            self._logger.error("maintenance reminder schedule failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="维护提醒计算失败")

    def _scheduled_callback(self) -> None:
        self.trigger_scheduled_reminders()

    def _require_view(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        return self._require_permission(session_or_id, Permission.MAINTENANCE_VIEW.value, target_summary)

    def _require_manage(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        return self._require_permission(session_or_id, Permission.MAINTENANCE_MANAGE.value, target_summary)

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
    plan_id: int,
    summary: str,
    details: dict[str, object] | None = None,
) -> None:
    OperationLogRepository(uow).add(
        action_type=action_type,
        result="success",
        actor_id=actor.user_id,
        actor_name=actor.username,
        target_type="maintenance_plan",
        target_id=str(plan_id),
        summary=summary,
        details=details or {},
    )


def _command_values(command: MaintenancePlanCommand) -> dict[str, object]:
    return {
        "detector_id": command.detector_id,
        "plan_type": command.plan_type,
        "due_at": command.due_at,
        "remind_days_before": command.remind_days_before,
        "status": command.status,
        "notes": command.notes,
    }


def _plan_view(row: dict[str, object]) -> MaintenancePlanView:
    return MaintenancePlanView(
        id=int(row["id"]),
        detector_id=int(row["detector_id"]),
        plan_type=str(row["plan_type"]),
        due_at=str(row["due_at"]),
        remind_days_before=int(row["remind_days_before"]),
        status=str(row["status"]),
        notes=str(row.get("notes") or ""),
        detector_position_code=_optional_str(row.get("detector_position_code")),
        detector_name=_optional_str(row.get("detector_name")),
        created_at=_optional_str(row.get("created_at")),
        updated_at=_optional_str(row.get("updated_at")),
    )


def _detector_reminders(row: dict[str, object], now: datetime) -> tuple[MaintenanceReminderView, ...]:
    reminders: list[MaintenanceReminderView] = []
    sensor_life_until = row.get("sensor_life_until")
    if sensor_life_until:
        reminder = _build_reminder(
            source="detector.sensor_life",
            detector=row,
            plan_type="sensor_life",
            due_at=_parse_datetime(sensor_life_until, "sensor_life_until"),
            remind_days_before=DEFAULT_SENSOR_LIFE_REMIND_DAYS,
            now=now,
        )
        if reminder is not None:
            reminders.append(reminder)
    cycle_days = row.get("calibration_cycle_days")
    if cycle_days is not None:
        # No last-calibration field exists yet; detector creation time is the only
        # stable persisted baseline for the first calibration-cycle reminder.
        due_at = _parse_datetime(row.get("created_at"), "created_at") + timedelta(days=int(cycle_days))
        reminder = _build_reminder(
            source="detector.calibration",
            detector=row,
            plan_type="calibration",
            due_at=due_at,
            remind_days_before=DEFAULT_CALIBRATION_REMIND_DAYS,
            now=now,
        )
        if reminder is not None:
            reminders.append(reminder)
    return tuple(reminders)


def _plan_reminder(row: dict[str, object], now: datetime) -> MaintenanceReminderView | None:
    if row.get("detector_deleted_at") is not None:
        return None
    detector = {
        "id": row["detector_id"],
        "position_code": row.get("detector_position_code"),
        "name": row.get("detector_name"),
    }
    return _build_reminder(
        source="maintenance_plan",
        detector=detector,
        plan_type=str(row["plan_type"]),
        due_at=_parse_datetime(row.get("due_at"), "due_at"),
        remind_days_before=int(row["remind_days_before"]),
        now=now,
        plan_id=int(row["id"]),
        notes=str(row.get("notes") or ""),
    )


def _build_reminder(
    *,
    source: str,
    detector: dict[str, object],
    plan_type: str,
    due_at: datetime,
    remind_days_before: int,
    now: datetime,
    plan_id: int | None = None,
    notes: str = "",
) -> MaintenanceReminderView | None:
    remind_at = due_at - timedelta(days=remind_days_before)
    if now < remind_at:
        return None
    seconds = (due_at - now).total_seconds()
    status: ReminderStatus = "overdue" if seconds < 0 else "due_soon"
    days_until = ceil(seconds / 86400) if seconds >= 0 else -ceil(abs(seconds) / 86400)
    return MaintenanceReminderView(
        source=source,
        detector_id=int(detector["id"]),
        detector_position_code=_optional_str(detector.get("position_code")),
        detector_name=_optional_str(detector.get("name")),
        plan_type=plan_type,
        due_at=due_at.isoformat(),
        remind_days_before=remind_days_before,
        status=status,
        days_until_due=days_until,
        plan_id=plan_id,
        notes=_safe_notes(notes),
    )


def _parse_datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return _aware_utc(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}:日期格式无效")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field}:日期格式无效") from exc
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("now:必须为日期时间")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_notes(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:1000]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=message)


def _not_found(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message=message)
