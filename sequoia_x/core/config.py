"""应用配置：连接串来自环境（Compose 的 DATABASE_URL），业务配置来自数据库。

换机器只拷 Postgres 数据即可；不需要 .env。
本地 `python main.py`：有 DATABASE_URL 则连 Postgres，否则 SQLite。
"""

from __future__ import annotations

from dataclasses import dataclass

from sequoia_x.db import resolve_dsn

DEFAULT_START_DATE = "2024-01-01"
DEFAULT_SQLITE_PATH = "data/sequoia_v2.db"
LEGACY_SYNC_JSON = "data/sync_scheduler.json"


@dataclass
class Settings:
    db_path: str = DEFAULT_SQLITE_PATH
    start_date: str = DEFAULT_START_DATE


_settings: Settings | None = None


def bootstrap_app(dsn: str | None = None) -> dict:
    """建表并写入空库默认业务配置；返回 sync_config（含 start_date）。"""
    from sequoia_x.strategy.store import ensure_strategy_tables
    from sequoia_x.sync.store import load_config, migrate_json_config

    target = dsn if dsn is not None else resolve_dsn()
    migrate_json_config(target, LEGACY_SYNC_JSON)
    cfg = load_config(target)
    ensure_strategy_tables(target)
    return cfg


def get_settings() -> Settings:
    """返回全局 Settings：连接 DSN + 库内 start_date。"""
    global _settings
    if _settings is None:
        dsn = resolve_dsn()
        cfg = bootstrap_app(dsn)
        _settings = Settings(db_path=dsn, start_date=cfg["start_date"])
    return _settings


def reset_settings() -> None:
    """测试用：丢掉单例。"""
    global _settings
    _settings = None
