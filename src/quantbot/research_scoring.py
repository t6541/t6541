"""Offline forward-return labels for five-minute research samples."""
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class ForwardLabel:
    sample_time: str
    horizon_bars: int
    forward_return: float
    maximum_favorable_excursion: float
    maximum_adverse_excursion: float


def label_five_minute_sample(market: pd.DataFrame, sample_time, *, horizon_bars: int = 6,
                             direction: int = 1) -> ForwardLabel:
    """Label one sample without exposing the result to live execution."""
    if direction not in {-1, 1} or horizon_bars <= 0:
        raise ValueError("invalid label inputs")
    frame = market.sort_values("date").reset_index(drop=True)
    matches = frame.index[pd.to_datetime(frame["date"]) == pd.Timestamp(sample_time)].tolist()
    if not matches or matches[0] + horizon_bars >= len(frame):
        raise ValueError("future window is incomplete")
    index = matches[0]
    entry = float(frame.iloc[index]["close"])
    future = frame.iloc[index + 1:index + horizon_bars + 1]
    final = float(future.iloc[-1]["close"])
    signed = direction
    forward_return = signed * (final - entry) / entry
    favorable = max(signed * (float(row.close) - entry) / entry for row in future.itertuples())
    adverse = min(signed * (float(row.close) - entry) / entry for row in future.itertuples())
    return ForwardLabel(pd.Timestamp(sample_time).isoformat(), horizon_bars,
                        round(forward_return, 8), round(favorable, 8), round(adverse, 8))
