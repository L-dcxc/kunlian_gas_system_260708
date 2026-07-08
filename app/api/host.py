from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import socket
import threading
from typing import Protocol

from fastapi import FastAPI
import uvicorn

from app.api.middleware import configure_api_middleware
from app.api.routers.read_model import create_read_model_router
from app.config.defaults import API_PORT_MAX, API_PORT_MIN, ApiConfig, AppConfig, default_config
from app.core.logging import get_logger, user_safe_error
from app.services.api_read_service import ApiReadService

DEFAULT_API_BIND_ADDRESS = "127.0.0.1"
API_PORT_IN_USE_MESSAGE = "本地 API 启动失败：端口被占用。桌面监控不受影响。"
API_START_FAILED_MESSAGE = "本地 API 启动失败。桌面监控不受影响。"
_LOOPBACK_ADDRESSES = {"127.0.0.1", "localhost", "::1"}

AlertCallback = Callable[[str], None]


class _ServerProtocol(Protocol):
    should_exit: bool

    def run(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ApiHostStartResult:
    started: bool
    message: str = ""


class LocalApiHost:
    def __init__(
        self,
        service: ApiReadService,
        config: AppConfig | None = None,
        *,
        alert_callback: AlertCallback | None = None,
        server_factory: Callable[[FastAPI, str, int], _ServerProtocol] | None = None,
    ) -> None:
        self._service = service
        self._config = config or default_config()
        self._api_config = _safe_api_config(self._config.api)
        self._alert_callback = alert_callback
        self._server_factory = server_factory or _uvicorn_server_factory
        self._logger = get_logger("api.host")
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._server: _ServerProtocol | None = None
        self._last_alert: str | None = None
        self.app = create_api_app(service)

    @property
    def bind_address(self) -> str:
        return self._api_config.bind_address

    @property
    def port(self) -> int:
        return self._api_config.port

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def last_alert(self) -> str | None:
        return self._last_alert

    def update_config(self, config: AppConfig) -> None:
        new_api_config = _safe_api_config(config.api)
        should_stop = self.is_running and new_api_config != self._api_config
        if should_stop:
            self.stop()
        with self._lock:
            self._config = config
            self._api_config = new_api_config

    def start(self) -> ApiHostStartResult:
        with self._lock:
            if not self._api_config.enabled:
                return ApiHostStartResult(started=False, message="本地 API 未启用。")
            if self.is_running:
                return ApiHostStartResult(started=True)
            if not _port_available(self.bind_address, self.port):
                self._alert(API_PORT_IN_USE_MESSAGE)
                return ApiHostStartResult(started=False, message=API_PORT_IN_USE_MESSAGE)

            try:
                server = self._server_factory(self.app, self.bind_address, self.port)
            except Exception as exc:
                self._logger.error("api server create failed: %s", user_safe_error(exc))
                self._alert(API_START_FAILED_MESSAGE)
                return ApiHostStartResult(started=False, message=API_START_FAILED_MESSAGE)

            self._server = server
            ready = threading.Event()
            thread = threading.Thread(target=self._run_server, args=(server, ready), name="local-api-host", daemon=True)
            self._thread = thread
            thread.start()
            ready.wait(timeout=2)
            return ApiHostStartResult(started=True)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            if server is not None:
                server.should_exit = True
        # Join outside the lifecycle lock so the server thread can run its own
        # cleanup path without deadlocking desktop shutdown.
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            if thread is None or not thread.is_alive():
                self._thread = None
                self._server = None

    def _run_server(self, server: _ServerProtocol, ready: threading.Event) -> None:
        ready.set()
        try:
            server.run()
        except OSError as exc:
            self._logger.error("api server socket failed: %s", user_safe_error(exc))
            self._alert(API_PORT_IN_USE_MESSAGE if _is_address_in_use(exc) else API_START_FAILED_MESSAGE)
        except Exception as exc:
            self._logger.error("api server failed: %s", user_safe_error(exc))
            self._alert(API_START_FAILED_MESSAGE)
        finally:
            with self._lock:
                if self._server is server:
                    self._server = None
                    self._thread = None

    def _alert(self, message: str) -> None:
        self._last_alert = message
        if self._alert_callback is not None:
            try:
                self._alert_callback(message)
            except Exception as exc:
                self._logger.error("api alert callback failed: %s", user_safe_error(exc))


def create_api_app(service: ApiReadService) -> FastAPI:
    app = FastAPI(title="Gas Safety Alarm Local API", docs_url=None, redoc_url=None)
    configure_api_middleware(app)
    app.include_router(create_read_model_router(service))
    return app


def _safe_api_config(config: ApiConfig) -> ApiConfig:
    bind_address = config.bind_address if config.bind_address in _LOOPBACK_ADDRESSES else DEFAULT_API_BIND_ADDRESS
    if isinstance(config.port, int) and not isinstance(config.port, bool) and API_PORT_MIN <= config.port <= API_PORT_MAX:
        port = config.port
    else:
        port = ApiConfig().port
    # LAN binding, API token, and IP allowlist are [待确认];
    # this host only honors loopback defaults.
    return ApiConfig(enabled=bool(config.enabled), bind_address=bind_address, port=port, cors_enabled=False)


def _uvicorn_server_factory(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    uvicorn_config = uvicorn.Config(app=app, host=host, port=port, log_level="warning", access_log=False)
    return uvicorn.Server(uvicorn_config)


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            return not _is_address_in_use(exc) and False
    return True


def _is_address_in_use(exc: OSError) -> bool:
    return getattr(exc, "errno", None) in {48, 98, 10048}
