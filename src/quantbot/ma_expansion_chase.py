from __future__ import annotations

import pandas as pd


def _atr(frame: pd.DataFrame, window: int = 14) -> float:
    f = frame.sort_values("date").reset_index(drop=True)
    previous = f["close"].astype(float).shift(1)
    tr = pd.concat(((f["high"] - f["low"]).abs(),
                    (f["high"] - previous).abs(),
                    (f["low"] - previous).abs()), axis=1).max(axis=1)
    return float(tr.rolling(window).mean().iloc[-1])


def dual_timeframe_ma_expansion_chase(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[int, str, float]:
    """Immediate trend-start chase after staged entries were missed.

    No pullback is required.  Both closed 1m and 5m frames must show ordered,
    widening MA5/10/20 with matching slopes.  A 2 ATR distance ceiling keeps
    the fallback from becoming unlimited late-stage chasing.
    """
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(five) < 24 or len(one) < 24:
        return 0, "双周期均线发散追单数据不足", 0.0

    def state(frame: pd.DataFrame) -> tuple[int, float, float, float]:
        close = frame["close"].astype(float)
        ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
        atr_value = _atr(frame)
        if atr_value <= 0:
            return 0, 0.0, 0.0, 0.0
        slopes = [(float(series.iloc[-1]) - float(series.iloc[-3])) / atr_value
                  for series in (ma5, ma10, ma20)]
        last = float(close.iloc[-1])
        bullish = (ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]
                   and min(slopes) > .01
                   and (ma5.iloc[-1] - ma20.iloc[-1]) / atr_value >= .18
                   and last >= ma5.iloc[-1])
        bearish = (ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1]
                   and max(slopes) < -.01
                   and (ma20.iloc[-1] - ma5.iloc[-1]) / atr_value >= .18
                   and last <= ma5.iloc[-1])
        return (1 if bullish else -1 if bearish else 0), atr_value, float(ma20.iloc[-1]), last

    five_direction, five_atr, five_ma20, five_last = state(five)
    one_direction, one_atr, _, one_last = state(one)
    if not five_direction or five_direction != one_direction:
        return 0, "等待1分钟与5分钟MA5/MA10/MA20同向发散", 0.0
    if abs(five_last - five_ma20) > five_atr * 2.0:
        return 0, "双周期已经发散但距5分钟MA20超过2.0 ATR，禁止末端追单", 0.0
    direction = five_direction
    if direction > 0:
        stop = float(one.iloc[-6:]["low"].astype(float).min()) - one_atr * .15
        reason = "三个阶段错过后的启动追涨：1分钟与5分钟MA5>MA10>MA20并同步向上发散，立即市价做多，不等待回踩"
    else:
        stop = float(one.iloc[-6:]["high"].astype(float).max()) + one_atr * .15
        reason = "三个阶段错过后的启动追空：1分钟与5分钟MA5<MA10<MA20并同步向下发散，立即市价做空，不等待反抽"
    return direction, reason, stop
