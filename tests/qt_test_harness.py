from __future__ import annotations

import atexit
import os
import sys
import tempfile
import threading
from typing import TextIO

from app.core.logging import Redactor

QT_FONT_LOG_RULE = "qt.qpa.fonts=false"
_CAPTURE_ENV = "GAS_ALARM_TEST_CAPTURE_QT_STDERR"
_CAPTURE_DISABLED = {"0", "false", "False", "no", "NO"}
_CAPTURE_LOCK = threading.Lock()
_CAPTURE_STARTED = False
_CAPTURE_CLOSED = False
_ORIGINAL_STDERR_FD: int | None = None
_CAPTURED_STDERR: TextIO | None = None


def configure_qt_test_environment() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _append_qt_logging_rule(QT_FONT_LOG_RULE)
    if os.environ.get(_CAPTURE_ENV) not in _CAPTURE_DISABLED:
        _start_native_stderr_capture()


def _append_qt_logging_rule(rule: str) -> None:
    existing_rules = os.environ.get("QT_LOGGING_RULES", "").strip()
    if not existing_rules:
        os.environ["QT_LOGGING_RULES"] = rule
        return
    rules = {item.strip() for item in existing_rules.split(";")}
    if rule not in rules:
        os.environ["QT_LOGGING_RULES"] = f"{existing_rules};{rule}"


def _start_native_stderr_capture() -> None:
    global _CAPTURE_STARTED, _ORIGINAL_STDERR_FD, _CAPTURED_STDERR
    with _CAPTURE_LOCK:
        if _CAPTURE_STARTED:
            return
        sys.stderr.flush()
        _ORIGINAL_STDERR_FD = os.dup(2)
        _CAPTURED_STDERR = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
        os.dup2(_CAPTURED_STDERR.fileno(), 2)
        _CAPTURE_STARTED = True
        atexit.register(_replay_sanitized_native_stderr)


def _replay_sanitized_native_stderr() -> None:
    global _CAPTURE_CLOSED
    with _CAPTURE_LOCK:
        if _CAPTURE_CLOSED or not _CAPTURE_STARTED:
            return
        _CAPTURE_CLOSED = True
        captured = _CAPTURED_STDERR
        original_fd = _ORIGINAL_STDERR_FD

    if captured is None or original_fd is None:
        return

    try:
        sys.stderr.flush()
        captured.flush()
        os.dup2(original_fd, 2)
        captured.seek(0)
        safe_text = Redactor().redact(captured.read()).rstrip()
        if safe_text:
            print(safe_text, file=sys.stderr)
    finally:
        captured.close()
        os.close(original_fd)
