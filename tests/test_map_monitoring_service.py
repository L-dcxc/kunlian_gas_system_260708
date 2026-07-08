from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.core.state_store import StateStore
from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.map_repository import MapPointRepository, MapRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import AuthService, SessionStore, hash_password
from app.services.device_config_service import ControllerCommand, DetectorCommand, DeviceConfigService, GasTypeCommand, PortCommand
from app.services.file_validation import FileValidator
from app.services.map_service import MapService, MapUploadCommand, SaveMapPointCommand
from app.services.models import DeviceReading, DeviceSourceType, DeviceStatus, ProtocolMode


class MapMonitoringServiceTests(unittest.TestCase):
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

    def _service(self, database: Database, store: SessionStore, root: Path, state_store: StateStore | None = None):
        return MapService(
            database,
            store,
            validator=FileValidator(data_root=root),
            maps_dir=root / "maps",
            state_store=state_store,
        )

    def _seed_detectors(self, database: Database, store: SessionStore, admin_session, count: int = 1):
        service = DeviceConfigService(database, store)
        port = service.save_port(
            admin_session,
            PortCommand(name="COM1", channel_type="serial", serial_port_name="COM1", baud_rate=9600),
        ).data
        gas = service.save_gas_type(
            admin_session,
            GasTypeCommand(name="methane", unit="%LEL", range_min=0, range_max=100, default_alarm_low=20),
        ).data
        controller = service.save_controller(
            admin_session,
            ControllerCommand(port_id=int(port["id"]), name="controller1", address=1, detector_count=count),
        ).data
        detectors = []
        for index in range(1, count + 1):
            detectors.append(
                service.save_detector(
                    admin_session,
                    DetectorCommand(
                        port_id=int(port["id"]),
                        controller_id=int(controller["id"]),
                        position_code=f"M-{index:03d}",
                        name=f"detector{index}",
                        protocol_address=index,
                        register_index=index - 1,
                        gas_type_id=int(gas["id"]),
                        unit="%LEL",
                        range_min=0,
                        range_max=100,
                        alarm_low=20,
                        store_interval_sec=60,
                    ),
                ).data
            )
        return port, controller, detectors

    def _valid_png(self, path: Path) -> Path:
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"map-bytes")
        return path

    def _upload_map(self, service: MapService, admin_session, root: Path, name: str = "factory map"):
        source = self._valid_png(root / f"{name.replace(' ', '_')}.png")
        result = service.upload_map(admin_session, MapUploadCommand(source_path=source, name=name))
        self.assertTrue(result.success, result.message)
        return result.data

    def _reading(self, detector_id: int, status: DeviceStatus, concentration: float | None) -> DeviceReading:
        return DeviceReading(
            protocol=ProtocolMode.PROTOCOL_2,
            source_type=DeviceSourceType.PROBE,
            port_id=1,
            controller_id=1,
            detector_id=detector_id,
            controller_address=1,
            detector_address=detector_id,
            status=status,
            concentration=concentration,
            gas_type="methane",
            unit="%LEL",
            alarm_level=1 if status is DeviceStatus.ALARM_LOW else None,
            raw_status=status.value,
            raw_value="fixture-summary",
            timestamp=datetime.now(timezone.utc),
        )

    def test_upload_rejects_illegal_extension_and_forged_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            service = self._service(database, store, root)

            bad_extension = root / "map.svg"
            bad_extension.write_text("<svg></svg>", encoding="utf-8")
            extension_result = service.upload_map(admin_session, MapUploadCommand(source_path=bad_extension))
            self.assertFalse(extension_result.success)
            self.assertEqual(extension_result.code, 400)

            forged_header = root / "map.png"
            forged_header.write_bytes(b"not-a-png")
            header_result = service.upload_map(admin_session, MapUploadCommand(source_path=forged_header))
            self.assertFalse(header_result.success)
            self.assertEqual(header_result.code, 400)

    def test_upload_stores_only_relative_path_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            service = self._service(database, store, root)
            source = self._valid_png(root / "..unsafe original name.png")

            uploaded = service.upload_map(admin_session, MapUploadCommand(source_path=source, name="plant map"))
            self.assertTrue(uploaded.success, uploaded.message)
            view = uploaded.data
            self.assertFalse(Path(view.relative_path).is_absolute())
            self.assertTrue(view.relative_path.startswith("maps/"))
            self.assertNotIn("..", view.relative_path)
            self.assertEqual(len(view.content_hash or ""), 64)

            with UnitOfWork(database) as uow:
                row = MapRepository(uow).find_active_by_id(view.id)
                self.assertIsNotNone(row)
                self.assertFalse(Path(str(row["relative_path"])).is_absolute())
                self.assertEqual(len(str(row["content_hash"])), 64)
                uow.commit()

    def test_point_ratio_boundaries_and_out_of_range_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            _, _, detectors = self._seed_detectors(database, store, admin_session)
            service = self._service(database, store, root)
            map_view = self._upload_map(service, admin_session, root)

            low_edge = service.save_point(
                admin_session,
                SaveMapPointCommand(map_id=map_view.id, detector_id=int(detectors[0]["id"]), x_ratio=0, y_ratio=0),
            )
            self.assertTrue(low_edge.success, low_edge.message)
            high_edge = service.save_point(
                admin_session,
                SaveMapPointCommand(map_id=map_view.id, detector_id=int(detectors[0]["id"]), x_ratio=1, y_ratio=1),
            )
            self.assertTrue(high_edge.success, high_edge.message)
            rejected = service.save_point(
                admin_session,
                SaveMapPointCommand(map_id=map_view.id, detector_id=int(detectors[0]["id"]), x_ratio=1.01, y_ratio=0.5),
            )
            self.assertFalse(rejected.success)
            self.assertEqual(rejected.code, 400)

    def test_same_detector_point_is_upserted_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            _, _, detectors = self._seed_detectors(database, store, admin_session)
            service = self._service(database, store, root)
            first_map = self._upload_map(service, admin_session, root, "first map")
            second_map = self._upload_map(service, admin_session, root, "second map")
            detector_id = int(detectors[0]["id"])

            first = service.save_point(
                admin_session,
                SaveMapPointCommand(map_id=first_map.id, detector_id=detector_id, x_ratio=0.2, y_ratio=0.3),
            )
            second = service.save_point(
                admin_session,
                SaveMapPointCommand(map_id=second_map.id, detector_id=detector_id, x_ratio=0.7, y_ratio=0.8),
            )
            self.assertTrue(first.success)
            self.assertTrue(second.success)
            self.assertEqual(first.data.id, second.data.id)

            with UnitOfWork(database) as uow:
                rows = MapPointRepository(uow).fetch_all(
                    "SELECT * FROM map_points WHERE detector_id = ? AND deleted_at IS NULL",
                    (detector_id,),
                )
                self.assertEqual(len(rows), 1)
                self.assertEqual(int(rows[0]["map_id"]), second_map.id)
                uow.commit()

    def test_runtime_view_aggregates_normal_alarm_and_offline_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            _, _, detectors = self._seed_detectors(database, store, admin_session, count=3)
            state_store = StateStore()
            service = self._service(database, store, root, state_store=state_store)
            map_view = self._upload_map(service, admin_session, root)
            detector_ids = [int(item["id"]) for item in detectors]
            for index, detector_id in enumerate(detector_ids):
                point = service.save_point(
                    admin_session,
                    SaveMapPointCommand(
                        map_id=map_view.id,
                        detector_id=detector_id,
                        x_ratio=0.2 + index * 0.2,
                        y_ratio=0.4,
                    ),
                )
                self.assertTrue(point.success, point.message)

            state_store.update_readings(
                [
                    self._reading(detector_ids[0], DeviceStatus.NORMAL, 12.5),
                    self._reading(detector_ids[1], DeviceStatus.ALARM_LOW, 28.0),
                ]
            )
            with UnitOfWork(database) as uow:
                AlarmRepository(uow).create_active(
                    detector_id=detector_ids[1],
                    alarm_type="alarm_low",
                    alarm_level=1,
                    trigger_value=28.0,
                    start_time=datetime.now(timezone.utc).isoformat(),
                )
                uow.commit()

            runtime = service.get_map_runtime_view(map_view.id)
            self.assertTrue(runtime.success, runtime.message)
            by_detector = {point.detector_id: point for point in runtime.data.points}
            self.assertEqual(by_detector[detector_ids[0]].status, "normal")
            self.assertEqual(by_detector[detector_ids[0]].concentration, 12.5)
            self.assertEqual(by_detector[detector_ids[1]].status, "alarm_low")
            self.assertTrue(by_detector[detector_ids[1]].active_alarm)
            self.assertEqual(by_detector[detector_ids[1]].active_alarm_type, "alarm_low")
            self.assertEqual(by_detector[detector_ids[2]].status, "offline")

    def test_operator_write_permission_failures_are_logged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, operator_session = self._sessions(database)
            _, _, detectors = self._seed_detectors(database, store, admin_session)
            service = self._service(database, store, root)
            map_view = self._upload_map(service, admin_session, root)
            source = self._valid_png(root / "operator.png")

            upload_denied = service.upload_map(operator_session, MapUploadCommand(source_path=source))
            save_denied = service.save_point(
                operator_session,
                SaveMapPointCommand(map_id=map_view.id, detector_id=int(detectors[0]["id"]), x_ratio=0.5, y_ratio=0.5),
            )
            delete_denied = service.delete_map(operator_session, map_view.id)
            self.assertEqual(upload_denied.code, 403)
            self.assertEqual(save_denied.code, 403)
            self.assertEqual(delete_denied.code, 403)

            with UnitOfWork(database) as uow:
                denied_rows, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                self.assertGreaterEqual(len(denied_rows), 3)
                uow.commit()

    def test_delete_map_with_active_points_returns_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            _, _, detectors = self._seed_detectors(database, store, admin_session)
            service = self._service(database, store, root)
            map_view = self._upload_map(service, admin_session, root)
            point = service.save_point(
                admin_session,
                SaveMapPointCommand(map_id=map_view.id, detector_id=int(detectors[0]["id"]), x_ratio=0.1, y_ratio=0.9),
            )
            self.assertTrue(point.success, point.message)

            deleted = service.delete_map(admin_session, map_view.id)
            self.assertFalse(deleted.success)
            self.assertEqual(deleted.code, 409)


if __name__ == "__main__":
    unittest.main()
