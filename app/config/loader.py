from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from app.config.defaults import (
    API_PORT_MAX,
    API_PORT_MIN,
    LOG_LEVELS,
    PROTOCOL_MODES,
    AcquisitionConfig,
    ApiConfig,
    AppConfig,
    BackupConfig,
    DatabaseConfig,
    LoggingConfig,
    RuntimeConfig,
    default_config,
)

T = TypeVar("T")

TOP_LEVEL_KEYS = {"api", "runtime", "acquisition", "backup", "logging", "database"}
LOOPBACK_ADDRESSES = {"127.0.0.1", "localhost", "::1"}
SAFE_DB_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.sqlite3$")


@dataclass(frozen=True)
class ConfigLoadResult:
    config: AppConfig
    warnings: tuple[str, ...] = ()
    created_default: bool = False


class ConfigError(ValueError):
    """Configuration errors safe to show to users."""


class _Collector:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def load_config(config_file: Path) -> ConfigLoadResult:
    if not config_file.exists():
        config = default_config()
        write_default_config(config_file, config)
        return ConfigLoadResult(config=config, created_default=True)

    collector = _Collector()
    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ConfigLoadResult(
            config=default_config(),
            warnings=("配置文件损坏，已使用安全默认配置。",),
        )

    if not isinstance(raw, dict):
        return ConfigLoadResult(
            config=default_config(),
            warnings=("配置文件格式无效，已使用安全默认配置。",),
        )

    config = validate_config(raw, collector)
    return ConfigLoadResult(config=config, warnings=tuple(collector.warnings))


def write_default_config(config_file: Path, config: AppConfig | None = None) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    target = config or default_config()
    config_file.write_text(json.dumps(target.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def validate_config(raw: Mapping[str, Any], collector: _Collector | None = None) -> AppConfig:
    collector = collector or _Collector()
    for key in raw:
        if key not in TOP_LEVEL_KEYS:
            collector.warn(f"未知配置节已忽略: {key}")

    base = default_config()
    api = _parse_api(raw.get("api"), base.api, collector)
    runtime = _parse_runtime(raw.get("runtime"), base.runtime, collector)
    acquisition = _parse_acquisition(raw.get("acquisition"), base.acquisition, collector)
    backup = _parse_backup(raw.get("backup"), base.backup, collector)
    logging = _parse_logging(raw.get("logging"), base.logging, collector)
    database = _parse_database(raw.get("database"), base.database, collector)
    return AppConfig(api=api, runtime=runtime, acquisition=acquisition, backup=backup, logging=logging, database=database)


def _mapping(value: Any, section: str, collector: _Collector, allowed_keys: set[str]) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        collector.warn(f"配置节 {section} 格式无效，已使用默认值。")
        return {}
    for key in value:
        if key not in allowed_keys:
            collector.warn(f"未知配置项已忽略: {section}.{key}")
    return value


def _bool(section: Mapping[str, Any], key: str, default: bool, collector: _Collector) -> bool:
    value = section.get(key, default)
    if isinstance(value, bool):
        return value
    collector.warn(f"配置 {key} 类型无效，已使用默认值。")
    return default


def _int_range(
    section: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
    collector: _Collector,
) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        collector.warn(f"配置 {key} 超出允许范围，已使用默认值。")
        return default
    return value


def _choice(section: Mapping[str, Any], key: str, default: str, choices: set[str], collector: _Collector) -> str:
    value = section.get(key, default)
    if isinstance(value, str) and value in choices:
        return value
    collector.warn(f"配置 {key} 无效，已使用默认值。")
    return default


def _parse_api(value: Any, default: ApiConfig, collector: _Collector) -> ApiConfig:
    section = _mapping(value, "api", collector, {"enabled", "bind_address", "port", "cors_enabled"})
    bind_address = section.get("bind_address", default.bind_address)
    if bind_address not in LOOPBACK_ADDRESSES:
        collector.warn("API 绑定地址已回退到本机地址。")
        bind_address = default.bind_address
    return ApiConfig(
        enabled=_bool(section, "enabled", default.enabled, collector),
        bind_address=str(bind_address),
        port=_int_range(section, "port", default.port, API_PORT_MIN, API_PORT_MAX, collector),
        cors_enabled=False if section.get("cors_enabled", default.cors_enabled) is not False else False,
    )


def _parse_runtime(value: Any, default: RuntimeConfig, collector: _Collector) -> RuntimeConfig:
    section = _mapping(value, "runtime", collector, {"debug", "protocol_mode"})
    if section.get("debug") is True:
        collector.warn("DEBUG 不能通过普通配置启用，已保持关闭。")
    return RuntimeConfig(
        debug=False,
        protocol_mode=_choice(section, "protocol_mode", default.protocol_mode, PROTOCOL_MODES, collector),
    )


def _parse_acquisition(value: Any, default: AcquisitionConfig, collector: _Collector) -> AcquisitionConfig:
    section = _mapping(value, "acquisition", collector, {"poll_interval_ms", "request_timeout_ms", "retry_limit", "offline_after_failures"})
    return AcquisitionConfig(
        poll_interval_ms=_int_range(section, "poll_interval_ms", default.poll_interval_ms, 100, 60000, collector),
        request_timeout_ms=_int_range(section, "request_timeout_ms", default.request_timeout_ms, 100, 60000, collector),
        retry_limit=_int_range(section, "retry_limit", default.retry_limit, 0, 20, collector),
        offline_after_failures=_int_range(section, "offline_after_failures", default.offline_after_failures, 1, 100, collector),
    )


def _parse_backup(value: Any, default: BackupConfig, collector: _Collector) -> BackupConfig:
    section = _mapping(value, "backup", collector, {"scheduled_enabled", "interval_hours", "keep_last"})
    return BackupConfig(
        scheduled_enabled=_bool(section, "scheduled_enabled", default.scheduled_enabled, collector),
        interval_hours=_int_range(section, "interval_hours", default.interval_hours, 1, 24 * 30, collector),
        keep_last=_int_range(section, "keep_last", default.keep_last, 1, 365, collector),
    )


def _parse_logging(value: Any, default: LoggingConfig, collector: _Collector) -> LoggingConfig:
    section = _mapping(value, "logging", collector, {"level", "max_bytes", "backup_count"})
    return LoggingConfig(
        level=_choice(section, "level", default.level, LOG_LEVELS, collector),
        max_bytes=_int_range(section, "max_bytes", default.max_bytes, 1024 * 1024, 100 * 1024 * 1024, collector),
        backup_count=_int_range(section, "backup_count", default.backup_count, 1, 20, collector),
    )


def _parse_database(value: Any, default: DatabaseConfig, collector: _Collector) -> DatabaseConfig:
    section = _mapping(value, "database", collector, {"filename", "busy_timeout_ms", "wal_enabled"})
    filename = section.get("filename", default.filename)
    if not isinstance(filename, str) or not SAFE_DB_FILENAME_RE.match(filename):
        collector.warn("数据库文件名无效，已使用默认值。")
        filename = default.filename
    return DatabaseConfig(
        filename=filename,
        busy_timeout_ms=_int_range(section, "busy_timeout_ms", default.busy_timeout_ms, 1000, 60000, collector),
        wal_enabled=_bool(section, "wal_enabled", default.wal_enabled, collector),
    )
