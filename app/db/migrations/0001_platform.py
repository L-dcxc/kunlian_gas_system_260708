from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            result TEXT NOT NULL,
            actor_id INTEGER,
            actor_name TEXT,
            target_type TEXT,
            target_id TEXT,
            summary TEXT NOT NULL DEFAULT '',
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_operation_logs_created_at
        ON operation_logs(created_at, actor_id, action_type)
        """
    )
