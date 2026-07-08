from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from app.db.repositories.base import EntityRepository


class UserRepository(EntityRepository):
    table_name = "users"
    allowed_sort_columns = frozenset({"id", "username", "role", "is_active", "created_at", "updated_at"})
    default_sort = "username"

    def count_not_deleted(self) -> int:
        row = self.fetch_one("SELECT COUNT(*) AS total FROM users WHERE deleted_at IS NULL")
        return int(row["total"] if row is not None else 0)

    def find_by_username(self, username: str):
        return self.fetch_one("SELECT * FROM users WHERE username = ?", (username,))

    def find_active_by_username(self, username: str):
        return self.fetch_one(
            "SELECT * FROM users WHERE username = ? AND deleted_at IS NULL",
            (username,),
        )

    def list_page(
        self,
        *,
        page: int = 1,
        per_page: int = 20,
        sort_by: str | None = None,
        sort_direction: str = "ASC",
        where_clause: str = "",
        parameters: Sequence[object] = (),
    ):
        rows, _pagination = super().list_page(
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_direction=sort_direction,
            where_clause=where_clause,
            parameters=parameters,
        )
        if where_clause:
            total_row = self.fetch_one(f"SELECT COUNT(*) AS total FROM users WHERE {where_clause}", tuple(parameters))
        else:
            total_row = self.fetch_one("SELECT COUNT(*) AS total FROM users")
        return rows, int(total_row["total"] if total_row is not None else 0)

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        password_salt: str,
        role: str,
        is_active: bool = True,
        must_change_password: bool = False,
    ) -> int:
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO users(
                username, password_hash, password_salt, role, is_active,
                must_change_password, permission_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                username,
                password_hash,
                password_salt,
                role,
                1 if is_active else 0,
                1 if must_change_password else 0,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update_user(
        self,
        user_id: int,
        *,
        username: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
        password_hash: str | None = None,
        password_salt: str | None = None,
        must_change_password: bool | None = None,
        increment_permission_version: bool = False,
    ) -> None:
        assignments: list[str] = ["updated_at = ?"]
        parameters: list[object] = [_now()]
        if username is not None:
            assignments.append("username = ?")
            parameters.append(username)
        if role is not None:
            assignments.append("role = ?")
            parameters.append(role)
        if is_active is not None:
            assignments.append("is_active = ?")
            parameters.append(1 if is_active else 0)
        if password_hash is not None:
            assignments.append("password_hash = ?")
            parameters.append(password_hash)
        if password_salt is not None:
            assignments.append("password_salt = ?")
            parameters.append(password_salt)
        if must_change_password is not None:
            assignments.append("must_change_password = ?")
            parameters.append(1 if must_change_password else 0)
        if increment_permission_version:
            assignments.append("permission_version = permission_version + 1")
        parameters.append(user_id)
        self.execute(
            f"UPDATE users SET {', '.join(assignments)} WHERE id = ? AND deleted_at IS NULL",
            tuple(parameters),
        )

    def soft_delete(self, user_id: int) -> None:
        now = _now()
        self.execute(
            """
            UPDATE users
            SET deleted_at = ?, is_active = 0, permission_version = permission_version + 1, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, user_id),
        )

    def count_active_admins(self) -> int:
        row = self.fetch_one(
            """
            SELECT COUNT(*) AS total
            FROM users
            WHERE role = 'admin' AND is_active = 1 AND deleted_at IS NULL
            """
        )
        return int(row["total"] if row is not None else 0)

    def count_admins_excluding(self, user_id: int) -> int:
        row = self.fetch_one(
            """
            SELECT COUNT(*) AS total
            FROM users
            WHERE role = 'admin' AND is_active = 1 AND deleted_at IS NULL AND id <> ?
            """,
            (user_id,),
        )
        return int(row["total"] if row is not None else 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
