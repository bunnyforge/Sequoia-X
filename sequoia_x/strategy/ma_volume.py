"""均线+成交量选股策略：5日均线上穿20日均线且成交量放大。"""

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class MaVolumeStrategy(BaseStrategy):
    """均线+成交量选股策略。

    选股条件（全部向量化，严禁 iterrows）：
    1. 5日收盘均线上穿20日收盘均线（金叉）
    2. 当日成交量 > 20日均量的 1.5 倍（放量确认）
    """

    default_params = {"ma_fast": 5, "ma_slow": 20, "volume_mult": 1.5}

    def run(self) -> list[str]:
        """
        遍历全市场，返回满足均线金叉+放量条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                ma_fast = int(self.params["ma_fast"])
                ma_slow = int(self.params["ma_slow"])
                volume_mult = float(self.params["volume_mult"])
                df = self.engine.get_recent_ohlcv(symbol, limit=ma_slow)
                if len(df) < ma_slow:
                    continue

                df["ma_fast"] = df["close"].rolling(ma_fast).mean()
                df["ma_slow"] = df["close"].rolling(ma_slow).mean()
                df["vol_ma"] = df["volume"].rolling(ma_slow).mean()

                last = df.iloc[-1]
                prev = df.iloc[-2]

                golden_cross = (
                    prev["ma_fast"] < prev["ma_slow"]
                    and last["ma_fast"] > last["ma_slow"]
                )
                volume_surge = last["volume"] > last["vol_ma"] * volume_mult

                if golden_cross and volume_surge:
                    selected.append(symbol)

            except Exception as exc:
                logger.warning(f"[{symbol}] 策略计算失败：{exc}")
                continue

        logger.info(f"MaVolumeStrategy 选出 {len(selected)} 只股票")
        return selected
