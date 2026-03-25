from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass
class RegimeThresholds:
    """Decision thresholds for final regime mapping."""

    trend_enter: float = 0.70
    trend_exit: float = 0.50
    range_enter: float = 0.35


class DailyRegimeClassifier:
    """Daily 3-state regime classifier with online hidden-state filtering.

    This classifier estimates state probabilities using:
    - daily feature engineering,
    - rolling state-conditional Gaussian emissions,
    - 3-state transition filtering (Bull / Bear / Range).

    It outputs directional regimes directly:
    - BULL_TREND
    - BEAR_TREND
    - RANGE
    - NO_TRADE (low-confidence uncertainty zone)

    Final labels are stabilized with a light hysteresis + confirmation rule.
    """

    def __init__(
        self,
        train_window: int = 504,
        thresholds: RegimeThresholds | None = None,
        p_bull_to_bull: float = 0.97,
        p_bear_to_bear: float = 0.97,
        p_range_to_range: float = 0.94,
        slow_ema_length: int = 200,
        slow_slope_lookback: int = 84,
        confirm_days: int = 1,
        adaptive_thresholds: bool = False,
        adaptive_threshold_window: int = 252,
        trend_enter_quantile: float = 0.75,
        trend_exit_quantile: float = 0.55,
        range_enter_quantile: float = 0.60,
        trend_enter_floor: float = 0.55,
        trend_exit_floor: float = 0.35,
        range_enter_floor: float = 0.25,
        trend_enter_ceiling: float = 0.85,
        trend_exit_ceiling: float = 0.70,
        range_enter_ceiling: float = 0.60,
    ):
        self.train_window = train_window
        self.thresholds = thresholds or RegimeThresholds()
        self.p_bull_to_bull = p_bull_to_bull
        self.p_bear_to_bear = p_bear_to_bear
        self.p_range_to_range = p_range_to_range
        self.slow_ema_length = slow_ema_length
        self.slow_slope_lookback = slow_slope_lookback
        self.confirm_days = max(1, int(confirm_days))
        self.adaptive_thresholds = adaptive_thresholds
        self.adaptive_threshold_window = max(20, int(adaptive_threshold_window))
        self.trend_enter_quantile = float(trend_enter_quantile)
        self.trend_exit_quantile = float(trend_exit_quantile)
        self.range_enter_quantile = float(range_enter_quantile)
        self.trend_enter_floor = float(trend_enter_floor)
        self.trend_exit_floor = float(trend_exit_floor)
        self.range_enter_floor = float(range_enter_floor)
        self.trend_enter_ceiling = float(trend_enter_ceiling)
        self.trend_exit_ceiling = float(trend_exit_ceiling)
        self.range_enter_ceiling = float(range_enter_ceiling)

    def _build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Build daily features used by the regime model."""
        out = df.copy()

        close = out["Close"]
        high = out["High"]
        low = out["Low"]
        volume = out["Volume"]

        out["r1"] = np.log(close / close.shift(1))
        out["mom5"] = close.pct_change(5)
        out["mom20"] = close.pct_change(20)
        out["vol20"] = out["r1"].rolling(20).std() * np.sqrt(252)
        out["atr14"] = ta.atr(high=high, low=low, close=close, length=14)
        out["atr_norm"] = out["atr14"] / close.replace(0, np.nan)
        out["slow_ema"] = ta.ema(close, length=self.slow_ema_length)
        out["slow_ema_gap"] = close / out["slow_ema"] - 1.0
        out["slow_ema_slope"] = out["slow_ema"].pct_change(self.slow_slope_lookback)

        vol_mean = volume.rolling(20).mean()
        vol_std = volume.rolling(20).std()
        out["vol_z20"] = (volume - vol_mean) / vol_std.replace(0, np.nan)

        # Seed indicator for train-window state bootstrapping.
        adx_df = ta.adx(high=high, low=low, close=close, length=14)
        adx_col = "ADX_14"
        if adx_df is not None and adx_col in adx_df.columns:
            out["adx14"] = adx_df[adx_col]
        else:
            out["adx14"] = np.nan

        return out

    @staticmethod
    def _safe_stats(train: pd.DataFrame, cols: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """Compute robust mean/std with fallback values."""
        means = []
        stds = []
        for col in cols:
            series = train[col].dropna()
            if series.empty:
                means.append(0.0)
                stds.append(1.0)
                continue
            mean = float(series.mean())
            std = float(series.std(ddof=0))
            if not np.isfinite(std) or std < 1e-8:
                std = 1.0
            means.append(mean)
            stds.append(std)
        return np.array(means), np.array(stds)

    @staticmethod
    def _gaussian_logpdf_diag(x: np.ndarray, mean: np.ndarray, std: np.ndarray) -> float:
        """Diagonal-covariance Gaussian log-pdf."""
        var = np.square(std)
        diff = x - mean
        return float(-0.5 * np.sum(np.log(2.0 * math.pi * var) + (diff * diff) / var))

    @staticmethod
    def _posterior_from_loglik(loglik: np.ndarray, prior: np.ndarray) -> np.ndarray:
        """Convert log-likelihoods and priors into normalized posterior probabilities."""
        safe_prior = np.clip(prior, 1e-12, 1.0)
        scores = loglik + np.log(safe_prior)
        scores = scores - np.max(scores)
        probs = np.exp(scores)
        denom = probs.sum()
        if denom <= 0 or not np.isfinite(denom):
            return prior
        return probs / denom

    @staticmethod
    def _rolling_quantile_threshold(
        series: pd.Series,
        window: int,
        quantile: float,
        fallback: float,
        lower: float,
        upper: float,
    ) -> pd.Series:
        """Build a past-only rolling quantile threshold with clipping and fallback."""
        threshold = series.shift(1).rolling(window=window, min_periods=max(20, window // 3)).quantile(quantile)
        threshold = threshold.clip(lower=lower, upper=upper)
        return threshold.fillna(fallback)

    def classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return DataFrame with probability and final regime labels.

        Output columns:
        - P_Bull / P_Bear / P_Range: filtered probabilities in [0, 1]
        - P_Trend: P_Bull + P_Bear
        - Regime_Raw: BULL_TREND / BEAR_TREND / RANGE / NO_TRADE
        - Regime_Final: hysteresis-stabilized regime
        """
        if df.empty:
            return df.copy()

        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        if not required_cols.issubset(df.columns):
            raise ValueError(f"Missing required columns for regime classification: {required_cols}")

        out = self._build_features(df)
        feat_cols = ["r1", "mom5", "mom20", "vol20", "atr_norm", "vol_z20", "slow_ema_gap", "slow_ema_slope"]

        n = len(out)
        p_bull = np.full(n, np.nan)
        p_bear = np.full(n, np.nan)
        p_range = np.full(n, np.nan)
        regime_raw = np.array(["NO_TRADE"] * n, dtype=object)

        prev_probs = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
        transition = np.array([
            [self.p_bull_to_bull, 1.0 - self.p_bull_to_bull - 0.005, 0.005],
            [1.0 - self.p_bear_to_bear - 0.005, self.p_bear_to_bear, 0.005],
            [(1.0 - self.p_range_to_range) / 2.0, (1.0 - self.p_range_to_range) / 2.0, self.p_range_to_range],
        ], dtype=float)

        for i in range(n):
            row = out.iloc[i]
            if i < self.train_window:
                p_bull[i], p_bear[i], p_range[i] = prev_probs
                continue

            train = out.iloc[i - self.train_window : i].copy()
            train = train.dropna(subset=feat_cols)
            if len(train) < max(80, self.train_window // 5):
                p_bull[i], p_bear[i], p_range[i] = prev_probs
                continue

            adx_thr = float(train["adx14"].quantile(0.55)) if train["adx14"].notna().any() else 20.0
            bull_seed = (
                (train["Close"] > train["slow_ema"])
                & (train["slow_ema_slope"] > 0)
                & (train["mom20"] > 0)
                & (train["adx14"].fillna(0.0) >= adx_thr)
            )
            bear_seed = (
                (train["Close"] < train["slow_ema"])
                & (train["slow_ema_slope"] < 0)
                & (train["mom20"] < 0)
                & (train["adx14"].fillna(0.0) >= adx_thr)
            )
            range_seed = ~(bull_seed | bear_seed)

            if bull_seed.sum() < 25:
                bull_seed = (train["Close"] > train["slow_ema"]) & (train["mom20"] > 0)
            if bear_seed.sum() < 25:
                bear_seed = (train["Close"] < train["slow_ema"]) & (train["mom20"] < 0)
            range_seed = ~(bull_seed | bear_seed)

            bull_train = train.loc[bull_seed]
            bear_train = train.loc[bear_seed]
            range_train = train.loc[range_seed]

            mean_bull, std_bull = self._safe_stats(bull_train, feat_cols)
            mean_bear, std_bear = self._safe_stats(bear_train, feat_cols)
            mean_range, std_range = self._safe_stats(range_train, feat_cols)

            x = row[feat_cols].to_numpy(dtype=float)
            if np.isnan(x).any():
                p_bull[i], p_bear[i], p_range[i] = prev_probs
                continue

            loglik = np.array([
                self._gaussian_logpdf_diag(x, mean_bull, std_bull),
                self._gaussian_logpdf_diag(x, mean_bear, std_bear),
                self._gaussian_logpdf_diag(x, mean_range, std_range),
            ])

            prior = prev_probs @ transition
            curr_probs = self._posterior_from_loglik(loglik, prior)
            p_bull[i], p_bear[i], p_range[i] = curr_probs
            prev_probs = curr_probs

        out["P_Bull"] = p_bull
        out["P_Bear"] = p_bear
        out["P_Range"] = p_range
        out["P_Trend"] = out["P_Bull"].fillna(0.0) + out["P_Bear"].fillna(0.0)

        thr = self.thresholds
        if self.adaptive_thresholds:
            out["Trend_Enter_Threshold"] = self._rolling_quantile_threshold(
                out["P_Trend"],
                window=self.adaptive_threshold_window,
                quantile=self.trend_enter_quantile,
                fallback=thr.trend_enter,
                lower=self.trend_enter_floor,
                upper=self.trend_enter_ceiling,
            )
            out["Trend_Exit_Threshold"] = self._rolling_quantile_threshold(
                out["P_Trend"],
                window=self.adaptive_threshold_window,
                quantile=self.trend_exit_quantile,
                fallback=thr.trend_exit,
                lower=self.trend_exit_floor,
                upper=self.trend_exit_ceiling,
            )
            out["Range_Enter_Threshold"] = self._rolling_quantile_threshold(
                out["P_Trend"],
                window=self.adaptive_threshold_window,
                quantile=self.range_enter_quantile,
                fallback=thr.range_enter,
                lower=self.range_enter_floor,
                upper=self.range_enter_ceiling,
            )
        else:
            out["Trend_Enter_Threshold"] = thr.trend_enter
            out["Trend_Exit_Threshold"] = thr.trend_exit
            out["Range_Enter_Threshold"] = thr.range_enter

        bull_raw_mask = (out["P_Trend"] >= out["Trend_Enter_Threshold"]) & (out["P_Bull"] >= out[["P_Bear", "P_Range"]].max(axis=1))
        bear_raw_mask = (out["P_Trend"] >= out["Trend_Enter_Threshold"]) & (out["P_Bear"] >= out[["P_Bull", "P_Range"]].max(axis=1))
        range_raw_mask = (out["P_Trend"] <= out["Range_Enter_Threshold"]) & (out["P_Range"] >= out[["P_Bull", "P_Bear"]].max(axis=1))

        out.loc[range_raw_mask, "Regime_Raw"] = "RANGE"
        out.loc[bull_raw_mask, "Regime_Raw"] = "BULL_TREND"
        out.loc[bear_raw_mask, "Regime_Raw"] = "BEAR_TREND"

        # Hysteresis-stabilized final regime.
        final = []
        current = "NO_TRADE"
        streak = 0
        for _, row in out.iterrows():
            p_bull_curr = row.get("P_Bull", np.nan)
            p_bear_curr = row.get("P_Bear", np.nan)
            raw = row.get("Regime_Raw", "NO_TRADE")
            trend_exit_threshold = float(row.get("Trend_Exit_Threshold", thr.trend_exit))

            if not np.isfinite(p_bull_curr) or not np.isfinite(p_bear_curr):
                final.append(current)
                continue

            desired = raw
            p_trend_curr = float(row.get("P_Trend", np.nan))

            if current == "BULL_TREND" and p_trend_curr < trend_exit_threshold:
                desired = "NO_TRADE"
            elif current == "BEAR_TREND" and p_trend_curr < trend_exit_threshold:
                desired = "NO_TRADE"
            elif current in ("BULL_TREND", "BEAR_TREND") and raw == "NO_TRADE":
                desired = current

            if desired == current:
                streak = 0
                final.append(current)
                continue

            streak += 1
            if streak >= self.confirm_days:
                current = desired
                streak = 0

            final.append(current)

        out["Regime_Final"] = final
        return out
