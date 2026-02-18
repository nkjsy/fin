import pandas as pd

from strategy.macd_obv_divergence import MacdObvDivergenceStrategy


def test_macd_obv_strategy_smoke():
    # Simple synthetic daily data for smoke testing shape/columns.
    periods = 220
    dates = pd.date_range(start="2024-01-01", periods=periods, freq="D")

    close = [100 + (i * 0.2) + ((-1) ** i) * 0.8 for i in range(periods)]
    open_ = [c - 0.3 for c in close]
    high = [c + 1.0 for c in close]
    low = [c - 1.0 for c in close]
    volume = [1_000_000 + (i % 15) * 30_000 for i in range(periods)]

    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }
    )

    strategy = MacdObvDivergenceStrategy(
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        obv_ma=30,
        pivot_window=5,
        max_pivot_gap=60,
    )

    result = strategy.generate_signals(df)

    required_columns = {
        "Signal",
        "Entry_Price",
        "Exit_Price",
        "Stop_Loss",
        "DIFF",
        "OBV",
        "OBV_SMA",
        "Bullish_Divergence",
        "Bearish_Divergence",
        "OBV_Bull_Cross",
        "OBV_Bear_Cross",
    }

    missing = required_columns.difference(set(result.columns))
    assert not missing, f"Missing columns: {missing}"

    # Signal values should stay in {-1, 0, 1}
    invalid_signal = ~result["Signal"].isin([-1, 0, 1])
    assert not invalid_signal.any(), "Signal column contains invalid values"

    print("MACD+OBV strategy smoke test passed.")


if __name__ == "__main__":
    test_macd_obv_strategy_smoke()
