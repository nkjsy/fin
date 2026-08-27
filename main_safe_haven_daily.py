import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from providers.yfinance_lib import YFinanceProvider
from strategy.safe_haven import PortfolioState, PutPosition, build_daily_plan


ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT / "safe_haven_portfolio.json"
REPORT_DIR = ROOT / "logs" / "safe_haven"
CACHE_DIR = ROOT / "data" / "safe_haven"


def parse_args():
    parser = argparse.ArgumentParser(description="Daily SPY tail-risk insurance reminder")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="Portfolio state JSON file")
    parser.add_argument("--symbol", choices=("SPY", "QQQ"), default="SPY", help="Underlying ETF")
    parser.add_argument("--notify", action="store_true", help="Show a Windows notification after writing the report")
    parser.add_argument("--install-task", action="store_true", help="Install a weekday Windows scheduled task")
    parser.add_argument("--task-time", default="18:00", help="Local task time in HH:MM format")
    return parser.parse_args()


def load_state(path: Path) -> PortfolioState:
    payload = json.loads(path.read_text(encoding="utf-8"))
    puts = [PutPosition(date.fromisoformat(item["expiration"]), float(item["market_value"])) for item in payload.get("puts", [])]
    last_purchase = payload.get("last_put_purchase")
    return PortfolioState(
        spy_value=float(payload["spy_value"]),
        sgov_value=float(payload["sgov_value"]),
        cash=float(payload.get("cash", 0.0)),
        puts=puts,
        year_start_equity=float(payload.get("year_start_equity", 0.0)),
        premium_spent_ytd=float(payload.get("premium_spent_ytd", 0.0)),
        premium_year=int(payload.get("premium_year", 0)),
        last_put_purchase=date.fromisoformat(last_purchase) if last_purchase else None,
        completed_drawdown_stage=int(payload.get("completed_drawdown_stage", 0)),
    )


def fetch_snapshot(symbol: str) -> tuple[date, float, float]:
    history = YFinanceProvider().get_history(symbol, "daily1", period="max")
    if history.empty:
        cache_path = CACHE_DIR / f"{symbol.lower()}_snapshot.json"
        if not cache_path.exists():
            raise RuntimeError(f"Unable to download {symbol} history and no cached snapshot is available")
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return date.fromisoformat(cached["market_date"]), float(cached["close"]), float(cached["all_time_high"])
    date_col = "Datetime" if "Datetime" in history.columns else "Date"
    latest = history.iloc[-1]
    snapshot = (latest[date_col].date(), float(latest["Close"]), float(history["Close"].max()))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{symbol.lower()}_snapshot.json").write_text(
        json.dumps(
            {"market_date": snapshot[0].isoformat(), "close": snapshot[1], "all_time_high": snapshot[2]},
            indent=2,
        ),
        encoding="utf-8",
    )
    return snapshot


def render_report(plan, state: PortfolioState) -> str:
    lines = [
        f"避风港每日操作计划 | {plan.symbol} | {plan.as_of.isoformat()}",
        "=" * 58,
        f"{plan.symbol} 收盘价：${plan.spy_price:,.2f} | 历史高点：${plan.all_time_high:,.2f}",
        f"当前回撤：{plan.drawdown:.2%} | 账户 {plan.symbol} 权重：{plan.spy_weight:.2%}",
        f"策略账户权益：${state.total_equity:,.2f} | Put市值：${state.put_value:,.2f}",
        "",
    ]
    for index, item in enumerate(plan.advice, start=1):
        lines.extend((f"{index}. {item.action}", f"   原因：{item.reason}"))
    lines.extend(("", "仅为规则提醒：下单前请核对实时报价、流动性、税务和实际成交。"))
    return "\n".join(lines)


def install_task(task_time: str, state_path: Path, symbol: str):
    datetime.strptime(task_time, "%H:%M")
    task_name = f"SafeHavenDailyReminder_{symbol}"
    command = f'"{sys.executable}" "{Path(__file__).resolve()}" --state "{state_path.resolve()}" --symbol {symbol} --notify'
    result = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/TR", command, "/SC", "WEEKLY", "/D", "MON,TUE,WED,THU,FRI", "/ST", task_time, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    print(f"Installed {task_name} for weekdays at {task_time} local time.")


def notify_windows(report_path: Path, plan):
    summary = plan.advice[0].action.replace('"', "'")
    subprocess.run(["msg", "*", f"Safe Haven: {summary}\nReport: {report_path}"], check=False)


def main():
    args = parse_args()
    if args.install_task:
        install_task(args.task_time, args.state, args.symbol)
        return
    if not args.state.exists():
        raise FileNotFoundError(f"Portfolio state not found: {args.state}")

    state = load_state(args.state)
    market_date, spy_price, all_time_high = fetch_snapshot(args.symbol)
    plan = build_daily_plan(state, spy_price, all_time_high, market_date, symbol=args.symbol)
    report = render_report(plan, state)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{market_date.isoformat()}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {report_path}")
    if (date.today() - market_date).days > 4:
        print(f"WARNING: Latest {args.symbol} data is stale; do not act before checking the market calendar/data feed.")
    if args.notify:
        notify_windows(report_path, plan)


if __name__ == "__main__":
    main()