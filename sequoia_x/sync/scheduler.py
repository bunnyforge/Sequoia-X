"""页面可配置的定时增量同步：间隔、失败重试、立即执行。配置与进度落数据库。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable

from sequoia_x.sync.lite_sync import run_market_sync
from sequoia_x.sync.store import (
    MIN_INTERVAL_SECONDS,
    JOB_DEFS,
    _empty_job,
    append_run,
    clamp_config,
    load_config,
    load_jobs,
    load_progress,
    save_config,
    save_job,
)

__all__ = ["SyncScheduler", "clamp_config", "MIN_INTERVAL_SECONDS"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_key_for(mode: str, triggered: str) -> str:
    if mode == "full":
        return "full"
    if triggered == "schedule":
        return "schedule"
    return "incremental"


class SyncScheduler:
    def __init__(
        self,
        db_path: str,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self.db_path = db_path
        self.on_complete = on_complete
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._workers: dict[str, threading.Thread] = {}
        self._pending: dict[str, dict] = {}
        self._last_persist: dict[str, float] = {}
        self._config = load_config(db_path)
        stored = {item["key"]: item for item in load_jobs(db_path)}
        self._jobs: dict[str, dict] = {}
        for job_key, _label in JOB_DEFS:
            job = stored.get(job_key) or _empty_job(job_key)
            if job.get("running"):
                job["running"] = False
                if job.get("state") == "running":
                    job["state"] = "idle"
                job["progress"] = job.get("progress") or "上次同步中断，进度已保留"
                save_job(
                    db_path,
                    job_key,
                    {
                        "running": False,
                        "state": job["state"],
                        "message": job["progress"],
                    },
                )
            self._jobs[job_key] = job

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sequoia-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        for worker in list(self._workers.values()):
            worker.join(timeout=1)
        for job_key in list(self._jobs):
            self._flush_job(job_key, force=True)

    def snapshot(self) -> dict:
        stored = load_progress(self.db_path)
        with self._lock:
            live_jobs = [dict(self._jobs[key]) for key, _label in JOB_DEFS]
            config = dict(self._config)
        jobs = []
        stored_by_key = {item["key"]: item for item in stored.get("jobs") or []}
        for live in live_jobs:
            merged = {**stored_by_key.get(live["key"], {}), **live}
            jobs.append(merged)
        active = next((item for item in jobs if item.get("running")), None)
        errored = next((item for item in jobs if item.get("state") == "error"), None)
        return {
            "config": config,
            **stored,
            "jobs": jobs,
            "running": any(item.get("running") for item in jobs),
            "state": "running" if active else ("error" if errored else "idle"),
            "recent_runs": stored.get("recent_runs") or [],
            "recent_failures": stored.get("recent_failures") or [],
        }

    def update_config(self, raw: dict) -> dict:
        with self._lock:
            self._config = clamp_config({**self._config, **raw})
            save_config(self.db_path, self._config)
            if not self._config["enabled"]:
                self._jobs["schedule"]["next_run_at"] = None
                self._queue_job("schedule", {"next_run_at": None})
        self._flush_job("schedule", force=True)
        self._wake.set()
        return self.snapshot()

    def request_run(self, mode: str = "incremental") -> dict:
        if mode not in {"incremental", "full"}:
            raise RuntimeError("同步模式必须是 incremental 或 full")
        job_key = _job_key_for(mode, "manual")
        self._start_job(job_key, mode, "manual")
        return self.snapshot()

    def _start_job(self, job_key: str, mode: str, triggered: str) -> None:
        with self._lock:
            job = self._jobs[job_key]
            if job.get("running"):
                raise RuntimeError(f"{job['label']}正在执行")
            worker = self._workers.get(job_key)
            if worker and worker.is_alive():
                raise RuntimeError(f"{job['label']}正在执行")
            job.update(
                {
                    "running": True,
                    "state": "running",
                    "mode": mode,
                    "attempt": 0,
                    "last_error": None,
                    "progress": "准备同步",
                    "current": 0,
                    "total": 0,
                    "symbol": None,
                }
            )
            config = dict(self._config)
        self._queue_job(
            job_key,
            {
                "running": True,
                "state": "running",
                "mode": mode,
                "attempt": 0,
                "last_error": None,
                "message": "准备同步",
                "current": 0,
                "total": 0,
                "symbol": None,
            },
        )
        self._flush_job(job_key, force=True)
        worker = threading.Thread(
            target=self._execute_cycle,
            args=(job_key, mode, triggered, config),
            name=f"sequoia-sync-{job_key}",
            daemon=True,
        )
        self._workers[job_key] = worker
        worker.start()

    def _queue_job(self, job_key: str, fields: dict) -> None:
        with self._persist_lock:
            pending = self._pending.get(job_key, {})
            pending.update(fields)
            self._pending[job_key] = pending

    def _flush_job(self, job_key: str, force: bool = False) -> None:
        now = time.monotonic()
        with self._persist_lock:
            pending = self._pending.get(job_key)
            if not pending:
                return
            if not force and now - self._last_persist.get(job_key, 0) < 0.4:
                return
            payload = dict(pending)
            self._pending[job_key] = {}
            self._last_persist[job_key] = now
        save_job(self.db_path, job_key, payload)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                config = dict(self._config)
                schedule_running = bool(self._jobs["schedule"].get("running"))
            if not config["enabled"]:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue
            if schedule_running:
                if self._stop.wait(0.5):
                    return
                continue
            delay = config["interval_seconds"]
            next_at = datetime.fromtimestamp(time.time() + delay, tz=timezone.utc).isoformat()
            with self._lock:
                self._jobs["schedule"]["next_run_at"] = next_at
            self._queue_job("schedule", {"next_run_at": next_at})
            self._flush_job("schedule", force=True)
            triggered = self._wake.wait(timeout=delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._lock:
                enabled = self._config["enabled"]
                schedule_running = bool(self._jobs["schedule"].get("running"))
            if triggered:
                continue
            if enabled and not schedule_running:
                try:
                    self._start_job("schedule", "incremental", "schedule")
                except RuntimeError:
                    continue

    def _execute_cycle(self, job_key: str, mode: str, triggered: str, config: dict) -> None:
        max_retries = config["max_retries"]
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            with self._lock:
                self._jobs[job_key]["attempt"] = attempt
            self._queue_job(job_key, {"attempt": attempt})
            self._flush_job(job_key, force=True)
            try:
                result = run_market_sync(
                    self.db_path,
                    mode=mode,
                    start_date=config.get("start_date") or "2024-01-01",
                    on_progress=lambda current, total, symbol, key=job_key: self._on_progress(
                        key, current, total, symbol
                    ),
                    on_status=lambda message, key=job_key: self._on_status(key, message),
                )
                failed = result.get("failed") or []
                message = result.get("message") or "同步完成"
                error = None
                if failed:
                    sample = "；".join(
                        f"{item.get('symbol')}: {item.get('error')}"
                        for item in failed[:3]
                    )
                    message = f"{message}。已停住的股票继续留在失败日，其它股票已照常拉完"
                    error = sample
                self._finish_run(
                    job_key,
                    ok=True,
                    message=message,
                    error=error,
                    failed=failed,
                    triggered=f"{triggered}:{mode}",
                    extra=result,
                    state="warning" if failed else "idle",
                )
                return
            except Exception as exc:
                error = str(exc)
                if attempt <= max_retries:
                    retry_msg = f"整轮任务第 {attempt} 次失败，{config['retry_seconds']} 秒后重试"
                    with self._lock:
                        self._jobs[job_key]["state"] = "retrying"
                        self._jobs[job_key]["last_error"] = error
                        self._jobs[job_key]["last_message"] = retry_msg
                    self._queue_job(
                        job_key,
                        {
                            "state": "retrying",
                            "last_error": error,
                            "last_message": retry_msg,
                        },
                    )
                    self._flush_job(job_key, force=True)
                    if self._stop.wait(config["retry_seconds"]):
                        return
                    continue
                self._finish_run(
                    job_key,
                    ok=False,
                    message=f"整轮同步失败，已达到最大重试次数：{error}",
                    error=error,
                    failed=[{"symbol": "*", "error": error}],
                    triggered=f"{triggered}:{mode}",
                    extra={},
                    state="error",
                )
                return

    def _on_status(self, job_key: str, message: str) -> None:
        with self._lock:
            self._jobs[job_key]["last_message"] = message
        self._queue_job(job_key, {"last_message": message})
        self._flush_job(job_key, force=True)

    def _on_progress(self, job_key: str, current: int, total: int, symbol: str) -> None:
        text = f"{current}/{total} {symbol}"
        with self._lock:
            self._jobs[job_key]["progress"] = text
            self._jobs[job_key]["current"] = current
            self._jobs[job_key]["total"] = total
            self._jobs[job_key]["symbol"] = symbol
        self._queue_job(
            job_key,
            {
                "message": text,
                "current": current,
                "total": total,
                "symbol": symbol,
            },
        )
        self._flush_job(job_key)

    def _finish_run(
        self,
        job_key: str,
        ok: bool,
        message: str,
        error: str | None,
        failed: list,
        triggered: str,
        extra: dict,
        state: str | None = None,
    ) -> None:
        run = {
            "at": _utc_now(),
            "ok": ok,
            "triggered": triggered,
            "message": message,
            "error": error,
            "written": extra.get("written", 0),
            "targets": extra.get("targets", 0),
            "failed_count": len(failed),
        }
        with self._lock:
            job = self._jobs[job_key]
            current = job.get("current") or extra.get("targets") or 0
            total = job.get("total") or extra.get("targets") or 0
            symbol = job.get("symbol")
            job_state = state or ("idle" if ok else "error")
            job.update(
                {
                    "running": False,
                    "state": job_state,
                    "last_run_at": run["at"],
                    "last_message": message,
                    "last_error": error,
                    "progress": f"{current}/{total}" if total else job.get("progress"),
                    "current": current,
                    "total": total,
                }
            )
        append_run(self.db_path, run)
        self._queue_job(
            job_key,
            {
                "running": False,
                "state": job_state,
                "last_run_at": run["at"],
                "last_message": message,
                "last_error": error,
                "message": job.get("progress"),
                "current": current,
                "total": total,
                "symbol": symbol,
            },
        )
        self._flush_job(job_key, force=True)
        if self.on_complete:
            self.on_complete()
