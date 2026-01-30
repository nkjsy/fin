"""
Finviz News Scanner

Scans Finviz for stocks with recent news and confirms via Schwab API.
Returns only confirmed symbols ready to buy.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Set

import pandas as pd
from finviz.screener import Screener

from scanner.base import BaseScanner, ET
from providers.schwab_lib import SchwabProvider
from logger import get_logger


logger = get_logger("SCANNER")

# Finviz screener: news in last 5 min, price <= $50, float < 100M
FINVIZ_NEWS_URL = "https://finviz.com/screener.ashx?v=111&f=news_date_prevminutes5,sh_float_u100,sh_price_u50"


class FinvizNewsScanner(BaseScanner):
    """
    Scans Finviz for stocks with news, confirms via Schwab API.
    Returns only confirmed symbols ready to buy.
    
    Confirmation criteria:
    - Gain >= 3% vs price 10 minutes ago
    - Relative volume >= 5x vs yesterday same-time premarket
    - 10 consecutive 1-minute candles in last 10 min (EXTO eligible)
    """
    
    # Confirmation thresholds
    MIN_GAIN_PCT = 0.03  # 3% minimum gain vs 10 min ago
    MIN_REL_VOLUME = 5.0  # 5x relative volume
    MIN_CANDLES = 10  # Require 10 consecutive candles in last 10 min
    
    # Thread pool size for parallel confirmation (matches max positions)
    MAX_WORKERS = 5
    
    def __init__(self, provider: SchwabProvider, data_dir: str = "data"):
        """
        Initialize FinvizNewsScanner.
        
        Args:
            provider: SchwabProvider for API calls
            data_dir: Data directory (inherited from BaseScanner)
        """
        super().__init__(data_dir)
        self.provider = provider
        self.confirming: Set[str] = set()  # Track in-flight confirmations
    
    def scan(self, skip: Set[str] = None) -> List[str]:
        """
        Scan Finviz for stocks with news and confirm via Schwab.
        
        Args:
            skip: Set of symbols to skip (e.g., already in position)
            
        Returns:
            List of confirmed symbols ready to buy
        """
        skip = skip or set()
        
        # Step 1: Scrape Finviz for candidates
        candidates = self._scrape_finviz()
        
        if not candidates:
            return []
        
        logger.info(f"Finviz returned {len(candidates)} candidates")
        
        # Step 2: Filter out symbols to skip or currently confirming (set operations)
        to_confirm = candidates - skip - self.confirming
        
        # Limit to MAX_WORKERS candidates
        if len(to_confirm) > self.MAX_WORKERS:
            to_confirm = set(list(to_confirm)[:self.MAX_WORKERS])
        
        if not to_confirm:
            logger.info("No new candidates to confirm")
            return []
        
        logger.info(f"Confirming {len(to_confirm)} candidates: {to_confirm}")
        
        # Step 3: Mark as confirming (set union)
        self.confirming |= to_confirm
        
        try:
            # Step 4: Parallel confirmation
            confirmed = self._confirm_parallel(to_confirm)
            
            if confirmed:
                logger.info(f"Confirmed: {confirmed}")
            else:
                logger.info("No candidates confirmed")
            
            return confirmed
            
        finally:
            # Step 5: Clean up confirming set (set difference)
            self.confirming -= to_confirm
    
    def _scrape_finviz(self) -> Set[str]:
        """
        Scrape Finviz for stocks with recent news.
        
        Returns:
            Set of ticker symbols
        """
        try:
            stock_list = Screener.init_from_url(FINVIZ_NEWS_URL)
            symbols = {stock["Ticker"] for stock in stock_list}
            return symbols
        except Exception as e:
            logger.info(f"Error scraping Finviz: {e}")
            return set()
    
    def _confirm_parallel(self, symbols: Set[str]) -> List[str]:
        """
        Confirm candidates in parallel using ThreadPoolExecutor.
        
        Args:
            symbols: Set of symbols to confirm
            
        Returns:
            List of confirmed symbols
        """
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            results = executor.map(self._confirm_candidate, symbols)
        
        return list(filter(None, results))
    
    def _confirm_candidate(self, symbol: str) -> Optional[str]:
        """
        Fetch history and confirm gain % and relative volume.
        
        Args:
            symbol: Ticker symbol
            
        Returns:
            Symbol if confirmed, None otherwise
        """
        try:
            # Fetch 2 days of 1-minute data with extended hours
            df = self.provider.get_history(
                symbol,
                interval="minute1",
                period="2d",
                need_extended_hours_data=True
            )
            
            if df.empty:
                logger.info(f"  {symbol}: No data")
                return None
            
            # Drop the last candle (may be incomplete)
            df = df.iloc[:-1]
            
            # Check for 10 consecutive candles at least 
            if len(df) < self.MIN_CANDLES:
                logger.info(f"  {symbol}: Only {len(df)} candles total, need {self.MIN_CANDLES}")
                return None
            
            # Get last 10 candles
            last_10 = df.iloc[-self.MIN_CANDLES:]
            
            # Verify consecutive 1-minute intervals in last 10 minutes (EXTO eligible)
            time_diffs = last_10["Datetime"].diff().iloc[1:]  # Skip first NaT
            expected_diff = pd.Timedelta(minutes=1)
            if not all(time_diffs == expected_diff):
                logger.info(f"  {symbol}: Candles not consecutive in last 10 min")
                return None
            
            # Get current price and price 10 min ago
            current_price = last_10["Close"].iloc[-1]
            price_10min_ago = last_10["Close"].iloc[0]
            
            # Filter by minimum price ($2)
            if current_price <= 2:
                logger.info(f"  {symbol}: Price ${current_price:.2f} <= $2")
                return None
            
            # Calculate gain % vs 10 minutes ago
            gain_pct = (current_price - price_10min_ago) / price_10min_ago
            
            if gain_pct < self.MIN_GAIN_PCT:
                logger.info(f"  {symbol}: Gain {gain_pct*100:.1f}% < {self.MIN_GAIN_PCT*100}% (${price_10min_ago:.2f} -> ${current_price:.2f})")
                return None
            
            # Confirm relative volume vs yesterday same-time (uses base class method)
            confirmed, rel_vol = self.confirm_volume(df, threshold=self.MIN_REL_VOLUME)
            
            if not confirmed:
                logger.info(f"  {symbol}: RelVol {rel_vol:.1f}x < {self.MIN_REL_VOLUME}x" if rel_vol else f"  {symbol}: Could not calculate rel volume")
                return None
            
            logger.info(f"  {symbol}: CONFIRMED (${price_10min_ago:.2f} -> ${current_price:.2f}, gain={gain_pct*100:.1f}%, relVol={rel_vol:.1f}x)")
            return symbol
            
        except Exception as e:
            logger.info(f"  {symbol}: Error - {e}")
            return None
