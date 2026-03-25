import numpy as np
import pandas as pd


def compute_performance_stats(
    equity_curve: pd.Series,
    initial_capital: float,
    exposure_pct: float,
    bars_per_year: int = 252,
) -> dict:
    if equity_curve.empty:
        return {
            "CAGR %": 0.0,
            "Max Drawdown %": 0.0,
            "Sharpe": 0.0,
            "Calmar": 0.0,
            "Exposure %": exposure_pct,
        }

    equity = equity_curve.astype(float)
    years = len(equity) / bars_per_year if len(equity) > 0 else 0.0
    if years > 0 and equity.iloc[-1] > 0 and initial_capital > 0:
        cagr = (equity.iloc[-1] / initial_capital) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

    daily_returns = equity.pct_change().dropna()
    if not daily_returns.empty and daily_returns.std(ddof=0) > 0:
        sharpe = float((daily_returns.mean() / daily_returns.std(ddof=0)) * np.sqrt(bars_per_year))
    else:
        sharpe = 0.0

    if max_drawdown < 0:
        calmar = float(cagr / abs(max_drawdown))
    else:
        calmar = 0.0

    return {
        "CAGR %": cagr * 100.0,
        "Max Drawdown %": max_drawdown * 100.0,
        "Sharpe": sharpe,
        "Calmar": calmar,
        "Exposure %": exposure_pct,
    }
