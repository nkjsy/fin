from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
LOGS_DIR = Path(__file__).resolve().parent / 'logs'
LOGS_DIR.mkdir(exist_ok=True)
STATE_PREFIX = 'momentum_live_state'


@dataclass
class ParsedState:
    as_of: str | None
    mode: str | None
    holdings: Dict[str, int]


def get_state_log_path(ts: datetime | None = None) -> Path:
    ts = ts or datetime.now(ET)
    return LOGS_DIR / f"{ts.strftime('%Y-%m-%d_%H-%M-%S')}_{STATE_PREFIX}.txt"


def get_latest_state_file() -> Path | None:
    files = sorted(LOGS_DIR.glob(f"*_{STATE_PREFIX}.txt"))
    return files[-1] if files else None


def parse_state_file(path: str | Path) -> ParsedState:
    path = Path(path)
    as_of = None
    mode = None
    holdings: Dict[str, int] = {}
    section = None

    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('AS_OF:'):
            as_of = line.split(':', 1)[1].strip()
            continue
        if line.startswith('MODE:'):
            mode = line.split(':', 1)[1].strip()
            continue
        if line == 'CURRENT_HOLDINGS:':
            section = 'holdings'
            continue
        if line.startswith('TARGET_') or line.endswith(':'):
            section = None if line != 'CURRENT_HOLDINGS:' else 'holdings'
        if section == 'holdings' and '|' in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2:
                symbol = parts[0]
                try:
                    qty = int(parts[1])
                except ValueError:
                    continue
                holdings[symbol] = qty

    return ParsedState(as_of=as_of, mode=mode, holdings=holdings)


def load_latest_holdings() -> Dict[str, int]:
    latest = get_latest_state_file()
    if latest is None:
        return {}
    return parse_state_file(latest).holdings


def write_state_file(
    *,
    as_of: datetime,
    mode: str,
    current_holdings: Dict[str, int],
    target_shares: Dict[str, int],
    quotes: Dict[str, float],
    orders: list[str],
    total_equity: float,
) -> Path:
    path = get_state_log_path(as_of)
    lines: list[str] = []
    lines.append(f"AS_OF: {as_of.isoformat()}")
    lines.append(f"MODE: {mode}")
    lines.append(f"TOTAL_EQUITY: {total_equity:.2f}")
    lines.append('')
    lines.append('CURRENT_HOLDINGS:')
    if current_holdings:
        for symbol in sorted(current_holdings):
            lines.append(f"{symbol} | {int(current_holdings[symbol])}")
    else:
        lines.append('NONE | 0')
    lines.append('')
    lines.append('TARGET_HOLDINGS:')
    if target_shares:
        for symbol in sorted(target_shares):
            px = quotes.get(symbol, 0.0)
            lines.append(f"{symbol} | {int(target_shares[symbol])} | {px:.2f}")
    else:
        lines.append('NONE | 0 | 0.00')
    lines.append('')
    lines.append('RECOMMENDED_ORDERS:')
    if orders:
        lines.extend(orders)
    else:
        lines.append('NO_ACTION')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path
