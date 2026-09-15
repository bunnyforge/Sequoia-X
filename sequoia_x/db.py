"""Database helpers: Postgres via DATABASE_URL, with optional SQLite fallback.

Prefer DATABASE_URL (postgresql://...). When unset, db_path is treated as a
SQLite file path for local/dev/tests.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

_PLACEHOLDER = object()


def resolve_dsn(db_path: str | None = None) -> str:
    """Return the active DSN: DATABASE_URL wins, else db_path / DB_PATH."""
    env = (os.environ.get("DATABASE_URL") or "").strip()
    if env:
        return env
    if db_path and str(db_path).strip():
        return str(db_path).strip()
    return (os.environ.get("DB_PATH") or "data/sequoia_v2.db").strip()


def is_postgres(dsn: str | None = None) -> bool:
    target = dsn if dsn is not None else resolve_dsn()
    return target.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))


def ensure_parent_dir(dsn: str) -> None:
    """Create parent directory for SQLite paths; no-op for Postgres URLs."""
    if is_postgres(dsn):
        return
    Path(dsn).parent.mkdir(parents=True, exist_ok=True)


def db_exists(dsn: str) -> bool:
    """True if SQLite file exists, or if Postgres DSN is configured."""
    if is_postgres(dsn):
        return True
    return Path(dsn).exists()


def _to_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    if dsn.startswith("postgres://"):
        return "postgresql://" + dsn[len("postgres://") :]
    return dsn


def adapt_sql(sql: str, *, postgres: bool) -> str:
    """Translate SQLite-flavored SQL fragments to Postgres when needed."""
    if not postgres:
        return sql
    # positional placeholders
    out = sql.replace("?", "%s")
    # SQLite two-arg MAX → GREATEST (common in upserts)
    out = out.replace("MAX(", "GREATEST(").replace("MIN(", "LEAST(")
    # Undo accidental rewrite of aggregate MAX/MIN in SELECT … FROM contexts
    # where GREATEST/LEAST would be wrong: restore COUNT-safe aggregates by
    # only applying GREATEST/LEAST for known two-arg upsert patterns.
    # Safer: only replace the known upsert idioms below in call sites.
    # Revert blanket MAX/MIN — too aggressive for SELECT MAX(date).
    out = sql.replace("?", "%s")
    return out


def pg_upsert_greatest(sql: str) -> str:
    """Convert SQLite MAX(a,b)/MIN(a,b) in upsert SET clauses to PG GREATEST/LEAST."""
    return (
        sql.replace("MAX(stock_sync_state.last_date, excluded.last_date)",
                    "GREATEST(stock_sync_state.last_date, excluded.last_date)")
        .replace("MIN(stock_sync_state.first_date, excluded.first_date)",
                 "LEAST(stock_sync_state.first_date, excluded.first_date)")
    )


class _CompatCursor:
    """Thin wrapper so fetchone/fetchall/row[0] work for both drivers."""

    def __init__(self, cursor: Any, *, postgres: bool) -> None:
        self._cursor = cursor
        self._postgres = postgres

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list:
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class CompatConnection:
    """DB-API connection with `?` placeholders and commit/close."""

    def __init__(self, raw: Any, *, postgres: bool) -> None:
        self._raw = raw
        self.postgres = postgres

    def execute(self, sql: str, params: Any = ()) -> _CompatCursor:
        statement = adapt_sql(sql, postgres=self.postgres)
        if self.postgres:
            statement = pg_upsert_greatest(statement)
            cur = self._raw.execute(statement, params or None)
        else:
            cur = self._raw.execute(statement, params)
        return _CompatCursor(cur, postgres=self.postgres)

    def executemany(self, sql: str, seq_of_params: Any) -> _CompatCursor:
        statement = adapt_sql(sql, postgres=self.postgres)
        if self.postgres:
            statement = pg_upsert_greatest(statement)
            cur = self._raw.executemany(statement, seq_of_params)
        else:
            cur = self._raw.executemany(statement, seq_of_params)
        return _CompatCursor(cur, postgres=self.postgres)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def cursor(self) -> Any:
        return self._raw.cursor()

    @property
    def raw(self) -> Any:
        """Underlying sqlite3/psycopg connection (for pandas read_sql)."""
        return self._raw


@contextmanager
def connect(db_path: str | None = None) -> Iterator[CompatConnection]:
    """Open a connection. Uses DATABASE_URL when set, else SQLite at db_path."""
    dsn = resolve_dsn(db_path)
    if is_postgres(dsn):
        import psycopg

        url = _to_psycopg_url(dsn)
        # CNPG app URIs may include sslmode; psycopg accepts them in the URL.
        raw = psycopg.connect(url)
        try:
            # Match sqlite row access style (tuple rows are default).
            yield CompatConnection(raw, postgres=True)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()
    else:
        ensure_parent_dir(dsn)
        raw = sqlite3.connect(dsn, timeout=30)
        try:
            yield CompatConnection(raw, postgres=False)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()


def table_exists(conn: CompatConnection, name: str) -> bool:
    if conn.postgres:
        row = conn.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (name,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def column_names(conn: CompatConnection, table: str) -> set[str]:
    if conn.postgres:
        rows = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def serial_pk(conn: CompatConnection) -> str:
    """Primary key column type for auto-increment ids."""
    if conn.postgres:
        return "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
    return "INTEGER PRIMARY KEY AUTOINCREMENT"


def insert_or_ignore(conn: CompatConnection) -> str:
    return "ON CONFLICT DO NOTHING" if conn.postgres else ""


# IntegrityError alias for callers that catch insert races
try:
    import psycopg

    IntegrityError = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)
except Exception:  # pragma: no cover
    IntegrityError = (sqlite3.IntegrityError,)
