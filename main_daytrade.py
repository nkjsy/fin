import os
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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
CURRENT_DATE = "2026-01-06" # Set to "YYYY-MM-DD" to simulate a specific trading day

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
        tickers = get_us_stocks(1000)
        data_manager.update_universe(tickers, interval, current_date=current_date)

def plot_performance(ticker, df_res, trades, timeframe, strategy_name, title_prefix="", trading_date=None):
    # Make a copy to avoid modifying original
    df_plot = df_res.copy()
    
    # Handle yfinance column naming: 'Datetime' for intraday, 'Date' for daily
    date_col = "Datetime" if "Datetime" in df_plot.columns else "Date"
    
    # Ensure Date format - handle both datetime and unix timestamps
    if not pd.api.types.is_datetime64_any_dtype(df_plot[date_col]):
        # Try parsing as datetime first, then as unix timestamp
        try:
            df_plot[date_col] = pd.to_datetime(df_plot[date_col])
        except:
            df_plot[date_col] = pd.to_datetime(df_plot[date_col], unit='s')
    
    # Filter to only the trading date if specified
    if trading_date:
        trading_dt = pd.to_datetime(trading_date)
        df_plot = df_plot[df_plot[date_col].dt.date == trading_dt.date()]
    
    if df_plot.empty:
        print(f"No data for {ticker} on {trading_date}")
        return

    # Create figure with secondary y-axis for volume overlay
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Candlestick (Primary Y)
    fig.add_trace(go.Candlestick(x=df_plot[date_col],
                    open=df_plot["Open"],
                    high=df_plot["High"],
                    low=df_plot["Low"],
                    close=df_plot["Close"],
                    name="OHLC"), secondary_y=False)

    # Buy Markers - filter trades to the same date range
    if not trades.empty:
        trades_plot = trades.copy()
        trade_date_col = "Datetime" if "Datetime" in trades_plot.columns else "Date"
        if not pd.api.types.is_datetime64_any_dtype(trades_plot[trade_date_col]):
            try:
                trades_plot[trade_date_col] = pd.to_datetime(trades_plot[trade_date_col])
            except:
                trades_plot[trade_date_col] = pd.to_datetime(trades_plot[trade_date_col], unit='s')
        
        if trading_date:
            trading_dt = pd.to_datetime(trading_date)
            trades_plot = trades_plot[trades_plot[trade_date_col].dt.date == trading_dt.date()]
        
        buys = trades_plot[trades_plot["Action"] == "BUY"]
        if not buys.empty:
            fig.add_trace(go.Scatter(x=buys[trade_date_col], y=buys["Price"], mode="markers", 
                                     marker=dict(symbol="triangle-up", size=12, color="green"), name="Buy"), secondary_y=False)

        # Sell Markers
        sells = trades_plot[trades_plot["Action"] == "SELL"]
        if not sells.empty:
            fig.add_trace(go.Scatter(x=sells[trade_date_col], y=sells["Price"], mode="markers", 
                                     marker=dict(symbol="triangle-down", size=12, color="red"), name="Sell"), secondary_y=False)

    # Volume Colors: Green if Close >= Open, Red if Close < Open
    colors = ['green' if row['Close'] >= row['Open'] else 'red' for index, row in df_plot.iterrows()]

    # Volume Bar Chart (Secondary Y)
    # Opacity 0.3 to not obstruct price too much
    fig.add_trace(go.Bar(x=df_plot[date_col], y=df_plot["Volume"], marker_color=colors, name="Volume", opacity=0.3), secondary_y=True)

    # Layout Updates
    fig.update_layout(
        title=f"{title_prefix} {ticker} - {timeframe} - {strategy_name}",
        height=800,
        xaxis_rangeslider_visible=False,
        showlegend=True
    )
    
    # Update axes titles
    fig.update_yaxes(title_text="Price ($)", secondary_y=False)
    fig.update_yaxes(title_text="Volume", showgrid=False, secondary_y=True)
    fig.update_xaxes(title_text="Date/Time")
    
    # Scale volume to only occupy the bottom ~20% of the chart
    if not df_plot["Volume"].empty:
        max_vol = df_plot["Volume"].max()
        fig.update_yaxes(range=[0, max_vol * 5], secondary_y=True)
    
    fig.show()

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

    # 4. Plotting
    if not results_df.empty:
        # Best Performer
        best_performer = results_df.sort_values("Return %", ascending=False).iloc[0]
        best_ticker = best_performer["Ticker"]
        print(f"\nPlotting best performer: {best_ticker}")
        
        best_result = next(r for r in results if r["Ticker"] == best_ticker)
        plot_performance(best_ticker, best_result["Data"], best_result["Trades_Log"], TIMEFRAME, STRATEGY_TYPE.__name__, title_prefix="BEST:", trading_date=CURRENT_DATE)

        # Worst Performer
        if len(results_df) > 1:
            worst_performer = results_df.sort_values("Return %", ascending=True).iloc[0]
            worst_ticker = worst_performer["Ticker"]
            print(f"\nPlotting worst performer: {worst_ticker}")
            
            worst_result = next(r for r in results if r["Ticker"] == worst_ticker)
            plot_performance(worst_ticker, worst_result["Data"], worst_result["Trades_Log"], TIMEFRAME, STRATEGY_TYPE.__name__, title_prefix="WORST:", trading_date=CURRENT_DATE)

if __name__ == "__main__":
    main()
