"""策略配置中心：保存参数、后台执行选股或回测。"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from sequoia_x.backtest.cli import run_symbols
from sequoia_x.backtest.double_buy import DoubleBuyParams
from sequoia_x.strategy.catalog import CATALOG, catalog_by_key, default_params
from sequoia_x.strategy.store import (
    load_strategy_configs,
    save_strategy_config,
    save_strategy_run,
)


class _LiteSettings:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.start_date = "2024-01-01"
        self.feishu_webhook_url = ""
        self.strategy_webhooks: dict[str, str] = {}

    def get_webhook_url(self, webhook_key: str) -> str:
        return ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrategyHub:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}
        stored = load_strategy_configs(db_path)
        for key, row in stored.items():
            if row["result"].get("running"):
                save_strategy_run(db_path, key, {"running": False, "message": "上次运行中断"})

    def snapshot(self) -> dict:
        stored = load_strategy_configs(self.db_path)
        items = []
        for spec in CATALOG:
            row = stored.get(spec["key"]) or {
                "enabled": False,
                "params": default_params(spec["key"]),
                "result": {
                    "last_run_at": None,
                    "ok": False,
                    "running": False,
                    "message": None,
                    "picks": [],
                    "extra": {},
                },
            }
            worker = self._workers.get(spec["key"])
            running = bool(worker and worker.is_alive()) or bool(row["result"].get("running"))
            items.append(
                {
                    "key": spec["key"],
                    "name": spec["name"],
                    "kind": spec["kind"],
                    "summary": spec["summary"],
                    "fields": spec["fields"],
                    "enabled": row["enabled"],
                    "params": row["params"],
                    "running": running,
                    "last_run_at": row["result"].get("last_run_at"),
                    "ok": row["result"].get("ok"),
                    "message": row["result"].get("message"),
                    "picks": row["result"].get("picks") or [],
                    "extra": row["result"].get("extra") or {},
                }
            )
        return {"items": items}

    def update(self, key: str, enabled: bool | None, params: dict | None) -> dict:
        if key not in catalog_by_key():
            raise RuntimeError(f"未知策略：{key}")
        stored = load_strategy_configs(self.db_path).get(key) or {
            "enabled": False,
            "params": default_params(key),
        }
        next_enabled = stored["enabled"] if enabled is None else bool(enabled)
        next_params = {**stored["params"], **(params or {})}
        save_strategy_config(self.db_path, key, next_enabled, next_params)
        return self.snapshot()

    def request_run(self, key: str) -> dict:
        if key not in catalog_by_key():
            raise RuntimeError(f"未知策略：{key}")
        with self._lock:
            worker = self._workers.get(key)
            if worker and worker.is_alive():
                raise RuntimeError("该策略正在运行")
            save_strategy_run(
                self.db_path,
                key,
                {"running": True, "message": "正在执行"},
            )
            thread = threading.Thread(target=self._execute, args=(key,), daemon=True, name=f"strategy-{key}")
            self._workers[key] = thread
            thread.start()
        return self.snapshot()

    def _execute(self, key: str) -> None:
        spec = catalog_by_key()[key]
        params = load_strategy_configs(self.db_path)[key]["params"]
        try:
            if spec["kind"] == "backtest":
                extra, picks, message = self._run_backtest(params)
            else:
                extra, picks, message = self._run_scan(key, params)
            save_strategy_run(
                self.db_path,
                key,
                {
                    "running": False,
                    "ok": True,
                    "at": _utc_now(),
                    "message": message,
                    "picks": picks,
                    "extra": extra,
                },
            )
        except Exception as exc:
            save_strategy_run(
                self.db_path,
                key,
                {
                    "running": False,
                    "ok": False,
                    "at": _utc_now(),
                    "message": str(exc),
                    "picks": [],
                    "extra": {},
                },
            )

    def _run_scan(self, key: str, params: dict) -> tuple[dict, list[str], str]:
        from sequoia_x.data.engine import DataEngine
        from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
        from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
        from sequoia_x.strategy.ma_volume import MaVolumeStrategy
        from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
        from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
        from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy

        classes = {
            "ma_volume": MaVolumeStrategy,
            "turtle": TurtleTradeStrategy,
            "flag": HighTightFlagStrategy,
            "shakeout": LimitUpShakeoutStrategy,
            "limit_down": UptrendLimitDownStrategy,
            "rps": RpsBreakoutStrategy,
        }
        settings = _LiteSettings(self.db_path)
        engine = DataEngine(settings)  # type: ignore[arg-type]
        strategy = classes[key](engine=engine, settings=settings, params=params)  # type: ignore[arg-type]
        picks = strategy.run()
        message = f"选出 {len(picks)} 只"
        return {"count": len(picks)}, picks, message

    def _run_backtest(self, params: dict) -> tuple[dict, list[str], str]:
        symbols = [item.strip() for item in str(params.get("symbols") or "").split(",") if item.strip()]
        bt_params = DoubleBuyParams(
            capital=float(params.get("capital") or 100000),
            layers=int(params.get("layers") or 5),
            drop_pct=float(params.get("drop_pct") or 0.10),
            take_profit=float(params.get("take_profit") or 0.15),
            fee=float(params.get("fee") or 0.001),
        )
        results = run_symbols(self.db_path, symbols, str(params.get("start") or "2024-01-01"), bt_params)
        extra = {
            "results": [
                {
                    "symbol": item.symbol,
                    "return_pct": round(item.return_pct, 2),
                    "max_drawdown_pct": round(item.max_drawdown_pct, 2),
                    "cycles": item.cycles,
                    "max_layer_used": item.max_layer_used,
                    "fills": len(item.fills),
                    "equity": round(item.equity, 2),
                    "message": item.message,
                }
                for item in results
            ]
        }
        picks = [item.symbol for item in results if item.return_pct > 0]
        message = f"回测 {len(results)} 只，收益为正 {len(picks)} 只"
        return extra, picks, message
