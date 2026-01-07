from .base import BaseScanner
from providers.yfinance_lib import YFinanceProvider
from data_manager import DataManager
from utils import get_next_day
import pandas as pd
import datetime

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
        RELATIVE_VOLUME_THRESHOLD = 5.0
        PRICE_GAP_THRESHOLD = 0.03  # 3%

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
            if df.empty:
                continue

            # Ensure Datetime column exists and is datetime
            if "Datetime" not in df.columns:
                # If Datetime is the index, reset it to be a column
                df = df.reset_index()
            
            df["Datetime"] = pd.to_datetime(df["Datetime"])
            
            # Filter for relevant dates
            target_date = pd.to_datetime(current_date).date()
            df["Date"] = df["Datetime"].dt.date
            
            available_dates = sorted(df["Date"].unique())
            
            try:
                target_idx = available_dates.index(target_date)
            except ValueError:
                print(f"Target date {target_date} not found in data for {ticker}")
                continue
                
            if target_idx == 0:
                print(f"No previous day data for {ticker}")
                continue
                
            prev_date = available_dates[target_idx - 1]
            
            # filter 1: relative volume: volume 9:30-9:40 is 2x volume at the same time yesterday
            start_time = datetime.time(9, 30)
            end_time = datetime.time(9, 40)
            
            target_day_data = df[df["Date"] == target_date]
            prev_day_data = df[df["Date"] == prev_date]
            
            def get_vol_930_940(day_df):
                times = day_df["Datetime"].dt.time
                mask = (times >= start_time) & (times < end_time)
                return day_df[mask]["Volume"].sum()

            vol_target = get_vol_930_940(target_day_data)
            vol_prev = get_vol_930_940(prev_day_data)
            
            if vol_prev == 0:
                continue
                
            if vol_target < RELATIVE_VOLUME_THRESHOLD * vol_prev:
                continue

            # filter 2: price momentum: open gap up 3% than close yesterday
            if target_day_data.empty or prev_day_data.empty:
                continue
            
            target_open = target_day_data.iloc[0]["Open"]
            prev_close = prev_day_data.iloc[-1]["Close"]
            
            if prev_close == 0:
                continue
                
            gap_percent = (target_open - prev_close) / prev_close
            
            if gap_percent < PRICE_GAP_THRESHOLD:
                continue
                
            print(f"{ticker} passed all filters!")
            final_tickers.append(ticker)
        
        return final_tickers
