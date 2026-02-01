"""
Unit tests for premarket trading components.

Usage:
    python test_premarket.py finviz       # Test Finviz scrape timing
    python test_premarket.py confirm      # Test Schwab confirmation timing
    python test_premarket.py scan         # Test full scan timing
    python test_premarket.py history      # Test 1-min history with extended hours
    python test_premarket.py tts          # Test TTS notification
    python test_premarket.py all          # Run all tests
"""

import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from utils import speak_symbols
from finviz.screener import Screener
from scanner.finviz_news import FinvizNewsScanner, FINVIZ_NEWS_URL
from client import AutoRefreshSchwabClient
from providers.schwab_lib import SchwabProvider
from logger import get_logger


logger = get_logger("TEST")
ET = ZoneInfo("America/New_York")


def get_provider():
    """Get a SchwabProvider for testing."""
    client_wrapper = AutoRefreshSchwabClient()
    return SchwabProvider(client_wrapper.client)


def test_finviz_scrape():
    """
    Test raw Finviz scrape timing (no Schwab confirmation).
    This tests the HTTP call to Finviz only.
    """
    logger.info("=" * 60)
    logger.info("Test: Finviz Scrape Timing")
    logger.info("=" * 60)
    
    logger.info(f"URL: {FINVIZ_NEWS_URL}")
    logger.info("Fetching from Finviz...")
    
    start = time.time()
    try:
        stock_list = Screener.init_from_url(FINVIZ_NEWS_URL)
        elapsed = time.time() - start
        
        symbols = [stock["Ticker"] for stock in stock_list]
        
        logger.info(f"Finviz returned {len(symbols)} stocks in {elapsed:.2f} sec")
        logger.info(f"Symbols: {symbols}")
        
        if elapsed < 5:
            logger.info("✓ Finviz scrape is FAST - no parallelism needed")
        else:
            logger.info("⚠ Finviz scrape is SLOW - consider background thread")
            
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"Error after {elapsed:.2f} sec: {e}")


def test_confirm_timing():
    """
    Test confirmation timing for Finviz candidates.
    Fetches candidates from Finviz, then times each confirmation.
    """
    logger.info("=" * 60)
    logger.info("Test: Confirmation Timing per Symbol")
    logger.info("=" * 60)
    
    # First get candidates from Finviz
    logger.info("Fetching candidates from Finviz...")
    try:
        stock_list = Screener.init_from_url(FINVIZ_NEWS_URL)
        symbols = [stock["Ticker"] for stock in stock_list]
    except Exception as e:
        logger.error(f"Error fetching from Finviz: {e}")
        return
    
    if not symbols:
        logger.info("No candidates from Finviz - try during market hours with news")
        return
    
    logger.info(f"Found {len(symbols)} candidates: {symbols}")
    
    # Test confirmation timing for each
    provider = get_provider()
    scanner = FinvizNewsScanner(provider)
    
    logger.info("Testing confirmation timing (max 5 symbols):")
    
    total_time = 0
    for symbol in symbols[:5]:
        start = time.time()
        result = scanner._confirm_candidate(symbol)
        elapsed = time.time() - start
        total_time += elapsed
        
        status = "CONFIRMED" if result else "REJECTED"
        logger.info(f"  {symbol}: {status} in {elapsed:.2f} sec")
    
    tested = min(5, len(symbols))
    logger.info(f"Average confirmation time: {total_time/tested:.2f} sec per symbol")


def test_full_scan():
    """
    Test full scan with Schwab confirmation timing.
    This is the complete scan() call timing.
    """
    logger.info("=" * 60)
    logger.info("Test: Full Scan Timing")
    logger.info("=" * 60)
    
    provider = get_provider()
    scanner = FinvizNewsScanner(provider)
    
    logger.info("Running full scan (Finviz + parallel confirmation)...")
    
    start = time.time()
    confirmed = scanner.scan()
    elapsed = time.time() - start
    
    logger.info(f"Full scan completed in {elapsed:.2f} sec")
    logger.info(f"Confirmed symbols: {confirmed}")
    
    if elapsed < 10:
        logger.info("✓ Full scan is FAST - can run in main loop")
    else:
        logger.info("⚠ Full scan is SLOW - consider reducing scan frequency")


def test_history_extended():
    """
    Test 1-minute history with extended hours data.
    This is what the scanner uses for confirmation.
    """
    logger.info("=" * 60)
    logger.info("Test: 1-Min History with Extended Hours")
    logger.info("=" * 60)
    
    provider = get_provider()
    
    # Test with a known active stock
    test_symbols = ["SPY", "QQQ", "AAPL"]
    
    for symbol in test_symbols:
        logger.info(f"\nFetching 2d 1-min history for {symbol}...")
        
        start = time.time()
        df = provider.get_history(
            symbol,
            interval="minute1",
            period="2d",
            need_extended_hours_data=True
        )
        elapsed = time.time() - start
        
        if df.empty:
            logger.info(f"  {symbol}: No data returned ({elapsed:.2f} sec)")
            continue
        
        logger.info(f"  {symbol}: {len(df)} candles in {elapsed:.2f} sec")
        
        # Show last 5 candles
        logger.info(f"  Last 5 candles:")
        for _, row in df.tail(5).iterrows():
            dt = row["Datetime"]
            logger.info(f"    {dt}: O={row['Open']:.2f} H={row['High']:.2f} L={row['Low']:.2f} C={row['Close']:.2f} V={row['Volume']}")
        
        # Check for premarket data
        now = datetime.now(ET)
        today = now.date()
        
        df_copy = df.copy()
        df_copy["Date"] = df_copy["Datetime"].dt.date
        df_copy["Time"] = df_copy["Datetime"].dt.time
        
        today_df = df_copy[df_copy["Date"] == today]
        premarket_df = today_df[today_df["Time"] < datetime.strptime("09:30", "%H:%M").time()]
        
        logger.info(f"  Today's candles: {len(today_df)}")
        logger.info(f"  Premarket candles: {len(premarket_df)}")


def test_tts():
    """
    Test TTS notification runs in background (non-blocking).
    Verifies speak_symbols() returns immediately while audio plays.
    """    
    logger.info("=" * 60)
    logger.info("Test: TTS Background Execution")
    logger.info("=" * 60)
    
    logger.info("Testing that speak_symbols() is non-blocking...")
    logger.info("You should hear 'Confirmed: AAPL, MSFT' repeated 3 times")
    
    start = time.time()
    speak_symbols(["AAPL", "MSFT"])
    elapsed = time.time() - start
    logger.info(f"speak_symbols() returned in {elapsed:.4f} sec")
    
    # Wait for audio to finish playing (daemon thread dies when main exits)
    logger.info("Waiting 15 seconds for audio to complete...")
    time.sleep(15)
    logger.info("Done! Did you hear the announcement 3 times?")


if __name__ == "__main__":
    tests = {
        "finviz": test_finviz_scrape,
        "confirm": test_confirm_timing,
        "scan": test_full_scan,
        "history": test_history_extended,
        "tts": test_tts,
    }
    
    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        if test_name == "all":
            for name, func in tests.items():
                logger.info(f"\n{'#' * 60}")
                logger.info(f"# Running: {name}")
                logger.info(f"{'#' * 60}")
                func()
        elif test_name in tests:
            tests[test_name]()
        else:
            logger.error(f"Unknown test: {test_name}")
            logger.info(f"Available tests: {', '.join(tests.keys())}, all")
    else:
        logger.info("Usage: python test_premarket.py <test_name>")
        logger.info(f"Available tests: {', '.join(tests.keys())}, all")
