from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from app.config.defaults import DatabaseConfig, default_config
from app.config.loader import load_config
from app.core.logging import configure_logging, shutdown_logging
from app.core.paths import AppPaths
from app.core.runtime_locks import RuntimeLockError, RuntimeLockManager
from app.core.state_store import RealtimeFilter, StateStore
from app.core.workers import WorkerError, WorkerPool
from app.db.connection import Database
from app.db.repositories.base import build_time_range_clause, order_by_clause, validate_pagination
from app.db.unit_of_work import UnitOfWork
from app.core.bootstrap import create_app_context


class PlatformRuntimeTests(unittest.TestCase):
    def test_config_missing_and_invalid_values_use_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.create(temp_dir)
            result = load_config(paths.config_file)
            self.assertTrue(result.created_default)
            self.assertEqual(result.config.api.bind_address, "127.0.0.1")
            self.assertFalse(result.config.api.cors_enabled)

            paths.config_file.write_text(
                json.dumps({"api": {"bind_address": "0.0.0.0", "port": 80, "cors_enabled": True}}),
                encoding="utf-8",
            )
            result = load_config(paths.config_file)
            self.assertEqual(result.config.api.bind_address, "127.0.0.1")
            self.assertEqual(result.config.api.port, default_config().api.port)
            self.assertFalse(result.config.api.cors_enabled)
            self.assertGreaterEqual(len(result.warnings), 1)

            paths.config_file.write_text("{broken", encoding="utf-8")
            result = load_config(paths.config_file)
            self.assertEqual(result.config.api.bind_address, "127.0.0.1")
            self.assertNotIn(str(paths.config_file), " ".join(result.warnings))

    def test_logging_redacts_sensitive_values_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = AppPaths.create(temp_dir)
            logger = configure_logging(paths.logs_dir, default_config().logging, sensitive_paths=(paths.data_dir,))
            try:
                logger.error("password=abc api_token=tok machine_id=raw path=%s forward=C:/Users/test/PySide6/lib/fonts", paths.database_file)
                for handler in logger.handlers:
                    handler.flush()
                text = (paths.logs_dir / "application.log").read_text(encoding="utf-8")
                self.assertIn("password=<redacted>", text)
                self.assertIn("api_token=<redacted>", text)
                self.assertIn("machine_id=<redacted>", text)
                self.assertNotIn("abc", text)
                self.assertNotIn(str(paths.data_dir), text)
                self.assertNotIn("C:/Users/test", text)
            finally:
                shutdown_logging()

    def test_database_migration_and_unit_of_work_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_file = Path(temp_dir) / "test.sqlite3"
            database = Database(db_file, DatabaseConfig(filename="test.sqlite3"))
            database.initialize()
            connection = database.connect()
            try:
                version = connection.execute("SELECT version FROM schema_migrations").fetchone()["version"]
                self.assertEqual(version, "0001")
                connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            finally:
                connection.close()

            with self.assertRaises(RuntimeError):
                with UnitOfWork(database) as uow:
                    uow.execute("INSERT INTO sample(name) VALUES (?)", ("rolled-back",))
                    raise RuntimeError("boom")

            connection = database.connect()
            try:
                count = connection.execute("SELECT COUNT(*) AS total FROM sample").fetchone()["total"]
                self.assertEqual(count, 0)
            finally:
                connection.close()

            with UnitOfWork(database) as uow:
                uow.execute("INSERT INTO sample(name) VALUES (?)", ("committed",))
                uow.commit()
            connection = database.connect()
            try:
                count = connection.execute("SELECT COUNT(*) AS total FROM sample").fetchone()["total"]
                self.assertEqual(count, 1)
            finally:
                connection.close()

    def test_repository_helpers_validate_bounds_and_identifiers(self) -> None:
        self.assertEqual(validate_pagination(2, 10).offset, 10)
        with self.assertRaises(ValueError):
            validate_pagination(0, 10)
        with self.assertRaises(ValueError):
            order_by_clause("created_at; DROP TABLE x", "ASC", {"created_at"})
        clause, params = build_time_range_clause(
            "created_at",
            "2026-01-01T00:00:00+00:00",
            None,
            {"created_at"},
        )
        self.assertEqual(clause, "created_at >= ?")
        self.assertEqual(len(params), 1)

    def test_state_store_locking_cache_and_filtering(self) -> None:
        store = StateStore(curve_cache_size=2, publish_interval_ms=0)
        store.update_readings([
            {"detector_id": 1, "status": "normal"},
            {"detector_id": 2, "status": "offline"},
            {"detector_id": 1, "status": "offline"},
        ])
        self.assertEqual(len(store.get_curve_cache(1)), 2)
        result = store.get_realtime_snapshot(RealtimeFilter(status="offline"))
        self.assertEqual({item["detector_id"] for item in result}, {1, 2})

    def test_runtime_locks_prevent_conflicting_operations(self) -> None:
        locks = RuntimeLockManager()
        with locks.acquire("acquisition"):
            with self.assertRaises(RuntimeLockError):
                with locks.acquire("restore"):
                    pass
            with locks.acquire("backup"):
                self.assertTrue(locks.is_active("backup"))

    def test_worker_errors_are_controlled(self) -> None:
        pool = WorkerPool(max_workers=1)
        errors: list[WorkerError] = []
        try:
            handle = pool.submit(
                "failing",
                lambda token: (_ for _ in ()).throw(RuntimeError("password=secret")),
                on_error=errors.append,
            )
            handle.future.exception(timeout=2)
            time.sleep(0.1)
            self.assertEqual(errors[0].job_name, "failing")
            self.assertNotIn("secret", errors[0].message)
        finally:
            pool.shutdown()

    def test_app_context_initializes_directories_and_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = create_app_context(temp_dir)
            try:
                self.assertTrue(context.paths.maps_dir.exists())
                self.assertTrue(context.paths.backups_dir.exists())
                self.assertTrue(context.paths.logs_dir.exists())
                self.assertTrue(context.paths.config_dir.exists())
                self.assertTrue(context.paths.database_file.exists())
                self.assertEqual(context.containers.services, {})
                self.assertEqual(context.containers.devices, {})
                self.assertEqual(context.containers.api, {})
            finally:
                context.shutdown()


if __name__ == "__main__":
    unittest.main()
