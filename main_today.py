"""
Live Trading Entry Point

Main entry point for live day trading using Schwab API.

Usage:
    python main_today.py              # Paper trading (default)
    python main_today.py --live       # Live trading with real money
    python main_today.py --skip-scan  # Skip scanner, use provided symbols
    python main_today.py --symbols AAPL MSFT  # Trade specific symbols
"""

import argparse
import sys
import time

from providers.schwab_lib import SchwabProvider
from scanner.live_momentum import LiveMomentumScanner
from broker.paper_broker import PaperBroker
from broker.schwab_broker import SchwabBroker
from live_engine import LiveTradingEngine
from utils import wait_for_market_open
from client import AutoRefreshSchwabClient
from logger import get_logger, enable_file_logging


# Enable file logging for production (writes to logs/YYYY-MM-DD.log)
enable_file_logging()

logger = get_logger("MAIN")

# Scanner filter constants
MIN_PRICE = 2.0


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Live Day Trading with Bull Flag Strategy"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading with real money (default: paper trading)"
    )
    parser.add_argument(
        "--skip-scan",
        action="store_true",
        help="Skip the scanner and use provided symbols"
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=[],
        help="Symbols to trade (used with --skip-scan)"
    )
    parser.add_argument(
        "--initial-cash",
        type=float,
        default=100000.0,
        help="Initial cash for paper trading (default: 100000)"
    )
    return parser.parse_args()


def run_scanner(provider: SchwabProvider) -> list:
    """
    Run the live momentum scanner.
    
    Args:
        provider: SchwabProvider instance
        
    Returns:
        List of confirmed ticker symbols
    """
    scanner = LiveMomentumScanner(provider)
    return scanner.scan(min_price=MIN_PRICE)


def main():
    """Main entry point."""
    args = parse_args()
    
    logger.info("=" * 60)
    logger.info("  LIVE DAY TRADING - Bull Flag Strategy")
    logger.info("=" * 60)
    
    # Display mode
    if args.live:
        logger.warning("LIVE TRADING MODE - REAL MONEY")
        logger.info("Press Ctrl+C within 5 seconds to cancel...")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Cancelled by user")
            sys.exit(0)
    else:
        logger.info("PAPER TRADING MODE (simulated)")
    
    # Create Schwab client with auto-refresh
    try:
        client_wrapper = AutoRefreshSchwabClient()
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        logger.info("Please check your credentials in client/config.py")
        logger.info("You may need to re-authenticate via browser")
        sys.exit(1)
    
    # Create provider (uses current client)
    provider = SchwabProvider(client_wrapper.client)
    
    # Create broker
    if args.live:
        broker = SchwabBroker(client_wrapper.client)
    else:
        broker = PaperBroker(initial_cash=args.initial_cash)
    
    # Wait for market open
    wait_for_market_open(client_wrapper.client)
    
    # Get symbols to trade
    if args.skip_scan:
        if not args.symbols:
            logger.error("--skip-scan requires --symbols")
            sys.exit(1)
        symbols = args.symbols
        logger.info(f"Using provided symbols: {symbols}")
    else:
        # Run scanner
        logger.info("--- Running Live Momentum Scanner ---")
        logger.info(f"Filters: min price ${MIN_PRICE}, gap >= 3%, volume 5x")
        symbols = run_scanner(provider)
        
        if not symbols:
            logger.warning("No stocks passed the scanner criteria")
            logger.info("Try again tomorrow or use --skip-scan with specific symbols")
            sys.exit(0)
    
    logger.info(f"Trading {len(symbols)} symbols: {symbols}")
    
    # Run trading session
    logger.info("--- Starting Live Trading Session ---")
    engine = LiveTradingEngine(client_wrapper, broker, symbols)
    try:
        engine.start()
    except Exception as e:
        logger.error(f"Trading session error: {e}")
        raise
    
    logger.info("--- Session Complete ---")
    
    # Final summary for paper trading
    if not args.live and hasattr(broker, 'print_summary'):
        broker.print_summary()


if __name__ == "__main__":
    main()
