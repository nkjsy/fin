from dataclasses import dataclass
from math import erf, exp, log, sqrt

import pandas as pd


@dataclass(frozen=True)
class ModeledPut:
    strike: float
    units: float
    opened_index: int
    expiry_index: int


@dataclass(frozen=True)
class SafeHavenBacktestResult:
    equity_curve: pd.DataFrame
    events: pd.DataFrame
    summary: dict


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def black_scholes_put(spot: float, strike: float, years: float, rate: float, volatility: float) -> float:
    if years <= 0:
        return max(strike - spot, 0.0)
    if spot <= 0 or strike <= 0 or volatility <= 0:
        raise ValueError("Option inputs must be positive")
    root_time = sqrt(years)
    d1 = (log(spot / strike) + (rate + 0.5 * volatility * volatility) * years) / (volatility * root_time)
    d2 = d1 - volatility * root_time
    return strike * exp(-rate * years) * _normal_cdf(-d2) - spot * _normal_cdf(-d1)


def _metrics(equity: pd.Series, trading_days: int) -> dict:
    returns = equity.pct_change().dropna()
    years = max((len(equity) - 1) / trading_days, 1.0 / trading_days)
    cagr = (float(equity.iloc[-1]) / float(equity.iloc[0])) ** (1.0 / years) - 1.0
    drawdown = equity / equity.cummax() - 1.0
    volatility = float(returns.std() * sqrt(trading_days)) if not returns.empty else 0.0
    sharpe = float(returns.mean() / returns.std() * sqrt(trading_days)) if len(returns) > 1 and returns.std() > 0 else 0.0
    return {
        "Final Equity": float(equity.iloc[-1]),
        "Total Return %": (float(equity.iloc[-1]) / float(equity.iloc[0]) - 1.0) * 100.0,
        "CAGR %": cagr * 100.0,
        "Max Drawdown %": float(drawdown.min()) * 100.0,
        "Volatility %": volatility * 100.0,
        "Sharpe": sharpe,
    }


class SafeHavenBacktester:
    """Modeled quarterly tail-risk strategy; not a historical option-quote backtest."""

    def __init__(
        self,
        initial_capital: float = 100000.0,
        target_weight: float = 0.85,
        lower_band: float = 0.80,
        upper_band: float = 0.90,
        quarterly_budget: float = 0.0075,
        annual_budget: float = 0.03,
        strike_otm: float = 0.30,
        purchase_interval: int = 63,
        expiry_days: int = 252,
        roll_after_days: int = 126,
        volatility_multiplier: float = 1.25,
        minimum_put_volatility: float = 0.25,
        maximum_put_volatility: float = 0.60,
        option_spread: float = 0.05,
        trading_days: int = 252,
    ):
        if not 0.0 < lower_band < target_weight < upper_band < 1.0:
            raise ValueError("Exposure bands must satisfy 0 < lower < target < upper < 1")
        if quarterly_budget < 0.0 or annual_budget < 0.0:
            raise ValueError("Premium budgets cannot be negative")
        self.initial_capital = float(initial_capital)
        self.target_weight = target_weight
        self.lower_band = lower_band
        self.upper_band = upper_band
        self.quarterly_budget = quarterly_budget
        self.annual_budget = annual_budget
        self.strike_otm = strike_otm
        self.purchase_interval = purchase_interval
        self.expiry_days = expiry_days
        self.roll_after_days = roll_after_days
        self.volatility_multiplier = volatility_multiplier
        self.minimum_put_volatility = minimum_put_volatility
        self.maximum_put_volatility = maximum_put_volatility
        self.option_spread = option_spread
        self.trading_days = trading_days

    def run(self, data: pd.DataFrame, symbol: str) -> SafeHavenBacktestResult:
        required = {"Close", "VIX", "Rate"}
        if data.empty or not required.issubset(data.columns):
            raise ValueError(f"Backtest data must contain {sorted(required)}")

        frame = data.sort_index().copy()
        frame[list(required)] = frame[list(required)].apply(pd.to_numeric, errors="coerce")
        frame = frame.dropna(subset=["Close", "VIX", "Rate"])
        if frame.empty:
            raise ValueError("Backtest data has no valid rows")

        first_price = float(frame["Close"].iloc[0])
        shares = self.initial_capital * self.target_weight / first_price
        defensive_cash = self.initial_capital * (1.0 - self.target_weight)
        puts: list[ModeledPut] = []
        events: list[dict] = []
        rows: list[dict] = []
        price_peak = first_price
        completed_stage = 0
        premium_year = frame.index[0].year
        year_start_equity = self.initial_capital
        premium_spent = 0.0
        total_premium = 0.0
        crisis_sales = 0

        def option_price(position: ModeledPut, index: int, spot: float, rate: float, volatility: float) -> float:
            years = max(position.expiry_index - index, 0) / self.trading_days
            return black_scholes_put(spot, position.strike, years, rate, volatility)

        for index, (timestamp, row) in enumerate(frame.iterrows()):
            spot = float(row["Close"])
            rate = max(float(row["Rate"]), 0.0) / 100.0
            volatility = min(
                max(float(row["VIX"]) / 100.0 * self.volatility_multiplier, self.minimum_put_volatility),
                self.maximum_put_volatility,
            )
            defensive_cash *= 1.0 + rate / self.trading_days
            price_peak = max(price_peak, spot)
            drawdown = max(0.0, 1.0 - spot / price_peak)

            put_prices = [option_price(position, index, spot, rate, volatility) for position in puts]
            put_value = sum(position.units * price for position, price in zip(puts, put_prices))
            equity = shares * spot + defensive_cash + put_value

            if timestamp.year != premium_year:
                premium_year = timestamp.year
                year_start_equity = equity
                premium_spent = 0.0

            if drawdown < 0.10:
                completed_stage = 0

            stage = 3 if drawdown >= 0.35 else 2 if drawdown >= 0.25 else 1 if drawdown >= 0.15 else 0
            stage_fractions = {1: 0.25, 2: 0.35, 3: 1.0}
            if stage > completed_stage and puts:
                for crossed_stage in range(completed_stage + 1, stage + 1):
                    fraction = stage_fractions[crossed_stage]
                    proceeds = 0.0
                    retained: list[ModeledPut] = []
                    for position, price in zip(puts, put_prices):
                        sold_units = position.units * fraction
                        proceeds += sold_units * price * (1.0 - self.option_spread)
                        remaining_units = position.units - sold_units
                        if remaining_units > 1e-12:
                            retained.append(ModeledPut(position.strike, remaining_units, position.opened_index, position.expiry_index))
                    defensive_cash += proceeds
                    puts = retained
                    crisis_sales += 1
                    events.append({"Date": timestamp, "Symbol": symbol, "Event": f"CRISIS_SELL_{crossed_stage}", "Value": proceeds})
                    put_prices = [option_price(position, index, spot, rate, volatility) for position in puts]
                completed_stage = stage

            retained = []
            for position in puts:
                price = option_price(position, index, spot, rate, volatility)
                if index - position.opened_index >= self.roll_after_days or index >= position.expiry_index:
                    proceeds = position.units * price * (1.0 - self.option_spread)
                    defensive_cash += proceeds
                    events.append({"Date": timestamp, "Symbol": symbol, "Event": "ROLL_SELL", "Value": proceeds})
                else:
                    retained.append(position)
            puts = retained

            if index % self.purchase_interval == 0 and stage == 0:
                remaining_budget = max(year_start_equity * self.annual_budget - premium_spent, 0.0)
                spend = min(year_start_equity * self.quarterly_budget, remaining_budget, defensive_cash)
                strike = spot * (1.0 - self.strike_otm)
                model_price = black_scholes_put(spot, strike, self.expiry_days / self.trading_days, rate, volatility)
                purchase_price = model_price * (1.0 + self.option_spread)
                if spend > 0 and purchase_price > 0:
                    puts.append(ModeledPut(strike, spend / purchase_price, index, index + self.expiry_days))
                    defensive_cash -= spend
                    premium_spent += spend
                    total_premium += spend
                    events.append({"Date": timestamp, "Symbol": symbol, "Event": "PUT_BUY", "Value": spend})

            put_value = sum(position.units * option_price(position, index, spot, rate, volatility) for position in puts)
            equity = shares * spot + defensive_cash + put_value
            underlying_weight = shares * spot / equity
            target_value = equity * self.target_weight
            if underlying_weight < self.lower_band:
                purchase = min(target_value - shares * spot, defensive_cash)
                if purchase > 0:
                    shares += purchase / spot
                    defensive_cash -= purchase
                    events.append({"Date": timestamp, "Symbol": symbol, "Event": "REBALANCE_BUY", "Value": purchase})
            elif underlying_weight > self.upper_band:
                sale = shares * spot - target_value
                shares -= sale / spot
                defensive_cash += sale
                events.append({"Date": timestamp, "Symbol": symbol, "Event": "REBALANCE_SELL", "Value": sale})

            put_value = sum(position.units * option_price(position, index, spot, rate, volatility) for position in puts)
            equity = shares * spot + defensive_cash + put_value
            rows.append({
                "Date": timestamp,
                "Equity": equity,
                "Underlying": shares * spot,
                "Defensive": defensive_cash,
                "Put_Value": put_value,
                "Drawdown": drawdown,
            })

        curve = pd.DataFrame(rows).set_index("Date")
        buy_hold = self.initial_capital * frame.loc[curve.index, "Close"] / first_price
        summary = {
            "Symbol": symbol,
            "Start": curve.index[0].date().isoformat(),
            "End": curve.index[-1].date().isoformat(),
            **_metrics(curve["Equity"], self.trading_days),
            "Buy Hold CAGR %": _metrics(buy_hold, self.trading_days)["CAGR %"],
            "Buy Hold Max Drawdown %": _metrics(buy_hold, self.trading_days)["Max Drawdown %"],
            "Total Premium": total_premium,
            "Crisis Sales": crisis_sales,
        }
        curve["Buy_Hold"] = buy_hold
        return SafeHavenBacktestResult(curve, pd.DataFrame(events), summary)