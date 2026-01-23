"""
Paper Broker

Simulated broker for forward testing. Logs all orders to console
and tracks positions/P&L in memory without executing real trades.
"""

from datetime import datetime
from typing import Dict, List, Optional
import uuid
from zoneinfo import ZoneInfo

from broker.interfaces import (
    IBroker, Order, Position, OrderType, OrderSide, OrderStatus
)
from logger import get_logger


# Eastern timezone for market hours
ET = ZoneInfo("America/New_York")

logger = get_logger("PAPER")


class PaperBroker(IBroker):
    """
    Paper trading broker for forward testing.
    
    - Logs all order activity to console with timestamps
    - Tracks simulated positions and P&L in memory
    - Assumes immediate fills at current price for market orders
    """
    
    def __init__(self, initial_cash: float = 100000.0):
        """
        Initialize PaperBroker.
        
        Args:
            initial_cash: Starting cash balance
        """
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.trade_log: List[dict] = []
        
        logger.info(f"PaperBroker initialized with ${initial_cash:,.2f}")
    
    def _generate_order_id(self) -> str:
        """Generate a unique order ID."""
        return f"PAPER-{uuid.uuid4().hex[:8].upper()}"
    
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> str:
        """
        Place a simulated order.
        
        For market orders, simulates immediate fill at limit_price or last known price.
        For limit/stop orders, order is tracked but not automatically filled.
        """
        order_id = self._generate_order_id()
        timestamp = datetime.now(ET).isoformat()
        
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            status=OrderStatus.SUBMITTED,
            timestamp=timestamp
        )
        
        self.orders[order_id] = order
        
        # Log the order
        price_info = ""
        if limit_price:
            price_info += f" @ ${limit_price:.2f}"
        if stop_price:
            price_info += f" (stop: ${stop_price:.2f})"
        
        logger.info(
            f"ORDER {order_id}: {side.value} {quantity} {symbol} "
            f"{order_type.value}{price_info}"
        )
        
        # For paper trading, simulate immediate fill for MARKET and LIMIT orders
        # LIMIT orders are filled at the limit price (best-case execution)
        if order_type in (OrderType.MARKET, OrderType.LIMIT):
            fill_price = limit_price if limit_price else 0.0
            if fill_price > 0:
                self._fill_order(order_id, fill_price)
        
        return order_id
    
    def _fill_order(self, order_id: str, fill_price: float):
        """Simulate filling an order."""
        if order_id not in self.orders:
            return
        
        order = self.orders[order_id]
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.filled_price = fill_price
        
        # Update cash and positions
        total_cost = fill_price * order.quantity
        
        if order.side == OrderSide.BUY:
            self.cash -= total_cost
            self._update_position_buy(order.symbol, order.quantity, fill_price)
        else:  # SELL
            self.cash += total_cost
            self._update_position_sell(order.symbol, order.quantity, fill_price)
        
        logger.info(
            f"FILLED {order_id}: {order.side.value} {order.quantity} {order.symbol} "
            f"@ ${fill_price:.2f} (Total: ${total_cost:,.2f})"
        )
        
        # Record trade
        self.trade_log.append({
            "timestamp": datetime.now(ET).isoformat(),
            "order_id": order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": fill_price,
            "total": total_cost
        })
    
    def _update_position_buy(self, symbol: str, quantity: int, price: float):
        """Update position after a buy."""
        if symbol in self.positions:
            pos = self.positions[symbol]
            total_cost = (pos.average_price * pos.quantity) + (price * quantity)
            total_qty = pos.quantity + quantity
            pos.average_price = total_cost / total_qty
            pos.quantity = total_qty
        else:
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                average_price=price,
                current_price=price
            )
    
    def _update_position_sell(self, symbol: str, quantity: int, price: float):
        """Update position after a sell."""
        if symbol not in self.positions:
            # Short selling - create negative position
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=-quantity,
                average_price=price,
                current_price=price
            )
            return
        
        pos = self.positions[symbol]
        pos.quantity -= quantity
        
        if pos.quantity == 0:
            # Position closed
            realized_pnl = (price - pos.average_price) * quantity
            logger.info(f"POSITION CLOSED: {symbol} | Realized P&L: ${realized_pnl:,.2f}")
            del self.positions[symbol]
        elif pos.quantity < 0:
            # Went short
            pos.average_price = price
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        if order_id not in self.orders:
            logger.info(f"CANCEL FAILED: Order {order_id} not found")
            return False
        
        order = self.orders[order_id]
        
        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
            logger.info(f"CANCEL FAILED: Order {order_id} already {order.status.value}")
            return False
        
        order.status = OrderStatus.CANCELLED
        logger.info(f"CANCELLED: Order {order_id}")
        return True
    
    def get_order_status(self, order_id: str) -> Optional[Order]:
        """Get the status of an order."""
        return self.orders.get(order_id)
    
    def get_positions(self) -> List[Position]:
        """Get all current positions."""
        return list(self.positions.values())
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        return self.positions.get(symbol)
    
    def get_account_balance(self) -> Dict[str, float]:
        """Get account balance information."""
        # Calculate total equity
        positions_value = sum(
            pos.quantity * pos.current_price 
            for pos in self.positions.values()
        )
        equity = self.cash + positions_value
        
        # Calculate unrealized P&L
        unrealized_pnl = sum(
            pos.quantity * (pos.current_price - pos.average_price)
            for pos in self.positions.values()
        )
        
        return {
            "cash": self.cash,
            "buying_power": self.cash,  # Simplified: no margin
            "equity": equity,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": equity - self.initial_cash - unrealized_pnl
        }
    
    def get_buying_power(self) -> float:
        """Get available buying power."""
        return self.cash
    
    def update_prices(self, prices: Dict[str, float]):
        """
        Update current prices for positions.
        
        Args:
            prices: Dict mapping symbol to current price
        """
        for symbol, price in prices.items():
            if symbol in self.positions:
                pos = self.positions[symbol]
                pos.current_price = price
                pos.unrealized_pnl = pos.quantity * (price - pos.average_price)
    
    def print_summary(self):
        """Print account summary."""
        balance = self.get_account_balance()
        
        logger.info("=" * 50)
        logger.info("ACCOUNT SUMMARY")
        logger.info(f"  Cash:          ${balance['cash']:>12,.2f}")
        logger.info(f"  Equity:        ${balance['equity']:>12,.2f}")
        logger.info(f"  Unrealized PnL:${balance['unrealized_pnl']:>12,.2f}")
        logger.info(f"  Realized PnL:  ${balance['realized_pnl']:>12,.2f}")
        
        if self.positions:
            logger.info("-" * 50)
            logger.info("POSITIONS:")
            for pos in self.positions.values():
                logger.info(
                    f"  {pos.symbol}: {pos.quantity} @ ${pos.average_price:.2f} "
                    f"(current: ${pos.current_price:.2f})"
                )
        
        logger.info("=" * 50)
