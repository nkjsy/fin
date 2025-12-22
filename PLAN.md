# Local Auto Trading Application Plan

## 1. Project Goal
Build a local, modular Python application for US stock trading. The app features a rule-based stock scanner, a custom backtesting engine, and interactive visualization. It is designed for future integration with Charles Schwab for live execution.

## 2. Architecture

The application follows a layered architecture to ensure separation of concerns and ease of maintenance.

### A. Data Layer
*   **Source**: `yfinance` (currently), migratable to `schwab-py`.
*   **Storage**: Local `Parquet` files for high-performance I/O.
    *   **Structure**: One file per ticker per timeframe (e.g., `data/1d/AAPL.parquet`).
    *   **Optimization**: A `universe_summary.parquet` file acts as an index, storing the latest snapshot (Price, Volume, Sector) for all stocks. This allows the scanner to filter thousands of stocks instantly without opening individual history files.
*   **Interfaces**: `IDataProvider` and `IBroker` abstract base classes ensure that switching data sources or brokers does not break the rest of the app.

### B. Scanner Module
*   **Logic**: Sequential filtering.
    1.  **Pre-filter**: Loads `universe_summary.parquet` to filter by basic metrics (Price, Volume) using vectorized operations.
    2.  **Deep Scan**: (Optional) Loads full history only for the filtered candidates to calculate complex indicators.
*   **Universe**: S&P 500 (expandable to full market).

### C. Strategy & Backtesting
*   **Strategy**: Base class using `pandas-ta` for indicator calculation.
    *   Example: `RsiStrategy` (Buy if RSI < 30, Sell if RSI > 70).
*   **Backtester**: Custom event-driven engine.
    *   Iterates through historical data.
    *   Tracks Cash, Positions, Equity Curve, and Trade Log.
    *   Supports multiple timeframes (1d, 5m, 1m).

### D. Execution & Visualization
*   **Entry Point**: `src/main.py`.
*   **Configuration**: All parameters (Ticker, Timeframe, Strategy settings) are configured directly in the Python code.
*   **Output**:
    *   Console output for performance metrics and trade logs.
    *   Interactive `Plotly` charts (opened in browser) for visual analysis of trade execution.

## 3. Directory Structure

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

## 4. Future Roadmap (Schwab Integration)

1.  **Authentication**: Implement `SchwabProvider` in `src/providers/` using `schwab-py`.
2.  **Execution**: Implement `place_order` in the `IBroker` interface.
3.  **Live Mode**: Update `src/main.py` to support a "Live" flag that routes orders to the Schwab provider.
