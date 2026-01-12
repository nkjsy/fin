import pandas as pd
from providers.yfinance_lib import YFinanceProvider
from data_manager import DataManager
from strategy import BullFlagStrategy, RsiStrategy
from backtester import BacktestEngine
from utils import get_next_day
from plotting import plot_performance

# --- CONFIGURATION ---
DATA_DIR = "data"
TICKER = "HUYA"  # Single ticker to debug
TIMEFRAME = "minute5"
INITIAL_CAPITAL = 10000.0
CURRENT_DATE = "2026-01-08"  # Set to "YYYY-MM-DD" to simulate a specific trading day

# Strategy Config
STRATEGY_TYPE = BullFlagStrategy  
# Options: BullFlagStrategy, RsiStrategy
# ---------------------

def main():
    # Initialize
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)

    print(f"--- Starting Single Stock Backtest for {TICKER} on {CURRENT_DATE} ---")

    # Download fresh data
    next_date = get_next_day(CURRENT_DATE)
    print(f"Downloading data for {TICKER}...")
    success = data_manager.download_data(TICKER, TIMEFRAME, end_date=next_date, period="1d")
    if not success:
        print(f"Failed to download data for {TICKER}.")
        return

    # Load Data
    df = data_manager.load_data(TICKER, TIMEFRAME)
    
    if df.empty:
        print(f"Data empty for {TICKER}.")
        return

    print(f"Loaded {len(df)} rows of data.")

    # Filter data to only the trading date to avoid trades on other days
    date_col = "Datetime" if "Datetime" in df.columns else "Date"
    if date_col in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        trading_dt = pd.to_datetime(CURRENT_DATE)
        df = df[df[date_col].dt.date == trading_dt.date()]

    if df.empty:
        print(f"No data for {TICKER} on {CURRENT_DATE}.")
        return

    print(f"Filtered to {len(df)} rows for {CURRENT_DATE}.")

    # Run Strategy
    print(f"Running {STRATEGY_TYPE.__name__} strategy...")
    strategy = STRATEGY_TYPE()
    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    
    df_res, trades, metrics = engine.run(df, strategy)

    # Print Metrics
    print("\n--- Results ---")
    print(f"Final Equity: ${metrics['Final-Equity']:.2f}")
    print(f"Return:       {metrics['Return %']:.2f}%")
    print(f"Total Trades: {metrics['Trades']}")
    
    if not trades.empty:
        print("\n--- Trade Log ---")
        print(trades.to_string())
    else:
        print("\nNo trades executed.")

    # Plotting
    print("\nRendering chart...")
    plot_performance(TICKER, df_res, trades, TIMEFRAME, STRATEGY_TYPE.__name__, title_prefix="DEBUG:", trading_date=CURRENT_DATE)

if __name__ == "__main__":
    main()
