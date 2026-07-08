from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detector_id INTEGER NOT NULL REFERENCES detectors(id),
            plan_type TEXT NOT NULL CHECK(plan_type IN ('sensor_life', 'calibration', 'custom')),
            due_at TEXT NOT NULL CHECK(length(due_at) BETWEEN 10 AND 40),
            remind_days_before INTEGER NOT NULL DEFAULT 7 CHECK(remind_days_before BETWEEN 0 AND 3650),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed', 'cancelled')),
            notes TEXT NOT NULL DEFAULT '' CHECK(length(notes) <= 1000),
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_maintenance_plans_due_status
        ON maintenance_plans(status, due_at, detector_id, deleted_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_maintenance_plans_detector_active
        ON maintenance_plans(detector_id, plan_type, status, deleted_at)
        """
    )
