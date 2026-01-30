"""
Live Trading Engine

Synchronous polling engine that processes 5-minute candles and checks
real-time prices when strategies are in PULLBACK or IN_POSITION state.
"""

import time
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from schwab.client import Client

from broker.interfaces import IBroker, OrderSide, OrderType
from strategy.bull_flag_live import BullFlagLiveStrategy, Candle, Signal, StrategyState
from client import AutoRefreshSchwabClient
from logger import get_logger
import httpx


logger = get_logger("ENGINE")


class LiveTradingEngine:
    """
    Synchronous live trading engine.
    
    Polls 5-minute candles every 5 minutes. When any strategy is in 
    PULLBACK or IN_POSITION state, switches to fast polling mode using
    real-time quotes.
    """
    
    # Polling intervals (seconds)
    CANDLE_POLL_INTERVAL = 300  # 5 minutes
    REALTIME_POLL_INTERVAL = 3  # 3 seconds for breakout/stop checks
    
    # Retry settings for API calls
    MAX_RETRIES = 3
    RETRY_DELAYS = [1, 2]  # seconds between retries
    
    # Eastern timezone for market hours
    ET = ZoneInfo("America/New_York")
    
    def __init__(
        self,
        client_wrapper: AutoRefreshSchwabClient,
        broker: IBroker,
        symbols: List[str]
    ):
        """
        Initialize LiveTradingEngine.
        
        Args:
            client_wrapper: AutoRefreshSchwabClient that manages token refresh
            broker: IBroker implementation (PaperBroker or SchwabBroker)
            symbols: List of symbols to trade
        """
        self.client_wrapper = client_wrapper
        self.broker = broker
        self.symbols = symbols
        
        self.strategies: Dict[str, BullFlagLiveStrategy] = {}
        self.running = False
        self._last_processed_slot: Dict[str, int] = {}  # slot = minutes since market open / 5
        
        # Initialize strategy for each symbol
        for symbol in symbols:
            self.strategies[symbol] = BullFlagLiveStrategy(
                symbol=symbol,
                on_signal=self._handle_signal
            )
        
        logger.info(f"Engine initialized for {len(symbols)} symbols")
    
    @property
    def client(self) -> Client:
        """Get the current Schwab client (auto-refreshes if needed)."""
        return self.client_wrapper.client
    
    def _handle_signal(self, signal: Signal):
        """
        Handle a signal from a strategy.
        
        Routes the signal to the broker for execution.
        """
        logger.info(f"Signal received: {signal.action} {signal.symbol} @ ${signal.price:.2f}")
        
        try:
            if signal.action == "BUY":
                # Calculate position size based on available buying power
                buying_power = self.broker.get_buying_power()
                max_position_value = buying_power * 0.25  # Use 25% of buying power per trade
                quantity = int(max_position_value / signal.price)
                
                if quantity < 1:
                    logger.info(f"Insufficient buying power for {signal.symbol}")
                    return
                
                order_id = self.broker.place_order(
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    quantity=quantity,
                    order_type=OrderType.LIMIT,
                    limit_price=signal.price,
                    reason=signal.reason
                )
                logger.info(f"BUY order placed: {order_id} for {quantity} shares")
                
            elif signal.action == "SELL":
                # Get current position
                position = self.broker.get_position(signal.symbol)
                
                if position is None or position.quantity <= 0:
                    logger.info(f"No position to sell for {signal.symbol}")
                    return
                
                order_id = self.broker.place_order(
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    quantity=position.quantity,
                    order_type=OrderType.LIMIT,
                    limit_price=signal.price,
                    reason=signal.reason
                )
                logger.info(f"SELL order placed: {order_id} for {position.quantity} shares")
                
        except Exception as e:
            logger.info(f"Error executing signal: {e}")
    
    def _needs_realtime_polling(self) -> bool:
        """Check if any strategy needs real-time price monitoring."""
        for strategy in self.strategies.values():
            if strategy.state in (StrategyState.PULLBACK, StrategyState.IN_POSITION):
                return True
        return False
    
    def _datetime_to_slot(self, dt: datetime) -> int:
        """Convert datetime to slot number (minutes since market open / 5)."""
        market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = int((dt - market_open).total_seconds() / 60)
        return minutes_since_open // 5
    
    def _slot_to_time_str(self, slot: int) -> str:
        """Convert slot number to time string (e.g., slot 0 -> '09:30')."""
        minutes = 9 * 60 + 30 + slot * 5
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    
    def _fetch_quotes(self, symbols: List[str]) -> Dict[str, float]:
        """
        Fetch real-time quotes for symbols.
        
        Returns:
            Dict mapping symbol to last price
        """
        
        prices = {}
        try:
            resp = self.client.get_quotes(symbols)
            
            if resp.status_code != httpx.codes.OK:
                logger.info(f"Failed to get quotes: {resp.status_code}")
                return prices
            
            data = resp.json()
            for symbol, quote in data.items():
                # Extract last price from quote response
                if "quote" in quote:
                    prices[symbol] = float(quote["quote"].get("lastPrice", 0))
                elif "lastPrice" in quote:
                    prices[symbol] = float(quote["lastPrice"])
                    
        except Exception as e:
            logger.info(f"Error fetching quotes: {e}")
        
        return prices
    
    def _fetch_candles(self, symbol: str) -> List[Candle]:
        """
        Fetch 5-minute candles for a symbol.
        
        Uses slot-based tracking to ensure complete candles are included and
        incomplete ones excluded. Retries on API failure or missing slots.
        
        Args:
            symbol: Stock symbol
        
        Returns:
            List of Candles (oldest first), may be empty
        """
        now_et = datetime.now(self.ET)
        current_slot = self._datetime_to_slot(now_et)
        
        # Determine expected slot range
        last_slot = self._last_processed_slot.get(symbol, -1)
        expected_slots = set(range(last_slot + 1, current_slot))
        
        if not expected_slots:
            return []
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # Start from market open today to limit data
                market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                
                resp = self.client.get_price_history_every_five_minutes(
                    symbol,
                    start_datetime=market_open,
                    end_datetime=None,
                    need_extended_hours_data=False,
                    need_previous_close=False
                )
                
                if resp.status_code != httpx.codes.OK:
                    logger.info(f"Failed to get candles for {symbol}: {resp.status_code}")
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(self.RETRY_DELAYS[attempt])
                        continue
                    return []
                
                data = resp.json()
                candles = data.get("candles", [])
                
                if not candles:
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(self.RETRY_DELAYS[attempt])
                        continue
                    return []
                
                # Filter candles by slot: include only complete candles we haven't processed
                result = []
                received_slots = set()
                
                for c in candles:
                    candle_time = datetime.fromtimestamp(c["datetime"] / 1000, tz=self.ET)
                    candle_slot = self._datetime_to_slot(candle_time)
                    
                    # Only include candles in expected range (complete and not yet processed)
                    if candle_slot in expected_slots:
                        received_slots.add(candle_slot)
                        result.append(Candle(
                            timestamp=candle_time,
                            open=float(c["open"]),
                            high=float(c["high"]),
                            low=float(c["low"]),
                            close=float(c["close"]),
                            volume=int(c["volume"])
                        ))
                
                # Check for missing slots
                missing_slots = expected_slots - received_slots
                
                if missing_slots and attempt < self.MAX_RETRIES - 1:
                    # Retry if slots are missing
                    missing_times = [self._slot_to_time_str(s) for s in sorted(missing_slots)]
                    logger.info(f"{symbol}: Retry {attempt + 1}/{self.MAX_RETRIES} - missing slots {missing_times}")
                    time.sleep(self.RETRY_DELAYS[attempt])
                    continue
                
                # Log warning if still missing after all retries
                if missing_slots:
                    missing_times = [self._slot_to_time_str(s) for s in sorted(missing_slots)]
                    logger.info(f"{symbol}: WARNING - missing candles for slots {missing_times}")
                
                # Sort by timestamp and update last processed slot
                result.sort(key=lambda c: c.timestamp)
                if received_slots:
                    self._last_processed_slot[symbol] = max(received_slots)
                
                return result
                
            except Exception as e:
                logger.info(f"Error fetching candles for {symbol}: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_DELAYS[attempt])
                    continue
                return []
        
        return []
    
    def _process_candles(self):
        """Fetch and process 5-minute candles for all symbols."""
        for symbol in self.symbols:
            candles = self._fetch_candles(symbol)
            for candle in candles:
                candle_time_str = candle.timestamp.strftime("%H:%M")
                logger.info(
                    f"[{candle_time_str}] {symbol}: O={candle.open:.2f} H={candle.high:.2f} "
                    f"L={candle.low:.2f} C={candle.close:.2f} V={candle.volume}"
                )
                strategy = self.strategies[symbol]
                strategy.process_candle(candle)
    
    def _check_realtime_triggers(self):
        """Check real-time prices for breakout/stop loss triggers."""
        # Collect symbols that need real-time checks
        symbols_to_check = []
        for symbol, strategy in self.strategies.items():
            if strategy.state in (StrategyState.PULLBACK, StrategyState.IN_POSITION):
                symbols_to_check.append(symbol)
        
        if not symbols_to_check:
            return
        
        # Fetch quotes
        prices = self._fetch_quotes(symbols_to_check)
        
        # Check each strategy
        for symbol in symbols_to_check:
            if symbol not in prices:
                continue
            
            price = prices[symbol]
            strategy = self.strategies[symbol]
            
            if strategy.state == StrategyState.PULLBACK:
                strategy.check_breakout(price)
            elif strategy.state == StrategyState.IN_POSITION:
                strategy.check_stop_loss(price)
    
    def start(self):
        """
        Start the live trading engine.
        
        Runs a polling loop until stopped or market close.
        """
        logger.info("Starting live trading engine...")
        self.running = True
        
        # Initialize to current slot - 1 to trigger immediate candle fetch
        now_et = datetime.now(self.ET)
        last_poll_slot = self._datetime_to_slot(now_et) - 1
        
        try:
            while self.running:
                now_et = datetime.now(self.ET)
                
                # Check if market is closed (4:00 PM ET)
                if now_et.time() >= dt_time(16, 0):
                    logger.info("Market closed - stopping engine")
                    break
                
                # Poll candles at each 5-minute boundary
                current_slot = self._datetime_to_slot(now_et)
                if current_slot != last_poll_slot:
                    self._process_candles()
                    last_poll_slot = current_slot
                
                # Fast polling for real-time checks when needed
                if self._needs_realtime_polling():
                    self._check_realtime_triggers()
                    time.sleep(self.REALTIME_POLL_INTERVAL)
                else:
                    # Sleep until next 5-minute boundary
                    seconds_into_slot = now_et.minute % 5 * 60 + now_et.second
                    sleep_time = self.CANDLE_POLL_INTERVAL - seconds_into_slot + 5  # +5s buffer for candle to be ready
                    time.sleep(max(1, sleep_time))
                    
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.info(f"Engine error: {e}")
            raise
        finally:
            self.stop()
    
    def stop(self):
        """Stop the live trading engine."""
        logger.info("Stopping engine...")
        self.running = False
        
        # Print broker summary
        if hasattr(self.broker, 'print_summary'):
            self.broker.print_summary()
        
        logger.info("Engine stopped")


# ============================================================================
# Premarket News Engine
# ============================================================================

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor


@dataclass
class PremarketPosition:
    """Tracks a premarket position."""
    symbol: str
    entry_price: float
    quantity: int
    stop_loss: float


class PremarketNewsEngine:
    """
    Premarket news gap trading engine.
    
    Manages positions only - scanning is handled externally by main_premarket.py.
    Reuses broker, client, and logging from LiveTradingEngine patterns.
    """
    
    POSITION_AMOUNT = 10000  # $10k per position
    STOP_LOSS_PCT = 0.05     # 5% stop loss
    LIMIT_BUFFER = 1.005     # Buy at ask + 0.5%
    
    ET = ZoneInfo("America/New_York")
    
    def __init__(self, client_wrapper: AutoRefreshSchwabClient, broker: IBroker):
        """
        Initialize PremarketNewsEngine.
        
        Args:
            client_wrapper: AutoRefreshSchwabClient for API calls
            broker: IBroker implementation (PaperBroker or SchwabBroker)
        """
        self.client_wrapper = client_wrapper
        self.broker = broker
        self.positions: Dict[str, PremarketPosition] = {}
        self._logger = get_logger("PREMARKET")
    
    @property
    def client(self) -> Client:
        """Get the current Schwab client."""
        return self.client_wrapper.client
    
    def add_positions(self, symbols: List[str]) -> None:
        """
        Buy symbols in parallel. Skip if already in position.
        
        Args:
            symbols: List of symbols to buy
        """
        new_symbols = [s for s in symbols if s not in self.positions]
        
        if not new_symbols:
            return
        
        self._logger.info(f"Adding positions: {new_symbols}")
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(self._place_buy_order, new_symbols)
    
    def _place_buy_order(self, symbol: str) -> None:
        """
        Place limit order at ask + 0.5% and track position.
        
        Args:
            symbol: Symbol to buy
        """
        try:
            # Fetch current quote
            quote = self._fetch_quote(symbol)
            
            if quote is None:
                self._logger.info(f"  {symbol}: Failed to get quote")
                return
            
            ask_price = quote.get("askPrice", 0)
            
            if ask_price <= 0:
                # Fallback to last price if ask not available
                ask_price = quote.get("lastPrice", 0)
            
            if ask_price <= 0:
                self._logger.info(f"  {symbol}: No valid price")
                return
            
            # Calculate limit price (ask + 0.5%)
            limit_price = round(ask_price * self.LIMIT_BUFFER, 2)
            quantity = int(self.POSITION_AMOUNT / limit_price)
            
            if quantity < 1:
                self._logger.info(f"  {symbol}: Quantity too small")
                return
            
            # Place order
            order_id = self.broker.place_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=quantity,
                order_type=OrderType.LIMIT,
                limit_price=limit_price,
                reason="News gap entry"
            )
            
            # Track position
            stop_loss = round(limit_price * (1 - self.STOP_LOSS_PCT), 2)
            self.positions[symbol] = PremarketPosition(
                symbol=symbol,
                entry_price=limit_price,
                quantity=quantity,
                stop_loss=stop_loss
            )
            
            self._logger.info(
                f"  {symbol}: BUY {quantity} @ ${limit_price:.2f} "
                f"(stop: ${stop_loss:.2f}) - {order_id}"
            )
            
        except Exception as e:
            self._logger.info(f"  {symbol}: Error placing order - {e}")
    
    def _fetch_quote(self, symbol: str) -> Optional[dict]:
        """
        Fetch quote for a single symbol.
        
        Args:
            symbol: Ticker symbol
            
        Returns:
            Quote dict or None
        """
        try:
            resp = self.client.get_quote(symbol)
            
            if resp.status_code != httpx.codes.OK:
                return None
            
            data = resp.json()
            return data.get(symbol, {}).get("quote", {})
            
        except Exception as e:
            self._logger.info(f"Error fetching quote for {symbol}: {e}")
            return None
    
    def _fetch_quotes(self, symbols: List[str]) -> Dict[str, float]:
        """
        Fetch quotes for multiple symbols.
        
        Args:
            symbols: List of symbols
            
        Returns:
            Dict mapping symbol to last price
        """
        prices = {}
        
        if not symbols:
            return prices
        
        try:
            resp = self.client.get_quotes(symbols)
            
            if resp.status_code != httpx.codes.OK:
                return prices
            
            data = resp.json()
            for symbol, quote_data in data.items():
                quote = quote_data.get("quote", {})
                price = quote.get("lastPrice", 0)
                if price > 0:
                    prices[symbol] = price
                    
        except Exception as e:
            self._logger.info(f"Error fetching quotes: {e}")
        
        return prices
    
    def check_stop_losses(self) -> None:
        """
        Check stop losses for all positions.
        Only call after 9:30 AM when market is open.
        """
        if not self.positions:
            return
        
        prices = self._fetch_quotes(list(self.positions.keys()))
        
        for symbol, price in prices.items():
            pos = self.positions.get(symbol)
            
            if pos and price <= pos.stop_loss:
                self._logger.info(
                    f"  {symbol}: Stop loss triggered @ ${price:.2f} "
                    f"(stop: ${pos.stop_loss:.2f})"
                )
                self._place_sell_order(symbol, "Stop loss triggered")
    
    def _place_sell_order(self, symbol: str, reason: str) -> None:
        """
        Place sell order for a position.
        
        Args:
            symbol: Symbol to sell
            reason: Reason for selling
        """
        pos = self.positions.get(symbol)
        
        if pos is None:
            return
        
        try:
            # Use market order for exits (faster execution)
            # Note: For premarket, we might need limit order
            quote = self._fetch_quote(symbol)
            bid_price = quote.get("bidPrice", 0) if quote else 0
            
            if bid_price <= 0:
                bid_price = quote.get("lastPrice", pos.entry_price) if quote else pos.entry_price
            
            order_id = self.broker.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=pos.quantity,
                order_type=OrderType.LIMIT,
                limit_price=round(bid_price * 0.995, 2),  # Bid - 0.5% for quick fill
                reason=reason
            )
            
            # Remove from positions
            del self.positions[symbol]
            
            self._logger.info(f"  {symbol}: SELL {pos.quantity} @ ${bid_price:.2f} - {order_id}")
            
        except Exception as e:
            self._logger.info(f"  {symbol}: Error placing sell order - {e}")
    
    def exit_all(self) -> None:
        """
        Exit all positions. Call at 9:35 AM.
        """
        if not self.positions:
            self._logger.info("No positions to exit")
            return
        
        self._logger.info(f"Exiting all positions: {list(self.positions.keys())}")
        
        symbols = list(self.positions.keys())
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(
                lambda s: self._place_sell_order(s, "Time exit 9:35 AM"),
                symbols
            )
    
    def print_summary(self) -> None:
        """Print position summary."""
        if hasattr(self.broker, 'print_summary'):
            self.broker.print_summary()

