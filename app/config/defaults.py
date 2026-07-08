from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

APP_NAME = "GasSafetyAlarm"
CONFIG_FILE_NAME = "config.json"
DEFAULT_DATABASE_FILE = "app.sqlite3"
API_PORT_MIN = 1024
API_PORT_MAX = 65535
PAGE_SIZE_MAX = 100
PROTOCOL_MODES = {"protocol_1", "protocol_2"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True)
class ApiConfig:
    enabled: bool = False
    bind_address: str = "127.0.0.1"
    port: int = 8765
    cors_enabled: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    # Debug remains false by default and is ignored from user config until a
    # production-safe operator switch is designed.
    debug: bool = False
    protocol_mode: str = "protocol_1"


@dataclass(frozen=True)
class AcquisitionConfig:
    poll_interval_ms: int = 1000
    request_timeout_ms: int = 1500
    retry_limit: int = 3
    offline_after_failures: int = 3


@dataclass(frozen=True)
class BackupConfig:
    scheduled_enabled: bool = False
    interval_hours: int = 24
    keep_last: int = 10


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5


@dataclass(frozen=True)
class DatabaseConfig:
    filename: str = DEFAULT_DATABASE_FILE
    busy_timeout_ms: int = 5000
    wal_enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_config() -> AppConfig:
    return AppConfig()
