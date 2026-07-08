from __future__ import annotations

import unittest

from tests.qt_test_harness import configure_qt_test_environment


class QtTestHarnessBootstrapTests(unittest.TestCase):
    def test_qt_test_environment_is_configured_before_ui_tests(self) -> None:
        configure_qt_test_environment()


configure_qt_test_environment()


if __name__ == "__main__":
    unittest.main()
