from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class HaltMomentumConfig:
    take_profit_pct: float = 0.10
    stop_loss_pct: float = 0.10
    max_hold_minutes: int = 5


@dataclass(frozen=True)
class HaltInferenceConfig:
    min_missing_minutes: int = 5
    runup_lookback_minutes: int = 5
    min_runup_pct: float = 0.10
    min_price: float = 2.0


class OhlcvHaltDetector:
    """Infer possible upward halts from missing regular-session minute bars."""

    def __init__(self, config: HaltInferenceConfig | None = None):
        self.config = config or HaltInferenceConfig()

    def detect(self, bars: pd.DataFrame) -> pd.DataFrame:
        required = {"Symbol", "Datetime", "Open", "Close"}
        if missing := required.difference(bars.columns):
            raise ValueError(f"bars missing columns: {sorted(missing)}")

        market_bars = bars.copy()
        market_bars["Datetime"] = pd.to_datetime(market_bars["Datetime"])
        market_bars = market_bars.sort_values(["Symbol", "Datetime"])
        events = []

        for symbol, symbol_bars in market_bars.groupby("Symbol"):
            regular = symbol_bars.set_index("Datetime").between_time("09:30", "16:00").reset_index()
            for _, day_bars in regular.groupby(regular["Datetime"].dt.date):
                day_bars = day_bars.reset_index(drop=True)
                for index in range(1, len(day_bars)):
                    previous = day_bars.iloc[index - 1]
                    resumed = day_bars.iloc[index]
                    gap_minutes = (resumed["Datetime"] - previous["Datetime"]).total_seconds() / 60
                    missing_minutes = int(gap_minutes - 1)
                    if missing_minutes < self.config.min_missing_minutes:
                        continue

                    lookback_start = previous["Datetime"] - pd.Timedelta(
                        minutes=self.config.runup_lookback_minutes - 1
                    )
                    runup_bars = day_bars[
                        (day_bars["Datetime"] >= lookback_start)
                        & (day_bars["Datetime"] <= previous["Datetime"])
                    ]
                    is_continuous = (
                        len(runup_bars) == self.config.runup_lookback_minutes
                        and runup_bars["Datetime"].diff().dropna().eq(pd.Timedelta(minutes=1)).all()
                    )
                    if not is_continuous:
                        continue
                    baseline = float(runup_bars.iloc[0]["Open"])
                    pre_halt_price = float(previous["Close"])
                    runup_pct = pre_halt_price / baseline - 1
                    if runup_pct < self.config.min_runup_pct:
                        continue
                    if float(resumed["Open"]) <= self.config.min_price:
                        continue

                    events.append(
                        {
                            "Symbol": symbol,
                            "ResumeTime": resumed["Datetime"],
                            "ReasonCode": "OHLCV_GAP_PROXY",
                            "Direction": "UP",
                            "DetectionSource": "SCHWAB_1M_OHLCV",
                            "MissingMinutes": missing_minutes,
                            "PreHaltRunupPct": runup_pct * 100,
                        }
                    )

        return pd.DataFrame(events)


class HaltMomentumBacktester:
    """Backtest independent trades entered on the first bar after an up-halt."""

    def __init__(self, config: HaltMomentumConfig | None = None):
        self.config = config or HaltMomentumConfig()

    def run(self, bars: pd.DataFrame, resumptions: pd.DataFrame) -> pd.DataFrame:
        required_bars = {"Symbol", "Datetime", "Open", "High", "Low", "Close"}
        required_events = {"Symbol", "ResumeTime", "ReasonCode", "Direction"}
        if missing := required_bars.difference(bars.columns):
            raise ValueError(f"bars missing columns: {sorted(missing)}")
        if missing := required_events.difference(resumptions.columns):
            raise ValueError(f"resumptions missing columns: {sorted(missing)}")

        market_bars = bars.copy()
        events = resumptions.copy()
        market_bars["Datetime"] = pd.to_datetime(market_bars["Datetime"])
        events["ResumeTime"] = pd.to_datetime(events["ResumeTime"])
        supported_reasons = {"LUDP", "OHLCV_GAP_PROXY"}
        events = events[
            events["ReasonCode"].isin(supported_reasons) & events["Direction"].eq("UP")
        ]
        market_bars = market_bars.sort_values(["Symbol", "Datetime"])

        trades = []
        for event in events.sort_values("ResumeTime").itertuples(index=False):
            resume_bar_time = event.ResumeTime.floor("min")
            symbol_bars = market_bars[
                (market_bars["Symbol"] == event.Symbol)
                & (market_bars["Datetime"] >= resume_bar_time)
            ]
            if symbol_bars.empty:
                continue

            entry_bar = symbol_bars.iloc[0]
            entry_time = entry_bar["Datetime"]
            entry_price = float(entry_bar["Open"])
            deadline = entry_time + pd.Timedelta(minutes=self.config.max_hold_minutes)
            target_price = entry_price * (1 + self.config.take_profit_pct)
            stop_price = entry_price * (1 - self.config.stop_loss_pct)

            exit_time = None
            exit_price = None
            exit_reason = None
            for bar in symbol_bars.itertuples(index=False):
                if bar.Datetime >= deadline:
                    exit_time = bar.Datetime
                    exit_price = float(bar.Open)
                    exit_reason = "TIME" if bar.Datetime == deadline else "TIME_NEXT_TRADE"
                    break

                hit_stop = float(bar.Low) <= stop_price
                hit_target = float(bar.High) >= target_price
                if hit_stop:
                    exit_time = bar.Datetime
                    exit_price = stop_price
                    exit_reason = "STOP"
                    break
                if hit_target:
                    exit_time = bar.Datetime
                    exit_price = target_price
                    exit_reason = "TARGET"
                    break

            if exit_price is None:
                final_bar = symbol_bars.iloc[-1]
                exit_time = final_bar["Datetime"]
                exit_price = float(final_bar["Close"])
                exit_reason = "END_OF_DATA"

            trades.append(
                {
                    "Symbol": event.Symbol,
                    "ResumeTime": event.ResumeTime,
                    "EntryTime": entry_time,
                    "EntryPrice": entry_price,
                    "ExitTime": exit_time,
                    "ExitPrice": exit_price,
                    "ExitReason": exit_reason,
                    "ReturnPct": (exit_price / entry_price - 1) * 100,
                    "HoldMinutes": (exit_time - entry_time).total_seconds() / 60,
                }
            )

        return pd.DataFrame(trades)