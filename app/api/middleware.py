from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.schemas import ApiErrorItem, error_envelope
from app.core.logging import get_logger, user_safe_error
from app.services.errors import ErrorCode

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

_HTTP_ERROR_MESSAGES = {
    400: "参数校验失败",
    404: "资源不存在",
    405: "方法不允许",
    409: "服务状态冲突",
    503: "服务暂不可用",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response


def configure_api_middleware(app: FastAPI, *, logger: logging.Logger | None = None) -> None:
    """Install API-only middleware and exception handlers.

    CORS is intentionally not added here: LAN exposure, tokens, and IP allowlists
    are [待确认], so the container keeps browser cross-origin access closed by default.
    """

    safe_logger = logger or get_logger("api.middleware")
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = tuple(_validation_error_item(error) for error in exc.errors())
        return _json_error(int(ErrorCode.VALIDATION_ERROR), "参数校验失败", errors)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status_code = int(exc.status_code)
        message = _HTTP_ERROR_MESSAGES.get(status_code, "请求处理失败")
        return _json_error(status_code, message)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        safe_logger.error("api request failed: %s", user_safe_error(exc))
        return _json_error(int(ErrorCode.INTERNAL_ERROR), "操作失败，请稍后重试。")


def _json_error(code: int, message: str, errors: tuple[ApiErrorItem, ...] = ()) -> JSONResponse:
    envelope = error_envelope(code, message, errors)
    response = JSONResponse(status_code=_status_code(code), content=envelope.to_dict())
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def _status_code(code: int) -> int:
    if 100 <= code <= 599:
        return code
    return int(ErrorCode.INTERNAL_ERROR)


def _validation_error_item(error: dict[str, Any]) -> ApiErrorItem:
    loc = error.get("loc")
    if isinstance(loc, tuple | list) and loc:
        field = str(loc[-1])
    else:
        field = ""
    return ApiErrorItem(field=field, message="参数无效")
