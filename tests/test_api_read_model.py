from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.api.schemas import (
    envelope_from_result,
    validate_alarm_history_query,
    validate_detector_id,
    validate_realtime_devices_query,
)
from app.config.defaults import ApiConfig, AppConfig, DatabaseConfig
from app.core.state_store import StateStore
from app.db.connection import Database
from app.db.unit_of_work import UnitOfWork
from app.services.api_read_service import ApiReadService
from app.services.models import (
    AcquisitionState,
    AcquisitionStatus,
    DeviceReading,
    DeviceSourceType,
    DeviceStatus,
    ProtocolMode,
    ServiceError,
    ServiceResult,
)


class ApiReadModelTests(unittest.TestCase):
    def _database(self, temp_dir: str) -> Database:
        database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
        database.initialize()
        return database

    def _seed_config_and_records(self, database: Database) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(hours=1)
        with UnitOfWork(database) as uow:
            port_id = uow.execute(
                """
                INSERT INTO ports(name, channel_type, serial_port_name, poll_interval_ms, timeout_ms,
                                  failure_threshold, reconnect_interval_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("COM1", "serial", "COM1", 1000, 500, 3, 1000),
            ).lastrowid
            gas_id = uow.execute(
                """
                INSERT INTO gas_types(name, unit, range_min, range_max, default_alarm_low, default_alarm_high, is_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("methane", "%LEL", 0, 100, 20, 50, 1),
            ).lastrowid
            controller_id = uow.execute(
                """
                INSERT INTO controllers(port_id, name, address, model, detector_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (port_id, "controller1", 1, "ctrl-x", 1),
            ).lastrowid
            detector_id = uow.execute(
                """
                INSERT INTO detectors(controller_id, port_id, position_code, name, model, protocol_address, register_index,
                                      gas_type_id, unit, range_min, range_max, alarm_low, alarm_high, alarm_type,
                                      store_interval_sec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    controller_id,
                    port_id,
                    "A-001",
                    "detector1",
                    "probe-x",
                    1,
                    0,
                    gas_id,
                    "%LEL",
                    0,
                    100,
                    20,
                    50,
                    "low_high",
                    1,
                ),
            ).lastrowid
            active_alarm_id = uow.execute(
                """
                INSERT INTO alarm_records(detector_id, alarm_type, alarm_level, trigger_value, start_time, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (detector_id, "alarm_low", 1, 25.0, earlier.isoformat(), "active"),
            ).lastrowid
            recovered_alarm_id = uow.execute(
                """
                INSERT INTO alarm_records(detector_id, alarm_type, alarm_level, trigger_value, start_time, end_time, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (detector_id, "alarm_high", 2, 60.0, now.isoformat(), (now + timedelta(minutes=1)).isoformat(), "recovered"),
            ).lastrowid
            uow.commit()
        return {
            "port_id": int(port_id),
            "gas_id": int(gas_id),
            "controller_id": int(controller_id),
            "detector_id": int(detector_id),
            "active_alarm_id": int(active_alarm_id),
            "recovered_alarm_id": int(recovered_alarm_id),
            "history_start": (now - timedelta(days=1)).isoformat(),
            "history_end": (now + timedelta(days=1)).isoformat(),
        }

    def _reading(self, ids: dict[str, object], *, status: DeviceStatus = DeviceStatus.ALARM_LOW) -> DeviceReading:
        return DeviceReading(
            protocol=ProtocolMode.PROTOCOL_2,
            source_type=DeviceSourceType.PROBE,
            port_id=int(ids["port_id"]),
            controller_id=int(ids["controller_id"]),
            detector_id=int(ids["detector_id"]),
            controller_address=1,
            detector_address=1,
            status=status,
            concentration=26.5,
            gas_type="methane",
            unit="%LEL",
            alarm_level=1 if status is DeviceStatus.ALARM_LOW else None,
            raw_status=status.value,
            raw_value="raw frame is not exposed",
            timestamp=datetime.now(timezone.utc),
        )

    def test_health_envelope_and_query_validation_are_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            state = StateStore()
            state.set_value("acquisition.status", AcquisitionState(AcquisitionStatus.RUNNING))
            service = ApiReadService(database, state, AppConfig(api=ApiConfig(enabled=True)))

            envelope = envelope_from_result(service.health()).to_dict()
            self.assertTrue(envelope["success"])
            self.assertEqual(envelope["data"]["api_enabled"], True)
            self.assertEqual(envelope["data"]["acquisition_status"], "running")

            invalid_id = validate_detector_id(0)
            self.assertFalse(invalid_id.success)
            self.assertEqual(invalid_id.code, 400)
            self.assertNotIn(str(Path(temp_dir)), envelope_from_result(invalid_id).message)

            bad_page = validate_realtime_devices_query(page=0, per_page=101, status="DROP TABLE alarms")
            self.assertFalse(bad_page.success)
            self.assertEqual(bad_page.message, "参数校验失败")
            fields = {error.field for error in bad_page.errors}
            self.assertIn("page", fields)
            self.assertIn("per_page", fields)
            self.assertIn("status", fields)

            bad_range = validate_alarm_history_query(
                start_time=(datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
                end_time=datetime.now(timezone.utc).isoformat(),
            )
            self.assertFalse(bad_range.success)
            self.assertEqual(bad_range.code, 400)

    def test_error_envelope_redacts_sensitive_service_messages(self) -> None:
        leaked = ServiceResult.fail(
            code=500,
            message="sqlite3.OperationalError: SELECT password FROM users at E:\\secret\\app.sqlite3",
            errors=(
                ServiceError(
                    code="internal_error",
                    field="path E:\\secret\\app.sqlite3",
                    message="sqlite3.OperationalError: SELECT password FROM users at E:\\secret\\app.sqlite3",
                ),
            ),
        )

        envelope = envelope_from_result(leaked).to_dict()
        self.assertFalse(envelope["success"])
        self.assertEqual(envelope["message"], "操作失败，请稍后重试。")
        self.assertEqual(envelope["data"]["errors"][0]["message"], "操作失败，请稍后重试。")
        self.assertEqual(envelope["data"]["errors"][0]["field"], "")
        output = str(envelope)
        for forbidden in ("sqlite", "SELECT", "password", "E:\\secret"):
            self.assertNotIn(forbidden, output)

    def test_realtime_devices_and_single_device_read_from_state_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            ids = self._seed_config_and_records(database)
            state = StateStore(publish_interval_ms=0)
            state.update_readings([self._reading(ids)])
            service = ApiReadService(database, state)
            query = validate_realtime_devices_query(
                port_id=ids["port_id"], controller_id=ids["controller_id"], status="alarm_low", page=1, per_page=10
            ).data

            devices = service.list_realtime_devices(query)
            self.assertTrue(devices.success)
            self.assertEqual(devices.data.total, 1)
            item = devices.data.items[0]
            self.assertEqual(item.detector_id, ids["detector_id"])
            self.assertEqual(item.position_code, "A-001")
            self.assertEqual(item.controller_name, "controller1")
            self.assertEqual(item.concentration, 26.5)

            single = service.get_realtime_device(int(ids["detector_id"]))
            self.assertTrue(single.success)
            self.assertEqual(single.data.detector_name, "detector1")
            missing = service.get_realtime_device(999)
            self.assertFalse(missing.success)
            self.assertEqual(missing.code, 404)
            invalid = service.get_realtime_device(0)
            self.assertFalse(invalid.success)
            self.assertEqual(invalid.code, 400)

    def test_active_alarm_and_history_pagination_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            ids = self._seed_config_and_records(database)
            state = StateStore(publish_interval_ms=0)
            state.update_readings([self._reading(ids)])
            service = ApiReadService(database, state)

            active = service.list_active_alarms()
            self.assertTrue(active.success)
            self.assertEqual(len(active.data), 1)
            self.assertEqual(active.data[0].alarm_id, ids["active_alarm_id"])
            self.assertEqual(active.data[0].current_status, "alarm_low")
            self.assertEqual(active.data[0].controller_name, "controller1")

            query = validate_alarm_history_query(
                detector_id=ids["detector_id"],
                controller_id=ids["controller_id"],
                start_time=ids["history_start"],
                end_time=ids["history_end"],
                page=1,
                per_page=1,
                sort_by="start_time",
                sort_direction="DESC",
            ).data
            history = service.list_alarm_history(query)
            self.assertTrue(history.success)
            self.assertEqual(history.data.total, 2)
            self.assertEqual(history.data.pagination.per_page, 1)
            self.assertEqual(history.data.total_pages, 2)
            self.assertEqual(history.data.items[0].status, "recovered")

    def test_configuration_outputs_are_allowlisted_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            ids = self._seed_config_and_records(database)
            state = StateStore(publish_interval_ms=0)
            state.update_readings([self._reading(ids, status=DeviceStatus.NORMAL)])
            service = ApiReadService(database, state)
            before = self._table_counts(database)

            controllers = service.list_controllers()
            detectors = service.list_detectors()
            service.list_realtime_devices(validate_realtime_devices_query(page=1, per_page=20).data)
            service.list_active_alarms()
            after = self._table_counts(database)

            self.assertTrue(controllers.success)
            self.assertTrue(detectors.success)
            controller_keys = set(envelope_from_result(controllers).to_dict()["data"][0])
            detector_keys = set(envelope_from_result(detectors).to_dict()["data"][0])
            self.assertEqual(
                controller_keys,
                {"controller_id", "port_id", "controller_name", "address", "model", "detector_count", "enabled"},
            )
            self.assertEqual(
                detector_keys,
                {
                    "detector_id",
                    "position_code",
                    "detector_name",
                    "port_id",
                    "controller_id",
                    "gas_type_id",
                    "gas_type",
                    "unit",
                    "range_min",
                    "range_max",
                    "alarm_low",
                    "alarm_high",
                    "enabled",
                },
            )
            all_output = str(envelope_from_result(detectors).to_dict()) + str(envelope_from_result(controllers).to_dict())
            self.assertNotIn("deleted_at", all_output)
            self.assertNotIn("password", all_output)
            self.assertNotIn("license", all_output)
            self.assertEqual(before, after)

    def _table_counts(self, database: Database) -> dict[str, int]:
        with UnitOfWork(database) as uow:
            counts = {}
            for table in ("ports", "controllers", "detectors", "alarm_records", "operation_logs", "users", "linkage_records"):
                counts[table] = int(uow.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"])
            uow.commit()
        return counts


if __name__ == "__main__":
    unittest.main()
