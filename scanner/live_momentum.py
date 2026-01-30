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
from logger import get_logger


logger = get_logger("SCANNER")


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
        logger.info("--- Live Momentum Scanner ---")
        
        # Step 1: Get candidates from Finviz
        logger.info("Step 1: Fetching candidates from Finviz...")
        symbols = self._get_finviz_candidates()
        
        if not symbols:
            logger.info("No candidates from Finviz.")
            return []
        
        logger.info(f"Finviz returned {len(symbols)} candidates: {symbols[:20]}{'...' if len(symbols) > 20 else ''}")
        
        # Step 2: Get quotes and filter by min_price and gap
        logger.info(f"Step 2: Filtering by min_price (${min_price}) and gap (>= {self.min_gap_percent}%)...")
        gap_stocks = self._filter_by_gap(symbols, min_price)
        
        if not gap_stocks:
            logger.info("No stocks passed gap filter.")
            return []
        
        # Take top 10 by gap
        gap_stocks = gap_stocks[:self.max_results]
        symbols = [s["symbol"] for s in gap_stocks]
        
        logger.info(f"Top {len(gap_stocks)} gap-up stocks:")
        for s in gap_stocks:
            logger.info(f"  {s['symbol']}: ${s['price']:.2f} (gap: {s['gap_percent']:+.2f}%)")
        
        # Step 3: Wait until 9:40 ET for volume data
        wait_until_time(9, 40, "checking volume")
        
        # Step 4: Confirm volume for each stock
        logger.info("Step 3: Confirming volume (5x threshold)...")
        confirmed = []
        
        for symbol in symbols:
            if self._confirm_volume(symbol):
                confirmed.append(symbol)
                logger.info(f"  ✓ {symbol} - CONFIRMED")
            else:
                logger.info(f"  ✗ {symbol} - insufficient volume")
        
        logger.info(f"Confirmed {len(confirmed)} tickers: {confirmed}")
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
            logger.error(f"Error fetching from Finviz: {e}")
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
                logger.error(f"  Error getting quote for {symbol}: {e}")
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
                logger.warning(f"    No data for {symbol}")
                return False
            
            # Ensure Datetime column
            if "Datetime" not in df.columns:
                if "Date" in df.columns:
                    df["Datetime"] = pd.to_datetime(df["Date"])
                else:
                    return False
            
            # Use shared confirm_volume (9:30-9:40 window)
            confirmed, ratio = self.confirm_volume(
                df,
                threshold=self.relative_volume_threshold,
                start_time=dt_time(9, 30),
                end_time=dt_time(9, 40)
            )
            
            if ratio is None:
                logger.warning(f"    Could not calculate rel volume for {symbol}")
                return False
            
            logger.info(f"    {symbol}: relVol={ratio:.1f}x")
            return confirmed
            
        except Exception as e:
            logger.error(f"    Error checking volume for {symbol}: {e}")
            return False
