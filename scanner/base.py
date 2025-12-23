import pandas as pd
import os
from abc import ABC, abstractmethod

class BaseScanner(ABC):
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.summary_path = os.path.join(data_dir, "universe_summary.parquet")
        # if using yfinance.screen, max 250 tickers returned
        self.limit = 250

    def load_summary(self) -> pd.DataFrame:
        if os.path.exists(self.summary_path):
            return pd.read_parquet(self.summary_path)
        return pd.DataFrame()

    @abstractmethod
    def scan(self, **kwargs) -> list:
        pass
