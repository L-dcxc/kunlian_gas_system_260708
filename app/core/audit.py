from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.core.logging import Redactor, get_logger

MAX_DETAIL_LENGTH = 2000


@dataclass(frozen=True)
class AuditRecord:
    action_type: str
    result: str
    actor_id: int | None = None
    actor_name: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AuditSink(Protocol):
    def write(self, record: AuditRecord) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self.records.append(record)


class AuditLogger:
    def __init__(self, sink: AuditSink | None = None) -> None:
        self._sink = sink or InMemoryAuditSink()
        self._redactor = Redactor()
        self._logger = get_logger("audit")

    @property
    def sink(self) -> AuditSink:
        return self._sink

    def record(self, record: AuditRecord) -> None:
        safe_record = sanitize_record(record, self._redactor)
        self._sink.write(safe_record)
        self._logger.info(
            "audit action=%s result=%s actor=%s target=%s summary=%s",
            safe_record.action_type,
            safe_record.result,
            safe_record.actor_name or safe_record.actor_id or "system",
            safe_record.target_type or "-",
            safe_record.summary,
        )

    def permission_denied(
        self,
        action_type: str,
        actor_id: int | None = None,
        actor_name: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        summary: str = "permission denied",
    ) -> None:
        self.record(
            AuditRecord(
                action_type=action_type,
                result="denied",
                actor_id=actor_id,
                actor_name=actor_name,
                target_type=target_type,
                target_id=target_id,
                summary=summary,
            )
        )


def sanitize_record(record: AuditRecord, redactor: Redactor | None = None) -> AuditRecord:
    redactor = redactor or Redactor()
    return AuditRecord(
        action_type=_limit(redactor.redact(record.action_type), 80),
        result=_limit(redactor.redact(record.result), 40),
        actor_id=record.actor_id,
        actor_name=_optional_limit(redactor.redact(record.actor_name), 80) if record.actor_name else None,
        target_type=_optional_limit(redactor.redact(record.target_type), 80) if record.target_type else None,
        target_id=_optional_limit(redactor.redact(record.target_id), 120) if record.target_id else None,
        summary=_limit(redactor.redact(record.summary), 500),
        details=_sanitize_details(record.details, redactor),
        created_at=record.created_at,
    )


def _sanitize_details(details: dict[str, Any], redactor: Redactor) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in details.items():
        safe_key = _limit(redactor.redact(key), 80)
        safe[safe_key] = _limit(redactor.redact(value), MAX_DETAIL_LENGTH)
    return safe


def _limit(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _optional_limit(value: str, limit: int) -> str | None:
    limited = _limit(value, limit)
    return limited or None
