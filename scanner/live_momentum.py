"""
Live Momentum Scanner

Scans for top movers at market open using Schwab API.
Confirms volume 9:30-9:40 against previous day.
"""

import time
from datetime import datetime, timedelta
import pandas as pd

from scanner.base import BaseScanner
from providers.schwab_lib import SchwabProvider


class LiveMomentumScanner(BaseScanner):
    """
    Live scanner that uses Schwab's market movers API and confirms with volume.
    
    Workflow:
    1. At 9:30, fetch top 10 up movers from NASDAQ
    2. Wait until 9:40 to collect volume data
    3. Confirm each mover has 5x relative volume vs previous day's same time window
    4. Return confirmed tickers
    """
    
    def __init__(self, provider: SchwabProvider, data_dir: str = "data"):
        """
        Initialize LiveMomentumScanner.
        
        Args:
            provider: SchwabProvider instance for API calls
            data_dir: Data directory (for BaseScanner compatibility)
        """
        super().__init__(data_dir)
        self.provider = provider
        self.relative_volume_threshold = 5.0  # 5x previous day volume
    
    def scan(self, wait_for_volume: bool = True, **kwargs) -> list:
        """
        Scan for top movers with volume confirmation.
        
        Args:
            wait_for_volume: If True, waits until 9:40 ET for volume confirmation.
                           If False, returns movers immediately (for testing).
            
        Returns:
            List of confirmed ticker symbols
        """
        print("--- Live Momentum Scanner ---")
        
        # Step 1: Get top 10 up movers
        print("Fetching top 10 NASDAQ up movers...")
        movers = self.provider.get_movers(index="NASDAQ", direction="up", count=10)
        
        if not movers:
            print("No movers found.")
            return []
        
        symbols = [m["symbol"] for m in movers]
        print(f"Top movers: {symbols}")
        
        for m in movers:
            print(f"  {m['symbol']}: ${m['lastPrice']:.2f} ({m['netPercentChange']:+.2f}%)")
        
        if not wait_for_volume:
            # Skip volume confirmation (for testing)
            return symbols
        
        # Step 2: Wait until 9:40 ET for volume data
        self._wait_until_940()
        
        # Step 3: Confirm volume for each mover
        print("\nConfirming volume (5x threshold)...")
        confirmed = []
        
        for symbol in symbols:
            if self._confirm_volume(symbol):
                confirmed.append(symbol)
                print(f"  ✓ {symbol} - CONFIRMED")
            else:
                print(f"  ✗ {symbol} - insufficient volume")
        
        print(f"\nConfirmed {len(confirmed)} tickers: {confirmed}")
        return confirmed
    
    def _wait_until_940(self):
        """Wait until 9:40 AM ET."""
        # Note: This is a simple implementation. In production, you'd want
        # proper timezone handling with pytz or zoneinfo.
        target_time = datetime.now().replace(hour=9, minute=40, second=0, microsecond=0)
        now = datetime.now()
        
        if now >= target_time:
            print("Already past 9:40 AM, proceeding with volume check...")
            return
        
        wait_seconds = (target_time - now).total_seconds()
        print(f"Waiting until 9:40 AM ({wait_seconds:.0f} seconds)...")
        
        # Wait with progress updates
        while datetime.now() < target_time:
            remaining = (target_time - datetime.now()).total_seconds()
            if remaining > 60:
                print(f"  {remaining/60:.1f} minutes remaining...")
                time.sleep(30)
            else:
                time.sleep(5)
        
        print("9:40 AM reached, checking volume...")
    
    def _confirm_volume(self, symbol: str) -> bool:
        """
        Confirm that today's 9:30-9:40 volume is 5x yesterday's same window.
        
        Args:
            symbol: Ticker symbol
            
        Returns:
            True if volume threshold met, False otherwise
        """
        try:
            # Fetch 2 days of 5-minute data
            df = self.provider.get_history(symbol, interval="minute5", period="2d")
            
            if df.empty:
                print(f"    No data for {symbol}")
                return False
            
            # Ensure Datetime column
            if "Datetime" not in df.columns:
                if "Date" in df.columns:
                    df["Datetime"] = pd.to_datetime(df["Date"])
                else:
                    return False
            
            df["Datetime"] = pd.to_datetime(df["Datetime"])
            df["Date"] = df["Datetime"].dt.date
            df["Time"] = df["Datetime"].dt.time
            
            # Get unique dates
            dates = sorted(df["Date"].unique())
            
            if len(dates) < 2:
                print(f"    Not enough days of data for {symbol}")
                return False
            
            today = dates[-1]
            yesterday = dates[-2]
            
            # Filter for 9:30-9:40 window
            from datetime import time as dt_time
            start_time = dt_time(9, 30)
            end_time = dt_time(9, 40)
            
            def get_window_volume(date):
                mask = (df["Date"] == date) & (df["Time"] >= start_time) & (df["Time"] < end_time)
                return df[mask]["Volume"].sum()
            
            vol_today = get_window_volume(today)
            vol_yesterday = get_window_volume(yesterday)
            
            if vol_yesterday == 0:
                print(f"    No yesterday volume for {symbol}")
                return False
            
            ratio = vol_today / vol_yesterday
            print(f"    {symbol}: {vol_today:,.0f} vs {vol_yesterday:,.0f} ({ratio:.1f}x)")
            
            return ratio >= self.relative_volume_threshold
            
        except Exception as e:
            print(f"    Error checking volume for {symbol}: {e}")
            return False
