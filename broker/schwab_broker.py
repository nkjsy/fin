"""
Schwab Broker

Live broker implementation using Schwab API via schwab-py.
Executes real orders through Charles Schwab.
"""

from datetime import datetime
from typing import Dict, List, Optional
import httpx
from zoneinfo import ZoneInfo

from schwab.client import Client
from schwab.orders.equities import (
    equity_buy_market, equity_buy_limit,
    equity_sell_market, equity_sell_limit
)
from schwab.utils import Utils

from broker.interfaces import (
    IBroker, Order, Position, OrderType, OrderSide, OrderStatus
)
import config


# Eastern timezone for market hours
ET = ZoneInfo("America/New_York")


class SchwabBroker(IBroker):
    """
    Live trading broker using Schwab API.
    
    Executes real orders through Charles Schwab.
    Use with caution - this involves real money!
    """
    
    def __init__(self, client: Client, account_hash: str = None):
        """
        Initialize SchwabBroker.
        
        Args:
            client: Authenticated schwab Client instance
            account_hash: Account hash for order placement. If None, fetches first account.
        """
        self.client = client
        self.account_hash = account_hash or self._get_account_hash()
        
        print(f"[SCHWAB] Broker initialized for account {self.account_hash[:8]}...")
    
    def _get_account_hash(self) -> str:
        """Get the account hash for the first linked account."""
        resp = self.client.get_account_numbers()
        if resp.status_code != httpx.codes.OK:
            raise RuntimeError(f"Failed to get account numbers: {resp.status_code}")
        
        accounts = resp.json()
        if not accounts:
            raise RuntimeError("No accounts found")
        
        # If specific account number configured, find it
        if config.SCHWAB_ACCOUNT_NUMBER:
            for acc in accounts:
                if acc["accountNumber"] == config.SCHWAB_ACCOUNT_NUMBER:
                    return acc["hashValue"]
            raise RuntimeError(f"Account {config.SCHWAB_ACCOUNT_NUMBER} not found")
        
        # Otherwise, use first account
        return accounts[0]["hashValue"]
    
    def _log(self, message: str):
        """Log a message with timestamp (Eastern time)."""
        timestamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[SCHWAB] {timestamp} | {message}")
    
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
        Place an order through Schwab.
        
        Args:
            symbol: Ticker symbol
            side: BUY or SELL
            quantity: Number of shares
            order_type: MARKET or LIMIT (STOP orders not yet implemented)
            limit_price: Limit price for limit orders
            stop_price: Stop price (not yet implemented)
            
        Returns:
            Order ID as string
        """
        # Build order spec
        if side == OrderSide.BUY:
            if order_type == OrderType.MARKET:
                order_spec = equity_buy_market(symbol, quantity)
            elif order_type == OrderType.LIMIT:
                if limit_price is None:
                    raise ValueError("Limit price required for LIMIT orders")
                order_spec = equity_buy_limit(symbol, quantity, limit_price)
            else:
                raise ValueError(f"Order type {order_type} not yet implemented for BUY")
        else:  # SELL
            if order_type == OrderType.MARKET:
                order_spec = equity_sell_market(symbol, quantity)
            elif order_type == OrderType.LIMIT:
                if limit_price is None:
                    raise ValueError("Limit price required for LIMIT orders")
                order_spec = equity_sell_limit(symbol, quantity, limit_price)
            else:
                raise ValueError(f"Order type {order_type} not yet implemented for SELL")
        
        # Place order
        self._log(f"Placing order: {side.value} {quantity} {symbol} {order_type.value}")
        
        resp = self.client.place_order(self.account_hash, order_spec)
        
        if resp.status_code not in [httpx.codes.OK, httpx.codes.CREATED]:
            self._log(f"Order failed: {resp.status_code} - {resp.text}")
            raise RuntimeError(f"Order placement failed: {resp.status_code}")
        
        # Extract order ID from response
        order_id = Utils.extract_order_id(resp)
        
        self._log(f"Order placed successfully: {order_id}")
        return str(order_id)
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        self._log(f"Cancelling order: {order_id}")
        
        try:
            resp = self.client.cancel_order(order_id, self.account_hash)
            
            if resp.status_code == httpx.codes.OK:
                self._log(f"Order {order_id} cancelled")
                return True
            else:
                self._log(f"Cancel failed: {resp.status_code}")
                return False
                
        except Exception as e:
            self._log(f"Cancel error: {e}")
            return False
    
    def get_order_status(self, order_id: str) -> Optional[Order]:
        """Get the status of an order."""
        try:
            resp = self.client.get_order(order_id, self.account_hash)
            
            if resp.status_code != httpx.codes.OK:
                return None
            
            data = resp.json()
            
            # Map Schwab status to our OrderStatus
            status_map = {
                "FILLED": OrderStatus.FILLED,
                "QUEUED": OrderStatus.SUBMITTED,
                "WORKING": OrderStatus.SUBMITTED,
                "PENDING_ACTIVATION": OrderStatus.PENDING,
                "CANCELED": OrderStatus.CANCELLED,
                "REJECTED": OrderStatus.REJECTED,
            }
            
            schwab_status = data.get("status", "")
            status = status_map.get(schwab_status, OrderStatus.PENDING)
            
            # Parse order details
            order_legs = data.get("orderLegCollection", [])
            if order_legs:
                leg = order_legs[0]
                symbol = leg.get("instrument", {}).get("symbol", "")
                quantity = int(leg.get("quantity", 0))
                side = OrderSide.BUY if leg.get("instruction") == "BUY" else OrderSide.SELL
            else:
                symbol = ""
                quantity = 0
                side = OrderSide.BUY
            
            # Determine order type
            order_type_str = data.get("orderType", "MARKET")
            order_type = OrderType.LIMIT if order_type_str == "LIMIT" else OrderType.MARKET
            
            return Order(
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                limit_price=data.get("price"),
                status=status,
                filled_quantity=int(data.get("filledQuantity", 0)),
                filled_price=float(data.get("averagePrice", 0.0)),
                timestamp=data.get("enteredTime", "")
            )
            
        except Exception as e:
            self._log(f"Error getting order status: {e}")
            return None
    
    def get_positions(self) -> List[Position]:
        """Get all current positions."""
        try:
            resp = self.client.get_account(
                self.account_hash,
                fields=[Client.Account.Fields.POSITIONS]
            )
            
            if resp.status_code != httpx.codes.OK:
                self._log(f"Failed to get positions: {resp.status_code}")
                return []
            
            data = resp.json()
            positions_data = data.get("securitiesAccount", {}).get("positions", [])
            
            positions = []
            for p in positions_data:
                instrument = p.get("instrument", {})
                if instrument.get("assetType") != "EQUITY":
                    continue
                
                positions.append(Position(
                    symbol=instrument.get("symbol", ""),
                    quantity=int(p.get("longQuantity", 0) - p.get("shortQuantity", 0)),
                    average_price=float(p.get("averagePrice", 0.0)),
                    current_price=float(p.get("marketValue", 0.0)) / max(int(p.get("longQuantity", 1)), 1),
                    unrealized_pnl=float(p.get("unrealizedPnL", 0.0))
                ))
            
            return positions
            
        except Exception as e:
            self._log(f"Error getting positions: {e}")
            return []
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol:
                return pos
        return None
    
    def get_account_balance(self) -> Dict[str, float]:
        """Get account balance information."""
        try:
            resp = self.client.get_account(self.account_hash)
            
            if resp.status_code != httpx.codes.OK:
                self._log(f"Failed to get account: {resp.status_code}")
                return {"cash": 0, "buying_power": 0, "equity": 0, "unrealized_pnl": 0}
            
            data = resp.json()
            balances = data.get("securitiesAccount", {}).get("currentBalances", {})
            
            return {
                "cash": float(balances.get("cashBalance", 0.0)),
                "buying_power": float(balances.get("buyingPower", 0.0)),
                "equity": float(balances.get("equity", 0.0)),
                "unrealized_pnl": float(balances.get("unrealizedPL", 0.0))
            }
            
        except Exception as e:
            self._log(f"Error getting account balance: {e}")
            return {"cash": 0, "buying_power": 0, "equity": 0, "unrealized_pnl": 0}
    
    def get_buying_power(self) -> float:
        """Get available buying power."""
        balance = self.get_account_balance()
        return balance.get("buying_power", 0.0)
