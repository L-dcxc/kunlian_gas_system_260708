from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.audit import AuditLogger
from app.core.logging import get_logger
from app.db.connection import Database
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.errors import PermissionDenied

DENIED_MESSAGE = "当前用户无权执行该操作"


class PermissionUser(Protocol):
    id: int | None
    username: str
    role: str
    permissions: set[str] | tuple[str, ...] | list[str]
    is_active: bool


@dataclass(frozen=True, slots=True)
class SimplePermissionUser:
    id: int | None
    username: str
    role: str
    permissions: tuple[str, ...] = ()
    is_active: bool = True


class PermissionGuard:
    def __init__(
        self,
        *,
        audit: AuditLogger | None = None,
        uow: UnitOfWork | None = None,
        database: Database | None = None,
    ) -> None:
        self._audit = audit or AuditLogger()
        self._uow = uow
        self._database = database
        self._logger = get_logger("services.permission_guard")

    def require(self, user: PermissionUser, action: str, target_summary: str) -> None:
        safe_action = _safe_code(action)
        safe_target = _safe_text(target_summary, 200)
        if not _has_permission(user, safe_action):
            # Permission failures are persisted/audited before raising so callers
            # cannot accidentally continue with writes after a denied decision.
            self._record_denial(user, safe_action, safe_target)
            raise PermissionDenied(DENIED_MESSAGE)

    def _record_denial(self, user: PermissionUser, action: str, target_summary: str) -> None:
        actor_id = getattr(user, "id", None)
        actor_name = _safe_text(getattr(user, "username", "unknown"), 80)
        try:
            if self._database is not None:
                # Denial logs are written before the caller's business transaction can
                # roll back, preserving audit evidence while still aborting the write.
                with UnitOfWork(self._database) as audit_uow:
                    self._add_operation_log(audit_uow, actor_id, actor_name, action, target_summary)
                    audit_uow.commit()
            elif self._uow is not None and self._uow.connection is not None:
                self._add_operation_log(self._uow, actor_id, actor_name, action, target_summary)
        except Exception as exc:
            self._logger.warning("permission denial operation log failed: %s", exc.__class__.__name__)
        self._audit.permission_denied(
            action_type="permission_denied",
            actor_id=actor_id,
            actor_name=actor_name,
            target_type="permission",
            summary="权限不足，操作已拒绝。",
        )

    def _add_operation_log(
        self,
        uow: UnitOfWork,
        actor_id: int | None,
        actor_name: str,
        action: str,
        target_summary: str,
    ) -> None:
        OperationLogRepository(uow).add(
            action_type="permission_denied",
            result="denied",
            actor_id=actor_id,
            actor_name=actor_name,
            target_type="permission",
            target_id=None,
            summary="权限不足，操作已拒绝。",
            details={"action": action, "target": target_summary},
        )


def _has_permission(user: PermissionUser, action: str) -> bool:
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "role", "") == "admin":
        return True
    permissions = set(getattr(user, "permissions", ()) or ())
    return action in permissions or "*" in permissions


def _safe_code(value: str) -> str:
    cleaned = _safe_text(value, 80)
    if not cleaned.replace("_", "").replace(":", "").replace(".", "").replace("-", "").isalnum():
        raise ValueError("action contains unsupported characters")
    return cleaned


def _safe_text(value: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text value is required")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return normalized[:max_length]
