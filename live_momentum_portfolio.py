from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Dict, List
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from broker.interfaces import IBroker, OrderSide, OrderType
from broker.paper_broker import PaperBroker
from data_manager import DataManager
from logger import get_logger
from strategy.momentum_11_1 import Momentum11_1Strategy


logger = get_logger("MOMO-LIVE")
ET = ZoneInfo("America/New_York")


@dataclass
class RebalanceOrder:
    symbol: str
    side: OrderSide
    quantity: int
    reference_price: float
    target_shares: int
    current_shares: int


@dataclass
class RebalancePlan:
    as_of: datetime
    selected_symbols: list[str]
    quotes: dict[str, float]
    target_shares: dict[str, int]
    current_shares: dict[str, int]
    total_equity: float
    cash: float
    orders: list[RebalanceOrder]


class MomentumLiveTrader:
    """Monthly live rebalance executor for the 11-1 momentum portfolio."""

    def __init__(
        self,
        client_wrapper,
        broker: IBroker,
        data_manager: DataManager,
        strategy: Momentum11_1Strategy,
        universe_by_month: dict[pd.Timestamp, list[str]],
        history_period: str = "3y",
        benchmark_symbol: str = "QQQ",
        rebalance_time: dt_time = dt_time(15, 55),
        price_buffer_pct: float = 0.002,
    ):
        self.client_wrapper = client_wrapper
        self.broker = broker
        self.data_manager = data_manager
        self.strategy = strategy
        self.universe_by_month = {
            self._normalize_timestamp(key): self.strategy.normalize_tickers(value)
            for key, value in universe_by_month.items()
        }
        self.history_period = history_period
        self.benchmark_symbol = benchmark_symbol
        self.rebalance_time = rebalance_time
        self.price_buffer_pct = max(0.0, float(price_buffer_pct))

    @property
    def client(self):
        return self.client_wrapper.client

    @staticmethod
    def _normalize_timestamp(value) -> pd.Timestamp:
        ts = pd.Timestamp(value)
        return ts.tz_localize(None) if ts.tz is not None else ts

    def get_current_universe(self, as_of: datetime | None = None) -> list[str]:
        as_of = as_of or datetime.now(ET)
        naive_as_of = self._normalize_timestamp(as_of)
        eligible_months = [month_end for month_end in self.universe_by_month if month_end <= naive_as_of]
        if not eligible_months:
            return []
        latest_month = max(eligible_months)
        return list(self.universe_by_month.get(latest_month, []))

    def is_rebalance_day(self, as_of: datetime | None = None) -> bool:
        as_of = as_of or datetime.now(ET)
        day = pd.Timestamp(as_of.date())
        return day == day + pd.offsets.BMonthEnd(0)

    def fetch_quotes(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}

        try:
            resp = self.client.get_quotes(symbols)
            if resp.status_code != httpx.codes.OK:
                logger.info(f"Failed to get quotes: {resp.status_code}")
                return {}

            data = resp.json()
            quotes = {}
            for symbol, payload in data.items():
                quote = payload.get("quote", payload)
                price = quote.get("lastPrice") or quote.get("mark") or quote.get("askPrice") or 0.0
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    price = 0.0
                if price > 0:
                    quotes[symbol] = price
            return quotes
        except Exception as exc:
            logger.info(f"Error fetching quotes: {exc}")
            return {}

    def _load_symbol_data(self, symbol: str) -> pd.DataFrame:
        df = self.data_manager.load_data(symbol, "daily1")
        if not df.empty:
            return df

        success = self.data_manager.download_data(symbol, "daily1", period=self.history_period)
        if not success:
            return pd.DataFrame()
        return self.data_manager.load_data(symbol, "daily1")

    def _build_live_close_matrix(self, price_data: dict[str, pd.DataFrame], quotes: dict[str, float], as_of: datetime) -> pd.DataFrame:
        close_matrix = self.strategy.build_close_matrix(price_data)
        if close_matrix.empty or not quotes:
            return close_matrix

        live_timestamp = pd.Timestamp(as_of)
        if live_timestamp.tz is not None:
            live_timestamp = live_timestamp.tz_localize(None)

        live_row = pd.Series({symbol: quotes.get(symbol, float("nan")) for symbol in close_matrix.columns}, name=live_timestamp)
        if close_matrix.index.size > 0 and pd.Timestamp(close_matrix.index[-1]).date() == live_timestamp.date():
            close_matrix.loc[close_matrix.index[-1], list(quotes.keys())] = [quotes[symbol] for symbol in quotes if symbol in close_matrix.columns]
            return close_matrix

        close_matrix = pd.concat([close_matrix, live_row.to_frame().T], axis=0)
        close_matrix = close_matrix[~close_matrix.index.duplicated(keep="last")].sort_index()
        return close_matrix

    def build_rebalance_plan(self, as_of: datetime | None = None) -> RebalancePlan:
        as_of = as_of or datetime.now(ET)
        universe = self.get_current_universe(as_of)
        if not universe:
            raise ValueError("No eligible universe available for current date")

        logger.info(f"Building rebalance plan for {as_of.date()} with {len(universe)} eligible symbols")

        price_data: dict[str, pd.DataFrame] = {}
        for symbol in universe:
            df = self._load_symbol_data(symbol)
            if not df.empty:
                price_data[symbol] = df

        if not price_data:
            raise ValueError("No price data available for eligible universe")

        benchmark_df = self._load_symbol_data(self.benchmark_symbol)
        if benchmark_df.empty:
            logger.info(f"Benchmark {self.benchmark_symbol} data unavailable for month-end check")

        all_symbols_for_quotes = sorted(set(price_data.keys()) | {position.symbol for position in self.broker.get_positions()})
        quotes = self.fetch_quotes(all_symbols_for_quotes)
        close_matrix = self._build_live_close_matrix(price_data, quotes, as_of)

        live_month_end = self._normalize_timestamp(as_of).to_period("M").to_timestamp("M")
        scores = self.strategy.compute_momentum_scores(close_matrix)
        if live_month_end not in self.universe_by_month:
            eligible_universe = set(universe)
        else:
            eligible_universe = set(self.universe_by_month[live_month_end])

        latest_index = close_matrix.index[-1]
        latest_scores = scores.loc[latest_index].dropna()
        latest_scores = latest_scores[latest_scores.index.isin(eligible_universe)]
        latest_scores = latest_scores.sort_values(ascending=False)
        selected_symbols = latest_scores.head(self.strategy.config.top_n).index.to_list()

        if not selected_symbols:
            raise ValueError("No selected symbols after momentum ranking")

        positions = {pos.symbol: pos for pos in self.broker.get_positions()}
        current_shares = {symbol: int(pos.quantity) for symbol, pos in positions.items() if pos.quantity > 0}

        if hasattr(self.broker, "update_prices"):
            self.broker.update_prices(quotes)

        cash = float(self.broker.get_account_balance().get("cash", 0.0))
        position_value = 0.0
        for symbol, quantity in current_shares.items():
            price = quotes.get(symbol)
            if price is not None and price > 0:
                position_value += quantity * price
        total_equity = cash + position_value

        target_value_per_symbol = total_equity / len(selected_symbols) if selected_symbols else 0.0
        target_shares = {}
        for symbol in selected_symbols:
            price = quotes.get(symbol, 0.0)
            if price > 0:
                target_shares[symbol] = int(target_value_per_symbol // price)
            else:
                target_shares[symbol] = 0

        orders: list[RebalanceOrder] = []
        symbols_union = sorted(set(current_shares.keys()) | set(target_shares.keys()))
        for symbol in symbols_union:
            quote = float(quotes.get(symbol, 0.0))
            if quote <= 0:
                continue
            current_qty = int(current_shares.get(symbol, 0))
            target_qty = int(target_shares.get(symbol, 0))
            delta = target_qty - current_qty
            if delta == 0:
                continue
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            orders.append(
                RebalanceOrder(
                    symbol=symbol,
                    side=side,
                    quantity=abs(delta),
                    reference_price=quote,
                    target_shares=target_qty,
                    current_shares=current_qty,
                )
            )

        sell_orders = [order for order in orders if order.side == OrderSide.SELL]
        buy_orders = [order for order in orders if order.side == OrderSide.BUY]
        ordered_orders = sell_orders + buy_orders

        return RebalancePlan(
            as_of=as_of,
            selected_symbols=selected_symbols,
            quotes=quotes,
            target_shares=target_shares,
            current_shares=current_shares,
            total_equity=total_equity,
            cash=cash,
            orders=ordered_orders,
        )

    def execute_rebalance(self, plan: RebalancePlan, live: bool = False) -> list[str]:
        order_ids = []
        broker_name = self.broker.__class__.__name__
        logger.info(
            f"Executing rebalance via {broker_name} | selected={plan.selected_symbols} | "
            f"equity=${plan.total_equity:,.2f} | orders={len(plan.orders)}"
        )

        for order in plan.orders:
            if order.quantity <= 0:
                continue

            order_type = OrderType.MARKET if live and not isinstance(self.broker, PaperBroker) else OrderType.LIMIT
            limit_price = None
            if order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY:
                    limit_price = order.reference_price * (1 + self.price_buffer_pct)
                else:
                    limit_price = order.reference_price * (1 - self.price_buffer_pct)

            order_id = self.broker.place_order(
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                order_type=order_type,
                limit_price=limit_price,
                reason=(
                    f"11-1 monthly rebalance | target={order.target_shares} | "
                    f"current={order.current_shares} | ref={order.reference_price:.2f}"
                ),
            )
            logger.info(
                f"ORDER: {order.side.value} {order.quantity} {order.symbol} | "
                f"current={order.current_shares} -> target={order.target_shares} | ref=${order.reference_price:.2f}"
            )
            order_ids.append(order_id)

        return order_ids
