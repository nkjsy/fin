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
import asyncio
from datetime import datetime, time as dt_time
import sys

from schwab.auth import easy_client

import config
from providers.schwab_lib import SchwabProvider
from scanner.live_momentum import LiveMomentumScanner
from broker.paper_broker import PaperBroker
from broker.schwab_broker import SchwabBroker
from live_engine import LiveTradingEngine


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
        "--no-wait",
        action="store_true",
        help="Don't wait for volume confirmation (for testing)"
    )
    parser.add_argument(
        "--initial-cash",
        type=float,
        default=100000.0,
        help="Initial cash for paper trading (default: 100000)"
    )
    return parser.parse_args()


def wait_for_market_open():
    """Wait until market is open (9:30 AM ET)."""
    market_open = dt_time(9, 30)
    now = datetime.now().time()
    
    if now >= market_open:
        print("Market is already open")
        return
    
    print(f"Waiting for market open at 9:30 AM...")
    
    while datetime.now().time() < market_open:
        remaining = datetime.combine(
            datetime.today(), market_open
        ) - datetime.now()
        
        minutes = remaining.seconds // 60
        if minutes > 0:
            print(f"  {minutes} minutes until market open...")
        
        # Sleep in intervals
        sleep_time = min(60, remaining.seconds)
        if sleep_time > 0:
            import time
            time.sleep(sleep_time)
    
    print("Market is open!")


def create_client():
    """Create authenticated Schwab client."""
    print("Authenticating with Schwab...")
    
    client = easy_client(
        api_key=config.SCHWAB_API_KEY,
        app_secret=config.SCHWAB_APP_SECRET,
        callback_url=config.SCHWAB_CALLBACK_URL,
        token_path=config.SCHWAB_TOKEN_PATH
    )
    
    print("Authentication successful")
    return client


def run_scanner(provider: SchwabProvider, wait_for_volume: bool = True) -> list:
    """
    Run the live momentum scanner.
    
    Args:
        provider: SchwabProvider instance
        wait_for_volume: Whether to wait for 9:40 volume confirmation
        
    Returns:
        List of confirmed ticker symbols
    """
    scanner = LiveMomentumScanner(provider)
    return scanner.scan(wait_for_volume=wait_for_volume)


async def run_trading_session(client, broker, symbols: list):
    """
    Run the live trading session.
    
    Args:
        client: Authenticated Schwab client
        broker: IBroker implementation
        symbols: Symbols to trade
    """
    engine = LiveTradingEngine(client, broker, symbols)
    
    try:
        await engine.run_until_market_close()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    finally:
        await engine.stop()


def main():
    """Main entry point."""
    args = parse_args()
    
    print("=" * 60)
    print("  LIVE DAY TRADING - Bull Flag Strategy")
    print("=" * 60)
    
    # Display mode
    if args.live:
        print("\n⚠️  LIVE TRADING MODE - REAL MONEY ⚠️")
        print("Press Ctrl+C within 5 seconds to cancel...")
        try:
            import time
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nCancelled by user")
            sys.exit(0)
    else:
        print("\n📝 PAPER TRADING MODE (simulated)")
    
    print()
    
    # Create Schwab client
    try:
        client = create_client()
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        print("\nPlease check your credentials in config.py")
        print("You may need to re-authenticate via browser")
        sys.exit(1)
    
    # Create provider
    provider = SchwabProvider(client)
    
    # Create broker
    if args.live:
        broker = SchwabBroker(client)
    else:
        broker = PaperBroker(initial_cash=args.initial_cash)
    
    # Get symbols to trade
    if args.skip_scan:
        if not args.symbols:
            print("❌ Error: --skip-scan requires --symbols")
            sys.exit(1)
        symbols = args.symbols
        print(f"Using provided symbols: {symbols}")
    else:
        # Wait for market open
        wait_for_market_open()
        
        # Run scanner
        print("\n--- Running Live Momentum Scanner ---")
        symbols = run_scanner(provider, wait_for_volume=not args.no_wait)
        
        if not symbols:
            print("\n❌ No stocks passed the scanner criteria")
            print("Try again tomorrow or use --skip-scan with specific symbols")
            sys.exit(0)
    
    print(f"\n📊 Trading {len(symbols)} symbols: {symbols}")
    
    # Run trading session
    print("\n--- Starting Live Trading Session ---")
    try:
        asyncio.run(run_trading_session(client, broker, symbols))
    except Exception as e:
        print(f"\n❌ Trading session error: {e}")
        raise
    
    print("\n--- Session Complete ---")
    
    # Final summary for paper trading
    if not args.live and hasattr(broker, 'print_summary'):
        broker.print_summary()


if __name__ == "__main__":
    main()
