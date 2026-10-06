from __future__ import annotations

import pandas as pd


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    high, low, close = (frame[name].astype(float) for name in ("high", "low", "close"))
    true_range = pd.concat((high - low, (high - close.shift()).abs(),
                            (low - close.shift()).abs()), axis=1).max(axis=1)
    return true_range.rolling(window, min_periods=window).mean()


def _confirmed(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    if "confirm" in data:
        data = data[data["confirm"].astype(bool)]
    return data.sort_values("date").reset_index(drop=True)


def low_structure_context(five_minute: pd.DataFrame,
                          lookback: int = 12) -> tuple[float, float]:
    """Return confirmed 5m support and ATR for shared early-bottom entries."""
    five = _confirmed(five_minute)
    if len(five) < max(21, lookback):
        return 0.0, 0.0
    atr_value = float(_atr(five).iloc[-1])
    if pd.isna(atr_value) or atr_value <= 0:
        return 0.0, 0.0
    support = float(five.tail(lookback)["low"].astype(float).min())
    return support, atr_value


def high_structure_context(five_minute: pd.DataFrame,
                           lookback: int = 12) -> tuple[float, float]:
    """Return confirmed 5m resistance and ATR for shared early-top entries."""
    five = _confirmed(five_minute)
    if len(five) < max(21, lookback):
        return 0.0, 0.0
    atr_value = float(_atr(five).iloc[-1])
    if pd.isna(atr_value) or atr_value <= 0:
        return 0.0, 0.0
    resistance = float(five.tail(lookback)["high"].astype(float).max())
    return resistance, atr_value


def shared_low_sweep_reclaim_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Common executable adapter used by every strategy, not only its registry."""
    stopping, reason, stop, _ = volume_stopping_pullback_long_setup(
        five_minute, one_minute)
    if stopping:
        return True, reason, stop
    support, five_atr = low_structure_context(five_minute)
    if support <= 0 or five_atr <= 0:
        return False, "5分钟低位结构与ATR数据不足", 0.0
    return low_sweep_reclaim_long_setup(one_minute, support, five_atr)


def volume_stopping_pullback_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float, float]:
    """Uptrend pullback: 5m stopping volume plus a 1m V-reclaim.

    Both timeframes use closed candles.  High volume alone is not enough: the
    5m sell impulse must stop making lows, and the 1m recovery must also expand
    volume, break the previous high and reclaim its fast averages.
    """
    waterfall = waterfall_exhaustion_rebound_long_setup(five_minute, one_minute)
    if waterfall[0]:
        return waterfall
    five, one = _confirmed(five_minute), _confirmed(one_minute)
    if len(five) < 32 or len(one) < 25:
        return False, "双周期放量止跌数据不足", 0.0, 0.0
    close5 = five["close"].astype(float)
    ma20 = close5.rolling(20).mean()
    atr5_series = _atr(five)
    atr5 = float(atr5_series.iloc[-1])
    if pd.isna(atr5) or atr5 <= 0:
        return False, "5分钟ATR无效，不能识别放量止跌", 0.0, 0.0
    prior = five.iloc[-12:-4]
    prior_ma = ma20.iloc[-12:-4]
    prior_uptrend = (
        int((prior["close"].astype(float).to_numpy() > prior_ma.to_numpy()).sum()) >= 6
        and float(ma20.iloc[-1]) > float(ma20.iloc[-5])
    )
    if not prior_uptrend:
        return False, "5分钟原上涨趋势或上扬MA20不足", 0.0, 0.0
    volume5 = five["volume"].astype(float) if "volume" in five else pd.Series(1.0, index=five.index)
    stopping_pos: int | None = None
    for pos in range(len(five) - 2, max(20, len(five) - 6), -1):
        candle = five.iloc[pos]
        baseline = float(volume5.iloc[max(0, pos - 20):pos].mean())
        candle_range = max(float(candle["high"] - candle["low"]), 1e-9)
        lower_wick = min(float(candle["open"]), float(candle["close"])) - float(candle["low"])
        pullback = float(candle["close"]) <= float(candle["open"])
        # A deep but still structurally valid pullback may sit below MA20; the
        # target is the return to MA20, so do not require the low to touch the
        # average itself.  Reject only a collapse more than 2.5 ATR below it.
        near_ma20 = float(candle["low"]) >= float(ma20.iloc[pos]) - atr5 * 2.50
        high_volume = baseline > 0 and float(volume5.iloc[pos]) >= baseline * 1.50
        wick_or_range = lower_wick >= candle_range * .20 or candle_range >= atr5 * 1.10
        after = five.iloc[pos + 1:]
        stopped = (
            not after.empty
            and float(after["low"].astype(float).min()) >= float(candle["low"]) - atr5 * .10
            and float(after.iloc[-1]["close"]) >= float(candle["low"]) + candle_range * .15
        )
        if pullback and near_ma20 and high_volume and wick_or_range and stopped:
            stopping_pos = pos
            break
    if stopping_pos is None:
        return False, "等待5分钟回踩放量后停止破低", 0.0, 0.0

    close1 = one["close"].astype(float)
    ma5, ma10 = close1.rolling(5).mean(), close1.rolling(10).mean()
    atr1_series = _atr(one)
    volume1 = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    recovery = one.iloc[-1]
    recovery_pos = len(one) - 1
    atr1 = float(atr1_series.iloc[-1])
    baseline1 = float(volume1.iloc[-21:-1].mean())
    recovery_body = float(recovery["close"] - recovery["open"])
    recovery_range = max(float(recovery["high"] - recovery["low"]), 1e-9)
    recovery_ok = (
        baseline1 > 0
        and float(volume1.iloc[-1]) >= baseline1 * 1.20
        and recovery_body >= atr1 * .25
        and float(recovery["close"]) > float(one.iloc[-2]["high"])
        and float(recovery["close"]) > max(float(ma5.iloc[-1]), float(ma10.iloc[-1]))
        and (float(recovery["high"]) - float(recovery["close"])) / recovery_range <= .40
    )
    if not recovery_ok:
        return False, "5分钟已放量止跌，等待1分钟放量阳线形成V形并突破前高", 0.0, 0.0
    sweep_window = one.iloc[-6:-1]
    sweep_pos = int(sweep_window["low"].astype(float).idxmin())
    sweep = one.loc[sweep_pos]
    sweep_baseline = float(volume1.loc[max(0, sweep_pos - 20):sweep_pos - 1].mean())
    if sweep_baseline <= 0 or float(volume1.loc[sweep_pos]) < sweep_baseline * 1.30:
        return False, "1分钟V形低点没有放量止跌，继续等待", 0.0, 0.0
    buffer = max(atr1 * .15, float(recovery["close"]) * .0003)
    stop = float(sweep["low"]) - buffer
    target = float(ma20.iloc[-1]) + max(atr5 * .15, float(recovery["close"]) * .0003)
    risk = float(recovery["close"]) - stop
    if target <= float(recovery["close"]) or target - float(recovery["close"]) < risk * 1.5:
        return False, "双周期放量止跌已确认，但五分钟MA20上沿不足1.5R，放弃小波段多单", 0.0, 0.0
    return (
        True,
        "上涨趋势回踩出现5分钟放量止跌；1分钟放量V形收回并突破前高，限价未成交时市价做多",
        stop,
        target,
    )


def waterfall_exhaustion_rebound_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float, float]:
    """Small Demo rebound after a closed high-volume waterfall exhausts."""
    five, one = _confirmed(five_minute), _confirmed(one_minute)
    if len(five) < 32 or len(one) < 25 or "volume" not in five or "volume" not in one:
        return False, "瀑布底部反弹数据不足", 0.0, 0.0
    atr5_series, atr1_series = _atr(five), _atr(one)
    close5 = five["close"].astype(float)
    ma10_5, ma20_5 = close5.rolling(10).mean(), close5.rolling(20).mean()
    volume5, volume1 = five["volume"].astype(float), one["volume"].astype(float)
    impulse_pos: int | None = None
    for pos in range(len(five) - 2, max(20, len(five) - 6), -1):
        candle = five.iloc[pos]
        atr5 = float(atr5_series.iloc[pos])
        baseline = float(volume5.iloc[pos - 20:pos].mean())
        body = float(candle["open"] - candle["close"])
        candle_range = float(candle["high"] - candle["low"])
        capitulation = (
            atr5 > 0 and baseline > 0 and body > 0
            and float(volume5.iloc[pos]) >= baseline * 1.80
            and (body >= atr5 * .80 or candle_range >= atr5 * 1.60)
            and float(candle["close"]) <= float(ma20_5.iloc[pos]) - atr5 * .80
        )
        after = five.iloc[pos + 1:]
        stopped = (
            not after.empty
            and float(after["low"].astype(float).min()) >= float(candle["low"]) - atr5 * .15
            and float(after.iloc[-1]["close"]) >= float(candle["low"]) + candle_range * .18
        )
        if capitulation and stopped:
            impulse_pos = pos
            break
    if impulse_pos is None:
        return False, "等待五分钟瀑布放量长阴后停止有效创新低", 0.0, 0.0

    recovery = one.iloc[-1]
    atr1 = float(atr1_series.iloc[-1])
    baseline1 = float(volume1.iloc[-21:-1].mean())
    recovery_range = max(float(recovery["high"] - recovery["low"]), 1e-9)
    recovery_ok = (
        atr1 > 0 and baseline1 > 0
        and float(recovery["close"]) > float(recovery["open"])
        and float(recovery["close"] - recovery["open"]) >= atr1 * .25
        and float(volume1.iloc[-1]) >= baseline1 * 1.20
        and float(recovery["close"]) > float(one.iloc[-2]["high"])
        and (float(recovery["high"]) - float(recovery["close"])) / recovery_range <= .45
    )
    if not recovery_ok:
        return False, "五分钟瀑布已放量止跌，等待一分钟放量V形阳线突破前高", 0.0, 0.0
    sweep_window = one.iloc[-6:-1]
    sweep = sweep_window.loc[sweep_window["low"].astype(float).idxmin()]
    stop = float(sweep["low"]) - max(atr1 * .15, float(recovery["close"]) * .0003)
    target = min(float(ma10_5.iloc[-1]), float(ma20_5.iloc[-1]))
    risk = float(recovery["close"]) - stop
    reward = target - float(recovery["close"])
    if risk <= 0 or reward < risk * 1.5:
        return False, "瀑布底部V形已确认，但到五分钟MA10/MA20首个回归目标不足1.5R", 0.0, 0.0
    return True, "五分钟放量瀑布停止创新低，一分钟放量V形突破前高；限价未成交时小仓市价反弹多", stop, target


def shared_high_sweep_reject_short_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Common mirror adapter for a sweep above resistance and bearish reclaim."""
    resistance, five_atr = high_structure_context(five_minute)
    if resistance <= 0 or five_atr <= 0:
        return False, "5分钟高位结构与ATR数据不足", 0.0
    return high_sweep_reject_short_setup(one_minute, resistance, five_atr)


def low_sweep_reclaim_long_setup(entry_market: pd.DataFrame, support: float,
                                 five_atr: float) -> tuple[bool, str, float]:
    """Shared early bottom entry before slow-MA cross or MA20 reclaim."""
    one = _confirmed(entry_market)
    if len(one) < 24 or five_atr <= 0:
        return False, "低位扫损收回数据不足", 0.0
    atr1 = _atr(one)
    volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    for sweep_offset in (2, 3):
        sweep_pos = len(one) - sweep_offset
        if sweep_pos < 20:
            continue
        sweep, recovery = one.iloc[sweep_pos], one.iloc[-1]
        previous = one.iloc[sweep_pos - 10:sweep_pos]
        local_atr = float(atr1.iloc[sweep_pos])
        if pd.isna(local_atr) or local_atr <= 0:
            continue
        baseline = float(volume.iloc[sweep_pos - 20:sweep_pos].mean())
        sweep_open, sweep_close = float(sweep["open"]), float(sweep["close"])
        sweep_high, sweep_low = float(sweep["high"]), float(sweep["low"])
        sweep_range = max(sweep_high - sweep_low, 1e-9)
        lower_wick = min(sweep_open, sweep_close) - sweep_low
        new_low = sweep_low < float(previous["low"].min())
        near_support = sweep_low <= support + five_atr * .30
        exhaustion = (
            lower_wick >= local_atr * .35 or sweep_range >= local_atr * 1.35
        ) and (
            float(volume.iloc[sweep_pos]) >= baseline * 1.30 or sweep_range >= local_atr * 1.80
        )
        recovery_range = max(float(recovery["high"] - recovery["low"]), 1e-9)
        bullish_reclaim = (
            float(recovery["close"]) > float(recovery["open"])
            and float(recovery["close"] - recovery["open"]) >= local_atr * .25
            and float(recovery["close"]) >= sweep_low + sweep_range * .55
            and (float(recovery["high"]) - float(recovery["close"])) / recovery_range <= .45
        )
        if new_low and near_support and exhaustion and bullish_reclaim:
            buffer = max(local_atr * .15, float(recovery["close"]) * .0003)
            return True, "5分钟结构低位出现1分钟放量/大振幅扫损新低，随后已收盘阳线收回针体55%以上，提前做多", sweep_low - buffer
    return False, "低位已进入做多候选；等待扫损新低后的1分钟收盘阳线收回针体55%以上", 0.0


def high_sweep_reject_short_setup(entry_market: pd.DataFrame, resistance: float,
                                  five_atr: float) -> tuple[bool, str, float]:
    """Mirror of the early bottom entry: sweep a high, then close back down."""
    one = _confirmed(entry_market)
    if len(one) < 24 or five_atr <= 0:
        return False, "高位扫高回落数据不足", 0.0
    atr1 = _atr(one)
    volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    for sweep_offset in (2, 3):
        sweep_pos = len(one) - sweep_offset
        if sweep_pos < 20:
            continue
        sweep, rejection = one.iloc[sweep_pos], one.iloc[-1]
        previous = one.iloc[sweep_pos - 10:sweep_pos]
        local_atr = float(atr1.iloc[sweep_pos])
        if pd.isna(local_atr) or local_atr <= 0:
            continue
        baseline = float(volume.iloc[sweep_pos - 20:sweep_pos].mean())
        sweep_open, sweep_close = float(sweep["open"]), float(sweep["close"])
        sweep_high, sweep_low = float(sweep["high"]), float(sweep["low"])
        sweep_range = max(sweep_high - sweep_low, 1e-9)
        upper_wick = sweep_high - max(sweep_open, sweep_close)
        new_high = sweep_high > float(previous["high"].max())
        near_resistance = sweep_high >= resistance - five_atr * .30
        exhaustion = (
            upper_wick >= local_atr * .35 or sweep_range >= local_atr * 1.35
        ) and (
            float(volume.iloc[sweep_pos]) >= baseline * 1.30 or sweep_range >= local_atr * 1.80
        )
        rejection_range = max(float(rejection["high"] - rejection["low"]), 1e-9)
        bearish_reclaim = (
            float(rejection["close"]) < float(rejection["open"])
            and float(rejection["open"] - rejection["close"]) >= local_atr * .25
            and float(rejection["close"]) <= sweep_high - sweep_range * .55
            and (float(rejection["close"]) - float(rejection["low"])) / rejection_range <= .45
        )
        if new_high and near_resistance and exhaustion and bearish_reclaim:
            buffer = max(local_atr * .15, float(rejection["close"]) * .0003)
            return True, "5分钟结构高位出现1分钟放量/大振幅扫高新高，随后已收盘阴线回落针体55%以上，提前做空", sweep_high + buffer
    return False, "高位已进入做空候选；等待扫高新高后的1分钟收盘阴线回落针体55%以上", 0.0
