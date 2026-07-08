from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from app.config.defaults import API_PORT_MAX, API_PORT_MIN, ApiConfig, AppConfig
from app.config.loader import write_default_config
from app.core.logging import get_logger, user_safe_error
from app.db.connection import Database
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Permission

_LOOPBACK_ADDRESSES = {"127.0.0.1", "localhost", "::1"}


class LocalApiConfigFacade:
    """Permission-checked facade used by the desktop API settings page."""

    def __init__(
        self,
        *,
        database: Database,
        session_store: SessionStore,
        config: AppConfig,
        config_file: Path,
        on_config_changed: Callable[[AppConfig], None] | None = None,
        api_host: object | None = None,
        read_service: object | None = None,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._config = config
        self._config_file = config_file
        self._on_config_changed = on_config_changed
        self._api_host = api_host
        self._read_service = read_service
        self._logger = get_logger("services.local_api_config")

    def get_api_config(self, session_or_id: Session | str | None = None) -> ServiceResult[ApiConfig]:
        return ServiceResult.ok(self._config.api)

    def save_api_config(self, session_or_id: Session | str, command: object) -> ServiceResult[ApiConfig]:
        actor = self._require_permission(session_or_id)
        if isinstance(actor, ServiceResult):
            return actor
        try:
            api_config = _api_config_from_command(command)
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=str(exc))

        new_config = replace(self._config, api=api_config)
        try:
            write_default_config(self._config_file, new_config)
        except OSError as exc:
            self._logger.error("api config write failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="本地 API 设置保存失败")

        self._config = new_config
        if self._on_config_changed is not None:
            self._on_config_changed(new_config)
        _call_update_config(self._read_service, new_config)
        _call_update_config(self._api_host, new_config)
        self._add_operation_log(actor, api_config)
        return ServiceResult.ok(api_config)

    def _require_permission(self, session_or_id: Session | str) -> Session | ServiceResult[ApiConfig]:
        try:
            return self._session_store.require_permission(
                self._database,
                session_or_id,
                Permission.SYSTEM_SETTINGS.value,
                "保存本地 API 设置",
            )
        except PermissionError as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))
        except Exception as exc:
            self._logger.warning("api config permission check failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="权限校验失败")

    def _add_operation_log(self, actor: Session, api_config: ApiConfig) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                OperationLogRepository(uow).add(
                    action_type="api.config.update",
                    result="success",
                    actor_id=actor.user_id,
                    actor_name=actor.username,
                    target_type="local_api",
                    target_id=None,
                    summary="保存本地 API 设置。",
                    details={
                        "enabled": api_config.enabled,
                        "bind_address": api_config.bind_address,
                        "port": api_config.port,
                    },
                )
                uow.commit()
        except sqlite3.Error as exc:
            self._logger.warning("api config audit log failed: %s", user_safe_error(exc))


def _api_config_from_command(command: object) -> ApiConfig:
    enabled = bool(getattr(command, "enabled", False))
    bind_address = str(getattr(command, "bind_address", "127.0.0.1") or "127.0.0.1")
    if bind_address not in _LOOPBACK_ADDRESSES:
        raise ValueError("本地 API 仅允许绑定本机地址")
    port_value = getattr(command, "port", None)
    if isinstance(port_value, bool) or not isinstance(port_value, int) or not API_PORT_MIN <= port_value <= API_PORT_MAX:
        raise ValueError(f"端口必须为 {API_PORT_MIN}-{API_PORT_MAX}")
    return ApiConfig(enabled=enabled, bind_address=bind_address, port=port_value, cors_enabled=False)


def _call_update_config(target: object | None, config: AppConfig) -> None:
    method = getattr(target, "update_config", None)
    if callable(method):
        method(config)
