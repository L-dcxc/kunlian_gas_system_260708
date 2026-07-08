from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.login_preferences import LoginPreferenceStore


class LoginPreferenceStoreTests(unittest.TestCase):
    def test_save_and_load_last_username_without_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "login_preferences.json"
            store = LoginPreferenceStore(path)

            store.save_last_username(" admin\r\noperator ")

            self.assertEqual(store.get_last_username(), "admin operator")
            self.assertNotIn("password", path.read_text(encoding="utf-8").lower())

    def test_invalid_preferences_file_returns_empty_username(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "login_preferences.json"
            path.write_text("{bad-json", encoding="utf-8")
            store = LoginPreferenceStore(path)

            self.assertEqual(store.get_last_username(), "")


if __name__ == "__main__":
    unittest.main()
