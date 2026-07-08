from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repositories.base import EntityRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BackupSettingsRepository(EntityRepository):
    table_name = "backup_settings"
    allowed_sort_columns = frozenset({"id", "scheduled_enabled", "updated_at", "created_at"})
    default_sort = "id"

    def get(self):
        row = self.fetch_one("SELECT * FROM backup_settings WHERE id = 1")
        if row is not None:
            return row
        self.upsert(
            scheduled_enabled=False,
            interval_hours=24,
            backup_time="02:00",
            target_directory="backups",
            keep_last=10,
            failure_notify_enabled=True,
        )
        return self.fetch_one("SELECT * FROM backup_settings WHERE id = 1")

    def upsert(
        self,
        *,
        scheduled_enabled: bool,
        interval_hours: int,
        backup_time: str,
        target_directory: str,
        keep_last: int,
        failure_notify_enabled: bool,
    ) -> None:
        now = _now()
        self.execute(
            """
            INSERT INTO backup_settings(
                id, scheduled_enabled, interval_hours, backup_time, target_directory,
                keep_last, failure_notify_enabled, created_at, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                scheduled_enabled = excluded.scheduled_enabled,
                interval_hours = excluded.interval_hours,
                backup_time = excluded.backup_time,
                target_directory = excluded.target_directory,
                keep_last = excluded.keep_last,
                failure_notify_enabled = excluded.failure_notify_enabled,
                updated_at = excluded.updated_at
            """,
            (
                1 if scheduled_enabled else 0,
                interval_hours,
                backup_time,
                target_directory,
                keep_last,
                1 if failure_notify_enabled else 0,
                now,
                now,
            ),
        )


class BackupRecordRepository(EntityRepository):
    table_name = "backup_records"
    allowed_sort_columns = frozenset({"id", "backup_type", "result", "created_at"})
    default_sort = "created_at"

    def add(
        self,
        *,
        backup_type: str,
        result: str,
        file_name: str | None = None,
        relative_path: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        message: str = "",
    ) -> int:
        cursor = self.execute(
            """
            INSERT INTO backup_records(
                backup_type, result, file_name, relative_path, size_bytes, sha256, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _choice(backup_type, {"manual", "scheduled", "pre_restore"}, "backup_type"),
                _choice(result, {"success", "failed"}, "result"),
                _optional_text(file_name, 180),
                _optional_text(relative_path, 260),
                _optional_size(size_bytes),
                _optional_sha256(sha256),
                _text(message, 500),
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    def list_successes_for_retention(self, *, backup_type: str, keep_last: int):
        return self.fetch_all(
            """
            SELECT * FROM backup_records
            WHERE backup_type = ? AND result = 'success' AND relative_path IS NOT NULL
            ORDER BY created_at DESC, id DESC
            LIMIT -1 OFFSET ?
            """,
            (_choice(backup_type, {"manual", "scheduled", "pre_restore"}, "backup_type"), _positive_int(keep_last, "keep_last")),
        )

    def latest_success(self, *, backup_type: str):
        return self.fetch_one(
            """
            SELECT * FROM backup_records
            WHERE backup_type = ? AND result = 'success'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (_choice(backup_type, {"manual", "scheduled", "pre_restore"}, "backup_type"),),
        )


def row_to_dict(row: Any | None) -> dict[str, object]:
    return {key: row[key] for key in row.keys()} if row is not None else {}


def _choice(value: str, choices: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{field}:unsupported value")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field}:must be positive")
    return value


def _optional_size(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("size_bytes:must be non-negative")
    return value


def _optional_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or not all(ch in "0123456789abcdef" for ch in value):
        raise ValueError("sha256:invalid")
    return value


def _optional_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text = _text(value, max_length)
    return text or None


def _text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value must be a string")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return normalized[:max_length]
