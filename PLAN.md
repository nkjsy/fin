# Local Auto Trading Application Plan

## Milestone 1: Backtesting Engine & Scanner

### 1. Project Goal
Build a local, modular Python application for US stock trading. The app features a rule-based stock scanner, a custom backtesting engine, and interactive visualization. It is designed for future integration with Charles Schwab for live execution.

### 2. Architecture

The application follows a layered architecture to ensure separation of concerns and ease of maintenance.

#### A. Data Layer
*   **Source**: `yfinance` (currently), migratable to `schwab-py`.
*   **Storage**: Local `Parquet` files for high-performance I/O.
    *   **Structure**: One file per ticker per timeframe (e.g., `data/1d/AAPL.parquet`).
    *   **Optimization**: A `universe_summary.parquet` file acts as an index, storing the latest snapshot (Price, Volume, Sector) for all stocks. This allows the scanner to filter thousands of stocks instantly without opening individual history files.
*   **Interfaces**: `IDataProvider` and `IBroker` abstract base classes ensure that switching data sources or brokers does not break the rest of the app.

#### B. Scanner Module
*   **Logic**: Sequential filtering.
    1.  **Pre-filter**: Loads `universe_summary.parquet` to filter by basic metrics (Price, Volume) using vectorized operations.
    2.  **Deep Scan**: (Optional) Loads full history only for the filtered candidates to calculate complex indicators.
*   **Universe**: S&P 500 (expandable to full market).

#### C. Strategy & Backtesting
*   **Strategy**: Base class using `pandas-ta` for indicator calculation.
    *   Example: `RsiStrategy` (Buy if RSI < 30, Sell if RSI > 70).
*   **Backtester**: Custom event-driven engine.
    *   Iterates through historical data.
    *   Tracks Cash, Positions, Equity Curve, and Trade Log.
    *   Supports multiple timeframes (1d, 5m, 1m).

#### D. Execution & Visualization
*   **Entry Point**: `src/main.py`.
*   **Configuration**: All parameters (Ticker, Timeframe, Strategy settings) are configured directly in the Python code.
*   **Output**:
    *   Console output for performance metrics and trade logs.
    *   Interactive `Plotly` charts (opened in browser) for visual analysis of trade execution.

### 3. Directory Structure

```text
fin/
├── data/                   # Local storage
│   ├── universe_summary.parquet
│   ├── 1d/                 # Daily data parquet files
│   ├── 5m/                 # 5-minute data parquet files
│   └── 1m/                 # 1-minute data parquet files
├── src/
│   ├── interfaces.py       # Abstract Base Classes (IDataProvider, IBroker)
│   ├── data_manager.py     # Handles Parquet I/O and incremental updates
│   ├── scanner.py          # Logic to filter stocks
│   ├── strategy.py         # Strategy logic & indicators
│   ├── backtester.py       # Simulation engine
│   ├── main.py             # Main entry point (Configuration & Execution)
│   ├── utils.py            # Helpers (e.g., S&P 500 fetcher)
│   └── providers/
│       └── yfinance_lib.py # Concrete implementation of IDataProvider
├── requirements.txt        # Project dependencies
└── PLAN.md                 # This document
```

### 4. Future Roadmap (Schwab Integration)

1.  **Authentication**: Implement `SchwabProvider` in `src/providers/` using `schwab-py`.
2.  **Execution**: Implement `place_order` in the `IBroker` interface.
3.  **Live Mode**: Update `src/main.py` to support a "Live" flag that routes orders to the Schwab provider.

## Milestone 2: Live Trading Integration (schwab-py)

Transition from historical backtesting to real-time trading using schwab-py. Scanners remain synchronous; a new async bull flag strategy handles live streaming. Broker abstraction allows seamless switching between paper and live trading.

### Steps

1. **Add Schwab authentication & config**: Create `config.py` (gitignored) for API credentials; use schwab-py's `easy_client()` for OAuth token management.

2. **Create `SchwabProvider`** in `providers/schwab_lib.py`: Implement `IDataProvider` interface, mapping intervals to `get_price_history_*()` methods. Keep synchronous for scanner compatibility.

3. **Create `LiveMomentumScanner`** in `scanner/live_momentum.py`: Synchronous scanner using `SchwabProvider` that:
   - Calls `get_movers(Movers.Index.NASDAQ, sort_order=Movers.SortOrder.PERCENT_CHANGE_UP)` to get top 10 up movers
   - Fetches previous day's volume via `get_price_history_every_day()`
   - Waits until 9:40, fetches 5-min data for 9:30–9:40
   - Filters to tickers with 5x relative volume vs previous day average
   - Returns confirmed ticker list

4. **Create broker folder structure**: New `broker/` folder with `__init__.py`, `interfaces.py`, `paper_broker.py`, and `schwab_broker.py`.

5. **Create `IBroker` interface** in `broker/interfaces.py`: Define `place_order()`, `cancel_order()`, `get_positions()`, `get_account_balance()`, `get_order_status()` abstract methods.

6. **Create `PaperBroker`** in `broker/paper_broker.py`: Implement `IBroker`, logs all order calls to console with timestamps, tracks simulated positions/P&L in memory.

7. **Create `SchwabBroker`** in `broker/schwab_broker.py`: Implement `IBroker` using schwab-py's order templates for real execution.

8. **Create sync bull flag strategy** in `strategy/bull_flag_live.py`: Synchronous version of `BullFlagStrategy` that:
   - Processes 5-minute candles via `process_candle()` method
   - Exposes `state` property (SCANNING, PULLBACK, IN_POSITION)
   - Provides `check_breakout(price)` for real-time breakout detection
   - Provides `check_stop_loss(price)` for real-time stop loss monitoring
   - Emits signals via callback when entry/exit conditions are met

9. **Create `LiveTradingEngine`** in `live_engine.py`: Synchronous polling engine that:
   - Polls 5-minute candles every 5 minutes for all tracked symbols
   - When any strategy is in PULLBACK or IN_POSITION state, switches to fast polling mode (every few seconds) using quote API
   - Calls `check_breakout(price)` for PULLBACK state entries
   - Calls `check_stop_loss(price)` for IN_POSITION state exits
   - Routes signals to injected `IBroker` for execution
   - Runs in a simple `while` loop with `time.sleep()`, no async required

10. **Rewrite `main_today.py`**: Entry point that:
    - Parses `--live` flag (default: paper trading)
    - At 9:30 calls `get_movers(NASDAQ, PERCENT_CHANGE_UP)` for top 10 up movers
    - Fetches 5-min data 9:30–9:40 to confirm 5x volume vs previous day
    - Injects `PaperBroker` or `SchwabBroker` based on flag
    - Runs `LiveTradingEngine` in sync polling loop until market close

## Milestone 3: Premarket News Gap Strategy

A premarket news-based strategy that scans Finviz every minute, buys confirmed stocks immediately, and exits at 9:35 AM. Speed-optimized with parallel API calls and order placement.

### Strategy Rules

| Rule | Value |
|------|-------|
| Scan time | 7:00 AM - 9:29 AM ET |
| Scan interval | 60 seconds |
| Pre-filter | Finviz: price $2-$50, float ≤ 100M |
| Confirmation | Schwab: gain ≥ 3% vs prev close, rel volume ≥ 5x vs yesterday same-time |
| Max positions | 5 per day |
| Position size | $10,000 each (constant) |
| Order type | Limit at ask + 0.5% |
| Stop loss | 5%, monitored after 9:30 AM only |
| Exit | 9:35 AM ET |

### Expected Behavior

| Scenario | Frequency |
|----------|-----------|
| Empty scan | Most rounds |
| 1-3 stocks | Occasional |
| 5+ stocks | Rare |

### Speed Optimizations

| Step | Method | Latency |
|------|--------|---------|
| Finviz scrape | Single request | ~1 sec |
| Schwab history + calculations | Parallel (ThreadPoolExecutor) | ~1-2 sec |
| Order placement | Parallel (ThreadPoolExecutor) | ~0.5 sec |
| **Total** | | ~3-4 sec after scan |

Rate limit: 120 Schwab API calls/min. With 1-3 candidates per scan, well within limit.

### Components

#### 1. `scanner/finviz_news.py`

Extends `BaseScanner`. All confirmation logic lives here.

- Scrape Finviz for stocks with news
- Pre-filter: price $2-$50, float ≤ 100M
- Skip symbols in `skip` param or currently `confirming`
- Parallel confirm via Schwab: fetch history, calculate gain % and rel volume
- Return confirmed `List[str]`

#### 2. `PremarketNewsEngine` in `live_engine.py`

Position management only. Reuses existing broker, logger, client.

- `POSITION_AMOUNT = 10000` constant
- `add_positions(symbols)` - Buy in parallel, limit at ask + 0.5%, skip if already in position
- `check_stop_losses()` - Check stop losses, only call after 9:30 AM
- `exit_all()` - Sell all positions in parallel at 9:35 AM
- `_fetch_quotes()` - Reuse pattern from `LiveTradingEngine`

#### 3. `main_premarket.py`

Entry point. Owns the scan loop.

- Parse `--live` flag (default: paper trading)
- 7:00-9:29: Scan every 60 sec, buy confirmed (max 5)
- 9:30-9:35: Check stop losses every 5 sec
- 9:35: Exit all positions
- Print summary

### Reused Components

| Component | Source | Usage |
|-----------|--------|-------|
| `IBroker` | `broker/interfaces.py` | Order placement |
| `PaperBroker` | `broker/paper_broker.py` | Testing |
| `SchwabBroker` | `broker/schwab_broker.py` | Live trading |
| `AutoRefreshSchwabClient` | `client/` | Schwab API auth |
| `get_logger` | `logger.py` | Logging |
| `BaseScanner` | `scanner/base.py` | Scanner base class |

### Edge Case: Overlapping Confirmations

If a symbol is still being confirmed when next scan returns it:

- Scanner tracks `confirming: set` for in-flight confirmations
- Skip symbols in `confirming` to avoid duplicate work
- Clean up `confirming` after each batch completes

## Milestone 4: Premarket Bull Flag Strategy (Refactor)

Replace immediate buy/sell at 9:35 with bull flag strategy on 1-min chart. Keep scanner, add pattern-based entries/exits.

### Strategy Rules

| Rule | Value |
|------|-------|
| Scan time | 7:00 AM - 9:29 AM ET |
| Scan interval | 60 seconds |
| Pre-filter | Finviz: price $2-$50, float ≤ 100M, news in last 5 min |
| Confirmation | Schwab: gain ≥ 3% vs 10 min ago, rel volume ≥ 5x, 10 consecutive candles |
| Max tracked | 3 symbols (for speed) |
| Position size | $10,000 each |
| Entry | Bull flag breakout on 1-min chart |
| Exit | Stop loss hit OR red candle close while in position |
| Trading hours | 7:00 AM - 4:00 PM ET (extended hours enabled) |

### Key Differences from Milestone 3

| Aspect | Milestone 3 | Milestone 4 |
|--------|-------------|-------------|
| Entry | Buy immediately on confirm | Wait for bull flag breakout |
| Exit | Force exit at 9:35 | Stop loss or red candle close |
| Candle interval | 5-min | 1-min |
| Max positions | 5 | 3 (tracking), entries depend on pattern |
| Duration | 7:00-9:35 only | 7:00-4:00 (full day) |

### Refactoring Plan

#### 1. Generalize `LiveTradingEngine`

Make the engine reusable for both 1-min (premarket) and 5-min (regular) trading.

**New parameters in `__init__`:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `candle_interval` | `int` | `5` | Candle interval in minutes (1 or 5) |
| `extended_hours` | `bool` | `False` | Enable premarket/afterhours data |
| `position_amount` | `float` | `None` | Fixed position size (if set, ignores account %) |

**Method updates:**

- `_datetime_to_slot()` - Use `self.candle_interval` instead of hardcoded 5
- `_slot_to_time_str()` - Use `self.candle_interval` instead of hardcoded 5
- `_fetch_candles()` - Select API method based on interval, pass `extended_hours`
- `_handle_signal()` - Use `position_amount` if set, otherwise calculate from account
- `start()` - Adjust sleep timing based on `candle_interval`

#### 2. Add `add_symbol()` method

Allow dynamic symbol addition from scanner during runtime.

```python
def add_symbol(self, symbol: str, replay_minutes: int = 10) -> None:
    """Add a symbol to track. Replays last N minutes of candles to catch up."""
    if symbol in self.strategies:
        return  # Already tracking
    if len(self.strategies) >= self.max_symbols:
        return  # At capacity
    
    # Fetch and replay historical candles
    candles = self._fetch_history(symbol, minutes=replay_minutes)
    strategy = BullFlagLiveStrategy(...)
    for candle in candles:
        strategy.process_candle(candle)
    
    self.strategies[symbol] = strategy
```

#### 3. Detect pullback failure

When strategy transitions from PULLBACK → SCANNING, stop tracking the symbol.

```python
def _check_pattern_failed(self) -> None:
    """Remove symbols where pattern failed (PULLBACK → SCANNING)."""
    to_remove = []
    for symbol, strategy in self.strategies.items():
        if strategy.state == StrategyState.SCANNING and strategy.prev_state == StrategyState.PULLBACK:
            to_remove.append(symbol)
    
    for symbol in to_remove:
        self.logger.info(f"Pattern failed for {symbol}, removing from tracking")
        del self.strategies[symbol]
        del self.candle_data[symbol]
```

#### 4. Update `main_premarket.py`

Combined scan + candle + quote loop architecture:

```
7:00 AM - 4:00 PM:
├── Every 60 sec: Run Finviz scan
│   └── If confirmed & slots available: add_symbol(sym, replay_minutes=10)
├── Every 1 min (on candle close): Fetch candles, process_candle()
│   └── Check for pattern failures, remove if PULLBACK → SCANNING
└── Every 3 sec: Poll quotes for PULLBACK/IN_POSITION symbols
    ├── PULLBACK: check_breakout(price) → entry signal
    └── IN_POSITION: check_stop_loss(price) → exit signal
```

#### 5. Delete `PremarketNewsEngine`

No longer needed after refactor. All functionality moves to generalized `LiveTradingEngine`.

### Timing Analysis

Measured latency (tested):

| Operation | Latency |
|-----------|---------|
| Finviz scrape (2 stocks) | ~2.6 sec |
| Schwab confirmation | ~2 sec |
| **Total scan** | ~5 sec |

Scan blocks at most 2 quote poll cycles (~6 sec max slippage). Acceptable for 60-sec scan interval.

### Components

#### 1. `LiveTradingEngine` (modified)

- Add `candle_interval`, `extended_hours`, `position_amount` params
- Add `add_symbol()` for dynamic symbol addition
- Add `_check_pattern_failed()` to remove dead patterns
- Support 1-min and 5-min candles

#### 2. `main_premarket.py` (rewritten)

- Use generalized `LiveTradingEngine` with `candle_interval=1`, `extended_hours=True`
- Run Finviz scan in main loop every 60 sec
- Track at most 3 symbols
- Run until market close (4:00 PM)

### Reused Components

| Component | Source | Usage |
|-----------|--------|-------|
| `FinvizNewsScanner` | `scanner/finviz_news.py` | Premarket scanning |
| `BullFlagLiveStrategy` | `strategy/bull_flag_live.py` | Pattern detection |
| `LiveTradingEngine` | `live_engine.py` | Candle/quote polling (generalized) |
| `IBroker` | `broker/interfaces.py` | Order placement |
| `AutoRefreshSchwabClient` | `client/` | Schwab API auth |
## Milestone 5: Combined Bull Flag + Opening Range Breakout (ORB)

Run two strategies sequentially in one session: bull flag during premarket (7:00–9:30), then ORB on all confirmed stocks after market open (9:30–close).

### Strategy Rules — ORB

| Rule | Value |
|------|-------|
| Candidates | All stocks confirmed by Finviz scanner during premarket Phase 1 |
| Range window | First 5 minutes after open (9:30–9:35), configurable |
| Range filter | Skip if range width > 4% of price (risk too large) |
| Entry | Price breaks above range high |
| Stop loss | Range low |
| Take profit (Phase 1) | 1:1 R:R target → sell 50% position |
| Take profit (Phase 2) | Trailing stop at range_width below highest price → sell remaining 50% |
| Position size | $10,000 each |
| No max symbol limit | ORB runs on all premarket-confirmed stocks |

### Two-Phase Session

| Phase | Time | Strategy | Scanner |
|-------|------|----------|---------|
| Phase 1 | 7:00–9:30 AM | Bull flag (existing) | Finviz every 60s |
| Transition | 9:30 AM | Stop bull flag scanning, let open positions ride | — |
| Phase 2 | 9:30 AM–4:00 PM | ORB on all confirmed stocks | None (candidates collected in Phase 1) |

### Exit Logic — ORB IN_POSITION

```
if not partial_exit_done:
    if price >= target_1r:
        SELL 50% (quantity_pct=0.5)
        partial_exit_done = True
        activate trailing stop
    if price <= stop_loss:
        SELL 100% (quantity_pct=1.0)
        go to SCANNING
else:
    update trailing_stop = highest_high - range_width
    if price <= trailing_stop:
        SELL 100% (quantity_pct=1.0)
        go to SCANNING
```

### Implementation Steps

#### 1. Update `strategy/base.py` — add shared types + ILiveStrategy ABC

Move `StrategyState`, `Candle`, `Signal` from `strategy/bull_flag_live.py` into `base.py`. Add `BUILDING_RANGE` to `StrategyState`. Add `ILiveStrategy` ABC:

- `process_candle(candle: Candle) -> Optional[Signal]`
- `check_breakout(current_price: float) -> Optional[Signal]`
- `check_stop_loss(current_price: float) -> Optional[Signal]`
- `reset()`
- Attributes: `state`, `prev_state`, `symbol`, `on_signal`

Keep existing `BaseStrategy` unchanged.

Add `quantity_pct: float = 1.0` field to `Signal` dataclass (1.0 = full position, 0.5 = half).

#### 2. Update `strategy/bull_flag_live.py` — use shared types

- Remove `StrategyState`, `Candle`, `Signal` class definitions
- Import from `strategy.base`
- Extend `ILiveStrategy`
- Keep `GreenSequence` in place
- No logic changes

#### 3. Create `strategy/orb_live.py` — ORB strategy

`ORBLiveStrategy(ILiveStrategy)` with params: `symbol`, `range_minutes` (default 5), `max_range_pct` (default 0.04), `on_signal`.

State machine (all core logic in `process_candle()`, real-time checks in `check_breakout()`/`check_stop_loss()`):

| State | Behavior |
|-------|----------|
| `BUILDING_RANGE` | Collect candles from 9:30 for `range_minutes` mins. Track `range_high`, `range_low`. Transition to `PULLBACK` when range complete. Skip if range width > `max_range_pct`. |
| `PULLBACK` | Watch for breakout. `check_breakout(price)`: if `price >= range_high` → BUY signal, enter `IN_POSITION`. `process_candle()`: same check on candle high. |
| `IN_POSITION` | Two sub-phases controlled by `self.partial_exit_done` flag. Before 1:1 target: `check_stop_loss(price)` at `range_low` (sell 100%), or sell 50% at 1:1 R:R target. After 1:1 target: trailing stop at `highest_high - range_width` (sell 100%). |
| `SCANNING` | Done. Strategy finished for this symbol. |

Candles before 9:30: stored in history for context, no state changes.

#### 4. Update `live_engine.py` — strategy-agnostic

- Import from `strategy.base` instead of `strategy.bull_flag_live`
- Type hint: `self.strategies: Dict[str, ILiveStrategy]`
- Add `strategy_factory` param to `__init__()`: callable `(symbol, on_signal) -> ILiveStrategy`, defaults to `BullFlagLiveStrategy`
- Update `add_symbol()`: use `self.strategy_factory(symbol, None)` instead of hardcoded `BullFlagLiveStrategy`
- Update `__init__` constructor loop: same factory usage
- Update `_handle_signal()` SELL branch: use `int(position.quantity * signal.quantity_pct)` instead of `position.quantity`
- Everything else unchanged

#### 5. Create `main_combined.py` — two-phase orchestration

Entry point with `--live` flag only. Constants: `BF_CUTOFF = dt_time(9, 30)`, `ORB_RANGE_MINUTES = 5`, `POSITION_AMOUNT = 10000`, `MAX_SYMBOLS = 3`, `REPLAY_MINUTES = 3`.

- Maintain `all_confirmed: Set[str]` — every scanner-confirmed symbol during Phase 1
- **Phase 1 (7:00–9:30):** Bull flag engine with Finviz scanner loop (same as `main_premarket.py`). All confirmed symbols added to `all_confirmed`.
- **Transition at 9:30:** Remove non-IN_POSITION bull flag strategies. Create second `LiveTradingEngine` with ORB factory. Add all `all_confirmed` symbols (no replay, no max_symbols limit). ORB can trade stocks bull flag already traded.
- **Phase 2 (9:30–close):** Combined loop running both engines. Bull flag engine manages remaining IN_POSITION symbols only. ORB engine processes candles for all candidates. Exit when both engines done or market close.

#### 6. Update `strategy/__init__.py` — add exports

Add `ILiveStrategy`, `ORBLiveStrategy`, `StrategyState`, `Candle`, `Signal`.

### Verification

- Paper-trade `python main_combined.py` and verify:
  - Phase 1: bull flag works identically to `main_premarket.py`
  - `all_confirmed` collects all scanner-confirmed symbols
  - At 9:30: non-positioned bull flag symbols removed, positioned ones kept
  - ORB builds range from first 5 minutes, skips stocks with range > 4%
  - ORB enters on breakout above range high via real-time polling
  - ORB sells 50% at 1:1 R:R, remaining 50% on trailing stop
  - Both engines coexist if bull flag has open position at 9:30
- Run `python main_premarket.py` to verify backward compatibility

### Reused Components

| Component | Source | Usage |
|-----------|--------|-------|
| `FinvizNewsScanner` | `scanner/finviz_news.py` | Premarket scanning (Phase 1) |
| `BullFlagLiveStrategy` | `strategy/bull_flag_live.py` | Premarket pattern detection |
| `LiveTradingEngine` | `live_engine.py` | Candle/quote polling (both phases) |
| `IBroker` | `broker/interfaces.py` | Order placement |
| `PaperBroker` | `broker/paper_broker.py` | Testing |
| `SchwabBroker` | `broker/schwab_broker.py` | Live trading |
| `AutoRefreshSchwabClient` | `client/` | Schwab API auth |