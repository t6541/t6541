from __future__ import annotations

import pandas as pd

from .trend_regime import classify_trend_regime


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    close = frame["close"].astype(float)
    previous = close.shift(1)
    return pd.concat(((frame["high"].astype(float) - frame["low"].astype(float)),
                      (frame["high"].astype(float) - previous).abs(),
                      (frame["low"].astype(float) - previous).abs()), axis=1).max(axis=1).rolling(window).mean()


def trend_entry_runway(five_minute: pd.DataFrame, price: float, direction: int,
                       *, max_ma20_atr: float = 1.0) -> tuple[bool, str, float]:
    """Reject a continuation entry after price is already one 5m ATR from MA20."""
    frame = five_minute.sort_values("date").reset_index(drop=True)
    if len(frame) < 21 or direction not in {-1, 1} or price <= 0:
        return False, "趋势利润空间数据不足", 0.0
    ma20 = float(frame["close"].astype(float).rolling(20).mean().iloc[-1])
    atr_value = float(_atr(frame).iloc[-1])
    if not pd.notna(atr_value) or atr_value <= 0:
        return False, "5分钟ATR无效，安全拒绝入场", 0.0
    extension = (ma20 - price) / atr_value if direction < 0 else (price - ma20) / atr_value
    if extension > max_ma20_atr:
        side = "低位追空" if direction < 0 else "高位追多"
        return False, f"价格已离5分钟MA20 {extension:.2f} ATR，剩余利润空间不足，禁止{side}", atr_value
    return True, f"距5分钟MA20 {max(extension, 0):.2f} ATR，仍有合理趋势空间", atr_value


def three_step_pullback_structure(five_minute: pd.DataFrame, direction: int) -> tuple[bool, str]:
    """Confirm three descending rebound highs (or mirrored rising pullback lows)."""
    frame = five_minute.sort_values("date").reset_index(drop=True)
    if len(frame) < 15 or direction not in {-1, 1}:
        return False, "五分钟三段结构数据不足"
    recent = frame.tail(15)
    atr_value = float(_atr(frame).iloc[-1])
    if not pd.notna(atr_value) or atr_value <= 0:
        return False, "五分钟ATR无效"
    blocks = [recent.iloc[offset:offset + 5] for offset in (0, 5, 10)]
    if direction < 0:
        points = [float(block["high"].astype(float).max()) for block in blocks]
        valid = points[0] > points[1] + atr_value * .05 and points[1] > points[2] + atr_value * .05
        detail = f"三个反抽高点依次降低：{points[0]:.2f}>{points[1]:.2f}>{points[2]:.2f}"
    else:
        points = [float(block["low"].astype(float).min()) for block in blocks]
        valid = points[0] < points[1] - atr_value * .05 and points[1] < points[2] - atr_value * .05
        detail = f"三个回踩低点依次抬高：{points[0]:.2f}<{points[1]:.2f}<{points[2]:.2f}"
    return valid, detail if valid else "五分钟三个结构点尚未连续同向"


def _pullback_continuation_setup(
    five_minute: pd.DataFrame,
    one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame,
    direction: int,
) -> tuple[bool, str, float]:
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
    side = "追空" if direction < 0 else "追多"
    if len(five) < 24 or len(fifteen) < 24 or len(one) < 24:
        return False, f"{side}数据不足", 0.0

    regime = classify_trend_regime(five, fifteen)
    opposite = "bullish" if direction < 0 else "bearish"
    higher_timeframe_waiting = regime.state == f"{opposite}_reversal_waiting_higher_timeframe"
    if (f"{opposite}_reversal" in regime.state or f"{opposite}_waiting" in regime.state) and not higher_timeframe_waiting:
        return False, f"{regime.reason}；反向反转候选/等待期间暂停{side}", 0.0

    def slow_background(frame: pd.DataFrame, *, five_frame: bool) -> tuple[bool, pd.Series, pd.Series]:
        close = frame["close"].astype(float)
        ma10, ma20 = close.rolling(10).mean(), close.rolling(20).mean()
        slope_ok = direction * (float(ma20.iloc[-1]) - float(ma20.iloc[-4])) > 0
        if direction < 0:
            near_count = int((close.tail(4) <= ma20.tail(4) * 1.0015).sum())
            ma10_ok = float(ma10.iloc[-1]) <= float(ma20.iloc[-1]) * 1.0015
            close_ok = float(close.iloc[-1]) < float(ma20.iloc[-1])
            strict_ma10 = float(ma10.iloc[-1]) < float(ma20.iloc[-1])
        else:
            near_count = int((close.tail(4) >= ma20.tail(4) * .9985).sum())
            ma10_ok = float(ma10.iloc[-1]) >= float(ma20.iloc[-1]) * .9985
            close_ok = float(close.iloc[-1]) > float(ma20.iloc[-1])
            strict_ma10 = float(ma10.iloc[-1]) > float(ma20.iloc[-1])
        valid = slope_ok and ((near_count >= 3 and ma10_ok) if five_frame else (close_ok and strict_ma10))
        return valid, ma10, ma20

    five_ok, _, _ = slow_background(five, five_frame=True)
    fifteen_ok, _, _ = slow_background(fifteen, five_frame=False)
    if not (five_ok and fifteen_ok):
        return False, f"等待5分钟与15分钟慢趋势同向（允许回踩期间MA5暂时反向）", 0.0

    stepped, stepped_reason = three_step_pullback_structure(five, direction)
    if not stepped:
        return False, stepped_reason, 0.0

    five_atr = float(_atr(five).iloc[-1])
    recent_five = five.tail(10)
    prior_extreme = (float(recent_five.iloc[:5]["high"].astype(float).max()) if direction < 0
                     else float(recent_five.iloc[:5]["low"].astype(float).min()))
    pullback_extreme = (float(recent_five.iloc[5:]["high"].astype(float).max()) if direction < 0
                        else float(recent_five.iloc[5:]["low"].astype(float).min()))
    structure_broken = (pullback_extreme > prior_extreme + five_atr * .20 if direction < 0
                        else pullback_extreme < prior_extreme - five_atr * .20)
    if structure_broken:
        structure = "次高点" if direction < 0 else "次低点"
        return False, f"趋势背景成立，但5分钟回踩已破坏{structure}结构", 0.0

    close = one["close"].astype(float)
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    latest = one.iloc[-1]
    one_atr = float(_atr(one).iloc[-1])
    confirmation_index = None
    for index in reversed(one.index[-3:]):
        position = one.index.get_loc(index)
        candle, previous = one.loc[index], one.iloc[position - 1]
        window = one.iloc[position - 5:position]
        if direction < 0:
            touched = bool(((window["high"].astype(float) >= ma10.loc[window.index] * .999)
                            | (window["high"].astype(float) >= ma20.loc[window.index] * .997)).any())
            body = float(candle["open"] - candle["close"])
            turned = body >= one_atr * .10 and float(ma5.loc[index]) <= float(ma5.iloc[position - 1]) + one_atr * .05
            rejection_wick = float(candle["close"] - candle["low"])
        else:
            touched = bool(((window["low"].astype(float) <= ma10.loc[window.index] * 1.001)
                            | (window["low"].astype(float) <= ma20.loc[window.index] * 1.003)).any())
            body = float(candle["close"] - candle["open"])
            turned = body >= one_atr * .10 and float(ma5.loc[index]) >= float(ma5.iloc[position - 1]) - one_atr * .05
            rejection_wick = float(candle["high"] - candle["close"])
        candle_range = max(float(candle["high"] - candle["low"]), 1e-9)
        if touched and turned and rejection_wick / candle_range <= .55:
            confirmation_index = index
            break
    if confirmation_index is None:
        action = "首根阴线转弱" if direction < 0 else "首根阳线转强"
        return False, f"1分钟已回抽/回踩，等待{action}", 0.0
    confirmation = one.loc[confirmation_index]
    invalid = (float(latest["close"]) > float(confirmation["high"]) + one_atr * .20 if direction < 0
               else float(latest["close"]) < float(confirmation["low"]) - one_atr * .20)
    if invalid:
        return False, "1分钟确认后已反向收复，等待新的回踩转强/转弱信号", 0.0
    runway_ok, runway_reason, _ = trend_entry_runway(five, float(latest["close"]), direction)
    if not runway_ok:
        return False, runway_reason, 0.0
    recent = one.tail(8)
    buffer = max(one_atr * .20, float(latest["close"]) * .0003)
    stop = (max(float(recent["high"].astype(float).max()), float(ma20.iloc[-1])) + buffer if direction < 0
            else min(float(recent["low"].astype(float).min()), float(ma20.iloc[-1])) - buffer)
    bars_ago = len(one) - 1 - one.index.get_loc(confirmation_index)
    structure = "次高反抽" if direction < 0 else "次低回踩"
    return True, f"{stepped_reason}；5分钟与15分钟趋势共振；{structure}后1分钟确认（{bars_ago}根前）{side}", stop


def downtrend_pullback_short_setup(five_minute: pd.DataFrame, one_minute: pd.DataFrame,
                                   fifteen_minute: pd.DataFrame) -> tuple[bool, str, float]:
    return _pullback_continuation_setup(five_minute, one_minute, fifteen_minute, -1)


def uptrend_pullback_long_setup(five_minute: pd.DataFrame, one_minute: pd.DataFrame,
                                 fifteen_minute: pd.DataFrame) -> tuple[bool, str, float]:
    """Mirror of the shared pullback short: higher-low pullback, then 1m reclaim."""
    return _pullback_continuation_setup(five_minute, one_minute, fifteen_minute, 1)


def _ma5_pullback_continuation_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame, direction: int,
) -> tuple[bool, str, float]:
    """Earlier continuation after a MA20-area pullback and closed MA5 cross.

    This is an independent queue source.  The older MA10/MA20 and three-step
    continuation remains available as a fallback.
    """
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
    side = "追空" if direction < 0 else "追多"
    if min(len(five), len(one), len(fifteen)) < 24:
        return False, f"MA5提前{side}数据不足", 0.0
    regime = classify_trend_regime(five, fifteen)
    opposite = "bullish" if direction < 0 else "bearish"
    if f"{opposite}_reversal" in regime.state and "waiting_higher_timeframe" not in regime.state:
        return False, f"{regime.reason}；暂停反向MA5提前{side}", 0.0

    five_close = five["close"].astype(float)
    fifteen_close = fifteen["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    fifteen_ma20 = fifteen_close.rolling(20).mean()
    atr5 = float(_atr(five).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0:
        return False, "5分钟ATR无效", 0.0
    five_slope = float(five_ma20.iloc[-1] - five_ma20.iloc[-4])
    fifteen_slope = float(fifteen_ma20.iloc[-1] - fifteen_ma20.iloc[-4])
    trend_ok = five_slope * direction > 0 and fifteen_slope * direction > 0
    near_five_ma20 = abs(float(five_close.iloc[-1]) - float(five_ma20.iloc[-1])) <= atr5 * .75
    if not trend_ok or not near_five_ma20:
        return False, f"等待5分钟/15分钟MA20同向且5分钟价格回到MA20附近", 0.0

    one_close = one["close"].astype(float)
    ma5 = one_close.rolling(5).mean()
    ma10 = one_close.rolling(10).mean()
    ma20 = one_close.rolling(20).mean()
    atr1 = float(_atr(one).iloc[-1])
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "1分钟ATR无效", 0.0
    # Do not confuse a true 1m trend reversal with a routine pullback.  A
    # rapidly rising MA20 together with upward MA5/MA10/MA20 expansion means
    # the former downtrend is already being repaired.  This gate is scoped to
    # the *early continuation* branch only; independent local-top reversal
    # rules retain their own evidence requirements and priority.
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr1
    ma5_ma10_gap = float(ma5.iloc[-1] - ma10.iloc[-1]) / atr1
    ma10_ma20_gap = float(ma10.iloc[-1] - ma20.iloc[-1]) / atr1
    if direction < 0:
        fast_reversal = (
            ma5_ma10_gap >= .10 and ma10_ma20_gap >= .10 and ma20_slope >= .18
        )
        if fast_reversal:
            return False, (
                f"1分钟MA20加速上行（{ma20_slope:.2f} ATR）且MA5>MA10>MA20向上发散；"
                "下跌趋势正在转为上涨，禁止按普通反抽高点提前追空，等待反转失败或重新形成下跌结构"
            ), 0.0
    else:
        fast_reversal = (
            ma5_ma10_gap <= -.10 and ma10_ma20_gap <= -.10 and ma20_slope <= -.18
        )
        if fast_reversal:
            return False, (
                f"1分钟MA20加速下行（{abs(ma20_slope):.2f} ATR）且MA5<MA10<MA20向下发散；"
                "上涨趋势正在转为下跌，禁止按普通回踩低点提前追多，等待反转失败或重新形成上涨结构"
            ), 0.0
    # Before the trigger MA5 must have counter-trend momentum and price must
    # have reached the 1m MA20 area; this prevents ordinary range MA5 noise.
    previous = one.iloc[-7:-1]
    if direction < 0:
        reached_ma20 = bool((previous["high"].astype(float) >= ma20.iloc[-7:-1].to_numpy() - atr1 * .20).any())
        counter_slope = float(ma5.iloc[-2] - ma5.iloc[-5]) > 0
    else:
        reached_ma20 = bool((previous["low"].astype(float) <= ma20.iloc[-7:-1].to_numpy() + atr1 * .20).any())
        counter_slope = float(ma5.iloc[-2] - ma5.iloc[-5]) < 0
    if not reached_ma20 or not counter_slope:
        return False, f"等待1分钟反抽/回踩MA20附近且MA5先出现反向修复", 0.0

    confirmation_index: int | None = None
    for i in range(len(one) - 1, len(one) - 4, -1):
        candle = one.iloc[i]
        ma5_slope = float(ma5.iloc[i] - ma5.iloc[i - 1]) / atr1
        if direction < 0:
            confirmed = float(candle["close"]) < float(candle["open"]) and float(candle["open"] - candle["close"]) >= atr1 * .15 and ma5_slope <= .05
        else:
            confirmed = float(candle["close"]) > float(candle["open"]) and float(candle["close"] - candle["open"]) >= atr1 * .15 and ma5_slope >= -.05
        if confirmed:
            confirmation_index = i
            break
    if confirmation_index is None:
        action = "首根阴线转弱" if direction < 0 else "首根阳线转强"
        return False, f"反抽/回踩已到MA20附近，等待1分钟{action}且MA5开始走平/转向", 0.0
    candle = one.iloc[confirmation_index]
    current_close = float(one.iloc[-1]["close"])
    signal_close = float(candle["close"])
    # A V-shaped rebound that has already reclaimed MA20 is not a normal
    # pullback continuation.  The former rule could short its first small
    # MA5 dip while MA5/MA10 were still driving upward.  Keep the ordinary
    # pullback branch intact, but after a decisive reclaim require a real
    # short-term rollover below MA10 and a non-rising MA5 (mirror for longs).
    recent_reclaim = bool((one_close.iloc[-4:] > ma20.iloc[-4:] + atr1 * .15).any())
    if recent_reclaim:
        ma5_slope_now = float(ma5.iloc[-1] - ma5.iloc[-2]) / atr1
        if direction < 0:
            rollover_confirmed = float(candle["close"]) < float(candle["open"]) and ma5_slope_now <= 0
            waiting_reason = "1分钟强势反弹后仍未出现阴线转弱且MA5走平/向下"
        else:
            rollover_confirmed = float(candle["close"]) > float(candle["open"]) and ma5_slope_now >= 0
            waiting_reason = "1分钟强势下探后仍未出现阳线转强且MA5走平/向上"
        if not rollover_confirmed:
            return False, waiting_reason, 0.0
    run = signal_close - current_close if direction < 0 else current_close - signal_close
    if run > atr1 * .35:
        return False, "MA5提前追单确认后已运行超过0.35 ATR，取消迟到追单", 0.0
    runway_ok, runway_reason, _ = trend_entry_runway(five, current_close, direction)
    if not runway_ok:
        return False, runway_reason, 0.0
    recent = one.tail(8)
    buffer = max(atr1 * .20, current_close * .0003)
    stop = (max(float(recent["high"].max()), float(ma20.iloc[-1])) + buffer if direction < 0
            else min(float(recent["low"].min()), float(ma20.iloc[-1])) - buffer)
    action = "阴线转弱，不等待下穿" if direction < 0 else "阳线转强，不等待上穿"
    bars_ago = len(one) - 1 - confirmation_index
    return True, (f"5分钟/15分钟趋势同向；1分钟反抽/回踩MA20且5分钟靠近MA20后，"
                  f"{action}MA5提前{side}（{bars_ago}根前；MA20兜底规则继续并行）"), stop


def downtrend_ma5_pullback_short_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame, fifteen_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    return _ma5_pullback_continuation_setup(five_minute, one_minute, fifteen_minute, -1)


def aggressive_double_rejection_ma5_short_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame, fifteen_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Two 1m green/red rejection pairs during an established downtrend."""
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
    if min(len(five), len(one), len(fifteen)) < 24:
        return False, "双局部高点追空数据不足", 0.0
    five_close, fifteen_close = five["close"].astype(float), fifteen["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    fifteen_ma20 = fifteen_close.rolling(20).mean()
    atr5 = float(_atr(five).iloc[-1])
    atr1 = float(_atr(one).iloc[-1])
    if not all(pd.notna(value) and value > 0 for value in (atr1, atr5)):
        return False, "双局部高点追空ATR无效", 0.0
    trend_ok = (
        float(five_ma20.iloc[-1]) < float(five_ma20.iloc[-4])
        and float(fifteen_ma20.iloc[-1]) < float(fifteen_ma20.iloc[-4])
        and float(five_close.iloc[-1]) < float(five_ma20.iloc[-1])
        and float(fifteen_close.iloc[-1]) < float(fifteen_ma20.iloc[-1])
    )
    if not trend_ok:
        return False, "等待5分钟与15分钟MA20下降且价格保持在MA20下方", 0.0
    close = one["close"].astype(float)
    ma5, ma20 = close.rolling(5).mean(), close.rolling(20).mean()
    recent = one.tail(10)
    if not bool((recent["high"].astype(float) > ma20.loc[recent.index]).any()):
        return False, "等待1分钟反抽上穿MA20形成局部高点", 0.0
    pairs: list[tuple[int, int]] = []
    start = max(20, len(one) - 10)
    for red_index in range(start + 1, len(one)):
        green_index = red_index - 1
        green, red = one.iloc[green_index], one.iloc[red_index]
        if (float(green["close"]) > float(green["open"])
                and float(red["close"]) < float(red["open"])):
            pairs.append((green_index, red_index))
    if len(pairs) < 2 or pairs[-1][1] != len(one) - 1:
        return False, "等待第二组阳线后紧跟阴线在最新收盘K线完成", 0.0
    first_pair, second_pair = pairs[-2], pairs[-1]
    first_high = float(one.iloc[first_pair[0]:first_pair[1] + 1]["high"].max())
    second_high = float(one.iloc[second_pair[0]:second_pair[1] + 1]["high"].max())
    if second_high > first_high + atr1 * .20:
        return False, "第二个局部高点明显突破第一个高点，反抽尚未失败", 0.0
    latest = one.iloc[-1]
    ma5_slope = float(ma5.iloc[-1] - ma5.iloc[-2]) / atr1
    prior_slope = float(ma5.iloc[-2] - ma5.iloc[-3]) / atr1
    body = float(latest["open"] - latest["close"])
    if not (ma5_slope <= .03 and prior_slope >= -.03 and body >= atr1 * .15):
        return False, "第二根阴线已出现，等待一分钟MA5走平并向下弯", 0.0
    runway_ok, runway_reason, _ = trend_entry_runway(
        five, float(latest["close"]), -1, max_ma20_atr=1.50)
    if not runway_ok:
        return False, runway_reason, 0.0
    stop = max(first_high, second_high) + max(atr1 * .20, float(latest["close"]) * .0003)
    return True, (
        "激进型下跌延续双局部高点追空：一分钟反抽上穿MA20后，两次阳线紧跟阴线均未创新高；"
        "第二根确认阴线收盘时MA5走平向下弯，允许在距5分钟MA20不超过1.50 ATR时追空"
    ), stop


def uptrend_ma5_pullback_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame, fifteen_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    return _ma5_pullback_continuation_setup(five_minute, one_minute, fifteen_minute, 1)
