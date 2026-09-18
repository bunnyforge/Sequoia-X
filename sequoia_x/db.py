"""SQLite helpers. Path comes from DB_PATH or data/sequoia_v2.db."""

from __future__ import annotations

import atexit
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

IntegrityError = sqlite3.IntegrityError

DEFAULT_DB_PATH = "data/sequoia_v2.db"

# Per-connection page cache in KiB (negative cache_size). Keep modest: many
# FastAPI worker threads may each hold a pooled connection.
_CACHE_SIZE_KIB = 16_384  # 16 MiB
_MMAP_SIZE = 256 * 1024 * 1024  # 256 MiB
_BUSY_TIMEOUT_MS = 30_000
_CONNECT_TIMEOUT_S = 30.0
_MAX_IDLE_PER_PATH = 8


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
        if self._raw.in_transaction:
            self._raw.execute("COMMIT")

    def rollback(self) -> None:
        if self._raw.in_transaction:
            self._raw.execute("ROLLBACK")

    def close(self) -> None:
        # Pooled connections are returned by the context manager, not closed here.
        self.commit()

    def cursor(self) -> sqlite3.Cursor:
        return self._raw.cursor()

    @property
    def raw(self) -> sqlite3.Connection:
        """Underlying sqlite3 connection (for pandas read_sql)."""
        return self._raw


class _ConnectionPool:
    """Exclusive checkout of sqlite3 connections; WAL-safe across threads.

    Connections are never used by two threads at once. check_same_thread is
    False so FastAPI's thread pool and sync workers can reuse idle conns.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: dict[str, list[sqlite3.Connection]] = {}

    def acquire(self, path: str) -> sqlite3.Connection:
        with self._lock:
            idle = self._idle.get(path)
            if idle:
                return idle.pop()
        return _open_connection(path)

    def release(self, path: str, raw: sqlite3.Connection) -> None:
        try:
            if raw.in_transaction:
                raw.execute("ROLLBACK")
        except sqlite3.Error:
            _close_quietly(raw)
            return
        with self._lock:
            idle = self._idle.setdefault(path, [])
            if len(idle) < _MAX_IDLE_PER_PATH:
                idle.append(raw)
                return
        _close_quietly(raw)

    def close_all(self) -> None:
        with self._lock:
            groups = list(self._idle.values())
            self._idle.clear()
        for group in groups:
            for raw in group:
                _close_quietly(raw)


_pool = _ConnectionPool()
atexit.register(_pool.close_all)


def _close_quietly(raw: sqlite3.Connection) -> None:
    try:
        raw.close()
    except sqlite3.Error:
        pass


def _open_connection(path: str) -> sqlite3.Connection:
    ensure_parent_dir(path)
    raw = sqlite3.connect(
        path,
        timeout=_CONNECT_TIMEOUT_S,
        isolation_level=None,
        check_same_thread=False,
    )
    _apply_pragmas(raw)
    return raw


def _apply_pragmas(raw: sqlite3.Connection) -> None:
    """Proven WAL settings for mixed readers + one writer.

    journal_mode=WAL lets readers proceed during a writer.
    synchronous=NORMAL is WAL-safe and avoids a full fsync per commit.
    busy_timeout retries on lock instead of raising immediately.
    """
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA synchronous=NORMAL")
    raw.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    raw.execute("PRAGMA temp_store=MEMORY")
    raw.execute(f"PRAGMA cache_size={-_CACHE_SIZE_KIB}")
    raw.execute(f"PRAGMA mmap_size={_MMAP_SIZE}")
    raw.execute("PRAGMA foreign_keys=ON")
    raw.execute("PRAGMA wal_autocheckpoint=1000")


def configure_connection(raw: sqlite3.Connection) -> None:
    """Apply the same pragmas used by pooled connections (tests / ad-hoc)."""
    _apply_pragmas(raw)


@contextmanager
def connect(
    db_path: str | None = None,
    *,
    immediate: bool = False,
) -> Iterator[CompatConnection]:
    """Checkout a pooled connection for one transaction.

    Readers keep the default DEFERRED begin so WAL snapshots do not take the
    reserved lock. Writers pass immediate=True (BEGIN IMMEDIATE) to avoid
    lock-upgrade races under concurrent readers.
    """
    path = resolve_db_path(db_path)
    raw = _pool.acquire(path)
    wrapped = CompatConnection(raw)
    began = False
    try:
        raw.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        began = True
        yield wrapped
        if raw.in_transaction:
            raw.execute("COMMIT")
    except Exception:
        if began and raw.in_transaction:
            try:
                raw.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        _pool.release(path, raw)


def close_pooled_connections() -> None:
    """Drop idle pooled connections (tests that delete the temp DB file)."""
    _pool.close_all()


def table_exists(conn: CompatConnection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def column_names(conn: CompatConnection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def ensure_hot_indexes(db_path: str | None = None) -> None:
    """Indexes for dashboard / sync hot paths. UNIQUE(symbol, date) already covers symbol lookups."""
    path = resolve_db_path(db_path)
    if not db_exists(path):
        return
    with connect(path, immediate=True) as conn:
        if table_exists(conn, "stock_daily"):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_stock_daily_date ON stock_daily (date)"
            )
            # Duplicate of UNIQUE(symbol, date); extra index only amplified writes.
            conn.execute("DROP INDEX IF EXISTS idx_symbol_date")
        if table_exists(conn, "stock_sync_state"):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_state_last_date "
                "ON stock_sync_state (last_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_state_failed "
                "ON stock_sync_state (failed_date) WHERE failed_date IS NOT NULL"
            )
