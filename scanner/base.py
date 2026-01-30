import pandas as pd
import os
from abc import ABC, abstractmethod
from datetime import datetime, time as dt_time
from typing import Optional, List
from zoneinfo import ZoneInfo


# Eastern timezone
ET = ZoneInfo("America/New_York")


class BaseScanner(ABC):
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.summary_path = os.path.join(data_dir, "universe_summary.parquet")
        # if using yfinance.screen, max 250 tickers returned
        self.limit = 250

    def load_summary(self) -> pd.DataFrame:
        if os.path.exists(self.summary_path):
            return pd.read_parquet(self.summary_path, engine='fastparquet')
        return pd.DataFrame()

    @abstractmethod
    def scan(self, **kwargs) -> list:
        pass
    
    @staticmethod
    def confirm_volume(
        df: pd.DataFrame,
        threshold: float = 5.0,
        start_time: dt_time = dt_time(4, 0),
        end_time: Optional[dt_time] = None
    ) -> tuple[bool, Optional[float]]:
        """
        Confirm relative volume meets threshold: today's volume vs yesterday's same time window.
        
        Args:
            df: DataFrame with 'Datetime' and 'Volume' columns
            threshold: Minimum relative volume ratio (default: 5.0x)
            start_time: Start of time window (default: 4:00 AM for premarket)
            end_time: End of time window (default: current time)
            
        Returns:
            Tuple of (confirmed: bool, ratio: float or None)
        """
        if df.empty or "Datetime" not in df.columns or "Volume" not in df.columns:
            return False, None
        
        now = datetime.now(ET)
        end_time = end_time or now.time()
        
        # Extract date and time components
        df = df.copy()
        df["_date"] = df["Datetime"].dt.date
        df["_time"] = df["Datetime"].dt.time
        
        # Get unique dates
        dates = sorted(df["_date"].unique())
        
        if len(dates) < 2:
            return False, None
        
        today = dates[-1]
        yesterday = dates[-2]
        
        def get_window_volume(date):
            mask = (df["_date"] == date) & (df["_time"] >= start_time) & (df["_time"] <= end_time)
            return df[mask]["Volume"].sum()
        
        vol_today = get_window_volume(today)
        vol_yesterday = get_window_volume(yesterday)
        
        if vol_yesterday == 0:
            return False, None
        
        ratio = vol_today / vol_yesterday
        return ratio >= threshold, ratio

