"""用本地日 K 回测二倍买入法。"""

from __future__ import annotations

import argparse
import os
from sequoia_x.db import connect

from sequoia_x.backtest.double_buy import DoubleBuyParams, DoubleBuyResult, run_double_buy

DEFAULT_SYMBOLS = [
    "600519",  # 贵州茅台
    "600036",  # 招商银行
    "601318",  # 中国平安
    "000858",  # 五粮液
    "601398",  # 工商银行
    "600900",  # 长江电力
    "000001",  # 平安银行
    "601288",  # 农业银行
]


def load_closes(db_path: str, symbol: str, start: str | None) -> tuple[list[str], list[float]]:
    sql = "SELECT date, close FROM stock_daily WHERE symbol = ?"
    params: list = [symbol]
    if start:
        sql += " AND date >= ?"
        params.append(start)
    sql += " ORDER BY date"
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    dates = [str(row[0]) for row in rows]
    closes = [float(row[1]) for row in rows]
    return dates, closes


def format_result(result: DoubleBuyResult) -> str:
    if result.message:
        return f"{result.symbol}\t{result.message}"
    return (
        f"{result.symbol}\t"
        f"{result.start} ~ {result.end}\t"
        f"收益 {result.return_pct:+.2f}%\t"
        f"最大回撤 {result.max_drawdown_pct:.2f}%\t"
        f"完成轮次 {result.cycles}\t"
        f"最深加到第 {result.max_layer_used} 层\t"
        f"成交 {len(result.fills)} 笔\t"
        f"期末 {result.equity:,.0f}"
    )


def run_symbols(
    db_path: str,
    symbols: list[str],
    start: str | None,
    params: DoubleBuyParams,
    verbose: bool = False,
) -> list[DoubleBuyResult]:
    results: list[DoubleBuyResult] = []
    for symbol in symbols:
        dates, closes = load_closes(db_path, symbol, start)
        if not dates:
            results.append(
                DoubleBuyResult(
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
                    message="本地没有这只股票的日K",
                )
            )
            continue
        result = run_double_buy(symbol, dates, closes, params)
        results.append(result)
        if verbose:
            print(f"\n== {symbol} ==")
            for fill in result.fills:
                print(
                    f"  {fill.date} {fill.side:4} 第{fill.layer}层 "
                    f"价 {fill.price:.2f} 金额 {fill.cash:,.0f} "
                    f"均价 {fill.avg_cost:.2f}  {fill.note}"
                )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="二倍买入法回测：不猜底，跌一档加一倍。")
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "data/sequoia_v2.db"),
        help="数据库路径或 DATABASE_URL",
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="逗号分隔的股票代码",
    )
    parser.add_argument("--start", default="2024-01-01", help="回测起始日")
    parser.add_argument("--capital", type=float, default=100000, help="总资金")
    parser.add_argument("--layers", type=int, default=5, help="最多加仓层数")
    parser.add_argument("--drop", type=float, default=0.10, help="相对上一笔再跌多少加仓")
    parser.add_argument("--profit", type=float, default=0.15, help="相对均价涨多少清仓")
    parser.add_argument("--fee", type=float, default=0.001, help="单边费率")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每笔成交")
    args = parser.parse_args()
    params = DoubleBuyParams(
        capital=args.capital,
        layers=args.layers,
        drop_pct=args.drop,
        take_profit=args.profit,
        fee=args.fee,
    )
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    print(
        f"二倍买入法  资金 {params.capital:,.0f}  最多 {params.layers} 层  "
        f"跌 {params.drop_pct:.0%} 加仓  均价涨 {params.take_profit:.0%} 卖  "
        f"起始 {args.start}"
    )
    results = run_symbols(args.db, symbols, args.start, params, verbose=args.verbose)
    print("")
    for item in results:
        print(format_result(item))


if __name__ == "__main__":
    main()
