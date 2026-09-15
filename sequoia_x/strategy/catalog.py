"""策略目录：页面展示与可配置字段。"""

from __future__ import annotations

from sequoia_x.backtest.cli import DEFAULT_SYMBOLS

CATALOG: list[dict] = [
    {
        "key": "double_buy",
        "name": "二倍买入法",
        "kind": "backtest",
        "summary": "不猜底。先买 1 份，收盘再跌一档就按上一笔的两倍加仓，均价涨到目标清仓后再开一轮。适合相对靠谱的票。",
        "fields": [
            {"key": "symbols", "label": "回测股票（逗号分隔）", "type": "text", "default": ",".join(DEFAULT_SYMBOLS)},
            {"key": "start", "label": "起始日期", "type": "date", "default": "2024-01-01"},
            {"key": "capital", "label": "总资金", "type": "float", "min": 1000, "max": 10_000_000, "default": 100000},
            {"key": "layers", "label": "最多层数", "type": "int", "min": 2, "max": 8, "default": 5},
            {"key": "drop_pct", "label": "加仓跌幅", "type": "percent", "min": 0.02, "max": 0.5, "default": 0.10},
            {"key": "take_profit", "label": "均价止盈", "type": "percent", "min": 0.02, "max": 1, "default": 0.15},
            {"key": "fee", "label": "单边费率", "type": "percent", "min": 0, "max": 0.01, "default": 0.001},
        ],
    },
    {
        "key": "ma_volume",
        "name": "均线放量",
        "kind": "scan",
        "summary": "短均线上穿长均线，并且当日成交量大于均量的指定倍数。",
        "fields": [
            {"key": "ma_fast", "label": "短均线天数", "type": "int", "min": 2, "max": 30, "default": 5},
            {"key": "ma_slow", "label": "长均线天数", "type": "int", "min": 5, "max": 120, "default": 20},
            {"key": "volume_mult", "label": "放量倍数", "type": "float", "min": 1, "max": 8, "default": 1.5},
        ],
    },
    {
        "key": "turtle",
        "name": "海龟突破",
        "kind": "scan",
        "summary": "收盘创 N 日新高，成交额够大，且必须是真涨阳线。",
        "fields": [
            {"key": "lookback", "label": "突破窗口（天）", "type": "int", "min": 5, "max": 60, "default": 20},
            {"key": "min_turnover", "label": "最低成交额", "type": "float", "min": 1_000_000, "max": 5_000_000_000, "default": 100_000_000},
        ],
    },
    {
        "key": "flag",
        "name": "高旗形整理",
        "kind": "scan",
        "summary": "先有一轮大涨，再在高位窄幅缩量整理。",
        "fields": [
            {"key": "lookback", "label": "动量窗口（天）", "type": "int", "min": 20, "max": 120, "default": 40},
            {"key": "momentum", "label": "区间涨幅倍数", "type": "float", "min": 1.1, "max": 4, "default": 1.6},
            {"key": "flag_days", "label": "整理天数", "type": "int", "min": 5, "max": 30, "default": 10},
            {"key": "flag_range", "label": "整理振幅倍数", "type": "float", "min": 1.02, "max": 1.5, "default": 1.15},
            {"key": "hold_ratio", "label": "高位抗跌比例", "type": "percent", "min": 0.5, "max": 1, "default": 0.8},
            {"key": "shrink", "label": "缩量比例", "type": "percent", "min": 0.2, "max": 1, "default": 0.6},
        ],
    },
    {
        "key": "shakeout",
        "name": "涨停洗盘",
        "kind": "scan",
        "summary": "昨天涨停，今天放量收阴，但最低价不破昨收。",
        "fields": [
            {"key": "limit_up", "label": "涨停倍数", "type": "float", "min": 1.05, "max": 1.21, "default": 1.095},
            {"key": "volume_mult", "label": "放量倍数", "type": "float", "min": 1, "max": 8, "default": 2.0},
        ],
    },
    {
        "key": "limit_down",
        "name": "上升趋势跌停",
        "kind": "scan",
        "summary": "均线仍是多头，当天放量跌停，找错杀。",
        "fields": [
            {"key": "ma_fast", "label": "短均线天数", "type": "int", "min": 5, "max": 30, "default": 20},
            {"key": "ma_slow", "label": "长均线天数", "type": "int", "min": 20, "max": 120, "default": 60},
            {"key": "limit_down", "label": "跌停倍数", "type": "float", "min": 0.8, "max": 0.95, "default": 0.905},
            {"key": "volume_mult", "label": "放量倍数", "type": "float", "min": 1, "max": 8, "default": 2.0},
        ],
    },
    {
        "key": "rps",
        "name": "RPS 动量突破",
        "kind": "scan",
        "summary": "近一段时间涨幅排在市场前列，并且收盘靠近期间高点。",
        "fields": [
            {"key": "rps_period", "label": "RPS 周期（天）", "type": "int", "min": 20, "max": 250, "default": 120},
            {"key": "rps_threshold", "label": "RPS 阈值", "type": "int", "min": 50, "max": 99, "default": 90},
            {"key": "breakout", "label": "接近高点比例", "type": "percent", "min": 0.7, "max": 1, "default": 0.90},
        ],
    },
]


def catalog_by_key() -> dict[str, dict]:
    return {item["key"]: item for item in CATALOG}


def default_params(key: str) -> dict:
    item = catalog_by_key()[key]
    return {field["key"]: field["default"] for field in item["fields"]}
