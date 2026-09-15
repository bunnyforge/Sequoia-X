"""二倍买入法：不猜底，跌一档就按上一笔的两倍加仓。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DoubleBuyParams:
    capital: float = 100_000.0
    layers: int = 5
    drop_pct: float = 0.10
    take_profit: float = 0.15
    fee: float = 0.001


@dataclass
class Fill:
    date: str
    side: str
    price: float
    cash: float
    shares: float
    layer: int
    avg_cost: float
    note: str


@dataclass
class DoubleBuyResult:
    symbol: str
    params: DoubleBuyParams
    start: str
    end: str
    bars: int
    equity: float
    return_pct: float
    max_drawdown_pct: float
    cycles: int
    max_layer_used: int
    fills: list[Fill] = field(default_factory=list)
    message: str = ""


def _unit(params: DoubleBuyParams) -> float:
    return params.capital / (2**params.layers - 1)


def _fee(cash: float, params: DoubleBuyParams) -> float:
    return abs(cash) * params.fee


def run_double_buy(
    symbol: str,
    dates: list[str],
    closes: list[float],
    params: DoubleBuyParams | None = None,
) -> DoubleBuyResult:
    """用收盘价走完一段行情。

    规则：
    1. 不判断是不是最低点。窗口第一天先买 1 份。
    2. 收盘相对上一笔买入价再跌 drop_pct，就用上一笔金额的两倍加仓。
    3. 最多 layers 层，总资金按 1+2+4+... 预先切好，最坏情况能买满。
    4. 收盘相对持仓均价涨 take_profit，全部卖掉，下一交易日再开一轮 1 份。
    """
    params = params or DoubleBuyParams()
    unit = _unit(params)
    if len(dates) == 0 or len(dates) != len(closes):
        return DoubleBuyResult(
            symbol=symbol,
            params=params,
            start="",
            end="",
            bars=0,
            equity=params.capital,
            return_pct=0.0,
            max_drawdown_pct=0.0,
            cycles=0,
            max_layer_used=0,
            message="没有日K",
        )

    cash = params.capital
    shares = 0.0
    spent = 0.0
    last_buy_price = 0.0
    next_layer = 0
    restart = True
    fills: list[Fill] = []
    cycles = 0
    max_layer_used = 0
    peak = params.capital
    max_dd = 0.0

    def equity(price: float) -> float:
        return cash + shares * price

    def avg_cost() -> float:
        return spent / shares if shares > 0 else 0.0

    def buy(date: str, price: float, layer: int, note: str) -> bool:
        nonlocal cash, shares, spent, last_buy_price, next_layer, max_layer_used
        budget = unit * (2**layer)
        if cash <= 0 or price <= 0:
            return False
        pay = min(budget, cash)
        if pay < budget * 0.5:
            return False
        fee = _fee(pay, params)
        if pay + fee > cash:
            pay = cash / (1 + params.fee)
            fee = cash - pay
        qty = pay / price
        cash -= pay + fee
        shares += qty
        spent += pay
        last_buy_price = price
        next_layer = layer + 1
        max_layer_used = max(max_layer_used, layer + 1)
        fills.append(
            Fill(
                date=date,
                side="buy",
                price=price,
                cash=pay,
                shares=qty,
                layer=layer + 1,
                avg_cost=avg_cost(),
                note=note,
            )
        )
        return True

    def sell(date: str, price: float, note: str) -> None:
        nonlocal cash, shares, spent, last_buy_price, next_layer, restart, cycles
        if shares <= 0:
            return
        proceeds = shares * price
        fee = _fee(proceeds, params)
        cash += proceeds - fee
        fills.append(
            Fill(
                date=date,
                side="sell",
                price=price,
                cash=proceeds,
                shares=shares,
                layer=next_layer,
                avg_cost=avg_cost(),
                note=note,
            )
        )
        shares = 0.0
        spent = 0.0
        last_buy_price = 0.0
        next_layer = 0
        restart = True
        cycles += 1

    for date, price in zip(dates, closes):
        if price is None or price <= 0:
            continue
        if restart and shares == 0:
            if buy(date, price, 0, "底仓 1 份"):
                restart = False
        else:
            while (
                shares > 0
                and next_layer < params.layers
                and price <= last_buy_price * (1 - params.drop_pct)
            ):
                if not buy(date, price, next_layer, f"再跌 {params.drop_pct:.0%}，二倍加仓"):
                    break
            if shares > 0 and avg_cost() > 0 and price >= avg_cost() * (1 + params.take_profit):
                sell(date, price, f"均价回升 {params.take_profit:.0%} 清仓")

        mark = equity(price)
        if mark > peak:
            peak = mark
        if peak > 0:
            max_dd = max(max_dd, (peak - mark) / peak)

    last_price = next((item for item in reversed(closes) if item and item > 0), 0.0)
    final = equity(last_price)
    return DoubleBuyResult(
        symbol=symbol,
        params=params,
        start=dates[0],
        end=dates[-1],
        bars=len(dates),
        equity=final,
        return_pct=(final / params.capital - 1) * 100,
        max_drawdown_pct=max_dd * 100,
        cycles=cycles,
        max_layer_used=max_layer_used,
        fills=fills,
    )
