from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.audit import AuditRecord, sanitize_record
from app.db.repositories.base import EntityRepository


class OperationLogRepository(EntityRepository):
    table_name = "operation_logs"
    allowed_sort_columns = frozenset({"id", "created_at", "actor_id", "action_type", "result"})
    default_sort = "created_at"

    def add(
        self,
        *,
        action_type: str,
        result: str,
        actor_id: int | None = None,
        actor_name: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        summary: str = "",
        details: dict[str, Any] | None = None,
    ) -> int:
        safe = sanitize_record(
            AuditRecord(
                action_type=_safe_code(action_type, 80),
                result=_safe_code(result, 40),
                actor_id=actor_id,
                actor_name=_optional_text(actor_name, 80),
                target_type=_optional_code(target_type, 80),
                target_id=_optional_text(target_id, 120),
                summary=_safe_text(summary, 500),
                details=_safe_details(details or {}),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        cursor = self.execute(
            """
            INSERT INTO operation_logs(
                action_type, result, actor_id, actor_name, target_type,
                target_id, summary, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                safe.action_type,
                safe.result,
                safe.actor_id,
                safe.actor_name,
                safe.target_type,
                safe.target_id,
                safe.summary,
                json.dumps(safe.details, ensure_ascii=False, sort_keys=True),
                safe.created_at,
            ),
        )
        return int(cursor.lastrowid)

    def list_for_action(
        self,
        *,
        action_type: str | None = None,
        actor_id: int | None = None,
        page: int = 1,
        per_page: int = 20,
    ):
        clauses: list[str] = []
        parameters: list[object] = []
        if action_type is not None:
            clauses.append("action_type = ?")
            parameters.append(_safe_code(action_type, 80))
        if actor_id is not None:
            if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
                raise ValueError("actor_id must be a positive integer")
            clauses.append("actor_id = ?")
            parameters.append(actor_id)
        return self.list_page(
            page=page,
            per_page=per_page,
            sort_by="created_at",
            sort_direction="DESC",
            where_clause=" AND ".join(clauses),
            parameters=tuple(parameters),
        )


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in details.items():
        safe[_safe_code(str(key), 80)] = _safe_text(str(value), 2000)
    return safe


def _optional_code(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return _safe_code(value, max_length)


def _optional_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text = _safe_text(value, max_length)
    return text or None


def _safe_code(value: str, max_length: int) -> str:
    text = _safe_text(value, max_length)
    if not text:
        raise ValueError("code is required")
    if not text.replace("_", "").replace(":", "").replace(".", "").replace("-", "").isalnum():
        raise ValueError("code contains unsupported characters")
    return text


def _safe_text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value must be a string")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return normalized[:max_length]
