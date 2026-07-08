from __future__ import annotations

import socket
import time
from collections.abc import Callable
from threading import Lock
from typing import Protocol

from app.core.logging import get_logger
from app.device.channels.base import (
    Channel,
    ChannelConfig,
    ChannelError,
    ChannelErrorCode,
    ChannelType,
    TcpFrameMode,
    TransactResult,
    validate_outbound_payload,
)
from app.device.protocols.base import bytes_to_hex


class SocketLike(Protocol):
    def settimeout(self, value: float) -> None: ...
    def sendall(self, data: bytes) -> None: ...
    def recv(self, size: int) -> bytes: ...
    def close(self) -> None: ...


class TcpChannel(Channel):
    def __init__(
        self,
        config: ChannelConfig,
        *,
        socket_factory: Callable[[tuple[str, int], float], SocketLike] | None = None,
    ) -> None:
        if config.channel_type is not ChannelType.TCP or config.tcp is None:
            raise ValueError("tcp channel requires tcp configuration")
        if config.tcp.frame_mode is not TcpFrameMode.RTU_OVER_TCP:
            raise ValueError("only RTU-over-TCP is supported")
        self.config = config
        self._socket_factory = socket_factory or socket.create_connection
        self._socket: SocketLike | None = None
        self._lock = Lock()
        self._logger = get_logger("device.tcp_channel")

    def open(self) -> None:
        params = self.config.tcp
        if params is None:
            raise ChannelError(ChannelErrorCode.OPEN_FAILED, "tcp parameters are missing")
        try:
            sock = self._socket_factory((params.host, params.port), params.connect_timeout_ms / 1000)
            sock.settimeout(self.config.timeout_ms / 1000)
        except Exception as exc:
            raise ChannelError(ChannelErrorCode.CONNECTION_FAILED, "TCP 连接失败") from exc
        with self._lock:
            self._socket = sock

    def close(self) -> None:
        with self._lock:
            sock = self._socket
            self._socket = None
        if sock is None:
            return
        try:
            sock.close()
        except Exception as exc:
            raise ChannelError(ChannelErrorCode.CLOSE_FAILED, "TCP 关闭失败") from exc

    def transact(self, payload: bytes, timeout_ms: int | None = None) -> TransactResult:
        try:
            safe_payload = validate_outbound_payload(payload, self.config.max_payload_bytes)
        except ValueError as exc:
            return TransactResult.failure(ChannelErrorCode.INVALID_PAYLOAD, str(exc))
        started = time.monotonic()
        with self._lock:
            if self._socket is None:
                return TransactResult.failure(ChannelErrorCode.NOT_OPEN, "通道未打开")
            try:
                self._socket.settimeout((timeout_ms or self.config.timeout_ms) / 1000)
                # 485-to-LAN gateways in scope expect the original Modbus RTU
                # frame bytes with no MBAP header; standard Modbus TCP is out of
                # scope until the customer confirms it.
                self._socket.sendall(safe_payload)
                self._log_frame("tcp tx", safe_payload)
                response = self._socket.recv(self.config.max_payload_bytes)
            except socket.timeout:
                return _failure(ChannelErrorCode.TIMEOUT, "通讯超时", started)
            except OSError:
                return _failure(ChannelErrorCode.CONNECTION_FAILED, "TCP 连接中断", started)
            except Exception as exc:
                self._logger.warning("tcp transact failed port_id=%s error=%s", self.config.port_id, exc.__class__.__name__)
                return _failure(ChannelErrorCode.IO_ERROR, "TCP 通讯失败", started)
        if not response:
            return _failure(ChannelErrorCode.TIMEOUT, "通讯超时", started)
        self._log_frame("tcp rx", response)
        return TransactResult.success(response, elapsed_ms=_elapsed_ms(started))

    def _log_frame(self, label: str, payload: bytes) -> None:
        self._logger.debug("%s port_id=%s hex=%s", label, self.config.port_id, bytes_to_hex(payload, max_chars=128))


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _failure(code: ChannelErrorCode, message: str, started: float) -> TransactResult:
    return TransactResult.failure(code, message, elapsed_ms=_elapsed_ms(started))
