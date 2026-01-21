"""
Schwab Data Provider

Implements IDataProvider interface using schwab-py for fetching market data.
Synchronous implementation for compatibility with existing scanners.
"""

import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx

from schwab.auth import easy_client
from schwab.client import Client

from providers.interfaces import IDataProvider
import config
from utils import calculate_start_date


class SchwabProvider(IDataProvider):
    """
    Data provider using Schwab API via schwab-py.
    
    Implements synchronous methods for compatibility with existing scanner architecture.
    For streaming data, use the StreamClient directly in live_engine.py.
    """
    
    def __init__(self, client: Client = None):
        """
        Initialize SchwabProvider.
        
        Args:
            client: Optional pre-configured schwab Client. If None, creates one using config.
        """
        if client is not None:
            self.client = client
        else:
            self.client = self._create_client()
    
    def _create_client(self) -> Client:
        """Create a Schwab client using credentials from config."""
        return easy_client(
            api_key=config.SCHWAB_API_KEY,
            app_secret=config.SCHWAB_APP_SECRET,
            callback_url=config.SCHWAB_CALLBACK_URL,
            token_path=config.SCHWAB_TOKEN_PATH
        )
    
    def get_history(self, ticker: str, interval: str, period: str = "max", end_date: str = None) -> pd.DataFrame:
        """
        Fetch historical data for a ticker.
        
        Args:
            ticker: Symbol (e.g., 'AAPL')
            interval: Timeframe ('daily1', 'minute1', 'minute5', 'hour1')
            period: Lookback period (e.g., '1y', '2d', 'max') - used as hint for date range
            end_date: End date for data fetching (exclusive), optional.
            
        Returns:
            DataFrame with columns: Date/Datetime, Open, High, Low, Close, Volume
        """
        try:
            # Parse end_date
            end_dt = None
            if end_date:
                end_dt = pd.to_datetime(end_date)
            else:
                end_dt = datetime.now(ZoneInfo("America/New_York"))
            
            # Calculate start_date based on period
            start_dt = calculate_start_date(self.client, period, end_dt)
            
            # Call appropriate Schwab API method based on interval
            resp = self._fetch_price_history(ticker, interval, start_dt, end_dt)
            
            if resp.status_code != httpx.codes.OK:
                print(f"Error fetching {ticker}: {resp.status_code}")
                return pd.DataFrame()
            
            data = resp.json()
            
            # Convert to DataFrame
            df = self._parse_candles(data, interval)
            return df
            
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")
            return pd.DataFrame()
    
    def _fetch_price_history(self, ticker: str, interval: str, start_dt: datetime, end_dt: datetime):
        """Fetch price history using the appropriate Schwab API method."""
        # Map custom intervals to Schwab API methods
        if interval == "minute1":
            return self.client.get_price_history_every_minute(
                ticker,
                start_datetime=start_dt,
                end_datetime=end_dt,
                need_extended_hours_data=False
            )
        elif interval == "minute5":
            return self.client.get_price_history_every_five_minutes(
                ticker,
                start_datetime=start_dt,
                end_datetime=end_dt,
                need_extended_hours_data=False
            )
        elif interval == "hour1":
            return self.client.get_price_history_every_thirty_minutes(
                ticker,
                start_datetime=start_dt,
                end_datetime=end_dt,
                need_extended_hours_data=False
            )
        elif interval == "daily1":
            return self.client.get_price_history_every_day(
                ticker,
                start_datetime=start_dt,
                end_datetime=end_dt,
                need_extended_hours_data=False
            )
        else:
            # Default to daily
            return self.client.get_price_history_every_day(
                ticker,
                start_datetime=start_dt,
                end_datetime=end_dt,
                need_extended_hours_data=False
            )
    
    def _parse_candles(self, data: dict, interval: str) -> pd.DataFrame:
        """Parse Schwab candles response into DataFrame."""
        if "candles" not in data or not data["candles"]:
            return pd.DataFrame()
        
        candles = data["candles"]
        df = pd.DataFrame(candles)
        
        # Schwab returns: datetime (epoch ms), open, high, low, close, volume
        # Convert epoch milliseconds to datetime in Eastern time
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert("America/New_York")
        
        # Standardize column names to match existing format
        df = df.rename(columns={
            "datetime": "Datetime" if interval != "daily1" else "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        })
        
        # Select and order columns
        date_col = "Date" if interval == "daily1" else "Datetime"
        df = df[[date_col, "Open", "High", "Low", "Close", "Volume"]]
        
        return df
