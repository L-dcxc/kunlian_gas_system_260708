from __future__ import annotations

import time
from threading import Lock
from typing import Any

from app.core.logging import get_logger
from app.device.channels.base import (
    Channel,
    ChannelConfig,
    ChannelError,
    ChannelErrorCode,
    ChannelType,
    Parity,
    TransactResult,
    validate_outbound_payload,
)
from app.device.protocols.base import bytes_to_hex

PARITY_MAP = {
    Parity.NONE: "N",
    Parity.EVEN: "E",
    Parity.ODD: "O",
}


class SerialChannel(Channel):
    def __init__(self, config: ChannelConfig, *, serial_factory: Any | None = None) -> None:
        if config.channel_type is not ChannelType.SERIAL or config.serial is None:
            raise ValueError("serial channel requires serial configuration")
        self.config = config
        self._serial_factory = serial_factory
        self._serial: Any | None = None
        self._lock = Lock()
        self._logger = get_logger("device.serial_channel")

    def open(self) -> None:
        params = self.config.serial
        if params is None:
            raise ChannelError(ChannelErrorCode.OPEN_FAILED, "serial parameters are missing")
        factory = self._serial_factory or _load_serial_factory()
        try:
            self._serial = factory(
                port=params.port_name,
                baudrate=params.baud_rate,
                bytesize=params.data_bits,
                stopbits=params.stop_bits,
                parity=PARITY_MAP[params.parity],
                timeout=self.config.timeout_ms / 1000,
                write_timeout=self.config.timeout_ms / 1000,
            )
        except ChannelError:
            raise
        except Exception as exc:
            raise ChannelError(ChannelErrorCode.OPEN_FAILED, "串口打开失败") from exc

    def close(self) -> None:
        with self._lock:
            serial_obj = self._serial
            self._serial = None
        if serial_obj is None:
            return
        try:
            serial_obj.close()
        except Exception as exc:
            raise ChannelError(ChannelErrorCode.CLOSE_FAILED, "串口关闭失败") from exc

    def transact(self, payload: bytes, timeout_ms: int | None = None) -> TransactResult:
        try:
            safe_payload = validate_outbound_payload(payload, self.config.max_payload_bytes)
        except ValueError as exc:
            return TransactResult.failure(ChannelErrorCode.INVALID_PAYLOAD, str(exc))
        timeout = (timeout_ms or self.config.timeout_ms) / 1000
        started = time.monotonic()
        with self._lock:
            if self._serial is None:
                return TransactResult.failure(ChannelErrorCode.NOT_OPEN, "通道未打开")
            try:
                if hasattr(self._serial, "timeout"):
                    self._serial.timeout = timeout
                if hasattr(self._serial, "write_timeout"):
                    self._serial.write_timeout = timeout
                self._serial.write(safe_payload)
                self._log_frame("serial tx", safe_payload)
                if hasattr(self._serial, "flush"):
                    self._serial.flush()
                response = self._serial.read(self.config.max_payload_bytes)
            except TimeoutError:
                return _failure(ChannelErrorCode.TIMEOUT, "通讯超时", started)
            except Exception as exc:
                self._logger.warning("serial transact failed port_id=%s error=%s", self.config.port_id, exc.__class__.__name__)
                return _failure(ChannelErrorCode.IO_ERROR, "串口通讯失败", started)
        if not response:
            return _failure(ChannelErrorCode.TIMEOUT, "通讯超时", started)
        self._log_frame("serial rx", response)
        return TransactResult.success(response, elapsed_ms=_elapsed_ms(started))

    def _log_frame(self, label: str, payload: bytes) -> None:
        # Raw frame diagnostics are intentionally bounded; continuous unbounded
        # frame dumps would turn untrusted device bytes into persistent log data.
        self._logger.debug("%s port_id=%s hex=%s", label, self.config.port_id, bytes_to_hex(payload, max_chars=128))


def _load_serial_factory() -> Any:
    try:
        import serial  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        # pyserial is optional in tests and packaging probes; fail with a stable
        # channel error instead of importing it at module load time.
        raise ChannelError(ChannelErrorCode.OPEN_FAILED, "串口驱动不可用") from exc
    return serial.Serial


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _failure(code: ChannelErrorCode, message: str, started: float) -> TransactResult:
    return TransactResult.failure(code, message, elapsed_ms=_elapsed_ms(started))
