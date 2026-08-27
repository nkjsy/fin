import argparse
from pathlib import Path

import pandas as pd

from providers.yfinance_lib import YFinanceProvider
from safe_haven_backtester import SafeHavenBacktester


OUTPUT_DIR = Path(__file__).resolve().parent / "logs" / "safe_haven_backtest"


def parse_args():
    parser = argparse.ArgumentParser(description="Compare modeled SPY and QQQ tail-risk strategies")
    parser.add_argument("--symbols", nargs="+", choices=("SPY", "QQQ"), default=("SPY", "QQQ"))
    parser.add_argument("--start", default="2000-01-03", help="Common backtest start date")
    parser.add_argument("--end", default=None, help="Optional inclusive end date")
    parser.add_argument("--initial-capital", type=float, default=100000.0)
    parser.add_argument("--target-weight", type=float, default=0.85, help="Target ETF exposure, e.g. 0.97")
    parser.add_argument("--lower-band", type=float, default=None, help="Rebalance-up threshold")
    parser.add_argument("--upper-band", type=float, default=None, help="Rebalance-down threshold")
    return parser.parse_args()


def load_close(provider: YFinanceProvider, symbol: str) -> pd.Series:
    history = provider.get_history(symbol, "daily1", period="max")
    if history.empty:
        raise RuntimeError(f"Unable to download {symbol}")
    date_col = "Datetime" if "Datetime" in history.columns else "Date"
    series = history.set_index(pd.to_datetime(history[date_col]).dt.tz_localize(None))["Close"]
    return pd.to_numeric(series, errors="coerce").rename(symbol).sort_index()


def build_common_data(provider: YFinanceProvider, symbols: tuple[str, ...], start: str, end: str | None) -> dict[str, pd.DataFrame]:
    closes = pd.concat([load_close(provider, symbol) for symbol in symbols], axis=1)
    vix = load_close(provider, "^VIX").rename("VIX")
    rate = load_close(provider, "^IRX").rename("Rate")
    combined = pd.concat([closes, vix, rate], axis=1).sort_index()
    combined[["VIX", "Rate"]] = combined[["VIX", "Rate"]].ffill()
    combined = combined.loc[pd.Timestamp(start):pd.Timestamp(end) if end else None]
    combined = combined.dropna(subset=[*symbols, "VIX", "Rate"])
    if combined.empty:
        raise RuntimeError("No common SPY/QQQ/VIX/rate history is available")
    return {symbol: combined[[symbol, "VIX", "Rate"]].rename(columns={symbol: "Close"}) for symbol in symbols}


def main():
    args = parse_args()
    symbols = tuple(args.symbols)
    lower_band = args.lower_band if args.lower_band is not None else max(args.target_weight - 0.05, 0.001)
    upper_band = args.upper_band if args.upper_band is not None else min(args.target_weight + 0.05, 0.999)
    data = build_common_data(YFinanceProvider(), symbols, args.start, args.end)
    engine = SafeHavenBacktester(
        initial_capital=args.initial_capital,
        target_weight=args.target_weight,
        lower_band=lower_band,
        upper_band=upper_band,
    )
    results = [engine.run(data[symbol], symbol) for symbol in symbols]
    no_put_engine = SafeHavenBacktester(
        initial_capital=args.initial_capital,
        target_weight=args.target_weight,
        lower_band=lower_band,
        upper_band=upper_band,
        quarterly_budget=0.0,
        annual_budget=0.0,
    )
    no_put_results = {symbol: no_put_engine.run(data[symbol], symbol) for symbol in symbols}
    for result in results:
        baseline = no_put_results[result.summary["Symbol"]].summary
        result.summary["No Put CAGR %"] = baseline["CAGR %"]
        result.summary["No Put Max Drawdown %"] = baseline["Max Drawdown %"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame([result.summary for result in results])
    exposure_tag = f"{args.target_weight:.0%}".replace("%", "pct")
    summary.to_csv(OUTPUT_DIR / f"comparison_{exposure_tag}.csv", index=False)
    for result in results:
        symbol = result.summary["Symbol"]
        result.equity_curve.to_csv(OUTPUT_DIR / f"{symbol.lower()}_{exposure_tag}_equity.csv")
        result.events.to_csv(OUTPUT_DIR / f"{symbol.lower()}_{exposure_tag}_events.csv", index=False)

    display_columns = [
        "Symbol", "Start", "End", "Final Equity", "CAGR %", "Max Drawdown %",
        "Volatility %", "Sharpe", "Buy Hold CAGR %", "Buy Hold Max Drawdown %",
        "No Put CAGR %", "No Put Max Drawdown %",
        "Total Premium", "Crisis Sales",
    ]
    print("\nMODELED SAFE-HAVEN BACKTEST")
    print(
        f"Assumptions: {args.target_weight:.0%} ETF, {lower_band:.0%}-{upper_band:.0%} bands, "
        "quarterly 0.75% budget, 12-month 30% OTM Put, VIX x 1.25 bounded at 25%-60%, 5% option spread"
    )
    print(summary[display_columns].round(2).to_string(index=False))
    print(f"\nSaved results to: {OUTPUT_DIR}")
    print("WARNING: Options are Black-Scholes modeled from VIX, not historical option quotes.")


if __name__ == "__main__":
    main()