from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):

    def __init__(self, params: dict):
        self.params = params

    @abstractmethod
    def generate_signal(
        self,
        close:  pd.Series,
        volume: pd.Series = None,
    ) -> dict:
        """
        シグナルを生成して返す。

        Args:
            close:  終値のSeries（インデックスは日付）
            volume: 出来高のSeries（使わない戦略はNoneのまま）

        Returns:
            {
                "signal":     "buy" / "sell" / None,
                "status":     "confirmed" / "candidate" / "none",
                "indicators": {
                    "rsi":   float,
                    "macd":  float,
                    ...
                }
            }
        """
        pass

    @property
    def name(self) -> str:
        return self.__class__.__name__
