from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.db.connection import Database
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import AuthService, SessionStore, hash_password
from app.services.device_config_service import (
    ControllerCommand,
    DetectorCommand,
    DeviceConfigService,
    GasTypeCommand,
    PortCommand,
)
from app.services.file_validation import FileValidator
from app.services.import_export import ImportExportService
from app.services.map_config_service import MapConfigService, MapPointCommand, MapUploadCommand
from app.services.models import AcquisitionStatus


class DeviceConfigurationTests(unittest.TestCase):
    def _database(self, temp_dir: str) -> Database:
        database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
        database.initialize()
        return database

    def _seed_user(self, database: Database, username: str, password: str, role: str) -> None:
        password_hash, password_salt = hash_password(password)
        with UnitOfWork(database) as uow:
            UserRepository(uow).create_user(
                username=username,
                password_hash=password_hash,
                password_salt=password_salt,
                role=role,
                is_active=True,
            )
            uow.commit()

    def _sessions(self, database: Database):
        self._seed_user(database, "admin", "AdminPass123", "admin")
        self._seed_user(database, "operator", "Operator123", "operator")
        store = SessionStore()
        auth = AuthService(database, store)
        return store, auth.login("admin", "AdminPass123").data, auth.login("operator", "Operator123").data

    def _service(self, database: Database, store: SessionStore, root: Path, status=AcquisitionStatus.STOPPED):
        validator = FileValidator(data_root=root)
        import_export = ImportExportService(validator)
        return DeviceConfigService(
            database,
            store,
            import_export=import_export,
            acquisition_status_provider=lambda: status,
        )

    def _seed_config(self, service: DeviceConfigService, admin_session):
        port = service.save_port(
            admin_session,
            PortCommand(name="COM1", channel_type="serial", serial_port_name="COM1", baud_rate=9600),
        ).data
        gas = service.save_gas_type(
            admin_session,
            GasTypeCommand(
                name="methane",
                unit="%LEL",
                range_min=0,
                range_max=100,
                default_alarm_low=20,
                default_alarm_high=50,
            ),
        ).data
        controller = service.save_controller(
            admin_session,
            ControllerCommand(port_id=int(port["id"]), name="controller1", address=1, detector_count=8),
        ).data
        return port, gas, controller

    def test_port_controller_gas_type_validation_duplicates_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, operator_session = self._sessions(database)
            service = self._service(database, store, root)

            port, gas, _ = self._seed_config(service, admin_session)
            duplicate_port = service.save_port(
                admin_session,
                PortCommand(name="COM1", channel_type="serial", serial_port_name="COM2"),
            )
            self.assertFalse(duplicate_port.success)
            self.assertEqual(duplicate_port.code, 409)

            bad_tcp = service.save_port(
                admin_session,
                PortCommand(name="bad", channel_type="tcp", tcp_host="127.0.0.1", tcp_port=70000),
            )
            self.assertFalse(bad_tcp.success)
            self.assertEqual(bad_tcp.code, 400)

            duplicate_controller = service.save_controller(
                admin_session,
                ControllerCommand(port_id=int(port["id"]), name="controller2", address=1),
            )
            self.assertFalse(duplicate_controller.success)
            self.assertEqual(duplicate_controller.code, 409)

            bad_gas = service.save_gas_type(
                admin_session,
                GasTypeCommand(name="badgas", unit="%LEL", range_min=100, range_max=1),
            )
            self.assertFalse(bad_gas.success)
            self.assertEqual(bad_gas.code, 400)

            denied = service.save_gas_type(
                operator_session,
                GasTypeCommand(name="operatorGas", unit="ppm", range_min=0, range_max=1000),
            )
            self.assertFalse(denied.success)
            self.assertEqual(denied.code, 403)

            snapshot = service.configuration_snapshot()
            self.assertEqual(len(snapshot["ports"]), 1)
            self.assertEqual(gas["name"], "methane")
            with UnitOfWork(database) as uow:
                denied_rows, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                success_rows, _ = OperationLogRepository(uow).list_for_action(action_type="device_config.port.create")
                self.assertGreaterEqual(len(denied_rows), 1)
                self.assertEqual(success_rows[0]["result"], "success")
                uow.commit()

    def test_detector_crud_import_template_and_formula_protection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            service = self._service(database, store, root)
            port, gas, controller = self._seed_config(service, admin_session)

            detector = service.save_detector(
                admin_session,
                DetectorCommand(
                    port_id=int(port["id"]),
                    controller_id=int(controller["id"]),
                    position_code="A-001",
                    name="detector1",
                    protocol_address=2,
                    register_index=0,
                    gas_type_id=int(gas["id"]),
                    unit="%LEL",
                    range_min=0,
                    range_max=100,
                    alarm_low=20,
                    alarm_high=50,
                    store_interval_sec=60,
                ),
            )
            self.assertTrue(detector.success)
            duplicate = service.save_detector(
                admin_session,
                DetectorCommand(
                    port_id=int(port["id"]),
                    position_code="A-001",
                    name="duplicate",
                    protocol_address=3,
                    register_index=0,
                    gas_type_id=int(gas["id"]),
                    unit="%LEL",
                    range_min=0,
                    range_max=100,
                    store_interval_sec=60,
                ),
            )
            self.assertFalse(duplicate.success)
            self.assertEqual(duplicate.code, 409)

            formula_csv = root / "formula.csv"
            formula_csv.write_text(
                "position_code,name,port_id,controller_id,protocol_address,register_index,gas_type_id,unit,"
                "range_min,range_max,alarm_low,alarm_high,store_interval_sec\n"
                f"=BAD(),Detector,{port['id']},,{4},0,{gas['id']},%LEL,0,100,20,50,60\n",
                encoding="utf-8",
            )
            formula_result = service.import_detectors(admin_session, formula_csv)
            self.assertTrue(formula_result.success)
            self.assertEqual(formula_result.data.imported_count, 0)
            self.assertEqual(formula_result.data.errors[0].row_number, 2)

            invalid_csv = root / "invalid.csv"
            invalid_csv.write_text(
                "position_code,name,port_id,controller_id,protocol_address,register_index,gas_type_id,unit,"
                "range_min,range_max,alarm_low,alarm_high,store_interval_sec\n"
                f"A-002,Detector,{port['id']},,248,0,{gas['id']},%LEL,0,100,20,50,60\n",
                encoding="utf-8",
            )
            invalid_result = service.import_detectors(admin_session, invalid_csv)
            self.assertTrue(invalid_result.success)
            self.assertEqual(invalid_result.data.errors[0].field, "protocol_address")

            valid_csv = root / "valid.csv"
            valid_csv.write_text(
                "position_code,name,port_id,controller_id,protocol_address,register_index,gas_type_id,unit,"
                "range_min,range_max,alarm_low,alarm_high,store_interval_sec\n"
                f"A-002,Detector2,{port['id']},,4,0,{gas['id']},%LEL,0,100,20,50,60\n",
                encoding="utf-8",
            )
            valid_result = service.import_detectors(admin_session, valid_csv)
            self.assertTrue(valid_result.success)
            self.assertEqual(valid_result.data.imported_count, 1)

            template_path = root / "exports" / "template.csv"
            template = service.export_detector_template(template_path)
            self.assertTrue(template.success)
            self.assertIn("position_code", template_path.read_text(encoding="utf-8-sig"))

    def test_protocol_mode_validation_and_running_acquisition_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            stopped_service = self._service(database, store, root)
            invalid = stopped_service.set_protocol_mode(admin_session, "protocol_x")
            self.assertFalse(invalid.success)
            self.assertEqual(invalid.code, 400)

            running_service = self._service(database, store, root, status=AcquisitionStatus.RUNNING)
            rejected = running_service.set_protocol_mode(admin_session, "protocol_2")
            self.assertFalse(rejected.success)
            self.assertEqual(rejected.code, 409)

            changed = stopped_service.set_protocol_mode(admin_session, "protocol_2")
            self.assertTrue(changed.success)
            self.assertTrue(changed.data.restart_required)
            self.assertEqual(stopped_service.get_protocol_mode(), "protocol_2")

    def test_map_upload_path_ratio_permissions_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, operator_session = self._sessions(database)
            device_service = self._service(database, store, root)
            port, gas, _ = self._seed_config(device_service, admin_session)
            detector = device_service.save_detector(
                admin_session,
                DetectorCommand(
                    port_id=int(port["id"]),
                    position_code="M-001",
                    name="map detector",
                    protocol_address=5,
                    register_index=0,
                    gas_type_id=int(gas["id"]),
                    unit="%LEL",
                    range_min=0,
                    range_max=100,
                    store_interval_sec=60,
                ),
            ).data
            validator = FileValidator(data_root=root)
            map_service = MapConfigService(database, store, validator=validator, maps_dir=root / "maps")
            source = root / "uploaded unsafe name.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")

            uploaded = map_service.upload_map(admin_session, MapUploadCommand(source_path=source, name="factory map"))
            self.assertTrue(uploaded.success)
            self.assertTrue(str(uploaded.data["relative_path"]).startswith("maps/"))
            self.assertNotIn("..", str(uploaded.data["relative_path"]))

            bad_ratio = map_service.save_map_point(
                admin_session,
                MapPointCommand(
                    map_id=int(uploaded.data["id"]),
                    detector_id=int(detector["id"]),
                    x_ratio=1.2,
                    y_ratio=0.2,
                ),
            )
            self.assertFalse(bad_ratio.success)
            self.assertEqual(bad_ratio.code, 400)

            point = map_service.save_map_point(
                admin_session,
                MapPointCommand(
                    map_id=int(uploaded.data["id"]),
                    detector_id=int(detector["id"]),
                    x_ratio=0.2,
                    y_ratio=0.8,
                ),
            )
            self.assertTrue(point.success)
            self.assertEqual(point.data["x_ratio"], 0.2)

            denied = map_service.update_map(operator_session, int(uploaded.data["id"]), name="denied")
            self.assertFalse(denied.success)
            self.assertEqual(denied.code, 403)
            delete_bound = map_service.delete_map(admin_session, int(uploaded.data["id"]))
            self.assertFalse(delete_bound.success)
            self.assertEqual(delete_bound.code, 409)

            with UnitOfWork(database) as uow:
                rows, _ = OperationLogRepository(uow).list_for_action(action_type="map_config.point.save")
                self.assertEqual(rows[0]["result"], "success")
                uow.commit()


if __name__ == "__main__":
    unittest.main()
