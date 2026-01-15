"""
Broker module for order execution.

Provides IBroker interface and implementations:
- PaperBroker: Simulated trading with console logging
- SchwabBroker: Live trading via Schwab API
"""

from broker.interfaces import IBroker
from broker.paper_broker import PaperBroker
from broker.schwab_broker import SchwabBroker

__all__ = ["IBroker", "PaperBroker", "SchwabBroker"]
