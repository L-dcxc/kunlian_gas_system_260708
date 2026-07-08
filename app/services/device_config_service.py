from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.db.connection import Database
from app.db.repositories.device_config_repository import (
    ControllerRepository,
    DetectorRepository,
    GasTypeRepository,
    PortRepository,
)
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.settings_repository import SettingsRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.file_validation import ImportRowError
from app.services.import_export import ImportExportService, ImportTemplate
from app.services.models import AcquisitionStatus, ProtocolMode, ServiceResult
from app.services.permissions import Permission

CHANNEL_TYPES = {"serial", "tcp"}
PARITIES = {"N", "E", "O"}
STOP_BITS = {1, 1.5, 2}
ALARM_TYPES = {"none", "low", "high", "low_high"}
DETECTOR_IMPORT_FIELDS = (
    "position_code",
    "name",
    "port_id",
    "controller_id",
    "protocol_address",
    "register_index",
    "gas_type_id",
    "unit",
    "range_min",
    "range_max",
    "alarm_low",
    "alarm_high",
    "store_interval_sec",
    "model",
    "alarm_type",
    "sound_enabled",
    "sensor_life_until",
    "calibration_cycle_days",
    "is_enabled",
)


@dataclass(frozen=True, slots=True)
class PortCommand:
    name: str
    channel_type: str
    serial_port_name: str | None = None
    baud_rate: int | None = None
    data_bits: int | None = None
    parity: str | None = None
    stop_bits: float | None = None
    tcp_host: str | None = None
    tcp_port: int | None = None
    poll_interval_ms: int = 1000
    timeout_ms: int = 1500
    failure_threshold: int = 3
    reconnect_interval_ms: int = 3000
    is_enabled: bool = True
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ControllerCommand:
    port_id: int
    name: str
    address: int
    model: str | None = None
    detector_count: int = 0
    is_enabled: bool = True
    id: int | None = None


@dataclass(frozen=True, slots=True)
class GasTypeCommand:
    name: str
    unit: str
    range_min: float
    range_max: float
    default_alarm_low: float | None = None
    default_alarm_high: float | None = None
    is_enabled: bool = True
    id: int | None = None


@dataclass(frozen=True, slots=True)
class DetectorCommand:
    port_id: int
    position_code: str
    name: str
    protocol_address: int
    register_index: int
    gas_type_id: int
    unit: str
    range_min: float
    range_max: float
    controller_id: int | None = None
    model: str | None = None
    alarm_low: float | None = None
    alarm_high: float | None = None
    alarm_type: str = "low_high"
    sound_enabled: bool = True
    store_interval_sec: int = 60
    sensor_life_until: str | None = None
    calibration_cycle_days: int | None = None
    is_enabled: bool = True
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ProtocolModeChangeResult:
    protocol_mode: str
    restart_required: bool
    message: str


@dataclass(frozen=True, slots=True)
class DetectorImportResult:
    imported_count: int
    errors: tuple[ImportRowError, ...]


class DeviceConfigService:
    def __init__(
        self,
        database: Database,
        session_store: SessionStore,
        *,
        import_export: ImportExportService | None = None,
        acquisition_status_provider: Callable[[], AcquisitionStatus | str] | None = None,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._import_export = import_export
        self._acquisition_status_provider = acquisition_status_provider or (lambda: AcquisitionStatus.STOPPED)

    def list_ports(self) -> tuple[dict[str, object], ...]:
        with UnitOfWork(self._database) as uow:
            rows = PortRepository(uow).list_active()
            uow.commit()
        return tuple(_row_dict(row) for row in rows)

    def list_controllers(self) -> tuple[dict[str, object], ...]:
        with UnitOfWork(self._database) as uow:
            rows = ControllerRepository(uow).list_active()
            uow.commit()
        return tuple(_row_dict(row) for row in rows)

    def list_gas_types(self) -> tuple[dict[str, object], ...]:
        with UnitOfWork(self._database) as uow:
            rows = GasTypeRepository(uow).list_active()
            uow.commit()
        return tuple(_row_dict(row) for row in rows)

    def list_detectors(self) -> tuple[dict[str, object], ...]:
        with UnitOfWork(self._database) as uow:
            rows = DetectorRepository(uow).list_active()
            uow.commit()
        return tuple(_row_dict(row) for row in rows)

    def configuration_snapshot(self) -> dict[str, object]:
        return {
            "protocol_mode": self.get_protocol_mode(),
            "ports": self.list_ports(),
            "controllers": self.list_controllers(),
            "gas_types": self.list_gas_types(),
            "detectors": self.list_detectors(),
        }

    def save_port(self, session_or_id: Session | str, command: PortCommand) -> ServiceResult[dict[str, object]]:
        actor = self._require_config_write(session_or_id, "保存端口配置")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = _normalize_port(command)
            with UnitOfWork(self._database) as uow:
                ports = PortRepository(uow)
                existing = ports.find_active_by_name(str(values["name"]))
                if existing is not None and (command.id is None or int(existing["id"]) != command.id):
                    return _conflict("端口名称已存在")
                if command.id is None:
                    row_id = ports.create(values)
                    action = "device_config.port.create"
                else:
                    row = ports.find_active_by_id(command.id)
                    if row is None:
                        return _not_found("端口不存在")
                    ports.update(command.id, values)
                    row_id = command.id
                    action = "device_config.port.update"
                row = ports.find_active_by_id(row_id)
                _add_log(uow, actor, action, "port", row_id, "保存端口配置。", {"name": values["name"]})
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def delete_port(self, session_or_id: Session | str, port_id: int) -> ServiceResult[None]:
        actor = self._require_config_write(session_or_id, f"删除端口 {port_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(port_id):
            return _validation("端口 ID 无效")
        with UnitOfWork(self._database) as uow:
            ports = PortRepository(uow)
            detectors = DetectorRepository(uow)
            if ports.find_active_by_id(port_id) is None:
                return _not_found("端口不存在")
            if detectors.count_for_port(port_id) > 0:
                return _conflict("端口已被探测器引用，当前按受控拒绝处理[待确认]")
            ports.soft_delete(port_id)
            _add_log(uow, actor, "device_config.port.delete", "port", port_id, "删除端口配置。")
            uow.commit()
        return ServiceResult.ok(None)

    def save_controller(
        self,
        session_or_id: Session | str,
        command: ControllerCommand,
    ) -> ServiceResult[dict[str, object]]:
        actor = self._require_config_write(session_or_id, "保存控制器配置")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = _normalize_controller(command)
            with UnitOfWork(self._database) as uow:
                ports = PortRepository(uow)
                controllers = ControllerRepository(uow)
                if ports.find_active_by_id(int(values["port_id"])) is None:
                    return _validation("端口不存在")
                duplicate = controllers.find_active_by_port_address(int(values["port_id"]), int(values["address"]))
                if duplicate is not None and (command.id is None or int(duplicate["id"]) != command.id):
                    return _conflict("同一端口下控制器地址已存在")
                if command.id is None:
                    row_id = controllers.create(values)
                    action = "device_config.controller.create"
                else:
                    if controllers.find_active_by_id(command.id) is None:
                        return _not_found("控制器不存在")
                    controllers.update(command.id, values)
                    row_id = command.id
                    action = "device_config.controller.update"
                row = controllers.find_active_by_id(row_id)
                _add_log(uow, actor, action, "controller", row_id, "保存控制器配置。", {"name": values["name"]})
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def delete_controller(self, session_or_id: Session | str, controller_id: int) -> ServiceResult[None]:
        actor = self._require_config_write(session_or_id, f"删除控制器 {controller_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(controller_id):
            return _validation("控制器 ID 无效")
        with UnitOfWork(self._database) as uow:
            controllers = ControllerRepository(uow)
            detectors = DetectorRepository(uow)
            if controllers.find_active_by_id(controller_id) is None:
                return _not_found("控制器不存在")
            if detectors.count_for_controller(controller_id) > 0:
                return _conflict("控制器已被探测器引用，当前按受控拒绝处理[待确认]")
            controllers.soft_delete(controller_id)
            _add_log(uow, actor, "device_config.controller.delete", "controller", controller_id, "删除控制器配置。")
            uow.commit()
        return ServiceResult.ok(None)

    def save_gas_type(self, session_or_id: Session | str, command: GasTypeCommand) -> ServiceResult[dict[str, object]]:
        actor = self._require_config_write(session_or_id, "保存气体类型")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = _normalize_gas_type(command)
            with UnitOfWork(self._database) as uow:
                repo = GasTypeRepository(uow)
                existing = repo.find_active_by_name(str(values["name"]))
                if existing is not None and (command.id is None or int(existing["id"]) != command.id):
                    return _conflict("气体类型名称已存在")
                if command.id is None:
                    row_id = repo.create(values)
                    action = "device_config.gas_type.create"
                else:
                    if repo.find_active_by_id(command.id) is None:
                        return _not_found("气体类型不存在")
                    repo.update(command.id, values)
                    row_id = command.id
                    action = "device_config.gas_type.update"
                row = repo.find_active_by_id(row_id)
                _add_log(uow, actor, action, "gas_type", row_id, "保存气体类型。", {"name": values["name"]})
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def delete_gas_type(self, session_or_id: Session | str, gas_type_id: int) -> ServiceResult[None]:
        actor = self._require_config_write(session_or_id, f"删除气体类型 {gas_type_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(gas_type_id):
            return _validation("气体类型 ID 无效")
        with UnitOfWork(self._database) as uow:
            repo = GasTypeRepository(uow)
            detectors = DetectorRepository(uow)
            if repo.find_active_by_id(gas_type_id) is None:
                return _not_found("气体类型不存在")
            if detectors.count_for_gas_type(gas_type_id) > 0:
                return _conflict("气体类型已被探测器引用，当前按受控拒绝处理[待确认]")
            repo.soft_delete(gas_type_id)
            _add_log(uow, actor, "device_config.gas_type.delete", "gas_type", gas_type_id, "删除气体类型。")
            uow.commit()
        return ServiceResult.ok(None)

    def save_detector(self, session_or_id: Session | str, command: DetectorCommand) -> ServiceResult[dict[str, object]]:
        actor = self._require_config_write(session_or_id, "保存探测器配置")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            with UnitOfWork(self._database) as uow:
                values = _normalize_detector(command, uow)
                detectors = DetectorRepository(uow)
                duplicate = detectors.find_active_by_position_code(str(values["position_code"]))
                if duplicate is not None and (command.id is None or int(duplicate["id"]) != command.id):
                    return _conflict("探测器位号已存在[待确认]")
                if command.id is None:
                    row_id = detectors.create(values)
                    action = "device_config.detector.create"
                else:
                    if detectors.find_active_by_id(command.id) is None:
                        return _not_found("探测器不存在")
                    detectors.update(command.id, values)
                    row_id = command.id
                    action = "device_config.detector.update"
                row = detectors.find_active_by_id(row_id)
                _add_log(
                    uow,
                    actor,
                    action,
                    "detector",
                    row_id,
                    "保存探测器配置。",
                    {"position_code": values["position_code"]},
                )
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def delete_detector(self, session_or_id: Session | str, detector_id: int) -> ServiceResult[None]:
        actor = self._require_config_write(session_or_id, f"删除探测器 {detector_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(detector_id):
            return _validation("探测器 ID 无效")
        with UnitOfWork(self._database) as uow:
            detectors = DetectorRepository(uow)
            if detectors.find_active_by_id(detector_id) is None:
                return _not_found("探测器不存在")
            detectors.soft_delete(detector_id)
            _add_log(uow, actor, "device_config.detector.delete", "detector", detector_id, "删除探测器配置。")
            uow.commit()
        return ServiceResult.ok(None)

    def import_detectors(self, session_or_id: Session | str, source: Path) -> ServiceResult[DetectorImportResult]:
        actor = self._require_config_write(session_or_id, "导入探测器配置")
        if isinstance(actor, ServiceResult):
            return actor
        if self._import_export is None:
            return ServiceResult.fail(code=int(ErrorCode.SERVICE_UNAVAILABLE), message="导入服务未配置")
        try:
            # Import files are untrusted; the shared validator rejects formulas/macros and unknown columns.
            plan = self._import_export.prepare_import(
                source,
                ImportTemplate(required_fields=DETECTOR_IMPORT_FIELDS[:13], allowed_fields=DETECTOR_IMPORT_FIELDS),
            )
        except Exception as exc:
            return _validation(str(exc))
        row_errors = list(plan.validation.errors)
        imported = 0
        seen_positions: set[str] = set()
        with UnitOfWork(self._database) as uow:
            detectors = DetectorRepository(uow)
            for index, row in enumerate(plan.validation.rows, start=2):
                try:
                    command = _detector_command_from_row(row)
                    values = _normalize_detector(command, uow)
                    position = str(values["position_code"])
                    if position in seen_positions:
                        row_errors.append(ImportRowError(index, "position_code", "导入文件中位号重复[待确认]"))
                        continue
                    if detectors.find_active_by_position_code(position) is not None:
                        row_errors.append(ImportRowError(index, "position_code", "探测器位号已存在[待确认]"))
                        continue
                    detectors.create(values)
                    seen_positions.add(position)
                    imported += 1
                except ValueError as exc:
                    field, message = _split_row_error(str(exc))
                    row_errors.append(ImportRowError(index, field, message))
            _add_log(
                uow,
                actor,
                "device_config.detector.import",
                "detector",
                None,
                "导入探测器配置。",
                {"imported": imported, "errors": len(row_errors)},
            )
            uow.commit()
        message = "导入完成" if not row_errors else "导入完成，存在错误行"
        return ServiceResult.ok(DetectorImportResult(imported, tuple(row_errors)), message=message)

    def export_detector_template(self, destination: Path) -> ServiceResult[Path]:
        if self._import_export is None:
            return ServiceResult.fail(code=int(ErrorCode.SERVICE_UNAVAILABLE), message="导出服务未配置")
        try:
            # CSV text is neutralized by ImportExportService so spreadsheet formulas cannot execute when opened later.
            return ServiceResult.ok(self._import_export.export_csv(destination, DETECTOR_IMPORT_FIELDS, ()))
        except Exception as exc:
            return _validation(str(exc))

    def get_protocol_mode(self) -> str:
        with UnitOfWork(self._database) as uow:
            mode = SettingsRepository(uow).get_value("protocol_mode", ProtocolMode.PROTOCOL_1.value)
            uow.commit()
        return ProtocolMode(mode or ProtocolMode.PROTOCOL_1.value).value

    def set_protocol_mode(self, session_or_id: Session | str, mode: str) -> ServiceResult[ProtocolModeChangeResult]:
        actor = self._require_config_write(session_or_id, "切换协议模式")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            normalized = ProtocolMode(mode).value
        except ValueError:
            return _validation("协议模式只能为 protocol_1 或 protocol_2")
        status = AcquisitionStatus(self._acquisition_status_provider())
        if status in {AcquisitionStatus.RUNNING, AcquisitionStatus.RECONNECTING}:
            # Adapter selection is per deployment; active switching could mix frame semantics.
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="采集运行中，不能切换协议模式")
        with UnitOfWork(self._database) as uow:
            settings = SettingsRepository(uow)
            current = settings.get_value("protocol_mode", ProtocolMode.PROTOCOL_1.value)
            settings.set_value("protocol_mode", normalized)
            _add_log(
                uow,
                actor,
                "device_config.protocol_mode.update",
                "system_settings",
                "protocol_mode",
                "切换协议模式。",
                {"old": current, "new": normalized},
            )
            uow.commit()
        return ServiceResult.ok(
            ProtocolModeChangeResult(normalized, True, "协议模式已切换，请重启采集或软件后生效。"),
            message="协议模式已切换，请重启采集或软件后生效。",
        )

    def _require_config_write(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        try:
            return self._session_store.require_permission(
                self._database,
                session_or_id,
                Permission.SYSTEM_SETTINGS.value,
                target_summary,
            )
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))


def _normalize_port(command: PortCommand) -> dict[str, object]:
    channel_type = _choice(command.channel_type, CHANNEL_TYPES, "channel_type")
    values: dict[str, object] = {
        "name": _text(command.name, 80, "name"),
        "channel_type": channel_type,
        "poll_interval_ms": _int_range(command.poll_interval_ms, 100, 600000, "poll_interval_ms"),
        "timeout_ms": _int_range(command.timeout_ms, 100, 60000, "timeout_ms"),
        "failure_threshold": _int_range(command.failure_threshold, 1, 20, "failure_threshold"),
        "reconnect_interval_ms": _int_range(command.reconnect_interval_ms, 500, 600000, "reconnect_interval_ms"),
        "is_enabled": _bool(command.is_enabled, "is_enabled"),
    }
    if channel_type == "serial":
        values.update(
            serial_port_name=_text(command.serial_port_name or "", 40, "serial_port_name"),
            baud_rate=_int_range(command.baud_rate or 9600, 1200, 115200, "baud_rate"),
            data_bits=_int_range(command.data_bits or 8, 5, 8, "data_bits"),
            parity=_choice(command.parity or "N", PARITIES, "parity"),
            stop_bits=_stop_bits(command.stop_bits if command.stop_bits is not None else 1),
            tcp_host=None,
            tcp_port=None,
        )
    else:
        values.update(
            serial_port_name=None,
            baud_rate=None,
            data_bits=None,
            parity=None,
            stop_bits=None,
            tcp_host=_text(command.tcp_host or "", 253, "tcp_host"),
            tcp_port=_int_range(command.tcp_port, 1, 65535, "tcp_port"),
        )
    return values


def _normalize_controller(command: ControllerCommand) -> dict[str, object]:
    return {
        "port_id": _positive_int(command.port_id, "port_id"),
        "name": _text(command.name, 80, "name"),
        "address": _int_range(command.address, 1, 247, "address"),
        "model": _optional_text(command.model, 80, "model"),
        "detector_count": _int_range(command.detector_count, 0, 4096, "detector_count"),
        "is_enabled": _bool(command.is_enabled, "is_enabled"),
    }


def _normalize_gas_type(command: GasTypeCommand) -> dict[str, object]:
    values = {
        "name": _text(command.name, 80, "name"),
        "unit": _text(command.unit, 32, "unit"),
        "range_min": _float(command.range_min, "range_min"),
        "range_max": _float(command.range_max, "range_max"),
        "default_alarm_low": _optional_float(command.default_alarm_low, "default_alarm_low"),
        "default_alarm_high": _optional_float(command.default_alarm_high, "default_alarm_high"),
        "is_enabled": _bool(command.is_enabled, "is_enabled"),
    }
    _validate_range(values["range_min"], values["range_max"], values["default_alarm_low"], values["default_alarm_high"])
    return values


def _normalize_detector(command: DetectorCommand, uow: UnitOfWork) -> dict[str, object]:
    port_id = _positive_int(command.port_id, "port_id")
    controller_id = _optional_positive_int(command.controller_id, "controller_id")
    gas_type_id = _positive_int(command.gas_type_id, "gas_type_id")
    ports = PortRepository(uow)
    controllers = ControllerRepository(uow)
    gas_types = GasTypeRepository(uow)
    if ports.find_active_by_id(port_id) is None:
        raise ValueError("port_id:端口不存在")
    if controller_id is not None:
        controller = controllers.find_active_by_id(controller_id)
        if controller is None:
            raise ValueError("controller_id:控制器不存在")
        if int(controller["port_id"]) != port_id:
            raise ValueError("controller_id:控制器不属于所选端口")
    if gas_types.find_active_by_id(gas_type_id) is None:
        raise ValueError("gas_type_id:气体类型不存在")
    values = {
        "controller_id": controller_id,
        "port_id": port_id,
        "position_code": _text(command.position_code, 80, "position_code"),
        "name": _text(command.name, 80, "name"),
        "model": _optional_text(command.model, 80, "model"),
        "protocol_address": _int_range(command.protocol_address, 1, 247, "protocol_address"),
        "register_index": _int_range(command.register_index, 0, 65535, "register_index"),
        "gas_type_id": gas_type_id,
        "unit": _text(command.unit, 32, "unit"),
        "range_min": _float(command.range_min, "range_min"),
        "range_max": _float(command.range_max, "range_max"),
        "alarm_low": _optional_float(command.alarm_low, "alarm_low"),
        "alarm_high": _optional_float(command.alarm_high, "alarm_high"),
        "alarm_type": _choice(command.alarm_type, ALARM_TYPES, "alarm_type"),
        "sound_enabled": _bool(command.sound_enabled, "sound_enabled"),
        "store_interval_sec": _int_range(command.store_interval_sec, 1, 86400, "store_interval_sec"),
        "sensor_life_until": _optional_iso_date(command.sensor_life_until, "sensor_life_until"),
        "calibration_cycle_days": _optional_int_range(
            command.calibration_cycle_days,
            1,
            3650,
            "calibration_cycle_days",
        ),
        "is_enabled": _bool(command.is_enabled, "is_enabled"),
    }
    _validate_range(values["range_min"], values["range_max"], values["alarm_low"], values["alarm_high"])
    return values


def _detector_command_from_row(row: dict[str, str]) -> DetectorCommand:
    return DetectorCommand(
        port_id=_parse_int(row, "port_id"),
        controller_id=_parse_optional_int(row, "controller_id"),
        position_code=row.get("position_code", ""),
        name=row.get("name", ""),
        model=row.get("model") or None,
        protocol_address=_parse_int(row, "protocol_address"),
        register_index=_parse_int(row, "register_index"),
        gas_type_id=_parse_int(row, "gas_type_id"),
        unit=row.get("unit", ""),
        range_min=_parse_float(row, "range_min"),
        range_max=_parse_float(row, "range_max"),
        alarm_low=_parse_optional_float(row, "alarm_low"),
        alarm_high=_parse_optional_float(row, "alarm_high"),
        alarm_type=row.get("alarm_type") or "low_high",
        sound_enabled=_parse_bool(row.get("sound_enabled", "1"), "sound_enabled"),
        store_interval_sec=_parse_int(row, "store_interval_sec"),
        sensor_life_until=row.get("sensor_life_until") or None,
        calibration_cycle_days=_parse_optional_int(row, "calibration_cycle_days"),
        is_enabled=_parse_bool(row.get("is_enabled", "1"), "is_enabled"),
    )


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


def _int_range(value: object, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{field}:必须在 {minimum}..{maximum} 范围内")
    return int(value)


def _optional_int_range(value: object, minimum: int, maximum: int, field: str) -> int | None:
    if value is None:
        return None
    return _int_range(value, minimum, maximum, field)


def _float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field}:必须为数字")
    return float(value)


def _optional_float(value: object, field: str) -> float | None:
    if value in {None, ""}:
        return None
    return _float(value, field)


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field}:必须为布尔值")
    return value


def _text(value: object, max_length: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}:必须为文本")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if not normalized:
        raise ValueError(f"{field}:不能为空")
    if len(normalized) > max_length:
        raise ValueError(f"{field}:长度超出限制")
    return normalized


def _optional_text(value: object, max_length: int, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, max_length, field)


def _choice(value: object, choices: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{field}:取值不受支持")
    return value


def _stop_bits(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or float(value) not in STOP_BITS:
        raise ValueError("stop_bits:取值不受支持")
    return float(value)


def _validate_range(minimum: object, maximum: object, low: object, high: object) -> None:
    min_value = float(minimum)
    max_value = float(maximum)
    if min_value >= max_value:
        raise ValueError("range:量程下限必须小于上限")
    if low is not None and not (min_value <= float(low) <= max_value):
        raise ValueError("alarm_low:低报阈值必须在量程内")
    if high is not None and not (min_value <= float(high) <= max_value):
        raise ValueError("alarm_high:高报阈值必须在量程内")
    if low is not None and high is not None and float(low) > float(high):
        raise ValueError("alarm:低报阈值不能高于高报阈值")


def _optional_iso_date(value: object, field: str) -> str | None:
    if value in {None, ""}:
        return None
    text = _text(value, 40, field)
    if len(text) < 10 or text[4:5] != "-" or text[7:8] != "-":
        raise ValueError(f"{field}:日期格式无效")
    return text


def _parse_int(row: dict[str, str], field: str) -> int:
    value = row.get(field, "")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{field}:必须为整数") from exc


def _parse_optional_int(row: dict[str, str], field: str) -> int | None:
    return None if not row.get(field) else _parse_int(row, field)


def _parse_float(row: dict[str, str], field: str) -> float:
    value = row.get(field, "")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{field}:必须为数字") from exc


def _parse_optional_float(row: dict[str, str], field: str) -> float | None:
    return None if not row.get(field) else _parse_float(row, field)


def _parse_bool(value: str, field: str) -> bool:
    if value in {"1", "true", "True", "是", "yes"}:
        return True
    if value in {"0", "false", "False", "否", "no"}:
        return False
    raise ValueError(f"{field}:必须为布尔值")


def _split_row_error(message: str) -> tuple[str, str]:
    if ":" in message:
        field, detail = message.split(":", 1)
        return field, detail
    return "row", message
