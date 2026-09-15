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
    assert cfg["start_date"] == "2024-01-01"


def test_scheduler_persists_config_in_db(tmp_path: Path) -> None:
    db_path = str(tmp_path / "x.db")
    first = SyncScheduler(db_path=db_path)
    first.update_config(
        {
            "enabled": True,
            "interval_seconds": 120,
            "retry_seconds": 15,
            "start_date": "2020-01-01",
        }
    )
    stored = load_config(db_path)
    assert stored["enabled"] is True
    assert stored["interval_seconds"] == 120
    assert stored["retry_seconds"] == 15
    assert stored["start_date"] == "2020-01-01"
    second = SyncScheduler(db_path=db_path)
    assert second.snapshot()["config"]["enabled"] is True
    assert second.snapshot()["config"]["interval_seconds"] == 120
    assert second.snapshot()["config"]["retry_seconds"] == 15
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
