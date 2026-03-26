# Plan: 11-1 Momentum Portfolio Backtest vs QQQ

## 1) Objective
Build a new **daily cross-sectional portfolio strategy** on top of the current repo using:
- **11-1 momentum** ranking
- **Nasdaq-100 constituents** as the candidate universe
- **Top 10 equal-weight holdings**
- **Same-day close approximation** for monthly rebalance execution
- **QQQ** as the benchmark

The goal is to add a clean MVP implementation without disturbing the existing intraday and single-symbol strategy flows.

---

## 2) Core Strategy Definition (Confirmed)

### 2.1 Universe
- Use **current Nasdaq-100 constituents** as the candidate stock pool.
- Source tickers via `utils.get_nasdaq100_tickers()`.
- Normalize symbols for yfinance compatibility when needed, for example:
  - `BRK.B` -> `BRK-B`
  - `BF.B` -> `BF-B`

### 2.2 Momentum score
Use **11-1 momentum**, defined as:

$$
M_{11-1}(t) = \frac{P_{t-21}}{P_{t-(231+21)}} - 1
$$

Where:
- `231` trading days approximates **11 months**
- `21` trading days approximates **1 month skip**

Interpretation:
- Rank stocks by performance over the prior 11 months
- Exclude the most recent 1 month from the ranking signal

### 2.3 Portfolio construction
- Rebalance **monthly**
- On each rebalance date, rank all eligible S&P 500 members by `11-1` momentum
- Buy the **top 10** names
- Use **equal weights** across selected holdings
- Long-only

### 2.4 Execution rule
- Compute rankings using data available through the rebalance date
- Approximate rebalancing at the **same-day close**
- Use each stock's `Close` as the execution price for both sells and buys

### 2.5 Benchmark
- Compare the strategy against **QQQ buy-and-hold** over the same backtest window
- Normalize benchmark equity to the same initial capital as the strategy

---

## 3) Scope and Constraints
- Do **not** refactor existing single-symbol backtest flow in `backtester.py`
- Do **not** modify existing intraday entry files
- Create a **new isolated portfolio backtest path** for this strategy
- Use **daily data** only
- Use **hardcoded constants** in the first MVP entry file
- Ignore transaction costs and slippage in MVP
- Ignore point-in-time historical S&P 500 membership in MVP

Important limitation for MVP:
- The universe will use the **current** Nasdaq-100 membership list, so the first version has **survivorship bias**

---

## 4) Why This Needs a Portfolio Engine
The existing `BaseStrategy` + `BacktestEngine` flow is designed for:
- one symbol
- one signal stream
- one position at a time

The 11-1 strategy is different because it requires:
- **cross-sectional ranking** across many stocks on the same date
- **simultaneous holdings** in multiple names
- **periodic portfolio rebalance** rather than single-name buy/sell events

Therefore, the clean design is to add a dedicated **portfolio backtester** instead of forcing this logic into the current single-name engine.

---

## 5) Files to Add / Update

### New files
1. `strategy/momentum_11_1.py`
   - Momentum score calculation helpers
   - Eligibility checks
   - Monthly rebalance date logic
   - Cross-sectional ranking helpers

2. `portfolio_backtester.py`
   - Multi-asset portfolio simulation engine
   - Monthly rebalance execution at close
   - Holdings history, trade log, equity curve, turnover

3. `main_momentum_11_1.py`
   - Dedicated entry point for downloading/loading Nasdaq-100 data
   - Runs strategy backtest and QQQ benchmark comparison
   - Prints summary metrics
   - Optional plotting hook if needed later

4. `test_momentum_11_1.py`
   - Unit/smoke tests for score calculation, ranking, rebalance logic, and benchmark alignment

### Existing file updates
1. `strategy/__init__.py`
   - Export the new strategy helpers if needed

2. `utils.py` or strategy helper layer
   - Add or reuse ticker normalization for yfinance-safe index constituents

---

## 6) Data Flow

### 6.1 Universe build
- Load tickers from `utils.get_nasdaq100_tickers()`
- Normalize tickers for provider compatibility
- Optionally de-duplicate and sort

### 6.2 Price data
- Download/load `daily1` OHLCV data for all eligible tickers using:
  - `data_manager.py`
  - `providers/yfinance_lib.py`
- Reuse local parquet cache when available
- Only download missing data or refresh when explicitly requested

### 6.3 Benchmark data
- Download/load `QQQ` daily data using the same provider flow
- Align benchmark window to the strategy backtest window

---

## 7) Eligibility Rules
A stock is eligible on rebalance date `t` only if:
- it has enough history to compute `11-1` momentum
- required `Close` values are present for:
  - `t`
  - `t - 21`
  - `t - (231 + 21)`
- the rebalance-date `Close` is valid for execution

If a name is missing required data on a rebalance date:
- exclude it from that month's ranking

If fewer than 10 names qualify:
- hold fewer than 10 names and keep the remainder in cash

---

## 8) Rebalance Logic

### 8.1 Rebalance calendar
- Use **monthly** rebalance dates
- Prefer the **last available trading day of each month** in the aligned dataset

### 8.2 Ranking step
At each rebalance date:
1. Compute the `11-1` score for every eligible stock
2. Sort descending by score
3. Select the top 10 names

### 8.3 Trade step
At the same rebalance-date close:
1. Sell names no longer in the top 10
2. Resize names still in the portfolio toward equal weight
3. Buy newly selected names

### 8.4 Weighting
- Target weight per selected name:

$$
w_i = \frac{1}{10}
$$

- If fewer than 10 names qualify, target weight becomes equal across selected names and residual stays in cash

---

## 9) Backtest Mechanics

### 9.1 Initial capital
- Start with a configurable fixed amount, for example `10000.0`

### 9.2 Equity accounting
- Between rebalance dates, mark holdings daily using close prices
- Total equity = cash + market value of all held positions

### 9.3 Execution approximation
- Use rebalance-date `Close` for fills
- This is intentionally approximate and acceptable for MVP

### 9.4 Transaction assumptions
- No commissions
- No slippage
- No borrow/short logic
- Fractional shares may be allowed or disallowed depending on implementation simplicity; choose one explicitly in code and keep it consistent

Recommended MVP choice:
- Allow **fractional position weights** at the portfolio level for cleaner equal-weight simulation

---

## 10) Output Metrics
The strategy summary should include at least:
- Final Equity
- Total Return %
- CAGR
- Max Drawdown
- Rebalance Count
- Average Turnover
- QQQ Return % over the same period
- Excess Return % vs QQQ

The backtest should also output:
- `equity_curve`
- `holdings_history`
- `rebalance_log`
- `benchmark_curve`

---

## 11) Main Entry Design
Create a new entry file: `main_momentum_11_1.py`

Suggested constants at top-level:
- `DATA_DIR = "data"`
- `TIMEFRAME = "daily1"`
- `INITIAL_CAPITAL = 10000.0`
- `BENCHMARK_SYMBOL = "QQQ"`
- `UNIVERSE_NAME = "NASDAQ100"`
- `TOP_N = 10`
- `LOOKBACK_DAYS = 231`
- `SKIP_DAYS = 21`
- `PERIOD = "15y"` or similar
- `REFRESH_DATA = False`
- `PLOT_RESULT = True/False`

Main flow:
1. Build Nasdaq-100 universe
2. Load/download all required daily data
3. Run monthly 11-1 portfolio backtest
4. Run QQQ buy-and-hold benchmark on the same window
5. Print summary comparison
6. Optionally plot strategy vs QQQ equity curves

---

## 12) Validation Checklist
Before trusting results, verify:

1. Momentum calculation
- Confirm recent 1-month return is excluded
- Confirm lookback indexing is correct

2. Ranking correctness
- Confirm top 10 selection matches computed scores on sample rebalance dates

3. Rebalance correctness
- Confirm dropped names are sold and new names are bought at rebalance close
- Confirm holdings weights are reset as expected

4. Benchmark correctness
- Confirm QQQ curve starts on the same backtest date
- Confirm initial capital normalization matches the strategy

5. Robustness
- Missing stock data on a rebalance date should not crash the run

---

## 13) Known Risks and Biases
- **Survivorship bias** from using today's Nasdaq-100 membership list
- **Execution approximation** from using same-day close fills
- No trading cost model in MVP
- Wikipedia-based constituent source may occasionally change schema or ticker formatting

These are acceptable for MVP but should be explicitly documented.

---

## 14) Post-MVP Extensions
After the core strategy is working, consider:

1. Historical point-in-time Nasdaq-100 membership
2. Trading cost and slippage model
3. Sector caps or diversification constraints
4. Volatility scaling or risk parity weighting
5. Regime filter overlay using existing daily regime work
6. Walk-forward parameter validation

---

## 15) Tested Regime Variants Summary (2026-03)

### Best current variant

**Recommended script:** `main_momentum_11_1_regime_immediate.py`

**Current default logic:**
- If `QQQ > MA200` → hold **Top3**
- If `QQQ < MA200` → hold **Top10**
- Regime is checked **daily** and the portfolio switches immediately between the precomputed monthly Top3 and Top10 lists.
- Cross-sectional ranking is still computed from the monthly 11-1 momentum snapshot; the strategy does **not** recompute a fresh daily cross-section.

### Immediate-switch regime logic

1. At each monthly rebalance point, compute:
   - the month's **Top3** momentum basket
   - the month's **Top10** momentum basket
2. On every trading day after that, check whether `QQQ` is above or below its 200-day moving average.
3. If above MA200, hold the month's **Top3** basket.
4. If below MA200, expand defensively into the month's **Top10** basket.
5. At the next monthly rebalance, refresh both baskets and continue the same daily regime switching.

### Why this design worked best

- It preserves the strength of concentrated momentum exposure in strong markets.
- It reduces single-name concentration risk during weaker market regimes without fully exiting the market.
- It avoids the overreaction and whipsaw risk of full cash filters.
- It keeps the cross-sectional ranking cadence consistent with the original monthly 11-1 strategy.

### Performance comparison table

| Variant | CAGR | MaxDD | Sharpe | Calmar | Total Return |
|---|---:|---:|---:|---:|---:|
| Top10 original | 22.38% | -42.82% | 0.851 | 0.523 | +2531.30% |
| Top5 original | 31.17% | -52.10% | 0.987 | 0.598 | +7991.45% |
| Top5 + MA200 flat below | 25.68% | -42.26% | 0.915 | 0.608 | +3948.63% |
| Top5 + MA300 flat below | 23.28% | -48.56% | 0.834 | 0.479 | +2860.33% |
| Top5 + MA200 confirm 5d then flat | 27.01% | -52.09% | 0.934 | 0.519 | +4701.67% |
| Top5 + MA200 below => 50% exposure | 28.71% | -46.97% | 0.976 | 0.611 | +5851.71% |
| Top10 + MA200 below => 50% exposure | 19.77% | -37.95% | 0.816 | 0.521 | +1755.40% |
| Top5 above / Top10 below (full, monthly switch) | 31.67% | -50.09% | 1.012 | 0.632 | +8497.81% |
| Top5 above / Top10 below (half, monthly switch) | 28.86% | -45.38% | 0.984 | 0.636 | +5962.55% |
| **Top3 above / Top10 below (full, immediate switch)** | **37.85%** | **-48.71%** | **1.069** | **0.777** | **+18000.23%** |

### Chart artifacts

Tracked chart files:
- `assets/immediate_regime_switch.png` — current best variant chart
- `assets/top5_top10_full.png` — Top5 above / Top10 below full-switch chart (add when regenerated)
- `assets/top5_top10_half.png` — Top5 above / Top10 below half-switch chart (add when regenerated)

### How to run

Use Python directly (no shell wrapper required):

```bash
cd /home/nkjsy/fin
source .venv312/bin/activate
python main_momentum_11_1_nocache.py
python main_momentum_11_1_regime_monthly.py
python main_momentum_11_1_regime_immediate.py
```

Notes:
- `main_momentum_11_1_regime_immediate.py` is the current recommended script.
- Current default in the immediate script is:
  - `QQQ > MA200` → Top3
  - `QQQ < MA200` → Top10
  - daily regime switching
