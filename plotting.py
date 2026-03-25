import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# State colors for chart visualization
STATE_COLORS = {
    'SCANNING': 'gray',
    'BUILDING_RANGE': 'cyan',
    'PULLBACK': 'orange',
    'IN_POSITION': 'blue',
}


def plot_equity_comparison(strategy_equity, benchmark_equity, benchmark_label="Benchmark", title="Equity Comparison"):
    """Plot simple strategy-vs-benchmark equity curves."""
    strategy_series = pd.Series(strategy_equity).dropna()
    benchmark_series = pd.Series(benchmark_equity).dropna()

    if strategy_series.empty or benchmark_series.empty:
        print("No equity data available for comparison plot.")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=strategy_series.index,
            y=strategy_series.values,
            mode="lines",
            name="Strategy",
            line=dict(color="#0b6e4f", width=2.5),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=benchmark_series.index,
            y=benchmark_series.values,
            mode="lines",
            name=benchmark_label,
            line=dict(color="#b5651d", width=2.0),
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Date",
        yaxis_title="Equity",
        template="plotly_white",
        height=650,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
    )
    fig.show()


def plot_performance(ticker, df_res, trades, timeframe, strategy_name, title_prefix="", trading_date=None, show_states=False):
    """
    Plot candlestick chart with volume overlay and buy/sell markers.
    
    Args:
        ticker: Stock ticker symbol
        df_res: DataFrame with OHLCV data (must have Datetime, Open, High, Low, Close, Volume)
        trades: DataFrame with trade log (must have Datetime, Action, Price)
        timeframe: Timeframe string for title
        strategy_name: Strategy name for title
        title_prefix: Optional prefix for title (e.g., "BEST:", "WORST:")
        trading_date: Optional date string to filter data to a specific trading day
        show_states: If True and 'State' column exists, show state arrows above candles
    """
    # Make a copy to avoid modifying original
    df_plot = df_res.copy()
    
    # Use "Datetime" as the standard column name
    date_col = "Datetime"
    
    # Ensure datetime format - handle both datetime and unix timestamps
    if not pd.api.types.is_datetime64_any_dtype(df_plot[date_col]):
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

    # Create figure with secondary y-axis for volume
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Candlestick (Primary Y)
    fig.add_trace(go.Candlestick(
        x=df_plot[date_col],
        open=df_plot["Open"],
        high=df_plot["High"],
        low=df_plot["Low"],
        close=df_plot["Close"],
        name="OHLC"
    ), secondary_y=False)

    # EMA Line (if available)
    if 'EMA' in df_plot.columns:
        fig.add_trace(go.Scatter(
            x=df_plot[date_col],
            y=df_plot["EMA"],
            mode='lines',
            line=dict(color='purple', width=1.5),
            name="EMA"
        ), secondary_y=False)

    # Buy/Sell Markers
    if not trades.empty:
        trades_plot = trades.copy()
        
        # Ensure datetime format
        if not pd.api.types.is_datetime64_any_dtype(trades_plot["Datetime"]):
            try:
                trades_plot["Datetime"] = pd.to_datetime(trades_plot["Datetime"])
            except:
                trades_plot["Datetime"] = pd.to_datetime(trades_plot["Datetime"], unit='s')
        
        # Filter trades to the trading date if specified
        if trading_date:
            trading_dt = pd.to_datetime(trading_date)
            trades_plot = trades_plot[trades_plot["Datetime"].dt.date == trading_dt.date()]
        
        # Buy Markers
        buys = trades_plot[trades_plot["Action"] == "BUY"]
        if not buys.empty:
            fig.add_trace(go.Scatter(
                x=buys["Datetime"], 
                y=buys["Price"], 
                mode="markers", 
                marker=dict(symbol="triangle-up", size=14, color="lime", line=dict(width=1, color="darkgreen")), 
                name="Buy"
            ), secondary_y=False)

        # Sell Markers — separate full sells vs partial sells
        sells = trades_plot[trades_plot["Action"] == "SELL"]
        if not sells.empty:
            has_qty = "Qty" in sells.columns
            partial = sells[sells["Qty"] < 1.0] if has_qty else sells.iloc[0:0]
            full = sells[sells["Qty"] >= 1.0] if has_qty else sells

            if not full.empty:
                fig.add_trace(go.Scatter(
                    x=full["Datetime"], 
                    y=full["Price"], 
                    mode="markers", 
                    marker=dict(symbol="triangle-down", size=14, color="red", line=dict(width=1, color="darkred")), 
                    name="Sell"
                ), secondary_y=False)

            if not partial.empty:
                labels = [f"Partial ({int(q*100)}%)" for q in partial["Qty"]]
                fig.add_trace(go.Scatter(
                    x=partial["Datetime"], 
                    y=partial["Price"], 
                    mode="markers", 
                    marker=dict(symbol="triangle-down", size=10, color="orange", line=dict(width=1, color="darkorange")),
                    text=labels,
                    hovertemplate="%{text}<br>$%{y:.2f}<extra></extra>",
                    name="Partial Sell"
                ), secondary_y=False)

    # Volume Colors: Green if Close >= Open, Red if Close < Open
    colors = ['green' if row['Close'] >= row['Open'] else 'red' for _, row in df_plot.iterrows()]

    # Volume Bar Chart (Secondary Y)
    fig.add_trace(go.Bar(
        x=df_plot[date_col], 
        y=df_plot["Volume"], 
        marker_color=colors, 
        name="Volume", 
        opacity=0.3
    ), secondary_y=True)

    # State visualization as tiny colored arrows above candles
    if show_states and 'State' in df_plot.columns:
        # Calculate offset above candles
        price_range = df_plot['High'].max() - df_plot['Low'].min()
        arrow_offset = price_range * 0.02  # 2% above the high
        
        # Add arrows for each state (grouped by state for legend)
        for state_name, color in STATE_COLORS.items():
            state_mask = df_plot['State'] == state_name
            state_data = df_plot[state_mask]
            
            if not state_data.empty:
                fig.add_trace(go.Scatter(
                    x=state_data[date_col],
                    y=state_data['High'] + arrow_offset,
                    mode='markers',
                    marker=dict(
                        symbol='triangle-down',
                        size=6,
                        color=color
                    ),
                    name=state_name,
                    hovertemplate=f'{state_name}<extra></extra>',
                    showlegend=True
                ), secondary_y=False)

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


def parse_log_file(log_path: str) -> dict:
    """
    Parse a log file to extract trading date, tickers, candle data, and trade signals.
    
    Returns:
        dict with keys:
            - trading_date: str (YYYY-MM-DD)
            - tickers: list of ticker symbols
            - candles: dict mapping ticker -> list of (datetime, O, H, L, C, V)
            - trades: dict mapping ticker -> list of (datetime, action, price)
    """
    result = {
        'trading_date': None,
        'tickers': [],
        'candles': {},  # ticker -> [(datetime, O, H, L, C, V), ...]
        'trades': {},  # ticker -> [(datetime, action, price, qty_pct), ...]
        'strategies': {},  # ticker -> strategy name (auto-detected)
    }
    
    # Extract date from log filename (e.g., 2026-01-22_09-31-34.log)
    log_filename = log_path.split('\\')[-1].split('/')[-1]
    date_match = re.match(r'(\d{4}-\d{2}-\d{2})', log_filename)
    if date_match:
        result['trading_date'] = date_match.group(1)
    
    with open(log_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    for line in lines:
        # Extract tickers from scanner confirmed lines
        # LiveMomentumScanner: [09:40:22] [SCANNER] Confirmed 7 tickers: ['SXTP', 'CMCT', ...]
        # FinvizNewsScanner:   [07:05:12] [SCANNER] Confirmed 3, returning top 3: ['AAPL', 'MSFT', ...]
        ticker_match = re.search(r"Confirmed \d+.*?: \[([^\]]+)\]", line)
        if ticker_match:
            tickers_str = ticker_match.group(1)
            new_tickers = [t.strip().strip("'\"" ) for t in tickers_str.split(',')]
            # Accumulate tickers across multiple scan rounds
            for t in new_tickers:
                if t not in result['tickers']:
                    result['tickers'].append(t)
        
        # Extract candle data from ENGINE logs (live and replay)
        # Live:   [09:40:42] [ENGINE] [09:30] MOVE: O=20.14 H=23.50 L=20.14 C=21.73 V=2056439
        # Replay: [08:34:40] [ENGINE] [REPLAY 08:30] PRFX: O=3.05 H=5.70 L=3.00 C=5.30 V=324254
        candle_match = re.search(
            r"\[ENGINE\] \[(?:REPLAY )?(\d{2}:\d{2})\] (\w+): O=([0-9.]+) H=([0-9.]+) L=([0-9.]+) C=([0-9.]+) V=(\d+)",
            line
        )
        if candle_match:
            time_str = candle_match.group(1)  # Candle time like "09:30"
            ticker = candle_match.group(2)
            o = float(candle_match.group(3))
            h = float(candle_match.group(4))
            l = float(candle_match.group(5))
            c = float(candle_match.group(6))
            v = int(candle_match.group(7))
            
            if ticker not in result['candles']:
                result['candles'][ticker] = []
            
            if result['trading_date']:
                dt_str = f"{result['trading_date']} {time_str}:00"
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                result['candles'][ticker].append((dt, o, h, l, c, v))
        
        # Extract BUY/SELL signals
        # Bull flag: [11:20:21] [ROLR] SIGNAL: BUY @ $11.50 (qty=100%) | Bull flag breakout
        # ORB:       [09:40:05] [AAPL] [ORB] SIGNAL: SELL @ $25.51 (qty=50%) | Partial take profit
        # Force-close: [15:55:01] [COMBINED] Force-closed AAPL: 50 shares @ $25.51
        signal_match = re.search(
            r"\[(\d{2}:\d{2}:\d{2})\] \[(\w+)\]\s*(?:\[ORB\])?\s*SIGNAL: (BUY|SELL) @ \$([0-9.]+)(?:\s*\(qty=(\d+)%\))?",
            line
        )
        if signal_match:
            time_str = signal_match.group(1)
            ticker = signal_match.group(2)
            action = signal_match.group(3)
            price = float(signal_match.group(4))
            qty_pct = int(signal_match.group(5)) / 100.0 if signal_match.group(5) else 1.0

            if ticker not in result['trades']:
                result['trades'][ticker] = []

            # Detect strategy from [ORB] prefix
            if '[ORB]' in line and ticker not in result['strategies']:
                result['strategies'][ticker] = 'ORB'
            elif ticker not in result['strategies']:
                result['strategies'][ticker] = 'BullFlagLive'

            if result['trading_date']:
                dt_str = f"{result['trading_date']} {time_str}"
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                result['trades'][ticker].append((dt, action, price, qty_pct))

        # Force-close: [15:55:01] [COMBINED] Force-closed AAPL: 50 shares @ $25.51
        force_match = re.search(
            r"\[(\d{2}:\d{2}:\d{2})\].*Force-closed (\w+):.*@ \$([0-9.]+)", line
        )
        if force_match:
            time_str = force_match.group(1)
            ticker = force_match.group(2)
            price = float(force_match.group(3))

            if ticker not in result['trades']:
                result['trades'][ticker] = []

            if result['trading_date']:
                dt_str = f"{result['trading_date']} {time_str}"
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                result['trades'][ticker].append((dt, 'SELL', price, 1.0))
    
    return result


def plot_from_log(log_path: str, tickers: list = None, timeframe: str = "minute5"):
    """
    Plot charts for tickers based on a log file.
    
    Args:
        log_path: Path to the log file
        tickers: Optional list of specific tickers to plot. If None, plots all tickers from the log.
        timeframe: Timeframe for the chart (default: "minute5")
    """
    # Parse log file
    log_data = parse_log_file(log_path)
    
    if not log_data['trading_date']:
        print(f"Could not extract trading date from log file: {log_path}")
        return
    
    trading_date = log_data['trading_date']
    
    # Determine which tickers to plot — only those with actual candle data
    if tickers:
        tickers_to_plot = [t for t in tickers if t in log_data['candles']]
    else:
        tickers_to_plot = list(log_data['candles'].keys())
    
    if not tickers_to_plot:
        print("No tickers with candle data found in log file")
        return
    
    print(f"Plotting {len(tickers_to_plot)} tickers for {trading_date}")

    # Plot each ticker using candle data from logs
    for ticker in tickers_to_plot:
        candles = log_data['candles'].get(ticker, [])
        
        if not candles:
            print(f"No candle data for {ticker} in log file")
            continue
        
        print(f"Plotting {ticker} with {len(candles)} candles from log...")
        
        # Create DataFrame from candle data
        df = pd.DataFrame(candles, columns=['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume'])
        
        # Create trades DataFrame for this ticker (now includes Qty column)
        trades_list = log_data['trades'].get(ticker, [])
        if trades_list:
            trades = pd.DataFrame(trades_list, columns=['Datetime', 'Action', 'Price', 'Qty'])
        else:
            trades = pd.DataFrame(columns=['Datetime', 'Action', 'Price', 'Qty'])
        
        # Auto-detect strategy name
        strategy_name = log_data['strategies'].get(ticker, 'Live')
        
        # Plot using existing function
        plot_performance(
            ticker=ticker,
            df_res=df,
            trades=trades,
            timeframe=timeframe,
            strategy_name=strategy_name,
            title_prefix="LOG:",
            trading_date=trading_date,
            show_states=False
        )


if __name__ == "__main__":
    # --- CONFIGURATION ---
    LOG_PATH = "logs/2026-01-23_09-26-31.log"
    TICKERS = None  # Set to list like ["MOVE", "RVYL"] to plot specific tickers, or None for all
    TIMEFRAME = "minute5"
    # ----------------------
    plot_from_log(LOG_PATH, tickers=TICKERS, timeframe=TIMEFRAME)