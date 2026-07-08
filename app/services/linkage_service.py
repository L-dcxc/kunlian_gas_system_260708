from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.db.connection import Database
from app.db.repositories.device_config_repository import DetectorRepository
from app.db.repositories.linkage_repository import LinkageObjectRepository, LinkageRecordRepository, LinkageRuleRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.models import DeviceReading, ServiceResult
from app.services.permissions import Permission

LINKAGE_ALARM_TYPES = frozenset({"alarm_low", "alarm_high", "over_range", "fault", "offline", "disabled", "warming", "*"})


@dataclass(frozen=True, slots=True)
class LinkageObjectCommand:
    object_type: str
    name: str
    location: str | None = None
    adapter_type: str = "simulated"
    is_enabled: bool = True
    id: int | None = None


@dataclass(frozen=True, slots=True)
class LinkageRuleCommand:
    name: str
    object_id: int
    action: str
    detector_id: int | None = None
    alarm_type: str = "*"
    alarm_level: int | None = None
    trigger_delay_sec: int = 0
    recovery_action: str | None = None
    is_enabled: bool = True
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ManualLinkageCommand:
    object_id: int
    action: str
    message: str = "手动联动模拟执行。"


class LinkageService:
    def __init__(self, database: Database, session_store: SessionStore | None = None) -> None:
        self._database = database
        self._session_store = session_store

    def list_objects(self) -> tuple[dict[str, object], ...]:
        with UnitOfWork(self._database) as uow:
            rows = LinkageObjectRepository(uow).list_active()
            uow.commit()
        return tuple(_row_dict(row) for row in rows)

    def list_rules(self) -> tuple[dict[str, object], ...]:
        with UnitOfWork(self._database) as uow:
            rows = LinkageRuleRepository(uow).list_active()
            uow.commit()
        return tuple(_row_dict(row) for row in rows)

    def save_object(self, session_or_id: Session | str, command: LinkageObjectCommand) -> ServiceResult[dict[str, object]]:
        actor = self._require_system_settings(session_or_id, "保存联动对象")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = _object_values(command)
            with UnitOfWork(self._database) as uow:
                repo = LinkageObjectRepository(uow)
                duplicate = repo.find_active_by_name(str(values["name"]))
                if duplicate is not None and (command.id is None or int(duplicate["id"]) != command.id):
                    return _conflict("联动对象名称已存在")
                if command.id is None:
                    object_id = repo.create(values)
                    action = "linkage.object.create"
                else:
                    if repo.find_active_by_id(command.id) is None:
                        return _not_found("联动对象不存在")
                    repo.update(command.id, values)
                    object_id = command.id
                    action = "linkage.object.update"
                row = repo.find_active_by_id(object_id)
                _add_log(uow, actor, action, "linkage_object", object_id, "保存联动对象。", {"name": values["name"]})
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def delete_object(self, session_or_id: Session | str, object_id: int) -> ServiceResult[None]:
        actor = self._require_system_settings(session_or_id, f"删除联动对象 {object_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(object_id):
            return _validation("联动对象 ID 无效")
        with UnitOfWork(self._database) as uow:
            repo = LinkageObjectRepository(uow)
            if repo.find_active_by_id(object_id) is None:
                return _not_found("联动对象不存在")
            repo.soft_delete(object_id)
            _add_log(uow, actor, "linkage.object.delete", "linkage_object", object_id, "删除联动对象。")
            uow.commit()
        return ServiceResult.ok(None)

    def save_rule(self, session_or_id: Session | str, command: LinkageRuleCommand) -> ServiceResult[dict[str, object]]:
        actor = self._require_system_settings(session_or_id, "保存联动规则")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = _rule_values(command)
            with UnitOfWork(self._database) as uow:
                objects = LinkageObjectRepository(uow)
                detectors = DetectorRepository(uow)
                rules = LinkageRuleRepository(uow)
                if objects.find_active_by_id(int(values["object_id"])) is None:
                    return _validation("联动对象不存在")
                if values.get("detector_id") is not None and detectors.find_active_by_id(int(values["detector_id"])) is None:
                    return _validation("探测器不存在")
                if command.id is None:
                    rule_id = rules.create(values)
                    action = "linkage.rule.create"
                else:
                    if rules.find_active_by_id(command.id) is None:
                        return _not_found("联动规则不存在")
                    rules.update(command.id, values)
                    rule_id = command.id
                    action = "linkage.rule.update"
                row = rules.find_active_by_id(rule_id)
                _add_log(uow, actor, action, "linkage_rule", rule_id, "保存联动规则。", {"name": values["name"]})
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def delete_rule(self, session_or_id: Session | str, rule_id: int) -> ServiceResult[None]:
        actor = self._require_system_settings(session_or_id, f"删除联动规则 {rule_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(rule_id):
            return _validation("联动规则 ID 无效")
        with UnitOfWork(self._database) as uow:
            repo = LinkageRuleRepository(uow)
            if repo.find_active_by_id(rule_id) is None:
                return _not_found("联动规则不存在")
            repo.soft_delete(rule_id)
            _add_log(uow, actor, "linkage.rule.delete", "linkage_rule", rule_id, "删除联动规则。")
            uow.commit()
        return ServiceResult.ok(None)

    def manual_control(self, session_or_id: Session | str, command: ManualLinkageCommand) -> ServiceResult[dict[str, object]]:
        actor = self._require_manual_control(session_or_id, f"手动联动 {command.object_id}")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            object_id = _positive_int(command.object_id, "object_id")
            action = _code(command.action, 80, "action")
            message = _optional_text(command.message, 300, "message") or "手动联动模拟执行。"
            with UnitOfWork(self._database) as uow:
                if LinkageObjectRepository(uow).find_active_by_id(object_id) is None:
                    return _not_found("联动对象不存在")
                record_id, _ = LinkageRecordRepository(uow).add(
                    object_id=object_id,
                    action=action,
                    trigger_reason="manual",
                    result="simulated_success",
                    message=message,
                    user_id=actor.user_id,
                    user_name=actor.username,
                )
                row = LinkageRecordRepository(uow).find_by_id(record_id)
                _add_log(
                    uow,
                    actor,
                    "linkage.manual_control",
                    "linkage_object",
                    object_id,
                    "手动联动模拟执行。",
                    {"record_id": record_id, "action": action},
                )
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def trigger_for_alarm(self, uow: UnitOfWork, *, alarm_row, reading: DeviceReading) -> tuple[int, ...]:
        alarm_id = int(alarm_row["id"])
        alarm_type = str(alarm_row["alarm_type"])
        rules = LinkageRuleRepository(uow).list_matching(
            detector_id=reading.detector_id,
            alarm_type=alarm_type,
            alarm_level=reading.alarm_level,
        )
        record_ids: list[int] = []
        records = LinkageRecordRepository(uow)
        for rule in rules:
            # Real IO protocol is not available yet; automatic linkage is persisted
            # as a simulated action and deduplicated by alarm_id + rule_id.
            record_id, created = records.add(
                object_id=int(rule["object_id"]),
                rule_id=int(rule["id"]),
                alarm_record_id=alarm_id,
                action=str(rule["action"]),
                trigger_reason="automatic_alarm",
                result="simulated_success",
                message="自动联动模拟触发。",
            )
            if created:
                record_ids.append(record_id)
        return tuple(record_ids)

    def _require_system_settings(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        if self._session_store is None:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="权限校验未配置")
        try:
            return self._session_store.require_permission(
                self._database,
                session_or_id,
                Permission.SYSTEM_SETTINGS.value,
                target_summary,
            )
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))

    def _require_manual_control(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        if self._session_store is None:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="权限校验未配置")
        try:
            return self._session_store.require_permission(
                self._database,
                session_or_id,
                Permission.LINKAGE_MANUAL_CONTROL.value,
                target_summary,
            )
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))


def _object_values(command: LinkageObjectCommand) -> dict[str, object]:
    return {
        "object_type": _code(command.object_type, 40, "object_type"),
        "name": _text(command.name, 120, "name"),
        "location": _optional_text(command.location, 200, "location"),
        "adapter_type": _choice(command.adapter_type, {"simulated", "real"}, "adapter_type"),
        "is_enabled": _bool(command.is_enabled, "is_enabled"),
    }


def _rule_values(command: LinkageRuleCommand) -> dict[str, object]:
    return {
        "name": _text(command.name, 120, "name"),
        "detector_id": _optional_positive_int(command.detector_id, "detector_id"),
        "alarm_type": _alarm_type(command.alarm_type),
        "alarm_level": _optional_non_negative_int(command.alarm_level, "alarm_level"),
        "object_id": _positive_int(command.object_id, "object_id"),
        "action": _code(command.action, 80, "action"),
        "trigger_delay_sec": _int_range(command.trigger_delay_sec, 0, 86400, "trigger_delay_sec"),
        "recovery_action": _optional_code(command.recovery_action, 80, "recovery_action"),
        "is_enabled": _bool(command.is_enabled, "is_enabled"),
    }


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


def _row_dict(row) -> dict[str, object]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=message)


def _conflict(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=message)


def _not_found(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message=message)


def _valid_id(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _positive_int(value: object, field: str) -> int:
    if not _valid_id(value):
        raise ValueError(f"{field}:必须为正整数")
    return int(value)


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field)


def _optional_non_negative_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field}:必须大于等于 0")
    return value


def _int_range(value: object, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{field}:必须在 {minimum}..{maximum} 范围内")
    return value


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field}:必须为布尔值")
    return value


def _choice(value: object, choices: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{field}:取值不受支持")
    return value


def _alarm_type(value: object) -> str:
    if not isinstance(value, str) or value not in LINKAGE_ALARM_TYPES:
        raise ValueError("alarm_type:取值不受支持")
    return value


def _optional_code(value: object, max_length: int, field: str) -> str | None:
    if value is None:
        return None
    return _code(value, max_length, field)


def _code(value: object, max_length: int, field: str) -> str:
    text = _text(value, max_length, field)
    if not text.replace("_", "").replace(":", "").replace(".", "").replace("-", "").isalnum():
        raise ValueError(f"{field}:包含不支持字符")
    return text


def _optional_text(value: object, max_length: int, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, max_length, field)


def _text(value: object, max_length: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}:必须为文本")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if not normalized:
        raise ValueError(f"{field}:不能为空")
    if len(normalized) > max_length:
        raise ValueError(f"{field}:长度超出限制")
    return normalized
