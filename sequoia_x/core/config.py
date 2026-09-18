"""应用配置：SQLite 路径来自环境（DB_PATH），业务配置来自数据库。"""

from __future__ import annotations

from dataclasses import dataclass

from sequoia_x.db import DEFAULT_DB_PATH, ensure_hot_indexes, resolve_db_path

DEFAULT_START_DATE = "2024-01-01"
DEFAULT_SQLITE_PATH = DEFAULT_DB_PATH
LEGACY_SYNC_JSON = "data/sync_scheduler.json"


@dataclass
class Settings:
    db_path: str = DEFAULT_SQLITE_PATH
    start_date: str = DEFAULT_START_DATE


_settings: Settings | None = None


def bootstrap_app(db_path: str | None = None) -> dict:
    """建表并写入空库默认业务配置；返回 sync_config（含 start_date）。"""
    from sequoia_x.strategy.store import ensure_strategy_tables
    from sequoia_x.sync.store import load_config, migrate_json_config

    target = db_path if db_path is not None else resolve_db_path()
    migrate_json_config(target, LEGACY_SYNC_JSON)
    cfg = load_config(target)
    ensure_strategy_tables(target)
    ensure_hot_indexes(target)
    return cfg


def get_settings() -> Settings:
    """返回全局 Settings：SQLite 路径 + 库内 start_date。"""
    global _settings
    if _settings is None:
        path = resolve_db_path()
        cfg = bootstrap_app(path)
        _settings = Settings(db_path=path, start_date=cfg["start_date"])
    return _settings


def reset_settings() -> None:
    """测试用：丢掉单例。"""
    global _settings
    _settings = None
