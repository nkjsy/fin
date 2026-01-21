"""
Live Momentum Scanner

Scans for gap-up stocks using Finviz screener + Schwab quotes.
Confirms volume 9:30-9:40 against previous day.
"""

import pandas as pd
from datetime import time as dt_time
from tqdm import tqdm
from finviz.screener import Screener

from scanner.base import BaseScanner
from providers.schwab_lib import SchwabProvider
from utils import wait_until_time


# Finviz screener URL: price < $50, float < 100M, news since yesterday
FINVIZ_SCREENER_URL = "https://finviz.com/screener.ashx?v=111&f=sh_float_u100,sh_price_u50,news_date_sinceyesterdayafter"


class LiveMomentumScanner(BaseScanner):
    """
    Live scanner using Finviz for initial candidates + Schwab for gap/volume confirmation.
    
    Workflow:
    1. Fetch candidates from Finviz (price < $50, float < 100M, recent news)
    2. Get quotes from Schwab, filter by min_price and gap >= 3%
    3. Take top 10 by gap percentage
    4. Wait until 9:40 to confirm 5x relative volume
    5. Return confirmed tickers
    """
    
    def __init__(self, provider: SchwabProvider, data_dir: str = "data"):
        super().__init__(data_dir)
        self.provider = provider
        self.relative_volume_threshold = 5.0  # 5x previous day volume
        self.min_gap_percent = 3.0  # Minimum gap up percentage
        self.max_results = 10  # Max symbols after gap filter
    
    def scan(self, min_price: float = 2.0, **kwargs) -> list:
        """
        Scan for gap-up stocks with volume confirmation.
        
        Args:
            min_price: Minimum price filter (default: $2)
            
        Returns:
            List of confirmed ticker symbols
        """
        print("--- Live Momentum Scanner ---")
        
        # Step 1: Get candidates from Finviz
        print("\nStep 1: Fetching candidates from Finviz...")
        symbols = self._get_finviz_candidates()
        
        if not symbols:
            print("No candidates from Finviz.")
            return []
        
        print(f"Finviz returned {len(symbols)} candidates: {symbols[:20]}{'...' if len(symbols) > 20 else ''}")
        
        # Step 2: Get quotes and filter by min_price and gap
        print(f"\nStep 2: Filtering by min_price (${min_price}) and gap (>= {self.min_gap_percent}%)...")
        gap_stocks = self._filter_by_gap(symbols, min_price)
        
        if not gap_stocks:
            print("No stocks passed gap filter.")
            return []
        
        # Take top 10 by gap
        gap_stocks = gap_stocks[:self.max_results]
        symbols = [s["symbol"] for s in gap_stocks]
        
        print(f"Top {len(gap_stocks)} gap-up stocks:")
        for s in gap_stocks:
            print(f"  {s['symbol']}: ${s['price']:.2f} (gap: {s['gap_percent']:+.2f}%)")
        
        # Step 3: Wait until 9:40 ET for volume data
        wait_until_time(9, 40, "checking volume")
        
        # Step 4: Confirm volume for each stock
        print("\nStep 3: Confirming volume (5x threshold)...")
        confirmed = []
        
        for symbol in symbols:
            if self._confirm_volume(symbol):
                confirmed.append(symbol)
                print(f"  ✓ {symbol} - CONFIRMED")
            else:
                print(f"  ✗ {symbol} - insufficient volume")
        
        print(f"\nConfirmed {len(confirmed)} tickers: {confirmed}")
        return confirmed
    
    def _get_finviz_candidates(self) -> list:
        """
        Fetch stock symbols from Finviz screener.
        
        Returns:
            List of ticker symbols
        """
        try:
            stock_list = Screener.init_from_url(FINVIZ_SCREENER_URL)
            return [stock["Ticker"] for stock in stock_list]
        except Exception as e:
            print(f"Error fetching from Finviz: {e}")
            return []
    
    def _filter_by_gap(self, symbols: list, min_price: float) -> list:
        """
        Filter symbols by minimum price and gap percentage using Schwab quotes.
        
        Args:
            symbols: List of ticker symbols
            min_price: Minimum price threshold
            
        Returns:
            List of dicts with symbol, price, gap_percent - sorted by gap descending
        """
        gap_stocks = []
        
        for symbol in tqdm(symbols):
            try:
                resp = self.provider.client.get_quote(symbol)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                quote = data.get(symbol, {}).get("quote", {})
            except Exception as e:
                print(f"  Error getting quote for {symbol}: {e}")
                continue
            
            price = quote.get("lastPrice", 0)
            open_price = quote.get("openPrice", 0)
            close_price = quote.get("closePrice", 0)
            
            # Filter by min_price
            if price < min_price:
                continue
            
            # Calculate gap: (today's open - yesterday's close) / yesterday's close
            if close_price <= 0 or open_price <= 0:
                continue
            
            gap_percent = (open_price - close_price) / close_price * 100
            
            # Filter by gap percentage
            if gap_percent < self.min_gap_percent:
                continue
            
            gap_stocks.append({
                "symbol": symbol,
                "price": price,
                "gap_percent": gap_percent
            })
        
        # Sort by gap descending
        gap_stocks.sort(key=lambda x: x["gap_percent"], reverse=True)
        
        return gap_stocks
    
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
            
            # Extract date and time components
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
