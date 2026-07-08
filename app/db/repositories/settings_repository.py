from __future__ import annotations

from datetime import datetime, timezone

from app.db.repositories.base import EntityRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SettingsRepository(EntityRepository):
    table_name = "system_settings"
    primary_key = "key"
    allowed_sort_columns = frozenset({"key", "updated_at", "created_at"})
    default_sort = "key"

    def get(self, key: str):
        return self.fetch_one("SELECT * FROM system_settings WHERE key = ?", (key,))

    def get_value(self, key: str, default: str | None = None) -> str | None:
        row = self.get(key)
        return str(row["value"]) if row is not None else default

    def set_value(self, key: str, value: str, *, value_type: str = "string") -> None:
        now = _now()
        self.execute(
            """
            INSERT INTO system_settings(key, value, value_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           value_type = excluded.value_type,
                                           updated_at = excluded.updated_at
            """,
            (key, value, value_type, now, now),
        )
