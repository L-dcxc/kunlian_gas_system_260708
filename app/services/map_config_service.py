from __future__ import annotations

import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.db.connection import Database
from app.db.repositories.device_config_repository import DetectorRepository
from app.db.repositories.map_repository import MapPointRepository, MapRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode, ValidationError
from app.services.file_validation import FileValidator, safe_relative_path
from app.services.models import ServiceResult
from app.services.permissions import Permission


@dataclass(frozen=True, slots=True)
class MapUploadCommand:
    source_path: Path
    name: str | None = None
    is_enabled: bool = True


@dataclass(frozen=True, slots=True)
class MapPointCommand:
    map_id: int
    detector_id: int
    x_ratio: float
    y_ratio: float
    label: str | None = None


class MapConfigService:
    def __init__(
        self,
        database: Database,
        session_store: SessionStore,
        *,
        validator: FileValidator,
        maps_dir: Path,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._validator = validator
        self._maps_dir = maps_dir.resolve()
        self._maps_dir.mkdir(parents=True, exist_ok=True)

    def list_maps(self) -> tuple[dict[str, object], ...]:
        with UnitOfWork(self._database) as uow:
            rows = MapRepository(uow).list_active()
            uow.commit()
        return tuple(_row_dict(row) for row in rows)

    def list_map_points(self, map_id: int) -> ServiceResult[tuple[dict[str, object], ...]]:
        if not _valid_id(map_id):
            return _validation("地图 ID 无效")
        with UnitOfWork(self._database) as uow:
            if MapRepository(uow).find_active_by_id(map_id) is None:
                return _not_found("地图不存在")
            rows = MapPointRepository(uow).list_active_for_map(map_id)
            uow.commit()
        return ServiceResult.ok(tuple(_row_dict(row) for row in rows))

    def upload_map(self, session_or_id: Session | str, command: MapUploadCommand) -> ServiceResult[dict[str, object]]:
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
            safe_filename = _safe_map_filename(validation.path)
            destination = self._validator.ensure_within_data_root(self._maps_dir / safe_filename)
            # The stored path is relative to data_root; original filenames are kept as text only.
            relative_path = safe_relative_path(self._validator.data_root, destination).as_posix()
            if validation.path.resolve() != destination.resolve():
                shutil.copy2(validation.path, destination)
            with UnitOfWork(self._database) as uow:
                repo = MapRepository(uow)
                map_id = repo.create(
                    {
                        "name": name,
                        "safe_filename": safe_filename,
                        "original_filename": _text(validation.path.name, 180, "original_filename"),
                        "relative_path": relative_path,
                        "size_bytes": validation.size_bytes,
                        "is_enabled": _bool(command.is_enabled, "is_enabled"),
                    }
                )
                row = repo.find_active_by_id(map_id)
                _add_log(uow, actor, "map_config.map.upload", "map", map_id, "上传地图。", {"name": name})
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, OSError, ValidationError, ValueError) as exc:
            return _validation(str(exc))

    def update_map(
        self,
        session_or_id: Session | str,
        map_id: int,
        *,
        name: str,
        is_enabled: bool = True,
    ) -> ServiceResult[dict[str, object]]:
        actor = self._require_config_write(session_or_id, f"修改地图 {map_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(map_id):
            return _validation("地图 ID 无效")
        try:
            safe_name = _text(name, 120, "name")
            with UnitOfWork(self._database) as uow:
                repo = MapRepository(uow)
                if repo.find_active_by_id(map_id) is None:
                    return _not_found("地图不存在")
                repo.update(map_id, name=safe_name, is_enabled=_bool(is_enabled, "is_enabled"))
                row = repo.find_active_by_id(map_id)
                _add_log(uow, actor, "map_config.map.update", "map", map_id, "修改地图。", {"name": safe_name})
                uow.commit()
            return ServiceResult.ok(_row_dict(row))
        except ValueError as exc:
            return _validation(str(exc))

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
                return _conflict("地图存在点位绑定，当前按受控拒绝处理[待确认]")
            maps.soft_delete(map_id)
            _add_log(uow, actor, "map_config.map.delete", "map", map_id, "删除地图。")
            uow.commit()
        return ServiceResult.ok(None)

    def save_map_point(
        self,
        session_or_id: Session | str,
        command: MapPointCommand,
    ) -> ServiceResult[dict[str, object]]:
        actor = self._require_config_write(session_or_id, "保存地图点位")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            map_id = _positive_int(command.map_id, "map_id")
            detector_id = _positive_int(command.detector_id, "detector_id")
            # Map point coordinates are ratios, not pixels, so resizing/fullscreen views can reproject them safely.
            x_ratio = _ratio(command.x_ratio, "x_ratio")
            y_ratio = _ratio(command.y_ratio, "y_ratio")
            label = _optional_text(command.label, 120, "label")
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
                row = points.find_active_by_id(point_id)
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
            return ServiceResult.ok(_row_dict(row))
        except (sqlite3.IntegrityError, ValueError) as exc:
            return _validation(str(exc))

    def delete_map_point(self, session_or_id: Session | str, point_id: int) -> ServiceResult[None]:
        actor = self._require_config_write(session_or_id, f"删除地图点位 {point_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(point_id):
            return _validation("地图点位 ID 无效")
        with UnitOfWork(self._database) as uow:
            points = MapPointRepository(uow)
            if points.find_active_by_id(point_id) is None:
                return _not_found("地图点位不存在")
            points.soft_delete(point_id)
            _add_log(uow, actor, "map_config.point.delete", "map_point", point_id, "删除地图点位。")
            uow.commit()
        return ServiceResult.ok(None)

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


def _safe_map_filename(source: Path) -> str:
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in source.stem)[:40].strip("_")
    stem = stem or "map"
    return f"{uuid.uuid4().hex}_{stem}{source.suffix.lower()}"


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
    return {key: row[key] for key in row.keys()} if row is not None else {}


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


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field}:必须为布尔值")
    return value
