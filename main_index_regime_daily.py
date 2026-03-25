import pandas as pd
import numpy as np

from data_manager import DataManager
from plotting import plot_performance
from providers.yfinance_lib import YFinanceProvider
from strategy import (
    DailyRegimeClassifier,
    RegimeThresholds,
)


# --- CONFIGURATION (CONSTANTS ONLY) ---
DATA_DIR = "data"
TIMEFRAME = "daily1"
INITIAL_CAPITAL = 10000.0

# Index-only trading target
SYMBOL = "QQQ"  # Example: QQQ / SPY / IWM

# Data window
PERIOD = "20y"
END_DATE = None  # e.g. "2026-02-26" (exclusive in yfinance)

# Regime model params
AUTO_REGIME_TRAIN_WINDOW = True
REGIME_TRAIN_WINDOW = 504
REGIME_TRAIN_WINDOW_RATIO = 0.30
REGIME_TRAIN_WINDOW_MIN = 252
REGIME_TRAIN_WINDOW_MAX = 756
REGIME_CONFIRM_DAYS = 1  # 1 keeps lag <= 1 day in operational use

# Confidence thresholds
TREND_ENTER = 0.70
TREND_EXIT = 0.50
RANGE_ENTER = 0.35
USE_ADAPTIVE_THRESHOLDS = True
ADAPTIVE_THRESHOLD_WINDOW = 168
TREND_ENTER_QUANTILE = 0.75
TREND_EXIT_QUANTILE = 0.60
RANGE_ENTER_QUANTILE = 0.25
TREND_ENTER_FLOOR = 0.62
TREND_EXIT_FLOOR = 0.48
RANGE_ENTER_FLOOR = 0.18
TREND_ENTER_CEILING = 0.85
TREND_EXIT_CEILING = 0.68
RANGE_ENTER_CEILING = 0.38

# 3-state model params
SLOW_TREND_EMA_LENGTH = 200
SLOW_TREND_SLOPE_LOOKBACK = 84
P_BULL_TO_BULL = 0.97
P_BEAR_TO_BEAR = 0.98
P_RANGE_TO_RANGE = 0.96

# Target exposure by regime
BULL_TARGET_EXPOSURE = 1.00
RANGE_TARGET_EXPOSURE = 1.00
NO_TRADE_TARGET_EXPOSURE = 0.10
BEAR_TARGET_EXPOSURE = 0.00

# Display
PLOT_RESULT = True
PRINT_DIAGNOSTICS = True
# -------------------------------------


def normalize_datetime_col(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure a `Datetime` column exists and has datetime dtype."""
    out = df.copy()
    if "Datetime" not in out.columns and "Date" in out.columns:
        out["Datetime"] = pd.to_datetime(out["Date"], errors="coerce")
    elif "Datetime" in out.columns and not pd.api.types.is_datetime64_any_dtype(out["Datetime"]):
        out["Datetime"] = pd.to_datetime(out["Datetime"], errors="coerce")
    return out


def resolve_train_window(num_rows: int) -> int:
    """Resolve regime training window from sample length.

    When auto mode is enabled, use a ratio of available rows and clamp it
    between configured minimum and maximum bounds.
    """
    if not AUTO_REGIME_TRAIN_WINDOW:
        return REGIME_TRAIN_WINDOW

    window = int(num_rows * REGIME_TRAIN_WINDOW_RATIO)
    window = max(REGIME_TRAIN_WINDOW_MIN, window)
    window = min(REGIME_TRAIN_WINDOW_MAX, window)
    window = min(window, max(1, num_rows - 1))
    return window


def build_regime_classifier(train_window: int) -> DailyRegimeClassifier:
    """Build daily regime classifier from constants."""

    thresholds = RegimeThresholds(
        trend_enter=TREND_ENTER,
        trend_exit=TREND_EXIT,
        range_enter=RANGE_ENTER,
    )
    regime = DailyRegimeClassifier(
        train_window=train_window,
        thresholds=thresholds,
        p_bull_to_bull=P_BULL_TO_BULL,
        p_bear_to_bear=P_BEAR_TO_BEAR,
        p_range_to_range=P_RANGE_TO_RANGE,
        slow_ema_length=SLOW_TREND_EMA_LENGTH,
        slow_slope_lookback=SLOW_TREND_SLOPE_LOOKBACK,
        confirm_days=REGIME_CONFIRM_DAYS,
        adaptive_thresholds=USE_ADAPTIVE_THRESHOLDS,
        adaptive_threshold_window=ADAPTIVE_THRESHOLD_WINDOW,
        trend_enter_quantile=TREND_ENTER_QUANTILE,
        trend_exit_quantile=TREND_EXIT_QUANTILE,
        range_enter_quantile=RANGE_ENTER_QUANTILE,
        trend_enter_floor=TREND_ENTER_FLOOR,
        trend_exit_floor=TREND_EXIT_FLOOR,
        range_enter_floor=RANGE_ENTER_FLOOR,
        trend_enter_ceiling=TREND_ENTER_CEILING,
        trend_exit_ceiling=TREND_EXIT_CEILING,
        range_enter_ceiling=RANGE_ENTER_CEILING,
    )
    return regime


def apply_regime_exposure_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Create effective regime labels and target exposure schedule.

    Execution lag policy (<= 1 day):
    - Use regime decided at day t close.
    - Execute transition at day t+1 open via shifted regime label.

    Exposure policy:
    - `BULL_TREND` => full exposure
    - `RANGE` => partial exposure
    - `NO_TRADE` => defensive partial exposure
    - `BEAR_TREND` => flat
    """
    out = df.copy()

    if "Open" not in out.columns:
        raise ValueError("Input DataFrame must contain 'Open' column for next-day execution prices.")

    effective_regime = out["Regime_Final"].shift(1)

    out["Effective_Regime"] = effective_regime
    exposure_map = {
        "BULL_TREND": BULL_TARGET_EXPOSURE,
        "RANGE": RANGE_TARGET_EXPOSURE,
        "NO_TRADE": NO_TRADE_TARGET_EXPOSURE,
        "BEAR_TREND": BEAR_TARGET_EXPOSURE,
    }
    out["Target_Exposure"] = effective_regime.map(exposure_map).fillna(BEAR_TARGET_EXPOSURE)

    return out


def run_target_exposure_backtest(df: pd.DataFrame, initial_capital: float):
    """Run a daily open rebalance backtest to target regime exposure.

    The portfolio is rebalanced at each bar open toward the target exposure.
    Equity is then marked to the close of the same bar.
    """
    out = df.copy()
    if out.empty:
        return out, pd.DataFrame(), {
            "Final-Equity": initial_capital,
            "Return %": 0.0,
            "Trades": 0,
        }

    date_col = "Datetime" if "Datetime" in out.columns else "Date"
    if date_col not in out.columns:
        out[date_col] = out.index

    cash = float(initial_capital)
    shares = 0
    trade_log = []
    equity_curve = []
    position_state = []
    actual_exposure = []
    active_target_exposure = None

    out["Signal"] = 0
    out["Entry_Price"] = 0.0
    out["Exit_Price"] = 0.0
    out["Stop_Loss"] = 0.0

    for idx, row in out.iterrows():
        open_price = float(row["Open"])
        close_price = float(row["Close"])
        target_exposure = float(row.get("Target_Exposure", 0.0))
        datetime_val = row[date_col]

        if open_price <= 0:
            equity = cash + shares * close_price
            equity_curve.append(equity)
            position_state.append("IN_POSITION" if shares > 0 else "FLAT")
            actual_exposure.append((shares * close_price / equity) if equity > 0 else 0.0)
            continue

        total_equity_open = cash + shares * open_price
        rebalance_needed = active_target_exposure is None or not np.isclose(target_exposure, active_target_exposure)
        if rebalance_needed:
            target_value = total_equity_open * target_exposure
            target_shares = int(target_value // open_price)
            delta_shares = target_shares - shares
        else:
            target_shares = shares
            delta_shares = 0

        if delta_shares > 0:
            cost = delta_shares * open_price
            affordable_shares = int(cash // open_price)
            delta_shares = min(delta_shares, affordable_shares)
            if delta_shares > 0:
                cost = delta_shares * open_price
                cash -= cost
                shares += delta_shares
                out.at[idx, "Signal"] = 1
                out.at[idx, "Entry_Price"] = open_price
                trade_log.append({
                    "Datetime": datetime_val,
                    "Action": "BUY",
                    "Price": open_price,
                    "Shares": delta_shares,
                    "Value": cost,
                    "Qty": target_exposure,
                })
                active_target_exposure = target_exposure
        elif delta_shares < 0:
            sell_shares = min(shares, abs(delta_shares))
            if sell_shares > 0:
                revenue = sell_shares * open_price
                cash += revenue
                shares -= sell_shares
                out.at[idx, "Signal"] = -1
                out.at[idx, "Exit_Price"] = open_price
                prev_equity = total_equity_open if total_equity_open > 0 else 1.0
                qty_ratio = revenue / prev_equity
                trade_log.append({
                    "Datetime": datetime_val,
                    "Action": "SELL",
                    "Price": open_price,
                    "Shares": sell_shares,
                    "Value": revenue,
                    "Qty": qty_ratio,
                })
                active_target_exposure = target_exposure

        if delta_shares == 0 and rebalance_needed:
            active_target_exposure = target_exposure

        equity = cash + shares * close_price
        equity_curve.append(equity)
        position_state.append("IN_POSITION" if shares > 0 else "FLAT")
        actual_exposure.append((shares * close_price / equity) if equity > 0 else 0.0)

    out["Equity"] = equity_curve
    out["Position_State"] = position_state
    out["Actual_Exposure"] = actual_exposure

    results = {
        "Final-Equity": equity_curve[-1],
        "Return %": ((equity_curve[-1] - initial_capital) / initial_capital) * 100.0,
        "Trades": len(trade_log),
    }
    return out, pd.DataFrame(trade_log), results


def print_diagnostics(df_res: pd.DataFrame, train_window: int):
    """Print regime and signal diagnostics for parameter tuning."""
    if not PRINT_DIAGNOSTICS:
        return

    buy_count = int((df_res.get("Signal", pd.Series(dtype=int)) == 1).sum())
    sell_count = int((df_res.get("Signal", pd.Series(dtype=int)) == -1).sum())

    bull_days = int((df_res.get("Regime_Final", pd.Series(dtype=object)) == "BULL_TREND").sum())
    bear_days = int((df_res.get("Regime_Final", pd.Series(dtype=object)) == "BEAR_TREND").sum())
    range_days = int((df_res.get("Regime_Final", pd.Series(dtype=object)) == "RANGE").sum())
    no_trade_days = int((df_res.get("Regime_Final", pd.Series(dtype=object)) == "NO_TRADE").sum())

    eff_bull_days = int((df_res.get("Effective_Regime", pd.Series(dtype=object)) == "BULL_TREND").sum())
    eff_bear_days = int((df_res.get("Effective_Regime", pd.Series(dtype=object)) == "BEAR_TREND").sum())
    eff_range_days = int((df_res.get("Effective_Regime", pd.Series(dtype=object)) == "RANGE").sum())
    eff_no_trade_days = int((df_res.get("Effective_Regime", pd.Series(dtype=object)) == "NO_TRADE").sum())
    held_days = int((df_res.get("Position_State", pd.Series(dtype=object)) == "IN_POSITION").sum())
    avg_target_exposure = float(df_res.get("Target_Exposure", pd.Series(dtype=float)).mean() * 100.0)
    avg_actual_exposure = float(df_res.get("Actual_Exposure", pd.Series(dtype=float)).mean() * 100.0)

    p_trend = df_res.get("P_Trend", pd.Series(dtype=float)).dropna()
    p_bull = df_res.get("P_Bull", pd.Series(dtype=float)).dropna()
    p_bear = df_res.get("P_Bear", pd.Series(dtype=float)).dropna()
    p_range = df_res.get("P_Range", pd.Series(dtype=float)).dropna()
    mean_trend_prob = float(p_trend.mean()) if not p_trend.empty else float("nan")
    mean_bull_prob = float(p_bull.mean()) if not p_bull.empty else float("nan")
    mean_bear_prob = float(p_bear.mean()) if not p_bear.empty else float("nan")
    mean_range_prob = float(p_range.mean()) if not p_range.empty else float("nan")
    trend_enter_thr = df_res.get("Trend_Enter_Threshold", pd.Series(dtype=float)).dropna()
    trend_exit_thr = df_res.get("Trend_Exit_Threshold", pd.Series(dtype=float)).dropna()
    range_enter_thr = df_res.get("Range_Enter_Threshold", pd.Series(dtype=float)).dropna()
    mean_trend_enter_thr = float(trend_enter_thr.mean()) if not trend_enter_thr.empty else float("nan")
    mean_trend_exit_thr = float(trend_exit_thr.mean()) if not trend_exit_thr.empty else float("nan")
    mean_range_enter_thr = float(range_enter_thr.mean()) if not range_enter_thr.empty else float("nan")

    print(
        "Diagnostics | "
        f"TrainWindow: {train_window} ({'AUTO' if AUTO_REGIME_TRAIN_WINDOW else 'MANUAL'}), "
        f"AdaptiveThresholds: {'ON' if USE_ADAPTIVE_THRESHOLDS else 'OFF'} ({ADAPTIVE_THRESHOLD_WINDOW}), "
        f"SlowTrendEMA/Slope: {SLOW_TREND_EMA_LENGTH}/{SLOW_TREND_SLOPE_LOOKBACK}, "
        f"Raw Bull/Bear/Range/NoTrade: {bull_days}/{bear_days}/{range_days}/{no_trade_days}, "
        f"Effective Bull/Bear/Range/NoTrade: {eff_bull_days}/{eff_bear_days}/{eff_range_days}/{eff_no_trade_days}, "
        f"HeldDays: {held_days}, AvgTargetExposure: {avg_target_exposure:.1f}%, AvgActualExposure: {avg_actual_exposure:.1f}%, "
        f"BuySignals: {buy_count}, SellSignals: {sell_count}, "
        f"Avg P(Bull/Bear/Range/Trend): {mean_bull_prob:.3f}/{mean_bear_prob:.3f}/{mean_range_prob:.3f}/{mean_trend_prob:.3f}, "
        f"Avg Thr(Enter/Exit/Range): {mean_trend_enter_thr:.3f}/{mean_trend_exit_thr:.3f}/{mean_range_enter_thr:.3f}"
    )


def compute_buy_and_hold_metrics(df: pd.DataFrame, initial_capital: float) -> dict:
    """Compute buy-and-hold baseline over the same data window.

    Baseline assumptions:
    - Buy at first bar Open with integer share sizing.
    - Hold until last bar Close.
    """
    if df.empty:
        return {
            "Final-Equity": initial_capital,
            "Return %": 0.0,
            "Shares": 0,
            "Entry_Price": 0.0,
            "Exit_Price": 0.0,
        }

    entry_price = float(df.iloc[0]["Open"])
    exit_price = float(df.iloc[-1]["Close"])

    if entry_price <= 0:
        return {
            "Final-Equity": initial_capital,
            "Return %": 0.0,
            "Shares": 0,
            "Entry_Price": entry_price,
            "Exit_Price": exit_price,
        }

    shares = int(initial_capital // entry_price)
    cash_left = initial_capital - shares * entry_price
    final_equity = cash_left + shares * exit_price
    ret_pct = ((final_equity - initial_capital) / initial_capital) * 100.0

    return {
        "Final-Equity": final_equity,
        "Return %": ret_pct,
        "Shares": shares,
        "Entry_Price": entry_price,
        "Exit_Price": exit_price,
    }


def build_buy_and_hold_equity_curve(df: pd.DataFrame, initial_capital: float) -> pd.Series:
    """Build a daily buy-and-hold equity curve over the same backtest window."""
    if df.empty:
        return pd.Series(dtype=float)

    entry_price = float(df.iloc[0]["Open"])
    if entry_price <= 0:
        return pd.Series([initial_capital] * len(df), index=df.index, dtype=float)

    shares = int(initial_capital // entry_price)
    cash_left = initial_capital - shares * entry_price
    return cash_left + shares * df["Close"].astype(float)


def compute_performance_stats(
    equity_curve: pd.Series,
    initial_capital: float,
    exposure_pct: float,
    bars_per_year: int = 252,
) -> dict:
    """Compute common performance statistics from an equity curve."""
    if equity_curve.empty:
        return {
            "CAGR %": 0.0,
            "Max Drawdown %": 0.0,
            "Sharpe": 0.0,
            "Calmar": 0.0,
            "Exposure %": exposure_pct,
        }

    equity = equity_curve.astype(float)
    total_return = equity.iloc[-1] / initial_capital - 1.0 if initial_capital > 0 else 0.0
    years = len(equity) / bars_per_year if len(equity) > 0 else 0.0
    if years > 0 and equity.iloc[-1] > 0 and initial_capital > 0:
        cagr = (equity.iloc[-1] / initial_capital) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

    daily_returns = equity.pct_change().dropna()
    if not daily_returns.empty and daily_returns.std(ddof=0) > 0:
        sharpe = float((daily_returns.mean() / daily_returns.std(ddof=0)) * np.sqrt(bars_per_year))
    else:
        sharpe = 0.0

    if max_drawdown < 0:
        calmar = float(cagr / abs(max_drawdown))
    else:
        calmar = 0.0

    return {
        "CAGR %": cagr * 100.0,
        "Max Drawdown %": max_drawdown * 100.0,
        "Sharpe": sharpe,
        "Calmar": calmar,
        "Exposure %": exposure_pct,
    }


def main():
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)

    print("--- Daily Index Regime Backtest ---")
    print(f"Symbol: {SYMBOL} | Timeframe: {TIMEFRAME} | Period: {PERIOD} | End Date: {END_DATE}")

    success = data_manager.download_data(SYMBOL, TIMEFRAME, period=PERIOD, end_date=END_DATE)
    if not success:
        print(f"Failed to download data for {SYMBOL}.")
        return

    df = data_manager.load_data(SYMBOL, TIMEFRAME)
    if df.empty:
        print(f"Data empty for {SYMBOL}.")
        return

    train_window = resolve_train_window(len(df))
    regime_classifier = build_regime_classifier(train_window)

    print(
        f"Regime train window: {train_window} rows "
        f"({'AUTO' if AUTO_REGIME_TRAIN_WINDOW else 'MANUAL'})"
    )

    df_regime = regime_classifier.classify(df)
    df_prepared = apply_regime_exposure_targets(df_regime)

    df_res, trades, metrics = run_target_exposure_backtest(df_prepared, INITIAL_CAPITAL)
    df_res = normalize_datetime_col(df_res)
    bh = compute_buy_and_hold_metrics(df_res, INITIAL_CAPITAL)
    bh_equity = build_buy_and_hold_equity_curve(df_res, INITIAL_CAPITAL)

    strategy_exposure = 0.0
    if "Actual_Exposure" in df_res.columns and len(df_res) > 0:
        strategy_exposure = float(df_res["Actual_Exposure"].mean() * 100.0)

    strategy_stats = compute_performance_stats(
        df_res["Equity"],
        INITIAL_CAPITAL,
        exposure_pct=strategy_exposure,
    )
    baseline_stats = compute_performance_stats(
        bh_equity,
        INITIAL_CAPITAL,
        exposure_pct=100.0,
    )

    print_diagnostics(df_res, train_window)

    print("\n--- Results ---")
    print(f"Final Equity: ${metrics['Final-Equity']:.2f}")
    print(f"Return:       {metrics['Return %']:.2f}%")
    print(f"Total Trades: {metrics['Trades']}")

    print("\n--- Baseline (Buy & Hold) ---")
    print(f"Entry Open:   ${bh['Entry_Price']:.2f}")
    print(f"Exit Close:   ${bh['Exit_Price']:.2f}")
    print(f"Shares:       {bh['Shares']}")
    print(f"Final Equity: ${bh['Final-Equity']:.2f}")
    print(f"Return:       {bh['Return %']:.2f}%")

    excess_ret = metrics["Return %"] - bh["Return %"]
    excess_equity = metrics["Final-Equity"] - bh["Final-Equity"]
    print("\n--- Strategy vs Baseline ---")
    print(f"Excess Return: {excess_ret:.2f}%")
    print(f"Excess Equity: ${excess_equity:.2f}")

    print("\n--- Risk Metrics Comparison ---")
    comparison = pd.DataFrame(
        {
            "Metric": ["CAGR %", "Max Drawdown %", "Sharpe", "Calmar", "Exposure %"],
            "Strategy": [
                strategy_stats["CAGR %"],
                strategy_stats["Max Drawdown %"],
                strategy_stats["Sharpe"],
                strategy_stats["Calmar"],
                strategy_stats["Exposure %"],
            ],
            "Buy & Hold": [
                baseline_stats["CAGR %"],
                baseline_stats["Max Drawdown %"],
                baseline_stats["Sharpe"],
                baseline_stats["Calmar"],
                baseline_stats["Exposure %"],
            ],
        }
    )
    print(comparison.to_string(index=False, formatters={
        "Strategy": lambda x: f"{x:.2f}",
        "Buy & Hold": lambda x: f"{x:.2f}",
    }))

    if not trades.empty:
        print("\n--- Trade Log ---")
        print(trades.to_string(index=False))
    else:
        print("\nNo trades executed.")

    if PLOT_RESULT:
        plot_performance(
            SYMBOL,
            df_res,
            trades,
            TIMEFRAME,
            "Regime-Only Execution",
            title_prefix="INDEX REGIME:",
            trading_date=None,
            show_states=False,
        )


if __name__ == "__main__":
    main()
