"""SQLite pragmas, pooling, and WAL concurrent readers."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from sequoia_x.db import close_pooled_connections, connect, ensure_hot_indexes
from sequoia_x.sync.lite_sync import _upsert, ensure_sync_state


def test_connect_applies_wal_and_busy_timeout(tmp_path: Path) -> None:
    db_path = str(tmp_path / "pragmas.db")
    with connect(db_path, immediate=True) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        temp_store = conn.execute("PRAGMA temp_store").fetchone()[0]
    close_pooled_connections()
    assert str(journal).lower() == "wal"
    assert int(synchronous) == 1  # NORMAL
    assert int(timeout) == 30_000
    assert int(temp_store) == 2  # MEMORY


def test_readers_proceed_while_writer_holds_immediate_txn(tmp_path: Path) -> None:
    db_path = str(tmp_path / "wal.db")
    with connect(db_path, immediate=True) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (id, v) VALUES (1, 'before')")

    hold = threading.Event()
    writing = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            with connect(db_path, immediate=True) as conn:
                conn.execute("UPDATE t SET v = 'inside' WHERE id = 1")
                writing.set()
                hold.wait(timeout=5)
                conn.execute("INSERT INTO t (id, v) VALUES (2, 'after')")
        except BaseException as exc:  # noqa: BLE001 — collect for the main thread
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    assert writing.wait(timeout=3)
    t0 = time.monotonic()
    with connect(db_path) as conn:
        value = conn.execute("SELECT v FROM t WHERE id = 1").fetchone()[0]
    elapsed = time.monotonic() - t0
    hold.set()
    thread.join(timeout=5)
    close_pooled_connections()
    assert errors == []
    assert value == "before"
    assert elapsed < 1.0


def test_hot_indexes_cover_date_and_drop_redundant_symbol_index(tmp_path: Path) -> None:
    db_path = str(tmp_path / "idx.db")
    with connect(db_path, immediate=True) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                UNIQUE (symbol, date)
            )
            """
        )
        conn.execute("CREATE INDEX idx_symbol_date ON stock_daily (symbol, date)")
        conn.execute(
            """
            CREATE TABLE stock_sync_state (
                symbol TEXT PRIMARY KEY,
                last_date TEXT NOT NULL,
                failed_date TEXT
            )
            """
        )
    ensure_hot_indexes(db_path)
    with connect(db_path) as conn:
        names = {
            row[1]
            for row in conn.execute(
                "SELECT * FROM pragma_index_list('stock_daily')"
            )
        }
        sync_names = {
            row[1]
            for row in conn.execute(
                "SELECT * FROM pragma_index_list('stock_sync_state')"
            )
        }
    close_pooled_connections()
    assert "idx_stock_daily_date" in names
    assert "idx_symbol_date" not in names
    assert "idx_sync_state_last_date" in sync_names


def test_upsert_writes_bars_and_clears_failure(tmp_path: Path) -> None:
    db_path = str(tmp_path / "up.db")
    with connect(db_path, immediate=True) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, turnover REAL,
                UNIQUE (symbol, date)
            )
            """
        )
    ensure_sync_state(db_path)
    rows = [
        ("000001", "2026-09-10", 10.0, 11.0, 9.0, 10.5, 1000.0, 10500.0),
        ("000001", "2026-09-11", 10.5, 11.5, 10.0, 11.0, 1200.0, 13200.0),
    ]
    written = _upsert(
        db_path, rows, symbol="000001", failed_date=None, failed_reason=None
    )
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        fail = conn.execute(
            "SELECT failed_date FROM stock_sync_state WHERE symbol='000001'"
        ).fetchone()[0]
    close_pooled_connections()
    assert written == 2
    assert count == 2
    assert fail is None
