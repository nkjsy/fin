import yfinance as yf
import pandas as pd
from providers.interfaces import IDataProvider

class YFinanceProvider(IDataProvider):
    def get_history(self, ticker: str, interval: str, period: str = "max", end_date: str = None) -> pd.DataFrame:
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
            
            if end_date:
                # If end_date is provided, fetch a small window before it to ensure we get the last trading day
                end_dt = pd.to_datetime(end_date)
                start_dt = end_dt - pd.Timedelta(days=7) # 7 days buffer for weekends/holidays
                df = stock.history(start=start_dt, end=end_dt, interval=yf_interval)
            else:
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
