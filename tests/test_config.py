"""配置：连接走 DB_PATH，业务项（如 start_date）走数据库。"""

from pathlib import Path

from hypothesis import HealthCheck, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings, get_settings, reset_settings
from sequoia_x.db import resolve_db_path
from sequoia_x.sync.store import load_config, migrate_json_config, save_config


@given(
    db_path=st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"),
            whitelist_characters="/_.-",
        ),
    )
)
@h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_db_path_env_overrides_default(db_path: str, monkeypatch) -> None:
    """DB_PATH 决定 SQLite 路径。"""
    monkeypatch.setenv("DB_PATH", db_path)
    assert resolve_db_path() == db_path


def test_settings_defaults_without_env_file() -> None:
    s = Settings()
    assert s.db_path == "data/sequoia_v2.db"
    assert s.start_date == "2024-01-01"


def test_get_settings_reads_start_date_from_db(tmp_path: Path, monkeypatch) -> None:
    db_path = str(tmp_path / "cfg.db")
    monkeypatch.setenv("DB_PATH", db_path)
    reset_settings()
    save_config(db_path, {"start_date": "2020-06-01", "enabled": False})
    s = get_settings()
    assert s.db_path == db_path
    assert s.start_date == "2020-06-01"
    reset_settings()


def test_empty_db_seeds_default_start_date(tmp_path: Path, monkeypatch) -> None:
    db_path = str(tmp_path / "empty.db")
    monkeypatch.setenv("DB_PATH", db_path)
    reset_settings()
    s = get_settings()
    assert s.start_date == "2024-01-01"
    assert load_config(db_path)["start_date"] == "2024-01-01"
    reset_settings()


def test_legacy_json_migrates_into_db(tmp_path: Path) -> None:
    db_path = str(tmp_path / "m.db")
    json_path = tmp_path / "sync_scheduler.json"
    json_path.write_text(
        '{"enabled": true, "interval_seconds": 120, "start_date": "2019-03-01"}',
        encoding="utf-8",
    )
    migrate_json_config(db_path, str(json_path))
    cfg = load_config(db_path)
    assert cfg["enabled"] is True
    assert cfg["interval_seconds"] == 120
    assert cfg["start_date"] == "2019-03-01"
    assert cfg["concurrency"] == 8
    assert cfg["sleep_seconds"] == 0
    assert not json_path.exists()
    assert json_path.with_name("sync_scheduler.json.migrated").exists()
