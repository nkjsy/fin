import os
import pandas as pd
from tqdm import tqdm
import yfinance as yf
from providers.interfaces import IDataProvider

class DataManager:
    def __init__(self, data_dir: str, provider: IDataProvider):
        self.data_dir = data_dir
        self.provider = provider
        
    def _get_folder(self, interval: str) -> str:
        return os.path.join(self.data_dir, interval)

    def save_data(self, ticker: str, interval: str, df: pd.DataFrame):
        if df.empty:
            return
        folder = self._get_folder(interval)
        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, f"{ticker}.parquet")
        
        # Explicitly remove existing file to ensure complete overwrite
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                raise Exception(f"Error removing old file {file_path}: {e}")
        
        # Save as parquet
        df.to_parquet(file_path, engine='fastparquet')

    def load_data(self, ticker: str, interval: str) -> pd.DataFrame:
        folder = self._get_folder(interval)
        file_path = os.path.join(folder, f"{ticker}.parquet")
        if os.path.exists(file_path):
            try:
                return pd.read_parquet(file_path, engine='fastparquet')
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
                return pd.DataFrame()
        return pd.DataFrame()

    def download_data(self, ticker: str, interval: str, period: str = "1y", end_date: str = None) -> bool:
        """
        Downloads and saves data for a single ticker. Returns True if successful.
        If end_date is provided, data is fetched up to that date (exclusive).
        """
        print(f"Downloading data for {ticker}...")
        df = self.provider.get_history(ticker, interval, period, end_date=end_date)
        if not df.empty:
            self.save_data(ticker, interval, df)
            return True
        return False

    def update_universe(self, tickers: list, interval: str, current_date: str = None):
        """
        Fetches latest data for all tickers to create a summary index using yf.Ticker.info.
        Does NOT save individual ticker history to disk.
        """
        summary_data = []
        
        print(f"Starting universe summary update for {len(tickers)} tickers...")
        
        for ticker in tqdm(tickers):
            try:
                info = yf.Ticker(ticker).info
                
                # Extract required fields
                price = info.get("fiftyDayAverage")
                volume = info.get("volume")
                float_shares = info.get("floatShares")
                
                if price is not None:
                    summary_data.append({
                        "Ticker": ticker,
                        "Price": price,
                        "Volume": volume,
                        "FloatShares": float_shares
                    })
            except Exception as e:
                print(f"Failed to fetch info for {ticker}: {e}")
        
        print(f"Successfully fetched data for {len(summary_data)} tickers.")
        # Save summary index
        if summary_data:
            summary_df = pd.DataFrame(summary_data)
            summary_path = os.path.join(self.data_dir, "universe_summary.parquet")
            summary_df.to_parquet(summary_path)
            print(f"Universe summary updated with {len(summary_df)} records.")
        else:
            print("No data fetched.")


    def get_data(self, ticker: str, interval: str, period: str = "1y", end_date: str = None) -> pd.DataFrame:
        """
        Fetches data for a single ticker directly from the provider.
        Does not save to disk.
        """
        print(f"Fetching data for {ticker}...")
        return self.provider.get_history(ticker, interval, period, end_date=end_date)
