from __future__ import annotations

import sqlite3


def apply(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
    if "must_change_password" not in columns:
        connection.execute(
            """
            ALTER TABLE users
            ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0 CHECK(must_change_password IN (0, 1))
            """
        )
