"""上升趋势跌停策略：趋势中放量跌停，捕捉错杀机会。"""

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class UptrendLimitDownStrategy(BaseStrategy):
    """上升趋势跌停策略。

    选股条件（向量化，严禁 iterrows）：
    1. 处于上升趋势：昨日20日均线 > 昨日60日均线
    2. 放量跌停：今日 close <= 昨日 close * 0.905
                且今日 volume > 20日均量的 2.0 倍

    Attributes:
        webhook_key: 路由到 'limit_down' 专属飞书机器人。
    """

    webhook_key: str = "limit_down"
    default_params = {"ma_fast": 20, "ma_slow": 60, "limit_down": 0.905, "volume_mult": 2.0}

    def run(self) -> list[str]:
        """
        遍历全市场，返回满足上升趋势跌停条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                ma_fast = int(self.params["ma_fast"])
                ma_slow = int(self.params["ma_slow"])
                df = self.engine.get_recent_ohlcv(symbol, limit=ma_slow)
                if len(df) < ma_slow:
                    continue

                df["ma_fast"] = df["close"].rolling(ma_fast).mean()
                df["ma_slow"] = df["close"].rolling(ma_slow).mean()
                df["vol_ma"] = df["volume"].rolling(ma_fast).mean()

                prev = df.iloc[-2]
                today = df.iloc[-1]

                if pd.isna(prev["ma_fast"]) or pd.isna(prev["ma_slow"]) or pd.isna(today["vol_ma"]):
                    continue

                uptrend = prev["ma_fast"] > prev["ma_slow"]
                limit_down = today["close"] <= prev["close"] * float(self.params["limit_down"])
                volume_surge = today["volume"] > today["vol_ma"] * float(self.params["volume_mult"])

                if uptrend and limit_down and volume_surge:
                    selected.append(symbol)

            except Exception as exc:
                logger.warning(f"[{symbol}] UptrendLimitDownStrategy 计算失败：{exc}")
                continue

        logger.info(f"UptrendLimitDownStrategy 选出 {len(selected)} 只股票")
        return selected
