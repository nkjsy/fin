import os
import pandas as pd
import requests
import sys
import time
import yfinance as yf
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from schwab.client import Client
import httpx
import pyttsx3
import threading
from client import AutoRefreshSchwabClient
from logger import get_logger

logger = get_logger("UTILS")

CURRENT_NASDAQ100_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'nasdaq', 'current_nasdaq100_constituents.csv')
HIST_NASDAQ100_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'nasdaq', 'nasdaq100_monthly_constituents_backtest_2010_2026.csv')


def get_float_shares(symbol: str) -> int | None:
    try:
        info = yf.Ticker(symbol).info
        float_shares = info.get("floatShares")
        return int(float_shares) if float_shares is not None else None
    except Exception:
        return None


def wait_for_market_open(client):
    ET = ZoneInfo("America/New_York")
    earliest_start = dt_time(8, 30)
    now = datetime.now(ET)
    logger.info("Fetching market hours from Schwab...")
    resp = client.get_market_hours(Client.MarketHours.Market.EQUITY, date=now.date())
    if resp.status_code != httpx.codes.OK:
        logger.warning(f"Failed to get market hours (status {resp.status_code}), using defaults")
        market_open = dt_time(9, 30)
        market_close = dt_time(16, 0)
    else:
        data = resp.json()
        equity_data = data.get("equity", {})
        market_info = equity_data.get("EQ") or equity_data.get("equity") or {}
        if not market_info.get("isOpen", False):
            logger.info(f"Market is closed today ({now.strftime('%A, %B %d, %Y')})")
            sys.exit(0)
        session_hours = market_info.get("sessionHours", {})
        regular_market = session_hours.get("regularMarket", [])
        if not regular_market:
            logger.error("Could not find regular market hours in API response")
            sys.exit(1)
        start_str = regular_market[0].get("start")
        end_str = regular_market[0].get("end")
        market_open = datetime.fromisoformat(start_str).time()
        market_close = datetime.fromisoformat(end_str).time()
        logger.info(f"Market hours today: {market_open.strftime('%H:%M')} - {market_close.strftime('%H:%M')} ET")
    current_time = now.time()
    if market_open <= current_time < market_close:
        logger.info("Market is already open")
        return
    if current_time >= market_close:
        logger.info(f"Market is closed for today (closed at {market_close.strftime('%H:%M')} ET, current time: {current_time.strftime('%H:%M')} ET)")
        sys.exit(0)
    if current_time < earliest_start:
        logger.info(f"Too early to start (current time: {current_time.strftime('%H:%M')} ET)")
        sys.exit(0)
    logger.info(f"Waiting for market open at {market_open.strftime('%H:%M')} ET...")
    while datetime.now(ET).time() < market_open:
        now_et = datetime.now(ET)
        target = datetime.combine(now_et.date(), market_open, tzinfo=ET)
        remaining = target - now_et
        sleep_time = min(60, remaining.seconds)
        if sleep_time > 0:
            time.sleep(sleep_time)
    logger.info("Market is open!")


def wait_until_time(hour: int, minute: int, description: str = None):
    ET = ZoneInfo("America/New_York")
    now = datetime.now(ET)
    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    time_str = f"{hour}:{minute:02d} AM" if hour < 12 else f"{hour-12 if hour > 12 else 12}:{minute:02d} PM"
    if now >= target_time:
        logger.info(f"Already past {time_str} ET" + (f", proceeding with {description}..." if description else ""))
        return False
    wait_seconds = (target_time - now).total_seconds()
    logger.info(f"Waiting until {time_str} ET ({wait_seconds:.0f} seconds)...")
    while datetime.now(ET) < target_time:
        remaining = (target_time - datetime.now(ET)).total_seconds()
        if remaining > 60:
            logger.info(f"  {remaining/60:.1f} minutes remaining...")
            time.sleep(30)
        else:
            time.sleep(5)
    logger.info(f"{time_str} ET reached" + (f", {description}..." if description else ""))
    return True


def get_sp500_tickers():
    try:
        table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
        df = table[0]
        return df['Symbol'].tolist()
    except Exception as e:
        print(f"Error fetching S&P 500: {e}")
        return ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"]


def get_nasdaq100_tickers():
    try:
        tables = pd.read_html('https://en.wikipedia.org/wiki/Nasdaq-100')
        for df in tables:
            columns = {str(col).strip() for col in df.columns}
            if "Ticker" in columns:
                series = df["Ticker"]
                return [str(symbol).strip() for symbol in series.dropna().tolist()]
            if "Symbol" in columns:
                series = df["Symbol"]
                return [str(symbol).strip() for symbol in series.dropna().tolist()]
        raise ValueError("Ticker column not found in Nasdaq-100 tables")
    except Exception as e:
        print(f"Error fetching Nasdaq-100: {e}")
        return ["AAPL", "MSFT", "NVDA", "AMZN", "META"]


def load_latest_historical_nasdaq100_membership() -> list[str]:
    df = pd.read_csv(HIST_NASDAQ100_FILE)
    df['month_end_date'] = pd.to_datetime(df['month_end_date'], errors='coerce')
    df = df.dropna(subset=['month_end_date', 'ticker'])
    latest_month = df['month_end_date'].max()
    latest = df[df['month_end_date'] == latest_month].copy()
    return sorted({str(t).strip().upper().replace('.', '-') for t in latest['ticker'].tolist()})


def write_current_nasdaq100_constituents(tickers: list[str]) -> None:
    os.makedirs(os.path.dirname(CURRENT_NASDAQ100_FILE), exist_ok=True)
    out = pd.DataFrame({'ticker': sorted(dict.fromkeys([str(t).strip().upper().replace('.', '-') for t in tickers if t]))})
    out.to_csv(CURRENT_NASDAQ100_FILE, index=False)


def update_historical_nasdaq100_tail(tickers: list[str], as_of: datetime | None = None) -> None:
    as_of = as_of or datetime.now(ZoneInfo('America/New_York'))
    month_end = pd.Timestamp(as_of.date()).to_period('M').to_timestamp('M')
    df = pd.read_csv(HIST_NASDAQ100_FILE)
    if 'month_end_date' not in df.columns or 'ticker' not in df.columns:
        raise ValueError('historical membership file missing required columns')
    df['month_end_date'] = pd.to_datetime(df['month_end_date'], errors='coerce')
    normalized = sorted(dict.fromkeys([str(t).strip().upper().replace('.', '-') for t in tickers if t]))
    existing_mask = df['month_end_date'] == month_end
    if existing_mask.any():
        # Only replace the latest-month slice; keep everything before untouched and in original order.
        latest_rows = df[existing_mask].copy()
        cols = list(df.columns)
        template = {c: '' for c in cols}
        if 'month' in cols:
            template['month'] = month_end.strftime('%Y-%m')
        template['month_end_date'] = month_end.strftime('%Y-%m-%d')
        if 'membership_basis' in cols:
            template['membership_basis'] = 'month_end'
        if 'snapshot_method' in cols:
            template['snapshot_method'] = 'live_refresh'
        if 'snapshot_inferred' in cols:
            template['snapshot_inferred'] = 'no'
        if 'membership_confidence' in cols:
            template['membership_confidence'] = 'high'
        if 'notes' in cols:
            template['notes'] = 'Updated from current Nasdaq-100 constituent refresh for live trading.'
        new_rows = []
        for t in normalized:
            row = template.copy()
            row['ticker'] = t
            if 'raw_reconstructed_ticker' in cols:
                row['raw_reconstructed_ticker'] = t
            new_rows.append(row)
        before = df.loc[: existing_mask.idxmax()-1] if existing_mask.idxmax() > 0 else df.iloc[0:0]
        after_start = df[existing_mask].index.max() + 1
        after = df.loc[after_start:] if after_start < len(df) else df.iloc[0:0]
        out = pd.concat([before, pd.DataFrame(new_rows, columns=cols), after], ignore_index=True)
        out.to_csv(HIST_NASDAQ100_FILE, index=False)
    else:
        cols = list(df.columns)
        template = {c: '' for c in cols}
        if 'month' in cols:
            template['month'] = month_end.strftime('%Y-%m')
        template['month_end_date'] = month_end.strftime('%Y-%m-%d')
        if 'membership_basis' in cols:
            template['membership_basis'] = 'month_end'
        if 'snapshot_method' in cols:
            template['snapshot_method'] = 'live_refresh'
        if 'snapshot_inferred' in cols:
            template['snapshot_inferred'] = 'no'
        if 'membership_confidence' in cols:
            template['membership_confidence'] = 'high'
        if 'notes' in cols:
            template['notes'] = 'Appended from current Nasdaq-100 constituent refresh for live trading.'
        new_rows = []
        for t in normalized:
            row = template.copy()
            row['ticker'] = t
            if 'raw_reconstructed_ticker' in cols:
                row['raw_reconstructed_ticker'] = t
            new_rows.append(row)
        out = pd.concat([df, pd.DataFrame(new_rows, columns=cols)], ignore_index=True)
        out.to_csv(HIST_NASDAQ100_FILE, index=False)


def refresh_current_nasdaq100_constituents(as_of: datetime | None = None) -> list[str]:
    try:
        tickers = get_nasdaq100_tickers()
        if not tickers:
            raise ValueError('empty current Nasdaq-100 ticker list')
        write_current_nasdaq100_constituents(tickers)
        update_historical_nasdaq100_tail(tickers, as_of=as_of)
        logger.info(f'Refreshed current Nasdaq-100 constituents ({len(tickers)} names)')
        return tickers
    except Exception as e:
        logger.info(f'Current Nasdaq-100 refresh failed, falling back to latest historical month: {e}')
        tickers = load_latest_historical_nasdaq100_membership()
        write_current_nasdaq100_constituents(tickers)
        return tickers


def load_current_nasdaq100_constituents() -> list[str]:
    if os.path.exists(CURRENT_NASDAQ100_FILE):
        df = pd.read_csv(CURRENT_NASDAQ100_FILE)
        if 'ticker' in df.columns:
            vals = [str(t).strip().upper().replace('.', '-') for t in df['ticker'].dropna().tolist()]
            if vals:
                return sorted(dict.fromkeys(vals))
    return load_latest_historical_nasdaq100_membership()


def get_us_stocks(limit=-1):
    url = 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true'
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        rows = data['data']['rows']
        df = pd.DataFrame(rows)
        symbols = df['symbol'].tolist()[:limit]
        cleaned = []
        for s in symbols:
            s = s.replace('/', '-').replace('^', '-P')
            cleaned.append(s)
        return cleaned
    except Exception as e:
        print(f'Error fetching US stocks: {e}')
        return []

def get_next_day(date_str):
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    next_day = date_obj + timedelta(days=1)
    return next_day.strftime('%Y-%m-%d')

# keep rest of legacy helpers below as-is

def calculate_start_date(client, period: str, end_dt: datetime) -> datetime:
    trading_day_periods = {'1d': 1, '2d': 2, '5d': 5}
    calendar_day_periods = {
        '1mo': timedelta(days=30), '3mo': timedelta(days=90), '6mo': timedelta(days=180),
        '1y': timedelta(days=365), '2y': timedelta(days=730), '5y': timedelta(days=1825), 'max': timedelta(days=365 * 20),
    }
    if period in trading_day_periods:
        trading_days_needed = trading_day_periods[period]
        check_date = end_dt.date() - timedelta(days=1)
        trading_days_found = 0
        for _ in range(15):
            try:
                resp = client.get_market_hours(Client.MarketHours.Market.EQUITY, date=check_date)
                if resp.status_code == httpx.codes.OK:
                    data = resp.json()
                    equity_data = data.get('equity', {})
                    market_info = equity_data.get('EQ') or equity_data.get('equity') or {}
                    if market_info.get('isOpen', False):
                        trading_days_found += 1
                        if trading_days_found >= trading_days_needed:
                            return datetime.combine(check_date, datetime.min.time()).replace(tzinfo=ZoneInfo('America/New_York'))
            except Exception:
                pass
            check_date -= timedelta(days=1)
        return end_dt - timedelta(days=trading_days_needed + 4)
    if period in calendar_day_periods:
        return end_dt - calendar_day_periods[period]
    return end_dt - timedelta(days=365)


def speak_symbols(symbols: list) -> None:
    if not symbols:
        return
    def _speak():
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 150)
            text = f"Confirmed: {', '.join(symbols)}"
            full_text = '. . . '.join([text] * 3)
            engine.say(full_text)
            engine.runAndWait()
            engine.stop()
        except Exception as e:
            logger.info(f'TTS error: {e}')
    thread = threading.Thread(target=_speak, daemon=True)
    thread.start()
