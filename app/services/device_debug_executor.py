from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger, user_safe_error
from app.device.channels.base import Channel, ChannelConfig, ChannelError, ChannelErrorCode, ChannelType, Parity, SerialParameters, TcpParameters
from app.device.channels.serial_channel import SerialChannel
from app.device.channels.tcp_channel import TcpChannel
from app.device.debug.debug_service import DebugFrameResult, DebugReadCommand, DeviceDebugService
from app.services.errors import ErrorCode
from app.services.models import ServiceResult

ChannelFactory = Callable[[ChannelConfig], Channel]


class DeviceDebugExecutor:
    """Open a configured channel for one read-only device debug exchange."""

    def __init__(
        self,
        *,
        debug_service: DeviceDebugService,
        device_config_service: object,
        channel_factory: ChannelFactory | None = None,
    ) -> None:
        self._debug_service = debug_service
        self._device_config_service = device_config_service
        self._channel_factory = channel_factory or _default_channel_factory
        self._logger = get_logger("services.device_debug_executor")

    def send_debug_read(self, session_or_id: object, command: DebugReadCommand) -> ServiceResult[DebugFrameResult]:
        built = self._debug_service.build_read_request(command, session_or_id)
        if not built.success:
            return built
        port = self._find_enabled_port(command.port_id)
        if port is None:
            return _diagnostic(built, "调试端口未配置或未启用", "port_not_configured")
        try:
            channel_config = _channel_config_from_port(port, timeout_ms=command.timeout_ms)
            channel = self._channel_factory(channel_config)
            channel.open()
        except ChannelError as exc:
            return _diagnostic(built, exc.message, exc.code.value)
        except ValueError as exc:
            return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=str(exc))
        except Exception as exc:
            self._logger.warning("debug channel open failed: %s", user_safe_error(exc))
            return _diagnostic(built, "调试通道打开失败", ChannelErrorCode.OPEN_FAILED.value)

        try:
            return self._debug_service.send_read_request(command, channel, session_or_id)
        except Exception as exc:
            self._logger.warning("debug send failed: %s", user_safe_error(exc))
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="设备调试处理失败")
        finally:
            try:
                channel.close()
            except Exception as exc:
                self._logger.warning("debug channel close failed: %s", user_safe_error(exc))

    def _find_enabled_port(self, port_id: int) -> dict[str, object] | None:
        method = getattr(self._device_config_service, "list_ports", None)
        if not callable(method):
            return None
        try:
            rows = method()
        except Exception as exc:
            self._logger.warning("debug list ports failed: %s", user_safe_error(exc))
            return None
        for row in rows:
            if _int(row.get("id")) == port_id and _is_enabled(row.get("is_enabled", 1)):
                return dict(row)
        return None


def _channel_config_from_port(row: dict[str, object], *, timeout_ms: int) -> ChannelConfig:
    port_id = _required_int(row.get("id"), "port_id")
    channel_type = ChannelType(str(row.get("channel_type") or ""))
    if channel_type is ChannelType.SERIAL:
        return ChannelConfig(
            port_id=port_id,
            channel_type=channel_type,
            serial=SerialParameters(
                port_name=str(row.get("serial_port_name") or ""),
                baud_rate=_required_int(row.get("baud_rate") or 9600, "baud_rate"),
                data_bits=_required_int(row.get("data_bits") or 8, "data_bits"),
                stop_bits=_stop_bits(row.get("stop_bits") or 1),
                parity=_parity(row.get("parity")),
            ),
            timeout_ms=timeout_ms,
            retry_count=0,
            labels=(str(row.get("name") or port_id),),
        )
    return ChannelConfig(
        port_id=port_id,
        channel_type=channel_type,
        tcp=TcpParameters(
            host=str(row.get("tcp_host") or ""),
            port=_required_int(row.get("tcp_port"), "tcp_port"),
            connect_timeout_ms=timeout_ms,
        ),
        timeout_ms=timeout_ms,
        retry_count=0,
        labels=(str(row.get("name") or port_id),),
    )


def _default_channel_factory(config: ChannelConfig) -> Channel:
    if config.channel_type is ChannelType.SERIAL:
        return SerialChannel(config)
    return TcpChannel(config)


def _diagnostic(
    built: ServiceResult[DebugFrameResult],
    message: str,
    error_code: str,
) -> ServiceResult[DebugFrameResult]:
    request_hex = getattr(getattr(built, "data", None), "request_hex", "")
    return ServiceResult.ok(
        DebugFrameResult(
            request_hex=request_hex,
            response_hex="",
            crc_ok=None,
            validation_message=message,
            error_code=error_code,
        ),
        message="调试请求未发送",
    )


def _required_int(value: object, field_name: str) -> int:
    parsed = _int(value)
    if parsed is None:
        raise ValueError(f"{field_name} 配置无效")
    return parsed


def _int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    parsed = _int(value)
    return parsed is None or parsed == 1


def _stop_bits(value: object) -> int:
    if float(value) == 2:
        return 2
    return 1


def _parity(value: object) -> Parity:
    if value == "E":
        return Parity.EVEN
    if value == "O":
        return Parity.ODD
    return Parity.NONE
