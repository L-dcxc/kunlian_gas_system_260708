from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'operator')),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            permission_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deleted_at TEXT
        )
        """
    )
    # SQLite partial unique indexes give the service-level administrator rule a
    # database backstop without preventing historical soft-deleted admin rows.
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_single_active_admin
        ON users(role)
        WHERE role = 'admin' AND deleted_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_active_role
        ON users(is_active, role, deleted_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS license_info (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            machine_fingerprint_hash TEXT NOT NULL,
            license_payload TEXT NOT NULL,
            authorization_signature TEXT NOT NULL,
            integrity_signature TEXT NOT NULL,
            status TEXT NOT NULL,
            activated_at TEXT NOT NULL,
            expires_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_license_info_status
        ON license_info(status, updated_at)
        """
    )
