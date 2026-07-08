from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_settings (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            scheduled_enabled INTEGER NOT NULL DEFAULT 0 CHECK(scheduled_enabled IN (0, 1)),
            interval_hours INTEGER NOT NULL DEFAULT 24 CHECK(interval_hours BETWEEN 1 AND 720),
            backup_time TEXT NOT NULL DEFAULT '02:00' CHECK(length(backup_time) = 5),
            target_directory TEXT NOT NULL DEFAULT 'backups' CHECK(length(target_directory) BETWEEN 1 AND 260),
            keep_last INTEGER NOT NULL DEFAULT 10 CHECK(keep_last BETWEEN 1 AND 365),
            failure_notify_enabled INTEGER NOT NULL DEFAULT 1 CHECK(failure_notify_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO backup_settings(
            id, scheduled_enabled, interval_hours, backup_time, target_directory, keep_last, failure_notify_enabled
        ) VALUES (1, 0, 24, '02:00', 'backups', 10, 1)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_type TEXT NOT NULL CHECK(backup_type IN ('manual', 'scheduled', 'pre_restore')),
            result TEXT NOT NULL CHECK(result IN ('success', 'failed')),
            file_name TEXT CHECK(file_name IS NULL OR length(file_name) <= 180),
            relative_path TEXT CHECK(relative_path IS NULL OR length(relative_path) <= 260),
            size_bytes INTEGER CHECK(size_bytes IS NULL OR size_bytes >= 0),
            sha256 TEXT CHECK(sha256 IS NULL OR length(sha256) = 64),
            message TEXT NOT NULL DEFAULT '' CHECK(length(message) <= 500),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_backup_records_created_type_result
        ON backup_records(created_at, backup_type, result)
        """
    )
