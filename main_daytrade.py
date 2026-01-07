import os
import pandas as pd
import plotly.graph_objects as go
from providers.yfinance_lib import YFinanceProvider
from data_manager import DataManager
from strategy import BullFlagStrategy, RsiStrategy
from backtester import BacktestEngine
from scanner import MomentumScanner
from utils import get_us_stocks, get_next_day

# --- CONFIGURATION ---
DATA_DIR = "data"
TIMEFRAME = "minute5"
INITIAL_CAPITAL = 10000.0
MIN_PRICE = 2
MAX_PRICE = 50
MAX_FLOAT = 100000000  # 100 million shares
CURRENT_DATE = "2026-01-05" # Set to "YYYY-MM-DD" to simulate a specific trading day

# Strategy Config
STRATEGY_TYPE = BullFlagStrategy  
# Options: "BullFlagStrategy", "RsiStrategy"
# ---------------------

def ensure_universe_data(data_manager, interval, current_date):
    summary_path = os.path.join(data_manager.data_dir, f"universe_summary_{current_date}.parquet")
    # If simulating a specific date, we might want to force update or check if summary is fresh enough?
    # For simplicity, let's assume if summary exists, it's good, unless we want to force it.
    # But if we change dates, the old summary might be wrong. 
    # Let's just update if it doesn't exist or if we are in simulation mode (to be safe).
    
    if not os.path.exists(summary_path):
        print(f"Updating universe summary on {current_date}...")
        tickers = get_us_stocks(2000)
        data_manager.update_universe(tickers, interval, current_date=current_date)

def main():
    # Initialize
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)
    scanner = MomentumScanner(DATA_DIR)

    print(f"--- Starting Automated Trading Flow on {CURRENT_DATE} ---")

    # 0. Ensure Universe Data reflects the day before CURRENT_DATE
    ensure_universe_data(data_manager, TIMEFRAME, current_date=CURRENT_DATE)

    # 1. Scan for stocks
    print("Scanning for stocks...")
    
    tickers = scanner.scan(current_date=CURRENT_DATE, min_price=MIN_PRICE, max_price=MAX_PRICE, max_float=MAX_FLOAT)
    
    if not tickers:
        print("No stocks found matching criteria. Please update data or change criteria.")
        return

    print(f"Scanner found {len(tickers)} stocks: {tickers}")

    # 2. Run Strategy on each stock
    results = []
    next_date = get_next_day(CURRENT_DATE) # in order to get current date data

    for ticker in tickers:
        print(f"Processing {ticker}...")
        
        # Always download fresh data and overwrite old data to ensure integrity and prevent look-ahead bias
        success = data_manager.download_data(ticker, TIMEFRAME, end_date=next_date, period="1d")
        if not success:
            print(f"Failed to download data for {ticker}. Skipping...")
            continue

        # Load Data
        df = data_manager.load_data(ticker, TIMEFRAME)
        
        if df.empty:
            print(f"Data empty for {ticker}. Skipping...")
            continue

        # Run Strategy
        strategy = STRATEGY_TYPE()
        engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
        
        df_res, trades, metrics = engine.run(df, strategy)
        
        results.append({
            "Ticker": ticker,
            "Final-Equity": metrics['Final-Equity'],
            "Return %": metrics['Return %'],
            "Trades": metrics['Trades'],
            "Trades_Log": trades,
            "Data": df_res
        })

    # 3. Display Results
    print("\n--- Summary Results ---")
    results_df = pd.DataFrame([{k: v for k, v in r.items() if k not in ['Trades_Log', 'Data']} for r in results])
    if not results_df.empty:
        print(results_df.sort_values("Return %", ascending=False))
    else:
        print("No results generated.")

    # 4. Plotting (Optional - maybe plot the best performer)
    if not results_df.empty:
        best_performer = results_df.sort_values("Return %", ascending=False).iloc[0]
        best_ticker = best_performer["Ticker"]
        print(f"\nPlotting best performer: {best_ticker}")
        
        best_result = next(r for r in results if r["Ticker"] == best_ticker)
        df_res = best_result["Data"]
        trades = best_result["Trades_Log"]
        
        fig = go.Figure()

        # Candlestick
        fig.add_trace(go.Candlestick(x=df_res["Date"],
                        open=df_res["Open"],
                        high=df_res["High"],
                        low=df_res["Low"],
                        close=df_res["Close"],
                        name="OHLC"))

        # Buy Markers
        if not trades.empty:
            buys = trades[trades["Action"] == "BUY"]
            if not buys.empty:
                fig.add_trace(go.Scatter(x=buys["Date"], y=buys["Price"], mode="markers", 
                                         marker=dict(symbol="triangle-up", size=12, color="green"), name="Buy"))

            # Sell Markers
            sells = trades[trades["Action"] == "SELL"]
            if not sells.empty:
                fig.add_trace(go.Scatter(x=sells["Date"], y=sells["Price"], mode="markers", 
                                         marker=dict(symbol="triangle-down", size=12, color="red"), name="Sell"))

        fig.update_layout(
            title=f"{best_ticker} - {TIMEFRAME} - {STRATEGY_TYPE.__name__}",
            height=800,
            xaxis_rangeslider_visible=False
        )
        
        fig.show()

if __name__ == "__main__":
    main()
