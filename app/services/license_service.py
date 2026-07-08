from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import Database
from app.db.repositories.license_repository import LicenseRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Permission

LICENSE_CODE_PREFIX = "gas-license-v1"
MAX_AUTHORIZATION_CODE_LENGTH = 4096


@dataclass(frozen=True, slots=True)
class LicenseStatus:
    status: str
    machine_fingerprint_hash: str
    activated_at: str | None = None
    expires_at: str | None = None
    message: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def can_enter_main_system(self) -> bool:
        # [待确认] demo mode policy pending; default backend blocks main system.
        return self.is_active


class LicenseService:
    def __init__(
        self,
        database: Database,
        *,
        activation_signing_key: bytes | None = None,
        machine_fingerprint_provider: Callable[[], str] | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._database = database
        self._activation_signing_key = activation_signing_key or _env_signing_key()
        self._machine_fingerprint_provider = machine_fingerprint_provider or _default_machine_fingerprint
        self._session_store = session_store

    def get_license_status(self) -> LicenseStatus:
        machine_hash = self.machine_fingerprint_hash()
        with UnitOfWork(self._database) as uow:
            row = LicenseRepository(uow).get_current()
            uow.commit()
        if row is None:
            return LicenseStatus("unlicensed", machine_hash, message="软件未授权")
        if str(row["machine_fingerprint_hash"]) != machine_hash:
            return LicenseStatus("invalid", machine_hash, message="授权信息与当前机器不匹配")
        if not self._verify_integrity(row):
            return LicenseStatus("invalid", machine_hash, message="授权信息校验失败")
        payload = _loads_payload(str(row["license_payload"]))
        if payload is None:
            return LicenseStatus("invalid", machine_hash, message="授权信息校验失败")
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, str) and _is_expired(expires_at):
            return LicenseStatus("expired", machine_hash, str(row["activated_at"]), expires_at, "授权已过期")
        safe_expires_at = expires_at if isinstance(expires_at, str) else None
        return LicenseStatus("active", machine_hash, str(row["activated_at"]), safe_expires_at)

    def activate(self, authorization_code: str, actor: Session | None = None) -> ServiceResult[LicenseStatus]:
        if actor is not None and self._session_store is not None:
            try:
                self._session_store.require_permission(
                    self._database,
                    actor,
                    Permission.LICENSE_ACTIVATE.value,
                    "软件授权激活",
                )
            except Exception as exc:
                return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))
        if self._activation_signing_key is None:
            return ServiceResult.fail(code=int(ErrorCode.SERVICE_UNAVAILABLE), message="授权校验配置未就绪")
        try:
            payload_json, authorization_signature = self._parse_and_verify_code(authorization_code)
        except ValueError:
            self._log_activation(actor, "denied", "授权码校验失败")
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="授权码无效")

        payload = _loads_payload(payload_json)
        machine_hash = self.machine_fingerprint_hash()
        if payload is None or payload.get("machine_fingerprint_hash") != machine_hash:
            self._log_activation(actor, "denied", "授权码校验失败")
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="授权码无效")
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, str) and _is_expired(expires_at):
            self._log_activation(actor, "denied", "授权码校验失败")
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="授权码无效")

        activated_at = datetime.now(timezone.utc).isoformat()
        status = "active"
        integrity_signature = self._integrity_signature(
            machine_hash=machine_hash,
            payload_json=payload_json,
            authorization_signature=authorization_signature,
            status=status,
            activated_at=activated_at,
            expires_at=expires_at if isinstance(expires_at, str) else None,
        )
        with UnitOfWork(self._database) as uow:
            LicenseRepository(uow).save_current(
                machine_fingerprint_hash=machine_hash,
                license_payload=payload_json,
                authorization_signature=authorization_signature,
                integrity_signature=integrity_signature,
                status=status,
                activated_at=activated_at,
                expires_at=expires_at if isinstance(expires_at, str) else None,
                updated_at=activated_at,
            )
            OperationLogRepository(uow).add(
                action_type="license.activate",
                result="success",
                actor_id=actor.user_id if actor else None,
                actor_name=actor.username if actor else None,
                target_type="license",
                target_id=None,
                summary="软件授权激活成功。",
                details={"machine_fingerprint_hash": _mask_hash(machine_hash)},
            )
            uow.commit()
        return ServiceResult.ok(self.get_license_status())

    def machine_fingerprint_hash(self) -> str:
        raw = self._machine_fingerprint_provider()
        # Only the digest crosses service/storage/logging boundaries. The raw
        # machine material is used in memory for one-machine-one-code binding and
        # must never be written to operation logs or user-facing errors.
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def build_authorization_code(self, payload: dict[str, Any]) -> str:
        if self._activation_signing_key is None:
            raise ValueError("activation signing key is not configured")
        payload_json = _canonical_payload(payload)
        payload_b64 = _b64encode(payload_json.encode("utf-8"))
        signature = self._authorization_signature(payload_b64)
        return f"{LICENSE_CODE_PREFIX}.{payload_b64}.{signature}"

    def _parse_and_verify_code(self, authorization_code: str) -> tuple[str, str]:
        if not isinstance(authorization_code, str) or not authorization_code.strip():
            raise ValueError("authorization code is required")
        code = authorization_code.strip()
        if len(code) > MAX_AUTHORIZATION_CODE_LENGTH:
            raise ValueError("authorization code is too long")
        parts = code.split(".")
        if len(parts) != 3 or parts[0] != LICENSE_CODE_PREFIX:
            raise ValueError("unsupported authorization code")
        payload_b64, signature = parts[1], parts[2]
        expected = self._authorization_signature(payload_b64)
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        payload_json = _b64decode_text(payload_b64)
        _loads_payload(payload_json, strict=True)
        return payload_json, signature

    def _authorization_signature(self, payload_b64: str) -> str:
        if self._activation_signing_key is None:
            raise ValueError("activation signing key is not configured")
        message = f"{LICENSE_CODE_PREFIX}.{payload_b64}".encode("utf-8")
        return _b64encode(hmac.new(self._activation_signing_key, message, hashlib.sha256).digest())

    def _integrity_signature(
        self,
        *,
        machine_hash: str,
        payload_json: str,
        authorization_signature: str,
        status: str,
        activated_at: str,
        expires_at: str | None,
    ) -> str:
        # The persisted status is display data; this HMAC binds it to the payload
        # and machine digest so flipping a plaintext status field cannot authorize
        # the application.
        key = self._integrity_key(machine_hash)
        message = "|".join(
            [machine_hash, payload_json, authorization_signature, status, activated_at, expires_at or ""]
        )
        return _b64encode(hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest())

    def _verify_integrity(self, row) -> bool:
        expected = self._integrity_signature(
            machine_hash=str(row["machine_fingerprint_hash"]),
            payload_json=str(row["license_payload"]),
            authorization_signature=str(row["authorization_signature"]),
            status=str(row["status"]),
            activated_at=str(row["activated_at"]),
            expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
        )
        return hmac.compare_digest(expected, str(row["integrity_signature"]))

    def _integrity_key(self, machine_hash: str) -> bytes:
        material = (self._activation_signing_key or b"local-license-integrity") + machine_hash.encode("ascii")
        return hashlib.sha256(material).digest()

    def _log_activation(self, actor: Session | None, result: str, summary: str) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                OperationLogRepository(uow).add(
                    action_type="license.activate",
                    result=result,
                    actor_id=actor.user_id if actor else None,
                    actor_name=actor.username if actor else None,
                    target_type="license",
                    summary=summary,
                )
                uow.commit()
        except Exception as exc:
            get_logger("services.license_service").warning(
                "license activation operation log failed: %s",
                exc.__class__.__name__,
            )


def _env_signing_key() -> bytes | None:
    value = os.environ.get("GAS_ALARM_LICENSE_SIGNING_KEY")
    if not value:
        return None
    return value.encode("utf-8")


def _default_machine_fingerprint() -> str:
    return "|".join([platform.node(), platform.system(), platform.machine(), str(uuid.getnode())])


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_payload(payload_json: str, strict: bool = False) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError("invalid payload") from exc
        return None
    if not isinstance(payload, dict):
        if strict:
            raise ValueError("invalid payload")
        return None
    return payload


def _is_expired(expires_at: str) -> bool:
    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires < datetime.now(timezone.utc)


def _mask_hash(value: str) -> str:
    return f"{value[:8]}...{value[-4:]}" if len(value) > 16 else "<hidden>"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode_text(value: str) -> str:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ValueError("invalid base64") from exc
