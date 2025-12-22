import sys
import os
import pandas as pd
import plotly.graph_objects as go
from providers.yfinance_lib import YFinanceProvider
from data_manager import DataManager
from strategy import RsiStrategy
from backtester import BacktestEngine
from scanner import SimpleScanner

# --- CONFIGURATION ---
DATA_DIR = "data"
TIMEFRAME = "daily1"
RSI_PERIOD = 14
INITIAL_CAPITAL = 10000.0
MIN_PRICE = 100.0
MIN_VOLUME = 1000000.0
# ---------------------

def main():
    # Initialize
    provider = YFinanceProvider()
    data_manager = DataManager(DATA_DIR, provider)
    scanner = SimpleScanner(DATA_DIR)

    print("--- Starting Automated Trading Flow ---")

    # 1. Scan for stocks
    print("Scanning for stocks...")
    # Ensure summary exists, if not, we might need to update universe first.
    # For this flow, we assume data exists or we run a quick update on a small list if needed.
    # But let's assume the user has run an update before or we just use what's there.
    
    tickers = scanner.scan(min_price=MIN_PRICE, min_volume=MIN_VOLUME)
    
    if not tickers:
        print("No stocks found matching criteria. Please update data or change criteria.")
        return

    print(f"Found {len(tickers)} stocks: {tickers}")

    # 2. Run Strategy on each stock
    results = []

    for ticker in tickers:
        print(f"\nProcessing {ticker}...")
        
        # Load Data
        df = data_manager.load_data(ticker, TIMEFRAME)
        
        if df.empty:
            print(f"Data not found for {ticker}. Downloading...")
            data_manager.update_universe([ticker], TIMEFRAME)
            df = data_manager.load_data(ticker, TIMEFRAME)
            if df.empty:
                print("Failed to download data.")
                return

        # Run Strategy
        strategy = RsiStrategy(rsi_period=RSI_PERIOD)
        engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
        
        df_res, trades, metrics = engine.run(df, strategy)
        
        results.append({
            "Ticker": ticker,
            "Final Equity": metrics['Final Equity'],
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
            title=f"{best_ticker} - {TIMEFRAME} (RSI Strategy)",
            height=800,
            xaxis_rangeslider_visible=False
        )
        
        fig.show()

if __name__ == "__main__":
    main()
