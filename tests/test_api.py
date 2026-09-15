"""仪表盘接口应返回 SQLite 中的真实汇总，而不是写死的演示值。"""

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app


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


def test_dashboard_summary_reads_sqlite(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    with TestClient(app) as client:
        summary = client.get("/api/dashboard/summary").json()
        jobs = client.get("/api/sync/jobs").json()["items"]

    assert summary["synced_records"] == 3
    assert summary["latest_trade_date"] == "2026-09-11"
    assert summary["latest_symbol_count"] == 1
    assert summary["stale_symbols"] == 1
    assert summary["invalid_rows"] == 1
    assert summary["pending_alerts"] == 2
    assert summary["trend"][-1]["date"] == "2026-09-11"
    assert jobs[0]["processed"] == 1
    assert jobs[1]["status"] == "warning"


def test_dashboard_summary_empty_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))

    with TestClient(app) as client:
        summary = client.get("/api/dashboard/summary").json()

    assert summary["synced_records"] == 0
    assert summary["latest_trade_date"] is None
    assert summary["trend"] == []
