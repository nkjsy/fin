import sys
import os

# Add the project root to the python path
# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import plotly.graph_objects as go
from providers.yfinance_lib import YFinanceProvider
from data_manager import DataManager
from strategy import RsiStrategy
from backtester import BacktestEngine

# --- CONFIGURATION ---
DATA_DIR = "data"
TIMEFRAME = "daily1"
TICKER = "MSFT"
RSI_PERIOD = 14
INITIAL_CAPITAL = 10000.0
# ---------------------

def main():
    # Initialize
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)

    print(f"--- Starting Backtest for {TICKER} ---")

    # Load Data
    print(f"Loading data for {TICKER}...")
    df = data_manager.get_data(TICKER, TIMEFRAME)
    
    if df.empty:
        print("Failed to download data.")
        return
    else:
        print(f"Loaded {len(df)} rows of data.")

    # Run Strategy
    print("Running strategy...")
    strategy = RsiStrategy(rsi_period=RSI_PERIOD)
    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    
    df_res, trades, metrics = engine.run(df, strategy)

    # Print Metrics
    print("\n--- Results ---")
    print(f"Final Equity: ${metrics['Final Equity']:.2f}")
    print(f"Return:       {metrics['Return %']:.2f}%")
    print(f"Total Trades: {metrics['Trades']}")
    
    if not trades.empty:
        print("\n--- Trade Log (First 10) ---")
        print(trades.head(10))
    else:
        print("\nNo trades executed.")

    # Plotting
    print("\nRendering chart...")
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
        title=f"{TICKER} - {TIMEFRAME} ({strategy.name})",
        height=800,
        xaxis_rangeslider_visible=False
    )
    
    # Show the plot (opens in browser)
    fig.show()

if __name__ == "__main__":
    main()
