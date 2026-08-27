from __future__ import annotations

from pathlib import Path

import pandas as pd


class AlternativeDataProvider:
    """Load locally supplied point-in-time sentiment and option histories."""

    REQUIRED_COLUMNS = {
        "sentiment": {"Date", "Buzz"},
        "options": {"Date", "CallIV90", "PutIV90", "PutCallOIRatio270"},
    }

    def __init__(self, data_dir: str = "data/alternative"):
        self.data_dir = Path(data_dir)

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        return str(ticker).strip().upper().replace(".", "-")

    def load_symbol(self, ticker: str, kind: str) -> pd.DataFrame:
        if kind not in self.REQUIRED_COLUMNS:
            raise ValueError(f"Unsupported alternative data kind: {kind}")

        symbol = self._normalize_ticker(ticker)
        base_path = self.data_dir / kind / symbol
        parquet_path = base_path.with_suffix(".parquet")
        csv_path = base_path.with_suffix(".csv")
        if parquet_path.exists():
            frame = pd.read_parquet(parquet_path, engine="fastparquet")
        elif csv_path.exists():
            frame = pd.read_csv(csv_path)
        else:
            return pd.DataFrame()

        missing = self.REQUIRED_COLUMNS[kind].difference(frame.columns)
        if missing:
            raise ValueError(f"{symbol} {kind} data missing columns: {sorted(missing)}")

        frame = frame.copy()
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dt.tz_localize(None)
        value_columns = sorted(self.REQUIRED_COLUMNS[kind].difference({"Date"}))
        frame[value_columns] = frame[value_columns].apply(pd.to_numeric, errors="coerce")
        return frame.dropna(subset=["Date"]).sort_values("Date").drop_duplicates("Date", keep="last")

    def load_universe(self, tickers: list[str], kind: str) -> dict[str, pd.DataFrame]:
        result = {}
        for ticker in tickers:
            frame = self.load_symbol(ticker, kind)
            if not frame.empty:
                result[self._normalize_ticker(ticker)] = frame
        return result