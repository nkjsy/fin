from datetime import date

import pandas as pd

from safe_haven_backtester import SafeHavenBacktester, black_scholes_put
from strategy.safe_haven import PortfolioState, PutPosition, build_daily_plan


def test_quarterly_put_purchase_respects_budget_and_strike_range():
    state = PortfolioState(85000, 12000, 3000, year_start_equity=100000, premium_year=2026)
    plan = build_daily_plan(state, 600, 610, date(2026, 7, 21))

    actions = " ".join(item.action for item in plan.advice)
    assert "最多 $750.00" in actions
    assert "$390-$450" in actions


def test_drawdown_crossing_generates_each_uncompleted_stage():
    state = PortfolioState(70000, 20000, 5000, puts=[PutPosition(date(2027, 6, 1), 5000)])
    plan = build_daily_plan(state, 70, 100, date(2026, 7, 21))

    actions = [item.action for item in plan.advice]
    assert any("25%" in action for action in actions)
    assert any("35%" in action for action in actions)
    assert not any("剩余盈利Put" in action for action in actions)


def test_completed_drawdown_stage_is_not_repeated():
    state = PortfolioState(70000, 20000, 5000, completed_drawdown_stage=2)
    plan = build_daily_plan(state, 70, 100, date(2026, 7, 21))

    assert not any(item.priority == 1 for item in plan.advice)


def test_low_spy_weight_rebalances_to_target_with_available_defensive_assets():
    state = PortfolioState(70000, 25000, 5000, last_put_purchase=date(2026, 7, 1))
    plan = build_daily_plan(state, 100, 110, date(2026, 7, 21))

    assert any("$15,000.00" in item.action and "SPY" in item.action for item in plan.advice)


def test_put_with_six_months_remaining_is_flagged_for_roll():
    state = PortfolioState(85000, 12000, 2000, puts=[PutPosition(date(2026, 12, 1), 1000)], last_put_purchase=date(2026, 7, 1))
    plan = build_daily_plan(state, 100, 105, date(2026, 7, 21))

    assert any("2026-12-01" in item.action for item in plan.advice)


def test_cash_only_portfolio_gets_complete_spy_sgov_and_put_plan():
    state = PortfolioState(0, 0, 52000, year_start_equity=52000, premium_year=2026)
    plan = build_daily_plan(state, 742.09, 757.62, date(2026, 7, 21))

    actions = " ".join(item.action for item in plan.advice)
    assert "$44,200.00" in actions
    assert "$6,240.00" in actions
    assert "$390.00" in actions


def test_black_scholes_put_respects_intrinsic_value_at_expiry():
    assert black_scholes_put(70, 80, 0, 0.03, 0.25) == 10


def test_modeled_put_strategy_monetizes_a_synthetic_crash():
    dates = pd.date_range("2020-01-01", periods=320, freq="B")
    closes = [100.0] * 80 + [100.0 - index * 1.0 for index in range(40)] + [61.0 + index * 0.35 for index in range(200)]
    vix = [20.0] * 80 + [20.0 + index * 1.5 for index in range(40)] + [30.0] * 200
    data = pd.DataFrame({"Close": closes, "VIX": vix, "Rate": 2.0}, index=dates)

    result = SafeHavenBacktester().run(data, "TEST")

    assert result.summary["Crisis Sales"] >= 3
    assert result.summary["Max Drawdown %"] > result.summary["Buy Hold Max Drawdown %"]


def test_97_percent_exposure_never_spends_more_cash_than_available():
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    data = pd.DataFrame({"Close": [100.0] * 300, "VIX": 20.0, "Rate": 2.0}, index=dates)

    result = SafeHavenBacktester(target_weight=0.97, lower_band=0.94, upper_band=0.99).run(data, "TEST")

    assert result.equity_curve["Defensive"].min() >= -1e-9
    assert result.summary["Total Premium"] <= 3000.0 + 1e-9


if __name__ == "__main__":
    test_quarterly_put_purchase_respects_budget_and_strike_range()
    test_drawdown_crossing_generates_each_uncompleted_stage()
    test_completed_drawdown_stage_is_not_repeated()
    test_low_spy_weight_rebalances_to_target_with_available_defensive_assets()
    test_put_with_six_months_remaining_is_flagged_for_roll()
    test_cash_only_portfolio_gets_complete_spy_sgov_and_put_plan()
    test_black_scholes_put_respects_intrinsic_value_at_expiry()
    test_modeled_put_strategy_monetizes_a_synthetic_crash()
    test_97_percent_exposure_never_spends_more_cash_than_available()
    print("Safe haven tests passed.")