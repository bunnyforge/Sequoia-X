"""仪表盘汇总应来自 SQLite 真实记录。"""

import sqlite3
from contextlib import closing
from pathlib import Path

from sequoia_x.data.stats import collect_dashboard_stats


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
                ("000002", "2026-09-10", 20.0, 19.0, 21.0, 20.5, 0.0, 0.0),
            ],
        )
        conn.commit()


def test_collect_dashboard_stats_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    summary = collect_dashboard_stats(str(db_path))
    assert summary["synced_records"] == 3
    assert summary["latest_trade_date"] == "2026-09-11"
    assert summary["latest_symbol_count"] == 1
    assert summary["stale_symbols"] == 1
    assert summary["invalid_rows"] == 1
    assert summary["pending_alerts"] == 2
    assert summary["trend"][-1]["date"] == "2026-09-11"


def test_collect_dashboard_stats_missing_file(tmp_path: Path) -> None:
    summary = collect_dashboard_stats(str(tmp_path / "missing.db"))
    assert summary["synced_records"] == 0
    assert summary["latest_trade_date"] is None
    assert summary["trend"] == []


def test_list_data_alerts_identifies_stale_and_invalid(tmp_path: Path) -> None:
    from sequoia_x.data.stats import list_data_alerts

    db_path = tmp_path / "test.db"
    _seed(db_path)
    alerts = list_data_alerts(str(db_path))
    assert alerts["latest_trade_date"] == "2026-09-11"
    assert alerts["stale"][0]["symbol"] == "000002"
    assert "落后于市场最新交易日" in alerts["stale"][0]["reason"]
    assert alerts["invalid"][0]["symbol"] == "000002"
    assert alerts["invalid"][0]["reason"]
