# MVP Plan: Daily MACD + OBV Divergence Backtest

## 1) Objective
Implement a **daily timeframe** strategy and backtest flow on top of the existing framework, with minimal and isolated changes.

Strategy signal logic:
- **Buy** when:
  1. A **bullish MACD divergence** is detected using **DIFF (MACD line)**, and
  2. **OBV crosses above OBV SMA(30)** on close confirmation.
- **Sell / Exit** when:
  1. A **bearish MACD divergence** is detected using **DIFF**, and
  2. **OBV crosses below OBV SMA(30)** on close confirmation.

Trading direction in MVP:
- **Long-only** (no short selling changes in backtester).

---

## 2) Scope and Constraints (Confirmed)
- Do **not** modify existing entry files:
  - `main_single.py`
  - `main_daytrade.py`
- Create a **new main entry** for this strategy.
- Do **not** use CLI arguments for MVP.
- Use **hardcoded constants** inside the new main file.
- Remove manual batch-symbol mode from MVP.
- Supported run modes for MVP main:
  - Single-symbol run
  - Scanner-based batch run

---

## 3) Reuse Strategy
Reuse the current architecture as-is:
- Strategy interface from `strategy/base.py`
- Backtest execution from `backtester.py`
- Daily data loading/download from `data_manager.py` and provider mapping in `providers/yfinance_lib.py`
- Optional scanner universe source from `scanner/yf_screen.py`

This keeps MVP isolated and avoids side effects on existing intraday workflows.

---

## 4) Files to Add / Update

### New files
1. `strategy/macd_obv_divergence.py`
   - Implements `BaseStrategy.generate_signals`.
   - Computes MACD/DIFF, OBV, OBV SMA(30), divergence flags, and final Signal.

2. `main_macd_obv_daily.py`
   - Dedicated daily backtest entry.
   - Configuration via constants only (no argparse).
   - Runs either SINGLE or SCAN mode.

3. `test_macd_obv_strategy.py`
   - Basic scenario-based checks for signal generation and backtest integration.

### Existing file updates
1. `strategy/__init__.py`
   - Export/import the new strategy class.

No changes to `main_single.py` and `main_daytrade.py`.

---

## 5) MVP Main Entry Design (Constants Only)
In `main_macd_obv_daily.py`, define constants at top-level, for example:
- `RUN_MODE = "SINGLE"` or `"SCAN"`
- `SYMBOL = "MSFT"` (used in SINGLE mode)
- `SCAN_SOURCE = "YF_SCREEN"`
- `SCAN_TOP_N = 50`
- `TIMEFRAME = "daily1"`
- `LOOKBACK_PERIOD = "2y"` (or start/end date constants)
- Strategy constants:
  - `MACD_FAST = 12`
  - `MACD_SLOW = 26`
  - `MACD_SIGNAL = 9`
  - `OBV_MA = 30`
  - `PIVOT_WINDOW = 5`
  - `MAX_PIVOT_GAP = 60`
  - `MIN_BARS = 120`

Behavior:
- **SINGLE**: fetch/load one symbol and run backtest.
- **SCAN**: build symbol list from scanner and run batch backtests, then rank results.

---

## 6) Signal Definition Details for MVP

### 6.1 MACD divergence basis
- Use **DIFF (MACD line)** for divergence comparisons.
- Bullish divergence candidate:
  - Price makes lower low between two recent valid pivots.
  - DIFF makes higher low across corresponding pivots.
- Bearish divergence candidate:
  - Price makes higher high.
  - DIFF makes lower high.

### 6.2 OBV cross filter
- Buy filter: yesterday `OBV <= OBV_SMA30` and today `OBV > OBV_SMA30`.
- Sell filter: yesterday `OBV >= OBV_SMA30` and today `OBV < OBV_SMA30`.
- Cross is evaluated on **close confirmation** only.

### 6.3 Final signals
- `Signal = 1` only when bullish divergence + bullish OBV cross are both true.
- `Signal = -1` only when bearish divergence + bearish OBV cross are both true.
- Otherwise `Signal = 0`.

---

## 7) Stock Universe Approach (MVP Decision)
For MVP, use **light pre-filter then backtest** (not full-market brute force first).

Why:
- Faster iteration and lower API/data cost.
- Easier debugging of signal correctness.
- Better operational stability than immediate full-universe runs.

MVP source options:
- Preferred: scanner-based list (`scanner/yf_screen.py`).
- Fallback: a small hardcoded quality watchlist for initial sanity checks.

---

## 8) Validation Checklist (MVP)
1. Signal correctness
   - Manually inspect at least 20–30 generated signal points on charts.
2. Reproducibility
   - Same data + same constants should produce identical trades.
3. Basic robustness
   - Slight parameter perturbations should not completely invert behavior.
4. Backtest outputs
   - Verify trade log, equity curve, and summary metrics are produced in both SINGLE and SCAN modes.

---

## 9) Out of Scope for MVP
- Short-selling support in `backtester.py`.
- Historical point-in-time universe reconstruction.
- Full-market exhaustive walk-forward research.
- Refactoring existing intraday entry scripts.

---

## 10) Post-MVP Next Step (When Strategy Is Stable)
After MVP passes validation, proceed to:
1. Expand sample coverage (more sectors, market regimes, longer history).
2. Add historical-consistent universe construction to reduce look-ahead/survivorship bias.
3. Re-run stability gates across segmented time periods before production use.
