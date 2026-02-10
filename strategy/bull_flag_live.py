"""
Bull Flag Live Strategy

Synchronous version that processes candles with real-time 
price checks during pullback and in-position states.
"""

from dataclasses import dataclass
from typing import Callable, Optional, List
import pandas as pd
import pandas_ta as ta

from strategy.base import ILiveStrategy, StrategyState, Candle, Signal


@dataclass 
class GreenSequence:
    """Tracks the green bar sequence for pattern detection."""
    count: int = 0
    start_price: float = 0.0
    high: float = 0.0
    low: float = float('inf')
    volume_sum: float = 0.0
    
    def reset(self):
        self.count = 0
        self.start_price = 0.0
        self.high = 0.0
        self.low = float('inf')
        self.volume_sum = 0.0
    
    def add_candle(self, candle: Candle):
        if self.count == 0:
            self.start_price = candle.open
            self.low = candle.low
        
        self.count += 1
        self.high = max(self.high, candle.high)
        self.low = min(self.low, candle.low)
        self.volume_sum += candle.volume
    
    @property
    def avg_volume(self) -> float:
        return self.volume_sum / self.count if self.count > 0 else 0
    
    # for logging purposes
    def __str__(self):
        return (f"GreenSequence(count={self.count}, start_price={self.start_price}, "
                f"high={self.high}, low={self.low}, avg_volume={self.avg_volume})")


class BullFlagLiveStrategy(ILiveStrategy):
    """
    Bull flag strategy for live trading.
    
    Processes candles synchronously. When in PULLBACK or IN_POSITION
    state, use check_breakout() or check_stop_loss() with real-time prices.
    """
    
    def __init__(
        self,
        symbol: str,
        min_green_bars: int = 2,
        price_increase_pct: float = 3.0,
        ema_period: int = 9,
        pullback_retracement: float = 0.5,
        on_signal: Optional[Callable[[Signal], None]] = None
    ):
        """
        Initialize BullFlagLiveStrategy.
        
        Args:
            symbol: Ticker symbol this strategy instance is tracking
            min_green_bars: Minimum consecutive green bars required
            price_increase_pct: Minimum % increase during green run
            ema_period: EMA period for support
            pullback_retracement: Max retracement allowed (0.5 = 50%)
            on_signal: Callback function when signal is generated
        """
        self.symbol = symbol
        self.min_green_bars = min_green_bars
        self.price_increase_pct = price_increase_pct / 100.0
        self.ema_period = ema_period
        self.pullback_retracement = pullback_retracement
        self.on_signal = on_signal
        
        # State tracking
        self.state = StrategyState.SCANNING
        self.prev_state = StrategyState.SCANNING  # Track previous state for pattern failure detection
        self.green_seq = GreenSequence()
        self.candle_history: List[Candle] = []
        
        # Pullback state
        self.pb_limit_price = 0.0
        self.pb_avg_green_vol = 0.0
        self.pb_min_low = float('inf')
        
        # Breakout trigger price (set when entering PULLBACK)
        self.breakout_price = 0.0
        
        # Position state
        self.entry_price = 0.0
        self.stop_loss = 0.0
        
        # Flag to skip the current candle's IN_POSITION handling
        # Set when we enter IN_POSITION via real-time check
        self._skip_current_candle = False
        
        # Previous candle
        self.prev_candle: Optional[Candle] = None
    
    def _calculate_ema(self) -> Optional[float]:
        """Calculate current EMA from candle history."""
        if len(self.candle_history) < self.ema_period:
            return None
        
        closes = [c.close for c in self.candle_history[-self.ema_period * 2:]]
        if len(closes) < self.ema_period:
            return None
        
        ema = ta.ema(pd.Series(closes), length=self.ema_period)
        return ema.iloc[-1] if ema is not None and len(ema) > 0 else None
    
    def check_breakout(self, current_price: float) -> Optional[Signal]:
        """
        Check if current price triggers a breakout entry.
        
        Call this with real-time price data when state is PULLBACK.
        
        Args:
            current_price: Current real-time price
            
        Returns:
            BUY signal if breakout triggered, None otherwise
        """
        if self.state != StrategyState.PULLBACK:
            return None
        
        if current_price >= self.breakout_price:
            self.entry_price = self.breakout_price
            self.stop_loss = self.pb_min_low
            self._set_state(StrategyState.IN_POSITION)
            self.green_seq.reset()
            
            # Skip the current candle to avoid double state transition
            self._skip_current_candle = True
            
            return self._emit_signal(
                action="BUY",
                price=self.entry_price,
                stop_loss=self.stop_loss,
                reason=f"Bull flag breakout | Stop: ${self.stop_loss:.2f}"
            )
        
        return None
    
    def check_stop_loss(self, current_price: float) -> Optional[Signal]:
        """
        Check if current price triggers stop loss.
        
        Call this with real-time price when state is IN_POSITION.
        
        Args:
            current_price: Current real-time price
            
        Returns:
            SELL signal if stop hit, None otherwise
        """
        if self.state != StrategyState.IN_POSITION:
            return None
        
        if current_price <= self.stop_loss:
            self._set_state(StrategyState.SCANNING)
            self.green_seq.reset()
            
            return self._emit_signal(
                action="SELL",
                price=self.stop_loss,
                reason="Stop loss hit"
            )
        
        return None
    
    def process_candle(self, candle: Candle) -> Optional[Signal]:
        """
        Process a new candle.
        
        Args:
            candle: New OHLCV candle
            
        Returns:
            Signal if generated, None otherwise
        """
        self.candle_history.append(candle)
        
        # Keep history limited to avoid memory bloat
        if len(self.candle_history) > 100:
            self.candle_history = self.candle_history[-100:]
        
        signal = None
        
        # Skip first candle (need previous for comparison)
        if self.prev_candle is None:
            if candle.is_green:
                self.green_seq.add_candle(candle)
            self.prev_candle = candle
            return None
        
        ema = self._calculate_ema()
        
        if self.state == StrategyState.SCANNING:
            signal = self._handle_scanning(candle, ema)
        
        elif self.state == StrategyState.PULLBACK:
            signal = self._handle_pullback(candle, ema)
        
        elif self.state == StrategyState.IN_POSITION:
            # Skip if we just entered via real-time check
            if self._skip_current_candle:
                self._skip_current_candle = False
            else:
                signal = self._handle_in_position(candle)
        
        self.prev_candle = candle
        return signal
    
    def _handle_scanning(self, candle: Candle, ema: Optional[float]) -> Optional[Signal]:
        """Handle SCANNING state."""
        if candle.is_green:
            self.green_seq.add_candle(candle)
            self._log(f"Green #{self.green_seq.count}: ${candle.close:.2f}")
            return None
        
        elif candle.is_red:
            # Check if we have enough green bars
            if self.green_seq.count >= self.min_green_bars:
                # Check price increase
                increase = (self.prev_candle.close - self.green_seq.start_price) / self.green_seq.start_price
                
                if increase >= self.price_increase_pct:
                    # Setup confirmed! Check pullback conditions
                    self.pb_limit_price = self.green_seq.high - self.pullback_retracement * (
                        self.green_seq.high - self.green_seq.low
                    )
                    self.pb_avg_green_vol = self.green_seq.avg_volume
                    
                    # Check conditions
                    cond_retracement = candle.low >= self.pb_limit_price
                    cond_ema = candle.low >= ema if ema is not None else True
                    cond_vol = candle.volume <= self.pb_avg_green_vol

                    # logging green sequence conditions and ema for analysis
                    ema_str = f"{ema:.2f}" if ema is not None else "N/A"
                    self._log(f"Green seq conditions: {self.green_seq}, "
                              f"EMA: {ema_str} | ")
                    
                    if cond_retracement and cond_ema and cond_vol:
                        self._set_state(StrategyState.PULLBACK)
                        self.pb_min_low = candle.low
                        self.breakout_price = candle.high  # Set breakout trigger
                        self._log(f"PULLBACK started | Breakout above: ${self.breakout_price:.2f}")
                        return None
                    else:
                        self._log(f"Pullback conditions failed | Retrace:{cond_retracement} EMA:{cond_ema} Vol:{cond_vol}")
            
            # Reset on red bar if no valid setup
            self.green_seq.reset()
        
        else:
            # Doji - reset
            self.green_seq.reset()
        
        return None
    
    def _handle_pullback(self, candle: Candle, ema: Optional[float]) -> Optional[Signal]:
        """Handle PULLBACK state."""
        # Check for entry trigger on candle close
        if candle.high > self.breakout_price:
            self.entry_price = self.breakout_price
            self.stop_loss = self.pb_min_low
            self._set_state(StrategyState.IN_POSITION)
            self.green_seq.reset()
            
            return self._emit_signal(
                action="BUY",
                price=self.entry_price,
                stop_loss=self.stop_loss,
                reason=f"Bull flag breakout | Stop: ${self.stop_loss:.2f}"
            )
        
        # Check if pullback is still valid
        cond_retracement = candle.close >= self.pb_limit_price
        cond_ema = candle.low >= ema if ema is not None else True
        cond_vol = candle.volume <= self.pb_avg_green_vol
        
        if not (cond_retracement and cond_ema and cond_vol):
            self._log(f"Pullback invalidated | Ret:{cond_retracement} EMA:{cond_ema} Vol:{cond_vol}")
            self._set_state(StrategyState.SCANNING)
            self.green_seq.reset()
            
            if candle.is_green:
                self.green_seq.add_candle(candle)
        else:
            self.pb_min_low = min(self.pb_min_low, candle.low)
            self.breakout_price = candle.high  # Update breakout trigger
        
        return None
    
    def _handle_in_position(self, candle: Candle) -> Optional[Signal]:
        """Handle IN_POSITION state."""
        # Check stop loss
        if candle.low < self.stop_loss:
            self._set_state(StrategyState.SCANNING)
            self.green_seq.reset()
            
            return self._emit_signal(
                action="SELL",
                price=self.stop_loss,
                reason="Stop loss hit"
            )
        
        # Check take profit (first red bar)
        if candle.is_red:
            self._set_state(StrategyState.SCANNING)
            self.green_seq.reset()
            
            return self._emit_signal(
                action="SELL",
                price=candle.close,
                reason="Take profit - first red bar"
            )
        
        return None
    
    def reset(self):
        """Reset strategy state."""
        self.state = StrategyState.SCANNING
        self.prev_state = StrategyState.SCANNING
        self.green_seq.reset()
        self.candle_history.clear()
        self.prev_candle = None
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.breakout_price = 0.0
        self._skip_current_candle = False
        self._log("Strategy reset")
