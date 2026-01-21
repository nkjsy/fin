"""
Unit tests for live trading components (Schwab API).

Usage:
    python test_live.py fundamentals  # Test fundamentals API
    python test_live.py movers        # Test movers API
    python test_live.py scanner       # Test live momentum scanner
    python test_live.py all           # Run all tests
"""

import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo
import httpx
from schwab.client import Client
from utils import create_client
from datetime import datetime, timedelta
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
            movers = data.get("screeners", [])[:3]  # Top 3 for debug
            
            if not movers:
                print("  No movers found.")
                continue
            
            for m in movers:
                print(f"\n  --- {m.get('symbol', '?')} ---")
                for key, value in m.items():
                    print(f"    {key}: {value}")
                
        except Exception as e:
            print(f"  Error: {e}")


def test_live_scanner():
    """
    Test the full LiveMomentumScanner with mocked time (9:41 AM ET).
    """
    print("=" * 60)
    print("Testing Live Momentum Scanner")
    print("=" * 60)
    
    client = create_client()
    provider = SchwabProvider(client)
    scanner = LiveMomentumScanner(provider)
    
    # Mock only datetime.now in utils module, preserving other datetime functionality
    fake_now = datetime.now(ZoneInfo("America/New_York")).replace(hour=9, minute=41, second=0)
    
    with patch("utils.datetime") as mock_datetime:
        # Preserve the real datetime class behavior
        mock_datetime.now.return_value = fake_now
        mock_datetime.combine = datetime.combine
        mock_datetime.min = datetime.min
        mock_datetime.strptime = datetime.strptime
        
        symbols = scanner.scan(min_price=2.0)
    
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
        print(f"\n--- {symbol} ---")
        # Show all fields to find gap-related ones
        for key, value in sorted(quote.items()):
            print(f"  {key}: {value}")


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
    
    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

    resp = client.get_price_history_every_five_minutes(
        symbol,
        start_datetime=market_open,
        need_extended_hours_data=False
    )
    
    if resp.status_code != httpx.codes.OK:
        print(f"Error: {resp.status_code}")
        return
    
    data = resp.json()
    candles = data.get("candles", [])
    
    print(f"Received {len(candles)} candles")
    
    # Show last 5 candles
    print(f"\nLast 5 candles:")
    for c in candles:
        dt = datetime.fromtimestamp(c["datetime"] / 1000, tz=ZoneInfo("America/New_York"))
        print(f"  {dt}: O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f} V={c['volume']:,}")


def test_volume_confirmation():
    """
    Test the volume confirmation logic in LiveMomentumScanner.
    """
    print("=" * 60)
    print("Testing Volume Confirmation")
    print("=" * 60)
    
    client = create_client()
    provider = SchwabProvider(client)
    scanner = LiveMomentumScanner(provider)
    
    # Test with known liquid symbols
    test_symbols = ["AAPL", "TSLA", "CRVS"]
    
    for symbol in test_symbols:
        print(f"\n{symbol}: ", end="")
        result = scanner._confirm_volume(symbol)
        print(f"{'CONFIRMED' if result else 'NOT CONFIRMED'}")


def test_volume_debug():
    """
    Debug volume data to see exactly which candles are being captured.
    """
    from datetime import time as dt_time
    
    print("=" * 60)
    print("Debugging Volume Data")
    print("=" * 60)
    
    client = create_client()
    provider = SchwabProvider(client)
    
    symbol = "AAPL"
    print(f"\nFetching 5-minute candles for {symbol}...")
    
    df = provider.get_history(symbol, interval="minute5", period="2d")
    
    if df.empty:
        print("No data!")
        return
    
    df["Date"] = df["Datetime"].dt.date
    df["Time"] = df["Datetime"].dt.time
    
    dates = sorted(df["Date"].unique())
    today = dates[-1]
    yesterday = dates[-2]
    
    print(f"\nToday: {today}, Yesterday: {yesterday}")
    
    # Show all candles in the 9:30-9:45 window for both days
    start_time = dt_time(9, 25)
    end_time = dt_time(9, 50)
    
    for date in [yesterday, today]:
        print(f"\n--- {date} (9:25-9:50 window) ---")
        mask = (df["Date"] == date) & (df["Time"] >= start_time) & (df["Time"] < end_time)
        window_df = df[mask]
        for _, row in window_df.iterrows():
            print(f"  {row['Datetime']} - Volume: {row['Volume']:,}")
        
        # Sum 9:30-9:40
        sum_mask = (df["Date"] == date) & (df["Time"] >= dt_time(9, 30)) & (df["Time"] < dt_time(9, 40))
        total = df[sum_mask]["Volume"].sum()
        print(f"  SUM (9:30-9:40): {total:,}")
    
    # Print full day volume table and sum
    # for date in [yesterday, today]:
    #     print(f"\n{'='*60}")
    #     print(f"FULL DAY: {date}")
    #     print(f"{'='*60}")
    #     day_df = df[df["Date"] == date].sort_values("Time")
    #     for _, row in day_df.iterrows():
    #         print(f"  {row['Time']} - Volume: {row['Volume']:,}")
        
    #     day_total = day_df["Volume"].sum()
    #     print(f"\n  TOTAL DAY VOLUME: {day_total:,}")
    #     print(f"  CANDLE COUNT: {len(day_df)}")


if __name__ == "__main__":
    tests = {
        "fundamentals": test_schwab_fundamentals,
        "movers": test_schwab_movers,
        "scanner": test_live_scanner,
        "quotes": test_schwab_quotes,
        "history": test_schwab_history,
        "volume": test_volume_confirmation,
        "debug": test_volume_debug,
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
