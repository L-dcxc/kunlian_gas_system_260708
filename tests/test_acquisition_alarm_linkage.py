from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.core.runtime_locks import RuntimeLockManager
from app.core.state_store import StateStore
from app.core.workers import WorkerPool
from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.linkage_repository import LinkageRecordRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.runtime_repository import RealtimeSnapshotRepository, RunningRecordRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.device.channels.base import ChannelConfig, ChannelErrorCode, ChannelType, SerialParameters, TransactResult
from app.device.polling.acquisition_service import AcquisitionService
from app.device.polling.port_worker import PollingTarget, PortPollingConfig, PortTargetGroup, PortWorker
from app.device.protocols.base import CRCByteOrder, PollRequest, ValidationErrorCode, ValidationResult
from app.services.alarm_service import AlarmService
from app.services.auth_service import AuthService, SessionStore, hash_password
from app.services.device_config_service import ControllerCommand, DetectorCommand, DeviceConfigService, GasTypeCommand, PortCommand
from app.services.linkage_service import (
    LinkageObjectCommand,
    LinkageRuleCommand,
    LinkageService,
    ManualLinkageCommand,
)
from app.services.models import AcquisitionStatus, DeviceReading, DeviceSourceType, DeviceStatus, ProtocolMode


class FakeChannel:
    def __init__(self, results: list[TransactResult]) -> None:
        self.results = list(results)
        self.opened = False
        self.closed = False
        self.sent: list[bytes] = []

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True
        self.opened = False

    def transact(self, payload: bytes, timeout_ms: int | None = None) -> TransactResult:
        self.sent.append(payload)
        if self.results:
            return self.results.pop(0)
        return TransactResult.failure(ChannelErrorCode.TIMEOUT, "timeout")


class FakeAdapter:
    mode = ProtocolMode.PROTOCOL_2

    def __init__(self, reading: DeviceReading, *, valid: bool = True) -> None:
        self.reading = reading
        self.valid = valid
        self.parse_count = 0

    def build_poll_requests(self, context):
        target = context.targets[0]
        return [
            PollRequest(
                protocol=context.protocol,
                source_type=context.source_type,
                port_id=context.port_id,
                unit_address=target.detector_address or 1,
                function_code=3,
                payload=b"\x01\x03\x00\x00\x00\x04\x44\x09",
                timeout_ms=100,
                expected_response_min_bytes=5,
                crc_byte_order=CRCByteOrder.LOW_BYTE_FIRST,
                detector_id=target.detector_id,
                controller_id=target.controller_id,
            )
        ]

    def validate_response(self, request, response: bytes) -> ValidationResult:
        if self.valid:
            return ValidationResult.success(response)
        return ValidationResult.failure(ValidationErrorCode.CRC_MISMATCH, "crc mismatch", raw_frame=response)

    def parse_response(self, request, response: bytes) -> list[DeviceReading]:
        self.parse_count += 1
        return [self.reading]


class FakeLifecycleWorker:
    def __init__(self, **kwargs) -> None:
        self.config = kwargs["config"]
        self._failures = 0

    def run(self, token):
        while not token.is_cancelled:
            time.sleep(0.01)
        return self.snapshot()

    def snapshot(self):
        from app.device.polling.port_worker import PortWorkerSnapshot

        return PortWorkerSnapshot(self.config.port_id, True, self._failures, ())


class AcquisitionAlarmLinkageTests(unittest.TestCase):
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

    def _seed_config(self, database: Database, store: SessionStore, admin_session, *, second_port: bool = False):
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
            ControllerCommand(port_id=int(port["id"]), name="controller1", address=1, detector_count=8),
        ).data
        detector = service.save_detector(
            admin_session,
            DetectorCommand(
                port_id=int(port["id"]),
                controller_id=int(controller["id"]),
                position_code="A-001",
                name="detector1",
                protocol_address=1,
                register_index=0,
                gas_type_id=int(gas["id"]),
                unit="%LEL",
                range_min=0,
                range_max=100,
                alarm_low=20,
                store_interval_sec=1,
            ),
        ).data
        second = None
        if second_port:
            second = service.save_port(
                admin_session,
                PortCommand(name="COM2", channel_type="serial", serial_port_name="COM2", baud_rate=9600),
            ).data
            service.save_detector(
                admin_session,
                DetectorCommand(
                    port_id=int(second["id"]),
                    position_code="A-002",
                    name="detector2",
                    protocol_address=2,
                    register_index=0,
                    gas_type_id=int(gas["id"]),
                    unit="%LEL",
                    range_min=0,
                    range_max=100,
                    alarm_low=20,
                    store_interval_sec=1,
                ),
            )
        return port, detector, second

    def _reading(self, detector_id: int, status: DeviceStatus, concentration: float | None = 10.0) -> DeviceReading:
        return DeviceReading(
            protocol=ProtocolMode.PROTOCOL_2,
            source_type=DeviceSourceType.PROBE,
            port_id=1,
            controller_id=None,
            detector_id=detector_id,
            controller_address=None,
            detector_address=detector_id,
            status=status,
            concentration=concentration,
            gas_type="methane",
            unit="%LEL",
            alarm_level=1 if status is DeviceStatus.ALARM_LOW else None,
            raw_status=status.value,
            raw_value="fixture",
            timestamp=datetime.now(timezone.utc),
        )

    def test_acquisition_lifecycle_permission_lock_and_idempotent_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin_session, operator_session = self._sessions(database)
            self._seed_config(database, store, admin_session)
            worker_pool = WorkerPool(max_workers=2)
            try:
                locks = RuntimeLockManager()
                service = AcquisitionService(
                    database=database,
                    session_store=store,
                    state_store=StateStore(),
                    worker_pool=worker_pool,
                    runtime_locks=locks,
                    worker_factory=FakeLifecycleWorker,
                    channel_factory=lambda config: FakeChannel([]),
                )
                denied = service.start(operator_session)
                self.assertFalse(denied.success)
                self.assertEqual(denied.code, 403)

                locks.acquire_operation("restore", timeout=0)
                locked = service.start(admin_session)
                self.assertFalse(locked.success)
                self.assertEqual(locked.code, 409)
                locks.release_operation("restore")

                started = service.start(admin_session)
                self.assertTrue(started.success)
                self.assertEqual(started.data.status, AcquisitionStatus.RUNNING)
                repeated = service.start(admin_session)
                self.assertTrue(repeated.success)
                self.assertEqual(repeated.data.status, AcquisitionStatus.RUNNING)
                stopped = service.stop(admin_session)
                self.assertTrue(stopped.success)
                self.assertEqual(stopped.data.status, AcquisitionStatus.STOPPED)
                self.assertTrue(service.stop(admin_session).success)

                with UnitOfWork(database) as uow:
                    denied_rows, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                    start_rows, _ = OperationLogRepository(uow).list_for_action(action_type="acquisition.start")
                    self.assertGreaterEqual(len(denied_rows), 1)
                    self.assertEqual(len(start_rows), 1)
                    uow.commit()
            finally:
                worker_pool.shutdown()

    def test_port_worker_valid_invalid_timeout_and_other_port_continue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            _, detector, second_port = self._seed_config(database, store, admin_session, second_port=True)
            detector_id = int(detector["id"])
            config = self._worker_config(1, detector_id)
            state = StateStore()
            alarm = AlarmService(database)

            adapter = FakeAdapter(self._reading(detector_id, DeviceStatus.NORMAL))
            worker = PortWorker(
                config=config,
                adapter=adapter,
                database=database,
                state_store=state,
                alarm_service=alarm,
                channel_factory=lambda cfg: FakeChannel([TransactResult.success(b"ok")]),
                sleep=lambda _: None,
            )
            result = worker.poll_once()
            self.assertEqual(len(result.valid_readings), 1)
            self.assertEqual(len(state.get_realtime_snapshot()), 1)

            invalid_adapter = FakeAdapter(self._reading(detector_id, DeviceStatus.ALARM_LOW, 30.0), valid=False)
            invalid_worker = PortWorker(
                config=config,
                adapter=invalid_adapter,
                database=database,
                state_store=StateStore(),
                alarm_service=alarm,
                channel_factory=lambda cfg: FakeChannel([TransactResult.success(b"bad")]),
                sleep=lambda _: None,
            )
            invalid = invalid_worker.poll_once()
            self.assertEqual(invalid.invalid_response_count, 1)
            self.assertEqual(invalid_adapter.parse_count, 0)

            failing_worker = PortWorker(
                config=config,
                adapter=adapter,
                database=database,
                state_store=StateStore(),
                alarm_service=alarm,
                channel_factory=lambda cfg: FakeChannel([
                    TransactResult.failure(ChannelErrorCode.TIMEOUT, "timeout"),
                    TransactResult.failure(ChannelErrorCode.TIMEOUT, "timeout"),
                ]),
                sleep=lambda _: None,
            )
            self.assertEqual(len(failing_worker.poll_once().offline_readings), 0)
            offline = failing_worker.poll_once()
            self.assertEqual(offline.offline_readings[0].status, DeviceStatus.OFFLINE)

            second_config = self._worker_config(int(second_port["id"]), 2)
            good_worker = PortWorker(
                config=second_config,
                adapter=FakeAdapter(self._reading(2, DeviceStatus.NORMAL)),
                database=database,
                state_store=StateStore(),
                alarm_service=alarm,
                channel_factory=lambda cfg: FakeChannel([TransactResult.success(b"ok")]),
                sleep=lambda _: None,
            )
            self.assertEqual(len(good_worker.poll_once().valid_readings), 1)
            with UnitOfWork(database) as uow:
                snapshots, _ = RealtimeSnapshotRepository(uow).list_current(per_page=10)
                records, _, total = RunningRecordRepository(uow).list_records(per_page=10)
                self.assertGreaterEqual(len(snapshots), 2)
                self.assertGreaterEqual(total, 2)
                self.assertGreaterEqual(len(records), 2)
                uow.commit()

    def test_alarm_active_dedupe_recovery_and_auto_linkage_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin_session, _ = self._sessions(database)
            _, detector, _ = self._seed_config(database, store, admin_session)
            detector_id = int(detector["id"])
            linkage = LinkageService(database, store)
            obj = linkage.save_object(admin_session, LinkageObjectCommand("relay", "Fan 1")).data
            linkage.save_rule(
                admin_session,
                LinkageRuleCommand(name="low alarm fan", object_id=int(obj["id"]), alarm_type="alarm_low", action="start"),
            )
            alarm = AlarmService(database, linkage)

            first = alarm.ingest_readings([self._reading(detector_id, DeviceStatus.ALARM_LOW, 30.0)])
            second = alarm.ingest_readings([self._reading(detector_id, DeviceStatus.ALARM_LOW, 35.0)])
            self.assertEqual(len(first.created), 1)
            self.assertEqual(len(first.linkage_record_ids), 1)
            self.assertEqual(len(second.created), 0)
            self.assertEqual(len(second.linkage_record_ids), 0)

            recovered = alarm.ingest_readings([self._reading(detector_id, DeviceStatus.NORMAL, 10.0)])
            self.assertEqual(len(recovered.recovered), 1)
            with UnitOfWork(database) as uow:
                active = AlarmRepository(uow).list_active()
                linkage_rows = LinkageRecordRepository(uow).list_for_alarm(first.created[0].alarm_record_id)
                history, _, total = AlarmRepository(uow).list_history(detector_id=detector_id, per_page=10)
                self.assertEqual(active, [])
                self.assertEqual(len(linkage_rows), 1)
                self.assertEqual(total, 1)
                self.assertEqual(history[0]["status"], "recovered")
                self.assertIsNotNone(history[0]["end_time"])
                uow.commit()

    def test_manual_linkage_admin_simulated_operator_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin_session, operator_session = self._sessions(database)
            service = LinkageService(database, store)
            obj = service.save_object(admin_session, LinkageObjectCommand("relay", "Fan 1"))
            self.assertTrue(obj.success)
            denied = service.manual_control(operator_session, ManualLinkageCommand(int(obj.data["id"]), "start"))
            self.assertFalse(denied.success)
            self.assertEqual(denied.code, 403)
            manual = service.manual_control(admin_session, ManualLinkageCommand(int(obj.data["id"]), "start"))
            self.assertTrue(manual.success)
            self.assertEqual(manual.data["result"], "simulated_success")
            with UnitOfWork(database) as uow:
                denied_rows, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                manual_rows, _ = OperationLogRepository(uow).list_for_action(action_type="linkage.manual_control")
                self.assertGreaterEqual(len(denied_rows), 1)
                self.assertEqual(len(manual_rows), 1)
                uow.commit()

    def _worker_config(self, port_id: int, detector_id: int) -> PortPollingConfig:
        return PortPollingConfig(
            port_id=port_id,
            protocol=ProtocolMode.PROTOCOL_2,
            channel_config=ChannelConfig(
                port_id=port_id,
                channel_type=ChannelType.SERIAL,
                serial=SerialParameters(port_name=f"COM{port_id}"),
                timeout_ms=100,
            ),
            target_groups=(
                PortTargetGroup(
                    source_type=DeviceSourceType.PROBE,
                    targets=(PollingTarget(detector_id=detector_id, detector_address=detector_id, store_interval_sec=1),),
                ),
            ),
            poll_interval_ms=100,
            timeout_ms=100,
            failure_threshold=2,
            reconnect_interval_ms=1,
        )


if __name__ == "__main__":
    unittest.main()
