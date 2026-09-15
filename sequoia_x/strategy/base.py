"""策略基类模块：定义所有选股策略的抽象接口。"""

from abc import ABC, abstractmethod
from typing import Any

from sequoia_x.data.engine import DataEngine


class BaseStrategy(ABC):
    """选股策略抽象基类。

    所有具体策略必须继承此类并实现 run() 方法。
    """

    default_params: dict = {}

    def __init__(
        self,
        engine: DataEngine,
        settings: Any,
        params: dict | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.params = {**self.default_params, **(params or {})}

    @abstractmethod
    def run(self) -> list[str]:
        """
        执行选股逻辑，返回选中的股票代码列表。

        Returns:
            满足策略条件的股票代码列表，如 ['000001', '600519']。
            无选股结果时返回空列表。
        """
        ...
