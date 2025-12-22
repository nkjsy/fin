import os
import pandas as pd
from providers.interfaces import IDataProvider

class DataManager:
    def __init__(self, data_dir: str, provider: IDataProvider):
        self.data_dir = data_dir
        self.provider = provider
        
    def get_data(self, ticker: str, interval: str, period: str = "1y", end_date: str = None) -> pd.DataFrame:
        """
        Fetches data for a single ticker directly from the provider.
        Does not save to disk.
        """
        print(f"Fetching data for {ticker}...")
        return self.provider.get_history(ticker, interval, period, end_date=end_date)
