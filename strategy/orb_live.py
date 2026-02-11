"""
Opening Range Breakout (ORB) Live Strategy

Builds the opening range from a fixed time window after market open (default
9:30–9:45), then enters on breakout above the range high with volume
confirmation.  Exit via scaled approach:
  Phase 1 — sell 50% at 1:1 R:R target
  Phase 2 — trailing stop at range_width below highest price for remaining 50%

Simplified state machine (no BUILDING_RANGE state):
  SCANNING  → range candles accumulated during fixed window; validated once
  PULLBACK  → watching for breakout above range high (volume required)
  IN_POSITION → managing exits (partial + trailing stop)
  (done)    → trade completed or disqualified, strategy requests removal

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

    Collects candles during a fixed time window (9:30–9:30+range_minutes),
    validates the range once when the first post-range candle arrives, then
    watches for a breakout above range high with volume confirmation.
    """

    def __init__(
        self,
        symbol: str,
        range_minutes: int = 15,
        max_range_pct: float = 0.10,
        volume_multiplier: float = 1.5,
        on_signal: Optional[Callable[[Signal], None]] = None,
    ):
        """
        Args:
            symbol:            Ticker symbol this instance tracks
            range_minutes:     Minutes after 9:30 to build the opening range
            max_range_pct:     Max allowed range width as fraction of price (0.08 = 8%)
            volume_multiplier: Breakout candle volume must be >= avg_range_vol * this
            on_signal:         Callback fired on BUY / SELL signals
        """
        self.symbol = symbol
        self.range_minutes = range_minutes
        self.max_range_pct = max_range_pct
        self.volume_multiplier = volume_multiplier
        self.on_signal = on_signal

        # State tracking
        self.state = StrategyState.SCANNING
        self.prev_state = StrategyState.SCANNING

        # Opening range — accumulated during fixed window
        self.range_high: float = 0.0
        self.range_low: float = float("inf")
        self.range_volumes: List[int] = []
        self.avg_range_volume: float = 0.0
        self.range_width: float = 0.0
        self.range_validated: bool = False

        # Fixed range end time (e.g. 9:45 for 15-min range)
        end_minutes = MARKET_OPEN.hour * 60 + MARKET_OPEN.minute + self.range_minutes
        self.range_end_time = dt_time(end_minutes // 60, end_minutes % 60)

        # Position state
        self.entry_price: float = 0.0
        self.stop_loss: float = 0.0
        self.target_1r: float = 0.0
        self.highest_high: float = 0.0
        self.trailing_stop: float = 0.0
        self.partial_exit_done: bool = False

        # Lifecycle flag
        self.remove_requested: bool = False   # Tells engine to drop this symbol

        # Skip flag (avoids double handling on mid-candle entry)
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
        """
        Real-time breakout check.

        Disabled for ORB — entry requires volume confirmation which is only
        available on completed candles.  Returns None always.
        """
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

        # Trade already completed — do nothing, engine will remove us
        if self.remove_requested:
            return None

        signal = None

        if self.state == StrategyState.SCANNING:
            signal = self._handle_scanning(candle, candle_time)

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
        self.range_volumes.clear()
        self.avg_range_volume = 0.0
        self.range_width = 0.0
        self.range_validated = False
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.target_1r = 0.0
        self.highest_high = 0.0
        self.trailing_stop = 0.0
        self.partial_exit_done = False
        self.remove_requested = False
        self._skip_current_candle = False
        self.candle_history.clear()
        self._log("Strategy reset")

    # ── state handlers ────────────────────────────────────────────────────────

    def _handle_scanning(self, candle: Candle, candle_time: dt_time) -> Optional[Signal]:
        """
        SCANNING state handles two phases based on time:
          1. During range window (< range_end_time): accumulate candles
          2. At/after range_end_time: validate range (once), then transition
        """
        # Phase 1: still within the range-building window
        if candle_time < self.range_end_time:
            self._accumulate_range(candle)
            return None

        # Phase 2: range window closed — validate once
        if not self.range_validated:
            return self._validate_range(candle)

        # Already validated — shouldn't reach here (removed or transitioned)
        return None

    def _accumulate_range(self, candle: Candle):
        """Add a candle to the opening range statistics."""
        self.range_high = max(self.range_high, candle.high)
        self.range_low = min(self.range_low, candle.low)
        self.range_volumes.append(candle.volume)
        self._log(
            f"Range candle #{len(self.range_volumes)}: "
            f"H=${self.range_high:.2f} L=${self.range_low:.2f} | "
            f"candle H=${candle.high:.2f} L=${candle.low:.2f} V={candle.volume}"
        )

    def _validate_range(self, candle: Candle) -> Optional[Signal]:
        """
        Validate the accumulated opening range. Called once on the first
        candle at or after range_end_time.

        If invalid (no data or too wide), sets remove_requested.
        If valid, transitions to PULLBACK and processes this candle.
        """
        self.range_validated = True

        # No range data (stock didn't trade during the window)
        if not self.range_volumes:
            self._log("No range data — requesting removal")
            self.remove_requested = True
            return None

        self.range_width = self.range_high - self.range_low
        mid_price = (self.range_high + self.range_low) / 2
        range_pct = self.range_width / mid_price if mid_price > 0 else 0

        # Range too wide — skip this stock
        if range_pct > self.max_range_pct:
            self._log(
                f"Range too wide: ${self.range_width:.2f} "
                f"({range_pct:.1%} > {self.max_range_pct:.0%}) — requesting removal"
            )
            self.remove_requested = True
            return None

        # Range valid — calculate average volume and transition
        self.avg_range_volume = sum(self.range_volumes) / len(self.range_volumes)
        self._log(
            f"Range valid: H=${self.range_high:.2f} L=${self.range_low:.2f} "
            f"Width=${self.range_width:.2f} ({range_pct:.1%}) "
            f"Candles={len(self.range_volumes)} | Avg vol={self.avg_range_volume:.0f}"
        )
        self._set_state(StrategyState.PULLBACK)

        # Process this first post-range candle for potential breakout
        return self._handle_pullback(candle)

    def _handle_pullback(self, candle: Candle) -> Optional[Signal]:
        """Watch for breakout above range high with volume confirmation."""
        if candle.high >= self.range_high:
            # Volume confirmation: breakout candle must show conviction
            vol_threshold = self.avg_range_volume * self.volume_multiplier
            if candle.volume < vol_threshold:
                self._log(
                    f"Breakout attempt rejected — low volume: "
                    f"{candle.volume:,} < {vol_threshold:,.0f} "
                    f"({self.volume_multiplier}x avg range vol)"
                )
                return None

            # Use candle open if it gaps above range high (realistic fill)
            entry_price = max(self.range_high, candle.open)
            return self._enter_position(entry_price)

        return None

    def _handle_in_position(self, candle: Candle) -> Optional[Signal]:
        """Manage position on candle close."""
        self.highest_high = max(self.highest_high, candle.high)
        return self._check_exit(candle.close, candle_low=candle.low)

    # ── trade logic ───────────────────────────────────────────────────────────

    def _enter_position(self, price: float) -> Signal:
        """Enter long at the given price."""
        self.entry_price = price
        self.stop_loss = self.range_low
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
            # Phase 1: before 1:1 target — check stop loss first
            if check_price <= self.stop_loss:
                return self._exit_position(
                    price=self.stop_loss,
                    reason="ORB stop loss hit",
                    quantity_pct=1.0,
                )

            # Check 1:1 R:R target — sell 50%
            if current_price >= self.target_1r:
                self.partial_exit_done = True
                self.highest_high = max(self.highest_high, current_price)
                self.trailing_stop = self.highest_high - self.range_width
                self._log(
                    f"1:1 target hit @ ${current_price:.2f} | "
                    f"Trailing stop activated @ ${self.trailing_stop:.2f}"
                )
                return self._emit_signal(
                    action="SELL",
                    price=current_price,
                    reason="ORB 1:1 R:R target hit — selling 50%",
                    quantity_pct=0.5,
                )
        else:
            # Phase 2: trailing stop for remaining 50%
            self.highest_high = max(self.highest_high, current_price)
            self.trailing_stop = self.highest_high - self.range_width

            if check_price <= self.trailing_stop:
                return self._exit_position(
                    price=self.trailing_stop,
                    reason=f"ORB trailing stop hit (high=${self.highest_high:.2f})",
                    quantity_pct=1.0,
                )

        return None

    def _exit_position(self, price: float, reason: str, quantity_pct: float) -> Signal:
        """
        Full exit — set state so engine removes us via _check_position_exited.
        """
        self._set_state(StrategyState.SCANNING)
        return self._emit_signal(
            action="SELL",
            price=price,
            reason=reason,
            quantity_pct=quantity_pct,
        )
