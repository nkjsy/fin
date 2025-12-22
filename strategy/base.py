from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Takes a DataFrame with OHLCV data, adds indicators, and generates 'Signal' column.
        Signal: 1 (Buy), -1 (Sell), 0 (Hold)
        """
        pass
