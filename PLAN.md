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
