import pandas as pd
import pandas_ta as ta
from .base import BaseStrategy

class RsiStrategy(BaseStrategy):
    def __init__(self, rsi_period: int = 14, buy_threshold: int = 30, sell_threshold: int = 70):
        super().__init__("RSI Strategy")
        self.rsi_period = rsi_period
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # Calculate RSI
        # pandas-ta requires a datetime index usually, but let's check if it works with columns
        # If 'Close' is present, it should work.
        df["RSI"] = df.ta.rsi(length=self.rsi_period)
        
        # Generate Signals
        df["Signal"] = 0
        
        # Buy when RSI crosses above buy_threshold (oversold)
        # Simple logic: If RSI < threshold, Buy. (Or crossover logic)
        # Let's do simple threshold for now:
        # Buy if RSI < 30
        # Sell if RSI > 70
        
        df.loc[df["RSI"] < self.buy_threshold, "Signal"] = 1
        df.loc[df["RSI"] > self.sell_threshold, "Signal"] = -1
        
        return df
