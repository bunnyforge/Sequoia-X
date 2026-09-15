"""二倍买入法：跌一档就按上一笔两倍加仓。"""

from sequoia_x.backtest.double_buy import DoubleBuyParams, run_double_buy


def test_doubles_on_step_drop_then_takes_profit() -> None:
    # 10 元底仓；跌 10% 到 9 加两倍；再跌到 8.1 再加；随后拉回均价 15% 卖出。
    dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    closes = [10.0, 9.0, 8.1, 8.1, 12.0]
    result = run_double_buy(
        "000001",
        dates,
        closes,
        DoubleBuyParams(capital=31000, layers=5, drop_pct=0.10, take_profit=0.15, fee=0.0),
    )
    buys = [item for item in result.fills if item.side == "buy"]
    sells = [item for item in result.fills if item.side == "sell"]
    assert [item.layer for item in buys] == [1, 2, 3]
    assert abs(buys[0].cash - 1000) < 1e-6
    assert abs(buys[1].cash - 2000) < 1e-6
    assert abs(buys[2].cash - 4000) < 1e-6
    assert len(sells) == 1
    assert result.cycles == 1
    assert result.equity > 31000


def test_empty_bars() -> None:
    result = run_double_buy("000001", [], [], DoubleBuyParams())
    assert result.message == "没有日K"
    assert result.equity == 100_000
