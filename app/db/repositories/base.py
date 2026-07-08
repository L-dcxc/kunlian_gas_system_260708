from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.unit_of_work import UnitOfWork

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_PER_PAGE = 100


@dataclass(frozen=True)
class Pagination:
    page: int = 1
    per_page: int = 20

    @property
    def limit(self) -> int:
        return self.per_page

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


def validate_pagination(page: int = 1, per_page: int = 20) -> Pagination:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be greater than or equal to 1")
    if isinstance(per_page, bool) or not isinstance(per_page, int) or per_page < 1 or per_page > MAX_PER_PAGE:
        raise ValueError(f"per_page must be between 1 and {MAX_PER_PAGE}")
    return Pagination(page=page, per_page=per_page)


class BaseRepository:
    """Low-level repository helper bound to a UnitOfWork-owned connection.

    Subclasses receive the active sqlite connection from UnitOfWork. The class
    intentionally exposes only parameterized helpers; SQL identifiers such as
    table names and sort columns must be constants or pass whitelist checks.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        if connection is None:
            raise RuntimeError("repository requires an active UnitOfWork connection")
        self.connection = connection

    @classmethod
    def from_uow(cls, uow: "UnitOfWork") -> "BaseRepository":
        if uow.connection is None:
            raise RuntimeError("UnitOfWork is not active")
        return cls(uow.connection)

    def execute(self, sql: str, parameters: Sequence[object] | dict[str, object] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, parameters)

    def fetch_all(self, sql: str, parameters: Sequence[object] | dict[str, object] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, parameters).fetchall())

    def fetch_one(self, sql: str, parameters: Sequence[object] | dict[str, object] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, parameters).fetchone()

    def apply_pagination(self, sql: str, pagination: Pagination) -> tuple[str, tuple[int, int]]:
        return f"{sql} LIMIT ? OFFSET ?", (pagination.limit, pagination.offset)


class EntityRepository(BaseRepository):
    table_name: str = ""
    primary_key: str = "id"
    allowed_sort_columns: frozenset[str] = frozenset({"id"})
    default_sort: str = "id"

    def __init__(self, uow: "UnitOfWork") -> None:
        if uow.connection is None:
            raise RuntimeError("UnitOfWork is not active")
        super().__init__(uow.connection)
        validate_identifier(self.table_name, {self.table_name})
        validate_identifier(self.primary_key, {self.primary_key})

    def find_by_id(self, row_id: int) -> sqlite3.Row | None:
        _validate_positive_int(row_id, "row_id")
        return self.fetch_one(
            f"SELECT * FROM {self.table_name} WHERE {self.primary_key} = ?",
            (row_id,),
        )

    @classmethod
    def from_uow(cls, uow: "UnitOfWork") -> "EntityRepository":
        return cls(uow)

    def list_page(
        self,
        *,
        page: int = 1,
        per_page: int = 20,
        sort_by: str | None = None,
        sort_direction: str = "ASC",
        where_clause: str = "",
        parameters: Sequence[object] = (),
    ) -> tuple[list[sqlite3.Row], Pagination]:
        pagination = validate_pagination(page, per_page)
        safe_sort = sort_by or self.default_sort
        order_clause = order_by_clause(safe_sort, sort_direction, self.allowed_sort_columns)
        if where_clause:
            # where_clause is repository-authored only. User fields must be values in
            # parameters, never interpolated into the SQL fragment.
            sql = f"SELECT * FROM {self.table_name} WHERE {where_clause} {order_clause}"
        else:
            sql = f"SELECT * FROM {self.table_name} {order_clause}"
        paged_sql, page_params = self.apply_pagination(sql, pagination)
        return self.fetch_all(paged_sql, tuple(parameters) + page_params), pagination


def build_time_range_clause(
    column: str,
    start_time: str | None,
    end_time: str | None,
    allowed_columns: Iterable[str],
) -> tuple[str, list[object]]:
    validate_identifier(column, allowed_columns)
    clauses: list[str] = []
    params: list[object] = []
    if start_time is not None:
        _validate_iso_time(start_time)
        clauses.append(f"{column} >= ?")
        params.append(start_time)
    if end_time is not None:
        _validate_iso_time(end_time)
        clauses.append(f"{column} <= ?")
        params.append(end_time)
    return (" AND ".join(clauses), params)


def order_by_clause(column: str, direction: str, allowed_columns: Iterable[str]) -> str:
    safe_column = validate_identifier(column, allowed_columns)
    normalized_direction = direction.upper()
    if normalized_direction not in {"ASC", "DESC"}:
        raise ValueError("unsupported sort direction")
    return f"ORDER BY {safe_column} {normalized_direction}"


def validate_identifier(identifier: str, allowed: Iterable[str]) -> str:
    allowed_set = set(allowed)
    if identifier not in allowed_set or not IDENTIFIER_RE.match(identifier):
        raise ValueError("unsupported SQL identifier")
    return identifier


def _validate_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _validate_iso_time(value: str) -> None:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc
