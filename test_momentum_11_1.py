import pandas as pd

from portfolio_backtester import PortfolioBacktester
from strategy.momentum_11_1 import Momentum11_1Strategy


def make_symbol_df(dates: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1_000_000] * len(dates),
        }
    )


def test_compute_momentum_scores_respects_skip_window():
    dates = pd.date_range("2024-01-01", periods=320, freq="B")
    closes = [100.0 + i for i in range(len(dates))]
    strategy = Momentum11_1Strategy(lookback_days=231, skip_days=21, top_n=2)
    close_matrix = pd.DataFrame({"AAA": closes}, index=dates)

    scores = strategy.compute_momentum_scores(close_matrix)
    last_date = dates[-1]
    expected = close_matrix.loc[dates[-22], "AAA"] / close_matrix.loc[dates[-253], "AAA"] - 1.0
    actual = scores.loc[last_date, "AAA"]
    assert abs(actual - expected) < 1e-12


def test_get_rebalance_dates_uses_last_trading_day_of_month():
    dates = pd.date_range("2024-01-01", periods=90, freq="B")
    close_matrix = pd.DataFrame({"AAA": range(len(dates))}, index=dates)
    strategy = Momentum11_1Strategy(top_n=1)

    rebalances = strategy.get_rebalance_dates(close_matrix)
    expected = pd.Series(dates, index=dates).groupby(dates.to_period("M")).max().tolist()
    assert list(rebalances) == expected


def test_portfolio_backtester_smoke():
    dates = pd.date_range("2024-01-01", periods=320, freq="B")
    aaa = [100.0 + i * 0.40 for i in range(len(dates))]
    bbb = [100.0 + i * 0.20 for i in range(len(dates))]
    ccc = [100.0 + i * 0.60 for i in range(len(dates))]

    price_data = {
        "AAA": make_symbol_df(dates, aaa),
        "BBB": make_symbol_df(dates, bbb),
        "CCC": make_symbol_df(dates, ccc),
    }

    strategy = Momentum11_1Strategy(lookback_days=60, skip_days=21, top_n=2)
    backtester = PortfolioBacktester(initial_capital=10000.0)
    result = backtester.run(price_data, strategy)

    assert not result.equity_curve.empty
    assert "Equity" in result.equity_curve.columns
    assert result.summary["Rebalances"] > 0
    assert result.summary["Trades"] > 0
    if not result.selection_history.empty:
        rank_cols = [col for col in result.selection_history.columns if col.startswith("Rank_")]
        assert len(rank_cols) <= 2


def test_portfolio_backtester_respects_historical_universe_filter():
    dates = pd.date_range("2024-01-01", periods=320, freq="B")
    aaa = [100.0 + i * 0.30 for i in range(len(dates))]
    bbb = [100.0 + i * 0.50 for i in range(len(dates))]

    price_data = {
        "AAA": make_symbol_df(dates, aaa),
        "BBB": make_symbol_df(dates, bbb),
    }

    strategy = Momentum11_1Strategy(lookback_days=60, skip_days=21, top_n=1)
    backtester = PortfolioBacktester(initial_capital=10000.0)
    eligible_map = {
        pd.Timestamp("2024-12-31"): ["AAA"],
        pd.Timestamp("2025-01-31"): ["AAA"],
        pd.Timestamp("2025-02-28"): ["AAA"],
        pd.Timestamp("2025-03-31"): ["AAA"],
    }

    result = backtester.run(price_data, strategy, eligible_universe_by_date=eligible_map)

    if not result.selection_history.empty:
        chosen = result.selection_history.filter(regex=r"^Rank_").stack().dropna().unique().tolist()
        assert chosen == ["AAA"]


def test_build_buy_and_hold_curve_alignment():
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    close = pd.Series([100.0, 101.0, 103.0, 102.0, 104.0], index=dates)
    curve = PortfolioBacktester.build_buy_and_hold_curve(close, initial_capital=10000.0, target_index=dates)

    assert curve.index.equals(dates)
    assert abs(curve.iloc[0] - 10000.0) < 1e-12
    assert abs(curve.iloc[-1] - 10400.0) < 1e-12


if __name__ == "__main__":
    test_compute_momentum_scores_respects_skip_window()
    test_get_rebalance_dates_uses_last_trading_day_of_month()
    test_portfolio_backtester_smoke()
    test_portfolio_backtester_respects_historical_universe_filter()
    test_build_buy_and_hold_curve_alignment()
    print("Momentum 11-1 tests passed.")
