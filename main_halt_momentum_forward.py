import argparse
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from schwab.client import Client

from client import AutoRefreshSchwabClient
from halt_momentum_backtester import HaltMomentumBacktester, OhlcvHaltDetector
from providers.schwab_lib import SchwabProvider
from utils import get_us_stocks


ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "logs" / "halt_momentum_forward"


def parse_args():
    parser = argparse.ArgumentParser(description="Daily post-close halt-momentum forward test")
    parser.add_argument("--date", help="Market date in YYYY-MM-DD; defaults to today ET")
    parser.add_argument("--symbols", nargs="*", help="Optional symbol subset; defaults to all US stocks")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Root output directory")
    parser.add_argument("--force", action="store_true", help="Allow running before close or on a closed date")
    parser.add_argument("--install-task", action="store_true", help="Install a weekday Windows scheduled task")
    parser.add_argument("--task-time", default="16:15", help="Local task start time in HH:MM")
    parser.add_argument("--progress-every", type=int, default=50, help="Print progress every N symbols")
    return parser.parse_args()


def market_close_for(client, market_date: date) -> datetime | None:
    response = client.get_market_hours(Client.MarketHours.Market.EQUITY, date=market_date)
    if response.status_code != httpx.codes.OK:
        raise RuntimeError(f"Unable to fetch market hours: HTTP {response.status_code}")
    equity = response.json().get("equity", {})
    market = equity.get("EQ") or equity.get("equity") or {}
    if not market.get("isOpen", False):
        return None
    sessions = market.get("sessionHours", {}).get("regularMarket", [])
    if not sessions or not sessions[0].get("end"):
        raise RuntimeError("Schwab response has no regular-market close time")
    return datetime.fromisoformat(sessions[0]["end"])


def summarize_day(trades: pd.DataFrame, symbols_requested: int, progress: pd.DataFrame) -> pd.DataFrame:
    row = {
        "SymbolsRequested": symbols_requested,
        "SymbolsCompleted": progress["Symbol"].nunique() if not progress.empty else 0,
        "SymbolsWithData": progress.loc[progress["Status"].eq("OK"), "Symbol"].nunique() if not progress.empty else 0,
        "Events": len(trades),
        "Wins": 0,
        "Losses": 0,
        "WinRatePct": 0.0,
        "AverageReturnPct": 0.0,
        "MedianReturnPct": 0.0,
        "CompoundedReturnPct": 0.0,
        "StrictTrades": 0,
        "StrictWinRatePct": 0.0,
        "StrictAverageReturnPct": 0.0,
        "StrictCompoundedReturnPct": 0.0,
    }
    if trades.empty:
        return pd.DataFrame([row])

    returns = trades["ReturnPct"] / 100
    strict = trades[trades["ExitReason"].ne("TIME_NEXT_TRADE")]
    strict_returns = strict["ReturnPct"] / 100
    row.update(
        {
            "Wins": int((returns > 0).sum()),
            "Losses": int((returns < 0).sum()),
            "WinRatePct": (returns > 0).mean() * 100,
            "AverageReturnPct": returns.mean() * 100,
            "MedianReturnPct": returns.median() * 100,
            "CompoundedReturnPct": ((1 + returns).prod() - 1) * 100,
            "StrictTrades": len(strict),
            "StrictWinRatePct": (strict_returns > 0).mean() * 100 if len(strict) else 0.0,
            "StrictAverageReturnPct": strict_returns.mean() * 100 if len(strict) else 0.0,
            "StrictCompoundedReturnPct": ((1 + strict_returns).prod() - 1) * 100 if len(strict) else 0.0,
        }
    )
    return pd.DataFrame([row])


def render_report(market_date: date, summary: pd.DataFrame, trades: pd.DataFrame) -> str:
    row = summary.iloc[0]
    lines = [
        f"Halt Momentum Forward Test | {market_date.isoformat()}",
        "=" * 54,
        f"Universe completed: {int(row.SymbolsCompleted):,}/{int(row.SymbolsRequested):,}",
        f"Symbols with data: {int(row.SymbolsWithData):,}",
        f"Trades: {int(row.Events)} | Wins: {int(row.Wins)} | Losses: {int(row.Losses)}",
        f"Win rate: {row.WinRatePct:.2f}% | Average: {row.AverageReturnPct:+.2f}% | Median: {row.MedianReturnPct:+.2f}%",
        f"Sequential compounded return: {row.CompoundedReturnPct:+.2f}%",
        "",
        "Strict execution (excludes delayed TIME_NEXT_TRADE fills):",
        f"Trades: {int(row.StrictTrades)} | Win rate: {row.StrictWinRatePct:.2f}% | Average: {row.StrictAverageReturnPct:+.2f}%",
        f"Sequential compounded return: {row.StrictCompoundedReturnPct:+.2f}%",
    ]
    if not trades.empty:
        lines.extend(("", "Exit reasons:"))
        lines.extend(f"  {reason}: {count}" for reason, count in trades["ExitReason"].value_counts().items())
        lines.extend(("", "Trades:"))
        for trade in trades.sort_values("EntryTime").itertuples(index=False):
            lines.append(
                f"  {trade.Symbol} {trade.EntryTime} entry={trade.EntryPrice:.4f} "
                f"exit={trade.ExitPrice:.4f} return={trade.ReturnPct:+.2f}% {trade.ExitReason}"
            )
    return "\n".join(lines)


def install_task(task_time: str):
    time.fromisoformat(task_time)
    command = f'"{sys.executable}" "{Path(__file__).resolve()}"'
    result = subprocess.run(
        [
            "schtasks", "/Create", "/TN", "HaltMomentumForwardDaily", "/TR", command,
            "/SC", "WEEKLY", "/D", "MON,TUE,WED,THU,FRI", "/ST", task_time, "/F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    print(f"Installed HaltMomentumForwardDaily for weekdays at {task_time} local time")


def run_forward_test(
    provider: SchwabProvider,
    market_date: date,
    symbols: list[str],
    output_dir: Path,
    progress_every: int = 50,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.csv"
    events_path = output_dir / "events.csv"
    trades_path = output_dir / "trades.csv"

    progress = pd.read_csv(progress_path, keep_default_na=False) if progress_path.exists() else pd.DataFrame()
    completed = set(progress.loc[progress["Status"].eq("OK"), "Symbol"]) if not progress.empty else set()
    detector = OhlcvHaltDetector()
    backtester = HaltMomentumBacktester()
    consecutive_empty = 0

    for index, symbol in enumerate(symbols, start=1):
        if symbol in completed:
            continue
        bars = provider.get_history(symbol, "minute1", period="2d")
        if bars.empty:
            consecutive_empty += 1
            status = "NO_DATA"
            day_bars = bars
        else:
            consecutive_empty = 0
            bars = bars.copy()
            timestamps = pd.to_datetime(bars["Datetime"])
            day_bars = bars[timestamps.dt.date == market_date].copy()
            status = "OK"

        if consecutive_empty >= 10:
            probe = provider.get_history("AAPL", "minute1", period="1d")
            if probe.empty:
                raise RuntimeError("Schwab connectivity failed; rerun the same command to resume")
            consecutive_empty = 0

        event_count = 0
        trade_count = 0
        if not day_bars.empty:
            day_bars["Symbol"] = symbol
            events = detector.detect(day_bars)
            trades = backtester.run(day_bars, events) if not events.empty else pd.DataFrame()
            event_count = len(events)
            trade_count = len(trades)
            if event_count:
                events.to_csv(events_path, mode="a", header=not events_path.exists(), index=False)
            if trade_count:
                trades.to_csv(trades_path, mode="a", header=not trades_path.exists(), index=False)

        pd.DataFrame(
            [{"Symbol": symbol, "Status": status, "Bars": len(day_bars), "Events": event_count, "Trades": trade_count}]
        ).to_csv(progress_path, mode="a", header=not progress_path.exists(), index=False)
        if event_count or index % progress_every == 0:
            print(f"[{index}/{len(symbols)}] {symbol}: {len(day_bars)} bars, {event_count} event(s)")

    progress = pd.read_csv(progress_path, keep_default_na=False).groupby("Symbol", as_index=False).tail(1)
    events = pd.read_csv(events_path) if events_path.exists() else pd.DataFrame()
    trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
    if not events.empty:
        events = events.drop_duplicates()
        events.to_csv(events_path, index=False)
    if not trades.empty:
        trades = trades.drop_duplicates()
        trades.to_csv(trades_path, index=False)
    summary = summarize_day(trades, len(symbols), progress)
    summary.to_csv(output_dir / "summary.csv", index=False)
    report = render_report(market_date, summary, trades)
    (output_dir / "summary.txt").write_text(report, encoding="utf-8")
    return summary, trades, report


def main():
    args = parse_args()
    if args.install_task:
        install_task(args.task_time)
        return

    market_date = date.fromisoformat(args.date) if args.date else datetime.now(ET).date()
    client_wrapper = AutoRefreshSchwabClient()
    close_time = market_close_for(client_wrapper.client, market_date)
    if close_time is None and not args.force:
        raise RuntimeError(f"US equity market was closed on {market_date}")
    if close_time and datetime.now(ET) < close_time and not args.force:
        raise RuntimeError(f"Run after market close at {close_time.astimezone(ET):%H:%M ET}")

    symbols = list(dict.fromkeys(args.symbols or get_us_stocks()))
    if not symbols:
        raise RuntimeError("No US stock symbols available")
    daily_dir = args.output / market_date.isoformat()
    summary, _, report = run_forward_test(
        SchwabProvider(client_wrapper), market_date, symbols, daily_dir, args.progress_every
    )
    print("\n" + report)
    if int(summary.iloc[0]["SymbolsCompleted"]) < len(symbols):
        print("\nWARNING: Scan is incomplete. Rerun the same command to resume.")
    print(f"\nSaved: {daily_dir.resolve()}")


if __name__ == "__main__":
    main()