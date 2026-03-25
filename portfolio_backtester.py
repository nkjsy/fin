from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy.momentum_11_1 import Momentum11_1Strategy


@dataclass
class PortfolioBacktestResult:
    equity_curve: pd.DataFrame
    rebalance_log: pd.DataFrame
    holdings_history: pd.DataFrame
    selection_history: pd.DataFrame
    score_matrix: pd.DataFrame
    summary: dict


class PortfolioBacktester:
    """Multi-asset close-to-close portfolio backtester for monthly momentum rebalances."""

    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = float(initial_capital)

    @staticmethod
    def build_buy_and_hold_curve(
        close_series: pd.Series,
        initial_capital: float,
        target_index: pd.Index | None = None,
    ) -> pd.Series:
        """Build a normalized buy-and-hold equity curve from close prices."""
        if close_series.empty:
            return pd.Series(dtype=float)

        series = pd.to_numeric(close_series, errors="coerce").dropna().sort_index()
        if series.empty:
            return pd.Series(dtype=float)

        if target_index is not None:
            series = series.reindex(target_index).ffill().dropna()
        if series.empty:
            return pd.Series(dtype=float)

        base_price = float(series.iloc[0])
        if base_price <= 0:
            return pd.Series([initial_capital] * len(series), index=series.index, dtype=float)

        return initial_capital * (series / base_price)

    def run(
        self,
        price_data: dict[str, pd.DataFrame],
        strategy: Momentum11_1Strategy,
        eligible_universe_by_date: dict[pd.Timestamp, list[str] | set[str]] | None = None,
    ) -> PortfolioBacktestResult:
        close_matrix = strategy.build_close_matrix(price_data)
        if close_matrix.empty:
            empty = pd.DataFrame()
            return PortfolioBacktestResult(
                equity_curve=empty,
                rebalance_log=empty,
                holdings_history=empty,
                selection_history=empty,
                score_matrix=empty,
                summary={
                    "Final-Equity": self.initial_capital,
                    "Return %": 0.0,
                    "Trades": 0,
                    "Rebalances": 0,
                    "Average Turnover %": 0.0,
                },
            )

        close_matrix = close_matrix.sort_index().ffill()
        score_matrix, selections = strategy.select_portfolio(
            close_matrix,
            eligible_universe_by_date=eligible_universe_by_date,
        )

        cash = self.initial_capital
        holdings: dict[str, float] = {}
        equity_rows = []
        rebalance_rows = []
        holdings_rows = []
        selection_rows = []
        turnover_values = []

        rebalance_dates = set(selections.keys())

        for date, prices_row in close_matrix.iterrows():
            prices = prices_row.dropna()
            if date in rebalance_dates:
                selected_symbols = [symbol for symbol in selections.get(date, []) if symbol in prices.index and prices[symbol] > 0]
                marked_equity = cash + sum(shares * float(prices[symbol]) for symbol, shares in holdings.items() if symbol in prices.index)

                target_weight = 1.0 / len(selected_symbols) if selected_symbols else 0.0
                current_symbols = set(holdings.keys())
                future_symbols = set(selected_symbols)
                trade_value_total = 0.0

                all_symbols = sorted(current_symbols.union(future_symbols))
                new_holdings: dict[str, float] = {}

                for symbol in all_symbols:
                    if symbol not in prices.index:
                        continue

                    price = float(prices[symbol])
                    if price <= 0:
                        continue

                    old_shares = float(holdings.get(symbol, 0.0))
                    old_value = old_shares * price
                    target_value = marked_equity * target_weight if symbol in future_symbols else 0.0
                    delta_value = target_value - old_value

                    if abs(delta_value) <= 1e-10:
                        if target_value > 0:
                            new_holdings[symbol] = target_value / price
                            holdings_rows.append({
                                "Datetime": date,
                                "Symbol": symbol,
                                "Weight": target_weight,
                                "Shares": new_holdings[symbol],
                                "Price": price,
                                "Value": target_value,
                            })
                        continue

                    action = "BUY" if delta_value > 0 else "SELL"
                    shares_delta = abs(delta_value) / price
                    trade_value = abs(delta_value)
                    trade_value_total += trade_value

                    rebalance_rows.append({
                        "Datetime": date,
                        "Symbol": symbol,
                        "Action": action,
                        "Price": price,
                        "Shares": shares_delta,
                        "Value": trade_value,
                        "Weight_After": target_weight if symbol in future_symbols else 0.0,
                    })

                    if target_value > 0:
                        new_holdings[symbol] = target_value / price
                        holdings_rows.append({
                            "Datetime": date,
                            "Symbol": symbol,
                            "Weight": target_weight,
                            "Shares": new_holdings[symbol],
                            "Price": price,
                            "Value": target_value,
                        })

                holdings = new_holdings
                invested_value = sum(shares * float(prices[symbol]) for symbol, shares in holdings.items() if symbol in prices.index)
                cash = marked_equity - invested_value

                turnover_values.append((trade_value_total / marked_equity) if marked_equity > 0 else 0.0)

                selection_row = {"Datetime": date}
                for idx, symbol in enumerate(selected_symbols, start=1):
                    selection_row[f"Rank_{idx}"] = symbol
                selection_rows.append(selection_row)

            equity = cash + sum(shares * float(prices[symbol]) for symbol, shares in holdings.items() if symbol in prices.index)
            equity_rows.append({
                "Datetime": date,
                "Equity": equity,
                "Cash": cash,
                "Positions": len(holdings),
            })

        equity_curve = pd.DataFrame(equity_rows)
        rebalance_log = pd.DataFrame(rebalance_rows)
        holdings_history = pd.DataFrame(holdings_rows)
        selection_history = pd.DataFrame(selection_rows)

        final_equity = float(equity_curve["Equity"].iloc[-1]) if not equity_curve.empty else self.initial_capital
        summary = {
            "Final-Equity": final_equity,
            "Return %": ((final_equity - self.initial_capital) / self.initial_capital) * 100.0,
            "Trades": len(rebalance_log),
            "Rebalances": len(selection_history),
            "Average Turnover %": (sum(turnover_values) / len(turnover_values) * 100.0) if turnover_values else 0.0,
        }

        return PortfolioBacktestResult(
            equity_curve=equity_curve,
            rebalance_log=rebalance_log,
            holdings_history=holdings_history,
            selection_history=selection_history,
            score_matrix=score_matrix,
            summary=summary,
        )
