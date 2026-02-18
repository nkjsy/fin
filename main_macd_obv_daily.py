import pandas as pd

from backtester import BacktestEngine
from data_manager import DataManager
from plotting import plot_performance
from providers.yfinance_lib import YFinanceProvider
from scanner import YFScanner
from strategy import MacdObvDivergenceStrategy


# --- CONFIGURATION (NO CLI ARGS) ---
DATA_DIR = "data"
TIMEFRAME = "daily1"
INITIAL_CAPITAL = 10000.0

RUN_MODE = "SINGLE"  # Options: SINGLE, SCAN

# SINGLE mode
SYMBOL = "NVDA"

# SCAN mode
SCAN_SOURCE = "YF_SCREEN"
SCAN_TOP_N = 50
SCAN_MIN_PRICE = 5.0
SCAN_MIN_VOLUME = 500000

# Data window
PERIOD = "2y"
END_DATE = None  # e.g. "2026-02-17" (exclusive in yfinance)

# Strategy params
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
OBV_MA = 30
PIVOT_WINDOW = 3
MAX_PIVOT_GAP = 120
CONFIRMATION_WINDOW = 10
PRICE_TOLERANCE_PCT = 0.003
DIFF_TOLERANCE_ABS = 0.03

# Display
PLOT_SINGLE_RESULT = True
PLOT_BEST_WORST_IN_SCAN = False
PRINT_SIGNAL_DIAGNOSTICS = True
# -----------------------------------


def build_strategy() -> MacdObvDivergenceStrategy:
    """Create a strategy instance from top-level constants."""
    return MacdObvDivergenceStrategy(
        macd_fast=MACD_FAST,
        macd_slow=MACD_SLOW,
        macd_signal=MACD_SIGNAL,
        obv_ma=OBV_MA,
        pivot_window=PIVOT_WINDOW,
        max_pivot_gap=MAX_PIVOT_GAP,
        confirmation_window=CONFIRMATION_WINDOW,
        price_tolerance_pct=PRICE_TOLERANCE_PCT,
        diff_tolerance_abs=DIFF_TOLERANCE_ABS,
    )


def normalize_datetime_col(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure a `Datetime` column exists and is parsed as datetime dtype."""
    df = df.copy()
    if "Datetime" not in df.columns and "Date" in df.columns:
        df["Datetime"] = pd.to_datetime(df["Date"], errors="coerce")
    elif "Datetime" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["Datetime"]):
        df["Datetime"] = pd.to_datetime(df["Datetime"], errors="coerce")
    return df


def run_one_symbol(ticker: str, data_manager: DataManager, strategy: MacdObvDivergenceStrategy):
    """Download/load one symbol, run backtest, and return a result dictionary."""
    print(f"\nProcessing {ticker}...")
    success = data_manager.download_data(
        ticker,
        TIMEFRAME,
        period=PERIOD,
        end_date=END_DATE,
    )
    if not success:
        print(f"Failed to download data for {ticker}. Skipping.")
        return None

    df = data_manager.load_data(ticker, TIMEFRAME)
    if df.empty:
        print(f"Data empty for {ticker}. Skipping.")
        return None

    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    df_res, trades, metrics = engine.run(df, strategy)
    df_res = normalize_datetime_col(df_res)

    if PRINT_SIGNAL_DIAGNOSTICS:
        bull_div_count = int(df_res.get("Bullish_Divergence", pd.Series(dtype=bool)).sum())
        bear_div_count = int(df_res.get("Bearish_Divergence", pd.Series(dtype=bool)).sum())
        obv_bull_cross_count = int(df_res.get("OBV_Bull_Cross", pd.Series(dtype=bool)).sum())
        obv_bear_cross_count = int(df_res.get("OBV_Bear_Cross", pd.Series(dtype=bool)).sum())
        buy_signal_count = int((df_res.get("Signal", pd.Series(dtype=int)) == 1).sum())
        sell_signal_count = int((df_res.get("Signal", pd.Series(dtype=int)) == -1).sum())

        print(
            "Signal diagnostics | "
            f"BullDiv: {bull_div_count}, BearDiv: {bear_div_count}, "
            f"OBV BullCross: {obv_bull_cross_count}, OBV BearCross: {obv_bear_cross_count}, "
            f"BuySignals: {buy_signal_count}, SellSignals: {sell_signal_count}"
        )

    print(
        f"{ticker} | Final Equity: ${metrics['Final-Equity']:.2f} | "
        f"Return: {metrics['Return %']:.2f}% | Trades: {metrics['Trades']}"
    )

    return {
        "Ticker": ticker,
        "Final-Equity": metrics["Final-Equity"],
        "Return %": metrics["Return %"],
        "Trades": metrics["Trades"],
        "Trades_Log": trades,
        "Data": df_res,
    }


def get_scan_tickers() -> list[str]:
    """Fetch scan candidates from configured scanner source and apply top-N cap."""
    if SCAN_SOURCE != "YF_SCREEN":
        print(f"Unsupported SCAN_SOURCE={SCAN_SOURCE}, fallback to YF_SCREEN")

    scanner = YFScanner(DATA_DIR)
    tickers = scanner.scan(min_price=SCAN_MIN_PRICE, min_volume=SCAN_MIN_VOLUME) or []

    if not tickers:
        return []

    if SCAN_TOP_N > 0:
        return tickers[:SCAN_TOP_N]
    return tickers


def main():
    """Run daily MACD+OBV backtest in SINGLE or SCAN mode using constants only."""
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)
    strategy = build_strategy()

    print("--- Daily MACD + OBV Divergence Backtest ---")
    print(f"Mode: {RUN_MODE} | Timeframe: {TIMEFRAME} | Period: {PERIOD} | End Date: {END_DATE}")

    if RUN_MODE == "SINGLE":
        result = run_one_symbol(SYMBOL, data_manager, strategy)
        if result is None:
            print("No result generated.")
            return

        print("\n--- Single Symbol Result ---")
        print(pd.DataFrame([{k: v for k, v in result.items() if k not in ["Trades_Log", "Data"]}]).to_string(index=False))

        if PLOT_SINGLE_RESULT:
            plot_performance(
                SYMBOL,
                result["Data"],
                result["Trades_Log"],
                TIMEFRAME,
                strategy.__class__.__name__,
                title_prefix="DAILY:",
                trading_date=None,
                show_states=False,
            )
        return

    if RUN_MODE == "SCAN":
        tickers = get_scan_tickers()
        if not tickers:
            print("Scanner returned no tickers.")
            return

        print(f"Scanner returned {len(tickers)} tickers.")
        results = []
        for ticker in tickers:
            result = run_one_symbol(ticker, data_manager, strategy)
            if result is not None:
                results.append(result)

        if not results:
            print("No valid results generated.")
            return

        summary = pd.DataFrame(
            [{k: v for k, v in item.items() if k not in ["Trades_Log", "Data"]} for item in results]
        ).sort_values("Return %", ascending=False)

        print("\n--- Scan Summary ---")
        print(summary.to_string(index=False))

        if PLOT_BEST_WORST_IN_SCAN:
            best = summary.iloc[0]["Ticker"]
            best_result = next(item for item in results if item["Ticker"] == best)
            plot_performance(
                best,
                best_result["Data"],
                best_result["Trades_Log"],
                TIMEFRAME,
                strategy.__class__.__name__,
                title_prefix="BEST:",
                trading_date=None,
                show_states=False,
            )

            if len(summary) > 1:
                worst = summary.iloc[-1]["Ticker"]
                worst_result = next(item for item in results if item["Ticker"] == worst)
                plot_performance(
                    worst,
                    worst_result["Data"],
                    worst_result["Trades_Log"],
                    TIMEFRAME,
                    strategy.__class__.__name__,
                    title_prefix="WORST:",
                    trading_date=None,
                    show_states=False,
                )
        return

    print(f"Unsupported RUN_MODE={RUN_MODE}. Use SINGLE or SCAN.")


if __name__ == "__main__":
    main()
