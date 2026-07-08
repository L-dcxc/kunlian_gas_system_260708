from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.core.event_bus import EventBus
from app.db.connection import Database
from app.db.repositories.maintenance_repository import MaintenanceRepository
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
from app.services.maintenance_service import (
    MAINTENANCE_REMINDERS_DUE_EVENT,
    MaintenancePlanCommand,
    MaintenanceService,
)


class MaintenanceReminderTests(unittest.TestCase):
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

    def _seed_detector(
        self,
        database: Database,
        store: SessionStore,
        admin_session,
        *,
        sensor_life_until: str | None = None,
        calibration_cycle_days: int | None = None,
    ) -> dict[str, object]:
        service = DeviceConfigService(database, store)
        port = service.save_port(
            admin_session,
            PortCommand(name="COM1", channel_type="serial", serial_port_name="COM1", baud_rate=9600),
        ).data
        gas = service.save_gas_type(
            admin_session,
            GasTypeCommand(name="methane", unit="%LEL", range_min=0, range_max=100),
        ).data
        controller = service.save_controller(
            admin_session,
            ControllerCommand(port_id=int(port["id"]), name="controller1", address=1, detector_count=8),
        ).data
        detector_result = service.save_detector(
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
                store_interval_sec=60,
                sensor_life_until=sensor_life_until,
                calibration_cycle_days=calibration_cycle_days,
            ),
        )
        self.assertTrue(detector_result.success, detector_result.message)
        return detector_result.data

    def test_repository_validates_and_persists_plan_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin, _ = self._sessions(database)
            detector = self._seed_detector(database, store, admin)
            due_at = datetime.now(timezone.utc) + timedelta(days=3)

            with UnitOfWork(database) as uow:
                repo = MaintenanceRepository(uow)
                plan_id = repo.create(
                    {
                        "detector_id": int(detector["id"]),
                        "plan_type": "custom",
                        "due_at": due_at,
                        "remind_days_before": 2,
                        "status": "active",
                        "notes": "  inspect\nprobe  ",
                    },
                    actor_id=admin.user_id,
                )
                row = repo.find_active_by_id(plan_id)
                self.assertEqual(row["due_at"], due_at.isoformat())
                self.assertEqual(row["notes"], "inspect probe")
                self.assertEqual(row["created_by"], admin.user_id)
                with self.assertRaises(ValueError):
                    repo.create(
                        {
                            "detector_id": int(detector["id"]),
                            "plan_type": "bad",
                            "due_at": due_at,
                            "remind_days_before": 1,
                        }
                    )
                with self.assertRaises(ValueError):
                    repo.create(
                        {
                            "detector_id": int(detector["id"]),
                            "plan_type": "custom",
                            "due_at": "not-a-date",
                            "remind_days_before": 1,
                        }
                    )
                with self.assertRaises(ValueError):
                    repo.create(
                        {
                            "detector_id": int(detector["id"]),
                            "plan_type": "custom",
                            "due_at": due_at,
                            "remind_days_before": -1,
                        }
                    )
                with self.assertRaises(ValueError):
                    repo.create(
                        {
                            "detector_id": int(detector["id"]),
                            "plan_type": "custom",
                            "due_at": due_at,
                            "remind_days_before": 1,
                            "notes": "x" * 1001,
                        }
                    )
                uow.commit()

    def test_service_permissions_crud_and_audit_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin, operator = self._sessions(database)
            detector = self._seed_detector(database, store, admin)
            service = MaintenanceService(database, store)
            due_at = datetime.now(timezone.utc) + timedelta(days=10)

            denied = service.create_plan(
                operator,
                MaintenancePlanCommand(detector_id=int(detector["id"]), plan_type="custom", due_at=due_at),
            )
            self.assertFalse(denied.success)
            self.assertEqual(denied.code, 403)

            created = service.create_plan(
                admin,
                MaintenancePlanCommand(
                    detector_id=int(detector["id"]),
                    plan_type="custom",
                    due_at=due_at,
                    remind_days_before=3,
                    notes="replace sensor head",
                ),
            )
            self.assertTrue(created.success, created.message)
            self.assertEqual(created.data.notes, "replace sensor head")

            listed = service.list_plans(operator)
            self.assertTrue(listed.success, listed.message)
            self.assertEqual(len(listed.data), 1)

            invalid_detector = service.create_plan(
                admin,
                MaintenancePlanCommand(detector_id=999, plan_type="custom", due_at=due_at),
            )
            self.assertFalse(invalid_detector.success)
            self.assertEqual(invalid_detector.code, 400)

            updated = service.update_plan(
                admin,
                int(created.data.id),
                MaintenancePlanCommand(
                    detector_id=int(detector["id"]),
                    plan_type="custom",
                    due_at=due_at + timedelta(days=1),
                    remind_days_before=5,
                    status="completed",
                    notes="done",
                ),
            )
            self.assertTrue(updated.success, updated.message)
            self.assertEqual(updated.data.status, "completed")

            with UnitOfWork(database) as uow:
                denied_logs, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                create_logs, _ = OperationLogRepository(uow).list_for_action(action_type="maintenance.plan.create")
                update_logs, _ = OperationLogRepository(uow).list_for_action(action_type="maintenance.plan.update")
                self.assertGreaterEqual(len(denied_logs), 1)
                self.assertEqual(len(create_logs), 1)
                self.assertEqual(len(update_logs), 1)
                uow.commit()

    def test_due_reminders_include_detector_and_custom_plan_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin, _ = self._sessions(database)
            now = datetime.now(timezone.utc)
            detector = self._seed_detector(
                database,
                store,
                admin,
                sensor_life_until=(now + timedelta(days=5)).isoformat(),
                calibration_cycle_days=10,
            )
            service = MaintenanceService(database, store)
            custom = service.create_plan(
                admin,
                MaintenancePlanCommand(
                    detector_id=int(detector["id"]),
                    plan_type="custom",
                    due_at=now - timedelta(days=2),
                    remind_days_before=1,
                    notes="overdue custom work",
                ),
            )
            self.assertTrue(custom.success, custom.message)

            reminders = service.list_due_reminders(now=now + timedelta(days=4))
            by_source = {item.source: item for item in reminders}
            self.assertIn("detector.sensor_life", by_source)
            self.assertEqual(by_source["detector.sensor_life"].status, "due_soon")
            self.assertEqual(by_source["detector.sensor_life"].days_until_due, 1)
            self.assertIn("detector.calibration", by_source)
            self.assertEqual(by_source["detector.calibration"].status, "due_soon")
            self.assertIn("maintenance_plan", by_source)
            self.assertEqual(by_source["maintenance_plan"].status, "overdue")
            self.assertEqual(by_source["maintenance_plan"].plan_id, custom.data.id)
            self.assertEqual(by_source["maintenance_plan"].notes, "overdue custom work")

    def test_scheduled_trigger_only_publishes_reminder_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            store, admin, _ = self._sessions(database)
            detector = self._seed_detector(database, store, admin)
            event_bus = EventBus()
            service = MaintenanceService(database, store, event_bus=event_bus)
            created = service.create_plan(
                admin,
                MaintenancePlanCommand(
                    detector_id=int(detector["id"]),
                    plan_type="custom",
                    due_at=datetime.now(timezone.utc) - timedelta(days=1),
                    remind_days_before=0,
                ),
            )
            self.assertTrue(created.success, created.message)
            published: list[tuple[str, object]] = []
            event_bus.subscribe(MAINTENANCE_REMINDERS_DUE_EVENT, lambda event, payload: published.append((event, payload)))

            result = service.trigger_scheduled_reminders()
            self.assertTrue(result.success, result.message)
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0][0], MAINTENANCE_REMINDERS_DUE_EVENT)
            self.assertEqual(result.data[0].source, "maintenance_plan")

            with UnitOfWork(database) as uow:
                alarm_count = uow.execute("SELECT COUNT(*) AS total FROM alarm_records").fetchone()["total"]
                scheduled_logs, _ = OperationLogRepository(uow).list_for_action(action_type="maintenance.reminders_due")
                self.assertEqual(alarm_count, 0)
                self.assertEqual(len(scheduled_logs), 0)
                uow.commit()


if __name__ == "__main__":
    unittest.main()
