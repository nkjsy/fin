from __future__ import annotations

import argparse
from datetime import datetime
from typing import Dict

from broker import PaperBroker, SchwabBroker
from broker.interfaces import OrderSide, OrderType
from client import AutoRefreshSchwabClient
from live_momentum_portfolio import ET
from live_signal_state import load_latest_holdings, write_state_file
from logger import enable_file_logging, get_logger
from strategy.momentum_11_1 import Momentum11_1Strategy

from main_momentum_11_1_regime_immediate import (
    BACKTEST_START_DATE,
    DATA_FILE as HISTORICAL_MEMBERSHIP_FILE,
    LOOKBACK_DAYS,
    SKIP_DAYS,
    TOP_N_UP,
    TOP_N_DOWN,
    MA_DAYS,
    load_membership,
    fetch_history_yf,
)

enable_file_logging()
logger = get_logger('MOMO-LIVE-MAIN')

INITIAL_CAPITAL = 10000.0
SIGNAL_ONLY_DEFAULT = True


def parse_args():
    parser = argparse.ArgumentParser(description='Live 11-1 momentum signal generator / executor')
    parser.add_argument('--live', action='store_true', help='Place real orders via Schwab (not default)')
    parser.add_argument('--initial-cash', type=float, default=INITIAL_CAPITAL, help='Paper trading starting cash')
    return parser.parse_args()


def build_target_plan(as_of: datetime):
    strategy_up = Momentum11_1Strategy(lookback_days=LOOKBACK_DAYS, skip_days=SKIP_DAYS, top_n=TOP_N_UP)
    strategy_down = Momentum11_1Strategy(lookback_days=LOOKBACK_DAYS, skip_days=SKIP_DAYS, top_n=TOP_N_DOWN)

    universe_by_month, universe = load_membership(strategy_up)
    price_data = {}
    for ticker in universe:
        df = fetch_history_yf(ticker)
        if not df.empty:
            price_data[ticker] = df

    benchmark_df = fetch_history_yf('QQQ')
    bench_close = strategy_up.build_close_matrix({'QQQ': benchmark_df}).get('QQQ')
    qqq_ma = bench_close.rolling(MA_DAYS).mean()
    trend_ok = bench_close > qqq_ma

    close_matrix = strategy_up.build_close_matrix(price_data).sort_index().ffill()
    _, sel_up = strategy_up.select_portfolio(close_matrix, eligible_universe_by_date=universe_by_month)
    _, sel_down = strategy_down.select_portfolio(close_matrix, eligible_universe_by_date=universe_by_month)

    latest_date = close_matrix.index[-1]
    mode = 'Top3' if bool(trend_ok.asof(latest_date)) else 'Top10'
    selected = sel_up.get(latest_date, []) if mode == 'Top3' else sel_down.get(latest_date, [])

    quote_symbols = sorted(set(selected) | {'QQQ'})
    latest_quotes: Dict[str, float] = {}
    for s in quote_symbols:
        df = price_data.get(s) if s in price_data else benchmark_df if s == 'QQQ' else None
        if df is not None and not df.empty:
            latest_quotes[s] = float(df['Close'].iloc[-1])

    return mode, selected, latest_quotes


def format_order_lines(current_holdings: Dict[str, int], target_shares: Dict[str, int]) -> list[str]:
    lines: list[str] = []
    symbols = sorted(set(current_holdings.keys()) | set(target_shares.keys()))
    for symbol in symbols:
        current_qty = int(current_holdings.get(symbol, 0))
        target_qty = int(target_shares.get(symbol, 0))
        delta = target_qty - current_qty
        if delta > 0:
            lines.append(f'BUY | {symbol} | delta={delta} | current={current_qty} | target={target_qty}')
        elif delta < 0:
            lines.append(f'SELL | {symbol} | delta={abs(delta)} | current={current_qty} | target={target_qty}')
        else:
            lines.append(f'HOLD | {symbol} | delta=0 | current={current_qty} | target={target_qty}')
    return lines


def main():
    args = parse_args()
    live = bool(args.live)
    signal_only = not live if SIGNAL_ONLY_DEFAULT else False
    now_et = datetime.now(ET)

    mode, selected_symbols, quotes = build_target_plan(now_et)
    current_holdings = load_latest_holdings()

    current_value = 0.0
    for symbol, qty in current_holdings.items():
        px = quotes.get(symbol, 0.0)
        current_value += qty * px
    total_equity = max(args.initial_cash, current_value if current_value > 0 else args.initial_cash)

    target_weight = 1.0 / len(selected_symbols) if selected_symbols else 0.0
    target_shares: Dict[str, int] = {}
    for symbol in selected_symbols:
        px = quotes.get(symbol, 0.0)
        if px > 0:
            target_shares[symbol] = int((total_equity * target_weight) // px)
        else:
            target_shares[symbol] = 0

    order_lines = format_order_lines(current_holdings, target_shares)

    logger.info('=' * 60)
    logger.info('LIVE MOMENTUM SIGNAL GENERATOR')
    logger.info(f'Mode: {mode} | SignalOnly: {signal_only} | AsOf: {now_et.isoformat()}')
    logger.info(f'Selected symbols: {selected_symbols}')
    logger.info(f'Estimated equity baseline: ${total_equity:,.2f}')
    logger.info('-' * 60)
    logger.info('CURRENT HOLDINGS')
    if current_holdings:
        for symbol in sorted(current_holdings):
            logger.info(f'  {symbol}: {current_holdings[symbol]} shares')
    else:
        logger.info('  none')
    logger.info('-' * 60)
    logger.info('TARGET HOLDINGS')
    for symbol in selected_symbols:
        logger.info(f'  {symbol}: target={target_shares[symbol]} shares | close=${quotes.get(symbol, 0.0):.2f}')
    logger.info('-' * 60)
    logger.info('RECOMMENDED ACTIONS')
    for line in order_lines:
        logger.info(f'  {line}')

    if live:
        logger.info('LIVE EXECUTION ENABLED -- placing after-hours LIMIT orders at close price')
        client_wrapper = AutoRefreshSchwabClient()
        broker = SchwabBroker(client_wrapper.client)
        for symbol in sorted(set(current_holdings.keys()) | set(target_shares.keys())):
            current_qty = int(current_holdings.get(symbol, 0))
            target_qty = int(target_shares.get(symbol, 0))
            delta = target_qty - current_qty
            if delta == 0:
                continue
            px = float(quotes.get(symbol, 0.0))
            if px <= 0:
                logger.info(f'SKIP {symbol}: no valid close price')
                continue
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            qty = abs(delta)
            order_id = broker.place_order(
                symbol=symbol,
                side=side,
                quantity=qty,
                order_type=OrderType.LIMIT,
                limit_price=px,
                reason=f'11-1 live signal | {mode} | after-hours limit at close',
            )
            logger.info(f'LIVE ORDER: {side.value} {qty} {symbol} @ ${px:.2f} | order_id={order_id}')

    path = write_state_file(
        as_of=now_et,
        mode=mode,
        current_holdings=current_holdings,
        target_shares=target_shares,
        quotes=quotes,
        orders=order_lines,
        total_equity=total_equity,
    )
    logger.info(f'Wrote state log: {path}')
    logger.info('=' * 60)


if __name__ == '__main__':
    main()
