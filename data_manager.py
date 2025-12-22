import os
import pandas as pd
from interfaces import IDataProvider

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

    def update_universe(self, tickers: list, interval: str, period: str = "1y"):
        """
        Fetches data for all tickers, saves to parquet, and creates a summary index.
        """
        summary_data = []
        
        print(f"Starting update for {len(tickers)} tickers...")
        
        for ticker in tickers:
            print(f"Updating {ticker}...") # Verbose logging enabled
            df = self.provider.get_history(ticker, interval, period)
            if not df.empty:
                self.save_data(ticker, interval, df)
                
                # Create summary info (last row)
                last_row = df.iloc[-1]
                
                # Handle different index names (Date vs Datetime)
                date_val = last_row.get("Date") if "Date" in last_row else last_row.name
                
                summary_data.append({
                    "Ticker": ticker,
                    "Date": date_val,
                    "Close": last_row["Close"],
                    "Volume": last_row["Volume"]
                })
        
        # Save summary index
        if summary_data:
            summary_df = pd.DataFrame(summary_data)
            summary_path = os.path.join(self.data_dir, "universe_summary.parquet")
            summary_df.to_parquet(summary_path)
            print(f"Universe summary updated with {len(summary_df)} records.")
        else:
            print("No data fetched.")
