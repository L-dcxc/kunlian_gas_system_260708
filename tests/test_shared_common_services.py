from __future__ import annotations

import socket
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.core.audit import AuditLogger, InMemoryAuditSink
from app.db.connection import Database
from app.db.repositories.base import EntityRepository
from app.db.repositories.record_repository import OperationLogRepository
from app.db.unit_of_work import UnitOfWork
from app.device.channels.base import ChannelConfig, ChannelError, ChannelErrorCode, ChannelType, SerialParameters, TcpParameters
from app.device.channels.serial_channel import SerialChannel
from app.device.channels.tcp_channel import TcpChannel
from app.services.errors import ErrorCode, PermissionDenied, ServiceError, ValidationError, to_service_result
from app.services.file_validation import FileValidator, validate_csv_import
from app.services.import_export import ImportExportService, ImportTemplate
from app.services.permission_guard import PermissionGuard, SimplePermissionUser


class SampleRepository(EntityRepository):
    table_name = "sample"
    allowed_sort_columns = frozenset({"id", "name", "created_at"})
    default_sort = "id"


class FakeSerial:
    def __init__(self, response: bytes = b"\x01\x03") -> None:
        self.response = response
        self.closed = False
        self.written = b""
        self.timeout = 0
        self.write_timeout = 0

    def write(self, payload: bytes) -> None:
        self.written += payload

    def flush(self) -> None:
        pass

    def read(self, size: int) -> bytes:
        return self.response[:size]

    def close(self) -> None:
        self.closed = True


class FakeSocket:
    def __init__(self, response: bytes = b"\x01\x03\x02") -> None:
        self.response = response
        self.sent = b""
        self.timeout = 0
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, size: int) -> bytes:
        return self.response[:size]

    def close(self) -> None:
        self.closed = True


class SharedCommonServiceTests(unittest.TestCase):
    def test_repository_uses_uow_pagination_and_sort_whitelist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
            database.initialize()
            with UnitOfWork(database) as uow:
                uow.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)")
                uow.execute("INSERT INTO sample(name, created_at) VALUES (?, ?)", ("b", "2026-01-02T00:00:00+00:00"))
                uow.execute("INSERT INTO sample(name, created_at) VALUES (?, ?)", ("a", "2026-01-01T00:00:00+00:00"))
                repo = SampleRepository(uow)
                rows, pagination = repo.list_page(page=1, per_page=1, sort_by="name", sort_direction="ASC")
                self.assertEqual(pagination.limit, 1)
                self.assertEqual(rows[0]["name"], "a")
                with self.assertRaises(ValueError):
                    repo.list_page(sort_by="name; DROP TABLE sample", sort_direction="ASC")
                uow.commit()

    def test_permission_guard_denial_writes_audit_and_operation_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
            database.initialize()
            sink = InMemoryAuditSink()
            user = SimplePermissionUser(id=7, username="operator", role="operator", permissions=())
            guard = PermissionGuard(audit=AuditLogger(sink), database=database)
            with self.assertRaises(PermissionDenied):
                guard.require(user, "backup.restore", "restore backup")

            connection = database.connect()
            try:
                with self.assertRaises(Exception):
                    connection.execute("SELECT * FROM rolled_back")
            finally:
                connection.close()

            self.assertEqual(len(sink.records), 1)
            with UnitOfWork(database) as uow:
                rows, _ = OperationLogRepository(uow).list_page(sort_by="created_at", sort_direction="DESC")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["result"], "denied")
                uow.commit()

    def test_file_validation_rejects_extension_size_path_traversal_and_formula(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validator = FileValidator(data_root=root)
            valid_map = root / "map.png"
            valid_map.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x")
            self.assertTrue(validator.validate_map_file(valid_map).ok)

            bad_map = root / "map.txt"
            bad_map.write_text("not image", encoding="utf-8")
            self.assertFalse(validator.validate_map_file(bad_map).ok)

            spoofed_map = root / "spoofed.png"
            spoofed_map.write_text("not image", encoding="utf-8")
            self.assertFalse(validator.validate_map_file(spoofed_map).ok)

            big_map = root / "map2.png"
            big_map.write_bytes(b"x" * 11)
            self.assertFalse(validator.validate_map_file(big_map, max_bytes=10).ok)

            backup = root / "bad.zip"
            with zipfile.ZipFile(backup, "w") as archive:
                archive.writestr("../escape.txt", "bad")
                archive.writestr("manifest.json", "{}")
            self.assertFalse(validator.validate_backup_candidate(backup).ok)

            csv_path = root / "import.csv"
            csv_path.write_text("name,position\n=HYPERLINK(1),A1\nnormal,A2\n", encoding="utf-8")
            result = validate_csv_import(csv_path, required_fields=("name",), allowed_fields=("name", "position"))
            self.assertFalse(result.ok)
            self.assertEqual(result.errors[0].row_number, 2)

            xlsx_path = root / "formula.xlsx"
            with zipfile.ZipFile(xlsx_path, "w") as archive:
                archive.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData><f>SUM(A1:A2)</f></sheetData></worksheet>")
            self.assertFalse(validator.validate_import_file(xlsx_path).ok)

            with self.assertRaises(ValidationError):
                validator.ensure_within_data_root(root.parent / "outside.csv")

    def test_import_export_uses_template_whitelist_and_neutralizes_export_formulas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validator = FileValidator(data_root=root)
            service = ImportExportService(validator)
            source = root / "devices.csv"
            source.write_text("name,position\nDetector,A1\n", encoding="utf-8")
            plan = service.prepare_import(source, ImportTemplate(required_fields=("name",), allowed_fields=("name", "position")))
            self.assertTrue(plan.validation.ok)
            destination = root / "exports" / "out.csv"
            service.export_csv(destination, ("name",), ({"name": "=1+1"},))
            self.assertIn("'=1+1", destination.read_text(encoding="utf-8-sig"))

    def test_controlled_error_conversion(self) -> None:
        denied = to_service_result(PermissionDenied())
        self.assertFalse(denied.success)
        self.assertEqual(denied.code, int(ErrorCode.PERMISSION_DENIED))
        generic = to_service_result(ServiceError("internal detail"))
        self.assertEqual(generic.code, int(ErrorCode.INTERNAL_ERROR))

    def test_tcp_channel_sends_raw_rtu_frame_and_handles_failures(self) -> None:
        fake = FakeSocket(response=b"\x01\x03\x00")
        config = ChannelConfig(port_id=1, channel_type=ChannelType.TCP, tcp=TcpParameters(host="127.0.0.1", port=1502))
        channel = TcpChannel(config, socket_factory=lambda address, timeout: fake)
        self.assertEqual(channel.transact(b"\x01").error_code, ChannelErrorCode.NOT_OPEN)
        channel.open()
        result = channel.transact(b"\x01\x03\x00\x00")
        self.assertTrue(result.ok)
        self.assertEqual(fake.sent, b"\x01\x03\x00\x00")

        timeout_channel = TcpChannel(config, socket_factory=lambda address, timeout: (_ for _ in ()).throw(socket.timeout()))
        with self.assertRaises(ChannelError):
            timeout_channel.open()

    def test_serial_channel_uses_optional_dependency_boundary_and_failure_results(self) -> None:
        config = ChannelConfig(
            port_id=1,
            channel_type=ChannelType.SERIAL,
            serial=SerialParameters(port_name="COM1"),
        )
        serial_obj = FakeSerial(response=b"\x01\x03")
        channel = SerialChannel(config, serial_factory=lambda **kwargs: serial_obj)
        self.assertEqual(channel.transact(b"\x01").error_code, ChannelErrorCode.NOT_OPEN)
        channel.open()
        result = channel.transact(b"\x01\x03")
        self.assertTrue(result.ok)
        self.assertEqual(serial_obj.written, b"\x01\x03")
        channel.close()
        self.assertTrue(serial_obj.closed)

        failing = SerialChannel(config, serial_factory=lambda **kwargs: (_ for _ in ()).throw(OSError("boom")))
        with self.assertRaises(ChannelError):
            failing.open()


if __name__ == "__main__":
    unittest.main()
