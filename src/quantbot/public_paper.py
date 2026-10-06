"""One-shot public-market paper cycle for the hourly monitor."""
from __future__ import annotations

from pathlib import Path
import pandas as pd

from .data import okx_history_market
from .intraday import latest_timeframe_signal
from .paper_ledger import PaperLedger, PaperMetrics


def run_public_paper_cycle(*, ledger_path: str | Path,
                           instrument: str = "ETH-USDT-SWAP",
                           bars: tuple[str, ...] = ("1m", "5m", "15m"),
                           history_limit: int = 100,
                           initial_cash: float = 10_000.0) -> PaperMetrics:
    """Read confirmed public candles and update the local ledger once.

    A target is emitted only when every requested timeframe agrees.  This is a
    research default, not an execution authorization.
    """
    signals = {}
    frames = []
    for bar in bars:
        frame = okx_history_market((instrument,), bar, history_limit)
        frames.append(frame)
        signals[bar] = latest_timeframe_signal(frame, bar)
    directions = {signal.direction for signal in signals.values()}
    direction = directions.pop() if len(directions) == 1 else 0
    latest = max(signal.candle_time for signal in signals.values())
    targets = pd.DataFrame({instrument: [float(direction)]}, index=pd.DatetimeIndex([latest]))
    market = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "symbol"])
    return PaperLedger(ledger_path, initial_cash=initial_cash).update(market, targets)
