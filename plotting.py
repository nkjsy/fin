import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# State colors for chart visualization (3 states only)
STATE_COLORS = {
    'SCANNING': 'gray',
    'PULLBACK': 'orange',
    'IN_POSITION': 'blue',
}


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

        # Sell Markers
        sells = trades_plot[trades_plot["Action"] == "SELL"]
        if not sells.empty:
            fig.add_trace(go.Scatter(
                x=sells["Datetime"], 
                y=sells["Price"], 
                mode="markers", 
                marker=dict(symbol="triangle-down", size=14, color="red", line=dict(width=1, color="darkred")), 
                name="Sell"
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
