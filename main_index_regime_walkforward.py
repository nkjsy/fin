import pandas as pd

from data_manager import DataManager
from providers.yfinance_lib import YFinanceProvider

from main_index_regime_daily import (
    DATA_DIR,
    TIMEFRAME,
    INITIAL_CAPITAL,
    PERIOD,
    END_DATE,
    normalize_datetime_col,
    resolve_train_window,
    build_regime_classifier,
    apply_regime_exposure_targets,
    run_target_exposure_backtest,
    build_buy_and_hold_equity_curve,
    compute_buy_and_hold_metrics,
    compute_performance_stats,
)


# --- WALK-FORWARD CONFIGURATION (CONSTANTS ONLY) ---
SYMBOLS = ["QQQ", "SPY", "IWM"]
WALKFORWARD_TEST_BARS = 252
MIN_SEGMENTS_REQUIRED = 3
PRINT_SEGMENT_TABLES = True
# --------------------------------------------------


def slice_walkforward_segments(df: pd.DataFrame, warmup_bars: int, test_bars: int) -> list[tuple[int, int, int]]:
    """Return consecutive out-of-sample segment boundaries.

    Each tuple is `(segment_id, start_idx, end_idx)` with `end_idx` exclusive.
    Segments begin only after the initial warmup period needed by the regime model.
    """
    segments = []
    start_idx = max(1, int(warmup_bars))
    segment_id = 1

    while start_idx < len(df):
        end_idx = min(start_idx + test_bars, len(df))
        if end_idx - start_idx < max(63, test_bars // 2):
            break
        segments.append((segment_id, start_idx, end_idx))
        segment_id += 1
        start_idx = end_idx

    return segments


def summarize_segment(symbol: str, prepared_df: pd.DataFrame, segment_id: int, start_idx: int, end_idx: int) -> dict:
    """Backtest one walk-forward segment using precomputed daily target exposures."""
    segment = prepared_df.iloc[start_idx:end_idx].copy()
    segment_res, segment_trades, segment_metrics = run_target_exposure_backtest(segment, INITIAL_CAPITAL)
    bh = compute_buy_and_hold_metrics(segment, INITIAL_CAPITAL)
    bh_equity = build_buy_and_hold_equity_curve(segment, INITIAL_CAPITAL)

    exposure_pct = float(segment_res["Actual_Exposure"].mean() * 100.0) if not segment_res.empty else 0.0
    strat_stats = compute_performance_stats(segment_res["Equity"], INITIAL_CAPITAL, exposure_pct)
    bh_stats = compute_performance_stats(bh_equity, INITIAL_CAPITAL, 100.0)

    start_dt = pd.to_datetime(segment.iloc[0]["Datetime"])
    end_dt = pd.to_datetime(segment.iloc[-1]["Datetime"])

    return {
        "Symbol": symbol,
        "Segment": segment_id,
        "Start": start_dt.date().isoformat(),
        "End": end_dt.date().isoformat(),
        "Bars": len(segment),
        "Strategy Return %": round(float(segment_metrics["Return %"]), 2),
        "BuyHold Return %": round(float(bh["Return %"]), 2),
        "Excess Return %": round(float(segment_metrics["Return %"] - bh["Return %"]), 2),
        "Trades": int(segment_metrics["Trades"]),
        "Sharpe": round(float(strat_stats["Sharpe"]), 2),
        "BuyHold Sharpe": round(float(bh_stats["Sharpe"]), 2),
        "MaxDD %": round(float(strat_stats["Max Drawdown %"]), 2),
        "BuyHold MaxDD %": round(float(bh_stats["Max Drawdown %"]), 2),
        "Exposure %": round(float(strat_stats["Exposure %"]), 2),
        "Bull Days": int((segment["Regime_Final"] == "BULL_TREND").sum()),
        "Bear Days": int((segment["Regime_Final"] == "BEAR_TREND").sum()),
        "Range Days": int((segment["Regime_Final"] == "RANGE").sum()),
        "NoTrade Days": int((segment["Regime_Final"] == "NO_TRADE").sum()),
        "Buy Signals": int((segment_res["Signal"] == 1).sum()),
        "Sell Signals": int((segment_res["Signal"] == -1).sum()),
    }


def run_symbol_walkforward(data_manager: DataManager, symbol: str) -> tuple[pd.DataFrame, dict]:
    """Run full-sample and segmented walk-forward validation for one symbol."""
    success = data_manager.download_data(symbol, TIMEFRAME, period=PERIOD, end_date=END_DATE)
    if not success:
        raise RuntimeError(f"Failed to download data for {symbol}.")

    df = data_manager.load_data(symbol, TIMEFRAME)
    if df.empty:
        raise RuntimeError(f"Data empty for {symbol}.")

    train_window = resolve_train_window(len(df))
    classifier = build_regime_classifier(train_window)
    regime_df = classifier.classify(df)
    prepared_df = normalize_datetime_col(apply_regime_exposure_targets(regime_df))

    full_res, full_trades, full_metrics = run_target_exposure_backtest(prepared_df, INITIAL_CAPITAL)
    bh = compute_buy_and_hold_metrics(full_res, INITIAL_CAPITAL)
    bh_equity = build_buy_and_hold_equity_curve(full_res, INITIAL_CAPITAL)

    exposure_pct = float(full_res["Actual_Exposure"].mean() * 100.0) if not full_res.empty else 0.0
    full_stats = compute_performance_stats(full_res["Equity"], INITIAL_CAPITAL, exposure_pct)
    bh_stats = compute_performance_stats(bh_equity, INITIAL_CAPITAL, 100.0)

    segments = slice_walkforward_segments(prepared_df, train_window, WALKFORWARD_TEST_BARS)
    segment_rows = [summarize_segment(symbol, prepared_df, segment_id, start_idx, end_idx) for segment_id, start_idx, end_idx in segments]
    segment_df = pd.DataFrame(segment_rows)

    summary = {
        "Symbol": symbol,
        "TrainWindow": train_window,
        "Rows": len(df),
        "Segments": len(segment_df),
        "Strategy Return %": round(float(full_metrics["Return %"]), 2),
        "BuyHold Return %": round(float(bh["Return %"]), 2),
        "Excess Return %": round(float(full_metrics["Return %"] - bh["Return %"]), 2),
        "Trades": int(full_metrics["Trades"]),
        "Strategy Sharpe": round(float(full_stats["Sharpe"]), 2),
        "BuyHold Sharpe": round(float(bh_stats["Sharpe"]), 2),
        "Strategy Calmar": round(float(full_stats["Calmar"]), 2),
        "BuyHold Calmar": round(float(bh_stats["Calmar"]), 2),
        "Strategy MaxDD %": round(float(full_stats["Max Drawdown %"]), 2),
        "BuyHold MaxDD %": round(float(bh_stats["Max Drawdown %"]), 2),
        "Exposure %": round(float(full_stats["Exposure %"]), 2),
        "Positive Segments": int((segment_df["Strategy Return %"] > 0).sum()) if not segment_df.empty else 0,
        "Beat BuyHold Segments": int((segment_df["Excess Return %"] > 0).sum()) if not segment_df.empty else 0,
        "Median Segment Return %": round(float(segment_df["Strategy Return %"].median()), 2) if not segment_df.empty else 0.0,
        "Median Segment Excess %": round(float(segment_df["Excess Return %"].median()), 2) if not segment_df.empty else 0.0,
    }
    return segment_df, summary


def main():
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)

    print("--- Index Regime Walk-Forward Validation ---")
    print(f"Symbols: {', '.join(SYMBOLS)} | Test Bars: {WALKFORWARD_TEST_BARS} | Period: {PERIOD}")

    all_segments = []
    summary_rows = []
    for symbol in SYMBOLS:
        segment_df, summary = run_symbol_walkforward(data_manager, symbol)
        summary_rows.append(summary)
        if not segment_df.empty:
            all_segments.append(segment_df)

        print(f"\n[{symbol}] Full-sample summary")
        print(pd.DataFrame([summary]).to_string(index=False))
        if PRINT_SEGMENT_TABLES and not segment_df.empty:
            print(f"\n[{symbol}] Walk-forward segments")
            print(segment_df.to_string(index=False))
        elif summary["Segments"] < MIN_SEGMENTS_REQUIRED:
            print(f"[{symbol}] Only {summary['Segments']} walk-forward segments available.")

    summary_df = pd.DataFrame(summary_rows)
    print("\n=== Cross-Symbol Summary ===")
    print(summary_df.to_string(index=False))

    if all_segments:
        merged_segments = pd.concat(all_segments, ignore_index=True)
        aggregate = {
            "Total Segments": int(len(merged_segments)),
            "Positive Segments": int((merged_segments["Strategy Return %"] > 0).sum()),
            "Beat BuyHold Segments": int((merged_segments["Excess Return %"] > 0).sum()),
            "Median Segment Return %": round(float(merged_segments["Strategy Return %"].median()), 2),
            "Median Segment Excess %": round(float(merged_segments["Excess Return %"].median()), 2),
            "Median Segment Sharpe": round(float(merged_segments["Sharpe"].median()), 2),
            "Median Segment MaxDD %": round(float(merged_segments["MaxDD %"].median()), 2),
        }
        print("\n=== Aggregate Segment Summary ===")
        print(pd.DataFrame([aggregate]).to_string(index=False))


if __name__ == "__main__":
    main()