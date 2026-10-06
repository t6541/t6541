"""Closing-price market structure shared by entry and regime classifiers."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class CloseStructure:
    direction: int
    state: str
    reason: str
    previous_high: float | None = None
    latest_high: float | None = None
    previous_low: float | None = None
    latest_low: float | None = None


def closing_price_structure(frame: pd.DataFrame, label: str) -> CloseStructure:
    """Classify HH/HL, LH/LL, or mixed structure from candle closes only.

    Wicks deliberately do not participate in the trend decision.  Two-bar
    confirmation keeps the latest unfinished edge from becoming look-ahead.
    """
    data = frame.sort_values("date").reset_index(drop=True)
    if len(data) < 24:
        return CloseStructure(0, "insufficient_data", f"{label}收盘价结构数据不足")
    closes = data["close"].astype(float).tolist()
    pivot_highs = [(i, closes[i]) for i in range(2, len(closes) - 2)
                   if closes[i] > max(closes[i - 2:i])
                   and closes[i] >= max(closes[i + 1:i + 3])]
    pivot_lows = [(i, closes[i]) for i in range(2, len(closes) - 2)
                  if closes[i] < min(closes[i - 2:i])
                  and closes[i] <= min(closes[i + 1:i + 3])]
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return CloseStructure(
            0, "sideways",
            f"{label}尚未形成两组确认收盘高低点，按横盘整理区处理",
        )
    (_, previous_high), (_, latest_high) = pivot_highs[-2:]
    (_, previous_low), (_, latest_low) = pivot_lows[-2:]
    # Only a tiny price-scaled tolerance is used.  The user's definition is
    # structural (one closing high/low above or below the prior one), not an
    # ATR-distance momentum filter.
    tolerance = max(abs(closes[-1]) * .00001, 1e-9)
    if latest_high > previous_high + tolerance and latest_low > previous_low + tolerance:
        return CloseStructure(
            1, "uptrend", f"{label}收盘价上涨：上沿高点抬高、下沿低点抬高",
            previous_high, latest_high, previous_low, latest_low,
        )
    if latest_high < previous_high - tolerance and latest_low < previous_low - tolerance:
        return CloseStructure(
            -1, "downtrend", f"{label}收盘价下跌：上沿高点降低、下沿低点降低",
            previous_high, latest_high, previous_low, latest_low,
        )
    return CloseStructure(
        0, "sideways",
        f"{label}收盘高低点上下交错，按横盘震荡/整理区处理，等待末段给出方向",
        previous_high, latest_high, previous_low, latest_low,
    )
