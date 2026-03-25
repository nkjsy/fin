import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt

from perf_stats import compute_performance_stats
from portfolio_backtester import PortfolioBacktester
from strategy.momentum_11_1 import Momentum11_1Strategy

DATA_FILE = 'nasdaq/nasdaq100_monthly_constituents_backtest_2010_2026.csv'
BENCHMARK_SYMBOL = 'QQQ'
INITIAL_CAPITAL = 10000.0
BACKTEST_START_DATE = '2010-01-01'
PERIOD = 'max'
LOOKBACK_DAYS = 231
SKIP_DAYS = 21
TOP_N_UP = 5
TOP_N_DOWN = 10
MA_DAYS = 200
OUT_PNG = 'immediate_regime_switch.png'


def load_membership(strategy):
    df = pd.read_csv(DATA_FILE)
    df['month_end_date'] = pd.to_datetime(df['month_end_date'], errors='coerce')
    df = df.dropna(subset=['month_end_date', 'ticker'])
    df = df[df['month_end_date'] >= pd.Timestamp(BACKTEST_START_DATE)].copy()
    df['ticker'] = df['ticker'].astype(str).str.strip().str.upper().str.replace('.', '-', regex=False)
    membership_by_date = {}
    for month_end, group in df.groupby('month_end_date'):
        membership_by_date[pd.Timestamp(month_end)] = strategy.normalize_tickers(group['ticker'].tolist())
    all_tickers = strategy.normalize_tickers(sorted(df['ticker'].unique().tolist()))
    return membership_by_date, all_tickers


def fetch_history_yf(ticker):
    try:
        df = yf.Ticker(ticker).history(period=PERIOD, interval='1d')
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


def run_backtest():
    strategy_up = Momentum11_1Strategy(lookback_days=LOOKBACK_DAYS, skip_days=SKIP_DAYS, top_n=TOP_N_UP)
    strategy_down = Momentum11_1Strategy(lookback_days=LOOKBACK_DAYS, skip_days=SKIP_DAYS, top_n=TOP_N_DOWN)
    backtester = PortfolioBacktester(initial_capital=INITIAL_CAPITAL)

    membership_by_date, universe = load_membership(strategy_up)
    price_data = {}
    for idx, ticker in enumerate(universe, start=1):
        df = fetch_history_yf(ticker)
        if not df.empty:
            price_data[ticker] = df
        if idx % 50 == 0 or idx == len(universe):
            print(f'Loaded {idx}/{len(universe)} symbols. Valid data: {len(price_data)}')

    benchmark_df = fetch_history_yf(BENCHMARK_SYMBOL)
    bench_close = strategy_up.build_close_matrix({BENCHMARK_SYMBOL: benchmark_df}).get(BENCHMARK_SYMBOL, pd.Series(dtype=float)).sort_index().dropna()
    qqq_ma = bench_close.rolling(MA_DAYS).mean()
    trend_ok = (bench_close > qqq_ma)

    close_matrix = strategy_up.build_close_matrix(price_data).sort_index().ffill()
    _, sel_up = strategy_up.select_portfolio(close_matrix, eligible_universe_by_date=membership_by_date)
    _, sel_down = strategy_down.select_portfolio(close_matrix, eligible_universe_by_date=membership_by_date)

    # Month-end picks snapshot, but regime can switch daily between the two portfolios until next rebalance
    rebalance_dates = sorted(set(sel_up.keys()).union(set(sel_down.keys())))
    rebalance_dates = [d for d in rebalance_dates if d in close_matrix.index]

    cash = INITIAL_CAPITAL
    holdings = {}
    equity_rows = []
    selection_rows = []
    turnover_values = []
    current_up = []
    current_down = []
    next_rebal_idx = 0

    for date, prices_row in close_matrix.iterrows():
        prices = prices_row.dropna()
        while next_rebal_idx < len(rebalance_dates) and date >= rebalance_dates[next_rebal_idx]:
            reb_date = rebalance_dates[next_rebal_idx]
            current_up = [s for s in sel_up.get(reb_date, []) if s in prices.index and prices[s] > 0]
            current_down = [s for s in sel_down.get(reb_date, []) if s in prices.index and prices[s] > 0]
            next_rebal_idx += 1

        ok = bool(trend_ok.asof(date)) if pd.notna(trend_ok.asof(date)) else False
        selected_symbols = current_up if ok else current_down

        marked_equity = cash + sum(sh * float(prices[s]) for s, sh in holdings.items() if s in prices.index)
        target_weight = 1.0 / len(selected_symbols) if selected_symbols else 0.0
        current_symbols = set(holdings.keys())
        future_symbols = set(selected_symbols)
        trade_value_total = 0.0
        new_holdings = {}
        for symbol in sorted(current_symbols.union(future_symbols)):
            if symbol not in prices.index:
                continue
            price = float(prices[symbol])
            if price <= 0:
                continue
            old_shares = float(holdings.get(symbol, 0.0))
            old_value = old_shares * price
            target_value = marked_equity * target_weight if symbol in future_symbols else 0.0
            delta_value = target_value - old_value
            if abs(delta_value) > 1e-10:
                trade_value_total += abs(delta_value)
            if target_value > 0:
                new_holdings[symbol] = target_value / price
        holdings = new_holdings
        invested_value = sum(sh * float(prices[s]) for s, sh in holdings.items() if s in prices.index)
        cash = marked_equity - invested_value
        if marked_equity > 0:
            turnover_values.append(trade_value_total / marked_equity)

        equity = cash + sum(sh * float(prices[s]) for s, sh in holdings.items() if s in prices.index)
        equity_rows.append({'Datetime': date, 'Equity': equity, 'Cash': cash, 'Positions': len(holdings)})
        selection_rows.append({'Datetime': date, 'Mode': 'Top5' if ok else 'Top10', 'Count': len(selected_symbols)})

    equity_df = pd.DataFrame(equity_rows)
    equity_df['Datetime'] = pd.to_datetime(equity_df['Datetime'])
    equity_df = equity_df.set_index('Datetime')
    start_ts = pd.Timestamp(BACKTEST_START_DATE)
    if getattr(equity_df.index, 'tz', None) is not None:
        start_ts = start_ts.tz_localize(equity_df.index.tz)
    equity_df = equity_df[equity_df.index >= start_ts]

    benchmark_curve = backtester.build_buy_and_hold_curve(bench_close, INITIAL_CAPITAL, target_index=equity_df.index)
    benchmark_curve = benchmark_curve[benchmark_curve.index >= start_ts]

    bench_ret = ((benchmark_curve.iloc[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100.0
    stats = compute_performance_stats(equity_df['Equity'], INITIAL_CAPITAL, exposure_pct=(equity_df['Positions'].gt(0).mean() * 100.0))
    summary = {
        'Final-Equity': float(equity_df['Equity'].iloc[-1]),
        'Return %': ((equity_df['Equity'].iloc[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100.0,
        'Average Turnover %': (sum(turnover_values) / len(turnover_values) * 100.0) if turnover_values else 0.0,
        **stats,
        'QQQ Return %': bench_ret,
    }
    summary['Excess Return %'] = summary['Return %'] - bench_ret

    print(pd.DataFrame([summary]).to_string(index=False))
    print('\nRecent regime states:')
    print(pd.DataFrame(selection_rows).tail(10).to_string(index=False))

    plt.figure(figsize=(16, 10))
    plt.plot(equity_df.index, equity_df['Equity'].values, label='Strategy', linewidth=2.5, color='#0b6e4f')
    plt.plot(benchmark_curve.index, benchmark_curve.values, label='QQQ', linewidth=2.2, color='#b5651d')
    plt.title('11-1 Momentum: Immediate switch Top5/Top10 by QQQ vs MA200')
    plt.xlabel('Date')
    plt.ylabel('Equity ($)')
    plt.legend(loc='upper left')
    plt.grid(True, alpha=0.25)
    text = (
        f"Final Equity: ${summary['Final-Equity']:,.0f}\n"
        f"Return: {summary['Return %']:.2f}%\n"
        f"CAGR: {summary['CAGR %']:.2f}%\n"
        f"MaxDD: {summary['Max Drawdown %']:.2f}%\n"
        f"Sharpe: {summary['Sharpe']:.3f}\n"
        f"QQQ Return: {summary['QQQ Return %']:.2f}%\n"
        f"Excess: {summary['Excess Return %']:.2f}%"
    )
    plt.text(0.02, 0.98, text, transform=plt.gca().transAxes, ha='left', va='top', fontsize=16,
             bbox=dict(boxstyle='round,pad=0.6', facecolor='white', alpha=0.9, edgecolor='gray'))
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=180)
    print(f'Wrote chart to {OUT_PNG}')


if __name__ == '__main__':
    run_backtest()
