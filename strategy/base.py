from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from enum import Enum
from zoneinfo import ZoneInfo

import pandas as pd

from logger import get_logger


# Eastern timezone for market hours
ET = ZoneInfo("America/New_York")


# ── Shared types for live strategies ──────────────────────────────────────────


class StrategyState(Enum):
    """Strategy state machine states."""
    SCANNING = "SCANNING"
    BUILDING_RANGE = "BUILDING_RANGE"
    PULLBACK = "PULLBACK"
    IN_POSITION = "IN_POSITION"


@dataclass
class Candle:
    """Represents a single OHLCV candle."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    @property
    def is_red(self) -> bool:
        return self.close < self.open


@dataclass
class Signal:
    """Represents a trading signal."""
    timestamp: datetime
    symbol: str
    action: str  # "BUY" or "SELL"
    price: float
    stop_loss: Optional[float] = None
    reason: str = ""
    quantity_pct: float = 1.0  # 1.0 = full position, 0.5 = half


# ── Abstract base classes ─────────────────────────────────────────────────────


class BaseStrategy(ABC):
    """Base class for batch/backtest strategies."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Takes a DataFrame with OHLCV data, adds indicators, and generates 'Signal' column.
        Signal: 1 (Buy), -1 (Sell), 0 (Hold)
        
        Should also add:
        - Entry_Price: The price at which to execute a buy (0.0 if no buy signal)
        - Exit_Price: The price at which to execute a sell (0.0 if no sell signal)
        - Stop_Loss: Optional stop loss price for the trade
        
        Note: Entry/Exit prices should be realistic (e.g., next bar open) to avoid look-ahead bias.
        """
        pass


class ILiveStrategy(ABC):
    """
    Base class for live candle-by-candle strategies.

    Implementations must expose:
        state       - current StrategyState
        prev_state  - previous StrategyState (for failure/exit detection)
        symbol      - ticker symbol this instance is tracking
        on_signal   - optional callback fired on BUY/SELL signals
    """

    # Subclasses must set these attributes
    state: StrategyState
    prev_state: StrategyState
    symbol: str
    on_signal: Optional[Callable[[Signal], None]]

    @abstractmethod
    def process_candle(self, candle: Candle) -> Optional[Signal]:
        """Process a new OHLCV candle. Return Signal if generated."""
        pass

    @abstractmethod
    def check_breakout(self, current_price: float) -> Optional[Signal]:
        """Check if current real-time price triggers an entry."""
        pass

    @abstractmethod
    def check_stop_loss(self, current_price: float) -> Optional[Signal]:
        """Check if current real-time price triggers an exit."""
        pass

    @abstractmethod
    def reset(self):
        """Reset strategy to initial state."""
        pass

    # ── concrete helpers (shared by all live strategies) ──────────────────────

    def _log(self, message: str):
        """Log with symbol context. Override in subclass for custom prefix."""
        get_logger(self.symbol).info(message)

    def _set_state(self, new_state: StrategyState):
        """Set state and track previous state for failure/exit detection."""
        self.prev_state = self.state
        self.state = new_state
        self._log(f"State: {self.prev_state.value} -> {new_state.value}")

    def _emit_signal(
        self,
        action: str,
        price: float,
        stop_loss: Optional[float] = None,
        reason: str = "",
        quantity_pct: float = 1.0,
    ) -> Signal:
        """Create and emit a trading signal."""
        signal = Signal(
            timestamp=datetime.now(ET),
            symbol=self.symbol,
            action=action,
            price=price,
            stop_loss=stop_loss,
            reason=reason,
            quantity_pct=quantity_pct,
        )
        self._log(f"SIGNAL: {action} @ ${price:.2f} (qty={quantity_pct:.0%}) | {reason}")
        if self.on_signal:
            self.on_signal(signal)
        return signal
