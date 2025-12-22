from .base import BaseScanner
import yfinance as yf
from yfinance import EquityQuery

class SimpleScanner(BaseScanner):
    def scan(self, min_price: float = 0, min_volume: float = 0) -> list:
        """
        Scans for stocks using yfinance EquityQuery.
        """
        try:
            # Build query
            # We want: intradayprice >= min_price AND dayvolume >= min_volume AND region == 'us'
            filters = [
                EquityQuery('eq', ['region', 'us'])
            ]
            
            if min_price > 0:
                filters.append(EquityQuery('gte', ['intradayprice', min_price]))
            
            if min_volume > 0:
                filters.append(EquityQuery('gte', ['dayvolume', min_volume]))
                
            q = EquityQuery('and', filters)
            
            # Execute screen
            # We sort by volume descending to get the most active ones
            response = yf.screen(q, size=self.limit, sortField='dayvolume', sortAsc=False)
            
            if 'quotes' in response and response['quotes']:
                return [quote['symbol'] for quote in response['quotes']]
            
            return []
            
        except Exception as e:
            print(f"Error during scanning: {e}")
            return []
