from __future__ import annotations

import hashlib
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.state_store import RealtimeFilter, StateStore
from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.device_config_repository import DetectorRepository
from app.db.repositories.map_repository import MapPointRepository, MapRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode, ValidationError
from app.services.file_validation import FileValidator, safe_relative_path
from app.services.models import DeviceStatus, ServiceResult
from app.services.monitoring_read_service import MonitoringReadService
from app.services.permissions import Permission


@dataclass(frozen=True, slots=True)
class MapUploadCommand:
    source_path: Path
    name: str | None = None
    is_enabled: bool = True


@dataclass(frozen=True, slots=True)
class SaveMapPointCommand:
    map_id: int
    detector_id: int
    x_ratio: float
    y_ratio: float
    label: str | None = None


@dataclass(frozen=True, slots=True)
class MapView:
    id: int
    name: str
    relative_path: str
    safe_file_name: str
    original_file_name: str
    size_bytes: int
    content_hash: str | None
    is_enabled: bool


@dataclass(frozen=True, slots=True)
class MapPointRuntimeView:
    id: int
    map_id: int
    detector_id: int
    x_ratio: float
    y_ratio: float
    label: str | None
    detector_position_code: str | None
    detector_name: str | None
    controller_id: int | None
    controller_name: str | None
    status: str
    concentration: float | None
    gas_type: str | None
    unit: str | None
    alarm_level: int | None
    timestamp: str | None
    active_alarm: bool
    active_alarm_type: str | None


@dataclass(frozen=True, slots=True)
class MapRuntimeView:
    map: MapView
    points: tuple[MapPointRuntimeView, ...]


class MapService:
    def __init__(
        self,
        database: Database,
        session_store: SessionStore | None = None,
        *,
        validator: FileValidator,
        maps_dir: Path,
        state_store: StateStore | None = None,
        monitoring_read_service: MonitoringReadService | None = None,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._validator = validator
        self._maps_dir = validator.ensure_within_data_root(maps_dir)
        self._maps_dir.mkdir(parents=True, exist_ok=True)
        self._state_store = state_store
        self._monitoring_read_service = monitoring_read_service

    def list_maps(self) -> tuple[MapView, ...]:
        with UnitOfWork(self._database) as uow:
            rows = MapRepository(uow).list_enabled()
            uow.commit()
        return tuple(_map_view(row) for row in rows)

    def upload_map(self, session_or_id: Session | str, command: MapUploadCommand) -> ServiceResult[MapView]:
        actor = self._require_config_write(session_or_id, "上传地图")
        if isinstance(actor, ServiceResult):
            return actor
        validation = self._validator.validate_map_file(command.source_path)
        if not validation.ok:
            return ServiceResult.fail(
                code=int(ErrorCode.VALIDATION_ERROR),
                message="地图文件校验失败",
                errors=validation.errors,
            )
        try:
            name = _text(command.name or validation.path.stem, 120, "name")
            original_filename = _text(validation.path.name, 180, "original_file_name")
            safe_filename = _safe_map_filename(validation.path)
            destination = self._validator.ensure_within_data_root(self._maps_dir / safe_filename)
            relative_path = safe_relative_path(self._validator.data_root, destination).as_posix()
            content_hash = _sha256_file(validation.path)
            if validation.path.resolve() != destination.resolve():
                shutil.copy2(validation.path, destination)
        except (OSError, ValidationError, ValueError):
            return _validation("地图文件保存失败")

        try:
            with UnitOfWork(self._database) as uow:
                repo = MapRepository(uow)
                map_id = repo.create(
                    {
                        "name": name,
                        "safe_filename": safe_filename,
                        "original_filename": original_filename,
                        "relative_path": relative_path,
                        "size_bytes": validation.size_bytes,
                        "content_hash": content_hash,
                        "is_enabled": _bool(command.is_enabled, "is_enabled"),
                    }
                )
                row = repo.find_active_by_id(map_id)
                _add_log(
                    uow,
                    actor,
                    "map_config.map.upload",
                    "map",
                    map_id,
                    "上传地图。",
                    {"name": name, "relative_path": relative_path},
                )
                uow.commit()
            return ServiceResult.ok(_map_view(row))
        except sqlite3.IntegrityError:
            return _conflict("地图保存冲突")
        except ValueError:
            return _validation("地图参数无效")

    def delete_map(self, session_or_id: Session | str, map_id: int) -> ServiceResult[None]:
        actor = self._require_config_write(session_or_id, f"删除地图 {map_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(map_id):
            return _validation("地图 ID 无效")
        with UnitOfWork(self._database) as uow:
            maps = MapRepository(uow)
            points = MapPointRepository(uow)
            if maps.find_active_by_id(map_id) is None:
                return _not_found("地图不存在")
            if points.count_for_map(map_id) > 0:
                return _conflict("地图存在点位绑定，不能删除")
            maps.soft_delete(map_id)
            _add_log(uow, actor, "map_config.map.delete", "map", map_id, "删除地图。")
            uow.commit()
        return ServiceResult.ok(None)

    def save_point(self, session_or_id: Session | str, command: SaveMapPointCommand) -> ServiceResult[MapPointRuntimeView]:
        actor = self._require_config_write(session_or_id, "保存地图点位")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            map_id = _positive_int(command.map_id, "map_id")
            detector_id = _positive_int(command.detector_id, "detector_id")
            x_ratio = _ratio(command.x_ratio, "x_ratio")
            y_ratio = _ratio(command.y_ratio, "y_ratio")
            label = _optional_text(command.label, 120, "label")
        except ValueError as exc:
            return _validation(str(exc))
        try:
            with UnitOfWork(self._database) as uow:
                maps = MapRepository(uow)
                detectors = DetectorRepository(uow)
                points = MapPointRepository(uow)
                if maps.find_active_by_id(map_id) is None:
                    return _not_found("地图不存在")
                if detectors.find_active_by_id(detector_id) is None:
                    return _validation("探测器不存在")
                point_id = points.upsert_for_detector(
                    map_id=map_id,
                    detector_id=detector_id,
                    x_ratio=x_ratio,
                    y_ratio=y_ratio,
                    label=label,
                )
                point_row = points.find_active_by_id(point_id)
                joined_rows = points.list_active_for_map_with_detectors(map_id)
                active_alarm_by_detector = _active_alarm_by_detector(AlarmRepository(uow).list_active())
                _add_log(
                    uow,
                    actor,
                    "map_config.point.save",
                    "map_point",
                    point_id,
                    "保存地图点位。",
                    {"map_id": map_id, "detector_id": detector_id},
                )
                uow.commit()
            readings = self._reading_by_detector((detector_id,))
            joined = next((row for row in joined_rows if int(row["id"]) == int(point_row["id"])), point_row)
            return ServiceResult.ok(_point_runtime_view(joined, readings.get(detector_id), active_alarm_by_detector))
        except sqlite3.IntegrityError:
            return _conflict("地图点位保存冲突")

    def get_map_runtime_view(self, map_id: int) -> ServiceResult[MapRuntimeView]:
        if not _valid_id(map_id):
            return _validation("地图 ID 无效")
        with UnitOfWork(self._database) as uow:
            maps = MapRepository(uow)
            points = MapPointRepository(uow)
            row = maps.find_active_by_id(map_id)
            if row is None or int(row["is_enabled"]) != 1:
                return _not_found("地图不存在")
            point_rows = points.list_active_for_map_with_detectors(map_id)
            active_alarm_by_detector = _active_alarm_by_detector(AlarmRepository(uow).list_active())
            uow.commit()
        detector_ids = tuple(int(row["detector_id"]) for row in point_rows)
        readings = self._reading_by_detector(detector_ids)
        runtime_points = tuple(_point_runtime_view(row, readings.get(int(row["detector_id"])), active_alarm_by_detector) for row in point_rows)
        return ServiceResult.ok(MapRuntimeView(map=_map_view(row), points=runtime_points))

    def _reading_by_detector(self, detector_ids: tuple[int, ...]) -> dict[int, Any]:
        readings: dict[int, Any] = {}
        if not detector_ids:
            return readings
        if self._state_store is not None:
            for reading in self._state_store.get_realtime_snapshot(RealtimeFilter(detector_ids=detector_ids)):
                detector_id = _get_int(reading, "detector_id")
                if detector_id is not None:
                    readings[detector_id] = reading
        if self._monitoring_read_service is not None:
            for detector_id in detector_ids:
                if detector_id in readings:
                    continue
                result = self._monitoring_read_service.get_realtime(detector_id)
                if result.success and result.data is not None:
                    readings[detector_id] = result.data
        return readings

    def _require_config_write(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
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


def _map_view(row: Any) -> MapView:
    return MapView(
        id=int(row["id"]),
        name=str(row["name"]),
        relative_path=str(row["relative_path"]),
        safe_file_name=str(row["safe_filename"]),
        original_file_name=str(row["original_filename"]),
        size_bytes=int(row["size_bytes"]),
        content_hash=None if "content_hash" not in row.keys() or row["content_hash"] is None else str(row["content_hash"]),
        is_enabled=bool(int(row["is_enabled"])),
    )


def _point_runtime_view(row: Any, reading: Any | None, active_alarm_by_detector: dict[int, Any]) -> MapPointRuntimeView:
    detector_id = int(row["detector_id"])
    alarm = active_alarm_by_detector.get(detector_id)
    status = _get_text(reading, "status") or ("offline" if _detector_is_enabled(row) else "disabled")
    return MapPointRuntimeView(
        id=int(row["id"]),
        map_id=int(row["map_id"]),
        detector_id=detector_id,
        x_ratio=float(row["x_ratio"]),
        y_ratio=float(row["y_ratio"]),
        label=None if row["label"] is None else str(row["label"]),
        detector_position_code=_optional_row_text(row, "detector_position_code"),
        detector_name=_optional_row_text(row, "detector_name"),
        controller_id=None if _row_value(row, "detector_controller_id") is None else int(row["detector_controller_id"]),
        controller_name=_optional_row_text(row, "controller_name"),
        status=status,
        concentration=_get_float(reading, "concentration"),
        gas_type=_get_text(reading, "gas_type"),
        unit=_get_text(reading, "unit") or _optional_row_text(row, "detector_unit"),
        alarm_level=_get_int(reading, "alarm_level"),
        timestamp=_get_timestamp(reading),
        active_alarm=alarm is not None,
        active_alarm_type=None if alarm is None else str(alarm["alarm_type"]),
    )


def _active_alarm_by_detector(rows: list[Any] | tuple[Any, ...]) -> dict[int, Any]:
    alarms: dict[int, Any] = {}
    for row in rows:
        detector_id = int(row["detector_id"])
        alarms.setdefault(detector_id, row)
    return alarms


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


def _safe_map_filename(source: Path) -> str:
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in source.stem)[:40].strip("_")
    return f"{uuid.uuid4().hex}_{stem or 'map'}{source.suffix.lower()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detector_is_enabled(row: Any) -> bool:
    value = _row_value(row, "detector_is_enabled")
    return value is None or int(value) == 1


def _row_value(row: Any, field: str) -> Any | None:
    return row[field] if field in row.keys() else None


def _optional_row_text(row: Any, field: str) -> str | None:
    value = _row_value(row, field)
    return None if value is None else str(value)


def _get_text(obj: Any | None, field: str) -> str | None:
    if obj is None:
        return None
    value = obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)
    if isinstance(value, DeviceStatus):
        return value.value
    return value if isinstance(value, str) else None


def _get_int(obj: Any | None, field: str) -> int | None:
    if obj is None:
        return None
    value = obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)
    return None if value is None else int(value)


def _get_float(obj: Any | None, field: str) -> float | None:
    if obj is None:
        return None
    value = obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)
    return None if value is None else float(value)


def _get_timestamp(obj: Any | None) -> str | None:
    if obj is None:
        return None
    value = obj.get("timestamp") if isinstance(obj, dict) else getattr(obj, "timestamp", None)
    return value.isoformat() if hasattr(value, "isoformat") else (value if isinstance(value, str) else None)


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=message)


def _not_found(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message=message)


def _conflict(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=message)


def _valid_id(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _positive_int(value: object, field: str) -> int:
    if not _valid_id(value):
        raise ValueError(f"{field}:必须为正整数")
    return int(value)


def _ratio(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0 or value > 1:
        raise ValueError(f"{field}:比例坐标必须在 0..1 范围内")
    return float(value)


def _text(value: object, max_length: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}:必须为文本")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").replace("\x00", " ").split())
    if not normalized:
        raise ValueError(f"{field}:不能为空")
    if len(normalized) > max_length:
        raise ValueError(f"{field}:长度超出限制")
    return normalized


def _optional_text(value: object, max_length: int, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, max_length, field)


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field}:必须为布尔值")
    return value
