from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Iterable

from app.config.defaults import LoggingConfig

LOGGER_NAME = "gas_alarm"
_base_logger = logging.getLogger(LOGGER_NAME)
_base_logger.addHandler(logging.NullHandler())
_base_logger.propagate = False
SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)(authorization_code|auth_code|license_code)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?token|token|secret|key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)(machine[_-]?id|machine[_-]?code|hardware[_-]?id)\s*[:=]\s*[^\s,;]+"),
]
ABSOLUTE_PATH_PATTERN = re.compile(r"(?i)((?<![A-Za-z])[A-Z]:[\\/][^\s,;]+|(?<![:/])/[A-Za-z0-9_.-]+(?:/[^\s,;]+)+)")


class Redactor:
    def __init__(self, sensitive_paths: Iterable[Path] = ()) -> None:
        self._path_tokens = sorted(
            {str(path.resolve()) for path in sensitive_paths if path},
            key=len,
            reverse=True,
        )

    def redact(self, value: object) -> str:
        text = str(value)
        for pattern in SENSITIVE_VALUE_PATTERNS:
            text = pattern.sub(lambda match: f"{match.group(1)}=<redacted>", text)
        for token in self._path_tokens:
            text = text.replace(token, "<path>")
        text = ABSOLUTE_PATH_PATTERN.sub("<path>", text)
        return text


class RedactionFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        if record.exc_info:
            exc_type, exc_value, _ = record.exc_info
            rendered = f"{rendered} | exception={exc_type.__name__}: {exc_value}"
            record.exc_info = None
            record.exc_text = None
        record.msg = self._redactor.redact(rendered)
        record.args = ()
        return True


def configure_logging(log_dir: Path, config: LoggingConfig, sensitive_paths: Iterable[Path] = ()) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, config.level, logging.INFO))
    logger.propagate = False
    _close_handlers(logger)

    redactor = Redactor(sensitive_paths)
    log_file = log_dir / "application.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RedactionFilter(redactor))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console_handler.addFilter(RedactionFilter(redactor))
    logger.addHandler(console_handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def shutdown_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    _close_handlers(logger)
    logger.addHandler(logging.NullHandler())


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def user_safe_error(exc: BaseException) -> str:
    return Redactor().redact(f"操作失败: {exc.__class__.__name__}")
