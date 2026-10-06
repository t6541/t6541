from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class WaterfallHold:
    active: bool
    direction: int
    atr_5m: float
    volume_ratio: float
    reason: str


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    return pd.concat(
        (frame["high"] - frame["low"], (frame["high"] - previous).abs(),
         (frame["low"] - previous).abs()), axis=1,
    ).max(axis=1).rolling(window).mean()


def waterfall_hold_signal(one_minute: pd.DataFrame, five_minute: pd.DataFrame,
                          fifteen_minute: pd.DataFrame, direction: int) -> WaterfallHold:
    """Recognize a genuine multi-timeframe, volume-backed cascade.

    The function is deliberately strict: it is an exit-management qualifier,
    never an entry signal.  Frames are expected to contain confirmed candles.
    """
    if direction not in {-1, 1} or min(len(one_minute), len(five_minute), len(fifteen_minute)) < 22:
        return WaterfallHold(False, direction, 0.0, 0.0, "瀑布持仓：周期数据不足")
    one = one_minute.copy()
    five = five_minute.copy()
    fifteen = fifteen_minute.copy()
    for frame in (one, five, fifteen):
        frame["ma5"] = frame["close"].rolling(5).mean()
        frame["ma10"] = frame["close"].rolling(10).mean()
        frame["ma20"] = frame["close"].rolling(20).mean()
    five["atr"] = _atr(five)
    last5 = five.iloc[-1]
    atr5 = float(last5["atr"])
    volume_average = float(five["volume"].iloc[-21:-1].mean())
    volume_ratio = float(last5["volume"]) / max(volume_average, 1e-12)
    body = direction * (float(last5["close"]) - float(last5["open"]))
    aligned5 = (last5["ma5"] > last5["ma10"] > last5["ma20"]) if direction > 0 else (
        last5["ma5"] < last5["ma10"] < last5["ma20"])
    aligned1 = (one.iloc[-1]["ma5"] > one.iloc[-1]["ma10"] > one.iloc[-1]["ma20"]) if direction > 0 else (
        one.iloc[-1]["ma5"] < one.iloc[-1]["ma10"] < one.iloc[-1]["ma20"])
    fifteen_ok = direction * (float(fifteen.iloc[-1]["close"]) - float(fifteen.iloc[-1]["ma20"])) >= 0
    directional_closes = direction * one["close"].tail(5).diff().dropna()
    acceleration = int((directional_closes > 0).sum()) >= 3
    active = bool(pd.notna(atr5) and atr5 > 0 and aligned5 and aligned1 and fifteen_ok and
                  acceleration and body >= atr5 * .75 and volume_ratio >= 1.5)
    if active:
        reason = (f"瀑布持仓生效：1分钟与5分钟均线同向、15分钟不逆向，"
                  f"5分钟实体{body / atr5:.2f} ATR、成交量{volume_ratio:.2f}倍")
    else:
        reason = (f"瀑布持仓未生效：均线/加速/放量条件未同时成立，"
                  f"5分钟实体{body / atr5:.2f} ATR、成交量{volume_ratio:.2f}倍" if atr5 > 0
                  else "瀑布持仓未生效：ATR无效")
    return WaterfallHold(active, direction, atr5, volume_ratio, reason)


def waterfall_trailing_prices(entry: float, direction: int, stop: float,
                              current_activation: float, current_callback: float,
                              atr_5m: float) -> tuple[float, float]:
    """Widen trailing protection so a cascade is not cut by a small rebound."""
    risk = abs(entry - stop)
    activation_distance = max(abs(current_activation - entry), risk, atr_5m * .8)
    callback = max(current_callback, atr_5m * .65, risk * .35)
    return entry + direction * activation_distance, callback
