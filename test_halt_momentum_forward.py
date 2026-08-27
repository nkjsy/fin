from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from main_halt_momentum_forward import run_forward_test, summarize_day


class FakeProvider:
    def get_history(self, symbol, interval, period="2d"):
        times = list(pd.date_range("2026-08-25 10:00", periods=5, freq="1min"))
        times.extend(pd.date_range("2026-08-25 10:10", periods=6, freq="1min"))
        return pd.DataFrame(
            {
                "Datetime": times,
                "Open": [100, 102, 104, 106, 108, 110, 110, 110, 110, 110, 110],
                "High": [103, 105, 107, 109, 111, 111, 111, 111, 111, 111, 111],
                "Low": [100] * 11,
                "Close": [102, 104, 106, 108, 110, 110, 110, 110, 110, 110, 110],
                "Volume": [1000] * 11,
            }
        )


def test_daily_forward_run_writes_summary_and_resumes():
    with TemporaryDirectory() as temp_dir:
        output = Path(temp_dir)
        summary, trades, _ = run_forward_test(
            FakeProvider(), date(2026, 8, 25), ["TEST"], output, progress_every=1
        )
        assert len(trades) == 1
        assert trades.iloc[0]["ExitReason"] == "TIME"
        assert int(summary.iloc[0]["SymbolsCompleted"]) == 1
        assert (output / "summary.txt").exists()

        second_summary, second_trades, _ = run_forward_test(
            FakeProvider(), date(2026, 8, 25), ["TEST"], output, progress_every=1
        )
        assert len(second_trades) == 1
        assert int(second_summary.iloc[0]["SymbolsCompleted"]) == 1


def test_strict_summary_excludes_delayed_time_fill():
    trades = pd.DataFrame(
        {
            "ReturnPct": [5.0, 20.0],
            "ExitReason": ["TIME", "TIME_NEXT_TRADE"],
        }
    )
    progress = pd.DataFrame({"Symbol": ["A", "B"], "Status": ["OK", "OK"]})
    summary = summarize_day(trades, 2, progress).iloc[0]
    assert summary["Events"] == 2
    assert summary["StrictTrades"] == 1
    assert summary["StrictAverageReturnPct"] == 5.0


if __name__ == "__main__":
    test_daily_forward_run_writes_summary_and_resumes()
    test_strict_summary_excludes_delayed_time_fill()
    print("Halt momentum forward tests passed.")