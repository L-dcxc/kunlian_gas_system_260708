from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config.defaults import APP_NAME, default_config
from app.core.paths import AppPaths, PathSetupError, default_user_data_dir, find_project_root, resolve_data_dir
from app.main import QT_FONT_LOG_RULE, _prepare_qt_smoke_environment, _sanitized_smoke_stderr


class PackagingRuntimePathTests(unittest.TestCase):
    def test_environment_data_dir_overrides_default_for_test_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"GAS_ALARM_DATA_DIR": temp_dir}, clear=False):
                paths = AppPaths.create()
            self.assertEqual(paths.data_dir, Path(temp_dir).resolve())
            self.assertTrue(paths.maps_dir.exists())
            self.assertTrue(paths.backups_dir.exists())
            self.assertTrue(paths.logs_dir.exists())
            self.assertTrue(paths.config_dir.exists())
            self.assertTrue(paths.db_dir.exists())

    def test_frozen_runtime_uses_per_user_data_root_not_bundle_root(self) -> None:
        with tempfile.TemporaryDirectory() as bundle_dir, tempfile.TemporaryDirectory() as local_app_data:
            env = {"LOCALAPPDATA": local_app_data, "APPDATA": "", "GAS_ALARM_DATA_DIR": ""}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch("app.core.paths.sys.frozen", True, create=True):
                    with mock.patch("app.core.paths.sys._MEIPASS", bundle_dir, create=True):
                        project_root = find_project_root()
                        data_dir = resolve_data_dir(project_root)

            self.assertEqual(project_root, Path(bundle_dir).resolve())
            self.assertEqual(data_dir, Path(local_app_data).resolve() / APP_NAME)
            self.assertNotEqual(data_dir, project_root / "data")

    def test_default_user_data_dir_falls_back_to_home_when_appdata_missing(self) -> None:
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "", "APPDATA": ""}, clear=False):
            self.assertEqual(default_user_data_dir(), Path.home().resolve() / APP_NAME)

    def test_path_setup_error_message_does_not_echo_sensitive_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "not-a-directory"
            file_path.write_text("blocked", encoding="utf-8")
            with self.assertRaises(PathSetupError) as caught:
                AppPaths.create(file_path)

            message = str(caught.exception)
            self.assertIn("运行数据目录", message)
            self.assertNotIn(str(file_path), message)

    def test_packaging_security_defaults_are_closed(self) -> None:
        config = default_config()
        self.assertFalse(config.runtime.debug)
        self.assertFalse(config.api.enabled)
        self.assertEqual(config.api.bind_address, "127.0.0.1")
        self.assertFalse(config.api.cors_enabled)

    def test_shell_smoke_sets_qt_font_log_rule_without_overwriting_existing_rules(self) -> None:
        with mock.patch.dict(os.environ, {"QT_LOGGING_RULES": "qt.network.ssl=false"}, clear=False):
            _prepare_qt_smoke_environment()
            rules = os.environ["QT_LOGGING_RULES"]
            self.assertIn("qt.network.ssl=false", rules)
            self.assertIn(QT_FONT_LOG_RULE, rules)
            self.assertEqual(os.environ.get("QT_QPA_PLATFORM"), "offscreen")

    def test_shell_smoke_stderr_replays_sanitized_native_qt_output(self) -> None:
        visible_stderr = io.StringIO()
        with mock.patch("sys.stderr", visible_stderr):
            with _sanitized_smoke_stderr():
                os.write(2, b"QFontDatabase path C:/Users/test/PySide6/lib/fonts\n")

        output = visible_stderr.getvalue()
        self.assertIn("QFontDatabase", output)
        self.assertIn("<path>", output)
        self.assertNotIn("C:/Users/test", output)


if __name__ == "__main__":
    unittest.main()
