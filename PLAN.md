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
