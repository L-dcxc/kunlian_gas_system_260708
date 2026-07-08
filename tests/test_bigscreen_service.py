from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.core.state_store import StateStore
from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.settings_repository import SettingsRepository
from app.db.unit_of_work import UnitOfWork
from app.services.bigscreen_service import (
    BIGSCREEN_ALARM_PRIORITY_KEY,
    BIGSCREEN_INTERVAL_KEY,
    BIGSCREEN_PAGES_KEY,
    BigscreenService,
)
from app.services.models import (
    AcquisitionState,
    AcquisitionStatus,
    DeviceReading,
    DeviceSourceType,
    DeviceStatus,
    ProtocolMode,
)


class TrackingStateStore(StateStore):
    def __init__(self) -> None:
        super().__init__(publish_interval_ms=0)
        self.snapshot_reads = 0

    def get_realtime_snapshot(self, filters=None):
        self.snapshot_reads += 1
        return super().get_realtime_snapshot(filters)


class BigscreenServiceTests(unittest.TestCase):
    def _database(self, temp_dir: str) -> Database:
        database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
        database.initialize()
        return database

    def _seed_config(self, database: Database) -> dict[str, object]:
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
                INSERT INTO gas_types(name, unit, range_min, range_max, default_alarm_low, default_alarm_high,
                                      is_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("methane", "%LEL", 0, 100, 20, 50, 1),
            ).lastrowid
            controller_id = uow.execute(
                """
                INSERT INTO controllers(port_id, name, address, model, detector_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (port_id, "controller1", 1, "ctrl-x", 4),
            ).lastrowid
            detector_ids: list[int] = []
            names = [
                "normal detector",
                "alarm detector",
                "offline detector",
                "E:\\secret\\app.sqlite3 SELECT password <b>bad</b> token",
            ]
            for index, name in enumerate(names, start=1):
                detector_ids.append(
                    int(
                        uow.execute(
                            """
                            INSERT INTO detectors(controller_id, port_id, position_code, name, model, protocol_address,
                                                  register_index, gas_type_id, unit, range_min, range_max, alarm_low,
                                                  alarm_high, alarm_type, store_interval_sec)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                controller_id,
                                port_id,
                                f"D-{index:03d}",
                                name,
                                "probe-x",
                                index,
                                index - 1,
                                gas_id,
                                "%LEL",
                                0,
                                100,
                                20,
                                50,
                                "low_high",
                                60,
                            ),
                        ).lastrowid
                    )
                )
            map_id = uow.execute(
                """
                INSERT INTO maps(name, safe_filename, original_filename, relative_path, size_bytes, is_enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("factory map", "safe.png", "original.png", "maps/safe.png", 64, 1),
            ).lastrowid
            point_id = uow.execute(
                """
                INSERT INTO map_points(map_id, detector_id, label, x_ratio, y_ratio)
                VALUES (?, ?, ?, ?, ?)
                """,
                (map_id, detector_ids[1], "alarm point", 0.25, 0.75),
            ).lastrowid
            uow.commit()
        return {
            "port_id": int(port_id),
            "gas_id": int(gas_id),
            "controller_id": int(controller_id),
            "detector_ids": tuple(detector_ids),
            "map_id": int(map_id),
            "point_id": int(point_id),
        }

    def _reading(
        self,
        ids: dict[str, object],
        detector_id: int,
        status: DeviceStatus,
        concentration: float | None,
    ) -> DeviceReading:
        return DeviceReading(
            protocol=ProtocolMode.PROTOCOL_2,
            source_type=DeviceSourceType.PROBE,
            port_id=int(ids["port_id"]),
            controller_id=int(ids["controller_id"]),
            detector_id=detector_id,
            controller_address=1,
            detector_address=detector_id,
            status=status,
            concentration=concentration,
            gas_type="methane",
            unit="%LEL",
            alarm_level=1 if status is DeviceStatus.ALARM_LOW else None,
            raw_status=status.value,
            raw_value="fixture raw value",
            timestamp=datetime.now(timezone.utc),
        )

    def test_default_carousel_config_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            service = BigscreenService(database, StateStore())
            before = self._table_counts(database)

            result = service.get_carousel_config()
            after = self._table_counts(database)

            self.assertTrue(result.success, result.message)
            self.assertEqual(result.data.pages, ("data", "map", "devices"))
            self.assertEqual(result.data.interval_seconds, 15)
            self.assertTrue(result.data.alarm_priority_enabled)
            self.assertGreaterEqual(result.data.refresh_after_ms, 250)
            self.assertEqual(before, after)

    def test_custom_config_is_allowlisted_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            with UnitOfWork(database) as uow:
                settings = SettingsRepository(uow)
                settings.set_value(BIGSCREEN_PAGES_KEY, '["map", "unknown", "devices"]', value_type="json")
                settings.set_value(BIGSCREEN_INTERVAL_KEY, "3", value_type="integer")
                settings.set_value(BIGSCREEN_ALARM_PRIORITY_KEY, "false", value_type="boolean")
                uow.commit()

            result = BigscreenService(database, StateStore()).get_carousel_config()

            self.assertTrue(result.success, result.message)
            self.assertEqual(result.data.pages, ("map", "devices"))
            self.assertEqual(result.data.interval_seconds, 15)
            self.assertFalse(result.data.alarm_priority_enabled)

    def test_metrics_summary_counts_runtime_statuses_and_refresh_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            ids = self._seed_config(database)
            detector_ids = ids["detector_ids"]
            state = StateStore(publish_interval_ms=0)
            state.set_value("acquisition.status", AcquisitionState(AcquisitionStatus.RUNNING))
            state.update_readings(
                [
                    self._reading(ids, detector_ids[0], DeviceStatus.NORMAL, 10.0),
                    self._reading(ids, detector_ids[1], DeviceStatus.ALARM_LOW, 25.0),
                    self._reading(ids, detector_ids[3], DeviceStatus.FAULT, None),
                ]
            )
            with UnitOfWork(database) as uow:
                AlarmRepository(uow).create_active(
                    detector_id=detector_ids[1],
                    alarm_type="alarm_low",
                    alarm_level=1,
                    trigger_value=25.0,
                    start_time=datetime.now(timezone.utc).isoformat(),
                )
                uow.commit()

            summary = BigscreenService(database, state).get_metrics_summary()

            self.assertTrue(summary.success, summary.message)
            self.assertEqual(summary.data.total_detectors, 4)
            self.assertEqual(summary.data.normal_count, 1)
            self.assertEqual(summary.data.alarm_count, 1)
            self.assertEqual(summary.data.offline_count, 1)
            self.assertEqual(summary.data.fault_count, 1)
            self.assertEqual(summary.data.active_alarm_count, 1)
            self.assertEqual(summary.data.acquisition_status, "running")
            self.assertGreaterEqual(summary.data.refresh_after_ms, 250)

    def test_active_alarm_creates_focus_and_no_active_alarm_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            ids = self._seed_config(database)
            detector_ids = ids["detector_ids"]
            state = StateStore(publish_interval_ms=0)
            state.update_readings([self._reading(ids, detector_ids[1], DeviceStatus.NORMAL, 12.0)])
            service = BigscreenService(database, state)

            no_alarm = service.get_alarm_focus()
            self.assertTrue(no_alarm.success, no_alarm.message)
            self.assertIsNone(no_alarm.data)

            with UnitOfWork(database) as uow:
                AlarmRepository(uow).create_active(
                    detector_id=detector_ids[1],
                    alarm_type="alarm_low",
                    alarm_level=1,
                    trigger_value=25.0,
                    start_time=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                )
                uow.commit()

            focus = service.get_alarm_focus()

            self.assertTrue(focus.success, focus.message)
            self.assertIsNotNone(focus.data)
            self.assertEqual(focus.data.detector_id, detector_ids[1])
            self.assertEqual(focus.data.alarm_type, "alarm_low")
            self.assertTrue(focus.data.device_card.active_alarm)
            self.assertIsNotNone(focus.data.map_point)
            self.assertEqual(focus.data.map_point.x_ratio, 0.25)
            self.assertEqual(focus.data.map_point.y_ratio, 0.75)
            self.assertFalse(hasattr(focus.data.map_point, "x_px"))
            self.assertFalse(hasattr(focus.data.map_point, "pixel_x"))

    def test_snapshot_sanitizes_user_text_and_keeps_ratio_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            ids = self._seed_config(database)
            detector_ids = ids["detector_ids"]
            state = StateStore(publish_interval_ms=0)
            state.update_readings([self._reading(ids, detector_ids[3], DeviceStatus.FAULT, None)])

            result = BigscreenService(database, state).get_snapshot()

            self.assertTrue(result.success, result.message)
            output = str(result.data)
            for forbidden in ("E:\\secret", "sqlite", "SELECT", "password", "token", "<b>", "</b>"):
                self.assertNotIn(forbidden, output)
            self.assertIn("[redacted]", output)
            point = result.data.map_points[0]
            self.assertEqual(point.x_ratio, 0.25)
            self.assertEqual(point.y_ratio, 0.75)
            self.assertFalse(hasattr(point, "x_pixel"))
            self.assertFalse(hasattr(point, "y_pixel"))

    def test_read_methods_do_not_change_stateful_tables_or_call_control_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            ids = self._seed_config(database)
            state = TrackingStateStore()
            state.update_readings([self._reading(ids, ids["detector_ids"][0], DeviceStatus.NORMAL, 10.0)])
            service = BigscreenService(database, state)
            before = self._table_counts(database)

            self.assertTrue(service.get_carousel_config().success)
            self.assertTrue(service.get_metrics_summary().success)
            self.assertTrue(service.get_alarm_focus().success)
            self.assertTrue(service.get_snapshot().success)
            after = self._table_counts(database)

            self.assertEqual(before, after)
            self.assertEqual(state.snapshot_reads, 3)

    def _table_counts(self, database: Database) -> dict[str, int]:
        with UnitOfWork(database) as uow:
            counts = {}
            for table in (
                "system_settings",
                "alarm_records",
                "linkage_records",
                "backup_records",
                "ports",
                "controllers",
                "detectors",
                "maps",
                "map_points",
                "users",
                "operation_logs",
            ):
                counts[table] = int(uow.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"])
            uow.commit()
        return counts


if __name__ == "__main__":
    unittest.main()
