"""增量同步只应处理缺失日 K 的股票。"""

import sqlite3
from contextlib import closing
from pathlib import Path

from sequoia_x.sync.lite_sync import (
    apply_universe,
    build_full_tasks,
    build_missing_tasks,
    ensure_sync_state,
    take_consecutive_bars,
)


def _seed(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                turnover REAL,
                UNIQUE (symbol, date)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO stock_daily
                (symbol, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "2026-09-10", 10.0, 11.0, 9.0, 10.5, 1000.0, 10500.0),
                ("000001", "2026-09-11", 10.5, 11.5, 10.0, 11.0, 1200.0, 13200.0),
                ("000002", "2026-09-10", 20.0, 21.0, 19.0, 20.5, 2000.0, 41000.0),
                ("000003", "2026-09-11", 8.0, 8.5, 7.9, 8.2, 800.0, 6500.0),
            ],
        )
        conn.commit()


def test_build_missing_tasks_skips_caught_up_symbols(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    ensure_sync_state(str(db_path))
    tasks = build_missing_tasks(str(db_path), "2026-09-11")
    symbols = [item[0] for item in tasks]
    assert symbols == ["000002"]
    assert tasks[0][1] == "2026-09-11"


def test_build_missing_tasks_empty_when_aligned(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    tasks = build_missing_tasks(str(db_path), "2026-09-10")
    assert tasks == []


def test_apply_universe_adds_and_removes(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    ensure_sync_state(str(db_path))
    result = apply_universe(str(db_path), {"000001", "000004"}, "2024-01-01")
    assert result["added"] == 1
    assert result["removed"] == 2
    with closing(sqlite3.connect(db_path)) as conn:
        symbols = {row[0] for row in conn.execute("SELECT symbol FROM stock_sync_state")}
        daily = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM stock_daily")}
        last_date = conn.execute(
            "SELECT last_date FROM stock_sync_state WHERE symbol='000004'"
        ).fetchone()[0]
    assert symbols == {"000001", "000004"}
    assert daily == {"000001"}
    assert last_date == "2023-12-31"


def test_full_tasks_backfill_from_start_date(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    ensure_sync_state(str(db_path))
    tasks = {symbol: start for symbol, start, _end in build_full_tasks(str(db_path), "2024-01-01", "2026-09-11")}
    assert tasks["000001"] == "2024-01-01"
    assert tasks["000002"] == "2024-01-01"
    complete = {symbol: start for symbol, start, _end in build_full_tasks(str(db_path), "2026-09-10", "2026-09-11")}
    assert "000001" not in complete
    assert complete["000002"] == "2026-09-11"


def test_take_consecutive_bars_stops_at_first_missing_day() -> None:
    rows = [
        ("000001", "2026-09-10", 10.0, 11.0, 9.0, 10.5, 1000.0, 10500.0),
        ("000001", "2026-09-12", 11.0, 12.0, 10.0, 11.5, 1100.0, 12000.0),
    ]
    kept, gap = take_consecutive_bars(
        rows,
        "2026-09-10",
        "2026-09-12",
        ["2026-09-10", "2026-09-11", "2026-09-12"],
    )
    assert gap == "2026-09-11"
    assert [item[1] for item in kept] == ["2026-09-10"]


def test_failed_symbol_retries_only_failed_day(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    ensure_sync_state(str(db_path))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE stock_sync_state
            SET failed_date = '2026-09-11', failed_reason = '缺K'
            WHERE symbol = '000002'
            """
        )
        conn.commit()
    tasks = {symbol: (start, end) for symbol, start, end in build_missing_tasks(str(db_path), "2026-09-14")}
    assert tasks["000002"] == ("2026-09-11", "2026-09-11")
