import yfinance as yf
import pandas as pd
from interfaces import IDataProvider

class YFinanceProvider(IDataProvider):
    def get_history(self, ticker: str, interval: str, period: str = "max") -> pd.DataFrame:
        # Map custom intervals to yfinance intervals
        interval_map = {
            "daily1": "1d",
            "minute1": "1m",
            "minute5": "5m",
            "hour1": "1h"
        }
        yf_interval = interval_map.get(interval, interval)

        try:
            # yfinance intervals: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo
            stock = yf.Ticker(ticker)
            df = stock.history(period=period, interval=yf_interval)
            
            if df.empty:
                return pd.DataFrame()
            
            # Reset index to make Date/Datetime a column
            df = df.reset_index()
            
            # Standardize column names if necessary (yfinance is usually Title Case)
            # Ensure we have: Date, Open, High, Low, Close, Volume
            return df
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")
            return pd.DataFrame()

    def get_quote(self, ticker: str) -> float:
        try:
            stock = yf.Ticker(ticker)
            return stock.fast_info.last_price
        except:
            return 0.0
