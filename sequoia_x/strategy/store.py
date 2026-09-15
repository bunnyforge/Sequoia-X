"""策略开关和参数落在数据库（Postgres 或 SQLite）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sequoia_x.db import connect, ensure_parent_dir
from sequoia_x.strategy.catalog import CATALOG, default_params


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_strategy_tables(db_path: str) -> None:
    ensure_parent_dir(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_config (
                strategy_key TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                params_json TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_run (
                strategy_key TEXT PRIMARY KEY,
                at TEXT,
                ok INTEGER NOT NULL DEFAULT 0,
                running INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                picks_json TEXT,
                extra_json TEXT
            )
            """
        )
        now = _now()
        for item in CATALOG:
            conn.execute(
                """
                INSERT INTO strategy_config (strategy_key, enabled, params_json, updated_at)
                VALUES (?, 0, ?, ?)
                ON CONFLICT (strategy_key) DO NOTHING
                """,
                (item["key"], json.dumps(default_params(item["key"]), ensure_ascii=False), now),
            )


def load_strategy_configs(db_path: str) -> dict[str, dict]:
    ensure_strategy_tables(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT strategy_key, enabled, params_json FROM strategy_config"
        ).fetchall()
        runs = conn.execute(
            "SELECT strategy_key, at, ok, running, message, picks_json, extra_json FROM strategy_run"
        ).fetchall()
    run_map = {
        row[0]: {
            "last_run_at": row[1],
            "ok": bool(row[2]),
            "running": bool(row[3]),
            "message": row[4],
            "picks": json.loads(row[5] or "[]"),
            "extra": json.loads(row[6] or "{}"),
        }
        for row in runs
    }
    configs = {}
    for key, enabled, params_json in rows:
        try:
            params = json.loads(params_json)
        except json.JSONDecodeError:
            params = default_params(key)
        configs[key] = {
            "enabled": bool(enabled),
            "params": {**default_params(key), **params},
            "result": run_map.get(key)
            or {
                "last_run_at": None,
                "ok": False,
                "running": False,
                "message": None,
                "picks": [],
                "extra": {},
            },
        }
    return configs


def save_strategy_config(db_path: str, key: str, enabled: bool, params: dict) -> None:
    ensure_strategy_tables(db_path)
    merged = {**default_params(key), **params}
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_config (strategy_key, enabled, params_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                enabled = excluded.enabled,
                params_json = excluded.params_json,
                updated_at = excluded.updated_at
            """,
            (key, 1 if enabled else 0, json.dumps(merged, ensure_ascii=False), _now()),
        )


def save_strategy_run(db_path: str, key: str, fields: dict) -> None:
    ensure_strategy_tables(db_path)
    with connect(db_path) as conn:
        current = conn.execute(
            "SELECT at, ok, running, message, picks_json, extra_json FROM strategy_run WHERE strategy_key = ?",
            (key,),
        ).fetchone()
        at, ok, running, message, picks_json, extra_json = current or (None, 0, 0, None, "[]", "{}")
        if "at" in fields:
            at = fields["at"]
        if "ok" in fields:
            ok = 1 if fields["ok"] else 0
        if "running" in fields:
            running = 1 if fields["running"] else 0
        if "message" in fields:
            message = fields["message"]
        if "picks" in fields:
            picks_json = json.dumps(fields["picks"], ensure_ascii=False)
        if "extra" in fields:
            extra_json = json.dumps(fields["extra"], ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO strategy_run (strategy_key, at, ok, running, message, picks_json, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_key) DO UPDATE SET
                at = excluded.at,
                ok = excluded.ok,
                running = excluded.running,
                message = excluded.message,
                picks_json = excluded.picks_json,
                extra_json = excluded.extra_json
            """,
            (key, at, ok, running, message, picks_json, extra_json),
        )
