"""同步配置、进度和运行记录落在 SQLite。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 86400
MIN_RETRY_SECONDS = 5
MAX_RETRY_SECONDS = 3600
MAX_RETRY_COUNT = 20


def clamp_config(raw: dict) -> dict:
    interval = int(raw.get("interval_seconds", 300))
    retry = int(raw.get("retry_seconds", 30))
    max_retries = int(raw.get("max_retries", 3))
    start_date = str(raw.get("start_date") or "2024-01-01").strip()
    try:
        parsed = date.fromisoformat(start_date)
    except ValueError:
        parsed = date(2024, 1, 1)
    if parsed.year < 1990:
        parsed = date(1990, 1, 1)
    return {
        "enabled": bool(raw.get("enabled", False)),
        "interval_seconds": max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, interval)),
        "retry_seconds": max(MIN_RETRY_SECONDS, min(MAX_RETRY_SECONDS, retry)),
        "max_retries": max(0, min(MAX_RETRY_COUNT, max_retries)),
        "start_date": parsed.strftime("%Y-%m-%d"),
    }


JOB_DEFS = (
    ("schedule", "定时增量"),
    ("incremental", "立即增量"),
    ("full", "全量同步"),
)


def _empty_job(job_key: str) -> dict:
    label = dict(JOB_DEFS).get(job_key, job_key)
    return {
        "key": job_key,
        "label": label,
        "state": "idle",
        "running": False,
        "mode": "full" if job_key == "full" else "incremental",
        "attempt": 0,
        "current": 0,
        "total": 0,
        "symbol": None,
        "progress": None,
        "last_run_at": None,
        "next_run_at": None,
        "last_error": None,
        "last_message": None,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_control_tables(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_config (
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
            """
            CREATE TABLE IF NOT EXISTS sync_progress (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT,
                running INTEGER NOT NULL DEFAULT 0,
                mode TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                current INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                symbol TEXT,
                message TEXT,
                last_run_at TEXT,
                next_run_at TEXT,
                last_error TEXT,
                last_message TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_run (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                ok INTEGER NOT NULL,
                triggered TEXT,
                message TEXT,
                error TEXT,
                written INTEGER NOT NULL DEFAULT 0,
                targets INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_job (
                job_key TEXT PRIMARY KEY,
                state TEXT,
                running INTEGER NOT NULL DEFAULT 0,
                mode TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                current INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                symbol TEXT,
                message TEXT,
                last_run_at TEXT,
                next_run_at TEXT,
                last_error TEXT,
                last_message TEXT,
                updated_at TEXT
            )
            """
        )
        now = _now()
        for job_key, _label in JOB_DEFS:
            conn.execute(
                "INSERT OR IGNORE INTO sync_job (job_key, state, mode, updated_at) VALUES (?, 'idle', ?, ?)",
                (job_key, "full" if job_key == "full" else "incremental", now),
            )
        conn.execute(
            "INSERT OR IGNORE INTO sync_config (id, updated_at) VALUES (1, ?)",
            (now,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO sync_progress (id, state, updated_at) VALUES (1, 'idle', ?)",
            (now,),
        )
        conn.commit()


def migrate_json_config(db_path: str, config_path: str) -> None:
    path = Path(config_path)
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    save_config(db_path, clamp_config(raw))
    migrated = path.with_name(path.name + ".migrated")
    try:
        path.replace(migrated)
    except OSError:
        pass


def load_config(db_path: str) -> dict:
    ensure_control_tables(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        row = conn.execute(
            """
            SELECT enabled, interval_seconds, retry_seconds, max_retries, start_date
            FROM sync_config WHERE id = 1
            """
        ).fetchone()
    if not row:
        return clamp_config({})
    return clamp_config(
        {
            "enabled": bool(row[0]),
            "interval_seconds": row[1],
            "retry_seconds": row[2],
            "max_retries": row[3],
            "start_date": row[4],
        }
    )


def save_config(db_path: str, config: dict) -> None:
    ensure_control_tables(db_path)
    cfg = clamp_config(config)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute(
            """
            INSERT INTO sync_config (
                id, enabled, interval_seconds, retry_seconds, max_retries, start_date, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                interval_seconds = excluded.interval_seconds,
                retry_seconds = excluded.retry_seconds,
                max_retries = excluded.max_retries,
                start_date = excluded.start_date,
                updated_at = excluded.updated_at
            """,
            (
                1 if cfg["enabled"] else 0,
                cfg["interval_seconds"],
                cfg["retry_seconds"],
                cfg["max_retries"],
                cfg["start_date"],
                _now(),
            ),
        )
        conn.commit()


def load_jobs(db_path: str) -> list[dict]:
    ensure_control_tables(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        rows = conn.execute(
            """
            SELECT job_key, state, running, mode, attempt, current, total, symbol, message,
                   last_run_at, next_run_at, last_error, last_message
            FROM sync_job
            """
        ).fetchall()
    by_key = {row[0]: row for row in rows}
    jobs = []
    for job_key, _label in JOB_DEFS:
        row = by_key.get(job_key)
        job = _empty_job(job_key)
        if row:
            job.update(
                {
                    "state": row[1] or "idle",
                    "running": bool(row[2]),
                    "mode": row[3] or job["mode"],
                    "attempt": int(row[4] or 0),
                    "current": int(row[5] or 0),
                    "total": int(row[6] or 0),
                    "symbol": row[7],
                    "progress": row[8],
                    "last_run_at": row[9],
                    "next_run_at": row[10],
                    "last_error": row[11],
                    "last_message": row[12],
                }
            )
        jobs.append(job)
    return jobs


def save_job(db_path: str, job_key: str, fields: dict) -> None:
    ensure_control_tables(db_path)
    allowed = {
        "state",
        "running",
        "mode",
        "attempt",
        "current",
        "total",
        "symbol",
        "message",
        "last_run_at",
        "next_run_at",
        "last_error",
        "last_message",
    }
    payload = {key: fields[key] for key in allowed if key in fields}
    if "running" in payload:
        payload["running"] = 1 if payload["running"] else 0
    if not payload:
        return
    assignments = ", ".join(f"{key} = ?" for key in payload)
    values = list(payload.values())
    values.append(_now())
    values.append(job_key)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute(
            f"UPDATE sync_job SET {assignments}, updated_at = ? WHERE job_key = ?",
            values,
        )
        conn.commit()


def load_progress(db_path: str) -> dict:
    ensure_control_tables(db_path)
    jobs = load_jobs(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        runs = conn.execute(
            """
            SELECT at, ok, triggered, message, error, written, targets, failed_count
            FROM sync_run ORDER BY id DESC LIMIT 20
            """
        ).fetchall()
        blocked = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='stock_sync_state'
            """
        ).fetchone()
        failures = []
        if blocked:
            columns = {item[1] for item in conn.execute("PRAGMA table_info(stock_sync_state)")}
            if "failed_date" in columns:
                failures = conn.execute(
                    """
                    SELECT symbol, failed_date, failed_reason
                    FROM stock_sync_state
                    WHERE failed_date IS NOT NULL
                    ORDER BY failed_date, symbol
                    LIMIT 200
                    """
                ).fetchall()
    active = next((item for item in jobs if item["running"]), None)
    errored = next((item for item in jobs if item["state"] == "error"), None)
    latest = max(jobs, key=lambda item: item["last_run_at"] or "", default=None)
    primary = active or errored or latest or _empty_job("incremental")
    schedule = next((item for item in jobs if item["key"] == "schedule"), primary)
    return {
        "state": "running" if active else ("error" if errored else primary.get("state") or "idle"),
        "running": bool(active),
        "mode": primary.get("mode"),
        "attempt": int(primary.get("attempt") or 0),
        "current": int(primary.get("current") or 0),
        "total": int(primary.get("total") or 0),
        "symbol": primary.get("symbol"),
        "progress": primary.get("progress"),
        "last_run_at": primary.get("last_run_at"),
        "next_run_at": schedule.get("next_run_at"),
        "last_error": primary.get("last_error"),
        "last_message": primary.get("last_message"),
        "jobs": jobs,
        "recent_runs": [
            {
                "at": item[0],
                "ok": bool(item[1]),
                "triggered": item[2],
                "message": item[3],
                "error": item[4],
                "written": item[5],
                "targets": item[6],
                "failed_count": item[7],
            }
            for item in runs
        ],
        "recent_failures": [
            {
                "symbol": item[0],
                "at": item[1],
                "reason": item[2] or f"{item[0]} 在 {item[1]} 失败，已停止后续更新",
                "error": item[2],
            }
            for item in failures
        ],
    }


def save_progress(db_path: str, fields: dict) -> None:
    ensure_control_tables(db_path)
    allowed = {
        "state",
        "running",
        "mode",
        "attempt",
        "current",
        "total",
        "symbol",
        "message",
        "last_run_at",
        "next_run_at",
        "last_error",
        "last_message",
    }
    payload = {key: fields[key] for key in allowed if key in fields}
    if "running" in payload:
        payload["running"] = 1 if payload["running"] else 0
    if not payload:
        return
    assignments = ", ".join(f"{key} = ?" for key in payload)
    values = list(payload.values())
    values.append(_now())
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute(
            f"UPDATE sync_progress SET {assignments}, updated_at = ? WHERE id = 1",
            values,
        )
        conn.commit()


def append_run(db_path: str, run: dict) -> None:
    ensure_control_tables(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute(
            """
            INSERT INTO sync_run (at, ok, triggered, message, error, written, targets, failed_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.get("at") or _now(),
                1 if run.get("ok") else 0,
                run.get("triggered"),
                run.get("message"),
                run.get("error"),
                int(run.get("written") or 0),
                int(run.get("targets") or 0),
                int(run.get("failed_count") or 0),
            ),
        )
        conn.execute(
            """
            DELETE FROM sync_run WHERE id NOT IN (
                SELECT id FROM sync_run ORDER BY id DESC LIMIT 50
            )
            """
        )
        conn.commit()
