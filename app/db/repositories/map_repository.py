from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repositories.base import EntityRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MapRepository(EntityRepository):
    table_name = "maps"
    allowed_sort_columns = frozenset({"id", "name", "relative_path", "created_at", "updated_at"})
    default_sort = "name"

    def list_active(self):
        return self.fetch_all("SELECT * FROM maps WHERE deleted_at IS NULL ORDER BY name ASC")

    def list_enabled(self):
        return self.fetch_all(
            "SELECT * FROM maps WHERE deleted_at IS NULL AND is_enabled = 1 ORDER BY name ASC, id ASC"
        )

    def find_active_by_id(self, map_id: int):
        return self.fetch_one("SELECT * FROM maps WHERE id = ? AND deleted_at IS NULL", (map_id,))

    def create(self, values: dict[str, Any]) -> int:
        now = _now()
        content_hash = values.get("content_hash")
        if self._has_column("content_hash"):
            cursor = self.execute(
                """
                INSERT INTO maps(
                    name, safe_filename, original_filename, relative_path, size_bytes, content_hash,
                    is_enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["name"],
                    values["safe_filename"],
                    values["original_filename"],
                    values["relative_path"],
                    values["size_bytes"],
                    content_hash,
                    1 if values.get("is_enabled", True) else 0,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)
        cursor = self.execute(
            """
            INSERT INTO maps(
                name, safe_filename, original_filename, relative_path, size_bytes,
                is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["name"],
                values["safe_filename"],
                values["original_filename"],
                values["relative_path"],
                values["size_bytes"],
                1 if values.get("is_enabled", True) else 0,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update(self, map_id: int, *, name: str, is_enabled: bool = True) -> None:
        now = _now()
        self.execute(
            """
            UPDATE maps SET name = ?, is_enabled = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (name, 1 if is_enabled else 0, now, map_id),
        )

    def soft_delete(self, map_id: int) -> None:
        # [待确认] Soft delete preserves map metadata for audit and backup history.
        now = _now()
        self.execute(
            """
            UPDATE maps
            SET deleted_at = ?, is_enabled = 0, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, map_id),
        )

    def _has_column(self, column_name: str) -> bool:
        return any(row["name"] == column_name for row in self.fetch_all("PRAGMA table_info(maps)"))


class MapPointRepository(EntityRepository):
    table_name = "map_points"
    allowed_sort_columns = frozenset({"id", "map_id", "detector_id", "created_at", "updated_at"})
    default_sort = "id"

    def list_active_for_map(self, map_id: int):
        return self.fetch_all(
            "SELECT * FROM map_points WHERE map_id = ? AND deleted_at IS NULL ORDER BY id ASC",
            (map_id,),
        )

    def list_active_for_map_with_detectors(self, map_id: int):
        return self.fetch_all(
            """
            SELECT
                mp.*,
                d.position_code AS detector_position_code,
                d.name AS detector_name,
                d.unit AS detector_unit,
                d.port_id AS detector_port_id,
                d.controller_id AS detector_controller_id,
                d.is_enabled AS detector_is_enabled,
                d.deleted_at AS detector_deleted_at,
                c.name AS controller_name
            FROM map_points mp
            LEFT JOIN detectors d ON d.id = mp.detector_id
            LEFT JOIN controllers c ON c.id = d.controller_id AND c.deleted_at IS NULL
            WHERE mp.map_id = ? AND mp.deleted_at IS NULL
            ORDER BY mp.id ASC
            """,
            (map_id,),
        )

    def find_active_by_detector(self, detector_id: int):
        return self.fetch_one(
            "SELECT * FROM map_points WHERE detector_id = ? AND deleted_at IS NULL",
            (detector_id,),
        )

    def find_active_by_id(self, point_id: int):
        return self.fetch_one("SELECT * FROM map_points WHERE id = ? AND deleted_at IS NULL", (point_id,))

    def count_for_map(self, map_id: int) -> int:
        row = self.fetch_one(
            "SELECT COUNT(*) AS total FROM map_points WHERE map_id = ? AND deleted_at IS NULL",
            (map_id,),
        )
        return int(row["total"] if row is not None else 0)

    def upsert_for_detector(
        self,
        *,
        map_id: int,
        detector_id: int,
        x_ratio: float,
        y_ratio: float,
        label: str | None,
    ) -> int:
        existing = self.find_active_by_detector(detector_id)
        now = _now()
        if existing is not None:
            point_id = int(existing["id"])
            self.execute(
                """
                UPDATE map_points
                SET map_id = ?, x_ratio = ?, y_ratio = ?, label = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (map_id, x_ratio, y_ratio, label, now, point_id),
            )
            return point_id
        cursor = self.execute(
            """
            INSERT INTO map_points(map_id, detector_id, label, x_ratio, y_ratio, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (map_id, detector_id, label, x_ratio, y_ratio, now, now),
        )
        return int(cursor.lastrowid)

    def soft_delete(self, point_id: int) -> None:
        now = _now()
        self.execute(
            "UPDATE map_points SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, now, point_id),
        )
