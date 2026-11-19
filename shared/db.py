"""psycopg Postgres client, tx() context, upsert helper."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Optional, Sequence

import psycopg
from psycopg.rows import dict_row

from .config import get


def _dsn() -> str:
    return get("DATABASE_URL", required=True)  # type: ignore[return-value]


def connect() -> psycopg.Connection:
    return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=False)


@contextmanager
def tx():
    """Transactional connection context. Commits on exit, rolls back on error."""
    conn = connect()
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.close()


def execute(sql: str, params: Sequence[Any] | None = None) -> None:
    with tx() as conn:
        conn.execute(sql, params or ())


def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with tx() as conn:
        cur = conn.execute(sql, params or ())
        return list(cur.fetchall())


def fetch_one(sql: str, params: Sequence[Any] | None = None) -> Optional[dict[str, Any]]:
    with tx() as conn:
        cur = conn.execute(sql, params or ())
        return cur.fetchone()


def upsert(
    table: str,
    row: dict[str, Any],
    *,
    conflict_cols: Iterable[str],
    update_cols: Optional[Iterable[str]] = None,
) -> dict[str, Any] | None:
    """ON CONFLICT upsert. Returns the resulting row."""
    cols = list(row.keys())
    vals = [row[c] for c in cols]
    placeholders = ", ".join(["%s"] * len(cols))
    conflict = ", ".join(conflict_cols)
    upd_cols = list(update_cols) if update_cols is not None else [c for c in cols if c not in conflict_cols]
    if upd_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in upd_cols)
        action = f"DO UPDATE SET {set_clause}"
    else:
        action = "DO NOTHING"
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) {action} RETURNING *"
    )
    with tx() as conn:
        cur = conn.execute(sql, vals)
        return cur.fetchone()
