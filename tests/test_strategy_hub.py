"""策略配置应能按策略分别保存。"""

from pathlib import Path

from sequoia_x.strategy.hub import StrategyHub
from sequoia_x.strategy.store import load_strategy_configs


def test_strategy_hub_saves_params(tmp_path: Path) -> None:
    db_path = str(tmp_path / "s.db")
    hub = StrategyHub(db_path)
    hub.update("double_buy", True, {"layers": 6, "drop_pct": 0.12})
    stored = load_strategy_configs(db_path)["double_buy"]
    assert stored["enabled"] is True
    assert stored["params"]["layers"] == 6
    assert stored["params"]["drop_pct"] == 0.12
    items = hub.snapshot()["items"]
    keys = [item["key"] for item in items]
    assert "double_buy" in keys
    assert "ma_volume" in keys
    assert len(items) >= 7
