import math

import pandas as pd

from halt_momentum_backtester import HaltMomentumBacktester, OhlcvHaltDetector


def _bars(highs, lows, closes=None):
    times = pd.date_range("2026-08-20 10:00", periods=len(highs), freq="1min")
    return pd.DataFrame(
        {
            "Symbol": "TEST",
            "Datetime": times,
            "Open": [100.0] * len(highs),
            "High": highs,
            "Low": lows,
            "Close": closes or [100.0] * len(highs),
        }
    )


def _event(reason="LUDP", direction="UP", resume_time="2026-08-20 10:00"):
    return pd.DataFrame(
        {
            "Symbol": ["TEST"],
            "ResumeTime": [resume_time],
            "ReasonCode": [reason],
            "Direction": [direction],
        }
    )


def test_target_exits_at_ten_percent():
    trade = HaltMomentumBacktester().run(
        _bars([105, 111], [98, 99]), _event()
    ).iloc[0]
    assert trade["ExitReason"] == "TARGET"
    assert math.isclose(trade["ExitPrice"], 110.0)
    assert math.isclose(trade["ReturnPct"], 10.0)


def test_timeout_uses_open_after_five_minutes():
    bars = _bars([105] * 6, [95] * 6)
    bars.loc[5, "Open"] = 103.0
    trade = HaltMomentumBacktester().run(bars, _event()).iloc[0]
    assert trade["ExitReason"] == "TIME"
    assert trade["ExitPrice"] == 103.0
    assert trade["HoldMinutes"] == 5.0


def test_stop_exits_at_ten_percent():
    trade = HaltMomentumBacktester().run(
        _bars([102, 103], [95, 89]), _event()
    ).iloc[0]
    assert trade["ExitReason"] == "STOP"
    assert trade["ExitPrice"] == 90.0
    assert math.isclose(trade["ReturnPct"], -10.0)


def test_same_bar_target_and_stop_is_conservative():
    trade = HaltMomentumBacktester().run(
        _bars([111], [89]), _event()
    ).iloc[0]
    assert trade["ExitReason"] == "STOP"
    assert math.isclose(trade["ReturnPct"], -10.0)


def test_subminute_resume_uses_containing_minute_bar():
    trade = HaltMomentumBacktester().run(
        _bars([111], [99]), _event(resume_time="2026-08-20 10:00:30")
    ).iloc[0]
    assert trade["EntryTime"] == pd.Timestamp("2026-08-20 10:00")


def test_non_upward_ludp_event_is_ignored():
    assert HaltMomentumBacktester().run(
        _bars([111], [99]), _event(direction="DOWN")
    ).empty
    assert HaltMomentumBacktester().run(
        _bars([111], [99]), _event(reason="T3")
    ).empty


def test_ohlcv_gap_infers_upward_halt_and_runs_trade():
    bars = _bars([103, 105, 107, 109, 111, 113], [99] * 6)
    bars["Open"] = [100, 102, 104, 106, 108, 110]
    bars["Close"] = [102, 104, 106, 108, 110, 112]
    bars.loc[5, "Datetime"] = pd.Timestamp("2026-08-20 10:10")

    events = OhlcvHaltDetector().detect(bars)
    assert len(events) == 1
    assert events.iloc[0]["MissingMinutes"] == 5
    assert events.iloc[0]["ReasonCode"] == "OHLCV_GAP_PROXY"
    trade = HaltMomentumBacktester().run(bars, events).iloc[0]
    assert trade["EntryTime"] == pd.Timestamp("2026-08-20 10:10")


def test_gap_without_runup_is_not_an_upward_halt():
    bars = _bars([101] * 2, [99] * 2)
    bars.loc[1, "Datetime"] = pd.Timestamp("2026-08-20 10:06")
    assert OhlcvHaltDetector().detect(bars).empty


def test_sparse_pre_gap_bars_are_not_a_halt():
    bars = _bars([101, 120, 121], [99] * 3)
    bars["Close"] = [100, 120, 121]
    bars.loc[1, "Datetime"] = pd.Timestamp("2026-08-20 10:03")
    bars.loc[2, "Datetime"] = pd.Timestamp("2026-08-20 10:09")
    assert OhlcvHaltDetector().detect(bars).empty


def test_resumption_price_must_be_above_two_dollars():
    bars = _bars([1.9, 2.0, 2.1, 2.2, 2.3, 2.4], [1.7] * 6)
    bars["Open"] = [1.8, 1.9, 2.0, 2.1, 2.2, 2.0]
    bars["Close"] = [1.9, 2.0, 2.1, 2.2, 2.3, 2.4]
    bars.loc[5, "Datetime"] = pd.Timestamp("2026-08-20 10:10")
    assert OhlcvHaltDetector().detect(bars).empty

    bars.loc[5, "Open"] = 2.01
    assert len(OhlcvHaltDetector().detect(bars)) == 1


if __name__ == "__main__":
    test_target_exits_at_ten_percent()
    test_timeout_uses_open_after_five_minutes()
    test_stop_exits_at_ten_percent()
    test_same_bar_target_and_stop_is_conservative()
    test_subminute_resume_uses_containing_minute_bar()
    test_non_upward_ludp_event_is_ignored()
    test_ohlcv_gap_infers_upward_halt_and_runs_trade()
    test_gap_without_runup_is_not_an_upward_halt()
    test_sparse_pre_gap_bars_are_not_a_halt()
    test_resumption_price_must_be_above_two_dollars()
    print("Halt momentum tests passed.")