from __future__ import annotations

import sqlite3
from types import TracebackType
from typing import Type

from app.db.connection import Database


class UnitOfWork:
    def __init__(self, database: Database) -> None:
        self._database = database
        self.connection: sqlite3.Connection | None = None
        self._committed = False

    def __enter__(self) -> "UnitOfWork":
        self.connection = self._database.connect()
        self.connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("UnitOfWork is not active")
        self.connection.execute("COMMIT")
        self._committed = True

    def rollback(self) -> None:
        if self.connection is None:
            return
        try:
            self.connection.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass

    def execute(self, sql: str, parameters: tuple[object, ...] | dict[str, object] = ()) -> sqlite3.Cursor:
        if self.connection is None:
            raise RuntimeError("UnitOfWork is not active")
        return self.connection.execute(sql, parameters)
