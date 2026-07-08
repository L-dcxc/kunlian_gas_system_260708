from __future__ import annotations

# ruff: noqa: E402

import tempfile
import unittest
from pathlib import Path

from tests.qt_test_harness import configure_qt_test_environment

configure_qt_test_environment()

from PySide6.QtWidgets import QApplication

from app.config.defaults import DatabaseConfig
from app.db.connection import Database
from app.services.license_codes import DEFAULT_LICENSE_SIGNING_KEY, build_authorization_code
from app.services.license_service import LicenseService
from tools.license_keygen import build_license_payload, main, normalize_expires_at, normalize_machine_code
from tools.license_keygen_gui import LicenseKeygenWindow
from tools.license_registry import CustomerLicenseStore


class LicenseKeygenToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_generated_code_activates_existing_license_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
            database.initialize()
            service = LicenseService(
                database,
                activation_signing_key=b"test-signing-key",
                machine_fingerprint_provider=lambda: "machine-secret-material",
            )

            payload = build_license_payload(
                machine_code=service.machine_fingerprint_hash(),
                customer_name="客户 A",
                note="现场 1",
            )
            code = build_authorization_code(payload, b"test-signing-key")
            result = service.activate(code)

            self.assertTrue(result.success)
            self.assertTrue(result.data.can_enter_main_system)
            self.assertIsNone(result.data.expires_at)

    def test_default_product_key_generates_code_without_customer_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
            database.initialize()
            service = LicenseService(
                database,
                machine_fingerprint_provider=lambda: "machine-secret-material",
            )
            payload = build_license_payload(machine_code=service.machine_fingerprint_hash(), customer_name="客户 B")
            code = build_authorization_code(payload, DEFAULT_LICENSE_SIGNING_KEY)

            result = service.activate(code)

            self.assertTrue(result.success)
            self.assertTrue(result.data.can_enter_main_system)

    def test_expiration_date_and_machine_code_validation(self) -> None:
        self.assertEqual(normalize_expires_at("2027-12-31"), "2027-12-31T23:59:59+08:00")
        self.assertEqual(normalize_machine_code("AA " * 32), "aa" * 32)
        with self.assertRaises(ValueError):
            normalize_machine_code("1234")

    def test_cli_writes_license_file_without_printing_signing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "customer.lic"
            exit_code = main(
                [
                    "--machine-code",
                    "ab" * 32,
                    "--expires-at",
                    "2027-12-31",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.read_text(encoding="utf-8").startswith("gas-license-v1."))

    def test_keygen_ui_generates_records_and_copies_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CustomerLicenseStore(Path(temp_dir) / "customers.json")
            window = LicenseKeygenWindow(store)
            window.customer_name_edit.setText("客户 C")
            window.machine_code_edit.setText("ab" * 32)
            window.note_edit.setText("一号项目")

            self.assertTrue(window.generate())
            self.assertIn("QTabBar#KeygenTabBar::tab:selected", window.styleSheet())
            self.assertIn("QTableWidget#CustomerRecordsTable::item:selected", window.styleSheet())
            self.assertTrue(window.result_edit.toPlainText().startswith("gas-license-v1."))
            self.assertEqual(window.records_table.rowCount(), 1)
            issued_text = window.records_table.item(0, 5).text()
            self.assertNotIn("T", issued_text)
            self.assertRegex(issued_text, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
            self.assertTrue(window.copy_code())
            self.assertEqual(QApplication.clipboard().text(), window.result_edit.toPlainText())

            window.records_table.selectRow(0)
            self.assertTrue(window.copy_selected_record_code())
            self.assertTrue(window.load_selected_record_to_form())
            self.assertEqual(window.customer_name_edit.text(), "客户 C")

    def test_keygen_ui_code_activates_default_license_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
            database.initialize()
            service = LicenseService(database, machine_fingerprint_provider=lambda: "machine-secret-material")
            window = LicenseKeygenWindow(CustomerLicenseStore(Path(temp_dir) / "customers.json"))
            window.machine_code_edit.setText(service.machine_fingerprint_hash())

            self.assertTrue(window.generate())
            code = window.result_edit.toPlainText()
            self.assertTrue(service.activate(code).success)


if __name__ == "__main__":
    unittest.main()
