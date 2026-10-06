from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SpikeGuardResult:
    blocked: bool
    reason: str
    timeframe: str = ""
    candle_time: pd.Timestamp | None = None


def _spike_in_recent(frame: pd.DataFrame, timeframe: str, recent_bars: int) -> SpikeGuardResult:
    """Detect a closed-candle liquidity spike without letting it pollute its own baseline."""
    if len(frame) < 25:
        return SpikeGuardResult(False, "行情样本不足，未触发扎针熔断")
    data = frame.copy()
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = data[column].astype(float)
    previous_close = data["close"].shift(1)
    true_range = pd.concat((
        data["high"] - data["low"],
        (data["high"] - previous_close).abs(),
        (data["low"] - previous_close).abs(),
    ), axis=1).max(axis=1)
    # Shifted baselines are essential: the spike itself must not make its ATR
    # and volume thresholds easier to pass.
    atr = true_range.shift(1).rolling(20).mean()
    volume_mean = data["volume"].shift(1).rolling(20).mean()
    for index in range(max(20, len(data) - recent_bars), len(data)):
        row = data.iloc[index]
        atr_value = float(atr.iloc[index])
        average_volume = float(volume_mean.iloc[index])
        if atr_value <= 0 or average_volume <= 0:
            continue
        candle_range = float(row["high"] - row["low"])
        body = abs(float(row["close"] - row["open"]))
        upper_wick = float(row["high"] - max(row["open"], row["close"]))
        lower_wick = float(min(row["open"], row["close"]) - row["low"])
        longest_wick = max(upper_wick, lower_wick)
        range_extreme = candle_range >= atr_value * 3.0
        wick_extreme = longest_wick >= max(body * 3.0, atr_value * 1.5)
        volume_extreme = float(row["volume"]) >= average_volume * 3.0
        if range_extreme and wick_extreme and volume_extreme:
            stamp = pd.Timestamp(row["date"])
            return SpikeGuardResult(
                True,
                f"扎针熔断：{timeframe}已收盘K线振幅{candle_range / atr_value:.2f} ATR、"
                f"最长影线{longest_wick / atr_value:.2f} ATR、成交量{float(row['volume']) / average_volume:.2f}倍；"
                "暂停新开仓10分钟，等待5分钟收回及1分钟结构确认",
                timeframe,
                stamp,
            )
    return SpikeGuardResult(False, "未检测到近期异常扎针")


def spike_circuit_breaker(one_minute: pd.DataFrame, five_minute: pd.DataFrame) -> SpikeGuardResult:
    """Shared pre-entry fuse for all three automatic Demo strategies.

    Ten confirmed 1m bars and two confirmed 5m bars form the same ten-minute
    lockout. Existing positions and exchange-side protection are unaffected.
    """
    one_result = _spike_in_recent(one_minute, "1分钟", 10)
    if one_result.blocked:
        return one_result
    return _spike_in_recent(five_minute, "5分钟", 2)
