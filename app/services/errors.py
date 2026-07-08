from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, TypeVar

from app.core.logging import get_logger, user_safe_error
from app.services.models import ServiceError as ResultError, ServiceResult

T = TypeVar("T")
MAX_TEXT = 512


class ErrorCode(IntEnum):
    VALIDATION_ERROR = 400
    PERMISSION_DENIED = 403
    NOT_FOUND = 404
    CONFLICT = 409
    SERVICE_UNAVAILABLE = 503
    INTERNAL_ERROR = 500


class ServiceErrorBase(RuntimeError):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    public_message = "操作失败，请稍后重试。"

    def __init__(self, message: str | None = None, *, details: list[ResultError] | None = None) -> None:
        self.details = tuple(details or ())
        super().__init__(_safe_public_text(message or self.public_message))

    @property
    def message(self) -> str:
        return str(self)


class ValidationError(ServiceErrorBase):
    code = ErrorCode.VALIDATION_ERROR
    public_message = "参数校验失败"


class PermissionDenied(ServiceErrorBase):
    code = ErrorCode.PERMISSION_DENIED
    public_message = "当前用户无权执行该操作"


class ServiceError(ServiceErrorBase):
    code = ErrorCode.INTERNAL_ERROR
    public_message = "服务处理失败，请稍后重试。"


@dataclass(frozen=True, slots=True)
class TextValue:
    """Text intended for UI/API display as plain text, never markup or SQL."""

    value: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _safe_public_text(self.value))


def to_service_result(
    exc: BaseException,
    *,
    logger: logging.Logger | None = None,
) -> ServiceResult[None]:
    if isinstance(exc, ServiceErrorBase):
        return ServiceResult.fail(code=int(exc.code), message=exc.message, errors=exc.details)

    safe_logger = logger or get_logger("services.errors")
    # Internal exception details are useful only in technical logs after the
    # project redactor has had a chance to sanitize them; UI/API receives a
    # stable message with no traceback, path or secret material.
    safe_logger.exception("controlled service boundary caught unexpected error: %s", user_safe_error(exc))
    return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="系统内部错误，请联系管理员。")


def ok_result(data: T | None = None, message: str = "ok") -> ServiceResult[T]:
    return ServiceResult.ok(data=data, message=_safe_public_text(message))


def validation_error(field: str, message: str) -> ResultError:
    return ResultError(code="validation_error", message=_safe_public_text(message), field=field)


def _safe_public_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("message must be a string")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return normalized[:MAX_TEXT]
