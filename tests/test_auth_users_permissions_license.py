from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config.defaults import DatabaseConfig
from app.db.connection import Database
from app.db.repositories.license_repository import LicenseRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import AuthService, SessionStore, hash_password
from app.services.license_service import LicenseService
from app.services.user_service import CreateUserCommand, UserService


class AuthUsersPermissionsLicenseTests(unittest.TestCase):
    def _database(self, temp_dir: str) -> Database:
        database = Database(Path(temp_dir) / "app.sqlite3", DatabaseConfig(filename="app.sqlite3"))
        database.initialize()
        return database

    def _seed_user(self, database: Database, username: str, password: str, role: str, active: bool = True) -> int:
        password_hash, password_salt = hash_password(password)
        with UnitOfWork(database) as uow:
            user_id = UserRepository(uow).create_user(
                username=username,
                password_hash=password_hash,
                password_salt=password_salt,
                role=role,
                is_active=active,
            )
            uow.commit()
        return user_id

    def test_license_activation_tamper_invalid_code_and_log_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            service = LicenseService(
                database,
                activation_signing_key=b"test-signing-key",
                machine_fingerprint_provider=lambda: "machine-secret-material",
            )
            invalid = service.activate("bad-secret-code authorization_code=SHOULD_NOT_LOG")
            self.assertFalse(invalid.success)
            self.assertEqual(service.get_license_status().status, "unlicensed")

            with UnitOfWork(database) as uow:
                rows, _ = OperationLogRepository(uow).list_for_action(action_type="license.activate")
                text = " ".join(str(row["summary"]) + str(row["details_json"]) for row in rows)
                self.assertNotIn("SHOULD_NOT_LOG", text)
                self.assertNotIn("machine-secret-material", text)
                uow.commit()

            code = service.build_authorization_code({"machine_fingerprint_hash": service.machine_fingerprint_hash()})
            activated = service.activate(code)
            self.assertTrue(activated.success)
            self.assertTrue(activated.data.can_enter_main_system)

            with UnitOfWork(database) as uow:
                row = LicenseRepository(uow).get_current()
                self.assertEqual(row["status"], "active")
                self.assertNotIn("machine-secret-material", row["machine_fingerprint_hash"])
                uow.execute("UPDATE license_info SET integrity_signature = ? WHERE id = 1", ("tampered",))
                uow.commit()
            self.assertEqual(service.get_license_status().status, "invalid")

    def test_password_hash_login_failure_message_and_password_change_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            self._seed_user(database, "admin", "OldPass123", "admin")
            auth = AuthService(database)

            wrong_user = auth.login("missing", "OldPass123")
            wrong_password = auth.login("admin", "WrongPass123")
            self.assertFalse(wrong_user.success)
            self.assertFalse(wrong_password.success)
            self.assertEqual(wrong_user.message, "用户名或密码错误")
            self.assertEqual(wrong_password.message, "用户名或密码错误")

            logged_in = auth.login("admin", "OldPass123")
            self.assertTrue(logged_in.success)
            changed = auth.change_password(logged_in.data, "OldPass123", "NewPass123")
            self.assertTrue(changed.success)
            self.assertFalse(auth.login("admin", "OldPass123").success)
            self.assertTrue(auth.login("admin", "NewPass123").success)

            with UnitOfWork(database) as uow:
                user = UserRepository(uow).find_active_by_username("admin")
                self.assertNotEqual(user["password_hash"], "NewPass123")
                rows, _ = OperationLogRepository(uow).list_for_action(action_type="password.change")
                details = json.loads(rows[0]["details_json"])
                self.assertNotIn("NewPass123", str(details))
                self.assertNotIn("OldPass123", str(details))
                uow.commit()

    def test_user_admin_uniqueness_unique_admin_protection_and_user_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            self._seed_user(database, "admin", "AdminPass123", "admin")
            session_store = SessionStore()
            auth = AuthService(database, session_store)
            user_service = UserService(database, session_store)
            admin_session = auth.login("admin", "AdminPass123").data

            second_admin = user_service.create_user(
                admin_session,
                CreateUserCommand(username="admin2", password="AdminPass456", role="admin"),
            )
            self.assertFalse(second_admin.success)
            self.assertIn("管理员", second_admin.message)

            disable_only_admin = user_service.disable_user(admin_session, admin_session.user_id)
            self.assertFalse(disable_only_admin.success)
            self.assertIn("唯一管理员", disable_only_admin.message)

            operator = user_service.create_user(
                admin_session,
                CreateUserCommand(username="operator1", password="Operator123", role="operator"),
            )
            self.assertTrue(operator.success)
            with UnitOfWork(database) as uow:
                rows, _ = OperationLogRepository(uow).list_for_action(action_type="users.create")
                self.assertGreaterEqual(len(rows), 1)
                uow.commit()

    def test_operator_permission_failure_audited_and_session_version_invalidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(temp_dir)
            self._seed_user(database, "admin", "AdminPass123", "admin")
            operator_id = self._seed_user(database, "operator1", "Operator123", "operator")
            session_store = SessionStore()
            auth = AuthService(database, session_store)
            user_service = UserService(database, session_store)
            admin_session = auth.login("admin", "AdminPass123").data
            operator_session = auth.login("operator1", "Operator123").data

            denied = user_service.create_user(
                operator_session,
                CreateUserCommand(username="operator2", password="Operator456", role="operator"),
            )
            self.assertFalse(denied.success)

            with UnitOfWork(database) as uow:
                denied_rows, _ = OperationLogRepository(uow).list_for_action(action_type="permission_denied")
                self.assertEqual(denied_rows[0]["result"], "denied")
                uow.commit()

            disabled = user_service.disable_user(admin_session, operator_id)
            self.assertTrue(disabled.success)
            with self.assertRaises(PermissionError):
                session_store.validate(database, operator_session)


if __name__ == "__main__":
    unittest.main()
