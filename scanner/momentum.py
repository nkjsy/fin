from .base import BaseScanner
from providers.yfinance_lib import YFinanceProvider
from data_manager import DataManager
from utils import get_next_day

class MomentumScanner(BaseScanner):
    def scan(self, current_date: str = None, min_price: float = 0, max_price: float = 0, max_float: int = 0) -> list:
        df = self.load_summary()
        if df.empty:
            return []
        print(f"Loaded universe summary with {len(df)} tickers.")
        
        # initial filters
        if "Price" not in df.columns or "FloatShares" not in df.columns:
            return []

        mask = (df["Price"] >= min_price) & (df["Price"] <= max_price) & (df["FloatShares"] <= max_float)
        filtered_df = df[mask]

        # deep filters
        TIMEFRAME = "minute5"
        DATA_DIR = "data"
        PERIOD = "2d"
        next_date = get_next_day(current_date) # in order to get current date data
        provider = YFinanceProvider()
        data_manager = DataManager(DATA_DIR, provider)
        final_tickers = []
        for ticker in filtered_df["Ticker"].tolist():
            print(f"Scanning {ticker}...")
            success = data_manager.download_data(ticker, interval=TIMEFRAME, end_date=next_date, period=PERIOD)
            if not success:
                print(f"Failed to download data for {ticker}. Drop it.")
                continue

            # Load Data
            df = data_manager.load_data(ticker, TIMEFRAME)

            # fillter 1: relative volume: volume 9:30-9:40 is 2x volume at the same time yesterday

            # filter 2: price momentum: open gap up 3% than close yesterday
        
        return final_tickers
