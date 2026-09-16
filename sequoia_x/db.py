"""SQLite helpers. Path comes from DB_PATH or data/sequoia_v2.db."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

IntegrityError = sqlite3.IntegrityError

DEFAULT_DB_PATH = "data/sequoia_v2.db"


def resolve_db_path(db_path: str | None = None) -> str:
    """Return the SQLite file path: explicit arg, else DB_PATH, else default."""
    if db_path and str(db_path).strip():
        return str(db_path).strip()
    return (os.environ.get("DB_PATH") or DEFAULT_DB_PATH).strip()


def ensure_parent_dir(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def db_exists(db_path: str) -> bool:
    return Path(db_path).exists()


class _Cursor:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list:
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class CompatConnection:
    """sqlite3 connection with execute/executemany/commit/close."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        return _Cursor(self._raw.execute(sql, params))

    def executemany(self, sql: str, seq_of_params: Any) -> _Cursor:
        return _Cursor(self._raw.executemany(sql, seq_of_params))

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def cursor(self) -> sqlite3.Cursor:
        return self._raw.cursor()

    @property
    def raw(self) -> sqlite3.Connection:
        """Underlying sqlite3 connection (for pandas read_sql)."""
        return self._raw


@contextmanager
def connect(db_path: str | None = None) -> Iterator[CompatConnection]:
    path = resolve_db_path(db_path)
    ensure_parent_dir(path)
    raw = sqlite3.connect(path, timeout=30)
    try:
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA busy_timeout=30000")
        yield CompatConnection(raw)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def table_exists(conn: CompatConnection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def column_names(conn: CompatConnection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}
