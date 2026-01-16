"""
Live Momentum Scanner

Scans for top movers at market open using Schwab API.
Confirms volume 9:30-9:40 against previous day.
"""

import time
from datetime import datetime, timedelta
import pandas as pd
import httpx
from zoneinfo import ZoneInfo
from datetime import time as dt_time
from scanner.base import BaseScanner
from providers.schwab_lib import SchwabProvider
from schwab.client import Client
from utils import wait_until_time


class LiveMomentumScanner(BaseScanner):
    """
    Live scanner that uses Schwab's market movers API and confirms with volume.
    
    Workflow:
    1. At 9:30, fetch top 10 up movers from all indices (NASDAQ, NYSE, $DJI, $COMPX, $SPX)
    2. Apply initial filters (price range, float shares)
    3. Wait until 9:40 to collect volume data
    4. Confirm each mover has 5x relative volume vs previous day's same time window
    5. Return confirmed tickers
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
        # All available indices
        self.indices = ["NASDAQ", "NYSE", "$DJI", "$COMPX", "$SPX"]
    
    def scan(self, wait_for_volume: bool = True, min_price: float = 0, max_price: float = float('inf'), max_float: int = float('inf'), **kwargs) -> list:
        """
        Scan for top movers with volume confirmation.
        
        Args:
            wait_for_volume: If True, waits until 9:40 ET for volume confirmation.
                           If False, returns movers immediately (for testing).
            min_price: Minimum price filter (default: 0)
            max_price: Maximum price filter (default: inf)
            max_float: Maximum float shares filter (default: inf)
            
        Returns:
            List of confirmed ticker symbols
        """
        print("--- Live Momentum Scanner ---")
        
        # Step 1: Get top 10 up movers from all indices
        print("Fetching top 10 up movers from all indices...")
        movers = self._get_movers_from_all_indices()
        
        if not movers:
            print("No movers found.")
            return []
        
        symbols = [m["symbol"] for m in movers]
        print(f"Top movers from all indices: {symbols}")
        
        for m in movers:
            print(f"  {m['symbol']}: ${m['lastPrice']:.2f} ({m['netPercentChange']:+.2f}%)")
        
        # Step 2: Apply initial filters (price range, float shares)
        print(f"\nApplying initial filters (price: ${min_price}-${max_price}, max float: {max_float:,})...")
        filtered_movers = self._apply_initial_filters(movers, min_price, max_price, max_float)
        
        if not filtered_movers:
            print("No movers passed initial filters.")
            return []
        
        symbols = [m["symbol"] for m in filtered_movers]
        print(f"Movers after filtering: {symbols}")
        
        if not wait_for_volume:
            # Skip volume confirmation (for testing)
            return symbols
        
        # Step 3: Wait until 9:40 ET for volume data
        wait_until_time(9, 40, "checking volume")
        
        # Step 4: Confirm volume for each mover
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
    
    def _get_movers_from_all_indices(self) -> list:
        """
        Fetch top 10 movers from each index and combine them.
        
        Returns:
            List of unique movers sorted by percent change
        """
        all_movers = []
        seen_symbols = set()
        
        for index in self.indices:
            try:
                movers = self.provider.get_movers(index=index, direction="up", count=10)
                for m in movers:
                    symbol = m["symbol"]
                    if symbol not in seen_symbols:
                        seen_symbols.add(symbol)
                        m["source_index"] = index
                        all_movers.append(m)
                print(f"  {index}: found {len(movers)} movers")
            except Exception as e:
                print(f"  {index}: error fetching movers - {e}")
        
        # Sort by percent change descending
        all_movers.sort(key=lambda x: x.get("netPercentChange", 0), reverse=True)
        
        print(f"Combined {len(all_movers)} unique movers from all indices")
        return all_movers
    
    def _apply_initial_filters(self, movers: list, min_price: float, max_price: float, max_float: int) -> list:
        """
        Apply price range and float shares filters to movers.
        
        Args:
            movers: List of mover dicts
            min_price: Minimum price
            max_price: Maximum price
            max_float: Maximum float shares
            
        Returns:
            Filtered list of movers
        """
        filtered = []
        
        # Get all symbols for batch fundamentals lookup
        symbols = [m["symbol"] for m in movers]
        fundamentals = self._get_fundamentals(symbols) if max_float < float('inf') else {}
        
        for m in movers:
            symbol = m["symbol"]
            price = m.get("lastPrice", 0)
            
            # Check price range first (we already have price from movers data)
            if price < min_price or price > max_price:
                print(f"  ✗ {symbol}: price ${price:.2f} outside range ${min_price}-${max_price}")
                continue
            
            # Check float shares if max_float filter is applied
            if max_float < float('inf'):
                float_shares = fundamentals.get(symbol)
                
                if float_shares is None:
                    print(f"  ? {symbol}: no float data available, skipping")
                    continue
                
                if float_shares > max_float:
                    print(f"  ✗ {symbol}: float {float_shares:,.0f} > max {max_float:,}")
                    continue
                
                print(f"  ✓ {symbol}: price ${price:.2f}, float {float_shares:,.0f}")
            else:
                print(f"  ✓ {symbol}: price ${price:.2f}")
            
            filtered.append(m)
        
        return filtered
    
    def _get_fundamentals(self, symbols: list) -> dict:
        """
        Get fundamental data (float shares) for symbols using Schwab API.
        
        Args:
            symbols: List of ticker symbols
            
        Returns:
            Dict mapping symbol to float shares
        """
        result = {}
        try:
            resp = self.provider.client.get_instruments(
                symbols, 
                Client.Instrument.Projection.FUNDAMENTAL
            )
            
            if resp.status_code != httpx.codes.OK:
                print(f"    Error fetching fundamentals: {resp.status_code}")
                return result
            
            data = resp.json()
            
            # Parse response - structure: {"instruments": [{"symbol": ..., "fundamental": {...}}]}
            instruments = data.get("instruments", [])
            for inst in instruments:
                symbol = inst.get("symbol")
                fundamental = inst.get("fundamental", {})
                float_shares = fundamental.get("marketCapFloat")  # Float shares in millions
                if float_shares is not None:
                    # Convert from millions to actual shares
                    result[symbol] = int(float_shares * 1_000_000)
                    
        except Exception as e:
            print(f"    Error fetching fundamentals: {e}")
        
        return result
    
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
