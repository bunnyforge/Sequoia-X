"""高旗形整理策略：强动量后极度收敛缩量。"""

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class HighTightFlagStrategy(BaseStrategy):
    """高旗形整理策略。

    选股条件（向量化，严禁 iterrows）：
    1. 强动量：过去40天区间最高价 / 区间最低价 > 1.6（涨幅超60%）
    2. 极度收敛：最近10天区间最高价 / 区间最低价 < 1.15（振幅低于15%）
    3. 缩量：今日 volume < 过去20日 volume 均值的 0.6 倍

    Attributes:
        webhook_key: 路由到 'flag' 专属飞书机器人。
    """

    webhook_key: str = "flag"
    default_params = {
        "lookback": 40,
        "momentum": 1.6,
        "flag_days": 10,
        "flag_range": 1.15,
        "hold_ratio": 0.8,
        "shrink": 0.6,
    }

    def run(self) -> list[str]:
        """
        遍历全市场，返回满足高旗形整理条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                lookback = int(self.params["lookback"])
                flag_days = int(self.params["flag_days"])
                df = self.engine.get_recent_ohlcv(symbol, limit=lookback)
                if len(df) < lookback:
                    continue

                tail_long = df.tail(lookback)
                tail_flag = df.tail(flag_days)

                high_long = tail_long["high"].max()
                low_long = tail_long["low"].min()
                high_flag = tail_flag["high"].max()
                low_flag = tail_flag["low"].min()

                if low_long == 0 or low_flag == 0:
                    continue

                momentum = high_long / low_long > float(self.params["momentum"])
                consolidation = high_flag / low_flag < float(self.params["flag_range"])
                high_level = low_flag >= high_long * float(self.params["hold_ratio"])
                vol_ma20 = df["volume"].iloc[-21:-1].mean()
                shrink = df["volume"].iloc[-1] < vol_ma20 * float(self.params["shrink"])

                if momentum and consolidation and high_level and shrink:
                    selected.append(symbol)

            except Exception as exc:
                logger.warning(f"[{symbol}] HighTightFlagStrategy 计算失败：{exc}")
                continue

        logger.info(f"HighTightFlagStrategy 选出 {len(selected)} 只股票")
        return selected
