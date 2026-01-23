"""
Broker Interface

Abstract base class defining the broker interface for order execution.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum


class OrderType(Enum):
    """Order types supported by the broker."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderSide(Enum):
    """Order side (buy/sell)."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    """Represents an order."""
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    filled_price: float = 0.0
    timestamp: str = ""


@dataclass
class Position:
    """Represents a position."""
    symbol: str
    quantity: int
    average_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0


class IBroker(ABC):
    """
    Abstract base class for broker implementations.
    
    Provides interface for:
    - Order placement and management
    - Position tracking
    - Account information
    """
    
    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        reason: str = ""
    ) -> str:
        """
        Place an order.
        
        Args:
            symbol: Ticker symbol
            side: BUY or SELL
            quantity: Number of shares
            order_type: MARKET, LIMIT, STOP, or STOP_LIMIT
            limit_price: Limit price (required for LIMIT and STOP_LIMIT orders)
            stop_price: Stop price (required for STOP and STOP_LIMIT orders)
            reason: Reason for the trade (e.g., "Bull flag breakout", "Stop loss hit")
            
        Returns:
            Order ID as string
        """
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.
        
        Args:
            order_id: ID of the order to cancel
            
        Returns:
            True if cancellation was successful
        """
        pass
    
    @abstractmethod
    def get_order_status(self, order_id: str) -> Optional[Order]:
        """
        Get the status of an order.
        
        Args:
            order_id: ID of the order
            
        Returns:
            Order object with current status, or None if not found
        """
        pass
    
    @abstractmethod
    def get_positions(self) -> List[Position]:
        """
        Get all current positions.
        
        Returns:
            List of Position objects
        """
        pass
    
    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get position for a specific symbol.
        
        Args:
            symbol: Ticker symbol
            
        Returns:
            Position object, or None if no position
        """
        pass
    
    @abstractmethod
    def get_account_balance(self) -> Dict[str, float]:
        """
        Get account balance information.
        
        Returns:
            Dict with keys: 'cash', 'buying_power', 'equity', 'unrealized_pnl'
        """
        pass
    
    @abstractmethod
    def get_buying_power(self) -> float:
        """
        Get available buying power.
        
        Returns:
            Available buying power as float
        """
        pass
