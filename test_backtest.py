import pandas as pd
from strategy.bull_flag import BullFlagStrategy


def test_bull_flag_strategy():
    """Test BullFlagStrategy signal generation."""
    # Create dummy data
    data = {
        'Open': [100, 101, 102, 101, 101.5],
        'High': [102, 103, 104, 102, 103],
        'Low': [99, 100, 101, 100.5, 101],
        'Close': [101, 102, 103, 100.8, 102.5],
        'Volume': [1000, 1000, 1000, 500, 1000]
    }
    df = pd.DataFrame(data)
    # Add index as datetime starting 9:30
    df.index = pd.date_range(start="2026-01-09 09:30", periods=5, freq="1min")
    
    print("Input DataFrame:")
    print(df)
    
    strategy = BullFlagStrategy(min_green_bars=2)
    df_res = strategy.generate_signals(df)
    
    print("\nResult DataFrame:")
    print(df_res[['Open', 'Close', 'Signal', 'EMA']])
    
    print(f"\nSignal at index 0: {df_res['Signal'].iloc[0]}")
    print(f"Signal at index 1: {df_res['Signal'].iloc[1]}")


if __name__ == "__main__":
    test_bull_flag_strategy()