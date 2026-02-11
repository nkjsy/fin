"""
Combined Bull Flag + ORB Day Trading

Two-phase strategy in one session:
  Phase 1 (7:00–9:30)  — Bull flag on premarket momentum stocks via Finviz scanner.
                          Collects ALL confirmed symbols into a candidate pool.
  Phase 2 (9:30–close) — Opening Range Breakout on every collected candidate.
                          Bull flag positions that are still open continue to ride.

Usage:
    python main_combined.py [--live]

Timeline:
    7:00 AM    - Start Phase 1: scan Finviz every 60 sec, run bull flag
    9:30 AM    - Transition: stop scanning, remove idle bull flag symbols,
                 start ORB engine on all confirmed candidates
    9:30-4:00  - Phase 2: ORB builds range, trades breakouts. Bull flag
                 positions ride until exit.
    4:00 PM    - Market close, force stop
"""

import argparse
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from client import AutoRefreshSchwabClient
from broker import PaperBroker, SchwabBroker
from broker.interfaces import OrderSide, OrderType
from providers.schwab_lib import SchwabProvider
from scanner.finviz_news import FinvizNewsScanner
from live_engine import LiveTradingEngine
from strategy.base import StrategyState
from strategy.orb_live import ORBLiveStrategy
from logger import get_logger, enable_file_logging, get_log_file_path
from plotting import plot_from_log
from utils import speak_symbols


ET = ZoneInfo("America/New_York")
logger = get_logger("COMBINED")

# ── Time constants ────────────────────────────────────────────────────────────
BF_CUTOFF = dt_time(9, 30)     # Bull flag stops scanning, ORB starts
FORCE_CLOSE_TIME = dt_time(15, 55)  # Force-close all positions at 3:55 PM
MARKET_CLOSE = dt_time(16, 0)

# ── Scan constants ────────────────────────────────────────────────────────────
SCAN_INTERVAL = 60  # seconds

# ── Bull flag constants ───────────────────────────────────────────────────────
BF_MAX_SYMBOLS = 3
BF_REPLAY_MINUTES = 5

# ── ORB constants ─────────────────────────────────────────────────────────────
ORB_RANGE_MINUTES = 15
ORB_MAX_RANGE_PCT = 0.08  # skip if opening range > 8% of price
ORB_VOLUME_MULTIPLIER = 1.5  # breakout candle volume >= avg range vol * this

# ── Shared constants ─────────────────────────────────────────────────────────
POSITION_AMOUNT = 10000  # $10k per position


def _orb_factory(symbol: str, on_signal) -> ORBLiveStrategy:
    """Factory for creating ORB strategy instances."""
    return ORBLiveStrategy(
        symbol=symbol,
        range_minutes=ORB_RANGE_MINUTES,
        max_range_pct=ORB_MAX_RANGE_PCT,
        volume_multiplier=ORB_VOLUME_MULTIPLIER,
        on_signal=on_signal,
    )


def main():
    parser = argparse.ArgumentParser(description="Combined Bull Flag + ORB Trading")
    parser.add_argument("--live", action="store_true", help="Use live trading (default: paper)")
    args = parser.parse_args()

    enable_file_logging()

    logger.info("=" * 60)
    logger.info("Combined Bull Flag + ORB Strategy")
    logger.info(f"Mode: {'LIVE' if args.live else 'PAPER'}")
    logger.info(f"Phase 1: Bull flag | max {BF_MAX_SYMBOLS} symbols | until {BF_CUTOFF}")
    logger.info(f"Phase 2: ORB | {ORB_RANGE_MINUTES}-min range | max range {ORB_MAX_RANGE_PCT:.0%} | vol x{ORB_VOLUME_MULTIPLIER}")
    logger.info(f"Position size: ${POSITION_AMOUNT:,}")
    logger.info("=" * 60)

    # ── shared components ─────────────────────────────────────────────────────
    client_wrapper = AutoRefreshSchwabClient()
    broker = SchwabBroker(client_wrapper) if args.live else PaperBroker()
    provider = SchwabProvider(client_wrapper)
    scanner = FinvizNewsScanner(provider)

    # ── Phase 1 engine (bull flag, premarket) ─────────────────────────────────
    bf_engine = LiveTradingEngine(
        client_wrapper=client_wrapper,
        broker=broker,
        symbols=[],
        candle_interval=1,
        extended_hours=True,
        position_amount=POSITION_AMOUNT,
        max_symbols=BF_MAX_SYMBOLS,
        remove_symbol=True,
        # default strategy_factory → BullFlagLiveStrategy
    )

    # Collect ALL scanner-confirmed symbols for ORB Phase 2
    all_confirmed: set[str] = set()

    try:
        # ══════════════════════════════════════════════════════════════════════
        # PHASE 1 — Bull Flag Premarket (7:00–9:30)
        # ══════════════════════════════════════════════════════════════════════
        logger.info("=" * 40)
        logger.info("PHASE 1: Bull Flag Premarket")
        logger.info("=" * 40)

        bf_engine.running = True
        last_scan_time = 0
        last_candle_slot = bf_engine._datetime_to_slot(datetime.now(ET)) - 1

        while bf_engine.running:
            now_et = datetime.now(ET)
            now_time = now_et.time()

            # Transition to Phase 2 at 9:30
            if now_time >= BF_CUTOFF:
                logger.info("Reached 9:30 AM — transitioning to Phase 2 (ORB)")
                break

            # Market close safety
            if now_time >= MARKET_CLOSE:
                logger.info("Market closed — stopping")
                break

            current_ts = time.time()

            # === SCAN PHASE (every 60 sec) ===
            if current_ts - last_scan_time >= SCAN_INTERVAL:
                last_scan_time = current_ts

                if len(bf_engine.strategies) < BF_MAX_SYMBOLS:
                    logger.info("--- Scanning ---")
                    skip_symbols = set(bf_engine.strategies.keys())
                    confirmed = scanner.scan(skip=skip_symbols)

                    if confirmed:
                        speak_symbols(confirmed)

                    # Record ALL confirmed symbols for ORB
                    all_confirmed.update(confirmed)

                    for symbol in confirmed:
                        if len(bf_engine.strategies) >= BF_MAX_SYMBOLS:
                            break
                        bf_engine.add_symbol(symbol, replay_minutes=BF_REPLAY_MINUTES)

                    if bf_engine.strategies:
                        states = {s: st.state.value for s, st in bf_engine.strategies.items()}
                        logger.info(f"Tracking ({len(bf_engine.strategies)}/{BF_MAX_SYMBOLS}): {states}")

            # === CANDLE PHASE ===
            current_slot = bf_engine._datetime_to_slot(now_et)
            if current_slot != last_candle_slot:
                bf_engine._process_candles()
                last_candle_slot = current_slot

            # === QUOTE PHASE ===
            if bf_engine._needs_realtime_polling():
                bf_engine._check_realtime_triggers()
                time.sleep(bf_engine.REALTIME_POLL_INTERVAL)
            else:
                candle_poll_interval = bf_engine.candle_interval * 60
                seconds_into_slot = (now_et.minute % bf_engine.candle_interval) * 60 + now_et.second
                time_to_next_candle = candle_poll_interval - seconds_into_slot + 5
                time_to_next_scan = max(0, SCAN_INTERVAL - (current_ts - last_scan_time))
                sleep_time = min(time_to_next_candle, time_to_next_scan)
                time.sleep(max(1, sleep_time))

        # ══════════════════════════════════════════════════════════════════════
        # TRANSITION — clean up bull flag, prepare ORB
        # ══════════════════════════════════════════════════════════════════════
        logger.info("=" * 40)
        logger.info("TRANSITION: Bull Flag → ORB")
        logger.info(f"All confirmed candidates from Phase 1: {all_confirmed or '(none)'}")
        logger.info("=" * 40)

        # Keep bull flag symbols that are IN_POSITION (let them ride)
        # Remove everything else from bf_engine
        bf_to_remove = []
        bf_in_position = []
        for sym, strat in bf_engine.strategies.items():
            if strat.state == StrategyState.IN_POSITION:
                bf_in_position.append(sym)
            else:
                bf_to_remove.append(sym)

        for sym in bf_to_remove:
            logger.info(f"Removing idle bull flag symbol: {sym} (state={bf_engine.strategies[sym].state.value})")
            bf_engine._remove_symbol(sym)

        if bf_in_position:
            logger.info(f"Bull flag positions still open (riding): {bf_in_position}")
        else:
            logger.info("No open bull flag positions")

        # ── Create ORB engine ─────────────────────────────────────────────────
        orb_engine = None
        if all_confirmed:
            orb_engine = LiveTradingEngine(
                client_wrapper=client_wrapper,
                broker=broker,
                symbols=[],
                candle_interval=1,
                extended_hours=False,
                position_amount=POSITION_AMOUNT,
                max_symbols=len(all_confirmed) + 10,  # effectively unlimited
                remove_symbol=True,
                strategy_factory=_orb_factory,
            )
            orb_engine.running = True

            # Add all confirmed candidates — no replay needed for ORB
            for symbol in all_confirmed:
                orb_engine.add_symbol(symbol, replay_minutes=0)

            logger.info(f"ORB engine started with {len(orb_engine.strategies)} symbols")
        else:
            logger.info("No confirmed candidates from Phase 1 — ORB will not run")

        # ══════════════════════════════════════════════════════════════════════
        # PHASE 2 — ORB + Bull Flag wind-down (9:30–close)
        # ══════════════════════════════════════════════════════════════════════
        logger.info("=" * 40)
        logger.info("PHASE 2: ORB Trading")
        logger.info("=" * 40)

        # Track candle slots for both engines
        now_et = datetime.now(ET)
        bf_last_slot = bf_engine._datetime_to_slot(now_et) - 1 if bf_engine.strategies else -1
        orb_last_slot = orb_engine._datetime_to_slot(now_et) - 1 if orb_engine else -1

        while True:
            now_et = datetime.now(ET)
            now_time = now_et.time()

            if now_time >= FORCE_CLOSE_TIME:
                logger.info("Approaching market close — exiting loop for force-close")
                break

            # Check if both engines are done
            bf_active = bool(bf_engine.strategies)
            orb_active = orb_engine is not None and bool(orb_engine.strategies)

            if not bf_active and not orb_active:
                logger.info("No active strategies in either engine — stopping")
                break

            # === CANDLE PHASE — both engines ===
            if bf_active:
                current_slot = bf_engine._datetime_to_slot(now_et)
                if current_slot != bf_last_slot:
                    bf_engine._process_candles()
                    bf_last_slot = current_slot

            if orb_active:
                current_slot = orb_engine._datetime_to_slot(now_et)
                if current_slot != orb_last_slot:
                    orb_engine._process_candles()
                    orb_last_slot = current_slot

            # === QUOTE PHASE — both engines ===
            needs_polling = False

            if bf_active and bf_engine._needs_realtime_polling():
                bf_engine._check_realtime_triggers()
                needs_polling = True

            if orb_active and orb_engine._needs_realtime_polling():
                orb_engine._check_realtime_triggers()
                needs_polling = True

            if needs_polling:
                time.sleep(LiveTradingEngine.REALTIME_POLL_INTERVAL)
            else:
                # Sleep until next candle boundary
                candle_poll_interval = 60  # 1-min candles
                seconds_into_slot = (now_et.minute % 1) * 60 + now_et.second
                time_to_next_candle = candle_poll_interval - seconds_into_slot + 5
                time.sleep(max(1, time_to_next_candle))

        # ══════════════════════════════════════════════════════════════════════
        # FORCE-CLOSE — sell any remaining positions at market close
        # ══════════════════════════════════════════════════════════════════════
        try:
            positions = broker.get_positions()
            open_positions = [p for p in positions if p.quantity > 0]
            if open_positions:
                logger.info(f"Force-closing {len(open_positions)} position(s) before market close")
                # Fetch last prices for fill simulation (needed by PaperBroker)
                active_engine = orb_engine if orb_engine else bf_engine
                symbols = [p.symbol for p in open_positions]
                prices = active_engine._fetch_quotes(symbols)
                for pos in open_positions:
                    price = prices.get(pos.symbol, pos.current_price) or pos.current_price
                    broker.place_order(
                        symbol=pos.symbol,
                        side=OrderSide.SELL,
                        quantity=pos.quantity,
                        order_type=OrderType.MARKET,
                        limit_price=price,  # PaperBroker needs a price to simulate fill
                        reason="Market close — force exit",
                    )
                    logger.info(f"Force-closed {pos.symbol}: {pos.quantity} shares @ ${price:.2f}")
            else:
                logger.info("No open positions — nothing to force-close")
        except Exception as e:
            logger.info(f"Error during force-close: {e}")

        # ══════════════════════════════════════════════════════════════════════
        # SUMMARY
        # ══════════════════════════════════════════════════════════════════════
        logger.info("=" * 40)
        logger.info("SUMMARY")
        logger.info("=" * 40)
        if hasattr(broker, "print_summary"):
            broker.print_summary()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        if hasattr(broker, "print_summary"):
            broker.print_summary()
    except Exception as e:
        logger.info(f"Error: {e}")
        raise

    # Visualize log file
    log_path = get_log_file_path()
    if log_path:
        logger.info(f"Generating charts from log: {log_path}")
        plot_from_log(log_path)


if __name__ == "__main__":
    main()
