"""
Premarket Bull Flag Trading

Scans Finviz every 60 seconds for stocks with news, confirms via Schwab,
and tracks confirmed stocks using bull flag strategy on 1-min chart.
Entry on breakout, exit on stop loss or first red bar.

Usage:
    python main_premarket.py [--live]

Timeline:
    7:00 AM    - Start scanning Finviz every 60 sec
    7:00-10:00 - Scan, track patterns, trade breakouts
    10:00 AM   - Stop new entries (configurable via ENTRY_CUTOFF)
    After 10:00 - Continue managing existing positions until all closed
    4:00 PM    - Market close, force stop

Strategy:
    - Scanner confirms stocks with news, 3% gain, 5x volume
    - Track at most 3 symbols with bull flag strategy
    - Replay last 10 min of 1-min candles when adding symbol
    - Entry on bull flag breakout
    - Exit on stop loss or first red bar
    - If pattern fails (PULLBACK -> SCANNING), stop tracking
"""

import argparse
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from client import AutoRefreshSchwabClient
from broker import PaperBroker, SchwabBroker
from providers.schwab_lib import SchwabProvider
from scanner.finviz_news import FinvizNewsScanner
from live_engine import LiveTradingEngine
from logger import get_logger, enable_file_logging
from utils import speak_symbols


ET = ZoneInfo("America/New_York")
logger = get_logger("MAIN")

# Time constants
ENTRY_CUTOFF = dt_time(10, 0)  # Stop new entries after this time
MARKET_CLOSE = dt_time(16, 0)

# Interval constants
SCAN_INTERVAL = 60  # seconds

# Position limits
MAX_SYMBOLS = 3
POSITION_AMOUNT = 10000  # $10k per position
REPLAY_MINUTES = 3  # minutes of history to replay when adding symbol


def main():
    parser = argparse.ArgumentParser(description="Premarket Bull Flag Trading")
    parser.add_argument('--live', action='store_true', help='Use live trading (default: paper)')
    args = parser.parse_args()
    
    # Enable file logging
    enable_file_logging()
    
    logger.info("=" * 60)
    logger.info("Premarket Bull Flag Strategy")
    logger.info(f"Mode: {'LIVE' if args.live else 'PAPER'}")
    logger.info(f"Max tracked symbols: {MAX_SYMBOLS}")
    logger.info(f"Position size: ${POSITION_AMOUNT:,}")
    logger.info(f"Entry cutoff: {ENTRY_CUTOFF.strftime('%H:%M')} ET")
    logger.info(f"Candle interval: 1 min")
    logger.info(f"Extended hours: True")
    logger.info("=" * 60)
    
    # Initialize components
    logger.info("Initializing components...")
    client_wrapper = AutoRefreshSchwabClient()
    
    if args.live:
        broker = SchwabBroker(client_wrapper)
    else:
        broker = PaperBroker()
    
    provider = SchwabProvider(client_wrapper)
    scanner = FinvizNewsScanner(provider)
    
    # Create engine with 1-min candles and extended hours
    engine = LiveTradingEngine(
        client_wrapper=client_wrapper,
        broker=broker,
        symbols=[],  # Start with no symbols, add dynamically
        candle_interval=1,
        extended_hours=True,
        position_amount=POSITION_AMOUNT,
        max_symbols=MAX_SYMBOLS,
        remove_symbol=True  # Remove symbols after pattern fail, position close, or scanning timeout
    )
    
    try:
        logger.info("=" * 40)
        logger.info("Starting combined scan + trade loop")
        logger.info("=" * 40)
        
        engine.running = True
        last_scan_time = 0
        last_candle_slot = engine._datetime_to_slot(datetime.now(ET)) - 1
        
        while engine.running:
            now_et = datetime.now(ET)
            now_time = now_et.time()
            
            # Check market close
            if now_time >= MARKET_CLOSE:
                logger.info("Market closed - stopping")
                break
            
            # Check if past entry cutoff and no symbols being tracked
            past_cutoff = now_time >= ENTRY_CUTOFF
            
            if past_cutoff and not engine.strategies:
                logger.info("Past entry cutoff and no symbols tracked - stopping")
                break
            
            current_ts = time.time()
            
            # === SCAN PHASE (every 60 sec, only before cutoff) ===
            if not past_cutoff and current_ts - last_scan_time >= SCAN_INTERVAL:
                last_scan_time = current_ts
                
                # Only scan if we have capacity
                if len(engine.strategies) < MAX_SYMBOLS:
                    logger.info("--- Scanning ---")
                    
                    # Skip symbols already being tracked
                    skip_symbols = set(engine.strategies.keys())
                    confirmed = scanner.scan(skip=skip_symbols)
                    
                    # Notify with sound if stocks confirmed
                    if confirmed:
                        speak_symbols(confirmed)
                    
                    # Add confirmed symbols
                    for symbol in confirmed:
                        if len(engine.strategies) >= MAX_SYMBOLS:
                            break
                        engine.add_symbol(symbol, replay_minutes=REPLAY_MINUTES)
                    
                    # Log current tracking state
                    if engine.strategies:
                        states = {s: st.state.value for s, st in engine.strategies.items()}
                        logger.info(f"Tracking ({len(engine.strategies)}/{MAX_SYMBOLS}): {states}")
            
            # === CANDLE PHASE (on interval boundary) ===
            current_slot = engine._datetime_to_slot(now_et)
            if current_slot != last_candle_slot:
                engine._process_candles()
                last_candle_slot = current_slot
            
            # === QUOTE PHASE (fast polling when needed) ===
            if engine._needs_realtime_polling():
                engine._check_realtime_triggers()
                time.sleep(engine.REALTIME_POLL_INTERVAL)
            else:
                # Sleep until next interval boundary or next scan
                candle_poll_interval = engine.candle_interval * 60
                seconds_into_slot = (now_et.minute % engine.candle_interval) * 60 + now_et.second
                time_to_next_candle = candle_poll_interval - seconds_into_slot + 5
                time_to_next_scan = max(0, SCAN_INTERVAL - (current_ts - last_scan_time))
                
                sleep_time = min(time_to_next_candle, time_to_next_scan)
                time.sleep(max(1, sleep_time))
        
        # Summary
        logger.info("=" * 40)
        logger.info("SUMMARY")
        logger.info("=" * 40)
        if hasattr(broker, 'print_summary'):
            broker.print_summary()
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        if hasattr(broker, 'print_summary'):
            broker.print_summary()
    except Exception as e:
        logger.info(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
