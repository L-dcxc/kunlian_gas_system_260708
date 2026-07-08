from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.db.connection import Database
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore, hash_password
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Role, SensitiveAction, normalize_role, should_increment_permission_version


@dataclass(frozen=True, slots=True)
class CreateUserCommand:
    username: str
    password: str
    role: str = Role.OPERATOR.value
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class UpdateUserCommand:
    username: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


@dataclass(frozen=True, slots=True)
class UserView:
    id: int
    username: str
    role: str
    is_active: bool
    permission_version: int
    created_at: str
    updated_at: str


class UserService:
    def __init__(self, database: Database, session_store: SessionStore) -> None:
        self._database = database
        self._session_store = session_store

    def create_user(self, session_or_id: Session | str, command: CreateUserCommand) -> ServiceResult[UserView]:
        actor = self._require_user_management(session_or_id, SensitiveAction.USER_CREATE.value, "新增用户")
        if isinstance(actor, ServiceResult):
            return actor
        try:
            username = _normalize_username(command.username)
            role = normalize_role(command.role)
            _validate_username(username)
            password_hash, password_salt = hash_password(command.password)
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=str(exc))

        try:
            with UnitOfWork(self._database) as uow:
                users = UserRepository(uow)
                if role == Role.ADMIN.value and users.count_active_admins() > 0:
                    return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="管理员账号只能有一个")
                user_id = users.create_user(
                    username=username,
                    password_hash=password_hash,
                    password_salt=password_salt,
                    role=role,
                    is_active=command.is_active,
                )
                row = users.find_by_id(user_id)
                OperationLogRepository(uow).add(
                    action_type="users.create",
                    result="success",
                    actor_id=actor.user_id,
                    actor_name=actor.username,
                    target_type="user",
                    target_id=str(user_id),
                    summary="新增用户。",
                    details={"username": username, "role": role},
                )
                uow.commit()
            return ServiceResult.ok(_row_to_view(row))
        except sqlite3.IntegrityError:
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="用户名已存在或管理员唯一约束冲突")

    def update_user(
        self,
        session_or_id: Session | str,
        user_id: int,
        command: UpdateUserCommand,
    ) -> ServiceResult[UserView]:
        actor = self._require_user_management(session_or_id, SensitiveAction.USER_UPDATE.value, f"修改用户 {user_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(user_id):
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message="用户 ID 无效")
        try:
            username = _normalize_username(command.username) if command.username is not None else None
            if username is not None:
                _validate_username(username)
            role = normalize_role(command.role) if command.role is not None else None
            password_hash: str | None = None
            password_salt: str | None = None
            if command.password is not None:
                password_hash, password_salt = hash_password(command.password)
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=str(exc))

        try:
            with UnitOfWork(self._database) as uow:
                users = UserRepository(uow)
                current = users.find_by_id(user_id)
                if current is None or current["deleted_at"] is not None:
                    return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="用户不存在")
                current_role = str(current["role"])
                current_active = int(current["is_active"]) == 1
                next_role = role or current_role
                next_active = command.is_active if command.is_active is not None else current_active
                self._ensure_admin_invariants(users, user_id, current_role, current_active, next_role, next_active)
                role_changed = role is not None and role != current_role
                active_changed = command.is_active is not None and command.is_active != current_active
                users.update_user(
                    user_id,
                    username=username,
                    role=role,
                    is_active=command.is_active,
                    password_hash=password_hash,
                    password_salt=password_salt,
                    increment_permission_version=should_increment_permission_version(
                        role_changed=role_changed,
                        active_changed=active_changed,
                    ),
                )
                row = users.find_by_id(user_id)
                OperationLogRepository(uow).add(
                    action_type="users.update",
                    result="success",
                    actor_id=actor.user_id,
                    actor_name=actor.username,
                    target_type="user",
                    target_id=str(user_id),
                    summary="修改用户。",
                    details={"username": username or str(current["username"]), "role": next_role},
                )
                uow.commit()
            return ServiceResult.ok(_row_to_view(row))
        except sqlite3.IntegrityError:
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="用户名已存在或管理员唯一约束冲突")
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=str(exc))

    def disable_user(self, session_or_id: Session | str, user_id: int) -> ServiceResult[None]:
        actor = self._require_user_management(session_or_id, SensitiveAction.USER_DISABLE.value, f"禁用用户 {user_id}")
        if isinstance(actor, ServiceResult):
            return actor
        return self._set_user_active(actor, user_id, False, action_type="users.disable", summary="禁用用户。")

    def delete_user(self, session_or_id: Session | str, user_id: int) -> ServiceResult[None]:
        actor = self._require_user_management(session_or_id, SensitiveAction.USER_DELETE.value, f"删除用户 {user_id}")
        if isinstance(actor, ServiceResult):
            return actor
        if not _valid_id(user_id):
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message="用户 ID 无效")
        try:
            with UnitOfWork(self._database) as uow:
                users = UserRepository(uow)
                current = users.find_by_id(user_id)
                if current is None or current["deleted_at"] is not None:
                    return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="用户不存在")
                self._ensure_admin_invariants(
                    users,
                    user_id,
                    str(current["role"]),
                    int(current["is_active"]) == 1,
                    str(current["role"]),
                    False,
                )
                users.soft_delete(user_id)
                OperationLogRepository(uow).add(
                    action_type="users.delete",
                    result="success",
                    actor_id=actor.user_id,
                    actor_name=actor.username,
                    target_type="user",
                    target_id=str(user_id),
                    summary="删除用户。",
                    details={"username": str(current["username"]), "role": str(current["role"])},
                )
                uow.commit()
            return ServiceResult.ok(None)
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=str(exc))

    def _set_user_active(
        self,
        actor: Session,
        user_id: int,
        is_active: bool,
        *,
        action_type: str,
        summary: str,
    ) -> ServiceResult[None]:
        if not _valid_id(user_id):
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message="用户 ID 无效")
        try:
            with UnitOfWork(self._database) as uow:
                users = UserRepository(uow)
                current = users.find_by_id(user_id)
                if current is None or current["deleted_at"] is not None:
                    return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="用户不存在")
                self._ensure_admin_invariants(
                    users,
                    user_id,
                    str(current["role"]),
                    int(current["is_active"]) == 1,
                    str(current["role"]),
                    is_active,
                )
                users.update_user(user_id, is_active=is_active, increment_permission_version=True)
                OperationLogRepository(uow).add(
                    action_type=action_type,
                    result="success",
                    actor_id=actor.user_id,
                    actor_name=actor.username,
                    target_type="user",
                    target_id=str(user_id),
                    summary=summary,
                    details={"username": str(current["username"]), "active": str(is_active)},
                )
                uow.commit()
            return ServiceResult.ok(None)
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=str(exc))

    def _require_user_management(
        self,
        session_or_id: Session | str,
        action: str,
        target_summary: str,
    ) -> Session | ServiceResult:
        try:
            return self._session_store.require_permission(self._database, session_or_id, action, target_summary)
        except PermissionError as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))

    def _ensure_admin_invariants(
        self,
        users: UserRepository,
        user_id: int,
        current_role: str,
        current_active: bool,
        next_role: str,
        next_active: bool,
    ) -> None:
        if next_role == Role.ADMIN.value and (current_role != Role.ADMIN.value or not current_active):
            # The service checks before writes so the user receives a stable
            # business error; the partial unique index remains a race backstop.
            if users.count_active_admins() > 0:
                raise ValueError("管理员账号只能有一个")
        if current_role == Role.ADMIN.value and current_active and (next_role != Role.ADMIN.value or not next_active):
            if users.count_admins_excluding(user_id) == 0:
                raise ValueError("不能禁用或删除唯一管理员")


def _row_to_view(row) -> UserView:
    if row is None:
        raise ValueError("user row is required")
    return UserView(
        id=int(row["id"]),
        username=str(row["username"]),
        role=str(row["role"]),
        is_active=int(row["is_active"]) == 1,
        permission_version=int(row["permission_version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _normalize_username(username: str | None) -> str:
    if not isinstance(username, str):
        return ""
    return " ".join(username.replace("\r", " ").replace("\n", " ").split())[:80]


def _validate_username(username: str) -> None:
    if not username:
        raise ValueError("用户名不能为空")
    if len(username) < 3:
        raise ValueError("用户名至少 3 个字符")
    if not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise ValueError("用户名包含不支持的字符")


def _valid_id(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0
