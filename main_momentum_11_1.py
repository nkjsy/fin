import pandas as pd

from client import AutoRefreshSchwabClient
from data_manager import DataManager
from main_index_regime_daily import compute_performance_stats
from plotting import plot_equity_comparison
from portfolio_backtester import PortfolioBacktester
from providers.schwab_lib import SchwabProvider
from providers.yfinance_lib import YFinanceProvider
from strategy.momentum_11_1 import Momentum11_1Strategy
from utils import get_nasdaq100_tickers, get_sp500_tickers


DATA_DIR = "data"
TIMEFRAME = "daily1"
INITIAL_CAPITAL = 10000.0
BENCHMARK_SYMBOL = "QQQ"
UNIVERSE_NAME = "NASDAQ100_HISTORICAL"
HISTORICAL_MEMBERSHIP_FILE = "nasdaq/nasdaq100_monthly_constituents_backtest_2010_2026.csv"
BACKTEST_START_DATE = "2010-01-01"

LOOKBACK_DAYS = 231
SKIP_DAYS = 21
TOP_N = 10
PERIOD = "max"
END_DATE = None

REFRESH_DATA = False
UNIVERSE_LIMIT = 0
PRINT_TOP_REBALANCES = 5
PLOT_RESULT = True
ENABLE_SCHWAB_FALLBACK = True


_SCHWAB_DATA_MANAGER = None


def get_schwab_data_manager() -> DataManager | None:
    global _SCHWAB_DATA_MANAGER
    if _SCHWAB_DATA_MANAGER is not None:
        return _SCHWAB_DATA_MANAGER

    if not ENABLE_SCHWAB_FALLBACK:
        return None

    try:
        client_wrapper = AutoRefreshSchwabClient()
        provider = SchwabProvider(client_wrapper)
        _SCHWAB_DATA_MANAGER = DataManager(DATA_DIR, provider)
        print("Schwab fallback provider enabled.")
        return _SCHWAB_DATA_MANAGER
    except Exception as exc:
        print(f"Schwab fallback unavailable: {exc}")
        _SCHWAB_DATA_MANAGER = None
        return None


def load_historical_nasdaq100_membership(strategy: Momentum11_1Strategy) -> tuple[dict[pd.Timestamp, list[str]], list[str]]:
    df = pd.read_csv(HISTORICAL_MEMBERSHIP_FILE)
    required_cols = {"month_end_date", "ticker"}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"Historical membership file missing columns: {required_cols}")

    df = df.copy()
    df["month_end_date"] = pd.to_datetime(df["month_end_date"], errors="coerce")
    df = df.dropna(subset=["month_end_date", "ticker"])
    df = df[df["month_end_date"] >= pd.Timestamp(BACKTEST_START_DATE)].copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)

    membership_by_date: dict[pd.Timestamp, list[str]] = {}
    for month_end, group in df.groupby("month_end_date"):
        membership_by_date[pd.Timestamp(month_end)] = strategy.normalize_tickers(group["ticker"].tolist())

    all_tickers = strategy.normalize_tickers(sorted(df["ticker"].unique().tolist()))
    return membership_by_date, all_tickers


def load_symbol_data(
    ticker: str,
    data_manager: DataManager,
    timeframe: str,
    period: str,
    end_date: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    df = data_manager.load_data(ticker, timeframe)
    if not df.empty and not refresh:
        return df

    success = data_manager.download_data(ticker, timeframe, period=period, end_date=end_date)
    if success:
        return data_manager.load_data(ticker, timeframe)

    schwab_data_manager = get_schwab_data_manager()
    if schwab_data_manager is None:
        return pd.DataFrame()

    fallback_success = schwab_data_manager.download_data(ticker, timeframe, period=period, end_date=end_date)
    if not fallback_success:
        return pd.DataFrame()
    return schwab_data_manager.load_data(ticker, timeframe)


def get_universe(strategy: Momentum11_1Strategy) -> list[str]:
    universe_name = str(UNIVERSE_NAME).strip().upper()
    if universe_name == "NASDAQ100_HISTORICAL":
        _, tickers = load_historical_nasdaq100_membership(strategy)
    elif universe_name == "NASDAQ100":
        tickers = strategy.normalize_tickers(get_nasdaq100_tickers())
    elif universe_name == "SP500":
        tickers = strategy.normalize_tickers(get_sp500_tickers())
    else:
        raise ValueError(f"Unsupported UNIVERSE_NAME={UNIVERSE_NAME}. Use NASDAQ100_HISTORICAL, NASDAQ100 or SP500.")

    if UNIVERSE_LIMIT and UNIVERSE_LIMIT > 0:
        return tickers[:UNIVERSE_LIMIT]
    return tickers


def run_strategy_backtest():
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)
    strategy = Momentum11_1Strategy(
        lookback_days=LOOKBACK_DAYS,
        skip_days=SKIP_DAYS,
        top_n=TOP_N,
    )
    backtester = PortfolioBacktester(initial_capital=INITIAL_CAPITAL)

    eligible_universe_by_date = None
    if str(UNIVERSE_NAME).strip().upper() == "NASDAQ100_HISTORICAL":
        eligible_universe_by_date, historical_universe = load_historical_nasdaq100_membership(strategy)
        if UNIVERSE_LIMIT and UNIVERSE_LIMIT > 0:
            universe = historical_universe[:UNIVERSE_LIMIT]
            allowed = set(universe)
            eligible_universe_by_date = {
                date: [ticker for ticker in tickers if ticker in allowed]
                for date, tickers in eligible_universe_by_date.items()
            }
        else:
            universe = historical_universe
        print(
            f"Universe: {UNIVERSE_NAME} | Months: {len(eligible_universe_by_date)} | "
            f"Unique Tickers: {len(universe)} | Benchmark: {BENCHMARK_SYMBOL}"
        )
    else:
        universe = get_universe(strategy)
        print(f"Universe: {UNIVERSE_NAME} | Size: {len(universe)} | Benchmark: {BENCHMARK_SYMBOL}")

    price_data = {}
    for idx, ticker in enumerate(universe, start=1):
        df = load_symbol_data(
            ticker=ticker,
            data_manager=data_manager,
            timeframe=TIMEFRAME,
            period=PERIOD,
            end_date=END_DATE,
            refresh=REFRESH_DATA,
        )
        if not df.empty:
            price_data[ticker] = df

        if idx % 50 == 0 or idx == len(universe):
            print(f"Loaded {idx}/{len(universe)} symbols. Valid data: {len(price_data)}")

    result = backtester.run(
        price_data,
        strategy,
        eligible_universe_by_date=eligible_universe_by_date,
    )
    if result.equity_curve.empty:
        print("No strategy equity curve generated.")
        return

    benchmark_df = load_symbol_data(
        ticker=BENCHMARK_SYMBOL,
        data_manager=data_manager,
        timeframe=TIMEFRAME,
        period=PERIOD,
        end_date=END_DATE,
        refresh=REFRESH_DATA,
    )
    benchmark_close = strategy.build_close_matrix({BENCHMARK_SYMBOL: benchmark_df}).get(BENCHMARK_SYMBOL, pd.Series(dtype=float))

    equity_df = result.equity_curve.copy()
    equity_df["Datetime"] = pd.to_datetime(equity_df["Datetime"], errors="coerce")
    equity_df = equity_df.dropna(subset=["Datetime"]).set_index("Datetime")
    start_timestamp = pd.Timestamp(BACKTEST_START_DATE)
    if getattr(equity_df.index, "tz", None) is not None:
        start_timestamp = start_timestamp.tz_localize(equity_df.index.tz)
    equity_df = equity_df[equity_df.index >= start_timestamp]

    benchmark_curve = backtester.build_buy_and_hold_curve(
        close_series=benchmark_close,
        initial_capital=INITIAL_CAPITAL,
        target_index=equity_df.index,
    )

    strategy_stats = compute_performance_stats(
        equity_curve=equity_df["Equity"],
        initial_capital=INITIAL_CAPITAL,
        exposure_pct=(equity_df["Positions"].gt(0).mean() * 100.0) if not equity_df.empty else 0.0,
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
        f"{BENCHMARK_SYMBOL} Return %": benchmark_return_pct,
        "Excess Return %": result.summary["Return %"] - benchmark_return_pct,
    }

    print(f"--- 11-1 Momentum Portfolio Backtest vs {BENCHMARK_SYMBOL} ---")
    print(pd.DataFrame([summary]).to_string(index=False))

    comparison = pd.DataFrame(
        {
            "Metric": ["CAGR %", "Max Drawdown %", "Sharpe", "Calmar", "Return %"],
            "Strategy": [
                summary.get("CAGR %", 0.0),
                summary.get("Max Drawdown %", 0.0),
                summary.get("Sharpe", 0.0),
                summary.get("Calmar", 0.0),
                summary.get("Return %", 0.0),
            ],
            BENCHMARK_SYMBOL: [
                benchmark_stats.get("CAGR %", 0.0),
                benchmark_stats.get("Max Drawdown %", 0.0),
                benchmark_stats.get("Sharpe", 0.0),
                benchmark_stats.get("Calmar", 0.0),
                summary.get(f"{BENCHMARK_SYMBOL} Return %", 0.0),
            ],
        }
    )
    print(f"\n--- Strategy vs {BENCHMARK_SYMBOL} ---")
    print(comparison.to_string(index=False))

    if not result.selection_history.empty:
        print("\n--- Recent Rebalance Selections ---")
        selection_history = result.selection_history.copy()
        selection_history["Datetime"] = pd.to_datetime(selection_history["Datetime"], errors="coerce")
        selection_start = pd.Timestamp(BACKTEST_START_DATE)
        selection_datetimes = selection_history["Datetime"]
        if hasattr(selection_datetimes.dt, "tz") and selection_datetimes.dt.tz is not None:
            selection_start = selection_start.tz_localize(selection_datetimes.dt.tz)
        selection_history = selection_history[selection_history["Datetime"] >= selection_start]
        print(selection_history.tail(PRINT_TOP_REBALANCES).to_string(index=False))

    if PLOT_RESULT and not benchmark_curve.empty:
        plot_equity_comparison(
            strategy_equity=equity_df["Equity"],
            benchmark_equity=benchmark_curve,
            benchmark_label=BENCHMARK_SYMBOL,
            title=f"11-1 Momentum vs {BENCHMARK_SYMBOL}",
        )


if __name__ == "__main__":
    run_strategy_backtest()
