from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Momentum11_1Config:
    lookback_days: int = 231
    skip_days: int = 21
    top_n: int = 10
    rebalance_frequency: str = "M"


class Momentum11_1Strategy:
    """Cross-sectional 11-1 momentum helper for portfolio construction."""

    def __init__(
        self,
        lookback_days: int = 231,
        skip_days: int = 21,
        top_n: int = 10,
        rebalance_frequency: str = "M",
    ):
        self.config = Momentum11_1Config(
            lookback_days=max(1, int(lookback_days)),
            skip_days=max(0, int(skip_days)),
            top_n=max(1, int(top_n)),
            rebalance_frequency=rebalance_frequency,
        )

    @staticmethod
    def normalize_tickers(tickers: list[str]) -> list[str]:
        """Normalize external ticker symbols into yfinance-compatible form."""
        normalized = []
        seen = set()
        for ticker in tickers:
            if not ticker:
                continue
            symbol = str(ticker).strip().upper().replace(".", "-")
            if symbol and symbol not in seen:
                normalized.append(symbol)
                seen.add(symbol)
        return normalized

    @staticmethod
    def _extract_close_series(df: pd.DataFrame) -> pd.Series:
        if df.empty or "Close" not in df.columns:
            return pd.Series(dtype=float)

        date_col = None
        if "Date" in df.columns:
            date_col = "Date"
        elif "Datetime" in df.columns:
            date_col = "Datetime"

        if date_col is not None:
            series = pd.Series(df["Close"].to_numpy(), index=pd.to_datetime(df[date_col], errors="coerce"))
        else:
            series = pd.Series(df["Close"].to_numpy(), index=pd.to_datetime(df.index, errors="coerce"))

        series = pd.to_numeric(series, errors="coerce")
        series = series[~series.index.isna()]
        series = series[~series.index.duplicated(keep="last")]
        return series.sort_index()

    def build_close_matrix(self, price_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Build a date x symbol close matrix from downloaded OHLCV data."""
        close_map: dict[str, pd.Series] = {}
        for symbol, df in price_data.items():
            close_series = self._extract_close_series(df)
            if not close_series.empty:
                close_map[symbol] = close_series

        if not close_map:
            return pd.DataFrame(dtype=float)

        close_matrix = pd.DataFrame(close_map).sort_index()
        close_matrix = close_matrix[~close_matrix.index.duplicated(keep="last")]
        return close_matrix

    def compute_momentum_scores(self, close_matrix: pd.DataFrame) -> pd.DataFrame:
        """Compute 11-1 momentum scores for all symbols across all dates."""
        if close_matrix.empty:
            return pd.DataFrame(dtype=float)

        delayed_close = close_matrix.shift(self.config.skip_days)
        base_close = close_matrix.shift(self.config.skip_days + self.config.lookback_days)
        scores = delayed_close / base_close - 1.0
        return scores.replace([pd.NA, pd.NaT], float("nan"))

    def get_rebalance_dates(self, close_matrix: pd.DataFrame) -> pd.DatetimeIndex:
        """Use the last available trading day of each month as rebalance date."""
        if close_matrix.empty:
            return pd.DatetimeIndex([])

        dates = pd.DatetimeIndex(close_matrix.index).sort_values()
        if self.config.rebalance_frequency.upper() != "M":
            return dates

        naive_dates = dates.tz_localize(None) if dates.tz is not None else dates
        grouped = pd.Series(dates, index=naive_dates).groupby(naive_dates.to_period("M")).max()
        return pd.DatetimeIndex(grouped.to_list())

    def select_portfolio(
        self,
        close_matrix: pd.DataFrame,
        eligible_universe_by_date: dict[pd.Timestamp, list[str] | set[str]] | None = None,
    ) -> tuple[pd.DataFrame, dict[pd.Timestamp, list[str]]]:
        """Return full score matrix and selected symbols on each rebalance date."""
        scores = self.compute_momentum_scores(close_matrix)
        rebalance_dates = self.get_rebalance_dates(close_matrix)
        selections: dict[pd.Timestamp, list[str]] = {}

        for date in rebalance_dates:
            if date not in scores.index:
                continue

            row = scores.loc[date].dropna().sort_values(ascending=False)
            if eligible_universe_by_date is not None:
                ts_date = pd.Timestamp(date)
                naive_date = ts_date.tz_localize(None) if ts_date.tz is not None else ts_date
                month_end = naive_date.to_period("M").to_timestamp("M")

                eligible = eligible_universe_by_date.get(ts_date)
                if eligible is None:
                    eligible = eligible_universe_by_date.get(naive_date)
                if eligible is None:
                    eligible = eligible_universe_by_date.get(month_end)
                if eligible is None and ts_date.tz is not None:
                    eligible = eligible_universe_by_date.get(month_end.tz_localize(ts_date.tz))

                if eligible is None:
                    row = row.iloc[0:0]
                else:
                    eligible_set = set(self.normalize_tickers(list(eligible)))
                    row = row[row.index.isin(eligible_set)]

            if row.empty:
                selections[pd.Timestamp(date)] = []
                continue

            selections[pd.Timestamp(date)] = row.head(self.config.top_n).index.to_list()

        return scores, selections
