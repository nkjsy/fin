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
        
        Should also add:
        - Entry_Price: The price at which to execute a buy (0.0 if no buy signal)
        - Exit_Price: The price at which to execute a sell (0.0 if no sell signal)
        - Stop_Loss: Optional stop loss price for the trade
        
        Note: Entry/Exit prices should be realistic (e.g., next bar open) to avoid look-ahead bias.
        """
        pass
