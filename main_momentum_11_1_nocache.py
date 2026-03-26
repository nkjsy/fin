import pandas as pd
import yfinance as yf

from perf_stats import compute_performance_stats
from portfolio_backtester import PortfolioBacktester
from strategy.momentum_11_1 import Momentum11_1Strategy

INITIAL_CAPITAL = 10000.0
BENCHMARK_SYMBOL = 'QQQ'
HISTORICAL_MEMBERSHIP_FILE = 'nasdaq/nasdaq100_monthly_constituents_backtest_2010_2026.csv'
TOP_N = 10
LOOKBACK_DAYS = 231
SKIP_DAYS = 21
BACKTEST_START_DATE = '2010-01-01'
PERIOD = 'max'
PRINT_TOP_REBALANCES = 8


def load_historical_nasdaq100_membership(strategy: Momentum11_1Strategy):
    df = pd.read_csv(HISTORICAL_MEMBERSHIP_FILE)
    df['month_end_date'] = pd.to_datetime(df['month_end_date'], errors='coerce')
    df = df.dropna(subset=['month_end_date', 'ticker'])
    df = df[df['month_end_date'] >= pd.Timestamp(BACKTEST_START_DATE)].copy()
    df['ticker'] = df['ticker'].astype(str).str.strip().str.upper().str.replace('.', '-', regex=False)

    membership_by_date = {}
    for month_end, group in df.groupby('month_end_date'):
        membership_by_date[pd.Timestamp(month_end)] = strategy.normalize_tickers(group['ticker'].tolist())

    all_tickers = strategy.normalize_tickers(sorted(df['ticker'].unique().tolist()))
    return membership_by_date, all_tickers


def fetch_history_yf(ticker: str, period: str = 'max') -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).history(period=period, interval='1d')
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        date_col = 'Datetime' if 'Datetime' in df.columns else 'Date'
        df = df[[date_col, 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=[date_col, 'Open', 'High', 'Low', 'Close', 'Volume'])
        return df
    except Exception as e:
        print(f'Error fetching {ticker}: {e}')
        return pd.DataFrame()


def plot_equity_stub(*args, **kwargs):
    pass


def run_strategy_backtest():
    strategy = Momentum11_1Strategy(
        lookback_days=LOOKBACK_DAYS,
        skip_days=SKIP_DAYS,
        top_n=TOP_N,
    )
    backtester = PortfolioBacktester(initial_capital=INITIAL_CAPITAL)

    eligible_universe_by_date, universe = load_historical_nasdaq100_membership(strategy)
    print(f'Universe: NASDAQ100_HISTORICAL | Months: {len(eligible_universe_by_date)} | Unique Tickers: {len(universe)} | Benchmark: {BENCHMARK_SYMBOL}')

    price_data = {}
    for idx, ticker in enumerate(universe, start=1):
        df = fetch_history_yf(ticker, period=PERIOD)
        if not df.empty:
            price_data[ticker] = df
        if idx % 50 == 0 or idx == len(universe):
            print(f'Loaded {idx}/{len(universe)} symbols. Valid data: {len(price_data)}')

    result = backtester.run(price_data, strategy, eligible_universe_by_date=eligible_universe_by_date)
    if result.equity_curve.empty:
        print('No strategy equity curve generated.')
        return

    benchmark_df = fetch_history_yf(BENCHMARK_SYMBOL, period=PERIOD)
    benchmark_close = strategy.build_close_matrix({BENCHMARK_SYMBOL: benchmark_df}).get(BENCHMARK_SYMBOL, pd.Series(dtype=float))

    equity_df = result.equity_curve.copy()
    equity_df['Datetime'] = pd.to_datetime(equity_df['Datetime'], errors='coerce')
    equity_df = equity_df.dropna(subset=['Datetime']).set_index('Datetime')
    start_timestamp = pd.Timestamp(BACKTEST_START_DATE)
    if getattr(equity_df.index, 'tz', None) is not None:
        start_timestamp = start_timestamp.tz_localize(equity_df.index.tz)
    equity_df = equity_df[equity_df.index >= start_timestamp]

    benchmark_curve = backtester.build_buy_and_hold_curve(
        close_series=benchmark_close,
        initial_capital=INITIAL_CAPITAL,
        target_index=equity_df.index,
    )

    strategy_stats = compute_performance_stats(
        equity_curve=equity_df['Equity'],
        initial_capital=INITIAL_CAPITAL,
        exposure_pct=(equity_df['Positions'].gt(0).mean() * 100.0) if not equity_df.empty else 0.0,
    )
    benchmark_stats = compute_performance_stats(
        equity_curve=benchmark_curve,
        initial_capital=INITIAL_CAPITAL,
        exposure_pct=100.0,
    )

    benchmark_return_pct = ((benchmark_curve.iloc[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100.0 if not benchmark_curve.empty else 0.0

    summary = {
        **result.summary,
        **strategy_stats,
        f'{BENCHMARK_SYMBOL} Return %': benchmark_return_pct,
        'Excess Return %': result.summary['Return %'] - benchmark_return_pct,
    }

    print(f'--- 11-1 Momentum Portfolio Backtest vs {BENCHMARK_SYMBOL} ---')
    print(pd.DataFrame([summary]).to_string(index=False))

    comparison = pd.DataFrame(
        {
            'Metric': ['CAGR %', 'Max Drawdown %', 'Sharpe', 'Calmar', 'Return %'],
            'Strategy': [
                summary.get('CAGR %', 0.0),
                summary.get('Max Drawdown %', 0.0),
                summary.get('Sharpe', 0.0),
                summary.get('Calmar', 0.0),
                summary.get('Return %', 0.0),
            ],
            BENCHMARK_SYMBOL: [
                benchmark_stats.get('CAGR %', 0.0),
                benchmark_stats.get('Max Drawdown %', 0.0),
                benchmark_stats.get('Sharpe', 0.0),
                benchmark_stats.get('Calmar', 0.0),
                summary.get(f'{BENCHMARK_SYMBOL} Return %', 0.0),
            ],
        }
    )
    print(f'\n--- Strategy vs {BENCHMARK_SYMBOL} ---')
    print(comparison.to_string(index=False))

    if not result.selection_history.empty:
        print('\n--- Recent Rebalance Selections ---')
        selection_history = result.selection_history.copy()
        selection_history['Datetime'] = pd.to_datetime(selection_history['Datetime'], errors='coerce')
        selection_start = pd.Timestamp(BACKTEST_START_DATE)
        selection_datetimes = selection_history['Datetime']
        if hasattr(selection_datetimes.dt, 'tz') and selection_datetimes.dt.tz is not None:
            selection_start = selection_start.tz_localize(selection_datetimes.dt.tz)
        selection_history = selection_history[selection_history['Datetime'] >= selection_start]
        print(selection_history.tail(PRINT_TOP_REBALANCES).to_string(index=False))


if __name__ == '__main__':
    run_strategy_backtest()
