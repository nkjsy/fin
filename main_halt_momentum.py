import argparse
from pathlib import Path

import pandas as pd

from client import AutoRefreshSchwabClient
from halt_momentum_backtester import HaltMomentumBacktester, OhlcvHaltDetector
from providers.schwab_lib import SchwabProvider
from utils import get_us_stocks


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest inferred upward halts from Schwab 1-minute OHLCV")
    parser.add_argument("--symbols", nargs="*", help="Symbols to scan; defaults to Nasdaq screener symbols")
    parser.add_argument("--period", default="1y", help="Schwab lookback hint, defaults to 1y")
    parser.add_argument("--output", default="logs/halt_momentum", help="Output directory")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted scan from the output directory")
    parser.add_argument("--progress-every", type=int, default=25, help="Print progress every N symbols")
    return parser.parse_args()


def summarize(
    trades: pd.DataFrame,
    period: str,
    data_start,
    data_end,
    symbols_requested: int,
    symbols_scanned: int,
) -> pd.DataFrame:
    coverage_days = (data_end - data_start).days if pd.notna(data_start) and pd.notna(data_end) else 0
    coverage = {
        "RequestedPeriod": period,
        "DataStart": data_start,
        "DataEnd": data_end,
        "CoverageDays": coverage_days,
        "SymbolsRequested": symbols_requested,
        "SymbolsScanned": symbols_scanned,
    }
    if trades.empty:
        return pd.DataFrame(
            [{**coverage, "Trades": 0, "WinRatePct": 0.0, "AverageReturnPct": 0.0, "CompoundedReturnPct": 0.0}]
        )
    returns = trades["ReturnPct"] / 100
    return pd.DataFrame(
        [
            {
                **coverage,
                "Trades": len(trades),
                "WinRatePct": (returns > 0).mean() * 100,
                "AverageReturnPct": returns.mean() * 100,
                "MedianReturnPct": returns.median() * 100,
                "CompoundedReturnPct": ((1 + returns).prod() - 1) * 100,
            }
        ]
    )


def main():
    args = parse_args()
    symbols = list(dict.fromkeys(args.symbols or get_us_stocks()))
    if not symbols:
        raise RuntimeError("No symbols supplied or returned by the Nasdaq screener")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "events.csv"
    trades_path = output_dir / "trades.csv"
    progress_path = output_dir / "progress.csv"

    completed = set()
    if args.resume and progress_path.exists():
        progress = pd.read_csv(progress_path, keep_default_na=False)
        completed = set(progress.loc[progress["Status"].eq("OK"), "Symbol"].astype(str))
        print(f"Resuming: {len(completed)} successful symbols already scanned; empty responses will retry")
    elif not args.resume:
        for path in (events_path, trades_path, progress_path):
            path.unlink(missing_ok=True)

    provider = SchwabProvider(AutoRefreshSchwabClient())
    detector = OhlcvHaltDetector()
    backtester = HaltMomentumBacktester()
    all_events = []
    all_trades = []
    data_start = pd.NaT
    data_end = pd.NaT
    consecutive_empty = 0

    if args.resume and events_path.exists():
        all_events.append(pd.read_csv(events_path))
    if args.resume and trades_path.exists():
        all_trades.append(pd.read_csv(trades_path))

    for index, symbol in enumerate(symbols, start=1):
        if symbol in completed:
            continue
        bars = provider.get_history(symbol, "minute1", period=args.period)
        if bars.empty:
            consecutive_empty += 1
            if consecutive_empty >= 10:
                probe = provider.get_history("AAPL", "minute1", period="1d")
                if probe.empty:
                    raise RuntimeError(
                        f"Schwab connectivity check failed after {consecutive_empty} empty responses; "
                        "rerun with --resume when connectivity returns"
                    )
                consecutive_empty = 0
            status = "NO_DATA"
            event_count = 0
            bar_count = 0
            symbol_start = pd.NaT
            symbol_end = pd.NaT
        else:
            consecutive_empty = 0
            bars = bars.copy()
            bars["Symbol"] = symbol
            events = detector.detect(bars)
            trades = backtester.run(bars, events) if not events.empty else pd.DataFrame()
            bar_count = len(bars)
            event_count = len(events)
            symbol_start = bars["Datetime"].min()
            symbol_end = bars["Datetime"].max()
            data_start = symbol_start if pd.isna(data_start) else min(data_start, symbol_start)
            data_end = symbol_end if pd.isna(data_end) else max(data_end, symbol_end)
            status = "OK"
            if not events.empty:
                all_events.append(events)
                events.to_csv(events_path, mode="a", header=not events_path.exists(), index=False)
            if not trades.empty:
                all_trades.append(trades)
                trades.to_csv(trades_path, mode="a", header=not trades_path.exists(), index=False)

        pd.DataFrame(
            [{
                "Symbol": symbol,
                "Status": status,
                "Bars": bar_count,
                "Events": event_count,
                "DataStart": symbol_start,
                "DataEnd": symbol_end,
            }]
        ).to_csv(progress_path, mode="a", header=not progress_path.exists(), index=False)

        if event_count or index % args.progress_every == 0:
            print(f"[{index}/{len(symbols)}] {symbol}: {bar_count} bars, {event_count} event(s)")

    events = pd.concat(all_events, ignore_index=True).drop_duplicates() if all_events else pd.DataFrame()
    trades = pd.concat(all_trades, ignore_index=True).drop_duplicates() if all_trades else pd.DataFrame()
    if progress_path.exists():
        progress = pd.read_csv(progress_path, keep_default_na=False)
        progress["DataStart"] = pd.to_datetime(progress["DataStart"], errors="coerce", utc=True)
        progress["DataEnd"] = pd.to_datetime(progress["DataEnd"], errors="coerce", utc=True)
        data_start = progress["DataStart"].min()
        data_end = progress["DataEnd"].max()
        symbols_scanned = progress["Symbol"].nunique()
    else:
        symbols_scanned = 0

    summary = summarize(
        trades,
        args.period,
        data_start,
        data_end,
        len(symbols),
        symbols_scanned,
    )

    if args.period == "1y" and int(summary.iloc[0]["CoverageDays"]) < 300:
        print(
            f"WARNING: Schwab returned only {summary.iloc[0]['CoverageDays']} calendar days "
            "of one-minute data for the requested 1y period."
        )

    events.to_csv(output_dir / "events.csv", index=False)
    trades.to_csv(output_dir / "trades.csv", index=False)
    summary.to_csv(output_dir / "summary.csv", index=False)

    print("\n", summary.to_string(index=False))
    if not trades.empty:
        print("\nExit reasons:")
        print(trades["ExitReason"].value_counts().to_string())
    print(f"Saved results to {output_dir.resolve()}")


if __name__ == "__main__":
    main()