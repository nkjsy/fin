import pandas as pd
import requests
import sys
import time
import yfinance as yf
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from schwab.client import Client
import httpx
from schwab.auth import easy_client
import config


def get_float_shares(symbol: str) -> int | None:
    """
    Get float shares for a symbol using yfinance.
    
    Args:
        symbol: Ticker symbol
        
    Returns:
        Float shares count, or None if not available
    """
    try:
        info = yf.Ticker(symbol).info
        float_shares = info.get("floatShares")
        return int(float_shares) if float_shares is not None else None
    except Exception:
        return None


def wait_for_market_open(client):
    """Wait until market is open, using Schwab API for accurate market hours."""
    
    ET = ZoneInfo("America/New_York")
    earliest_start = dt_time(8, 30)
    now = datetime.now(ET)
    
    # Fetch market hours from Schwab API
    print("Fetching market hours from Schwab...")
    resp = client.get_market_hours(Client.MarketHours.Market.EQUITY, date=now.date())
    
    if resp.status_code != httpx.codes.OK:
        print(f"⚠️  Failed to get market hours (status {resp.status_code}), using defaults")
        # Fallback to hardcoded hours
        market_open = dt_time(9, 30)
        market_close = dt_time(16, 0)
    else:
        data = resp.json()
        # Parse the response to get market hours
        # Response structure: {"equity": {"EQ": {...}}} or {"equity": {"equity": {...}}}
        equity_data = data.get("equity", {})
        market_info = equity_data.get("EQ") or equity_data.get("equity") or {}
        
        if not market_info.get("isOpen", False):
            # Market is closed (weekend/holiday)
            print(f"Market is closed today ({now.strftime('%A, %B %d, %Y')})")
            print("This could be a weekend or market holiday.")
            sys.exit(0)
        
        # Parse session hours (format: "2024-01-15T09:30:00-05:00")
        session_hours = market_info.get("sessionHours", {})
        regular_market = session_hours.get("regularMarket", [])
        
        if not regular_market:
            print("Could not find regular market hours in API response")
            sys.exit(1)
        
        # Get start and end times
        start_str = regular_market[0].get("start")
        end_str = regular_market[0].get("end")
        
        market_open = datetime.fromisoformat(start_str).time()
        market_close = datetime.fromisoformat(end_str).time()
        
        print(f"Market hours today: {market_open.strftime('%H:%M')} - {market_close.strftime('%H:%M')} ET")
    
    current_time = now.time()
    
    # Check if market is currently open
    if market_open <= current_time < market_close:
        print("Market is already open")
        return
    
    # Check if market has closed for today
    if current_time >= market_close:
        print(f"Market is closed for today (closed at {market_close.strftime('%H:%M')} ET, current time: {current_time.strftime('%H:%M')} ET)")
        print("Please run again tomorrow before market close.")
        sys.exit(0)
    
    # Check if it's too early
    if current_time < earliest_start:
        print(f"Too early to start (current time: {current_time.strftime('%H:%M')} ET)")
        print("Please run again after 8:30 AM ET.")
        sys.exit(0)
    
    print(f"Waiting for market open at {market_open.strftime('%H:%M')} ET...")
    
    while datetime.now(ET).time() < market_open:
        now_et = datetime.now(ET)
        target = datetime.combine(now_et.date(), market_open, tzinfo=ET)
        remaining = target - now_et
        
        minutes = remaining.seconds // 60
        if minutes > 0:
            print(f"  {minutes} minutes until market open...")
        
        # Sleep in intervals
        sleep_time = min(60, remaining.seconds)
        if sleep_time > 0:
            time.sleep(sleep_time)
    
    print("Market is open!")


def wait_until_time(hour: int, minute: int, description: str = None):
    """
    Wait until a specific time in Eastern Time.
    
    Args:
        hour: Target hour (0-23)
        minute: Target minute (0-59)
        description: Optional description for logging (e.g., "volume check")
    
    Returns:
        True if waited, False if already past target time
    """
    ET = ZoneInfo("America/New_York")
    now = datetime.now(ET)
    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    time_str = f"{hour}:{minute:02d} AM" if hour < 12 else f"{hour-12 if hour > 12 else 12}:{minute:02d} PM"
    
    if now >= target_time:
        print(f"Already past {time_str} ET" + (f", proceeding with {description}..." if description else ""))
        return False
    
    wait_seconds = (target_time - now).total_seconds()
    print(f"Waiting until {time_str} ET ({wait_seconds:.0f} seconds)...")
    
    # Wait with progress updates
    while datetime.now(ET) < target_time:
        remaining = (target_time - datetime.now(ET)).total_seconds()
        if remaining > 60:
            print(f"  {remaining/60:.1f} minutes remaining...")
            time.sleep(30)
        else:
            time.sleep(5)
    
    print(f"{time_str} ET reached" + (f", {description}..." if description else ""))
    return True


class AutoRefreshClient:
    """
    Wrapper around Schwab Client that proactively refreshes the access token.
    
    The Schwab access token expires every 30 minutes. While schwab-py handles
    automatic refresh, it can sometimes fail. This wrapper proactively recreates
    the client before expiration to ensure uninterrupted operation.
    
    Usage:
        wrapper = AutoRefreshClient()
        client = wrapper.client  # Use this for API calls
        
        # In your main loop, periodically call:
        wrapper.ensure_fresh()
    """
    
    # Refresh 5 minutes before expiration (25 minutes)
    ACCESS_TOKEN_REFRESH_SECONDS = 25 * 60
    
    # 5.5 days for refresh token (proactive weekly refresh)
    REFRESH_TOKEN_MAX_AGE_SECONDS = 5.5 * 24 * 60 * 60
    
    def __init__(self):
        """Initialize with a fresh client."""
        self._client = None
        self._client_created_at = None
        self._create_client()
    
    @property
    def client(self) -> Client:
        """Get the current client, refreshing if needed."""
        self.ensure_fresh()
        return self._client
    
    def _create_client(self):
        """Create a new authenticated Schwab client."""
        print("Authenticating with Schwab...")
        self._client = easy_client(
            api_key=config.SCHWAB_API_KEY,
            app_secret=config.SCHWAB_APP_SECRET,
            callback_url=config.SCHWAB_CALLBACK_URL,
            token_path=config.SCHWAB_TOKEN_PATH,
            max_token_age=self.REFRESH_TOKEN_MAX_AGE_SECONDS
        )
        self._client_created_at = time.time()
        print("Authentication successful")
    
    def ensure_fresh(self):
        """
        Ensure the client has a fresh access token.
        
        Call this periodically (e.g., every polling cycle) to proactively
        refresh the client before the 30-minute access token expires.
        """
        if self._client is None or self._client_created_at is None:
            self._create_client()
            return
        
        elapsed = time.time() - self._client_created_at
        if elapsed >= self.ACCESS_TOKEN_REFRESH_SECONDS:
            print(f"[AutoRefresh] Access token age: {elapsed/60:.1f}min, refreshing...")
            self._create_client()
    
    def force_refresh(self):
        """Force an immediate client refresh."""
        print("[AutoRefresh] Forcing client refresh...")
        self._create_client()


def create_client():
    """
    Create authenticated Schwab client.
    
    Uses max_token_age for proactive token refresh. Per schwab-py docs, tokens
    expire after 7 days. Setting max_token_age to ~5.5 days ensures the token
    gets refreshed on Monday mornings if created the previous week.
    
    Note: For long-running applications, consider using AutoRefreshClient instead
    to handle the 30-minute access token expiration.
    """
    print("Authenticating with Schwab...")
    
    # 5.5 days in seconds - triggers proactive refresh on Monday if token
    # was created on Tuesday or earlier of the previous week
    MAX_TOKEN_AGE_SECONDS = 5.5 * 24 * 60 * 60  # 475200 seconds
    
    client = easy_client(
        api_key=config.SCHWAB_API_KEY,
        app_secret=config.SCHWAB_APP_SECRET,
        callback_url=config.SCHWAB_CALLBACK_URL,
        token_path=config.SCHWAB_TOKEN_PATH,
        max_token_age=MAX_TOKEN_AGE_SECONDS
    )
    
    print("Authentication successful")
    return client


def get_sp500_tickers():
    try:
        # Requires lxml or html5lib
        table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
        df = table[0]
        return df['Symbol'].tolist()
    except Exception as e:
        print(f"Error fetching S&P 500: {e}")
        return ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"] # Fallback

def get_us_stocks(limit=-1):
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        rows = data['data']['rows']
        df = pd.DataFrame(rows)
        
        # Clean symbols
        # NASDAQ uses '/' for classes (e.g. BRK/B) and '^' for other things.
        # yfinance uses '-' for classes (e.g. BRK-B).
        symbols = df['symbol'].tolist()[:limit]
        cleaned_symbols = []
        for s in symbols:
            s = s.replace('/', '-')
            s = s.replace('^', '-P') # Assumption for preferreds, might need refinement
            cleaned_symbols.append(s)
            
        return cleaned_symbols
    except Exception as e:
        print(f"Error fetching US stocks: {e}")
        return []
    
def get_next_day(date_str):
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    next_day = date_obj + timedelta(days=1)
    return next_day.strftime("%Y-%m-%d")


def calculate_start_date(client, period: str, end_dt: datetime) -> datetime:
    """
    Calculate start date based on period string.
    
    For short periods (1d-5d), uses market hours API to find actual trading days,
    avoiding weekends and holidays.
    
    Args:
        client: Schwab client for market hours API
        period: Period string like '1d', '2d', '5d', '1mo', '1y', etc.
        end_dt: End datetime (timezone-aware)
        
    Returns:
        Start datetime (timezone-aware)
    """
    # Map period to number of trading days needed
    trading_day_periods = {
        "1d": 1,
        "2d": 2,
        "5d": 5,
    }
    
    # Map period to calendar days for longer periods
    calendar_day_periods = {
        "1mo": timedelta(days=30),
        "3mo": timedelta(days=90),
        "6mo": timedelta(days=180),
        "1y": timedelta(days=365),
        "2y": timedelta(days=730),
        "5y": timedelta(days=1825),
        "max": timedelta(days=365 * 20),  # 20 years
    }
    
    # For short periods, count actual trading days
    if period in trading_day_periods:
        trading_days_needed = trading_day_periods[period]
        check_date = end_dt.date() - timedelta(days=1)
        trading_days_found = 0
        max_lookback = 15  # Safety limit
        
        for _ in range(max_lookback):
            try:
                resp = client.get_market_hours(
                    Client.MarketHours.Market.EQUITY,
                    date=check_date
                )
                if resp.status_code == httpx.codes.OK:
                    data = resp.json()
                    equity_data = data.get("equity", {})
                    market_info = equity_data.get("EQ") or equity_data.get("equity") or {}
                    
                    if market_info.get("isOpen", False):
                        trading_days_found += 1
                        if trading_days_found >= trading_days_needed:
                            # Return start of this trading day
                            return datetime.combine(check_date, datetime.min.time()).replace(
                                tzinfo=ZoneInfo("America/New_York")
                            )
            except Exception:
                pass
            
            check_date -= timedelta(days=1)
        
        # Fallback if API fails
        return end_dt - timedelta(days=trading_days_needed + 4)
    
    # For longer periods, use calendar days
    if period in calendar_day_periods:
        return end_dt - calendar_day_periods[period]
    
    # Fallback to 1 year
    return end_dt - timedelta(days=365)


if __name__ == "__main__":
    # sp = get_sp500_tickers()
    # us = get_us_stocks()
    # print(f"US Tickers: {us[:5]} ... Total: {len(us)}")
    
    # Test calculate_start_date
    print("Testing calculate_start_date...")
    
    client = create_client()
    ET = ZoneInfo("America/New_York")
    
    # Test with today as end date
    end_dt = datetime.now(ET)
    print(f"End date: {end_dt.strftime('%Y-%m-%d %A')}")
    
    # Test 2d period - should skip weekends/holidays
    start_2d = calculate_start_date(client, "2d", end_dt)
    print(f"  2d period start: {start_2d.strftime('%Y-%m-%d %A')}")
