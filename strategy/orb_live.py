"""
Opening Range Breakout (ORB) Live Strategy

Builds an opening range from the first N minutes after market open (9:30 AM),
then enters on breakout above the range high. Exit via scaled approach:
  Phase 1 — sell 50% at 1:1 R:R target
  Phase 2 — trailing stop at range_width below highest price for remaining 50%

Uses the same ILiveStrategy interface as BullFlagLiveStrategy so it plugs
directly into LiveTradingEngine.
"""

from datetime import datetime, time as dt_time
from typing import Callable, Optional, List
from zoneinfo import ZoneInfo

from logger import get_logger
from strategy.base import ILiveStrategy, StrategyState, Candle, Signal, ET

# Market open time
MARKET_OPEN = dt_time(9, 30)


class ORBLiveStrategy(ILiveStrategy):
    """
    Opening Range Breakout strategy for live trading.

    Collects candles during the opening range window, then watches for a
    breakout above the range high.  All core logic runs through
    process_candle() and the real-time check_breakout()/check_stop_loss()
    hooks used by the engine's fast-polling mode.
    """

    def __init__(
        self,
        symbol: str,
        range_minutes: int = 5,
        max_range_pct: float = 0.04,
        on_signal: Optional[Callable[[Signal], None]] = None,
    ):
        """
        Args:
            symbol:         Ticker symbol this instance tracks
            range_minutes:  Minutes after 9:30 to build the opening range (5 or 15)
            max_range_pct:  Max allowed range width as fraction of price (0.04 = 4%)
            on_signal:      Callback fired on BUY / SELL signals
        """
        self.symbol = symbol
        self.range_minutes = range_minutes
        self.max_range_pct = max_range_pct
        self.on_signal = on_signal

        # State tracking
        self.state = StrategyState.SCANNING
        self.prev_state = StrategyState.SCANNING

        # Opening range
        self.range_high: float = 0.0
        self.range_low: float = float("inf")
        self.range_candle_count: int = 0
        self.range_end_time: Optional[dt_time] = None  # set when first range candle arrives

        # Position state
        self.entry_price: float = 0.0
        self.stop_loss: float = 0.0
        self.target_1r: float = 0.0
        self.range_width: float = 0.0
        self.highest_high: float = 0.0
        self.trailing_stop: float = 0.0
        self.partial_exit_done: bool = False

        # Skip flag (same pattern as bull flag — avoids double handling
        # when entering IN_POSITION via real-time check mid-candle)
        self._skip_current_candle: bool = False

        # Candle history (for context / debugging)
        self.candle_history: List[Candle] = []

        # Per-symbol logger
        self._logger = get_logger(self.symbol)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _log(self, message: str):
        """Override base _log to add [ORB] prefix."""
        self._logger.info(f"[ORB] {message}")

    # ── ILiveStrategy interface ───────────────────────────────────────────────

    def check_breakout(self, current_price: float) -> Optional[Signal]:
        """Real-time breakout check (called every ~3 s by engine)."""
        if self.state != StrategyState.PULLBACK:
            return None

        if current_price >= self.range_high:
            return self._enter_position(current_price)
        return None

    def check_stop_loss(self, current_price: float) -> Optional[Signal]:
        """Real-time stop / trailing-stop / target check."""
        if self.state != StrategyState.IN_POSITION:
            return None

        return self._check_exit(current_price)

    def process_candle(self, candle: Candle) -> Optional[Signal]:
        self.candle_history.append(candle)
        if len(self.candle_history) > 200:
            self.candle_history = self.candle_history[-200:]

        # Before market open — store for context, don't act
        candle_time = candle.timestamp.astimezone(ET).time()
        if candle_time < MARKET_OPEN:
            return None

        signal = None

        if self.state == StrategyState.SCANNING:
            # First candle at/after 9:30 — start building range
            self._set_state(StrategyState.BUILDING_RANGE)
            signal = self._handle_building_range(candle)

        elif self.state == StrategyState.BUILDING_RANGE:
            signal = self._handle_building_range(candle)

        elif self.state == StrategyState.PULLBACK:
            signal = self._handle_pullback(candle)

        elif self.state == StrategyState.IN_POSITION:
            if self._skip_current_candle:
                self._skip_current_candle = False
            else:
                signal = self._handle_in_position(candle)

        return signal

    def reset(self):
        self.state = StrategyState.SCANNING
        self.prev_state = StrategyState.SCANNING
        self.range_high = 0.0
        self.range_low = float("inf")
        self.range_candle_count = 0
        self.range_end_time = None
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.target_1r = 0.0
        self.range_width = 0.0
        self.highest_high = 0.0
        self.trailing_stop = 0.0
        self.partial_exit_done = False
        self._skip_current_candle = False
        self.candle_history.clear()
        self._log("Strategy reset")

    # ── state handlers ────────────────────────────────────────────────────────

    def _handle_building_range(self, candle: Candle) -> Optional[Signal]:
        """Accumulate candles into the opening range."""
        self.range_high = max(self.range_high, candle.high)
        self.range_low = min(self.range_low, candle.low)
        self.range_candle_count += 1

        # Calculate end time on first candle
        if self.range_end_time is None:
            open_minutes = MARKET_OPEN.hour * 60 + MARKET_OPEN.minute
            end_minutes = open_minutes + self.range_minutes
            self.range_end_time = dt_time(end_minutes // 60, end_minutes % 60)

        candle_time = candle.timestamp.astimezone(ET).time()
        self._log(
            f"Building range ({self.range_candle_count} candles): "
            f"H=${self.range_high:.2f} L=${self.range_low:.2f} | candle {candle_time}"
        )

        # Check if range window is complete
        if candle_time >= self.range_end_time:
            self.range_width = self.range_high - self.range_low

            # Range filter — skip if too wide
            mid_price = (self.range_high + self.range_low) / 2
            if mid_price > 0 and (self.range_width / mid_price) > self.max_range_pct:
                self._log(
                    f"Range too wide: ${self.range_width:.2f} "
                    f"({self.range_width / mid_price:.1%} > {self.max_range_pct:.0%}), skipping"
                )
                self._set_state(StrategyState.SCANNING)
                return None

            self._log(
                f"Range complete: H=${self.range_high:.2f} L=${self.range_low:.2f} "
                f"Width=${self.range_width:.2f} ({self.range_candle_count} candles)"
            )
            self._set_state(StrategyState.PULLBACK)

        return None

    def _handle_pullback(self, candle: Candle) -> Optional[Signal]:
        """Watch for breakout above range high on candle data."""
        if candle.high >= self.range_high:
            # Use candle open if it gaps above range high (realistic fill price)
            entry_price = max(self.range_high, candle.open)
            return self._enter_position(entry_price)
        return None

    def _handle_in_position(self, candle: Candle) -> Optional[Signal]:
        """Manage position on candle close."""
        # Update highest high for trailing stop
        self.highest_high = max(self.highest_high, candle.high)

        return self._check_exit(candle.close, candle_low=candle.low)

    # ── trade logic ───────────────────────────────────────────────────────────

    def _enter_position(self, price: float) -> Signal:
        """Enter long at the given price."""
        self.entry_price = price
        self.stop_loss = self.range_low
        self.range_width = self.range_high - self.range_low
        self.target_1r = self.entry_price + self.range_width  # 1:1 R:R
        self.highest_high = price
        self.trailing_stop = 0.0
        self.partial_exit_done = False

        self._set_state(StrategyState.IN_POSITION)
        self._skip_current_candle = True

        return self._emit_signal(
            action="BUY",
            price=self.entry_price,
            stop_loss=self.stop_loss,
            reason=(
                f"ORB breakout above ${self.range_high:.2f} | "
                f"Stop: ${self.stop_loss:.2f} | Target 1R: ${self.target_1r:.2f}"
            ),
        )

    def _check_exit(
        self, current_price: float, candle_low: Optional[float] = None
    ) -> Optional[Signal]:
        """
        Shared exit logic used by both process_candle and check_stop_loss.

        Args:
            current_price: price to evaluate (candle.close or real-time quote)
            candle_low:    if from a candle, the candle low for stop-loss check
        """
        check_price = candle_low if candle_low is not None else current_price

        if not self.partial_exit_done:
            # Phase 1: before 1:1 target
            # Check stop loss first
            if check_price <= self.stop_loss:
                self._set_state(StrategyState.SCANNING)
                return self._emit_signal(
                    action="SELL",
                    price=self.stop_loss,
                    reason="ORB stop loss hit",
                    quantity_pct=1.0,
                )

            # Check 1:1 R:R target — sell 50%
            if current_price >= self.target_1r:
                self.partial_exit_done = True
                # Activate trailing stop
                self.highest_high = max(self.highest_high, current_price)
                self.trailing_stop = self.highest_high - self.range_width
                self._log(
                    f"1:1 target hit @ ${current_price:.2f} | "
                    f"Trailing stop activated @ ${self.trailing_stop:.2f}"
                )
                return self._emit_signal(
                    action="SELL",
                    price=current_price,
                    reason=f"ORB 1:1 R:R target hit — selling 50%",
                    quantity_pct=0.5,
                )
        else:
            # Phase 2: trailing stop for remaining 50%
            self.highest_high = max(self.highest_high, current_price)
            self.trailing_stop = self.highest_high - self.range_width

            if check_price <= self.trailing_stop:
                self._set_state(StrategyState.SCANNING)
                return self._emit_signal(
                    action="SELL",
                    price=self.trailing_stop,
                    reason=f"ORB trailing stop hit (high=${self.highest_high:.2f})",
                    quantity_pct=1.0,
                )

        return None
