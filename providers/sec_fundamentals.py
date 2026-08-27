from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import requests


class SECFundamentalsProvider:
    """Load compact point-in-time annual fundamentals from SEC Company Facts."""

    CACHE_VERSION = 2
    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

    def __init__(
        self,
        cache_dir: str = "data/fundamentals_sec",
        user_agent: str | None = None,
        request_interval_seconds: float = 0.12,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent or os.getenv(
            "SEC_USER_AGENT",
            "fin-personal-quant/1.0 research@example.com",
        )
        self.request_interval_seconds = max(0.0, float(request_interval_seconds))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        return str(ticker).strip().upper().replace(".", "-")

    def _get_json(self, url: str) -> dict:
        response = self.session.get(url, timeout=60)
        response.raise_for_status()
        time.sleep(self.request_interval_seconds)
        return response.json()

    def load_ticker_map(self, refresh: bool = False) -> dict[str, int]:
        cache_path = self.cache_dir / "ticker_cik.json"
        if cache_path.exists() and not refresh:
            with cache_path.open("r", encoding="utf-8") as file:
                return {key: int(value) for key, value in json.load(file).items()}

        payload = self._get_json(self.TICKER_MAP_URL)
        ticker_map = {
            self._normalize_ticker(row["ticker"]): int(row["cik_str"])
            for row in payload.values()
        }
        with cache_path.open("w", encoding="utf-8") as file:
            json.dump(ticker_map, file, indent=2, sort_keys=True)
        return ticker_map

    @staticmethod
    def _fact_rows(payload: dict, taxonomy: str, tag: str, unit: str, is_flow: bool) -> pd.DataFrame:
        records = payload.get("facts", {}).get(taxonomy, {}).get(tag, {}).get("units", {}).get(unit, [])
        rows = pd.DataFrame(records)
        if rows.empty or not {"end", "filed", "val", "form"}.issubset(rows.columns):
            return pd.DataFrame(columns=["Filed", "PeriodEnd", "Value"])

        rows = rows[rows["form"].isin(["10-K", "20-F"])].copy()
        if "fp" in rows.columns:
            rows = rows[rows["fp"].eq("FY")]
        rows["Filed"] = pd.to_datetime(rows["filed"], errors="coerce")
        rows["PeriodEnd"] = pd.to_datetime(rows["end"], errors="coerce")
        rows["Value"] = pd.to_numeric(rows["val"], errors="coerce")
        rows = rows.dropna(subset=["Filed", "PeriodEnd", "Value"])

        if is_flow and "start" in rows.columns:
            rows["PeriodStart"] = pd.to_datetime(rows["start"], errors="coerce")
            duration = (rows["PeriodEnd"] - rows["PeriodStart"]).dt.days
            rows = rows[duration.between(250, 450)]

        rows = rows.sort_values("Filed").drop_duplicates("PeriodEnd", keep="first")
        return rows[["Filed", "PeriodEnd", "Value"]].reset_index(drop=True)

    @classmethod
    def parse_company_facts(cls, payload: dict) -> pd.DataFrame:
        specifications = {
            "OperatingIncome": ("us-gaap", "OperatingIncomeLoss", "USD", True),
            "OperatingCashFlow": (
                "us-gaap",
                "NetCashProvidedByUsedInOperatingActivities",
                "USD",
                True,
            ),
            "Assets": ("us-gaap", "Assets", "USD", False),
            "Liabilities": ("us-gaap", "Liabilities", "USD", False),
            "Shares": ("dei", "EntityCommonStockSharesOutstanding", "shares", False),
        }

        metric_frames = []
        for column, specification in specifications.items():
            rows = cls._fact_rows(payload, *specification).rename(columns={"Value": column})
            if rows.empty:
                metric_frames.append(pd.DataFrame(columns=[column], index=pd.DatetimeIndex([], name="Filed")))
                continue
            rows = rows.sort_values(["Filed", "PeriodEnd"]).drop_duplicates("Filed", keep="last")
            metric_frames.append(rows.set_index("Filed")[[column]])

        events = pd.concat(metric_frames, axis=1).sort_index()
        if events.empty:
            return pd.DataFrame(columns=["Filed", "PeriodEnd", *specifications])

        value_columns = list(specifications)
        events[value_columns] = events[value_columns].apply(pd.to_numeric, errors="coerce").ffill()
        events = events.dropna(subset=["OperatingIncome", "OperatingCashFlow", "Assets", "Liabilities", "Shares"])
        events = events.reset_index()
        events["PeriodEnd"] = events["Filed"]
        return events[["Filed", "PeriodEnd", *value_columns]]

    def load_symbol(self, ticker: str, cik: int, refresh: bool = False) -> pd.DataFrame:
        symbol = self._normalize_ticker(ticker)
        cache_path = self.cache_dir / f"{symbol}.v{self.CACHE_VERSION}.parquet"
        if cache_path.exists() and not refresh:
            return pd.read_parquet(cache_path, engine="fastparquet")

        payload = self._get_json(self.COMPANY_FACTS_URL.format(cik=int(cik)))
        frame = self.parse_company_facts(payload)
        if not frame.empty:
            frame.to_parquet(cache_path, engine="fastparquet", index=False)
        return frame

    def load_universe(self, tickers: list[str], refresh: bool = False) -> dict[str, pd.DataFrame]:
        ticker_map = self.load_ticker_map(refresh=False)
        result: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            symbol = self._normalize_ticker(ticker)
            cik = ticker_map.get(symbol)
            if cik is None:
                continue
            try:
                frame = self.load_symbol(symbol, cik, refresh=refresh)
            except (requests.RequestException, OSError, ValueError) as exc:
                print(f"SEC fundamentals unavailable for {symbol}: {exc}")
                continue
            if not frame.empty:
                result[symbol] = frame
        return result