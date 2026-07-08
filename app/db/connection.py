from __future__ import annotations

import importlib
import pkgutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from app.config.defaults import DatabaseConfig


class DatabaseError(RuntimeError):
    """Database errors converted to controlled application startup failures."""


@dataclass(frozen=True)
class Migration:
    version: str
    module: ModuleType


class Database:
    def __init__(self, database_file: Path, config: DatabaseConfig) -> None:
        self.database_file = database_file
        self.config = config

    def connect(self) -> sqlite3.Connection:
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                self.database_file,
                timeout=self.config.busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            self._apply_pragmas(connection)
            return connection
        except sqlite3.Error as exc:
            raise DatabaseError("数据库连接初始化失败，请检查数据目录权限。") from exc

    def initialize(self) -> None:
        connection = self.connect()
        try:
            try:
                ensure_migration_table(connection)
                apply_migrations(connection, load_migrations())
            except sqlite3.Error as exc:
                raise DatabaseError("数据库迁移失败，应用已停止启动。") from exc
        finally:
            connection.close()

    def _apply_pragmas(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.config.busy_timeout_ms)}")
        if self.config.wal_enabled:
            connection.execute("PRAGMA journal_mode = WAL")


def ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def applied_versions(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {str(row["version"]) for row in rows}


def apply_migrations(connection: sqlite3.Connection, migrations: list[Migration]) -> None:
    ensure_migration_table(connection)
    completed = applied_versions(connection)
    for migration in migrations:
        if migration.version in completed:
            continue
        try:
            connection.execute("BEGIN IMMEDIATE")
            migration.module.apply(connection)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (migration.version,))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def load_migrations() -> list[Migration]:
    package_name = "app.db.migrations"
    package = importlib.import_module(package_name)
    migrations: list[Migration] = []
    for module_info in pkgutil.iter_modules(package.__path__):
        if not module_info.name[:4].isdigit():
            continue
        module = importlib.import_module(f"{package_name}.{module_info.name}")
        migrations.append(Migration(version=module_info.name.split("_", 1)[0], module=module))
    return sorted(migrations, key=lambda item: item.version)
