from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sequoia_x.core.config import bootstrap_app
from sequoia_x.data.engine import DataEngine
from sequoia_x.data.stats import collect_dashboard_stats, list_data_alerts
from sequoia_x.db import resolve_db_path
from sequoia_x.sync.scheduler import SyncScheduler
from sequoia_x.strategy.hub import StrategyHub

_STATS_TTL_SECONDS = 60
_stats_cache: dict = {"at": 0.0, "path": "", "data": None}
_alerts_cache: dict = {"at": 0.0, "path": "", "data": None}
scheduler: SyncScheduler | None = None
strategy_hub: StrategyHub | None = None


def get_db_path() -> str:
    return resolve_db_path()


def invalidate_stats_cache() -> None:
    _stats_cache["data"] = None
    _stats_cache["at"] = 0.0
    _alerts_cache["data"] = None
    _alerts_cache["at"] = 0.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global scheduler, strategy_hub
    db = get_db_path()
    cfg = bootstrap_app(db)
    scheduler = SyncScheduler(
        db_path=db,
        on_complete=invalidate_stats_cache,
    )
    strategy_hub = StrategyHub(db_path=db)

    class _BootSettings:
        db_path = db
        start_date = cfg["start_date"]

    DataEngine(_BootSettings())  # type: ignore[arg-type]
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Sequoia-X API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8002",
        "http://127.0.0.1:8002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TrendPoint(BaseModel):
    date: str
    records: int


class SyncSummary(BaseModel):
    latest_trade_date: str | None
    symbol_count: int
    latest_symbol_count: int
    tasks_today: int
    synced_records: int
    success_rate: float
    pending_alerts: int
    correctness_rate: float
    completeness_rate: float
    duplicate_rate: float
    invalid_rows: int
    stale_symbols: int
    trend: list[TrendPoint]


class SyncJob(BaseModel):
    id: int
    name: str
    source: str
    processed: int
    success_rate: float
    status: str


class SchedulerConfigIn(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=86400)
    retry_seconds: int | None = Field(default=None, ge=5, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=20)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class SchedulerRunIn(BaseModel):
    mode: str = "incremental"


class StrategyUpdateIn(BaseModel):
    enabled: bool | None = None
    params: dict | None = None


def _require_scheduler() -> SyncScheduler:
    if scheduler is None:
        raise HTTPException(status_code=503, detail="调度器未启动")
    return scheduler


def _stats() -> dict:
    import time

    path = get_db_path()
    now = time.monotonic()
    if (
        _stats_cache["data"] is not None
        and _stats_cache["path"] == path
        and now - _stats_cache["at"] < _STATS_TTL_SECONDS
    ):
        return _stats_cache["data"]
    data = collect_dashboard_stats(path)
    _stats_cache["at"] = now
    _stats_cache["path"] = path
    _stats_cache["data"] = data
    return data


@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/dashboard/summary", response_model=SyncSummary)
def dashboard_summary():
    return SyncSummary.model_validate(_stats())


@app.get("/api/sync/jobs")
def sync_jobs():
    stats = _stats()
    sched = _require_scheduler().snapshot()
    latest = stats["latest_trade_date"]
    market_status = "idle" if latest is None else "completed"
    if sched["running"]:
        market_status = "running"
    elif sched["state"] == "error":
        market_status = "warning"
    quality_status = (
        "idle"
        if latest is None
        else ("warning" if stats["pending_alerts"] else "completed")
    )
    items = [
        SyncJob(
            id=1,
            name="A股日行情增量同步",
            source="baostock",
            processed=stats["latest_symbol_count"],
            success_rate=stats["success_rate"],
            status=market_status,
        ),
        SyncJob(
            id=2,
            name="历史数据完整性校验",
            source="database",
            processed=stats["synced_records"],
            success_rate=stats["completeness_rate"],
            status=quality_status,
        ),
    ]
    return {"items": [item.model_dump() for item in items]}


@app.get("/api/alerts")
def alerts():
    import time

    path = get_db_path()
    now = time.monotonic()
    if (
        _alerts_cache["data"] is not None
        and _alerts_cache["path"] == path
        and now - _alerts_cache["at"] < 30
    ):
        data = _alerts_cache["data"]
    else:
        data = list_data_alerts(path)
        _alerts_cache["at"] = now
        _alerts_cache["path"] = path
        _alerts_cache["data"] = data
    failures = _require_scheduler().snapshot().get("recent_failures") or []
    return {
        **data,
        "sync_failures": failures,
        "pending_count": len(data["stale"]) + len(data["invalid"]) + len(failures),
    }


@app.get("/api/scheduler")
def get_scheduler():
    return _require_scheduler().snapshot()


@app.put("/api/scheduler")
def put_scheduler(body: SchedulerConfigIn):
    payload = body.model_dump(exclude_none=True)
    return _require_scheduler().update_config(payload)


@app.post("/api/scheduler/run")
def run_scheduler(body: SchedulerRunIn | None = None):
    mode = (body.mode if body else "incremental") or "incremental"
    try:
        return _require_scheduler().request_run(mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _require_hub() -> StrategyHub:
    if strategy_hub is None:
        raise HTTPException(status_code=503, detail="策略中心未启动")
    return strategy_hub


@app.get("/api/strategies")
def get_strategies():
    return _require_hub().snapshot()


@app.put("/api/strategies/{key}")
def put_strategy(key: str, body: StrategyUpdateIn):
    try:
        return _require_hub().update(key, body.enabled, body.params)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/strategies/{key}/run")
def run_strategy(key: str):
    try:
        return _require_hub().request_run(key)
    except RuntimeError as exc:
        status = 409 if "正在运行" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc


# Serve Vite build last so /api/* routes take precedence
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
