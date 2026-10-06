from __future__ import annotations

import pandas as pd


def heikin_ashi_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a Heikin-Ashi view while preserving dates and raw volume.

    This view is for reversal recognition only. Execution prices and protective
    extremes must continue to use the exchange's raw candles.
    """
    ordered = frame.sort_values("date").reset_index(drop=True).copy()
    if ordered.empty:
        return ordered
    raw_open = ordered["open"].astype(float)
    raw_high = ordered["high"].astype(float)
    raw_low = ordered["low"].astype(float)
    raw_close = ordered["close"].astype(float)
    ha_close = (raw_open + raw_high + raw_low + raw_close) / 4.0
    ha_open: list[float] = [(float(raw_open.iloc[0]) + float(raw_close.iloc[0])) / 2.0]
    for index in range(1, len(ordered)):
        ha_open.append((ha_open[-1] + float(ha_close.iloc[index - 1])) / 2.0)
    result = ordered.copy()
    result["open"] = ha_open
    result["close"] = ha_close
    result["high"] = pd.concat(
        (raw_high.rename("raw"), pd.Series(ha_open, name="open"), ha_close.rename("close")), axis=1
    ).max(axis=1)
    result["low"] = pd.concat(
        (raw_low.rename("raw"), pd.Series(ha_open, name="open"), ha_close.rename("close")), axis=1
    ).min(axis=1)
    return result


def latest_heikin_reversal(frame: pd.DataFrame, *, minimum_run: int = 2) -> dict | None:
    """Identify a new HA colour reversal after a meaningful opposite run."""
    if len(frame) < minimum_run + 2:
        return None
    ha = heikin_ashi_frame(frame)
    bodies = ha["close"].astype(float) - ha["open"].astype(float)
    direction = 1 if bodies.iloc[-1] > 0 else -1 if bodies.iloc[-1] < 0 else 0
    if not direction or any(direction * float(value) >= 0 for value in bodies.iloc[-minimum_run-1:-1]):
        return None
    raw = frame.sort_values("date").reset_index(drop=True)
    window = raw.tail(minimum_run + 2)
    extreme = (float(window["low"].astype(float).min()) if direction > 0
               else float(window["high"].astype(float).max()))
    return {
        "direction": direction,
        "time": pd.Timestamp(raw.iloc[-1]["date"]),
        "price": float(raw.iloc[-1]["close"]),
        "extreme": extreme,
        "run_bars": minimum_run,
    }
