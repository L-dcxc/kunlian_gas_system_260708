from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS realtime_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detector_id INTEGER NOT NULL REFERENCES detectors(id),
            protocol TEXT NOT NULL,
            source_type TEXT NOT NULL,
            port_id INTEGER NOT NULL REFERENCES ports(id),
            controller_id INTEGER REFERENCES controllers(id),
            controller_address INTEGER,
            detector_address INTEGER,
            status TEXT NOT NULL,
            concentration REAL,
            gas_type TEXT,
            unit TEXT,
            alarm_level INTEGER,
            raw_status TEXT,
            raw_value TEXT,
            quality TEXT NOT NULL DEFAULT 'valid' CHECK(quality IN ('valid', 'offline')),
            timestamp TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_realtime_snapshots_detector
        ON realtime_snapshots(detector_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_realtime_snapshots_status
        ON realtime_snapshots(status, updated_at)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS running_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detector_id INTEGER NOT NULL REFERENCES detectors(id),
            protocol TEXT NOT NULL,
            source_type TEXT NOT NULL,
            port_id INTEGER NOT NULL REFERENCES ports(id),
            controller_id INTEGER REFERENCES controllers(id),
            status TEXT NOT NULL,
            concentration REAL,
            gas_type TEXT,
            unit TEXT,
            alarm_level INTEGER,
            raw_status TEXT,
            raw_value TEXT,
            quality TEXT NOT NULL DEFAULT 'valid' CHECK(quality IN ('valid', 'offline')),
            recorded_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_running_records_detector_time
        ON running_records(detector_id, recorded_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_running_records_port_time
        ON running_records(port_id, recorded_at)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS alarm_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detector_id INTEGER NOT NULL REFERENCES detectors(id),
            alarm_type TEXT NOT NULL CHECK(
                alarm_type IN ('alarm_low', 'alarm_high', 'over_range', 'fault', 'offline', 'disabled', 'warming')
            ),
            alarm_level INTEGER,
            trigger_value REAL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            status TEXT NOT NULL CHECK(status IN ('active', 'recovered')),
            source_reading_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_alarm_records_active_unique
        ON alarm_records(detector_id, alarm_type)
        WHERE status = 'active'
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alarm_records_time_status
        ON alarm_records(start_time, detector_id, status)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS linkage_objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_type TEXT NOT NULL CHECK(length(object_type) BETWEEN 1 AND 40),
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
            location TEXT CHECK(location IS NULL OR length(location) <= 200),
            adapter_type TEXT NOT NULL DEFAULT 'simulated' CHECK(adapter_type IN ('simulated', 'real')),
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_linkage_objects_name_active
        ON linkage_objects(name)
        WHERE deleted_at IS NULL
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS linkage_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
            detector_id INTEGER REFERENCES detectors(id),
            alarm_type TEXT NOT NULL CHECK(
                alarm_type IN ('alarm_low', 'alarm_high', 'over_range', 'fault', 'offline', 'disabled', 'warming', '*')
            ),
            alarm_level INTEGER,
            object_id INTEGER NOT NULL REFERENCES linkage_objects(id),
            action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 80),
            trigger_delay_sec INTEGER NOT NULL DEFAULT 0 CHECK(trigger_delay_sec BETWEEN 0 AND 86400),
            recovery_action TEXT CHECK(recovery_action IS NULL OR length(recovery_action) <= 80),
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_linkage_rules_match
        ON linkage_rules(detector_id, alarm_type, is_enabled, deleted_at)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS linkage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id INTEGER NOT NULL REFERENCES linkage_objects(id),
            rule_id INTEGER REFERENCES linkage_rules(id),
            alarm_record_id INTEGER REFERENCES alarm_records(id),
            action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 80),
            trigger_reason TEXT NOT NULL CHECK(length(trigger_reason) BETWEEN 1 AND 80),
            user_id INTEGER,
            user_name TEXT,
            result TEXT NOT NULL CHECK(length(result) BETWEEN 1 AND 40),
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_linkage_records_auto_once
        ON linkage_records(alarm_record_id, rule_id, trigger_reason)
        WHERE alarm_record_id IS NOT NULL AND rule_id IS NOT NULL AND trigger_reason = 'automatic_alarm'
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_linkage_records_time_object
        ON linkage_records(created_at, object_id)
        """
    )
