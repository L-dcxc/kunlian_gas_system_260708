from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from app.db.connection import Database
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permission_guard import PermissionGuard, SimplePermissionUser
from app.services.permissions import Permission, permissions_for_role

PASSWORD_ITERATIONS = 210_000
SALT_BYTES = 16
LOGIN_FAILED_MESSAGE = "用户名或密码错误"
SESSION_INVALID_MESSAGE = "登录状态已失效，请重新登录。"


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    user_id: int
    username: str
    role: str
    permissions: tuple[str, ...]
    permission_version: int
    login_at: str


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def create(self, *, user_id: int, username: str, role: str, permission_version: int) -> Session:
        session = Session(
            session_id=secrets.token_urlsafe(32),
            user_id=user_id,
            username=username,
            role=role,
            permissions=permissions_for_role(role),
            permission_version=permission_version,
            login_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def logout(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def refresh_permission_version(self, session_id: str, permission_version: int) -> None:
        with self._lock:
            current = self._sessions.get(session_id)
            if current is None:
                return
            self._sessions[session_id] = Session(
                session_id=current.session_id,
                user_id=current.user_id,
                username=current.username,
                role=current.role,
                permissions=current.permissions,
                permission_version=permission_version,
                login_at=current.login_at,
            )

    def validate(self, database: Database, session_or_id: Session | str) -> Session:
        session = self._resolve(session_or_id)
        with UnitOfWork(database) as uow:
            row = UserRepository(uow).find_by_id(session.user_id)
            uow.commit()
        if row is None or row["deleted_at"] is not None or int(row["is_active"]) != 1:
            self.logout(session.session_id)
            raise PermissionError(SESSION_INVALID_MESSAGE)
        if int(row["permission_version"]) != session.permission_version:
            # Role/active changes increment permission_version so already-issued
            # sessions cannot retain stale service permissions after UI state changes.
            self.logout(session.session_id)
            raise PermissionError(SESSION_INVALID_MESSAGE)
        return session

    def require_permission(
        self,
        database: Database,
        session_or_id: Session | str,
        action: str,
        target_summary: str,
    ) -> Session:
        session = self.validate(database, session_or_id)
        user = SimplePermissionUser(
            id=session.user_id,
            username=session.username,
            role=session.role,
            permissions=session.permissions,
            is_active=True,
        )
        PermissionGuard(database=database).require(user, action, target_summary)
        return session

    def _resolve(self, session_or_id: Session | str) -> Session:
        session_id = session_or_id.session_id if isinstance(session_or_id, Session) else session_or_id
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise PermissionError(SESSION_INVALID_MESSAGE)
        return session


class AuthService:
    def __init__(self, database: Database, session_store: SessionStore | None = None) -> None:
        self._database = database
        self.session_store = session_store or SessionStore()

    def login(self, username: str, password: str) -> ServiceResult[Session]:
        normalized_username = _normalize_username(username)
        if not _valid_password_input(password):
            _verify_password(password or "", _dummy_hash(), _dummy_salt())
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=LOGIN_FAILED_MESSAGE)

        with UnitOfWork(self._database) as uow:
            row = UserRepository(uow).find_active_by_username(normalized_username) if normalized_username else None
            uow.commit()
        if row is None or int(row["is_active"]) != 1:
            _verify_password(password, _dummy_hash(), _dummy_salt())
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=LOGIN_FAILED_MESSAGE)
        if not _verify_password(password, str(row["password_hash"]), str(row["password_salt"])):
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=LOGIN_FAILED_MESSAGE)

        session = self.session_store.create(
            user_id=int(row["id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            permission_version=int(row["permission_version"]),
        )
        return ServiceResult.ok(session)

    def logout(self, session_or_id: Session | str) -> None:
        session_id = session_or_id.session_id if isinstance(session_or_id, Session) else session_or_id
        self.session_store.logout(session_id)

    def change_password(
        self,
        session_or_id: Session | str,
        old_password: str,
        new_password: str,
    ) -> ServiceResult[None]:
        try:
            session = self.session_store.validate(self._database, session_or_id)
        except PermissionError as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))
        if not _valid_password_input(old_password) or not _valid_new_password(new_password):
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message="密码不符合要求")

        with UnitOfWork(self._database) as uow:
            users = UserRepository(uow)
            row = users.find_by_id(session.user_id)
            if row is None or int(row["is_active"]) != 1 or row["deleted_at"] is not None:
                return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=SESSION_INVALID_MESSAGE)
            if not _verify_password(old_password, str(row["password_hash"]), str(row["password_salt"])):
                return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="原密码错误")
            password_hash, password_salt = hash_password(new_password)
            users.update_user(
                session.user_id,
                password_hash=password_hash,
                password_salt=password_salt,
                increment_permission_version=True,
            )
            updated = users.find_by_id(session.user_id)
            OperationLogRepository(uow).add(
                action_type="password.change",
                result="success",
                actor_id=session.user_id,
                actor_name=session.username,
                target_type="user",
                target_id=str(session.user_id),
                summary="用户修改密码。",
                details={"username": session.username},
            )
            uow.commit()
        if updated is not None:
            self.session_store.refresh_permission_version(session.session_id, int(updated["permission_version"]))
        return ServiceResult.ok(None)

    def require_app_exit(self, session_or_id: Session | str) -> ServiceResult[None]:
        try:
            self.session_store.require_permission(self._database, session_or_id, "app.exit", "退出系统")
        except PermissionError as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))
        return ServiceResult.ok(None)


def hash_password(password: str) -> tuple[str, str]:
    if not _valid_new_password(password):
        raise ValueError("password does not meet policy")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return _b64encode(digest), _b64encode(salt)


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    if not _valid_password_input(password):
        return False
    return _verify_password(password, password_hash, password_salt)


def _verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    try:
        salt = _b64decode(password_salt)
        expected = _b64decode(password_hash)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return hmac.compare_digest(actual, expected)


def _normalize_username(username: str) -> str:
    if not isinstance(username, str):
        return ""
    return " ".join(username.replace("\r", " ").replace("\n", " ").split())[:80]


def _valid_password_input(password: str) -> bool:
    return isinstance(password, str) and 1 <= len(password) <= 256


def _valid_new_password(password: str) -> bool:
    return isinstance(password, str) and 8 <= len(password) <= 128


def _dummy_salt() -> str:
    return _b64encode(b"gas-alarm-dummy-salt")


def _dummy_hash() -> str:
    digest = hashlib.pbkdf2_hmac("sha256", b"dummy-password", b"gas-alarm-dummy-salt", PASSWORD_ITERATIONS)
    return _b64encode(digest)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise ValueError("invalid base64") from exc
