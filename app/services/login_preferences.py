from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

LAST_USERNAME_KEY = "last_username"
MAX_USERNAME_CHARS = 80


class LoginPreferenceStore:
    def __init__(self, preferences_file: Path) -> None:
        self._preferences_file = preferences_file

    def get_last_username(self) -> str:
        try:
            raw = json.loads(self._preferences_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        username = raw.get(LAST_USERNAME_KEY) if isinstance(raw, dict) else ""
        return _normalize_username(username)

    def save_last_username(self, username: object) -> None:
        normalized = _normalize_username(username)
        if not normalized:
            return
        self._preferences_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {LAST_USERNAME_KEY: normalized}
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self._preferences_file.parent,
            prefix=f"{self._preferences_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
        temp_path.replace(self._preferences_file)


def _normalize_username(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:MAX_USERNAME_CHARS]
