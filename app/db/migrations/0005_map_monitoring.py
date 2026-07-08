from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    # Existing deployments may already have maps from the device-configuration phase;
    # add monitoring metadata only when the column is absent.
    if not _column_exists(connection, "maps", "content_hash"):
        connection.execute(
            """
            ALTER TABLE maps
            ADD COLUMN content_hash TEXT CHECK(content_hash IS NULL OR length(content_hash) = 64)
            """
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_maps_enabled_active
        ON maps(is_enabled, deleted_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_maps_content_hash_active
        ON maps(content_hash)
        WHERE deleted_at IS NULL AND content_hash IS NOT NULL
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


def _column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(row[1] == column_name for row in connection.execute(f"PRAGMA table_info({table_name})"))
