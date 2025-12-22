from abc import ABC, abstractmethod
import pandas as pd

class IDataProvider(ABC):
    @abstractmethod
    def get_history(self, ticker: str, interval: str, period: str = "max", end_date: str = None) -> pd.DataFrame:
        """
        Fetch historical data for a ticker.
        :param ticker: Symbol (e.g., 'AAPL')
        :param interval: Timeframe (e.g., '1d', '5m')
        :param period: Lookback period (e.g., '1y', 'max')
        :param end_date: End date for data fetching (exclusive), optional.
        :return: DataFrame with OHLCV data
        """
        pass

    @abstractmethod
    def get_quote(self, ticker: str) -> float:
        """
        Get the latest price.
        """
        pass
