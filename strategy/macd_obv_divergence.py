import pandas as pd
import pandas_ta as ta

from .base import BaseStrategy


class MacdObvDivergenceStrategy(BaseStrategy):
    """Daily long-only strategy using DIFF divergence + OBV SMA cross confirmation.

    Buy signal:
    - Bullish divergence between price and DIFF (MACD line), and
    - OBV crosses above OBV SMA on close confirmation.

    Sell signal:
    - Bearish divergence between price and DIFF, and
    - OBV crosses below OBV SMA on close confirmation.
    """

    def __init__(
        self,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        obv_ma: int = 30,
        pivot_window: int = 5,
        max_pivot_gap: int = 60,
        confirmation_window: int = 10,
        price_tolerance_pct: float = 0.003,
        diff_tolerance_abs: float = 0.03,
    ):
        super().__init__("MACD OBV Divergence Strategy")
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.obv_ma = obv_ma
        self.pivot_window = pivot_window
        self.max_pivot_gap = max_pivot_gap
        self.confirmation_window = confirmation_window
        self.price_tolerance_pct = price_tolerance_pct
        self.diff_tolerance_abs = diff_tolerance_abs

    def _find_pivots(self, series: pd.Series, is_low: bool = True) -> pd.Series:
        """Detect local pivot points with a centered rolling window.

        Args:
            series: Price series (typically `Low` or `High`).
            is_low: True to detect pivot lows, False for pivot highs.

        Returns:
            Boolean Series where True marks pivot locations.
        """
        if len(series) < (self.pivot_window * 2 + 1):
            return pd.Series(False, index=series.index)

        rolling = (
            series.rolling(window=self.pivot_window * 2 + 1, center=True)
            .min()
            if is_low
            else series.rolling(window=self.pivot_window * 2 + 1, center=True).max()
        )

        pivots = series.eq(rolling)
        pivots = pivots.fillna(False)
        return pivots

    def _build_divergence_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Mark bullish/bearish DIFF divergences between consecutive pivot pairs.

        Bullish divergence:
            Price makes a lower low while DIFF makes a higher low.
        Bearish divergence:
            Price makes a higher high while DIFF makes a lower high.

        The two pivots in each pair must be within `max_pivot_gap` bars.
        """
        df = df.copy()
        df["Bullish_Divergence"] = False
        df["Bearish_Divergence"] = False

        low_pivots = df.index[df["Price_Pivot_Low"]].tolist()
        for j in range(1, len(low_pivots)):
            i1 = low_pivots[j - 1]
            i2 = low_pivots[j]

            if i2 - i1 > self.max_pivot_gap:
                continue

            low_1 = df.at[i1, "Low"]
            low_2 = df.at[i2, "Low"]
            diff_1 = df.at[i1, "DIFF"]
            diff_2 = df.at[i2, "DIFF"]

            # Relaxed bullish divergence:
            # - price can be lower low OR near-equal low within tolerance
            # - DIFF can be higher low OR near-equal within absolute tolerance
            price_lower_or_equal = low_2 <= low_1 * (1 + self.price_tolerance_pct)
            diff_higher_or_equal = diff_2 >= diff_1 - self.diff_tolerance_abs

            if price_lower_or_equal and diff_higher_or_equal:
                df.at[i2, "Bullish_Divergence"] = True

        high_pivots = df.index[df["Price_Pivot_High"]].tolist()
        for j in range(1, len(high_pivots)):
            i1 = high_pivots[j - 1]
            i2 = high_pivots[j]

            if i2 - i1 > self.max_pivot_gap:
                continue

            high_1 = df.at[i1, "High"]
            high_2 = df.at[i2, "High"]
            diff_1 = df.at[i1, "DIFF"]
            diff_2 = df.at[i2, "DIFF"]

            # Relaxed bearish divergence with symmetric tolerances.
            price_higher_or_equal = high_2 >= high_1 * (1 - self.price_tolerance_pct)
            diff_lower_or_equal = diff_2 <= diff_1 + self.diff_tolerance_abs

            if price_higher_or_equal and diff_lower_or_equal:
                df.at[i2, "Bearish_Divergence"] = True

        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate trading signals for the full DataFrame.

        Args:
            df: Input OHLCV DataFrame with columns `Open`, `High`, `Low`,
                `Close`, and `Volume`.

        Returns:
            A DataFrame with indicators and signal columns including:
            `Signal`, `Entry_Price`, `Exit_Price`, `Stop_Loss`.
        """
        df = df.copy()

        if df.empty:
            return df

        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        if not required_cols.issubset(set(df.columns)):
            raise ValueError(f"Missing required columns. Need: {required_cols}")

        macd_df = ta.macd(
            df["Close"],
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal=self.macd_signal,
        )

        macd_col = f"MACD_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"
        if macd_df is None or macd_col not in macd_df.columns:
            df["DIFF"] = 0.0
        else:
            df["DIFF"] = macd_df[macd_col].fillna(0.0)

        df["OBV"] = ta.obv(df["Close"], df["Volume"]).fillna(0.0)
        df["OBV_SMA"] = ta.sma(df["OBV"], length=self.obv_ma)

        df["Price_Pivot_Low"] = self._find_pivots(df["Low"], is_low=True)
        df["Price_Pivot_High"] = self._find_pivots(df["High"], is_low=False)

        df = self._build_divergence_signals(df)

        prev_obv = df["OBV"].shift(1)
        prev_obv_sma = df["OBV_SMA"].shift(1)

        df["OBV_Bull_Cross"] = (prev_obv <= prev_obv_sma) & (df["OBV"] > df["OBV_SMA"])
        df["OBV_Bear_Cross"] = (prev_obv >= prev_obv_sma) & (df["OBV"] < df["OBV_SMA"])

        df["Signal"] = 0
        df["Entry_Price"] = 0.0
        df["Exit_Price"] = 0.0
        df["Stop_Loss"] = 0.0

        # Allow OBV cross to confirm divergence within a recent window
        # to avoid requiring both events on the exact same bar.
        window = max(0, int(self.confirmation_window)) + 1
        recent_bull_div = (
            df["Bullish_Divergence"]
            .rolling(window=window, min_periods=1)
            .max()
            .astype(bool)
        )
        recent_bear_div = (
            df["Bearish_Divergence"]
            .rolling(window=window, min_periods=1)
            .max()
            .astype(bool)
        )

        buy_mask = recent_bull_div & df["OBV_Bull_Cross"]
        sell_mask = recent_bear_div & df["OBV_Bear_Cross"]

        df.loc[buy_mask, "Signal"] = 1
        df.loc[buy_mask, "Entry_Price"] = df.loc[buy_mask, "Close"]

        df.loc[sell_mask, "Signal"] = -1
        df.loc[sell_mask, "Exit_Price"] = df.loc[sell_mask, "Close"]

        return df
