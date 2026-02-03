"""
Finviz News Scanner

Scans Finviz for stocks with recent news and confirms via Schwab API.
Returns only confirmed symbols ready to buy.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Optional, Set

import pandas as pd
from finviz.screener import Screener
from finviz.helper_functions.error_handling import NoResults

from scanner.base import BaseScanner, ET
from providers.schwab_lib import SchwabProvider
from logger import get_logger


logger = get_logger("SCANNER")

# Finviz screener: news in last 5 min, price <= $50, float < 100M
FINVIZ_NEWS_URL = "https://finviz.com/screener.ashx?v=111&f=news_date_prevminutes5%2Csh_float_u100%2Csh_price_u50&ft=6"


class FinvizNewsScanner(BaseScanner):
    """
    Scans Finviz for stocks with news, confirms via Schwab API.
    Returns only confirmed symbols ready to buy.
    
    Confirmation criteria:
    - Gain >= 3% vs previous day close
    - Relative volume >= 5x vs yesterday same-time premarket
    - At least 3 candles in last 3 min (confirms active trading)
    """
    
    # Confirmation thresholds
    MIN_GAIN_PCT = 0.03  # 3% minimum gain vs prev day close
    MIN_REL_VOLUME = 5.0  # 5x relative volume
    MIN_CANDLES = 3  # Require 3 candles in last 3 min
    
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
        except NoResults:
            # No stocks match the filter criteria (normal during off-hours or quiet periods)
            logger.info("Finviz: No stocks with news in last 5 min matching criteria")
            return set()
        except Exception as e:
            logger.error(f"Error scraping Finviz: {e}")
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
            # Fetch 2 days of 1-minute data with extended hours and previous close
            df, prev_close = self.provider.get_history(
                symbol,
                interval="minute1",
                period="2d",
                need_extended_hours_data=True,
                need_previous_close=True
            )
            
            if df.empty:
                logger.info(f"  {symbol}: No data")
                return None
            
            if prev_close <= 0:
                logger.info(f"  {symbol}: No previous close data")
                return None
            
            # Keep last candle even if incomplete - we want recent activity
            # Check for minimum candles in last 3 min (confirms active trading after news)
            if len(df) < self.MIN_CANDLES:
                logger.info(f"  {symbol}: Only {len(df)} candles total, need {self.MIN_CANDLES}")
                return None
            
            # Get last 3 candles
            last_candles = df.iloc[-self.MIN_CANDLES:]
            
            # Verify candles are within last 3 minutes (allow gaps but must be recent)
            now = datetime.now(ET)
            oldest_allowed = now - timedelta(minutes=self.MIN_CANDLES)
            oldest_candle_time = last_candles["Datetime"].iloc[0]
            if oldest_candle_time < oldest_allowed:
                logger.info(f"  {symbol}: No candles in last {self.MIN_CANDLES} min")
                return None
            
            # Get current price
            current_price = last_candles["Close"].iloc[-1]
            
            # Filter by minimum price ($2)
            if current_price <= 2:
                logger.info(f"  {symbol}: Price ${current_price:.2f} <= $2")
                return None
            
            # Calculate gain % vs previous day close
            gain_pct = (current_price - prev_close) / prev_close
            
            if gain_pct < self.MIN_GAIN_PCT:
                logger.info(f"  {symbol}: Gain {gain_pct*100:.1f}% < {self.MIN_GAIN_PCT*100}% (prev close ${prev_close:.2f} -> ${current_price:.2f})")
                return None
            
            # Confirm relative volume vs yesterday same-time (uses base class method)
            confirmed, rel_vol = self.confirm_volume(df, threshold=self.MIN_REL_VOLUME)
            
            if not confirmed:
                logger.info(f"  {symbol}: RelVol {rel_vol:.1f}x < {self.MIN_REL_VOLUME}x" if rel_vol else f"  {symbol}: Could not calculate rel volume")
                return None
            
            logger.info(f"  {symbol}: CONFIRMED (prev ${prev_close:.2f} -> ${current_price:.2f}, gain={gain_pct*100:.1f}%, relVol={rel_vol:.1f}x)")
            return symbol
            
        except Exception as e:
            logger.info(f"  {symbol}: Error - {e}")
            return None
