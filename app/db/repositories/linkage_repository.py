from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repositories.base import EntityRepository

ALARM_TYPES_WITH_WILDCARD = frozenset(
    {"alarm_low", "alarm_high", "over_range", "fault", "offline", "disabled", "warming", "*"}
)
MAX_TEXT = 512


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LinkageObjectRepository(EntityRepository):
    table_name = "linkage_objects"
    allowed_sort_columns = frozenset({"id", "name", "object_type", "created_at", "updated_at"})
    default_sort = "name"

    def list_active(self):
        return self.fetch_all("SELECT * FROM linkage_objects WHERE deleted_at IS NULL ORDER BY name ASC")

    def find_active_by_id(self, object_id: int):
        return self.fetch_one("SELECT * FROM linkage_objects WHERE id = ? AND deleted_at IS NULL", (_positive_int(object_id, "object_id"),))

    def find_active_by_name(self, name: str):
        return self.fetch_one("SELECT * FROM linkage_objects WHERE name = ? AND deleted_at IS NULL", (_text(name, 120, "name"),))

    def create(self, values: dict[str, Any]) -> int:
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO linkage_objects(object_type, name, location, adapter_type, is_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _code(values["object_type"], 40, "object_type"),
                _text(values["name"], 120, "name"),
                _optional_text(values.get("location"), 200, "location"),
                _choice(values.get("adapter_type", "simulated"), {"simulated", "real"}, "adapter_type"),
                1 if _bool(values.get("is_enabled", True), "is_enabled") else 0,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update(self, object_id: int, values: dict[str, Any]) -> None:
        now = _now()
        self.execute(
            """
            UPDATE linkage_objects
            SET object_type = ?, name = ?, location = ?, adapter_type = ?, is_enabled = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                _code(values["object_type"], 40, "object_type"),
                _text(values["name"], 120, "name"),
                _optional_text(values.get("location"), 200, "location"),
                _choice(values.get("adapter_type", "simulated"), {"simulated", "real"}, "adapter_type"),
                1 if _bool(values.get("is_enabled", True), "is_enabled") else 0,
                now,
                _positive_int(object_id, "object_id"),
            ),
        )

    def soft_delete(self, object_id: int) -> None:
        now = _now()
        self.execute(
            """
            UPDATE linkage_objects SET deleted_at = ?, is_enabled = 0, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, _positive_int(object_id, "object_id")),
        )


class LinkageRuleRepository(EntityRepository):
    table_name = "linkage_rules"
    allowed_sort_columns = frozenset({"id", "name", "is_enabled", "created_at", "updated_at"})
    default_sort = "name"

    def list_active(self):
        return self.fetch_all("SELECT * FROM linkage_rules WHERE deleted_at IS NULL ORDER BY name ASC")

    def find_active_by_id(self, rule_id: int):
        return self.fetch_one("SELECT * FROM linkage_rules WHERE id = ? AND deleted_at IS NULL", (_positive_int(rule_id, "rule_id"),))

    def create(self, values: dict[str, Any]) -> int:
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO linkage_rules(
                name, detector_id, alarm_type, alarm_level, object_id, action, trigger_delay_sec,
                recovery_action, is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _rule_params(values, now),
        )
        return int(cursor.lastrowid)

    def update(self, rule_id: int, values: dict[str, Any]) -> None:
        now = _now()
        self.execute(
            """
            UPDATE linkage_rules
            SET name = ?, detector_id = ?, alarm_type = ?, alarm_level = ?, object_id = ?, action = ?,
                trigger_delay_sec = ?, recovery_action = ?, is_enabled = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            _rule_params(values, now, _positive_int(rule_id, "rule_id")),
        )

    def soft_delete(self, rule_id: int) -> None:
        now = _now()
        self.execute(
            """
            UPDATE linkage_rules SET deleted_at = ?, is_enabled = 0, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, _positive_int(rule_id, "rule_id")),
        )

    def list_matching(self, *, detector_id: int, alarm_type: str, alarm_level: int | None):
        params: list[object] = [_positive_int(detector_id, "detector_id"), _alarm_type(alarm_type), "*"]
        level_clause = "(alarm_level IS NULL"
        if alarm_level is not None:
            level_clause += " OR alarm_level = ?"
            params.append(_optional_non_negative_int(alarm_level, "alarm_level"))
        level_clause += ")"
        return self.fetch_all(
            f"""
            SELECT linkage_rules.* FROM linkage_rules
            JOIN linkage_objects ON linkage_objects.id = linkage_rules.object_id
            WHERE linkage_rules.deleted_at IS NULL
              AND linkage_objects.deleted_at IS NULL
              AND linkage_rules.is_enabled = 1
              AND linkage_objects.is_enabled = 1
              AND (linkage_rules.detector_id IS NULL OR linkage_rules.detector_id = ?)
              AND linkage_rules.alarm_type IN (?, ?)
              AND {level_clause}
            ORDER BY linkage_rules.id ASC
            """,
            tuple(params),
        )


class LinkageRecordRepository(EntityRepository):
    table_name = "linkage_records"
    allowed_sort_columns = frozenset({"id", "object_id", "result", "created_at"})
    default_sort = "created_at"

    def add(
        self,
        *,
        object_id: int,
        action: str,
        trigger_reason: str,
        result: str,
        message: str = "",
        rule_id: int | None = None,
        alarm_record_id: int | None = None,
        user_id: int | None = None,
        user_name: str | None = None,
    ) -> tuple[int, bool]:
        cursor = self.execute(
            """
            INSERT OR IGNORE INTO linkage_records(
                object_id, rule_id, alarm_record_id, action, trigger_reason, user_id, user_name,
                result, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _positive_int(object_id, "object_id"),
                _optional_positive_int(rule_id, "rule_id"),
                _optional_positive_int(alarm_record_id, "alarm_record_id"),
                _code(action, 80, "action"),
                _code(trigger_reason, 80, "trigger_reason"),
                _optional_positive_int(user_id, "user_id"),
                _optional_text(user_name, 80, "user_name"),
                _code(result, 40, "result"),
                _text(message, MAX_TEXT, "message") if message else "",
                _now(),
            ),
        )
        if cursor.rowcount == 0 and rule_id is not None and alarm_record_id is not None:
            row = self.fetch_one(
                """
                SELECT * FROM linkage_records
                WHERE alarm_record_id = ? AND rule_id = ? AND trigger_reason = ?
                """,
                (alarm_record_id, rule_id, trigger_reason),
            )
            return (int(row["id"]), False) if row is not None else (0, False)
        return int(cursor.lastrowid), True

    def list_for_alarm(self, alarm_record_id: int):
        return self.fetch_all(
            "SELECT * FROM linkage_records WHERE alarm_record_id = ? ORDER BY created_at ASC",
            (_positive_int(alarm_record_id, "alarm_record_id"),),
        )


def _rule_params(values: dict[str, Any], now: str, rule_id: int | None = None) -> tuple[object, ...]:
    params: tuple[object, ...] = (
        _text(values["name"], 120, "name"),
        _optional_positive_int(values.get("detector_id"), "detector_id"),
        _alarm_type_with_wildcard(values["alarm_type"]),
        _optional_non_negative_int(values.get("alarm_level"), "alarm_level"),
        _positive_int(values["object_id"], "object_id"),
        _code(values["action"], 80, "action"),
        _int_range(values.get("trigger_delay_sec", 0), 0, 86400, "trigger_delay_sec"),
        _optional_code(values.get("recovery_action"), 80, "recovery_action"),
        1 if _bool(values.get("is_enabled", True), "is_enabled") else 0,
        now,
        now,
    )
    if rule_id is not None:
        return params[:-1] + (rule_id,)
    return params


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _optional_positive_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field_name)


def _optional_non_negative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return value


def _int_range(value: object, minimum: int, maximum: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{field_name} out of range")
    return value


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _choice(value: object, choices: set[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{field_name} unsupported")
    return value


def _alarm_type(value: str) -> str:
    if value not in ALARM_TYPES_WITH_WILDCARD or value == "*":
        raise ValueError("unsupported alarm_type")
    return value


def _alarm_type_with_wildcard(value: str) -> str:
    if value not in ALARM_TYPES_WITH_WILDCARD:
        raise ValueError("unsupported alarm_type")
    return value


def _optional_code(value: object, max_length: int, field_name: str) -> str | None:
    if value is None:
        return None
    return _code(value, max_length, field_name)


def _code(value: object, max_length: int, field_name: str) -> str:
    text = _text(value, max_length, field_name)
    if not text.replace("_", "").replace(":", "").replace(".", "").replace("-", "").isalnum():
        raise ValueError(f"{field_name} contains unsupported characters")
    return text


def _optional_text(value: object, max_length: int, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, max_length, field_name)


def _text(value: object, max_length: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized[:max_length]
