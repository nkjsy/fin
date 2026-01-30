"""
Premarket News Gap Trading

Scans Finviz every 60 seconds for stocks with news, confirms via Schwab,
and buys confirmed stocks immediately. Exits all positions at 9:35 AM.

Usage:
    python main_premarket.py [--live]

Timeline:
    7:00 AM  - Start scanning Finviz every 60 sec
    7:00-9:29 - Buy confirmed stocks (max 5)
    9:30-9:35 - Monitor stop losses every 5 sec
    9:35 AM  - Exit all positions
"""

import argparse
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from client import AutoRefreshSchwabClient
from broker import PaperBroker, SchwabBroker
from providers.schwab_lib import SchwabProvider
from scanner.finviz_news import FinvizNewsScanner
from live_engine import PremarketNewsEngine
from logger import get_logger, enable_file_logging
from utils import wait_until_time


ET = ZoneInfo("America/New_York")
logger = get_logger("MAIN")

# Time constants
SCAN_START = dt_time(7, 0)
ENTRY_CUTOFF = dt_time(9, 29)
STOP_LOSS_START = dt_time(9, 30)
EXIT_TIME = dt_time(9, 35)

# Interval constants
SCAN_INTERVAL = 60  # seconds
STOP_LOSS_INTERVAL = 5  # seconds

# Position limits
MAX_POSITIONS = 5


def current_time() -> dt_time:
    """Get current time in ET."""
    return datetime.now(ET).time()


def main():
    parser = argparse.ArgumentParser(description="Premarket News Gap Trading")
    parser.add_argument('--live', action='store_true', help='Use live trading (default: paper)')
    args = parser.parse_args()
    
    # Enable file logging
    enable_file_logging()
    
    logger.info("=" * 60)
    logger.info("Premarket News Gap Strategy")
    logger.info(f"Mode: {'LIVE' if args.live else 'PAPER'}")
    logger.info(f"Max positions: {MAX_POSITIONS}")
    logger.info(f"Position size: ${PremarketNewsEngine.POSITION_AMOUNT:,}")
    logger.info(f"Stop loss: {PremarketNewsEngine.STOP_LOSS_PCT*100:.0f}%")
    logger.info("=" * 60)
    
    # Initialize components
    logger.info("Initializing components...")
    client_wrapper = AutoRefreshSchwabClient()
    
    if args.live:
        broker = SchwabBroker(client_wrapper)
    else:
        broker = PaperBroker()
    
    provider = SchwabProvider(client_wrapper.client)
    scanner = FinvizNewsScanner(provider)
    engine = PremarketNewsEngine(client_wrapper, broker)
    
    try:
        # Phase 1: Scan & buy (until 9:29)
        logger.info("=" * 40)
        logger.info("PHASE 1: Scanning & buying")
        logger.info("=" * 40)
        
        scan_count = 0
        while current_time() < ENTRY_CUTOFF and len(engine.positions) < MAX_POSITIONS:
            scan_count += 1
            logger.info(f"--- Scan #{scan_count} @ {datetime.now(ET).strftime('%H:%M:%S')} ---")
            
            # Scan for confirmed stocks
            confirmed = scanner.scan(skip=set(engine.positions.keys()))
            
            # Calculate remaining slots
            remaining = MAX_POSITIONS - len(engine.positions)
            
            if confirmed and remaining > 0:
                to_buy = confirmed[:remaining]
                logger.info(f"Buying: {to_buy}")
                engine.add_positions(to_buy)
            
            # Log current positions
            if engine.positions:
                logger.info(f"Current positions ({len(engine.positions)}/{MAX_POSITIONS}): {list(engine.positions.keys())}")
            
            # Check if we've hit max positions
            if len(engine.positions) >= MAX_POSITIONS:
                logger.info("Max positions reached, stopping scan phase")
                break
            
            # Sleep until next scan
            time.sleep(SCAN_INTERVAL)
        
        # Phase 2: Stop loss monitoring (9:30 - 9:35)
        logger.info("=" * 40)
        logger.info("PHASE 2: Stop loss monitoring")
        logger.info("=" * 40)
        
        if current_time() < STOP_LOSS_START:
            wait_until_time(STOP_LOSS_START.hour, STOP_LOSS_START.minute, "market open")
        
        logger.info("Market open - monitoring stop losses")
        
        while current_time() < EXIT_TIME and engine.positions:
            engine.check_stop_losses()
            time.sleep(STOP_LOSS_INTERVAL)
        
        # Phase 3: Exit all (9:35)
        logger.info("=" * 40)
        logger.info("PHASE 3: Exiting all positions")
        logger.info("=" * 40)
        
        if current_time() < EXIT_TIME:
            wait_until_time(EXIT_TIME.hour, EXIT_TIME.minute, "exit time")
        
        engine.exit_all()
        
        # Summary
        logger.info("=" * 40)
        logger.info("SUMMARY")
        logger.info("=" * 40)
        engine.print_summary()
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        engine.exit_all()
        engine.print_summary()
    except Exception as e:
        logger.info(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
