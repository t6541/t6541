"""Offline replay features for Beijing hourly multi-timeframe boundaries."""

from __future__ import annotations

import json
from datetime import timezone

import numpy as np
import pandas as pd


def _frame(one: pd.DataFrame, rule: str) -> pd.DataFrame:
    indexed = one.set_index("date")[["open", "high", "low", "close", "volume"]]
    return indexed.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna().reset_index()


def _state(frame: pd.DataFrame, boundary: pd.Timestamp) -> dict:
    past = frame[frame["date"] < boundary].copy()
    closes = past["close"].astype(float)
    row = past.iloc[-1]
    ma5 = float(closes.tail(5).mean())
    ma10 = float(closes.tail(10).mean())
    ma20 = float(closes.tail(20).mean())
    prev20 = float(closes.iloc[-21:-1].mean()) if len(closes) >= 21 else ma20
    direction = 1 if ma5 > ma10 > ma20 and ma20 >= prev20 else (-1 if ma5 < ma10 < ma20 and ma20 <= prev20 else 0)
    return {"open": float(row.open), "high": float(row.high), "low": float(row.low),
            "close": float(row.close), "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "direction": direction, "colour": 1 if row.close > row.open else (-1 if row.close < row.open else 0)}


def build_hourly_samples(one_minute: pd.DataFrame, *, hours: int = 720) -> list[dict]:
    """Build the latest ``hours`` completed Beijing-hour boundary observations.

    The first five minutes after the boundary are the observable trigger window;
    higher-timeframe state uses only candles closed before that boundary.
    """
    one = one_minute.copy()
    one["date"] = pd.to_datetime(one["date"], utc=True)
    one = one.sort_values("date").drop_duplicates("date")
    frames = {"5m": _frame(one, "5min"), "15m": _frame(one, "15min"), "1H": _frame(one, "1h")}
    first = one.date.min().ceil("h") + pd.Timedelta(hours=21)
    last = one.date.max().floor("h")
    boundaries = pd.date_range(first, last, freq="1h", tz="UTC")[-hours:]
    result: list[dict] = []
    for boundary in boundaries:
        pre = one[one.date < boundary].tail(25)
        trigger = one[(one.date >= boundary) & (one.date < boundary + pd.Timedelta(minutes=5))]
        future = one[(one.date >= boundary + pd.Timedelta(minutes=5)) &
                     (one.date < boundary + pd.Timedelta(minutes=65))]
        if len(pre) < 21 or len(trigger) < 3:
            continue
        states = {name: _state(frame, boundary) for name, frame in frames.items()}
        close = pre.close.astype(float)
        ma5, ma10, ma20 = (float(close.tail(n).mean()) for n in (5, 10, 20))
        prior_high = float(pre.tail(8)[["open", "close"]].max(axis=1).max())
        prior_low = float(pre.tail(8)[["open", "close"]].min(axis=1).min())
        trig_high = float(trigger[["open", "close"]].max(axis=1).max())
        trig_low = float(trigger[["open", "close"]].min(axis=1).min())
        start, end = float(trigger.iloc[0].open), float(trigger.iloc[-1].close)
        turn = -1 if trig_high >= prior_high and end < start else (1 if trig_low <= prior_low and end > start else 0)
        fan = 1 if ma5 > ma10 > ma20 else (-1 if ma5 < ma10 < ma20 else 0)
        parent = states["15m"]["direction"] if states["15m"]["direction"] == states["1H"]["direction"] else 0
        if turn == -1:
            classification = "反抽高点追空" if parent == -1 else "局部顶部反转做空"
        elif turn == 1:
            classification = "回踩低点追多" if parent == 1 else "局部底部反转做多"
        else:
            classification = "整点观察"
        direction = turn
        entry = end
        f30 = future[future.date < boundary + pd.Timedelta(minutes=35)]
        f60 = future
        def metrics(frame: pd.DataFrame) -> tuple[float | None, float | None, float | None]:
            if direction == 0 or frame.empty:
                return None, None, None
            last_close = float(frame.iloc[-1].close)
            ret = (last_close - entry) * direction
            mfe = (float(frame.high.max()) - entry) if direction > 0 else (entry - float(frame.low.min()))
            mae = (entry - float(frame.low.min())) if direction > 0 else (float(frame.high.max()) - entry)
            return ret, mfe, mae
        r30, mfe30, mae30 = metrics(f30)
        r60, mfe60, mae60 = metrics(f60)
        evidence = {"one_minute_fan_before": fan, "boundary_turn": turn,
                    "trigger_open": start, "trigger_close": end,
                    "trigger_body_high": trig_high, "trigger_body_low": trig_low,
                    "states": states}
        result.append({
            "instrument": "ETH-USDT-SWAP", "beijing_hour": boundary.tz_convert("Asia/Shanghai").isoformat(),
            "boundary_utc": boundary.isoformat(), "classification": classification, "direction": direction,
            "one_minute_fan_direction": fan, "five_direction": states["5m"]["direction"],
            "fifteen_direction": states["15m"]["direction"], "hour_direction": states["1H"]["direction"],
            "entry_reference": entry, "forward_30m_points": r30, "forward_60m_points": r60,
            "mfe_30m_points": mfe30, "mae_30m_points": mae30,
            "mfe_60m_points": mfe60, "mae_60m_points": mae60,
            "success_3_points_30m": int(mfe30 is not None and mfe30 >= 3 and (mae30 or 0) < mfe30),
            "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        })
    return result

