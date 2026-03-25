import argparse
import sys
from datetime import datetime

from broker import PaperBroker, SchwabBroker
from client import AutoRefreshSchwabClient
from data_manager import DataManager
from live_momentum_portfolio import MomentumLiveTrader, ET
from logger import enable_file_logging, get_logger
from providers.schwab_lib import SchwabProvider
from strategy.momentum_11_1 import Momentum11_1Strategy
from utils import wait_until_time

from main_momentum_11_1 import (
    DATA_DIR,
    INITIAL_CAPITAL,
    LOOKBACK_DAYS,
    SKIP_DAYS,
    TOP_N,
    BENCHMARK_SYMBOL,
    HISTORICAL_MEMBERSHIP_FILE,
    BACKTEST_START_DATE,
    load_historical_nasdaq100_membership,
)


enable_file_logging()
logger = get_logger("MOMO-LIVE-MAIN")

HISTORY_PERIOD = "3y"
REBALANCE_HOUR = 15
REBALANCE_MINUTE = 55
PRICE_BUFFER_PCT = 0.002


def parse_args():
    parser = argparse.ArgumentParser(description="Live monthly 11-1 momentum rebalancer")
    parser.add_argument("--live", action="store_true", help="Place real orders via Schwab")
    parser.add_argument("--execute-now", action="store_true", help="Execute immediately instead of waiting for rebalance time")
    parser.add_argument("--force", action="store_true", help="Execute even if today is not estimated month-end")
    parser.add_argument("--initial-cash", type=float, default=INITIAL_CAPITAL, help="Paper trading starting cash")
    parser.add_argument("--top-n", type=int, default=TOP_N, help="Number of holdings in the live portfolio")
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=" * 60)
    logger.info("LIVE MONTHLY MOMENTUM REBALANCER")
    logger.info(f"Mode: {'LIVE' if args.live else 'PAPER'} | TopN: {args.top_n} | Benchmark: {BENCHMARK_SYMBOL}")
    logger.info("=" * 60)

    try:
        client_wrapper = AutoRefreshSchwabClient()
    except Exception as exc:
        logger.error(f"Authentication failed: {exc}")
        sys.exit(1)

    provider = SchwabProvider(client_wrapper)
    data_manager = DataManager(DATA_DIR, provider)
    broker = SchwabBroker(client_wrapper.client) if args.live else PaperBroker(initial_cash=args.initial_cash)
    strategy = Momentum11_1Strategy(
        lookback_days=LOOKBACK_DAYS,
        skip_days=SKIP_DAYS,
        top_n=args.top_n,
    )

    universe_by_month, universe = load_historical_nasdaq100_membership(strategy)
    logger.info(
        f"Historical universe loaded from {HISTORICAL_MEMBERSHIP_FILE} | "
        f"months={len(universe_by_month)} | unique_tickers={len(universe)} | start={BACKTEST_START_DATE}"
    )

    trader = MomentumLiveTrader(
        client_wrapper=client_wrapper,
        broker=broker,
        data_manager=data_manager,
        strategy=strategy,
        universe_by_month=universe_by_month,
        history_period=HISTORY_PERIOD,
        benchmark_symbol=BENCHMARK_SYMBOL,
        price_buffer_pct=PRICE_BUFFER_PCT,
    )

    now_et = datetime.now(ET)
    if not args.force and not trader.is_rebalance_day(now_et):
        logger.warning(f"Today ({now_et.date()}) is not estimated month-end. Use --force to override.")
        return

    if not args.execute_now:
        wait_until_time(REBALANCE_HOUR, REBALANCE_MINUTE, description="monthly momentum rebalance")

    plan = trader.build_rebalance_plan(datetime.now(ET))
    logger.info(f"Selected symbols: {plan.selected_symbols}")
    logger.info(f"Cash=${plan.cash:,.2f} | Total Equity=${plan.total_equity:,.2f} | Orders={len(plan.orders)}")

    for order in plan.orders:
        logger.info(
            f"PLAN: {order.side.value} {order.quantity} {order.symbol} | "
            f"current={order.current_shares} -> target={order.target_shares} | ref=${order.reference_price:.2f}"
        )

    trader.execute_rebalance(plan, live=args.live)
    logger.info("Monthly momentum rebalance complete")

    if hasattr(broker, "print_summary"):
        broker.print_summary()


if __name__ == "__main__":
    main()
