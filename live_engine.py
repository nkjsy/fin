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
import httpx


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
    
    # Eastern timezone for market hours
    ET = ZoneInfo("America/New_York")
    
    def __init__(
        self,
        client: Client,
        broker: IBroker,
        symbols: List[str]
    ):
        """
        Initialize LiveTradingEngine.
        
        Args:
            client: Authenticated Schwab Client
            broker: IBroker implementation (PaperBroker or SchwabBroker)
            symbols: List of symbols to trade
        """
        self.client = client
        self.broker = broker
        self.symbols = symbols
        
        self.strategies: Dict[str, BullFlagLiveStrategy] = {}
        self.running = False
        self._last_candle_time: Dict[str, datetime] = {}
        
        # Initialize strategy for each symbol
        for symbol in symbols:
            self.strategies[symbol] = BullFlagLiveStrategy(
                symbol=symbol,
                on_signal=self._handle_signal
            )
        
        self._log(f"Engine initialized for {len(symbols)} symbols")
    
    def _log(self, message: str):
        """Log with timestamp (Eastern time)."""
        timestamp = datetime.now(self.ET).strftime("%H:%M:%S")
        print(f"[{timestamp}] [ENGINE] {message}")
    
    def _handle_signal(self, signal: Signal):
        """
        Handle a signal from a strategy.
        
        Routes the signal to the broker for execution.
        """
        self._log(f"Signal received: {signal.action} {signal.symbol} @ ${signal.price:.2f}")
        
        try:
            if signal.action == "BUY":
                # Calculate position size based on available buying power
                buying_power = self.broker.get_buying_power()
                max_position_value = buying_power * 0.25  # Use 25% of buying power per trade
                quantity = int(max_position_value / signal.price)
                
                if quantity < 1:
                    self._log(f"Insufficient buying power for {signal.symbol}")
                    return
                
                order_id = self.broker.place_order(
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    quantity=quantity,
                    order_type=OrderType.LIMIT,
                    limit_price=signal.price
                )
                self._log(f"BUY order placed: {order_id} for {quantity} shares")
                
            elif signal.action == "SELL":
                # Get current position
                position = self.broker.get_position(signal.symbol)
                
                if position is None or position.quantity <= 0:
                    self._log(f"No position to sell for {signal.symbol}")
                    return
                
                order_id = self.broker.place_order(
                    symbol=signal.symbol,
                    side=OrderSide.SELL,
                    quantity=position.quantity,
                    order_type=OrderType.LIMIT,
                    limit_price=signal.price
                )
                self._log(f"SELL order placed: {order_id} for {position.quantity} shares")
                
        except Exception as e:
            self._log(f"Error executing signal: {e}")
    
    def _needs_realtime_polling(self) -> bool:
        """Check if any strategy needs real-time price monitoring."""
        for strategy in self.strategies.values():
            if strategy.state in (StrategyState.PULLBACK, StrategyState.IN_POSITION):
                return True
        return False
    
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
                self._log(f"Failed to get quotes: {resp.status_code}")
                return prices
            
            data = resp.json()
            for symbol, quote in data.items():
                # Extract last price from quote response
                if "quote" in quote:
                    prices[symbol] = float(quote["quote"].get("lastPrice", 0))
                elif "lastPrice" in quote:
                    prices[symbol] = float(quote["lastPrice"])
                    
        except Exception as e:
            self._log(f"Error fetching quotes: {e}")
        
        return prices
    
    def _fetch_candles(self, symbol: str, num_candles: int = 1) -> List[Candle]:
        """
        Fetch the last N 5-minute candles for a symbol.
        
        Args:
            symbol: Stock symbol
            num_candles: Number of candles to fetch (from most recent)
        
        Returns:
            List of Candles (oldest first), may be empty
        """
        
        try:
            # Start from market open today to limit data
            now_et = datetime.now(self.ET)
            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            
            resp = self.client.get_price_history_every_five_minutes(
                symbol,
                start_datetime=market_open,
                end_datetime=None,
                need_extended_hours_data=False,
                need_previous_close=False
            )
            
            if resp.status_code != httpx.codes.OK:
                self._log(f"Failed to get candles for {symbol}: {resp.status_code}")
                return []
            
            data = resp.json()
            candles = data.get("candles", [])
            
            if not candles:
                return []
            
            # Get last N candles (excluding the very last which may be incomplete)
            # Take from index -(num_candles+1) to -1
            start_idx = max(0, len(candles) - num_candles - 1)
            selected = candles[start_idx:-1] if len(candles) > 1 else []
            
            result = []
            for c in selected:
                candle_time = datetime.fromtimestamp(c["datetime"] / 1000)
                
                # Skip if we already processed this candle
                if symbol in self._last_candle_time:
                    if candle_time <= self._last_candle_time[symbol]:
                        continue
                
                result.append(Candle(
                    timestamp=candle_time,
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    volume=int(c["volume"])
                ))
            
            # Update last candle time
            if result:
                self._last_candle_time[symbol] = result[-1].timestamp
            
            return result
            
        except Exception as e:
            self._log(f"Error fetching candles for {symbol}: {e}")
            return []
    
    def _process_candles(self, num_candles: int = 1):
        """Fetch and process 5-minute candles for all symbols."""
        for symbol in self.symbols:
            candles = self._fetch_candles(symbol, num_candles)
            for candle in candles:
                self._log(
                    f"{symbol}: O={candle.open:.2f} H={candle.high:.2f} "
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
        self._log("Starting live trading engine...")
        self.running = True
        
        last_candle_slot = 5  # Slot 5 = :25 minute mark, ensures we catch candles from market open
        
        try:
            while self.running:
                now_et = datetime.now(self.ET)
                
                # Check if market is closed (4:00 PM ET)
                if now_et.time() >= dt_time(16, 0):
                    self._log("Market closed - stopping engine")
                    break
                
                # Poll candles at each 5-minute boundary (e.g., :00, :05, :10...)
                current_5min_slot = now_et.minute // 5
                if current_5min_slot != last_candle_slot:
                    # Calculate how many candles we need to process
                    num_candles = current_5min_slot - last_candle_slot
                    if num_candles < 0:
                        num_candles += 12  # Handle hour boundary (0-11 slots per hour)
                    
                    self._process_candles(num_candles)
                    last_candle_slot = current_5min_slot
                
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
            self._log("Interrupted by user")
        except Exception as e:
            self._log(f"Engine error: {e}")
            raise
        finally:
            self.stop()
    
    def stop(self):
        """Stop the live trading engine."""
        self._log("Stopping engine...")
        self.running = False
        
        # Print broker summary
        if hasattr(self.broker, 'print_summary'):
            self.broker.print_summary()
        
        self._log("Engine stopped")
