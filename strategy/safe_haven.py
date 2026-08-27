from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass(frozen=True)
class SafeHavenConfig:
    spy_target: float = 0.85
    sgov_target: float = 0.12
    spy_lower_band: float = 0.80
    spy_upper_band: float = 0.90
    annual_premium_limit: float = 0.03
    quarterly_premium_limit: float = 0.0075
    put_target_otm: tuple[float, float] = (0.25, 0.35)
    put_target_months: tuple[int, int] = (9, 15)
    roll_days_to_expiry: int = 180
    purchase_interval_days: int = 90


@dataclass(frozen=True)
class PutPosition:
    expiration: date
    market_value: float


@dataclass
class PortfolioState:
    spy_value: float
    sgov_value: float
    cash: float
    puts: list[PutPosition] = field(default_factory=list)
    year_start_equity: float = 0.0
    premium_spent_ytd: float = 0.0
    premium_year: int = 0
    last_put_purchase: Optional[date] = None
    completed_drawdown_stage: int = 0

    @property
    def put_value(self) -> float:
        return sum(position.market_value for position in self.puts)

    @property
    def total_equity(self) -> float:
        return self.spy_value + self.sgov_value + self.cash + self.put_value


@dataclass(frozen=True)
class Advice:
    priority: int
    action: str
    reason: str


@dataclass(frozen=True)
class DailyPlan:
    as_of: date
    symbol: str
    spy_price: float
    all_time_high: float
    drawdown: float
    spy_weight: float
    advice: tuple[Advice, ...]


def _drawdown_stage(drawdown: float) -> int:
    if drawdown >= 0.35:
        return 3
    if drawdown >= 0.25:
        return 2
    if drawdown >= 0.15:
        return 1
    return 0


def build_daily_plan(
    state: PortfolioState,
    spy_price: float,
    all_time_high: float,
    as_of: date,
    config: SafeHavenConfig = SafeHavenConfig(),
    symbol: str = "SPY",
) -> DailyPlan:
    if spy_price <= 0 or all_time_high <= 0:
        raise ValueError("SPY price and all-time high must be positive")
    if state.total_equity <= 0:
        raise ValueError("Portfolio total equity must be positive")

    drawdown = max(0.0, 1.0 - spy_price / all_time_high)
    spy_weight = state.spy_value / state.total_equity
    advice: list[Advice] = []
    planned_cash_outflow = 0.0

    current_stage = _drawdown_stage(drawdown)
    if current_stage > state.completed_drawdown_stage:
        stage_actions = {
            1: f"卖出盈利 {symbol} Put市值的25%，所得资金转入SGOV。",
            2: f"卖出原始Put保险配置的35%，所得资金分两批买入 {symbol}。",
            3: f"逐步卖出剩余盈利Put，将 {symbol} 恢复至85%目标权重。",
        }
        for stage in range(state.completed_drawdown_stage + 1, current_stage + 1):
            advice.append(Advice(1, stage_actions[stage], f"{symbol} 回撤已触发第{stage}档阈值。"))

    expiring = [position for position in state.puts if (position.expiration - as_of).days <= config.roll_days_to_expiry]
    if expiring:
        expirations = ", ".join(sorted({position.expiration.isoformat() for position in expiring}))
        advice.append(Advice(2, f"检查并滚动到期日为 {expirations} 的Put持仓。", "这些Put的剩余期限不超过180天。"))

    premium_year = state.premium_year or as_of.year
    premium_spent = state.premium_spent_ytd if premium_year == as_of.year else 0.0
    premium_base = state.year_start_equity if premium_year == as_of.year and state.year_start_equity > 0 else state.total_equity
    annual_remaining = max(0.0, premium_base * config.annual_premium_limit - premium_spent)
    purchase_due = state.last_put_purchase is None or (as_of - state.last_put_purchase).days >= config.purchase_interval_days
    if purchase_due and current_stage == 0:
        purchase_amount = min(premium_base * config.quarterly_premium_limit, annual_remaining)
        if purchase_amount > 0:
            planned_cash_outflow += purchase_amount
            low_strike = spy_price * (1.0 - config.put_target_otm[1])
            high_strike = spy_price * (1.0 - config.put_target_otm[0])
            advice.append(
                Advice(
                    3,
                    f"购买最多 ${purchase_amount:,.2f} 的9-15个月 {symbol} Put，执行价参考 ${low_strike:,.0f}-${high_strike:,.0f}。",
                    f"本季度保险应建仓；年度剩余保费预算为 ${annual_remaining:,.2f}。",
                )
            )
        else:
            advice.append(Advice(3, "不要购买新的Put。", "年度3%保费预算已经用完。"))

    target_spy_value = state.total_equity * config.spy_target
    if spy_weight < config.spy_lower_band:
        amount = min(target_spy_value - state.spy_value, state.cash + state.sgov_value)
        if amount > 0:
            planned_cash_outflow += min(amount, state.cash)
            advice.append(Advice(4, f"使用现金/SGOV购买约 ${amount:,.2f} 的 {symbol}。", f"{symbol} 权重为 {spy_weight:.1%}，低于80%下限。"))
    elif spy_weight > config.spy_upper_band:
        amount = state.spy_value - target_spy_value
        advice.append(Advice(4, f"卖出约 ${amount:,.2f} 的 {symbol}，所得资金转入SGOV。", f"{symbol} 权重为 {spy_weight:.1%}，高于90%上限。"))

    target_sgov_value = state.total_equity * config.sgov_target
    available_cash = max(state.cash - planned_cash_outflow, 0.0)
    sgov_purchase = min(max(target_sgov_value - state.sgov_value, 0.0), available_cash)
    if sgov_purchase > 0:
        advice.append(
            Advice(
                5,
                f"使用剩余现金购买约 ${sgov_purchase:,.2f} 的SGOV。",
                f"将SGOV配置提高至策略账户的 {config.sgov_target:.0%} 左右。",
            )
        )

    if not advice:
        advice.append(Advice(9, "今天不交易，保持当前持仓。", "没有触发回撤、滚动、保费或再平衡规则。"))

    return DailyPlan(as_of, symbol, spy_price, all_time_high, drawdown, spy_weight, tuple(sorted(advice, key=lambda item: item.priority)))