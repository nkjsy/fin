"""
Live Trading Engine

Synchronous polling engine that processes candles (1-min or 5-min) and checks
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
    
    Polls candles at configurable intervals (1-min or 5-min). When any strategy
    is in PULLBACK or IN_POSITION state, switches to fast polling mode using
    real-time quotes.
    
    Supports dynamic symbol addition via add_symbol() for scanner integration.
    """
    
    # Polling intervals (seconds)
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
        symbols: Optional[List[str]] = None,
        candle_interval: int = 5,
        extended_hours: bool = False,
        position_amount: Optional[float] = None,
        max_symbols: int = 10,
        remove_after_exit: bool = False
    ):
        """
        Initialize LiveTradingEngine.
        
        Args:
            client_wrapper: AutoRefreshSchwabClient that manages token refresh
            broker: IBroker implementation (PaperBroker or SchwabBroker)
            symbols: List of symbols to trade (optional, can add dynamically)
            candle_interval: Candle interval in minutes (1 or 5)
            extended_hours: Enable premarket/afterhours data
            position_amount: Fixed position size in dollars (if None, uses 25% of buying power)
            max_symbols: Maximum number of symbols to track
            remove_after_exit: If True, remove symbol after any exit (pattern fail or position close)
        """
        self.client_wrapper = client_wrapper
        self.broker = broker
        self.symbols = symbols or []
        self.candle_interval = candle_interval
        self.extended_hours = extended_hours
        self.position_amount = position_amount
        self.max_symbols = max_symbols
        self.remove_after_exit = remove_after_exit
        
        self.strategies: Dict[str, BullFlagLiveStrategy] = {}
        self.running = False
        self._last_processed_slot: Dict[str, int] = {}  # slot = minutes since market open / interval
        
        # Candle data storage for each symbol (for pattern failure detection)
        self.candle_data: Dict[str, List[Candle]] = {}
        
        # Initialize strategy for each symbol
        for symbol in self.symbols:
            self.strategies[symbol] = BullFlagLiveStrategy(
                symbol=symbol,
                on_signal=self._handle_signal
            )
            self.candle_data[symbol] = []
        
        logger.info(
            f"Engine initialized: {len(self.symbols)} symbols, "
            f"{candle_interval}min candles, extended_hours={extended_hours}"
        )
    
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
                # Calculate position size
                if self.position_amount is not None:
                    # Use fixed position amount
                    quantity = int(self.position_amount / signal.price)
                else:
                    # Use 25% of buying power per trade
                    buying_power = self.broker.get_buying_power()
                    max_position_value = buying_power * 0.25
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
        """Convert datetime to slot number (minutes since market open / interval)."""
        if self.extended_hours:
            # For extended hours, use 4:00 AM as start
            market_open = dt.replace(hour=4, minute=0, second=0, microsecond=0)
        else:
            market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = int((dt - market_open).total_seconds() / 60)
        return minutes_since_open // self.candle_interval
    
    def _slot_to_time_str(self, slot: int) -> str:
        """Convert slot number to time string."""
        if self.extended_hours:
            minutes = 4 * 60 + slot * self.candle_interval
        else:
            minutes = 9 * 60 + 30 + slot * self.candle_interval
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
        Fetch candles for a symbol at the configured interval.
        
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
                # Start from market/premarket open today
                if self.extended_hours:
                    market_open = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
                else:
                    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                
                # Select API method based on interval
                if self.candle_interval == 1:
                    resp = self.client.get_price_history_every_minute(
                        symbol,
                        start_datetime=market_open,
                        end_datetime=None,
                        need_extended_hours_data=self.extended_hours,
                        need_previous_close=False
                    )
                else:
                    resp = self.client.get_price_history_every_five_minutes(
                        symbol,
                        start_datetime=market_open,
                        end_datetime=None,
                        need_extended_hours_data=self.extended_hours,
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
        """Fetch and process candles for all tracked symbols."""
        for symbol in list(self.strategies.keys()):
            candles = self._fetch_candles(symbol)
            for candle in candles:
                candle_time_str = candle.timestamp.strftime("%H:%M")
                logger.info(
                    f"[{candle_time_str}] {symbol}: O={candle.open:.2f} H={candle.high:.2f} "
                    f"L={candle.low:.2f} C={candle.close:.2f} V={candle.volume}"
                )
                strategy = self.strategies[symbol]
                strategy.process_candle(candle)
                
                # Store candle data for this symbol
                if symbol in self.candle_data:
                    self.candle_data[symbol].append(candle)
        
        # Check for exits after processing all candles
        if self.remove_after_exit:
            self._check_pattern_failed()
            self._check_position_exited()
    
    def _check_pattern_failed(self) -> None:
        """
        Remove symbols where pattern failed (PULLBACK -> SCANNING).
        
        When a strategy was in PULLBACK but transitions back to SCANNING,
        it means the pattern failed. We stop tracking to catch only first pullback.
        """
        
        to_remove = []
        for symbol, strategy in self.strategies.items():
            # Check if pattern failed: was in PULLBACK, now back to SCANNING
            if (strategy.state == StrategyState.SCANNING and 
                hasattr(strategy, 'prev_state') and 
                strategy.prev_state == StrategyState.PULLBACK):
                to_remove.append(symbol)
        
        for symbol in to_remove:
            logger.info(f"Pattern failed for {symbol}, removing from tracking")
            self._remove_symbol(symbol)
    
    def _check_position_exited(self) -> None:
        """
        Remove symbols where position was exited (IN_POSITION -> SCANNING).
        
        After a sell (stop loss or take profit), remove the symbol from tracking
        to avoid re-entering the same stock in the same session.
        """
        to_remove = []
        for symbol, strategy in self.strategies.items():
            # Check if position exited: was IN_POSITION, now back to SCANNING
            if (strategy.state == StrategyState.SCANNING and 
                hasattr(strategy, 'prev_state') and 
                strategy.prev_state == StrategyState.IN_POSITION):
                to_remove.append(symbol)
        
        for symbol in to_remove:
            logger.info(f"Position exited for {symbol}, removing from tracking")
            self._remove_symbol(symbol)
    
    def _remove_symbol(self, symbol: str) -> None:
        """
        Remove a symbol from all tracking data structures.
        
        Args:
            symbol: Symbol to remove
        """
        if symbol in self.strategies:
            del self.strategies[symbol]
        if symbol in self.candle_data:
            del self.candle_data[symbol]
        if symbol in self._last_processed_slot:
            del self._last_processed_slot[symbol]
        if symbol in self.symbols:
            self.symbols.remove(symbol)
    
    def add_symbol(self, symbol: str, replay_minutes: int = 10) -> bool:
        """
        Add a symbol to track. Replays last N minutes of candles to catch up.
        
        Args:
            symbol: Stock symbol to add
            replay_minutes: Minutes of historical candles to replay
            
        Returns:
            True if symbol was added, False if already tracking or at capacity
        """
        if symbol in self.strategies:
            logger.info(f"Already tracking {symbol}")
            return False
        
        if len(self.strategies) >= self.max_symbols:
            logger.info(f"At max capacity ({self.max_symbols}), cannot add {symbol}")
            return False
        
        logger.info(f"Adding {symbol} with {replay_minutes}min replay...")
        
        # Fetch historical candles for replay
        candles = self._fetch_history_for_replay(symbol, replay_minutes)
        
        # Create strategy and replay candles
        strategy = BullFlagLiveStrategy(
            symbol=symbol,
            on_signal=self._handle_signal
        )
        
        for candle in candles:
            candle_time_str = candle.timestamp.strftime("%H:%M")
            logger.info(
                f"[REPLAY {candle_time_str}] {symbol}: O={candle.open:.2f} H={candle.high:.2f} "
                f"L={candle.low:.2f} C={candle.close:.2f} V={candle.volume}"
            )
            strategy.process_candle(candle)
        
        # Add to tracking
        self.strategies[symbol] = strategy
        self.candle_data[symbol] = list(candles)
        if symbol not in self.symbols:
            self.symbols.append(symbol)
        
        # Set last processed slot to current to avoid re-fetching replayed candles
        now_et = datetime.now(self.ET)
        self._last_processed_slot[symbol] = self._datetime_to_slot(now_et) - 1
        
        logger.info(f"Added {symbol}, state={strategy.state.value}, replayed {len(candles)} candles")
        return True
    
    def _fetch_history_for_replay(self, symbol: str, minutes: int) -> List[Candle]:
        """
        Fetch historical candles for replay when adding a new symbol.
        
        Args:
            symbol: Stock symbol
            minutes: How many minutes of history to fetch
            
        Returns:
            List of Candles (oldest first)
        """
        now_et = datetime.now(self.ET)
        
        # Calculate start time (minutes ago)
        from datetime import timedelta
        start_time = now_et - timedelta(minutes=minutes + self.candle_interval)
        
        try:
            if self.candle_interval == 1:
                resp = self.client.get_price_history_every_minute(
                    symbol,
                    start_datetime=start_time,
                    end_datetime=None,
                    need_extended_hours_data=self.extended_hours,
                    need_previous_close=False
                )
            else:
                resp = self.client.get_price_history_every_five_minutes(
                    symbol,
                    start_datetime=start_time,
                    end_datetime=None,
                    need_extended_hours_data=self.extended_hours,
                    need_previous_close=False
                )
            
            if resp.status_code != httpx.codes.OK:
                logger.info(f"Failed to fetch history for {symbol}: {resp.status_code}")
                return []
            
            data = resp.json()
            candles = data.get("candles", [])
            
            result = []
            current_slot = self._datetime_to_slot(now_et)
            
            for c in candles:
                candle_time = datetime.fromtimestamp(c["datetime"] / 1000, tz=self.ET)
                candle_slot = self._datetime_to_slot(candle_time)
                
                # Only include complete candles (not the current incomplete one)
                if candle_slot < current_slot:
                    result.append(Candle(
                        timestamp=candle_time,
                        open=float(c["open"]),
                        high=float(c["high"]),
                        low=float(c["low"]),
                        close=float(c["close"]),
                        volume=int(c["volume"])
                    ))
            
            result.sort(key=lambda c: c.timestamp)
            return result
            
        except Exception as e:
            logger.info(f"Error fetching history for {symbol}: {e}")
            return []
    
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
        
        # Remove symbols where position was exited via real-time stop loss
        if self.remove_after_exit:
            self._check_position_exited()
    
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
                
                # Poll candles at each candle boundary
                current_slot = self._datetime_to_slot(now_et)
                if current_slot != last_poll_slot:
                    self._process_candles()
                    last_poll_slot = current_slot
                
                # Fast polling for real-time checks when needed
                if self._needs_realtime_polling():
                    self._check_realtime_triggers()
                    time.sleep(self.REALTIME_POLL_INTERVAL)
                else:
                    # Sleep until next candle boundary
                    candle_poll_interval = self.candle_interval * 60
                    seconds_into_slot = (now_et.minute % self.candle_interval) * 60 + now_et.second
                    sleep_time = candle_poll_interval - seconds_into_slot + 5  # +5s buffer for candle to be ready
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
