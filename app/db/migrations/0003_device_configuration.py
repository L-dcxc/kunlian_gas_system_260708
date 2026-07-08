from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
            channel_type TEXT NOT NULL CHECK(channel_type IN ('serial', 'tcp')),
            serial_port_name TEXT CHECK(serial_port_name IS NULL OR length(serial_port_name) BETWEEN 1 AND 40),
            baud_rate INTEGER CHECK(baud_rate IS NULL OR baud_rate BETWEEN 1200 AND 115200),
            data_bits INTEGER CHECK(data_bits IS NULL OR data_bits BETWEEN 5 AND 8),
            parity TEXT CHECK(parity IS NULL OR parity IN ('N', 'E', 'O')),
            stop_bits REAL CHECK(stop_bits IS NULL OR stop_bits IN (1, 1.5, 2)),
            tcp_host TEXT CHECK(tcp_host IS NULL OR length(tcp_host) BETWEEN 1 AND 253),
            tcp_port INTEGER CHECK(tcp_port IS NULL OR tcp_port BETWEEN 1 AND 65535),
            poll_interval_ms INTEGER NOT NULL CHECK(poll_interval_ms BETWEEN 100 AND 600000),
            timeout_ms INTEGER NOT NULL CHECK(timeout_ms BETWEEN 100 AND 60000),
            failure_threshold INTEGER NOT NULL CHECK(failure_threshold BETWEEN 1 AND 20),
            reconnect_interval_ms INTEGER NOT NULL CHECK(reconnect_interval_ms BETWEEN 500 AND 600000),
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ports_name_active
        ON ports(name)
        WHERE deleted_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ports_channel_enabled
        ON ports(channel_type, is_enabled, deleted_at)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gas_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
            unit TEXT NOT NULL CHECK(length(unit) BETWEEN 1 AND 32),
            range_min REAL NOT NULL,
            range_max REAL NOT NULL,
            default_alarm_low REAL,
            default_alarm_high REAL,
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT,
            CHECK(range_min < range_max),
            CHECK(default_alarm_low IS NULL OR (default_alarm_low >= range_min AND default_alarm_low <= range_max)),
            CHECK(default_alarm_high IS NULL OR (default_alarm_high >= range_min AND default_alarm_high <= range_max)),
            CHECK(default_alarm_low IS NULL OR default_alarm_high IS NULL OR default_alarm_low <= default_alarm_high)
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gas_types_name_active
        ON gas_types(name)
        WHERE deleted_at IS NULL
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS controllers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_id INTEGER NOT NULL REFERENCES ports(id),
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
            address INTEGER NOT NULL CHECK(address BETWEEN 1 AND 247),
            model TEXT CHECK(model IS NULL OR length(model) <= 80),
            detector_count INTEGER NOT NULL DEFAULT 0 CHECK(detector_count BETWEEN 0 AND 4096),
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_controllers_port_address_active
        ON controllers(port_id, address)
        WHERE deleted_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_controllers_port_enabled
        ON controllers(port_id, is_enabled, deleted_at)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS detectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            controller_id INTEGER REFERENCES controllers(id),
            port_id INTEGER NOT NULL REFERENCES ports(id),
            position_code TEXT NOT NULL CHECK(length(position_code) BETWEEN 1 AND 80),
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
            model TEXT CHECK(model IS NULL OR length(model) <= 80),
            protocol_address INTEGER NOT NULL CHECK(protocol_address BETWEEN 1 AND 247),
            register_index INTEGER NOT NULL CHECK(register_index BETWEEN 0 AND 65535),
            gas_type_id INTEGER NOT NULL REFERENCES gas_types(id),
            unit TEXT NOT NULL CHECK(length(unit) BETWEEN 1 AND 32),
            range_min REAL NOT NULL,
            range_max REAL NOT NULL,
            alarm_low REAL,
            alarm_high REAL,
            alarm_type TEXT NOT NULL DEFAULT 'low_high' CHECK(alarm_type IN ('none', 'low', 'high', 'low_high')),
            sound_enabled INTEGER NOT NULL DEFAULT 1 CHECK(sound_enabled IN (0, 1)),
            store_interval_sec INTEGER NOT NULL CHECK(store_interval_sec BETWEEN 1 AND 86400),
            sensor_life_until TEXT,
            calibration_cycle_days INTEGER CHECK(
                calibration_cycle_days IS NULL OR calibration_cycle_days BETWEEN 1 AND 3650
            ),
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT,
            CHECK(range_min < range_max),
            CHECK(alarm_low IS NULL OR (alarm_low >= range_min AND alarm_low <= range_max)),
            CHECK(alarm_high IS NULL OR (alarm_high >= range_min AND alarm_high <= range_max)),
            CHECK(alarm_low IS NULL OR alarm_high IS NULL OR alarm_low <= alarm_high)
        )
        """
    )
    # [待确认] Detector position codes are treated as globally unique among active rows until site rules say otherwise.
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_detectors_position_code_active
        ON detectors(position_code)
        WHERE deleted_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_detectors_controller_port
        ON detectors(controller_id, port_id, is_enabled, deleted_at)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY CHECK(length(key) BETWEEN 1 AND 80),
            value TEXT NOT NULL CHECK(length(value) <= 1000),
            value_type TEXT NOT NULL DEFAULT 'string' CHECK(value_type IN ('string', 'integer', 'boolean', 'json')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO system_settings(key, value, value_type)
        VALUES ('protocol_mode', 'protocol_1', 'string')
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS maps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
            safe_filename TEXT NOT NULL CHECK(length(safe_filename) BETWEEN 1 AND 180),
            original_filename TEXT NOT NULL CHECK(length(original_filename) BETWEEN 1 AND 180),
            relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 260),
            size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_maps_relative_path_active
        ON maps(relative_path)
        WHERE deleted_at IS NULL
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS map_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_id INTEGER NOT NULL REFERENCES maps(id),
            detector_id INTEGER NOT NULL REFERENCES detectors(id),
            label TEXT CHECK(label IS NULL OR length(label) <= 120),
            x_ratio REAL NOT NULL CHECK(x_ratio >= 0 AND x_ratio <= 1),
            y_ratio REAL NOT NULL CHECK(y_ratio >= 0 AND y_ratio <= 1),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_map_points_detector_active
        ON map_points(detector_id)
        WHERE deleted_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_map_points_map_active
        ON map_points(map_id, deleted_at)
        """
    )
