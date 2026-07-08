from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.core.state_store import StateStore
from app.db.connection import Database
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.record_repository import RecordRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import AuthService, SessionStore, hash_password
from app.services.chart_service import ChartService, HistoryCurveQuery
from app.services.export_service import ExportService
from app.services.models import DeviceReading, DeviceSourceType, DeviceStatus, ProtocolMode
from app.services.record_service import ClearRecordsCommand, ExportRecordsCommand, RecordQuery, RecordService


class ChartRecordsTests(unittest.TestCase):
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

    def _seed_device_and_records(self, database: Database) -> tuple[int, int, str, str]:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=1)).isoformat()
        later = (now - timedelta(minutes=30)).isoformat()
        end = (now + timedelta(minutes=1)).isoformat()
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
                INSERT INTO gas_types(name, unit, range_min, range_max, is_enabled)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("methane", "%LEL", 0, 100, 1),
            ).lastrowid
            controller_id = uow.execute(
                """
                INSERT INTO controllers(port_id, name, address, detector_count)
                VALUES (?, ?, ?, ?)
                """,
                (port_id, "controller1", 1, 1),
            ).lastrowid
            detector_id = uow.execute(
                """
                INSERT INTO detectors(controller_id, port_id, position_code, name, protocol_address, register_index,
                                      gas_type_id, unit, range_min, range_max, alarm_type, store_interval_sec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (controller_id, port_id, "A-001", "=detector", 1, 0, gas_id, "%LEL", 0, 100, "low_high", 1),
            ).lastrowid
            uow.execute(
                """
                INSERT INTO running_records(detector_id, protocol, source_type, port_id, controller_id, status,
                                            concentration, gas_type, unit, alarm_level, raw_status, raw_value,
                                            quality, recorded_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detector_id,
                    "protocol_2",
                    "probe",
                    port_id,
                    controller_id,
                    "normal",
                    10.0,
                    "methane",
                    "%LEL",
                    None,
                    "normal",
                    "01 03 should stay raw text",
                    "valid",
                    start,
                    start,
                ),
            )
            uow.execute(
                """
                INSERT INTO running_records(detector_id, protocol, source_type, port_id, controller_id, status,
                                            concentration, gas_type, unit, alarm_level, raw_status, raw_value,
                                            quality, recorded_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detector_id,
                    "protocol_2",
                    "probe",
                    port_id,
                    controller_id,
                    "alarm_low",
                    25.0,
                    "methane",
                    "%LEL",
                    1,
                    "alarm_low",
                    "=cmd|' /C calc'!A0",
                    "valid",
                    later,
                    later,
                ),
            )
            uow.execute(
                """
                INSERT INTO alarm_records(detector_id, alarm_type, alarm_level, trigger_value, start_time, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (detector_id, "alarm_low", 1, 25.0, later, "active"),
            )
            OperationLogRepository(uow).add(
                action_type="records.test",
                result="success",
                actor_id=1,
                actor_name="=operator",
                target_type="test",
                target_id="1",
                summary="<unsafe summary>",
                details={"keyword": "ordinary"},
            )
            uow.commit()
        return int(port_id), int(detector_id), start, end

    def test_record_repository_pagination_time_sort_whitelist_and_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            _, detector_id, start, end = self._seed_device_and_records(database)
            with UnitOfWork(database) as uow:
                repo = RecordRepository(uow)
                rows, pagination, total = repo.list_running_records(
                    detector_id=detector_id,
                    start_time=start,
                    end_time=end,
                    page=1,
                    per_page=1,
                    sort_by="recorded_at",
                    sort_direction="ASC",
                )
                self.assertEqual(pagination.per_page, 1)
                self.assertEqual(total, 2)
                self.assertEqual(float(rows[0]["concentration"]), 10.0)

                with self.assertRaises(ValueError):
                    repo.list_running_records(start_time=start, end_time=end, per_page=101)
                with self.assertRaises(ValueError):
                    repo.list_running_records(start_time=start, end_time=end, sort_by="recorded_at; DROP TABLE users")

                operations, _, operation_total = repo.list_operation_records(keyword="%' OR 1=1 --", start_time=start, end_time=end)
                self.assertEqual(operation_total, 0)
                users_count = uow.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
                self.assertEqual(users_count, 0)
                uow.commit()

    def test_record_service_permission_confirmation_logs_delete_clear_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin_session, operator_session = self._sessions(database)
            _, detector_id, start, end = self._seed_device_and_records(database)
            service = RecordService(database, store)

            query = service.query_records(
                operator_session,
                RecordQuery("running", filters={"detector_id": detector_id, "start_time": start, "end_time": end}),
            )
            self.assertTrue(query.success)
            self.assertEqual(query.data.total, 2)

            denied = service.delete_record(operator_session, record_type="running", record_id=1, confirmed=True)
            self.assertFalse(denied.success)
            self.assertEqual(denied.code, 403)

            unconfirmed = service.delete_record(admin_session, record_type="running", record_id=1, confirmed=False)
            self.assertFalse(unconfirmed.success)
            self.assertEqual(unconfirmed.code, 400)

            deleted = service.delete_record(admin_session, record_type="running", record_id=1, confirmed=True)
            self.assertTrue(deleted.success)

            cleared = service.clear_records(
                admin_session,
                ClearRecordsCommand("running", filters={"detector_id": detector_id, "start_time": start, "end_time": end}, confirmed=True),
            )
            self.assertTrue(cleared.success)
            self.assertEqual(cleared.data.deleted_count, 1)

            exported = service.export_records(
                admin_session,
                ExportRecordsCommand("operation", filters={"start_time": start, "end_time": end}, export_format="xlsx"),
            )
            self.assertTrue(exported.success)
            text = str(exported.data.rows)
            self.assertNotIn(str(Path(temp_dir)), text)

            with UnitOfWork(database) as uow:
                denied_rows, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                delete_rows, _ = OperationLogRepository(uow).list_for_action(action_type="records.delete")
                clear_rows, _ = OperationLogRepository(uow).list_for_action(action_type="records.clear")
                self.assertGreaterEqual(len(denied_rows), 1)
                self.assertEqual(len(delete_rows), 1)
                self.assertEqual(len(clear_rows), 1)
                uow.commit()

    def test_export_service_formula_injection_and_pdf_escape(self) -> None:
        service = ExportService()
        xlsx = service.build_record_export(
            record_type="operation",
            export_format="xlsx",
            rows=[{"actor_name": "=operator", "summary": "+SUM(1,2)", "action_type": "records.test"}],
        )
        self.assertTrue(xlsx.success)
        self.assertEqual(xlsx.data.rows[0]["actor_name"], "'=operator")
        self.assertEqual(xlsx.data.rows[0]["summary"], "'+SUM(1,2)")

        pdf = service.build_record_export(
            record_type="operation",
            export_format="pdf",
            rows=[{"actor_name": "normal", "summary": "<script>alert(1)</script>", "action_type": "records.test"}],
        )
        self.assertTrue(pdf.success)
        self.assertIn("&lt;script&gt;", pdf.data.rows[0]["summary"])

    def test_chart_service_realtime_cache_and_history_query_do_not_parse_protocol_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            _, detector_id, start, end = self._seed_device_and_records(database)
            state = StateStore(curve_cache_size=5, publish_interval_ms=0)
            state.update_readings([
                DeviceReading(
                    protocol=ProtocolMode.PROTOCOL_2,
                    source_type=DeviceSourceType.PROBE,
                    port_id=1,
                    controller_id=1,
                    detector_id=detector_id,
                    controller_address=1,
                    detector_address=1,
                    status=DeviceStatus.NORMAL,
                    concentration=11.0,
                    gas_type="methane",
                    unit="%LEL",
                    alarm_level=None,
                    raw_status="normal",
                    raw_value="not parsed here",
                    timestamp=datetime.now(timezone.utc),
                )
            ])
            service = ChartService(database, state)
            realtime = service.get_realtime_series([detector_id])
            self.assertTrue(realtime.success)
            self.assertEqual(realtime.data[0].points[0].concentration, 11.0)

            history = service.query_history(HistoryCurveQuery(detector_ids=(detector_id,), start_time=start, end_time=end, per_page=10))
            self.assertTrue(history.success)
            self.assertEqual(history.data.total, 2)
            self.assertEqual(history.data.items[0].detector_id, detector_id)

            with self.assertRaises(ValueError):
                HistoryCurveQuery(
                    detector_ids=(detector_id,),
                    start_time=(datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
                    end_time=datetime.now(timezone.utc).isoformat(),
                )


if __name__ == "__main__":
    unittest.main()
