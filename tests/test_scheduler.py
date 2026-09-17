"""调度配置应被限制在安全区间并写入数据库。"""

from pathlib import Path

from sequoia_x.sync.scheduler import SyncScheduler, clamp_config
from sequoia_x.sync.store import load_config, load_jobs


def test_clamp_config_bounds() -> None:
    cfg = clamp_config(
        {
            "enabled": True,
            "interval_seconds": 1,
            "retry_seconds": 1,
            "max_retries": 99,
        }
    )
    assert cfg["enabled"] is True
    assert cfg["interval_seconds"] == 10
    assert cfg["retry_seconds"] == 5
    assert cfg["max_retries"] == 20
    assert cfg["concurrency"] == 8
    assert cfg["sleep_seconds"] == 0
    assert cfg["start_date"] == "2024-01-01"


def test_clamp_config_concurrency_and_sleep() -> None:
    cfg = clamp_config({"concurrency": 0, "sleep_seconds": -1})
    assert cfg["concurrency"] == 1
    assert cfg["sleep_seconds"] == 0
    cfg = clamp_config({"concurrency": 99, "sleep_seconds": 120})
    assert cfg["concurrency"] == 32
    assert cfg["sleep_seconds"] == 60


def test_scheduler_persists_config_in_db(tmp_path: Path) -> None:
    db_path = str(tmp_path / "x.db")
    first = SyncScheduler(db_path=db_path)
    first.update_config(
        {
            "enabled": True,
            "interval_seconds": 120,
            "retry_seconds": 15,
            "start_date": "2020-01-01",
            "concurrency": 4,
            "sleep_seconds": 2,
        }
    )
    stored = load_config(db_path)
    assert stored["enabled"] is True
    assert stored["interval_seconds"] == 120
    assert stored["retry_seconds"] == 15
    assert stored["concurrency"] == 4
    assert stored["sleep_seconds"] == 2
    assert stored["start_date"] == "2020-01-01"
    second = SyncScheduler(db_path=db_path)
    assert second.snapshot()["config"]["enabled"] is True
    assert second.snapshot()["config"]["interval_seconds"] == 120
    assert second.snapshot()["config"]["retry_seconds"] == 15
    assert second.snapshot()["config"]["concurrency"] == 4
    assert second.snapshot()["config"]["sleep_seconds"] == 2
    assert second.snapshot()["config"]["start_date"] == "2020-01-01"


def test_scheduler_keeps_progress_in_db(tmp_path: Path) -> None:
    db_path = str(tmp_path / "x.db")
    scheduler = SyncScheduler(db_path=db_path)
    scheduler._on_progress("incremental", 12, 100, "000001")
    scheduler._flush_job("incremental", force=True)
    jobs = {item["key"]: item for item in load_jobs(db_path)}
    progress = jobs["incremental"]
    assert progress["current"] == 12
    assert progress["total"] == 100
    assert progress["symbol"] == "000001"
    assert "12/100" in (progress["progress"] or "")
    snapshot = scheduler.snapshot()
    assert len(snapshot["jobs"]) == 3
    assert [item["key"] for item in snapshot["jobs"]] == ["schedule", "incremental", "full"]


def test_load_config_migrates_legacy_sync_config_schema(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    db_path = str(tmp_path / "legacy.db")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE sync_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_seconds INTEGER NOT NULL DEFAULT 300,
                retry_seconds INTEGER NOT NULL DEFAULT 30,
                max_retries INTEGER NOT NULL DEFAULT 3,
                start_date TEXT NOT NULL DEFAULT '2024-01-01',
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO sync_config (id, enabled, interval_seconds, start_date) VALUES (1, 1, 120, '2020-01-01')"
        )
        conn.commit()
    stored = load_config(db_path)
    assert stored["enabled"] is True
    assert stored["interval_seconds"] == 120
    assert stored["concurrency"] == 8
    assert stored["sleep_seconds"] == 0


def test_execute_cycle_passes_concurrency_and_sleep(tmp_path: Path, monkeypatch) -> None:
    seen: dict = {}

    def fake_run_market_sync(*_args, **kwargs):
        seen.update(kwargs)
        return {"written": 0, "targets": 0, "failed": [], "message": "ok"}

    monkeypatch.setattr("sequoia_x.sync.scheduler.run_market_sync", fake_run_market_sync)
    db_path = str(tmp_path / "x.db")
    scheduler = SyncScheduler(db_path=db_path)
    scheduler.update_config({"concurrency": 2, "sleep_seconds": 5})
    scheduler._execute_cycle("incremental", "incremental", "manual", dict(scheduler._config))
    assert seen["concurrency"] == 2
    assert seen["sleep_seconds"] == 5
