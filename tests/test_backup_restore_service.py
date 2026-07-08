from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.core.event_bus import EventBus
from app.core.paths import AppPaths
from app.core.runtime_locks import RuntimeLockManager
from app.db.connection import Database
from app.db.repositories.backup_repository import BackupSettingsRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import AuthService, SessionStore, hash_password
from app.services.backup_service import BackupService, BackupSettingsCommand, RestoreConfirm
from app.services.models import ServiceResult


class StopFacade:
    def __init__(self, result: ServiceResult[object] | None = None) -> None:
        self.calls = 0
        self.result = result or ServiceResult.ok(None)

    def stop(self, session_or_id):
        self.calls += 1
        return self.result


class BackupRestoreServiceTests(unittest.TestCase):
    def _paths(self, temp_dir: str) -> AppPaths:
        return AppPaths.create(temp_dir)

    def _database(self, paths: AppPaths) -> Database:
        database = Database(paths.database_file, DatabaseConfig(filename=paths.database_file.name))
        database.initialize()
        return database

    def _seed_user(self, database: Database, username: str, password: str, role: str) -> None:
        password_hash, password_salt = hash_password(password)
        with UnitOfWork(database) as uow:
            UserRepository(uow).create_user(
                username=username,
                password_hash=password_hash,
                password_salt=password_salt,
                role=role,
                is_active=True,
            )
            uow.commit()

    def _sessions(self, database: Database):
        self._seed_user(database, "admin", "AdminPass123", "admin")
        self._seed_user(database, "operator", "Operator123", "operator")
        store = SessionStore()
        auth = AuthService(database, store)
        return store, auth.login("admin", "AdminPass123").data, auth.login("operator", "Operator123").data

    def _service(
        self,
        database: Database,
        store: SessionStore,
        paths: AppPaths,
        locks: RuntimeLockManager | None = None,
        acquisition: StopFacade | None = None,
        event_bus: EventBus | None = None,
    ) -> BackupService:
        return BackupService(
            database,
            store,
            paths=paths,
            runtime_locks=locks or RuntimeLockManager(),
            acquisition_service=acquisition,
            event_bus=event_bus,
        )

    def _runtime_files(self, paths: AppPaths, *, config_text: str = "config-v1", map_text: str = "map-v1") -> None:
        paths.config_file.write_text(config_text, encoding="utf-8")
        (paths.config_dir / "license.key").write_text("license-secret", encoding="utf-8")
        (paths.maps_dir / "plant.map").write_text(map_text, encoding="utf-8")
        (paths.maps_dir / "license-map.png").write_text("license-map", encoding="utf-8")

    def test_settings_validation_permission_and_controlled_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            database = self._database(paths)
            store, admin, operator = self._sessions(database)
            service = self._service(database, store, paths)

            denied = service.update_settings(
                operator,
                BackupSettingsCommand(scheduled_enabled=True, interval_hours=12, target_directory="backups/daily"),
            )
            self.assertFalse(denied.success)
            self.assertEqual(denied.code, 403)

            outside = service.update_settings(
                admin,
                BackupSettingsCommand(scheduled_enabled=True, interval_hours=12, target_directory=Path(temp_dir).parent),
            )
            self.assertFalse(outside.success)
            self.assertEqual(outside.code, 400)
            self.assertNotIn(str(Path(temp_dir).parent), outside.message)

            saved = service.update_settings(
                admin,
                BackupSettingsCommand(
                    scheduled_enabled=True,
                    interval_hours=6,
                    backup_time="03:30",
                    target_directory="backups/daily",
                    keep_last=3,
                ),
            )
            self.assertTrue(saved.success)
            self.assertEqual(saved.data.target_directory, "backups/daily")
            with UnitOfWork(database) as uow:
                row = BackupSettingsRepository(uow).get()
                self.assertEqual(row["target_directory"], "backups/daily")
                logs, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                self.assertGreaterEqual(len(logs), 1)
                uow.commit()

    def test_manual_backup_contains_database_config_maps_and_excludes_license(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            database = self._database(paths)
            store, admin, _ = self._sessions(database)
            self._runtime_files(paths)
            service = self._service(database, store, paths)

            result = service.create_manual_backup(admin, paths.backups_dir)
            self.assertTrue(result.success, result.message)
            backup_file = paths.data_dir / result.data.relative_path
            self.assertTrue(backup_file.exists())

            with zipfile.ZipFile(backup_file) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertIn("db/app.sqlite3", names)
            self.assertIn("config/config.json", names)
            self.assertIn("maps/plant.map", names)
            self.assertNotIn("config/license.key", names)
            self.assertNotIn("maps/license-map.png", names)
            self.assertFalse(manifest["include_license"])

    def test_invalid_zip_manifest_and_hash_are_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            database = self._database(paths)
            store, admin, _ = self._sessions(database)
            self._runtime_files(paths, config_text="current")
            service = self._service(database, store, paths)

            missing_manifest = paths.backups_dir / "missing_manifest.zip"
            with zipfile.ZipFile(missing_manifest, "w") as archive:
                archive.writestr("db/app.sqlite3", b"bad")
            result = service.restore_from_backup(admin, missing_manifest, RestoreConfirm(confirmed=True))
            self.assertFalse(result.success)
            self.assertEqual(paths.config_file.read_text(encoding="utf-8"), "current")

            traversal = paths.backups_dir / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../config/config.json", b"bad")
                archive.writestr("manifest.json", b"{}")
            result = service.restore_from_backup(admin, traversal, RestoreConfirm(confirmed=True))
            self.assertFalse(result.success)
            self.assertEqual(paths.config_file.read_text(encoding="utf-8"), "current")

            bad_hash = paths.backups_dir / "bad_hash.zip"
            manifest = {
                "manifest_version": 1,
                "app_version": "0.1.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": "0006",
                "include_license": False,
                "files": [
                    {
                        "path": "db/app.sqlite3",
                        "kind": "database",
                        "size_bytes": 4,
                        "sha256": "0" * 64,
                    }
                ],
            }
            with zipfile.ZipFile(bad_hash, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
                archive.writestr("db/app.sqlite3", b"data")
            result = service.restore_from_backup(admin, bad_hash, RestoreConfirm(confirmed=True))
            self.assertFalse(result.success)
            self.assertEqual(paths.config_file.read_text(encoding="utf-8"), "current")

    def test_restore_requires_confirmation_stops_acquisition_and_restores_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            database = self._database(paths)
            store, admin, _ = self._sessions(database)
            self._runtime_files(paths, config_text="backup-config", map_text="backup-map")
            acquisition = StopFacade()
            service = self._service(database, store, paths, acquisition=acquisition)
            backup = service.create_manual_backup(admin, paths.backups_dir)
            self.assertTrue(backup.success, backup.message)
            backup_file = paths.data_dir / backup.data.relative_path

            paths.config_file.write_text("current-config", encoding="utf-8")
            (paths.maps_dir / "plant.map").write_text("current-map", encoding="utf-8")
            no_confirm = service.restore_from_backup(admin, backup_file, RestoreConfirm(confirmed=False))
            self.assertFalse(no_confirm.success)
            self.assertEqual(paths.config_file.read_text(encoding="utf-8"), "current-config")

            restored = service.restore_from_backup(admin, backup_file, RestoreConfirm(confirmed=True))
            self.assertTrue(restored.success, restored.message)
            self.assertEqual(acquisition.calls, 1)
            self.assertTrue(restored.data.restart_required)
            self.assertIn("重启", restored.message)
            self.assertEqual(paths.config_file.read_text(encoding="utf-8"), "backup-config")
            self.assertEqual((paths.maps_dir / "plant.map").read_text(encoding="utf-8"), "backup-map")
            self.assertIsNotNone(restored.data.pre_restore_backup)

    def test_restore_maintenance_lock_conflict_returns_controlled_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            database = self._database(paths)
            store, admin, _ = self._sessions(database)
            self._runtime_files(paths)
            locks = RuntimeLockManager()
            acquisition = StopFacade()
            service = self._service(database, store, paths, locks=locks, acquisition=acquisition)
            backup = service.create_manual_backup(admin, paths.backups_dir)
            self.assertTrue(backup.success, backup.message)
            locks.acquire_operation("backup", timeout=0)
            try:
                result = service.restore_from_backup(
                    admin,
                    paths.data_dir / backup.data.relative_path,
                    RestoreConfirm(confirmed=True),
                )
            finally:
                locks.release_operation("backup")
            self.assertFalse(result.success)
            self.assertEqual(result.code, 409)
            self.assertEqual(acquisition.calls, 0)

    def test_scheduled_backup_failure_records_log_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            database = self._database(paths)
            store, admin, _ = self._sessions(database)
            self._runtime_files(paths)
            locks = RuntimeLockManager()
            events: list[object] = []
            event_bus = EventBus()
            event_bus.subscribe("backup.scheduled.failed", lambda event_type, payload: events.append(payload))
            service = self._service(database, store, paths, locks=locks, event_bus=event_bus)
            saved = service.update_settings(
                admin,
                BackupSettingsCommand(scheduled_enabled=True, interval_hours=1, target_directory="backups"),
            )
            self.assertTrue(saved.success)

            locks.acquire_operation("restore", timeout=0)
            try:
                result = service.trigger_scheduled_backup()
            finally:
                locks.release_operation("restore")
            self.assertFalse(result.success)
            self.assertEqual(result.message, "定时备份失败")
            self.assertEqual(len(events), 1)
            with UnitOfWork(database) as uow:
                rows, _ = OperationLogRepository(uow).list_for_action(action_type="backup.scheduled")
                self.assertGreaterEqual(len(rows), 1)
                self.assertNotIn(str(paths.data_dir), " ".join(str(row["details_json"]) for row in rows))
                uow.commit()


if __name__ == "__main__":
    unittest.main()
