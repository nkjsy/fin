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
        df["Entry_Price"] = 0.0
        df["Exit_Price"] = 0.0
        df["Stop_Loss"] = 0.0
        
        # Buy when RSI crosses above buy_threshold (oversold)
        # Simple logic: If RSI < threshold, Buy. (Or crossover logic)
        # Let's do simple threshold for now:
        # Buy if RSI < 30
        # Sell if RSI > 70
        
        # RSI is calculated using Close price, so signal is known at bar close
        # For realistic execution without look-ahead bias:
        # - We use Close price as entry/exit (assuming we can execute at close)
        # - Alternative would be next bar's Open, but that requires shifting
        
        buy_mask = df["RSI"] < self.buy_threshold
        sell_mask = df["RSI"] > self.sell_threshold
        
        df.loc[buy_mask, "Signal"] = 1
        df.loc[buy_mask, "Entry_Price"] = df.loc[buy_mask, "Close"]
        
        df.loc[sell_mask, "Signal"] = -1
        df.loc[sell_mask, "Exit_Price"] = df.loc[sell_mask, "Close"]
        
        return df
