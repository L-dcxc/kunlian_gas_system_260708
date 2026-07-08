from __future__ import annotations

import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from app.core.event_bus import EventBus
from app.core.logging import get_logger, user_safe_error
from app.core.paths import AppPaths
from app.core.runtime_locks import RuntimeLockError, RuntimeLockManager
from app.core.scheduler import Scheduler
from app.db.connection import Database
from app.db.repositories.backup_repository import BackupRecordRepository, BackupSettingsRepository, row_to_dict
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.backup_package import (
    BackupPackageError,
    PackageSource,
    create_backup_package,
    relative_restore_path,
    stage_validated_package,
    validate_backup_package,
)
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Permission

BACKUP_JOB_NAME = "backup.scheduled"
BACKUP_FILE_PREFIX = "backup"
PRE_RESTORE_PREFIX = "pre_restore"
SCHEDULED_BACKUP_FAILED_EVENT = "backup.scheduled.failed"
RESTORE_SUCCESS_MESSAGE = "数据恢复完成，请重启或重新加载应用数据。"


class AcquisitionRestoreFacade(Protocol):
    def stop(self, session_or_id: Session | str) -> ServiceResult[object]: ...


@dataclass(frozen=True, slots=True)
class BackupSettingsCommand:
    scheduled_enabled: bool
    interval_hours: int
    backup_time: str = "02:00"
    target_directory: str | Path | None = None
    keep_last: int = 10
    failure_notify_enabled: bool = True


@dataclass(frozen=True, slots=True)
class BackupSettingsView:
    scheduled_enabled: bool
    interval_hours: int
    backup_time: str
    target_directory: str
    keep_last: int
    failure_notify_enabled: bool
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class BackupResult:
    file_name: str
    relative_path: str
    size_bytes: int
    sha256: str
    created_at: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class RestoreConfirm:
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class RestoreResult:
    restored_files: tuple[str, ...]
    pre_restore_backup: BackupResult | None
    restart_required: bool
    message: str


class BackupService:
    def __init__(
        self,
        database: Database,
        session_store: SessionStore | None,
        *,
        paths: AppPaths,
        runtime_locks: RuntimeLockManager,
        scheduler: Scheduler | None = None,
        acquisition_service: AcquisitionRestoreFacade | object | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._paths = paths
        self._runtime_locks = runtime_locks
        self._scheduler = scheduler
        self._acquisition_service = acquisition_service
        self._event_bus = event_bus
        self._logger = get_logger("services.backup")
        self._schedule_lock = threading.RLock()
        self._scheduled_registered = False
        self._scheduled_paused = False
        self._paths.ensure_directories()

    def get_settings(self) -> ServiceResult[BackupSettingsView]:
        try:
            with UnitOfWork(self._database) as uow:
                row = BackupSettingsRepository(uow).get()
                uow.commit()
            return ServiceResult.ok(_settings_view(row_to_dict(row)))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="备份设置读取失败")

    def update_settings(self, session_or_id: Session | str, command: BackupSettingsCommand) -> ServiceResult[BackupSettingsView]:
        actor = self._require_backup_permission(session_or_id, "更新备份设置")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            values = self._normalize_settings(command)
            with UnitOfWork(self._database) as uow:
                BackupSettingsRepository(uow).upsert(**values)
                row = BackupSettingsRepository(uow).get()
                _add_operation_log(
                    uow,
                    actor,
                    action_type="backup.settings.update",
                    result="success",
                    summary="更新备份设置。",
                    details={
                        "scheduled_enabled": values["scheduled_enabled"],
                        "interval_hours": values["interval_hours"],
                        "target_directory": values["target_directory"],
                        "keep_last": values["keep_last"],
                    },
                )
                uow.commit()
            self._configure_schedule_from_settings(_settings_view(row_to_dict(row)))
            return ServiceResult.ok(_settings_view(row_to_dict(row)))
        except ValueError as exc:
            self._log_failure(actor, "backup.settings.update", "备份设置校验失败。", str(exc))
            return _validation(str(exc))
        except Exception as exc:
            self._logger.error("backup settings update failed: %s", user_safe_error(exc))
            self._log_failure(actor, "backup.settings.update", "备份设置更新失败。", "服务处理失败")
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="备份设置更新失败")

    def create_manual_backup(self, session_or_id: Session | str, target_dir: Path) -> ServiceResult[BackupResult]:
        actor = self._require_backup_permission(session_or_id, "手动备份")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            directory = self._controlled_backup_dir(target_dir)
            with self._runtime_locks.acquire("backup", timeout=0):
                result = self._create_backup_package_unlocked(directory, backup_type="manual")
            self._log_backup_success(actor, "backup.manual", "manual", result, "手动备份成功。")
            self._apply_retention(directory, keep_last=self._current_settings().keep_last, backup_type="manual")
            return ServiceResult.ok(result)
        except RuntimeLockError as exc:
            self._log_failure(actor, "backup.manual", "手动备份失败。", str(exc))
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=str(exc))
        except (ValueError, BackupPackageError) as exc:
            self._log_failure(actor, "backup.manual", "手动备份失败。", str(exc))
            return _validation(str(exc))
        except Exception as exc:
            self._logger.error("manual backup failed: %s", user_safe_error(exc))
            self._log_failure(actor, "backup.manual", "手动备份失败。", "服务处理失败")
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="备份失败，请稍后重试。")

    def register_scheduled_backup(self) -> ServiceResult[BackupSettingsView]:
        settings = self._current_settings()
        try:
            self._configure_schedule_from_settings(settings)
            return ServiceResult.ok(settings)
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="定时备份注册失败")

    def trigger_scheduled_backup(self) -> ServiceResult[BackupResult]:
        return self._run_scheduled_backup()

    def restore_from_backup(
        self,
        session_or_id: Session | str,
        backup_file: Path,
        confirm: RestoreConfirm,
    ) -> ServiceResult[RestoreResult]:
        actor = self._require_backup_permission(session_or_id, "恢复备份")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            schema_version = self._schema_version()
            validate_backup_package(backup_file, current_schema_version=schema_version)
            if not confirm.confirmed:
                return _validation("数据恢复需要显式确认")
            self._pause_scheduled_backup()
            try:
                with self._runtime_locks.acquire("restore", timeout=0):
                    stop_result = self._stop_acquisition_for_restore(session_or_id)
                    if stop_result is not None and not stop_result.success:
                        self._log_failure(actor, "backup.restore", "数据恢复失败。", "采集停止失败")
                        return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="恢复前停止采集失败")
                    pre_restore = self._try_pre_restore_backup()
                    restored = self._restore_validated_files(backup_file, current_schema_version=schema_version)
                    self._log_restore_success(actor, restored, pre_restore)
                    return ServiceResult.ok(
                        RestoreResult(
                            restored_files=tuple(restored),
                            pre_restore_backup=pre_restore,
                            restart_required=True,
                            message=RESTORE_SUCCESS_MESSAGE,
                        ),
                        message=RESTORE_SUCCESS_MESSAGE,
                    )
            finally:
                self._resume_scheduled_backup()
        except RuntimeLockError as exc:
            self._log_failure(actor, "backup.restore", "数据恢复失败。", str(exc))
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=str(exc))
        except (ValueError, BackupPackageError) as exc:
            self._log_failure(actor, "backup.restore", "数据恢复失败。", str(exc))
            return _validation(str(exc))
        except Exception as exc:
            self._logger.error("restore failed: %s", user_safe_error(exc))
            self._log_failure(actor, "backup.restore", "数据恢复失败。", "服务处理失败")
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="恢复失败，请使用安全备份并联系管理员。")

    def _run_scheduled_backup(self) -> ServiceResult[BackupResult]:
        if self._scheduled_paused:
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="定时备份已暂停")
        try:
            settings = self._current_settings()
            if not settings.scheduled_enabled:
                return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="定时备份未启用")
            directory = self._controlled_backup_dir(Path(settings.target_directory))
            with self._runtime_locks.acquire("backup", timeout=0):
                result = self._create_backup_package_unlocked(directory, backup_type="scheduled")
            self._apply_retention(directory, keep_last=settings.keep_last, backup_type="scheduled")
            return ServiceResult.ok(result)
        except Exception as exc:
            safe_message = user_safe_error(exc)
            self._logger.error("scheduled backup failed: %s", safe_message)
            self._log_system_backup_failure("scheduled", "定时备份失败")
            if self._event_bus is not None:
                self._event_bus.publish(SCHEDULED_BACKUP_FAILED_EVENT, {"message": "定时备份失败"})
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="定时备份失败")

    def _create_backup_package_unlocked(self, directory: Path, *, backup_type: str) -> BackupResult:
        directory.mkdir(parents=True, exist_ok=True)
        schema_version = self._schema_version()
        created_at = datetime.now(timezone.utc)
        filename = _backup_filename(PRE_RESTORE_PREFIX if backup_type == "pre_restore" else BACKUP_FILE_PREFIX, created_at)
        target_file = _unique_backup_path(directory / filename)
        with TemporaryDirectory(prefix="backup_build_", dir=str(self._paths.backups_dir)) as temp_name:
            temp_dir = Path(temp_name)
            db_snapshot = temp_dir / "app.sqlite3"
            self._snapshot_database(db_snapshot)
            sources = [PackageSource(db_snapshot, "db/app.sqlite3", "database")]
            sources.extend(self._config_sources())
            sources.extend(self._map_sources())
            manifest = create_backup_package(target_file=target_file, sources=sources, schema_version=schema_version)
        validated = validate_backup_package(target_file, current_schema_version=schema_version)
        relative_path = self._relative_to_data(target_file)
        result = BackupResult(
            file_name=target_file.name,
            relative_path=relative_path,
            size_bytes=validated.total_size_bytes,
            sha256=_sha256_file(target_file),
            created_at=manifest.created_at,
            schema_version=manifest.schema_version,
        )
        self._log_system_backup_record(backup_type, "success", result, f"{backup_type} backup success")
        return result

    def _restore_validated_files(self, backup_file: Path, *, current_schema_version: str) -> tuple[str, ...]:
        validated, stage_dir, temp = stage_validated_package(backup_file, current_schema_version=current_schema_version)
        restored: list[str] = []
        with temp:
            with TemporaryDirectory(prefix="restore_rollback_", dir=str(self._paths.backups_dir)) as rollback_name:
                rollback_dir = Path(rollback_name)
                backups: list[tuple[Path, Path | None]] = []
                try:
                    for item in validated.manifest.files:
                        relative = relative_restore_path(item.path)
                        source = stage_dir / relative
                        target = self._restore_target_for(relative)
                        original = self._backup_original(target, rollback_dir)
                        backups.append((target, original))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temp_target = target.with_name(f".{target.name}.restore_tmp")
                        shutil.copy2(source, temp_target)
                        temp_target.replace(target)
                        if target == self._paths.database_file:
                            self._remove_sqlite_sidecars(target)
                        restored.append(relative.as_posix())
                    return tuple(restored)
                except Exception:
                    self._rollback_restored_files(backups)
                    raise

    def _try_pre_restore_backup(self) -> BackupResult | None:
        try:
            # Restore already holds the restore maintenance lock, so the safety
            # backup is created in-place without taking the conflicting backup lock.
            return self._create_backup_package_unlocked(self._paths.backups_dir, backup_type="pre_restore")
        except Exception as exc:
            self._logger.warning("pre-restore safety backup failed: %s", user_safe_error(exc))
            self._log_system_backup_failure("pre_restore", "预恢复安全备份失败")
            return None

    def _snapshot_database(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(str(self._database.database_file), timeout=self._database.config.busy_timeout_ms / 1000)
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def _config_sources(self) -> tuple[PackageSource, ...]:
        sources: list[PackageSource] = []
        for path in _iter_files(self._paths.config_dir):
            relative = path.relative_to(self._paths.config_dir).as_posix()
            if _looks_like_license(path):
                continue
            sources.append(PackageSource(path, f"config/{relative}", "config"))
        return tuple(sources)

    def _map_sources(self) -> tuple[PackageSource, ...]:
        sources: list[PackageSource] = []
        for path in _iter_files(self._paths.maps_dir):
            relative = path.relative_to(self._paths.maps_dir).as_posix()
            if _looks_like_license(path):
                continue
            sources.append(PackageSource(path, f"maps/{relative}", "map"))
        return tuple(sources)

    def _restore_target_for(self, relative: Path) -> Path:
        parts = relative.parts
        if len(parts) < 2:
            raise BackupPackageError("备份文件路径无效")
        top = parts[0]
        if top == "db" and relative.as_posix() == "db/app.sqlite3":
            return self._paths.database_file
        if top == "config":
            return self._contained_data_path(self._paths.config_dir / Path(*parts[1:]))
        if top == "maps":
            return self._contained_data_path(self._paths.maps_dir / Path(*parts[1:]))
        raise BackupPackageError("备份文件目录无效")

    def _backup_original(self, target: Path, rollback_dir: Path) -> Path | None:
        if not target.exists():
            return None
        relative = target.resolve().relative_to(self._paths.data_dir.resolve())
        destination = rollback_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, destination)
        return destination

    def _rollback_restored_files(self, backups: list[tuple[Path, Path | None]]) -> None:
        for target, original in reversed(backups):
            try:
                if original is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
            except OSError as exc:
                self._logger.error("restore rollback failed: %s", user_safe_error(exc))

    def _remove_sqlite_sidecars(self, database_file: Path) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database_file) + suffix)
            try:
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                pass

    def _stop_acquisition_for_restore(self, session_or_id: Session | str) -> ServiceResult[object] | None:
        if self._acquisition_service is None:
            return None
        stop_for_restore = getattr(self._acquisition_service, "stop_for_restore", None)
        if callable(stop_for_restore):
            return stop_for_restore()
        stop = getattr(self._acquisition_service, "stop", None)
        if callable(stop):
            return stop(session_or_id)
        return None

    def _configure_schedule_from_settings(self, settings: BackupSettingsView) -> None:
        if self._scheduler is None:
            return
        with self._schedule_lock:
            self._scheduler.cancel(BACKUP_JOB_NAME)
            self._scheduled_registered = False
            if settings.scheduled_enabled:
                self._scheduler.every(
                    BACKUP_JOB_NAME,
                    settings.interval_hours * 3600,
                    self._scheduled_callback,
                    run_immediately=False,
                )
                self._scheduled_registered = True

    def _pause_scheduled_backup(self) -> None:
        with self._schedule_lock:
            self._scheduled_paused = True
            if self._scheduler is not None:
                self._scheduler.cancel(BACKUP_JOB_NAME)
                self._scheduled_registered = False

    def _resume_scheduled_backup(self) -> None:
        with self._schedule_lock:
            self._scheduled_paused = False
        self._configure_schedule_from_settings(self._current_settings())

    def _scheduled_callback(self) -> None:
        self._run_scheduled_backup()

    def _apply_retention(self, directory: Path, *, keep_last: int, backup_type: str) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                rows = BackupRecordRepository(uow).list_successes_for_retention(backup_type=backup_type, keep_last=keep_last)
                uow.commit()
            for row in rows:
                relative_path = str(row["relative_path"] or "")
                if not relative_path:
                    continue
                candidate = self._contained_data_path(self._paths.data_dir / Path(relative_path))
                if candidate.parent == directory.resolve() and candidate.exists():
                    candidate.unlink()
        except Exception as exc:
            self._logger.warning("backup retention failed: %s", user_safe_error(exc))

    def _current_settings(self) -> BackupSettingsView:
        with UnitOfWork(self._database) as uow:
            row = BackupSettingsRepository(uow).get()
            uow.commit()
        return _settings_view(row_to_dict(row))

    def _normalize_settings(self, command: BackupSettingsCommand) -> dict[str, object]:
        target = command.target_directory if command.target_directory is not None else self._paths.backups_dir
        directory = self._controlled_backup_dir(Path(target))
        return {
            "scheduled_enabled": _bool(command.scheduled_enabled, "scheduled_enabled"),
            "interval_hours": _int_range(command.interval_hours, 1, 720, "interval_hours"),
            "backup_time": _backup_time(command.backup_time),
            "target_directory": self._relative_to_data(directory),
            "keep_last": _int_range(command.keep_last, 1, 365, "keep_last"),
            "failure_notify_enabled": _bool(command.failure_notify_enabled, "failure_notify_enabled"),
        }

    def _controlled_backup_dir(self, target_dir: Path) -> Path:
        candidate = target_dir.expanduser()
        if not candidate.is_absolute():
            candidate = self._paths.data_dir / candidate
        resolved = candidate.resolve()
        backups_root = self._paths.backups_dir.resolve()
        try:
            resolved.relative_to(backups_root)
        except ValueError as exc:
            # Backup destination is deliberately constrained to the application
            # backup directory so settings cannot persist arbitrary sensitive paths.
            raise ValueError("备份目录必须位于受控备份目录内") from exc
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _contained_data_path(self, candidate: Path) -> Path:
        resolved = candidate.resolve()
        if not self._paths.contains(resolved):
            raise BackupPackageError("恢复文件超出受控数据目录")
        return resolved

    def _relative_to_data(self, path: Path) -> str:
        resolved = path.resolve()
        if not self._paths.contains(resolved):
            raise ValueError("路径不在受控数据目录内")
        return resolved.relative_to(self._paths.data_dir.resolve()).as_posix()

    def _schema_version(self) -> str:
        connection = self._database.connect()
        try:
            row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            version = str(row["version"] if row is not None and row["version"] is not None else "0000")
            return version.zfill(4)
        except sqlite3.Error as exc:
            raise BackupPackageError("数据库结构版本读取失败") from exc
        finally:
            connection.close()

    def _require_backup_permission(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        if self._session_store is None:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="权限校验未配置")
        try:
            return self._session_store.require_permission(
                self._database,
                session_or_id,
                Permission.BACKUP_RESTORE.value,
                target_summary,
            )
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))

    def _log_backup_success(
        self,
        actor: Session,
        action_type: str,
        backup_type: str,
        result: BackupResult,
        summary: str,
    ) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                _add_operation_log(
                    uow,
                    actor,
                    action_type=action_type,
                    result="success",
                    summary=summary,
                    details={
                        "backup_type": backup_type,
                        "file_name": result.file_name,
                        "relative_path": result.relative_path,
                        "size_bytes": result.size_bytes,
                    },
                )
                uow.commit()
        except sqlite3.Error:
            pass

    def _log_restore_success(
        self,
        actor: Session,
        restored: tuple[str, ...],
        pre_restore: BackupResult | None,
    ) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                _add_operation_log(
                    uow,
                    actor,
                    action_type="backup.restore",
                    result="success",
                    summary="数据恢复成功。",
                    details={
                        "restored_count": len(restored),
                        "pre_restore_backup": pre_restore.relative_path if pre_restore else "none",
                    },
                )
                uow.commit()
        except sqlite3.Error:
            pass

    def _log_failure(self, actor: Session, action_type: str, summary: str, reason: str) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                _add_operation_log(
                    uow,
                    actor,
                    action_type=action_type,
                    result="failed",
                    summary=summary,
                    details={"reason": _safe_reason(reason)},
                )
                uow.commit()
        except sqlite3.Error:
            pass

    def _log_system_backup_record(self, backup_type: str, result: str, backup: BackupResult, message: str) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                BackupRecordRepository(uow).add(
                    backup_type=backup_type,
                    result=result,
                    file_name=backup.file_name,
                    relative_path=backup.relative_path,
                    size_bytes=backup.size_bytes,
                    sha256=backup.sha256,
                    message=message,
                )
                uow.commit()
        except sqlite3.Error:
            pass

    def _log_system_backup_failure(self, backup_type: str, message: str) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                BackupRecordRepository(uow).add(backup_type=backup_type, result="failed", message=message)
                OperationLogRepository(uow).add(
                    action_type="backup.scheduled" if backup_type == "scheduled" else "backup.pre_restore",
                    result="failed",
                    actor_id=None,
                    actor_name="system",
                    target_type="backup",
                    summary=message,
                    details={},
                )
                uow.commit()
        except sqlite3.Error:
            pass


def _settings_view(row: dict[str, object]) -> BackupSettingsView:
    return BackupSettingsView(
        scheduled_enabled=bool(int(row.get("scheduled_enabled", 0))),
        interval_hours=int(row.get("interval_hours", 24)),
        backup_time=str(row.get("backup_time", "02:00")),
        target_directory=str(row.get("target_directory", "backups")),
        keep_last=int(row.get("keep_last", 10)),
        failure_notify_enabled=bool(int(row.get("failure_notify_enabled", 1))),
        updated_at=str(row.get("updated_at")) if row.get("updated_at") is not None else None,
    )


def _add_operation_log(
    uow: UnitOfWork,
    actor: Session,
    *,
    action_type: str,
    result: str,
    summary: str,
    details: dict[str, object] | None = None,
) -> None:
    OperationLogRepository(uow).add(
        action_type=action_type,
        result=result,
        actor_id=actor.user_id,
        actor_name=actor.username,
        target_type="backup",
        target_id=None,
        summary=summary,
        details=details or {},
    )


def _iter_files(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    return tuple(path for path in sorted(root.rglob("*")) if path.is_file())


def _backup_filename(prefix: str, created_at: datetime) -> str:
    stamp = created_at.strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}.zip"


def _unique_backup_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("备份文件名冲突")


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_like_license(path: Path) -> bool:
    return any("license" in part.lower() or "licence" in part.lower() for part in path.parts)


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=_safe_reason(message))


def _safe_reason(value: str) -> str:
    text = user_safe_error(ValueError(value)) if ":\\" in value or "/" in value else value
    normalized = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    return normalized[:500]


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field}:必须为布尔值")
    return value


def _int_range(value: object, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{field}:必须在 {minimum}..{maximum} 范围内")
    return value


def _backup_time(value: object) -> str:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("backup_time:格式必须为 HH:MM")
    hour, minute = value[:2], value[3:]
    if not hour.isdigit() or not minute.isdigit() or int(hour) > 23 or int(minute) > 59:
        raise ValueError("backup_time:格式必须为 HH:MM")
    return value
