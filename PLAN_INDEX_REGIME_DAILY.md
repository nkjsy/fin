# Plan: Daily Index Regime Engine (High Accuracy, <= 1-Day Lag)

## 1) Objective
Build a **daily regime classification layer** for index trading that:
1. Has high regime classification quality.
2. Has low operational lag (maximum 1 trading day).
3. Uses only daily data.
4. Enforces: **if confidence is low, do not run any strategy**.

---

## 2) Core Decision Policy (Confirmed)
Use model probability `P(Trend)` plus directional state dominance and apply 4 labels:

- `P(Trend) >= 0.65` and direction positive → `BULL_TREND`
- `P(Trend) >= 0.65` and direction negative → `BEAR_TREND`
- `P(Trend) <= 0.35` → `RANGE`
- `0.35 < P(Trend) < 0.65` or neutral direction → `NO_TRADE`

Long-only execution policy (current):
- Trade only in `BULL_TREND`.
- Treat `BEAR_TREND`, `RANGE`, and uncertain zone as non-bull exposure states.
- Current default exposure mapping:
   - `BULL_TREND` => `1.00`
   - `RANGE` => `1.00`
   - `NO_TRADE` => `0.10`
   - `BEAR_TREND` => `0.00`

---

## 3) Low-Lag Requirement (<= 1 Day)
Execution timing is fixed to avoid look-ahead:
- Compute regime at day **t close**.
- Apply mode/strategy decision at day **t+1 open**.

This guarantees at most 1-day decision lag and no future leakage.

---

## 4) Regime Model Choice
Primary model: **online/filtered direct 3-state regime model**.

Why:
- Better regime separation than static thresholds.
- Lower lag than smoothed hindsight labels.
- Stable enough for index-level signals.

Model output:
- Daily filtered probabilities `P_Bull`, `P_Bear`, `P_Range`.
- Daily filtered trend probability `P(Trend) = P_Bull + P_Bear`.
- Final direct labels `BULL_TREND`, `BEAR_TREND`, `RANGE`, `NO_TRADE`.

Current implementation details:
- Rolling state-conditional Gaussian emissions.
- 3-state transition priors for bull, bear, and range persistence.
- Hysteresis-based final label stabilization.

---

## 5) Feature Set (Daily, Index-Level)
Use one index as regime anchor (e.g., QQQ or SPY).

Suggested features:
- `r1`: 1-day log return
- `mom5`: 5-day momentum
- `mom20`: 20-day momentum
- `vol20`: 20-day realized volatility
- `atr_norm`: ATR(14) / Close
- `vol_z20`: 20-day volume z-score
- `slow_ema_gap`: Close / slow EMA - 1
- `slow_ema_slope`: slow EMA slope over rolling lookback
- `adx14`: used for in-window state seeding

All features must be computed with rolling windows using only data available up to day `t`.

---

## 6) Confidence & Switch Stabilization
To reduce noisy flipping while preserving low lag:

- Use hysteresis thresholds: enter trend at `0.65`, exit trend below `0.45`.
- Optional confirmation: require 2 consecutive closes in new zone before switching mode.
- Keep `NO_TRADE` strict in uncertain zone.

Current implementation update:
- Fixed thresholds are still supported.
- Default mode now uses **adaptive threshold gating** based on rolling quantiles of past `P(Trend)`.
- Adaptive thresholds are computed with `shift(1)` so threshold values themselves remain leakage-safe.
- Direction still comes from bull/bear state dominance; adaptive thresholds only gate trend/range confidence.

Note: if 2-day confirmation is enabled, keep it only for **mode switching**, not for signal generation, to preserve practical responsiveness.

---

## 7) Integration Design in Current Repo
No changes required to existing intraday mains.

Add:
1. `strategy/market_regime_daily.py`
   - Feature builder
   - Rolling train + filtered probability inference
   - Direct 3-state probability output
   - Fixed-threshold or adaptive-threshold regime label generation

2. `main_index_regime_daily.py`
   - New standalone daily index entry (constants-only config)
   - Applies `BULL_TREND / BEAR_TREND / RANGE / NO_TRADE` policy
   - Runs next-day-open target-exposure execution from regime labels

3. `main_index_regime_walkforward.py`
   - New standalone validation entry
   - Reuses the same classifier and execution logic
   - Runs multi-index walk-forward validation on `QQQ / SPY / IWM`

Reuse:
- `providers/yfinance_lib.py`
- `data_manager.py`
- performance/risk metric helpers in `main_index_regime_daily.py`

---

## 8) Backtest Protocol (Leakage-Safe)
Use walk-forward evaluation:
1. Train model on rolling history up to day `t`.
2. Infer filtered `P(Trend)` at day `t`.
3. Map regime decided at day `t close` to target exposure.
4. Rebalance at day `t+1 open`.

Report:
- Return, CAGR, max drawdown, Sharpe, Calmar, exposure
- Buy-and-hold baseline on the same window
- Walk-forward segment performance across multiple indices
- Time spent in each regime / effective exposure state

---

## 9) Acceptance Criteria
The solution is accepted only if all hold:
1. Decision lag is <= 1 day by construction.
2. `NO_TRADE` removes low-confidence trades as specified.
3. Risk-adjusted performance improves vs always-on baseline (lower drawdown preferred).
4. Regime switching frequency remains stable (no excessive whipsaw).

---

## 10) Default MVP Parameters
- Regime anchor: `QQQ`
- Training window: auto-sized, currently capped to `756` trading days on long samples
- Inference update: daily
- Fixed-threshold fallback: trend enter `0.70`, trend exit `0.50`, range enter `0.35`
- Current adaptive threshold defaults:
   - window `168`
   - trend enter quantile `0.75`
   - trend exit quantile `0.60`
   - range enter quantile `0.25`
- Current exposure policy: bull `1.00`, range `1.00`, no-trade `0.10`, bear `0.00`

---

## 11) Future Extension (After MVP)
- Add short-side execution for `BEAR_TREND` when backtester supports shorting.
- Add index ensemble voting (SPY + QQQ + IWM).
- Add online parameter drift monitoring and periodic threshold recalibration.
- Add side-by-side fixed-vs-adaptive threshold reporting in the main entry.
- Add adaptive threshold grid search by index instead of one shared default set.
