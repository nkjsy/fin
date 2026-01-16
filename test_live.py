"""
Unit tests for live trading components (Schwab API).

Usage:
    python test_live.py fundamentals  # Test fundamentals API
    python test_live.py movers        # Test movers API
    python test_live.py scanner       # Test live momentum scanner
    python test_live.py all           # Run all tests
"""

import sys
import httpx
from schwab.client import Client
from utils import create_client
from datetime import datetime
from providers.schwab_lib import SchwabProvider
from scanner.live_momentum import LiveMomentumScanner


def test_schwab_fundamentals():
    """
    Test Schwab API fundamentals endpoint to discover available fields.
    Run this to see what fundamental data Schwab provides.
    """
    print("=" * 60)
    print("Testing Schwab Fundamentals API")
    print("=" * 60)
    
    # Create client
    client = create_client()
    
    # Test symbols
    test_symbols = ["AAPL", "MSFT", "TSLA"]
    
    print(f"\nFetching fundamentals for: {test_symbols}")
    
    resp = client.get_instruments(
        test_symbols,
        Client.Instrument.Projection.FUNDAMENTAL
    )
    
    if resp.status_code != httpx.codes.OK:
        print(f"Error: {resp.status_code}")
        print(resp.text)
        return
    
    data = resp.json()
    
    # Print raw response structure
    print(f"\nResponse keys: {data.keys()}")
    
    instruments = data.get("instruments", [])
    print(f"Number of instruments: {len(instruments)}")
    
    for inst in instruments:
        symbol = inst.get("symbol")
        fundamental = inst.get("fundamental", {})
        
        print(f"\n{'='*40}")
        print(f"Symbol: {symbol}")
        print(f"{'='*40}")
        
        # Print all fundamental fields
        print(f"\nAvailable fundamental fields ({len(fundamental)} total):")
        for key in sorted(fundamental.keys()):
            value = fundamental[key]
            print(f"  {key}: {value}")
        
        # Specifically look for float-related fields
        print(f"\nFloat-related fields:")
        float_keywords = ['float', 'shares', 'outstanding', 'market']
        for key, value in fundamental.items():
            if any(kw in key.lower() for kw in float_keywords):
                print(f"  {key}: {value}")


def test_schwab_movers():
    """
    Test Schwab API movers endpoint.
    """
    print("=" * 60)
    print("Testing Schwab Movers API")
    print("=" * 60)
    
    client = create_client()
    
    indices = ["NASDAQ", "NYSE", "$DJI", "$COMPX", "$SPX"]
    
    for index in indices:
        print(f"\n--- {index} ---")
        try:
            index_map = {
                "NASDAQ": Client.Movers.Index.NASDAQ,
                "NYSE": Client.Movers.Index.NYSE,
                "$DJI": Client.Movers.Index.DJI,
                "$COMPX": Client.Movers.Index.COMPX,
                "$SPX": Client.Movers.Index.SPX,
            }
            index_enum = index_map.get(index)
            
            resp = client.get_movers(
                index_enum,
                sort_order=Client.Movers.SortOrder.PERCENT_CHANGE_UP
            )
            
            if resp.status_code != httpx.codes.OK:
                print(f"  Error: {resp.status_code}")
                continue
            
            data = resp.json()
            movers = data.get("screeners", [])[:5]  # Top 5
            
            for m in movers:
                symbol = m.get("symbol", "?")
                price = m.get("lastPrice", 0)
                pct = m.get("netPercentChangeInDouble", 0)
                print(f"  {symbol}: ${price:.2f} ({pct:+.2f}%)")
                
        except Exception as e:
            print(f"  Error: {e}")


def test_live_scanner():
    """
    Test the LiveMomentumScanner without waiting for volume.
    """
    print("=" * 60)
    print("Testing Live Momentum Scanner")
    print("=" * 60)
    
    client = create_client()
    provider = SchwabProvider(client)
    scanner = LiveMomentumScanner(provider)
    
    # Run scanner without volume wait (for testing)
    symbols = scanner.scan(
        wait_for_volume=False,
        min_price=2.0,
        max_price=50.0,
        max_float=100_000_000
    )
    
    print(f"\nScanner returned {len(symbols)} symbols: {symbols}")


def test_schwab_quotes():
    """
    Test Schwab API quotes endpoint.
    """
    print("=" * 60)
    print("Testing Schwab Quotes API")
    print("=" * 60)
    
    client = create_client()
    
    test_symbols = ["AAPL", "MSFT", "TSLA", "NVDA"]
    
    print(f"\nFetching quotes for: {test_symbols}")
    
    resp = client.get_quotes(test_symbols)
    
    if resp.status_code != httpx.codes.OK:
        print(f"Error: {resp.status_code}")
        return
    
    data = resp.json()
    
    for symbol, quote_data in data.items():
        quote = quote_data.get("quote", {})
        print(f"\n{symbol}:")
        print(f"  Last Price: ${quote.get('lastPrice', 0):.2f}")
        print(f"  Bid: ${quote.get('bidPrice', 0):.2f} x {quote.get('bidSize', 0)}")
        print(f"  Ask: ${quote.get('askPrice', 0):.2f} x {quote.get('askSize', 0)}")
        print(f"  Volume: {quote.get('totalVolume', 0):,}")


def test_schwab_history():
    """
    Test Schwab API price history endpoint.
    """
    print("=" * 60)
    print("Testing Schwab Price History API")
    print("=" * 60)
    
    client = create_client()
    
    symbol = "AAPL"
    print(f"\nFetching 5-minute candles for {symbol}...")
    
    resp = client.get_price_history_every_five_minutes(
        symbol,
        need_extended_hours_data=True
    )
    
    if resp.status_code != httpx.codes.OK:
        print(f"Error: {resp.status_code}")
        return
    
    data = resp.json()
    candles = data.get("candles", [])
    
    print(f"Received {len(candles)} candles")
    
    # Show last 5 candles
    print(f"\nLast 5 candles:")
    for c in candles[-5:]:
        dt = datetime.fromtimestamp(c["datetime"] / 1000)
        print(f"  {dt}: O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f} V={c['volume']:,}")


if __name__ == "__main__":
    tests = {
        "fundamentals": test_schwab_fundamentals,
        "movers": test_schwab_movers,
        "scanner": test_live_scanner,
        "quotes": test_schwab_quotes,
        "history": test_schwab_history,
    }
    
    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        if test_name == "all":
            for name, func in tests.items():
                print(f"\n\n{'#' * 60}")
                print(f"# Running: {name}")
                print(f"{'#' * 60}\n")
                func()
        elif test_name in tests:
            tests[test_name]()
        else:
            print(f"Unknown test: {test_name}")
            print(f"Available tests: {', '.join(tests.keys())}, all")
    else:
        print("Usage: python test_live.py <test_name>")
        print(f"Available tests: {', '.join(tests.keys())}, all")
