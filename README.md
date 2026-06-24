# fin

Research and live-signal scripts for market strategies. The current production-style workflow is **Daily Momentum Live Signal After Close**, implemented by `main_momentum_11_1_live.py`.

## Daily Momentum Live Signal After Close

This script generates a daily after-close target portfolio and recommended rebalance actions for the Nasdaq-100 11-1 momentum strategy.

### What the strategy does

- Refreshes the current Nasdaq-100 universe before each live/paper signal run.
- Downloads daily OHLCV history with `yfinance`.
- Computes 11-1 cross-sectional momentum:
  - lookback: `231` trading days
  - skip: `21` trading days
- Uses QQQ versus its 200-day moving average as the regime filter:
  - `QQQ > MA200` → risk-on, hold momentum **Top3**
  - `QQQ <= MA200` → risk-off, hold momentum **Top10**
- In risk-on mode, reranks using the latest close every run.
- In risk-off mode, freezes ranks between monthly rank refreshes.
- Targets equal-weight positions based on the equity baseline.
- Writes both a normal run log and a state log under `logs/`.

By default this is **signal-only / paper mode**. It prints recommended actions but does not place real broker orders. `--live` currently only enables live-intent logging; broker order placement should be wired only after Schwab client/config cleanup.

### Files involved

- `main_momentum_11_1_live.py` — daily live/paper signal entrypoint.
- `live_signal_state.py` — reads the latest state log as current holdings and writes the next state log.
- `strategy/momentum_11_1.py` — momentum score and portfolio selection helper.
- `main_momentum_11_1_regime_immediate.py` — shared constants and `yfinance` history fetch helper.
- `logs/*_momentum_live_state.txt` — persisted state used by the next run.

### Environment setup

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If the existing virtualenv is already present, just activate it:

```bash
cd /path/to/fin
source .venv/bin/activate
```

### Run manually after market close

Recommended run time: **after US market close, around 17:10 America/New_York**. This gives daily close data time to settle in `yfinance`.

```bash
cd /path/to/fin
source .venv/bin/activate
python3 main_momentum_11_1_live.py
```

Optional paper equity baseline:

```bash
python3 main_momentum_11_1_live.py --initial-cash 100000
```

The script will print sections like:

- `REGIME SUMMARY`
- `CURRENT HOLDINGS`
- `TARGET HOLDINGS`
- `RECOMMENDED ACTIONS`

It also writes:

- `logs/YYYY-MM-DD_HH-MM-SS.log`
- `logs/YYYY-MM-DD_HH-MM-SS_momentum_live_state.txt`

### Install as a daily cron job

Use a timezone-aware cron entry so the schedule follows US Eastern time through daylight-saving changes.

Open crontab:

```bash
crontab -e
```

Add:

```cron
TZ=America/New_York
10 17 * * 1-5 cd /path/to/fin && . .venv/bin/activate && python3 main_momentum_11_1_live.py >> logs/cron_momentum_live.log 2>&1
```

This runs Monday-Friday at **17:10 ET**.

Replace `/path/to/fin` with the actual repo path, for example:

```cron
TZ=America/New_York
10 17 * * 1-5 cd /home/nkjsy/fin && . .venv/bin/activate && python3 main_momentum_11_1_live.py >> logs/cron_momentum_live.log 2>&1
```

### Check the latest signal

```bash
ls -lt logs/*_momentum_live_state.txt | head
cat "$(ls -t logs/*_momentum_live_state.txt | head -1)"
```

Important fields in the state log:

- `MODE:` — `Top3` or `Top10`.
- `CURRENT_HOLDINGS:` — holdings loaded from the previous state log.
- `MOMENTUM_ORDER:` — ranked selection order from strongest to weakest momentum.
- `TARGET_HOLDINGS:` — target share counts in momentum order.
- `RECOMMENDED_ORDERS:` — buy/sell/hold actions needed to reach target holdings.

### First-run behavior

If there is no previous `logs/*_momentum_live_state.txt`, the script treats current holdings as empty and recommends buys into the target portfolio. After the first run, subsequent runs use the latest state log as the current paper holdings baseline.

### Safety notes

- Run without `--live` for normal daily signal generation.
- Do not rely on `--live` for real orders until broker integration is reviewed and tested.
- Review `RECOMMENDED_ORDERS` before trading manually.
- If `yfinance` returns incomplete data, rerun later after close data has settled.
