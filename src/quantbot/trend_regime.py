from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .trade_cycle_rules import ADJACENT_BODY_COVER_MINIMUM, LOCAL_EXTREME_CANDLES

from .close_structure import closing_price_structure


@dataclass(frozen=True)
class TrendRegime:
    direction: int
    state: str
    reason: str
    confirmation_index: int | None = None
    opposite_closes: int = 0


def aggressive_three_timeframe_intrabar_ma5_reversal_setup(
    one_minute: pd.DataFrame, one_live: pd.DataFrame | None,
    five_minute: pd.DataFrame, five_live: pd.DataFrame | None,
    fifteen_minute: pd.DataFrame, fifteen_live: pd.DataFrame | None,
    *, require_fifteen: bool = True,
) -> tuple[int, str, float]:
    """Trigger when 1m/5m/15m prices hold the valid side of MA5.

    Candle colour and a fresh cross are deliberately irrelevant: the second
    or later candle remains valid while price is on the reversal side of MA5
    regardless of the lagging MA5 slope.  MA10 and MA20 are not confirmation
    gates for this independent reversal branch.
    """
    frames = [("一分钟", one_minute, one_live, 36),
              ("五分钟", five_minute, five_live, 24)]
    if require_fifteen:
        frames.append(("十五分钟", fifteen_minute, fifteen_live, 16))
    level = "三周期大波段" if require_fifteen else "双周期小波段"
    if any(len(closed) < 12 or live is None or live.empty
           for _, closed, live, _ in frames):
        return 0, f"{level}MA5盘中反转等待所需实时K线", 0.0

    states: list[tuple[str, pd.DataFrame, pd.Series, float, float, float]] = []
    for label, closed, live, lookback in frames:
        history = closed.sort_values("date").reset_index(drop=True)
        current = live.sort_values("date").iloc[-1]
        closes = history["close"].astype(float)
        previous_ma5 = float(closes.tail(5).mean())
        dynamic_ma5 = float((closes.tail(4).sum() + float(current["close"])) / 5.0)
        previous = closes.shift(1)
        true_range = pd.concat([
            history["high"].astype(float) - history["low"].astype(float),
            (history["high"].astype(float) - previous).abs(),
            (history["low"].astype(float) - previous).abs(),
        ], axis=1).max(axis=1)
        atr = float(true_range.tail(14).mean())
        if not pd.notna(atr) or atr <= 0:
            return 0, f"{level}MA5盘中反转等待{label}有效ATR", 0.0
        recent = history.tail(lookback)
        states.append((label, recent, current, previous_ma5, dynamic_ma5, atr))

    def latest_alignment(direction: int, state) -> pd.Timestamp | None:
        _, recent, current, _, _, atr = state
        combined = pd.concat([recent, current.to_frame().T], ignore_index=True)
        combined = combined.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        close = combined["close"].astype(float)
        ma5 = close.rolling(5).mean()
        latest_time = pd.Timestamp(combined.iloc[-1]["date"])
        cutoff = latest_time - pd.Timedelta(minutes=30)
        found = None
        for i in range(4, len(combined)):
            candle = combined.iloc[i]
            candle_time = pd.Timestamp(candle["date"])
            if candle_time < cutoff or pd.isna(ma5.iloc[i]) or pd.isna(ma5.iloc[i - 1]):
                continue
            close_price = float(candle["close"])
            current_ma5 = float(ma5.iloc[i])
            aligned = close_price > current_ma5 if direction > 0 else close_price < current_ma5
            if aligned:
                found = candle_time
        return found

    long_times = [latest_alignment(1, state) for state in states]
    short_times = [latest_alignment(-1, state) for state in states]

    def live_alignment(direction: int, state) -> bool:
        _, _, current, _, dynamic_ma5, _ = state
        price = float(current["close"])
        return price > dynamic_ma5 if direction > 0 else price < dynamic_ma5

    def evidence_complete(times: list[pd.Timestamp | None]) -> bool:
        if any(value is None for value in times):
            return False
        confirmed = [value for value in times if value is not None]
        return max(confirmed) - min(confirmed) <= pd.Timedelta(minutes=30)

    long_cross = evidence_complete(long_times) and all(live_alignment(1, state) for state in states)
    short_cross = evidence_complete(short_times) and all(live_alignment(-1, state) for state in states)
    if not long_cross and not short_cross:
        wait_for = "五分钟、十五分钟" if require_fifteen else "五分钟"
        return 0, (f"{level}MA5反转候选记忆30分钟：一分钟有效站到MA5反转侧先记录，等待{wait_for}"
                   "在窗口内形成同向有效站位；不限K线阴阳、不要求本根刚好穿线，各周期价格须仍在MA5有效侧，"
                   "；MA5斜率不作门槛"), 0.0
    direction = 1 if long_cross else -1
    evidence_times = long_times if direction > 0 else short_times

    one_state = states[0]
    recent_one, current_one, atr1 = one_state[1], one_state[2], one_state[5]
    compact = recent_one.tail(4)
    buffer = max(atr1 * .12, float(current_one["close"]) * .0003)
    stop = ((min(float(compact["low"].min()), float(current_one["low"])) - buffer)
            if direction > 0 else
            (max(float(compact["high"].max()), float(current_one["high"])) + buffer))
    entry = float(current_one["close"])
    if abs(entry - stop) > atr1 * 1.80:
        return 0, f"{level}MA5盘中反转已汇合，但最近一分钟局部极值止损超过1.80 ATR，放弃迟到入场", 0.0
    side = "做多" if direction > 0 else "做空"
    return direction, (
        f"激进型{level}MA5盘中汇合反转{side}：{'一分钟、五分钟、十五分钟' if require_fifteen else '一分钟、五分钟'}"
        f"在30分钟候选窗口内先后形成价格位于各自动态MA5{'上方' if direction > 0 else '下方'}的有效站位，"
        "不限K线阴阳、不要求本根刚好穿线、也不等待收盘，"
        f"MA5斜率不作门槛；不等待{'五分钟、十五分钟' if require_fifteen else '五分钟'}收盘，"
        "不检查MA10或MA20，也不要求五分钟/十五分钟仍贴近最初局部极值；"
        "止损仅放最近一分钟局部极值外，"
        f"止盈由{'一分钟MA5、五分钟MA5、十五分钟MA5逐级接管' if require_fifteen else '一分钟MA5起步、五分钟MA5接管'}；"
        f"证据时间={','.join(pd.Timestamp(value).strftime('%H:%M') for value in evidence_times if value is not None)}"
    ), stop


def aggressive_two_timeframe_intrabar_ma5_reversal_setup(
    one_minute: pd.DataFrame, one_live: pd.DataFrame | None,
    five_minute: pd.DataFrame, five_live: pd.DataFrame | None,
) -> tuple[int, str, float]:
    """Earlier small-swing tier: 1m + 5m, with 15m intentionally optional."""
    direction, _, stop = aggressive_three_timeframe_intrabar_ma5_reversal_setup(
        one_minute, one_live, five_minute, five_live, five_minute, five_live,
        require_fifteen=False,
    )
    if not direction:
        return 0, "局部双周期MA5信号等待一分钟与五分钟价格处于同一侧；价格须仍在MA5有效侧", 0.0
    side = "回踩追多" if direction > 0 else "反抽追空"
    return direction, (
        f"局部双周期MA5{side}：一分钟与五分钟价格已处于各自MA5的有效方向；"
        "不限K线阴阳，不要求本根刚好穿线，也不要求五分钟/十五分钟仍贴近最初局部极值；"
        "该信号不是三均线发散末端首仓，属于局部波段/趋势追单；"
        "止损使用最近一分钟局部极值，止盈只使用一分钟MA5拐弯，不升级到五分钟MA5"
    ), stop


def five_minute_price_structure_regime(five_minute: pd.DataFrame) -> TrendRegime:
    """Classify the trading regime from confirmed 5m closing highs and lows.

    A bullish structure requires a higher high *and* higher low; bearish is
    the exact mirror.  A missing pair, mixed swings, or a failed break that
    returns inside the prior range is a ranging / reversal-waiting state.
    MA5/10/20 remain the 1m timing evidence; they do not replace price
    structure as the primary trend definition.
    """
    structure = closing_price_structure(five_minute, "5分钟")
    if structure.state == "insufficient_data":
        return TrendRegime(0, "insufficient_data", structure.reason)
    if structure.direction > 0:
        return TrendRegime(1, "bullish_structure", f"{structure.reason}；等待1分钟回踩后重新转强")
    if structure.direction < 0:
        return TrendRegime(-1, "bearish_structure", f"{structure.reason}；等待1分钟反抽后重新转弱")
    return TrendRegime(0, "ranging_or_reversal_waiting", structure.reason)


def candidate_reversal_setup(
    five_minute: pd.DataFrame,
    one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame | None = None,
    *, block_consolidation: bool = False,
) -> tuple[int, str, float]:
    """Allow an early reversal entry inside a 5m candidate state.

    The candidate state blocks entries in the old trend, but a closed 1m
    candle may authorize a small-risk entry in the candidate direction.  This
    is intentionally stricter than merely crossing MA20 and is kept separate
    from confirmed-trend continuation statistics.
    """
    regime = classify_trend_regime(
        five_minute, fifteen_minute, block_consolidation=block_consolidation)
    fast_direction, fast_reason = _weakening_structure_candidate(five_minute)
    if regime.state not in {"bearish_reversal_candidate", "bullish_reversal_candidate"} and not fast_direction:
        return 0, regime.reason, 0.0
    direction = fast_direction or regime.direction
    failed_retest = _five_minute_ma20_failed_retest(five_minute, direction)
    consolidating, consolidation_reason = five_minute_consolidation(five_minute)
    if block_consolidation and consolidating and not failed_retest:
        return 0, f"{fast_reason or regime.reason}；{consolidation_reason}，快速反转候选只观察不下单", 0.0
    higher_timeframe_block = (
        "" if failed_retest else
        _higher_timeframe_candidate_block(fifteen_minute, direction)
    )
    if higher_timeframe_block:
        return 0, f"{fast_reason or regime.reason}；{higher_timeframe_block}", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(one) < 24:
        return 0, "反转候选区已形成，等待足够的1分钟确认数据", 0.0
    close = one["close"].astype(float)
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return 0, "反转候选区已形成，1分钟ATR不足", 0.0
    volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    retest_signal = _candidate_ma20_retest_signal(one, ma5, ma10, ma20, atr1, direction)
    # Polling can occur after the decisive 1m candle has already closed inside
    # the latest 5m impulse candle.  Search that completed five-minute window
    # newest-first instead of inspecting only the current tail candle.
    lookback = 6 if fast_direction else 3
    signal_index: int | None = None
    for i in range(len(one) - 1, max(20, len(one) - lookback) - 1, -1):
        candle = one.iloc[i]
        previous = one.iloc[i - 3:i]
        body = float(candle["close"] - candle["open"])
        candle_range = max(float(candle["high"] - candle["low"]), 1e-9)
        volume_mean = float(volume.iloc[max(0, i - 20):i].mean())
        volume_ok = volume_mean <= 0 or float(volume.iloc[i]) >= volume_mean
        one_ma_cross = (
            float(candle["open"]) > float(ma20.iloc[i]) > float(candle["close"])
            if direction < 0 else
            float(candle["open"]) < float(ma20.iloc[i]) < float(candle["close"])
        )
        if direction < 0:
            alignment = one_ma_cross or (float(ma5.iloc[i]) < float(ma10.iloc[i]) and (
                float(ma10.iloc[i]) < float(ma20.iloc[i]) or fast_direction < 0
            ))
            confirmed = (
                alignment and float(candle["close"]) < float(ma20.iloc[i])
                and body <= -atr1 * .30
                and (one_ma_cross or float(candle["close"]) < float(previous["low"].min()))
                and float(candle["close"] - candle["low"]) / candle_range <= .35
                and (volume_ok or fast_direction < 0)
            )
        else:
            alignment = one_ma_cross or (float(ma5.iloc[i]) > float(ma10.iloc[i]) and (
                float(ma10.iloc[i]) > float(ma20.iloc[i]) or fast_direction > 0
            ))
            confirmed = (
                alignment and float(candle["close"]) > float(ma20.iloc[i])
                and body >= atr1 * .30
                and (one_ma_cross or float(candle["close"]) > float(previous["high"].max()))
                and float(candle["high"] - candle["close"]) / candle_range <= .35
                and (volume_ok or fast_direction > 0)
            )
        if confirmed:
            signal_index = i
            break
    if signal_index is None:
        if retest_signal is not None:
            signal_index = retest_signal
        else:
            side = "空头" if direction < 0 else "多头"
            action = "跌破前三根低点" if direction < 0 else "突破前三根高点"
            return 0, f"{fast_reason or regime.reason}；等待1分钟{side}长实体穿越MA20，或反抽/回踩MA20失败后的二次确认", 0.0

    signal_close = float(one.iloc[signal_index]["close"])
    current_close = float(one.iloc[-1]["close"])
    adverse_run = signal_close - current_close if direction < 0 else current_close - signal_close
    current_ma20 = float(ma20.iloc[-1])
    retest_near_ma20 = abs(current_close - current_ma20) <= atr1 * .25
    retest_rejected = (
        float(one.iloc[-1]["high"]) >= current_ma20 and current_close < current_ma20
        if direction < 0 else
        float(one.iloc[-1]["low"]) <= current_ma20 and current_close > current_ma20
    )
    rearmed_after_retest = retest_near_ma20 and retest_rejected
    if adverse_run > atr1 * .35 and not rearmed_after_retest:
        return 0, f"{fast_reason or regime.reason}；1分钟确认后已运行{adverse_run / atr1:.2f} ATR，取消迟到追单", 0.0
    start = max(0, signal_index - 5)
    protection = one.iloc[start:signal_index + 1]
    buffer = max(atr1 * .15, signal_close * .0003)
    if direction < 0:
        stop = float(protection["high"].max()) + buffer
        reason = (
            "5分钟反抽MA20上穿失败、看跌反转候选优先" if failed_retest else
            "5分钟顶部降低、走弱快速候选" if fast_direction < 0 else
            "5分钟看跌反转候选优先"
        )
        detail = ("反抽1分钟MA10/MA20失败后，已收盘阴线再次跌破短结构"
                  if retest_signal == signal_index else
                  "已收盘1分钟长阴实体向下穿破1分钟MA20" if fast_direction < 0 else "1分钟强阴跌破短结构")
        rearm = "；反抽回到1分钟MA20附近后重新拒绝，候选已重置" if rearmed_after_retest else ""
        return -1, f"{reason}；{detail}{rearm}，抢先小风险做空", stop
    stop = float(protection["low"].min()) - buffer
    reason = (
        "5分钟回踩MA20下穿失败、看涨反转候选优先" if failed_retest else
        "5分钟底部抬高、走强快速候选" if fast_direction > 0 else
        "5分钟看涨反转候选优先"
    )
    detail = ("回踩1分钟MA10/MA20企稳后，已收盘阳线再次突破短结构"
              if retest_signal == signal_index else
              "已收盘1分钟长阳实体向上穿破1分钟MA20" if fast_direction > 0 else "1分钟强阳突破短结构")
    rearm = "；回踩1分钟MA20附近后重新转强，候选已重置" if rearmed_after_retest else ""
    return 1, f"{reason}；{detail}{rearm}，抢先小风险做多", stop


def _five_minute_direct_rollover_short(five_minute: pd.DataFrame) -> tuple[bool, str]:
    """Detect a rally top that rolls over before the normal waiting state appears."""
    if len(five_minute) < 24:
        return False, ""
    frame = five_minute.sort_values("date").reset_index(drop=True)
    close = frame["close"].astype(float)
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    atr5 = float(_atr(frame).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0:
        return False, ""
    recent = frame.iloc[-6:]
    latest = frame.iloc[-1]
    prior = frame.iloc[-2]
    rallied = float(recent["high"].max()) - float(recent["low"].min()) >= atr5 * 1.2
    rolled = (
        float(latest["close"]) < float(latest["open"])
        and float(latest["close"]) < float(prior["close"])
        and float(latest["close"]) < float(ma5.iloc[-1])
        and float(latest["close"]) < float(ma10.iloc[-1])
        and float(ma5.iloc[-1]) < float(ma5.iloc[-2])
        and float(latest["open"] - latest["close"]) >= atr5 * .20
    )
    prior_uptrend = bool(
        float(ma5.iloc[-3]) > float(ma10.iloc[-3]) > float(ma20.iloc[-3])
        and float(ma20.iloc[-3]) >= float(ma20.iloc[-6])
    )
    if rallied and rolled and prior_uptrend:
        return True, "5分钟原上涨排列顶部失速，最新已收盘阴线跌破MA5/MA10且MA5转下"
    return False, ""


def _five_minute_direct_rollover_long(five_minute: pd.DataFrame) -> tuple[bool, str]:
    """Mirror: detect a selloff bottom that turns up before a waiting state."""
    if len(five_minute) < 24:
        return False, ""
    frame = five_minute.sort_values("date").reset_index(drop=True)
    close = frame["close"].astype(float)
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    atr5 = float(_atr(frame).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0:
        return False, ""
    recent, latest, prior = frame.iloc[-6:], frame.iloc[-1], frame.iloc[-2]
    sold_off = float(recent["high"].max()) - float(recent["low"].min()) >= atr5 * 1.2
    turned = (
        float(latest["close"]) > float(latest["open"])
        and float(latest["close"]) > float(prior["close"])
        and float(latest["close"]) > float(ma5.iloc[-1])
        and float(latest["close"]) > float(ma10.iloc[-1])
        and float(ma5.iloc[-1]) > float(ma5.iloc[-2])
        and float(latest["close"] - latest["open"]) >= atr5 * .20
    )
    prior_downtrend = bool(
        float(ma5.iloc[-3]) < float(ma10.iloc[-3]) < float(ma20.iloc[-3])
        and float(ma20.iloc[-3]) <= float(ma20.iloc[-6])
    )
    if sold_off and turned and prior_downtrend:
        return True, "5分钟原下跌排列底部止跌，最新已收盘阳线站上MA5/MA10且MA5转上"
    return False, ""


def _direct_rollover_first_ma_cross_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame, direction: int, period: int,
) -> tuple[bool, str, float]:
    """First closed 1m MA5/MA20 cross after a direct 5m rollover."""
    rollover, rollover_reason = (
        _five_minute_direct_rollover_short(five_minute) if direction < 0
        else _five_minute_direct_rollover_long(five_minute)
    )
    if not rollover or len(one_minute) < 24:
        side = "上涨顶部直接转弱" if direction < 0 else "下跌底部直接转强"
        return False, rollover_reason or f"5分钟尚未形成{side}", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    average = close.rolling(period).mean()
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, f"{rollover_reason}；1分钟ATR不足", 0.0
    slope = float(average.iloc[-1] - average.iloc[-3]) / atr1
    slope_ok = slope <= .03 if direction < 0 else slope >= -.03
    if not slope_ok:
        trend = "上升" if direction < 0 else "下降"
        return False, f"{rollover_reason}；1分钟MA{period}仍明显{trend} {slope:.3f} ATR", 0.0
    for i in range(len(one) - 1, max(20, len(one) - 4) - 1, -1):
        candle = one.iloc[i]
        avg = float(average.iloc[i])
        prior_avg = float(average.iloc[i - 1])
        if direction < 0:
            directional = float(candle["close"]) < float(candle["open"])
            crossed = (float(one.iloc[i - 1]["close"]) >= prior_avg or float(candle["open"]) >= avg) and float(candle["close"]) < avg
            body = float(candle["open"] - candle["close"])
        else:
            directional = float(candle["close"]) > float(candle["open"])
            crossed = (float(one.iloc[i - 1]["close"]) <= prior_avg or float(candle["open"]) <= avg) and float(candle["close"]) > avg
            body = float(candle["close"] - candle["open"])
        stage_two_single = False
        stage_two_two_bulls = False
        if direction > 0 and period == 5 and directional:
            stage_two_single = (
                crossed
                and body >= atr1 * .15
            )
            if i >= 2:
                first_bull = one.iloc[i - 1]
                first_crossed_ma5 = (
                    float(first_bull["close"]) > float(first_bull["open"])
                    and float(first_bull["open"]) <= float(ma5.iloc[i - 1])
                    and float(first_bull["close"]) > float(ma5.iloc[i - 1])
                )
                stage_two_two_bulls = (
                    first_crossed_ma5
                    and float(candle["open"]) <= float(first_bull["close"]) + atr1 * .12
                    and float(candle["close"]) > float(candle["open"])
                    and float(candle["close"]) >= float(ma10.iloc[i])
                )
        stage_two_pattern = stage_two_single or stage_two_two_bulls
        ordinary_cross = directional and crossed and body >= atr1 * .15
        if ordinary_cross or stage_two_pattern:
            signal_close = float(candle["close"])
            if direction > 0 and period == 5:
                # Bottom-reversal stage 2 belongs near the micro bottom:
                # reclaim MA5/MA10 while the still-falling MA20 remains above
                # as resistance.  Once price has already reclaimed MA20 or
                # travelled far from the low, this is no longer stage 2.
                bottom_window = one.iloc[max(0, i - 19):i + 1]
                bottom_low = float(bottom_window["low"].min())
                bottom_high = float(bottom_window["high"].max())
                bottom_range = max(bottom_high - bottom_low, atr1)
                bottom_position = (signal_close - bottom_low) / bottom_range
                ma20_above = signal_close < float(ma20.iloc[i])
                if not stage_two_pattern:
                    return False, (
                        "底部反转第二阶段尚未成立：等待单根阳线覆盖前阴并收上MA5，"
                        "或连续两根阳线合力覆盖前阴且第二根到达MA10"
                    ), 0.0
                if not ma20_above:
                    return False, (
                        "底部反转第二阶段已过期：当前一分钟已站上MA20，位置进入中部/局部高点；"
                        "不再按底部MA5/MA10微型反转追多，改等第三阶段MA20首次回踩或其他独立规则"
                    ), 0.0
                if bottom_position > .45:
                    return False, (
                        f"底部反转第二阶段位置过高：当前位于近20根一分钟区间的{bottom_position:.0%}（上限45%），"
                        "禁止在中部或局部高点补做多"
                    ), 0.0
            run = signal_close - float(one.iloc[-1]["close"]) if direction < 0 else float(one.iloc[-1]["close"]) - signal_close
            if run > atr1 * .35:
                return False, f"首次穿越MA{period}后已运行超过0.35 ATR，取消迟到追单", 0.0
            protection = one.iloc[max(0, i - 5):i + 1]
            buffer = max(atr1 * .15, signal_close * .0003)
            stop = (float(protection["high"].max()) + buffer if direction < 0
                    else float(protection["low"].min()) - buffer)
            action = "阴线收盘向下穿越" if direction < 0 else "阳线收盘向上穿越"
            priority = (("底部反转第二阶段｜单根阳线站上MA5" if stage_two_single
                         else "底部反转第二阶段｜两根阳线V形收复至MA10")
                        if direction > 0 and period == 5 else
                        "第一优先" if period == 5 else "MA5未触发后的兜底")
            return True, (f"{rollover_reason}；{priority}：首次1分钟{action}MA{period}，"
                          f"MA{period}斜率{slope:.3f} ATR，独立分支排队"), stop
    action = "阴线下穿" if direction < 0 else "阳线上穿"
    return False, f"{rollover_reason}；等待首次1分钟{action}MA{period}", 0.0


def direct_rollover_first_ma5_short_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    return _direct_rollover_first_ma_cross_setup(five_minute, one_minute, -1, 5)


def direct_rollover_first_ma5_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    return _direct_rollover_first_ma_cross_setup(five_minute, one_minute, 1, 5)


def _closed_trend_background(frame: pd.DataFrame, direction: int) -> bool:
    """Require a settled directional MA20 background, not one candle colour."""
    if frame is None or len(frame) < 24:
        return False
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")
    data = frame.sort_values("date").reset_index(drop=True)
    close = data["close"].astype(float)
    ma20 = close.rolling(20).mean()
    return (direction * (float(close.iloc[-1]) - float(ma20.iloc[-1])) > 0
            and direction * (float(ma20.iloc[-1]) - float(ma20.iloc[-4])) > 0)


def _closed_downtrend_background(frame: pd.DataFrame) -> bool:
    return _closed_trend_background(frame, -1)


def _closed_uptrend_background(frame: pd.DataFrame) -> bool:
    return _closed_trend_background(frame, 1)


def aggressive_small_bottom_ma5_rebound_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Small countertrend long: long bear, doji, then a MA5-reclaiming bull.

    This deliberately applies only inside a confirmed 5m/15m downtrend.  It is
    a small-risk aggressive rebound, not a declaration that the higher trend
    has reversed.
    """
    if not (_closed_downtrend_background(five_minute)
            and _closed_downtrend_background(fifteen_minute)):
        return False, "等待五分钟、十五分钟下跌背景中的小底部反弹", 0.0
    if len(one_minute) < 24:
        return False, "小底部反弹等待足够的一分钟K线", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(period).mean() for period in (5, 10, 20))
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "小底部反弹的一分钟ATR不足", 0.0
    impulse, doji, bull = one.iloc[-3], one.iloc[-2], one.iloc[-1]
    impulse_body = float(impulse["open"] - impulse["close"])
    doji_body = abs(float(doji["close"] - doji["open"]))
    bull_body = float(bull["close"] - bull["open"])
    impulse_bear = impulse_body >= atr1 * .65
    middle_doji = doji_body <= atr1 * .25
    bottom_cluster = float(doji["low"]) <= float(impulse["low"]) + atr1 * .25
    bull_reclaim = (
        bull_body >= atr1 * .22
        and float(bull["close"]) > float(ma5.iloc[-1])
        and (float(bull["open"]) <= float(ma5.iloc[-1])
             or float(one.iloc[-2]["close"]) <= float(ma5.iloc[-2]))
    )
    ma5_turning_up = float(ma5.iloc[-1]) > float(ma5.iloc[-2])
    if not (impulse_bear and middle_doji and bottom_cluster
            and bull_reclaim and ma5_turning_up):
        return False, "等待长阴、十字星、阳线收复MA5且MA5向上拐头的小底部", 0.0
    structural_low = float(one.iloc[-5:]["low"].min())
    stop = structural_low - max(atr1 * .15, float(bull["close"]) * .0003)
    return True, (
        "激进型小底部反弹多：五分钟、十五分钟仍为下跌趋势；一分钟长阴后夹十字星，"
        "首根阳线收盘站上MA5且MA5向上拐头，小资金做多；本信号仅表示下跌中的反抽"
    ), stop


def aggressive_delayed_five_ma5_bottom_recovery_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Join varied 1m bottom evidence to a delayed 2nd-4th 5m MA5 reclaim."""
    if min(len(five_minute), len(one_minute)) < 30:
        return False, "延迟五分钟MA5底部接力等待足够K线", 0.0
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    atr5 = float(_atr(five).tail(14).mean())
    atr1 = float(_atr(one).tail(14).mean())
    if not all(pd.notna(value) and value > 0 for value in (atr1, atr5)):
        return False, "延迟五分钟MA5底部接力等待有效ATR", 0.0

    recent_one = one.tail(45).reset_index(drop=True)
    lows = recent_one["low"].astype(float).tolist()
    troughs = [(i, value) for i, value in enumerate(lows[1:-1], start=1)
               if value <= lows[i - 1] and value < lows[i + 1]]
    double_bottom = None
    for left in range(len(troughs) - 1):
        for right in range(left + 1, len(troughs)):
            first, second = troughs[left], troughs[right]
            if second[0] - first[0] < 4 or second[0] < len(recent_one) - 30:
                continue
            between_high = float(recent_one.iloc[first[0]:second[0] + 1]["high"].max())
            comparable = first[1] - atr1 * .18 <= second[1] <= first[1] + atr1 * .55
            clear_rebound = between_high - max(first[1], second[1]) >= atr1 * .55
            if comparable and clear_rebound:
                double_bottom = (first, second)
    volume = (recent_one["volume"].astype(float) if "volume" in recent_one
              else pd.Series(1.0, index=recent_one.index))
    sweep_bottom = None
    for i in range(max(10, len(recent_one) - 30), len(recent_one) - 1):
        candle = recent_one.iloc[i]
        previous_low = float(recent_one.iloc[i - 10:i]["low"].min())
        candle_range = float(candle["high"] - candle["low"])
        baseline = float(volume.iloc[max(0, i - 20):i].mean())
        new_low = float(candle["low"]) < previous_low
        exhausted = (candle_range >= atr1 * 1.35
                     or (baseline > 0 and float(volume.iloc[i]) >= baseline * 1.30))
        later = recent_one.iloc[i + 1:min(len(recent_one), i + 7)]
        reclaimed = (not later.empty and float(later["close"].max())
                     >= float(candle["low"]) + candle_range * .55)
        if new_low and exhausted and reclaimed:
            sweep_bottom = (i, float(candle["low"]))
    single_v_bottom = None
    for trough in troughs:
        later = recent_one.iloc[trough[0] + 1:min(len(recent_one), trough[0] + 9)]
        if (trough[0] >= len(recent_one) - 30 and not later.empty
                and float(later["high"].max()) - trough[1] >= atr1 * .75):
            single_v_bottom = trough
    selected_bottom = (double_bottom[1] if double_bottom is not None
                       else sweep_bottom if sweep_bottom is not None
                       else single_v_bottom)
    if selected_bottom is None:
        return False, "等待一分钟扫底收回、单V形低点、双底或较高低点回踩中的任一底部证据", 0.0

    second_index = selected_bottom[0]
    close1 = recent_one["close"].astype(float)
    ma5_1 = close1.rolling(5).mean()
    # Keep the MA5 cross as state.  After a violent sweep the cross candle can
    # arrive one or two bars before the lagging MA5 visibly flattens; requiring
    # both on that exact candle would recreate the late-entry problem.  It is
    # enough for price to keep holding above MA5 when the average turns flat/up
    # within the following four closed 1m bars.
    one_reclaimed = False
    cross_index = None
    for i in range(max(5, second_index + 1), len(recent_one)):
        if (float(close1.iloc[i - 1]) <= float(ma5_1.iloc[i - 1])
                and float(close1.iloc[i]) > float(ma5_1.iloc[i])):
            cross_index = i
        if (cross_index is not None and i - cross_index <= 4
                and float(close1.iloc[i]) >= float(ma5_1.iloc[i])
                and float(ma5_1.iloc[i]) >= float(ma5_1.iloc[i - 1])):
            one_reclaimed = True
            break
    if not one_reclaimed:
        return False, "一分钟底部证据已建立，等待底点后阳线站上MA5且MA5走平向上", 0.0

    close5 = five["close"].astype(float)
    ma5_5 = close5.rolling(5).mean()
    bullish_count = 0
    for i in range(len(five) - 1, max(-1, len(five) - 5), -1):
        if float(five.iloc[i]["close"]) > float(five.iloc[i]["open"]):
            bullish_count += 1
        else:
            break
    delayed_reclaim = (
        2 <= bullish_count <= 4
        and float(close5.iloc[-1]) > float(ma5_5.iloc[-1])
        and float(close5.iloc[-2]) <= float(ma5_5.iloc[-2])
        and float(ma5_5.iloc[-1]) > float(ma5_5.iloc[-2])
    )
    if not delayed_reclaim:
        return False, "一分钟底部证据继续保留；等待第2至第4根五分钟连续阳线完成上穿MA5", 0.0

    # The delayed confirmation must retain a compact executable stop.  Protect
    # the latest 1m higher-low/retest rather than the remote first sweep low.
    post_second = recent_one.iloc[second_index:]
    latest_pullback_low = float(post_second.tail(8)["low"].min())
    entry = float(close1.iloc[-1])
    stop = latest_pullback_low - max(atr1 * .15, entry * .0003)
    if entry - stop > atr1 * 1.50:
        return False, "五分钟延迟确认已出现，但最近一分钟回踩止损超过1.50 ATR，放弃迟到追多", 0.0
    return True, (
        "激进型错位底部接力多：一分钟扫底收回、单V形、双底或较高低点回踩证据持续保留，"
        f"第{bullish_count}根连续五分钟阳线才上穿MA5；不要求第一根阳线立即穿线，也不要求同时上穿MA10，"
        "立即使用最近一分钟回踩低点外的小止损做多"
    ), stop


def aggressive_downtrend_local_high_ma5_short_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame, one_hour: pd.DataFrame | None = None,
    *, durable_downtrend: bool = False,
) -> tuple[bool, str, float]:
    """Downtrend continuation: short the first live 1m turn at the MA5 edge."""
    higher_pair_downtrend = bool(
        one_hour is not None
        and _closed_downtrend_background(fifteen_minute)
        and _closed_downtrend_background(one_hour))
    if not (higher_pair_downtrend or durable_downtrend
            or _closed_downtrend_background(five_minute)
            or _closed_downtrend_background(fifteen_minute)):
        return False, "等待上一级五分钟或十五分钟下跌趋势", 0.0
    if len(one_minute) < 24:
        return False, "下跌局部反抽失败等待足够的一分钟K线", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(period).mean() for period in (5, 10, 20))
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "下跌局部反抽失败的一分钟ATR不足", 0.0
    green, red = one.iloc[-2], one.iloc[-1]
    fifteen_confirmed = _closed_downtrend_background(fifteen_minute)
    # In an established 5m downtrend, a shallow pullback into MA5/MA10 can
    # be the only re-entry before a waterfall.  Do not force every continuation
    # pullback to reach the slower MA20.
    # MA5 is the timing edge. Requiring MA10 too delayed the audited entry.
    touched_fast_band = float(green["high"]) >= float(ma5.iloc[-2])
    previous_swing_high = float(one.iloc[-20:-2]["high"].max())
    lower_high = float(green["high"]) <= previous_swing_high + atr1 * .12
    recent_local_high = float(one.iloc[-5:-2]["high"].max())
    at_recent_local_high = float(green["high"]) >= recent_local_high - atr1 * .20
    red_body = max(float(red["open"] - red["close"]), 0.0)
    color_turn = (
        float(green["close"]) > float(green["open"])
        and float(red["close"]) < float(red["open"])
        and red_body >= atr1 * .10
        and float(red["close"]) < float(green["close"])
    )
    # This branch owns the first weakening print at the upper MA5 edge. Once
    # price is below MA5, the precise continuation entry has already passed.
    before_ma5_break = float(red["close"]) >= float(ma5.iloc[-1])
    location_ready = (at_recent_local_high if higher_pair_downtrend
                      else touched_fast_band)
    if not (location_ready and lower_high and color_turn and before_ma5_break):
        return False, "等待MA5/MA10外沿反抽高点形成冲高转弱；不要求单根覆盖前阳线50%", 0.0
        return False, "等待下跌中一分钟反抽MA5/MA10形成较低高点，随后阴线收破MA5且MA5走平向下", 0.0
    stop = float(one.tail(3)[["open", "close"]].astype(float).max(axis=1).max()) + max(
        atr1 * .15, float(red["close"]) * .0003)
    reference = ("15分钟和1小时同步下跌，允许一分钟局部高点提前触发"
                 if higher_pair_downtrend else
                 "15分钟下降趋势已确认，授权一分钟早期反抽追空" if fifteen_confirmed
                 else "15分钟方向不作否决")
    return True, (
        "下降趋势局部高点/MA5外沿提前续空（反抽追空）：一分钟反抽形成新局部高点后，"
        "当前首根转弱阴线即提前做空，不等待下穿MA5、不要求触及MA10，也不等待五分钟收盘；"
        "不要求单根覆盖50%；止损放最近3根一分钟实体上沿外加缓冲，不取上影线最高点，止盈使用一分钟MA5拐弯；" +
        reference + (" | 15m aligned: audit only" if fifteen_confirmed
                     else " | 15m optional reference not aligned")
    ), stop
    timeframe_upgrade = (
        " | 15m aligned: upgraded three-timeframe continuation"
        if fifteen_confirmed else
        " | 15m optional reference not aligned; 1m+5m continuation remains valid"
    )
    early_half_cover_reason = (
        "下降趋势MA5外沿提前续空：反抽已到MA5/MA10上方并形成冲高转弱；"
        "不要求单根覆盖50%，不等待下穿MA5，止损放在本次反抽高点外侧 | "
    )
    return True, (early_half_cover_reason + timeframe_upgrade), stop
    early_half_cover_reason = (
        "下降趋势提前续空：反抽高点已到MA5和MA10上方，阴线实体覆盖前阳线至少一半；"
        "不等待下穿MA5，止损放在本次反抽高点外侧 | "
    )
    return True, (early_half_cover_reason +
        "激进型下跌微反抽续空：五分钟、十五分钟同步下跌；一分钟反抽MA5/MA10形成较低高点，"
        "阴线收盘重新跌破MA5且MA5走平向下，不等待反抽到MA20，使用微型高点止损追空"
        + timeframe_upgrade
    ), stop


def aggressive_uptrend_local_low_ma5_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame, one_hour: pd.DataFrame | None = None,
    *, durable_uptrend: bool = False,
) -> tuple[bool, str, float]:
    """Mirror continuation: buy the first live 1m turn at the MA5 lower edge."""
    higher_pair_uptrend = bool(
        one_hour is not None
        and _closed_uptrend_background(fifteen_minute)
        and _closed_uptrend_background(one_hour))
    if not (higher_pair_uptrend or durable_uptrend
            or _closed_uptrend_background(five_minute)
            or _closed_uptrend_background(fifteen_minute)):
        return False, "等待上一级五分钟或十五分钟上涨趋势", 0.0
    if len(one_minute) < 24:
        return False, "上涨局部回踩止跌等待足够的一分钟K线", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma5 = close.rolling(5).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "上涨局部回踩止跌的一分钟ATR不足", 0.0
    red, green = one.iloc[-2], one.iloc[-1]
    fifteen_confirmed = _closed_uptrend_background(fifteen_minute)
    touched_fast_band = float(red["low"]) <= float(ma5.iloc[-2])
    previous_swing_low = float(one.iloc[-20:-2]["low"].min())
    higher_low = float(red["low"]) >= previous_swing_low - atr1 * .12
    recent_local_low = float(one.iloc[-5:-2]["low"].min())
    at_recent_local_low = float(red["low"]) <= recent_local_low + atr1 * .20
    green_body = max(float(green["close"] - green["open"]), 0.0)
    color_turn = (
        float(red["close"]) < float(red["open"])
        and float(green["close"]) > float(green["open"])
        and green_body >= atr1 * .10
        and float(green["close"]) > float(red["close"])
    )
    # Own the first strengthening print at the lower MA5 edge.  Requiring a
    # later close above MA5 would make the long materially later than its short mirror.
    before_ma5_break = float(green["close"]) <= float(ma5.iloc[-1])
    location_ready = (at_recent_local_low if higher_pair_uptrend else touched_fast_band)
    if not (location_ready and higher_low and color_turn and before_ma5_break):
        return False, "等待MA5/MA10外沿回踩低点形成止跌转强；不要求单根覆盖前阴线50%", 0.0
    stop = float(one.tail(3)[["open", "close"]].astype(float).min(axis=1).min()) - max(
        atr1 * .15, float(green["close"]) * .0003)
    reference = ("15分钟和1小时同步上涨，允许一分钟局部低点提前触发"
                 if higher_pair_uptrend else
                 "15分钟上涨趋势已确认，授权一分钟早期回踩追多" if fifteen_confirmed
                 else "15分钟方向不作否决")
    return True, (
        "上涨趋势局部低点/MA5外沿提前续多（回踩追多）：一分钟回踩形成新局部低点后，"
        "当前首根转强阳线即提前做多，不等待上穿MA5、不要求触及MA10，也不等待五分钟收盘；"
        "不要求单根覆盖50%；止损放最近3根一分钟实体下沿外加缓冲，不取下影线最低点，止盈使用一分钟MA5拐弯；"
        + reference + (" | 15m aligned: audit only" if fifteen_confirmed
                       else " | 15m optional reference not aligned")
    ), stop


def aggressive_fifteen_minute_recovery_long_setup(
    one_minute: pd.DataFrame, five_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Recovery fallback after two *closed* 15m bulls, before slow MAs fully flip."""
    if min(len(one_minute), len(five_minute), len(fifteen_minute)) < 24:
        return False, "十五分钟恢复多等待足够的已收盘K线", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    five = five_minute.sort_values("date").reset_index(drop=True)
    fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
    atr15 = float(_atr(fifteen).tail(14).mean())
    atr5 = float(_atr(five).tail(14).mean())
    atr1 = float(_atr(one).tail(14).mean())
    if min(atr1, atr5, atr15) <= 0:
        return False, "十五分钟恢复多ATR不足", 0.0
    first, second = fifteen.iloc[-2], fifteen.iloc[-1]
    two_closed_bulls = (
        float(first["close"]) > float(first["open"])
        and float(second["close"]) > float(second["open"])
        and float(second["close"]) > float(first["close"])
        and float(second["low"]) >= float(first["low"]) - atr15 * .15
        and float((first["close"] - first["open"]) + (second["close"] - second["open"])) >= atr15 * .45
    )
    five_close = five["close"].astype(float)
    five_ma5, five_ma10 = five_close.rolling(5).mean(), five_close.rolling(10).mean()
    five_recovered = (
        float(five_ma5.iloc[-1]) > float(five_ma10.iloc[-1])
        and float(five_ma5.iloc[-1]) > float(five_ma5.iloc[-3])
        and float(five_close.iloc[-1]) > float(five_ma5.iloc[-1])
    )
    one_close = one["close"].astype(float)
    one_ma5, one_ma10 = one_close.rolling(5).mean(), one_close.rolling(10).mean()
    one_confirmed = (
        float(one_ma5.iloc[-1]) > float(one_ma10.iloc[-1])
        and float(one_ma5.iloc[-1]) > float(one_ma5.iloc[-2])
        and float(one_close.iloc[-1]) > float(one_ma5.iloc[-1])
    )
    if not (two_closed_bulls and five_recovered and one_confirmed):
        return False, "等待两根已收盘十五分钟阳线抬高、五分钟转多和一分钟再次确认", 0.0
    recent = one.tail(12)
    local_low = float(recent["low"].min())
    stop = local_low - max(atr1 * .15, float(one_close.iloc[-1]) * .0003)
    return True, (
        "激进型十五分钟恢复多：两根已收盘十五分钟阳线连续抬高，五分钟MA5上穿MA10并继续上升，"
        "一分钟MA5再次向上确认；不等待十五分钟旧均线完全翻多"
    ), stop


def aggressive_intrabar_doji_ma5_cross_long_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Enter during a live bullish MA5 cross after a closed bottom doji."""
    if len(one_minute_closed) < 24 or one_minute_live is None or one_minute_live.empty:
        return False, "盘中MA5快追等待已收盘历史和当前一分钟K线", 0.0
    closed = one_minute_closed.sort_values("date").reset_index(drop=True)
    live = one_minute_live.sort_values("date").iloc[-1]
    close = closed["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    atr1 = float(_atr(closed).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "盘中MA5快追的一分钟ATR不足", 0.0
    prior = closed.iloc[-1]
    prior_body = abs(float(prior["close"] - prior["open"]))
    recent = closed.iloc[-20:]
    local_low = float(recent["low"].min())
    local_high = float(recent["high"].max())
    local_bottom = float(prior["low"]) <= local_low + atr1 * .35
    bottom_pause = prior_body <= atr1 * .45
    ma5_already_turning = float(ma5.iloc[-1]) > float(ma5.iloc[-2])
    live_price = float(live["close"])
    live_ma5 = float((close.iloc[-4:].sum() + live_price) / 5.0)
    live_ma10 = float((close.iloc[-9:].sum() + live_price) / 10.0)
    live_ma20 = float((close.iloc[-19:].sum() + live_price) / 20.0)
    crossed_during_bar = (
        min(float(live["open"]), float(live["low"])) <= live_ma5
        and live_price >= live_ma5 + atr1 * .05
    )
    bullish_now = live_price > float(live["open"]) and live_price - float(live["open"]) >= atr1 * .12
    prior_bear = float(prior["close"]) < float(prior["open"])
    two_bull_v = False
    if len(closed) >= 2:
        first_bull = prior
        first_ma5 = float(ma5.iloc[-1])
        two_bull_v = (
            float(first_bull["close"]) > float(first_bull["open"])
            and float(first_bull["open"]) <= first_ma5
            and float(first_bull["close"]) > first_ma5
            and live_price > float(live["open"])
            and live_price >= live_ma10
        )
        if two_bull_v:
            local_bottom = float(first_bull["low"]) <= local_low + atr1 * .35
    ma20_still_above = live_price < live_ma20
    bottom_range = max(local_high - local_low, atr1)
    bottom_position = (live_price - local_low) / bottom_range
    not_late = live_price - live_ma5 <= atr1 * .45 and bottom_position <= .45
    single_ma5_reclaim = crossed_during_bar and bullish_now
    stage_two_shape = single_ma5_reclaim or two_bull_v
    pause_or_v = bottom_pause or two_bull_v or prior_bear
    if not (pause_or_v and local_bottom and ma5_already_turning
            and (crossed_during_bar or two_bull_v) and stage_two_shape and ma20_still_above
            and bullish_now and not_late):
        return False, (
            "等待底部单根阳线覆盖前阴并站上MA5，或连续两根阳线合力形成V形且第二根到达MA10；"
            "价格必须仍在MA20下方、"
            f"位于近20根区间下部45%以内（当前{bottom_position:.0%}），禁止在中部追多"
        ), 0.0
    structural_low = min(float(closed.iloc[-10:]["low"].min()), float(live["low"]))
    stop = structural_low - max(atr1 * .15, live_price * .0003)
    return True, (
        f"激进型底部反转第二阶段：{'单根阳线有效站上MA5' if single_ma5_reclaim else '连续两根阳线合力V形收复至MA10'}，"
        "价格仍在MA20下方，立即小风险追多"
    ), stop


def aggressive_range_top_doji_ma5_intrabar_short_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
    five_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Independent range-top short: bull, closed doji, live MA5 break."""
    if min(len(one_minute_closed), len(five_minute)) < 24 or one_minute_live is None or one_minute_live.empty:
        return False, "横盘高点MA5快空等待历史和当前一分钟K线", 0.0
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    live = one_minute_live.sort_values("date").iloc[-1]
    five = five_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma5 = close.rolling(5).mean()
    atr1 = float(_atr(one).tail(14).mean())
    five_close = five["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    atr5 = float(_atr(five).tail(14).mean())
    if not all(pd.notna(value) and value > 0 for value in (atr1, atr5)):
        return False, "横盘高点MA5快空等待有效ATR", 0.0

    recent = one.tail(20)
    travelled = float(recent["close"].astype(float).diff().abs().sum())
    efficiency = abs(float(recent.iloc[-1]["close"] - recent.iloc[0]["close"])) / max(travelled, 1e-9)
    five_slope = abs(float(five_ma20.iloc[-1] - five_ma20.iloc[-4])) / atr5
    range_background = efficiency <= .48 and five_slope <= .25
    doji = one.iloc[-1]
    doji_body = abs(float(doji["close"] - doji["open"]))
    prior_bull = any(float(row["close"]) > float(row["open"]) + atr1 * .08
                     for _, row in one.iloc[-4:-1].iterrows())
    local_high = float(one.iloc[-12:]["high"].max())
    doji_at_top = float(doji["high"]) >= local_high - atr1 * .12
    live_price = float(live["close"])
    live_ma5 = float((close.iloc[-4:].sum() + live_price) / 5.0)
    crossed = (max(float(live["open"]), float(live["high"])) >= live_ma5
               and live_price <= live_ma5 - atr1 * .05)
    bearish_now = live_price < float(live["open"]) and float(live["open"] - live_price) >= atr1 * .12
    # The live bearish cross itself may be the event that starts bending MA5;
    # allow a nearly-flat line instead of demanding it had already turned.
    ma5_not_rising = live_ma5 <= float(ma5.iloc[-1]) + atr1 * .10
    not_late = live_ma5 - live_price <= atr1 * .45
    if not (range_background and prior_bull and doji_body <= atr1 * .25 and doji_at_top
            and crossed and bearish_now and ma5_not_rising and not_late):
        return False, (
            f"等待横盘局部高点阳线、十字星及当前阴线盘中下穿MA5；"
            f"一分钟方向效率={efficiency:.2f}，五分钟MA20斜率={five_slope:.2f} ATR，"
            "距MA5不得超过0.45倍一分钟ATR"
        ), 0.0
    structural_high = max(local_high, float(live["high"]))
    stop = structural_high + max(atr1 * .15, live_price * .0003)
    return True, (
        "激进型横盘高点盘中快空：局部高点阳线后出现已收盘十字星，"
        "当前一分钟阴线尚未收盘但已盘中下穿MA5，立即按市价小风险做空；"
        "该分支与MA20快空及原有触发并行"
    ), stop


def top_weakening_ma5_short_trigger(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame | None = None,
) -> tuple[bool, str, float, float]:
    """Allow 1-3 bars below MA5 or a wick-below-MA5 fresh death cross."""
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    if len(one) < 21:
        return False, "顶部走弱触发等待足够K线", 0.0, 0.0
    close = one["close"].astype(float)
    ma5, ma10 = close.rolling(5).mean(), close.rolling(10).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "顶部走弱触发等待有效ATR", 0.0, 0.0
    latest = one.iloc[-1]
    # The first bearish MA5 break remains actionable for three completed bars
    # while price stays below MA5.  This prevents a 20-second polling cycle or
    # a multi-candle rollover from missing the only exact crossing candle.
    cross_age = None
    for index in range(max(1, len(one) - 3), len(one)):
        candle = one.iloc[index]
        bearish = float(candle["close"]) < float(candle["open"])
        crossed = (float(one.iloc[index - 1]["close"]) >= float(ma5.iloc[index - 1])
                   and float(candle["close"]) < float(ma5.iloc[index]))
        if bearish and crossed:
            cross_age = len(one) - 1 - index
            break
    if cross_age is not None and float(latest["close"]) < float(ma5.iloc[-1]):
        return True, f"顶部走弱后第{cross_age + 1}根K线仍在MA5下方", float(latest["close"]), float(latest["high"])
    # Second anti-miss trigger: on the fresh MA5/MA10 death cross, a wick below
    # MA5 is sufficient.  The candle body need not have closed below the line.
    closed_death_cross = (float(ma5.iloc[-2]) >= float(ma10.iloc[-2])
                          and float(ma5.iloc[-1]) < float(ma10.iloc[-1]))
    if closed_death_cross and float(latest["low"]) < float(ma5.iloc[-1]):
        return True, "一分钟新鲜小死叉且下影线已经下穿MA5", float(latest["close"]), float(latest["high"])
    if one_minute_live is not None and not one_minute_live.empty:
        live = one_minute_live.sort_values("date").iloc[-1]
        live_price = float(live["close"])
        live_ma5 = float((close.iloc[-4:].sum() + live_price) / 5.0)
        live_ma10 = float((close.iloc[-9:].sum() + live_price) / 10.0)
        live_death_cross = (float(ma5.iloc[-1]) >= float(ma10.iloc[-1])
                            and live_ma5 < live_ma10)
        if live_death_cross and float(live["low"]) < live_ma5:
            return True, "一分钟盘中新鲜小死叉且下影线已经下穿MA5", live_price, float(live["high"])
    return False, "等待顶部走弱后1至3根内价格保持MA5下方，或小死叉同步下影线下穿MA5", float(latest["close"]), float(latest["high"])


def aggressive_top_weakening_ma5_short_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Short a weakening 1m rally top on the first or renewed MA5 failure.

    This deliberately does not wait for MA20 to turn down.  A strong rally can
    remain above a rising MA20 while its successive local highs already weaken.
    The entry is allowed while MA20 is falling, flat, or only gently rising.
    """
    if len(one_minute_closed) < 30:
        return False, "顶部走弱MA5快空等待足够的一分钟K线", 0.0
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma5, ma20 = close.rolling(5).mean(), close.rolling(20).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "顶部走弱MA5快空等待有效一分钟ATR", 0.0
    # Keep the short window for the original lower-high branch, but inspect a
    # longer structural window for double tops / head-and-shoulders.  Those
    # formations commonly take 30-60 minutes and must not be reduced to the
    # last three one-bar micro peaks.
    window = one.tail(18).reset_index(drop=True)
    highs = window["high"].astype(float).tolist()
    peaks = [(i, value) for i, value in enumerate(highs[1:-1], start=1)
             if value >= highs[i - 1] and value > highs[i + 1]]
    if len(peaks) < 2:
        return False, "等待上涨冲顶后形成至少两组局部高点", 0.0
    (_, older_peak), (_, latest_peak) = peaks[-2:]
    window_ma20 = ma20.tail(18).reset_index(drop=True)
    latest_peak_body_top = max(float(window.iloc[peaks[-1][0]]["open"]),
                               float(window.iloc[peaks[-1][0]]["close"]))
    peak_retested_ma20 = latest_peak_body_top >= float(window_ma20.iloc[peaks[-1][0]])
    impulse = max(highs) - float(window["low"].astype(float).min())
    lower_high = latest_peak <= older_peak - atr1 * .05
    structural = one.tail(60).reset_index(drop=True)
    structural_highs = structural["high"].astype(float).tolist()
    structural_peaks = [
        (i, value) for i, value in enumerate(structural_highs[2:-2], start=2)
        if value > max(structural_highs[i - 2:i])
        and value >= max(structural_highs[i + 1:i + 3])
    ]
    recent_right_edge = len(structural) - 12
    double_top_pair = None
    for left in range(len(structural_peaks) - 1):
        for right in range(left + 1, len(structural_peaks)):
            first, second = structural_peaks[left], structural_peaks[right]
            if second[0] >= recent_right_edge and second[0] - first[0] >= 4 \
                    and abs(second[1] - first[1]) <= atr1 * .25:
                double_top_pair = (first, second)
    head_shoulders_triplet = None
    for left in range(len(structural_peaks) - 2):
        for middle in range(left + 1, len(structural_peaks) - 1):
            for right in range(middle + 1, len(structural_peaks)):
                shoulder_l, head, shoulder_r = (
                    structural_peaks[left], structural_peaks[middle], structural_peaks[right])
                if (shoulder_r[0] >= recent_right_edge
                        and head[0] - shoulder_l[0] >= 4
                        and shoulder_r[0] - head[0] >= 4
                        and head[1] >= shoulder_l[1] + atr1 * .15
                        and head[1] >= shoulder_r[1] + atr1 * .15
                        and abs(shoulder_l[1] - shoulder_r[1]) <= atr1 * .35):
                    head_shoulders_triplet = (shoulder_l, head, shoulder_r)
    double_top = double_top_pair is not None
    head_shoulders = head_shoulders_triplet is not None
    selected_shape = (head_shoulders_triplet if head_shoulders_triplet is not None
                      else double_top_pair)
    if selected_shape is not None:
        right_index = selected_shape[-1][0]
        structural_ma20 = ma20.tail(len(structural)).reset_index(drop=True)
        right_body_top = max(float(structural.iloc[right_index]["open"]),
                             float(structural.iloc[right_index]["close"]))
        peak_retested_ma20 = right_body_top >= float(structural_ma20.iloc[right_index])
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr1
    # This is a closed-candle structure rule, not the intrabar engulf rule.
    # It may arm as the formerly steep MA20 is decelerating, before its slope
    # reaches the stricter flat threshold used by the engulf fast entry.
    gentle_ma20 = ma20_slope <= .25
    recent_closed = one.tail(6)
    doji_count = sum(abs(float(row["close"] - row["open"])) <= atr1 * .25
                     for _, row in recent_closed.iterrows())
    shape_name = ("头肩顶" if head_shoulders else "双顶" if double_top
                  else "局部高点降低" if lower_high else "")
    weakening_shape = bool(shape_name)
    shape_filter = ((lower_high and gentle_ma20 and doji_count >= 1)
                    or ((double_top or head_shoulders) and ma20_slope <= .12))
    prepared = (impulse >= atr1 * 1.20 and weakening_shape and peak_retested_ma20
                and shape_filter)
    if not prepared:
        return False, (
            f"等待冲顶后形成较低高点、双顶或头肩顶，并由MA20走弱确认；"
            f"局部高点必须位于MA20上方；高点差={(older_peak - latest_peak) / atr1:.2f} ATR，"
            f"MA20斜率={ma20_slope:.3f} ATR，十字数={doji_count}"
        ), 0.0
    latest = one.iloc[-1]
    closed_bear = float(latest["open"] - latest["close"]) >= atr1 * .12
    closed_cross = (float(one.iloc[-2]["close"]) >= float(ma5.iloc[-2])
                    and float(latest["close"]) <= float(ma5.iloc[-1]))
    live_cross = False
    live_bear = False
    live_price = float(latest["close"])
    live_high = float(latest["high"])
    live_ma5 = float(ma5.iloc[-1])
    if one_minute_live is not None and not one_minute_live.empty:
        live = one_minute_live.sort_values("date").iloc[-1]
        live_price, live_high = float(live["close"]), float(live["high"])
        live_ma5 = float((close.iloc[-4:].sum() + live_price) / 5.0)
        live_cross = (float(live["open"]) >= live_ma5 and live_price <= live_ma5 - atr1 * .04)
        live_bear = float(live["open"] - live_price) >= atr1 * .12
    closed_ma5_bends_down = float(ma5.iloc[-1] - ma5.iloc[-2]) <= atr1 * .02
    live_ma5_bends_down = live_ma5 < float(ma5.iloc[-1])
    triggered_on = "已收盘" if closed_bear and closed_cross else "盘中"
    triggered = ((closed_bear and closed_cross and closed_ma5_bends_down)
                 or (live_cross and live_bear and live_ma5_bends_down))
    # This branch owns the first MA5 failure at a fresh top.  It must not reuse
    # the older 1-to-3-bar persistence helper: doing so lets a historical top
    # authorize a short after price has already fallen to the lower edge.
    not_late = live_ma5 - live_price <= atr1 * .50
    if not (triggered and not_late):
        return False, (
            f"顶部走弱候选已建立：{shape_name}、MA20斜率={ma20_slope:.3f} ATR；"
            "只允许当前阴线首次跌破MA5时入场，且收盘/实时价距MA5不超过0.50 ATR；"
            "旧顶部授权不得在后续下沿补追"
        ), 0.0
    # Entry risk belongs to the fresh trigger high, not the remote head of an
    # older 30-60 minute formation.  Once price has built a lower right-side
    # plateau, putting the stop above the old head destroys the early-entry
    # advantage and makes nearby support appear untradeable.
    structural_high = max(one.tail(6)["high"].astype(float).tolist() + [live_high])
    stop = structural_high + max(atr1 * .15, live_price * .0003)
    return True, (
        f"激进型顶部走弱MA5快空：上涨冲顶后形成{shape_name}，MA20走弱（{ma20_slope:.3f} ATR），"
        f"触发={triggered_on}；有效跌破MA5并令MA5走平或向下弯曲，"
        "或者小死叉下影线穿线，立即小风险做空。"
        "止损只放本轮局部高点上方；后续每次反抽失败再破MA5可独立重新评估"
    ), stop


def aggressive_five_minute_high_half_cover_short_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame | None = None,
    one_hour: pd.DataFrame | None = None,
) -> tuple[bool, str, float]:
    """Short the 1m local top after price enters MA5 and 5m covers 45%."""
    if len(five_minute) < 24 or len(one_minute) < 20:
        return False, "五分钟高位覆盖早空等待足够K线", 0.0
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    latest, prior = five.iloc[-1], five.iloc[-2]
    atr5 = float(_atr(five).tail(14).mean())
    atr1 = float(_atr(one).tail(14).mean())
    if min(atr5, atr1) <= 0 or not pd.notna(atr5) or not pd.notna(atr1):
        return False, "五分钟高位覆盖早空等待有效ATR", 0.0
    # Five-minute confirmation is only the adjacent pullback bull and current
    # bearish turn.  The up-to-six mixed-bar cluster belongs to one minute;
    # searching six five-minute bars would import a stale half-hour high.
    prior_body = float(prior["close"]) - float(prior["open"])
    bearish_overlap = max(
        0.0,
        min(float(prior["close"]), float(latest["open"]))
        - max(float(prior["open"]), float(latest["close"])),
    )
    cover_ratio = bearish_overlap / max(prior_body, 1e-9)
    if not (float(prior["close"]) > float(prior["open"])
            and float(latest["close"]) < float(latest["open"])
            and cover_ratio >= ADJACENT_BODY_COVER_MINIMUM):
        return False, f"等待五分钟阴线覆盖相邻前阳线实体45%（当前{cover_ratio:.0%}）", 0.0
    one_close = one["close"].astype(float)
    one_ma5 = float(one_close.rolling(5).mean().iloc[-1])
    one_ma5_previous = float(one_close.rolling(5).mean().iloc[-2])
    latest_one = one.iloc[-1]
    ma5_slope = one_ma5 - one_ma5_previous
    fresh_ma5_inside_weakening = (
        float(latest_one["open"]) > float(latest_one["close"])
        and float(latest_one["close"]) <= one_ma5
        and float(latest_one["high"]) >= one_ma5
        and ma5_slope <= atr1 * .02
    )
    fresh_ma5_outer_weakening = (
        float(latest_one["open"]) > float(latest_one["close"])
        and float(latest_one["close"]) >= one_ma5
        and float(latest_one["high"]) >= one_ma5
    )
    if not (fresh_ma5_inside_weakening or fresh_ma5_outer_weakening):
        return False, (
            "五分钟45%覆盖已出现，但一分钟价格尚未从外沿进入MA5内侧，"
            "或没有在MA5外沿形成当前新鲜阴线转弱"
        ), 0.0
    recent_three_high = float(one.tail(LOCAL_EXTREME_CANDLES)["high"].astype(float).max())
    earlier_high = float(one.iloc[-15:-3]["high"].astype(float).max())
    local_top = recent_three_high >= earlier_high - atr1 * .20
    higher_downtrend = bool(
        fifteen_minute is not None and one_hour is not None
        and _closed_downtrend_background(fifteen_minute)
        and _closed_downtrend_background(one_hour)
    )
    if not (local_top or higher_downtrend):
        return False, "1分钟近3根未形成局部顶部，15分钟与1小时也未同步下降", 0.0
    buffer = max(atr1 * .15, float(latest["close"]) * .0003)
    stop = recent_three_high + buffer
    identity = "15分钟+1小时下降趋势反抽追空" if higher_downtrend else "1分钟局部顶部做空"
    return True, (
        f"{identity}：1分钟价格已进入MA5内侧且MA5走平/下弯，"
        "或仍处MA5外沿的首根新鲜转弱阴线；"
        f"5分钟阴线覆盖相邻前阳线实体{cover_ratio:.0%}；"
        "止损放在最近3根1分钟K线上沿外，止盈使用1分钟MA5拐弯"
    ), stop


def aggressive_five_minute_low_half_cover_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Buy a frozen local bottom once live 5m covers half the prior bear."""
    if len(five_minute) < 24 or len(one_minute) < 20:
        return False, "five-minute low half-cover needs enough candles", 0.0
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    latest, prior = five.iloc[-1], five.iloc[-2]
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "five-minute low half-cover needs valid ATR", 0.0
    prior_mid = (float(prior["open"]) + float(prior["close"])) / 2.0
    if not (float(prior["close"]) < float(prior["open"])
            and float(latest["close"]) >= prior_mid
            and float(latest["close"]) > float(latest["open"])):
        return False, "waiting for the current five-minute bull to recover half of the adjacent pullback bear", 0.0
    buffer = max(atr1 * .15, float(latest["close"]) * .0003)
    five_local_low = min(float(prior["low"]), float(latest["low"]))
    one_cluster_low = float(one.tail(6)["low"].astype(float).min())
    stop = min(five_local_low, one_cluster_low) - buffer
    return True, (
        "the current five-minute bull recovered half of the adjacent pullback bear; "
        "an up-to-six-bar one-minute mixed cluster supplies the local-bottom location and stop"
    ), stop


def aggressive_weak_top_ma20_retest_short_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Second chance after a missed weakening-top entry: reject at 1m MA20.

    The first MA5 break may be observed too late for a compact structural stop.
    This independent retry waits for price to pull back to the now-flat/falling
    MA20 and reject it, then protects only above that fresh micro pullback high.
    """
    if len(one_minute_closed) < 32 or one_minute_live is None or one_minute_live.empty:
        return False, "顶部走弱后的MA20反抽确认等待足够一分钟历史和当前K线", 0.0
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    live = one_minute_live.sort_values("date").iloc[-1]
    close = one["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(period).mean() for period in (5, 10, 20))
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "顶部走弱后的MA20反抽确认等待有效一分钟ATR", 0.0

    recent = one.tail(18).reset_index(drop=True)
    highs = recent["high"].astype(float).tolist()
    peaks = [(i, value) for i, value in enumerate(highs[1:-1], start=1)
             if value >= highs[i - 1] and value > highs[i + 1]]
    if len(peaks) < 2:
        return False, "等待顶部形成两组局部高点后再监控MA20反抽", 0.0
    (_, older_peak), (latest_peak_index, latest_peak) = peaks[-2:]
    lower_or_equal_high = latest_peak <= older_peak + atr1 * .08
    after_peak = recent.iloc[latest_peak_index + 1:]
    first_break_confirmed = (
        not after_peak.empty
        and float(after_peak["close"].min()) <= float(ma5.iloc[-1]) - atr1 * .12
        and float(close.iloc[-1]) < float(ma10.iloc[-1])
    )
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr1
    ma20_allows = ma20_slope <= .18

    live_price = float(live["close"])
    live_ma20 = float((close.iloc[-19:].sum() + live_price) / 20.0)
    live_range = max(float(live["high"] - live["low"]), 1e-9)
    upper_wick = float(live["high"]) - max(float(live["open"]), live_price)
    touched_ma20 = float(live["high"]) >= live_ma20 - atr1 * .15
    failed_to_hold = live_price <= live_ma20 - atr1 * .03
    bearish_rejection = (
        float(live["open"] - live_price) >= atr1 * .12
        or upper_wick >= max(live_range * .30, atr1 * .15)
    )
    not_late = live_ma20 - live_price <= atr1 * .45
    if not (lower_or_equal_high and first_break_confirmed and ma20_allows
            and touched_ma20 and failed_to_hold and bearish_rejection and not_late):
        return False, (
            "顶部走弱候选继续保留：等待随后小幅回抽一分钟MA20，不能站稳后再次转阴；"
            f"当前MA20斜率={ma20_slope:.3f} ATR，距MA20={(live_ma20 - live_price) / atr1:.2f} ATR"
        ), 0.0
    micro_high = max(float(one.tail(6)["high"].max()), float(live["high"]))
    stop = micro_high + max(atr1 * .15, live_price * .0003)
    return True, (
        "激进型顶部走弱第二切入点：第一次MA5转弱做空若错过，价格随后小幅回抽一分钟MA20但未能站稳，"
        f"当前盘中再次转阴受阻（MA20斜率{ma20_slope:.3f} ATR），按新的局部回抽高点保护确认做空"
    ), stop


def aggressive_expanded_ma_top_ma5_short_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Independent short for a locally exhausted, upward MA expansion.

    A rising MA20 is deliberately not a veto here: this rule captures the
    first small-risk reversal at an extended local top, protected just above
    that top.  It is separate from all MA20-break, engulf and weakening-top
    entries.
    """
    if len(one_minute_closed) < 28:
        return False, "均线发散顶部快空等待足够一分钟K线", 0.0
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "均线发散顶部快空等待有效一分钟ATR", 0.0
    previously_expanded = (
        float(ma5.iloc[-2]) > float(ma10.iloc[-2]) > float(ma20.iloc[-2])
        and float(ma5.iloc[-2] - ma10.iloc[-2]) >= atr1 * .10
        and float(ma10.iloc[-2] - ma20.iloc[-2]) >= atr1 * .10
        and float(ma5.iloc[-2]) > float(ma5.iloc[-5])
        and float(ma10.iloc[-2]) > float(ma10.iloc[-5])
        and float(ma20.iloc[-2]) >= float(ma20.iloc[-5])
    )
    prior = one.iloc[-2]
    local_top = float(prior["high"]) >= float(one.iloc[-10:-1]["high"].max()) - atr1 * .12
    prior_bull = float(prior["close"] - prior["open"]) >= atr1 * .10
    latest = one.iloc[-1]
    closed_cross = (
        float(latest["open"] - latest["close"]) >= atr1 * .12
        and float(prior["close"]) >= float(ma5.iloc[-2])
        and float(latest["close"]) <= float(ma5.iloc[-1])
    )
    live_cross = False
    live_price, live_high, live_ma5 = float(latest["close"]), float(latest["high"]), float(ma5.iloc[-1])
    if one_minute_live is not None and not one_minute_live.empty:
        live = one_minute_live.sort_values("date").iloc[-1]
        live_price, live_high = float(live["close"]), float(live["high"])
        live_ma5 = float((close.iloc[-4:].sum() + live_price) / 5.0)
        live_cross = (
            float(live["open"] - live_price) >= atr1 * .12
            and float(live["open"]) >= live_ma5 and live_price <= live_ma5 - atr1 * .04
        )
    not_late = live_ma5 - live_price <= atr1 * 1.20
    if not (previously_expanded and local_top and prior_bull and (closed_cross or live_cross) and not_late):
        return False, "等待MA5>MA10>MA20向上发散、局部高点阳线转阴并盘中或收盘跌破MA5", 0.0
    recent_body_top = float(one.tail(3)[["open", "close"]].astype(float).max(axis=1).max())
    if one_minute_live is not None and not one_minute_live.empty:
        live_body_top = max(float(live["open"]), live_price)
        recent_body_top = max(recent_body_top, live_body_top)
    stop = recent_body_top + max(atr1 * .15, live_price * .0003)
    phase = "收盘" if closed_cross else "盘中"
    return True, (
        f"激进型均线发散顶部快空：三条均线原本向上发散，局部高点阳线转阴，"
        f"当前阴线{phase}跌破MA5；止损放最近3根一分钟实体上沿外加缓冲，不取上影线最高点，按MA5下滑规则止盈"
    ), stop


def aggressive_local_top_intrabar_bear_engulf_short_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Independent fast short when a live red body engulfs the prior bull."""
    if len(one_minute_closed) < 24 or one_minute_live is None or one_minute_live.empty:
        return False, "局部高点盘中阴线吞没等待历史和当前一分钟K线", 0.0
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    live = one_minute_live.sort_values("date").iloc[-1]
    close = one["close"].astype(float)
    ma20 = close.rolling(20).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "局部高点盘中阴线吞没等待有效ATR", 0.0
    bull = one.iloc[-1]
    bull_body = float(bull["close"] - bull["open"])
    bull_length = float(bull["high"] - bull["low"])
    local_high = float(one.iloc[-12:]["high"].max())
    at_local_top = float(bull["high"]) >= local_high - atr1 * .12
    above_ma20_top = float(bull["high"]) >= float(ma20.iloc[-1])
    live_body = float(live["open"] - live["close"])
    bearish_now = live_body > 0 and float(live["close"]) < float(bull["open"])
    starts_near_bull_top = float(live["open"]) >= float(bull["close"]) - atr1 * .12
    fully_engulfed = live_body >= max(bull_length, bull_body, atr1 * .18)
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr1
    ma20_allows_short = ma20_slope <= .08
    if not (bull_body >= atr1 * .10 and at_local_top and above_ma20_top and bearish_now
            and starts_near_bull_top and fully_engulfed and ma20_allows_short):
        ratio = live_body / max(bull_length, 1e-9)
        return False, (
            f"等待阳线触及/站上一分钟MA20形成局部高点后，当前未收盘阴线完整覆盖前阳线；"
            f"当前下跌实体为前阳线全长{ratio:.2f}倍；"
            f"一分钟MA20斜率={ma20_slope:.3f} ATR，明显上涨时禁止做空"
        ), 0.0
    stop = max(local_high, float(live["high"])) + max(atr1 * .15, float(live["close"]) * .0003)
    return True, (
        "激进型局部高点盘中吞没快空：前一根已收盘阳线位于局部高点，"
        "当前一分钟阴线尚未收盘但下跌实体已经超过前阳线全部长度并跌破其开盘价，"
        f"一分钟MA20下降、基本走平或仅微微上扬（斜率{ma20_slope:.3f} ATR），"
        "立即按市价小风险做空；该分支与所有旧规则并行"
    ), stop


def aggressive_first_ma20_retest_intrabar_long_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
    five_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Stage three: buy the first live hold/rebound above MA20 after reclaim."""
    if min(len(one_minute_closed), len(five_minute)) < 24 or one_minute_live is None or one_minute_live.empty:
        return False, "第三阶段等待一分钟MA20历史、当前K线和五分钟背景", 0.0
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    live = one_minute_live.sort_values("date").iloc[-1]
    five = five_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma20 = close.rolling(20).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "第三阶段一分钟ATR不足", 0.0
    crosses = [i for i in range(max(20, len(one) - 20), len(one))
               if float(close.iloc[i - 1]) <= float(ma20.iloc[i - 1])
               and float(close.iloc[i]) > float(ma20.iloc[i])]
    live_price = float(live["close"])
    live_ma20 = float((close.iloc[-19:].sum() + live_price) / 20.0)
    live_body = live_price - float(live["open"])
    live_range = max(float(live["high"] - live["low"]), 1e-9)
    live_breaks_ma20 = (
        float(live["open"]) <= live_ma20
        and live_price >= live_ma20 + atr1 * .08
        and live_body >= atr1 * .25
        and live_price - live_ma20 <= atr1 * .45
    )
    if not crosses and live_breaks_ma20:
        recent_low = float(one.iloc[-12:]["low"].min())
        stop = min(recent_low, float(live["low"])) - max(atr1 * .15, live_price * .0003)
        return True, (
            "激进型底部反转第三阶段突破：当前尚未收盘一分钟阳线首次有效上穿MA20，"
            "实体达到0.25倍ATR且未远离MA20，立即小风险追多；后续首次回踩仍可独立接力"
        ), stop
    if not crosses:
        return False, "第三阶段等待一分钟首次有效上穿MA20，随后继续监控第一次回踩", 0.0
    cross_index = crosses[-1]
    # Any completed touch after the reclaim means the first retest is gone.
    for i in range(cross_index + 1, len(one)):
        if float(one.iloc[i]["low"]) <= float(ma20.iloc[i]) + atr1 * .15:
            return False, "MA20第一次回踩已经完成，不重复追多", 0.0
    candle_range = live_range
    touched = float(live["low"]) <= live_ma20 + atr1 * .15
    held_above = live_price >= live_ma20 + atr1 * .03 and float(live["low"]) >= live_ma20 - atr1 * .12
    rebounding = (live_price > float(live["open"])
                  or (live_price - float(live["low"])) / candle_range >= .55)
    ma20_not_falling = live_ma20 >= float(ma20.iloc[-1])
    five_close = five["close"].astype(float)
    five_ma5 = five_close.rolling(5).mean()
    five_ma5_rising = (float(five_ma5.iloc[-1]) > float(five_ma5.iloc[-2])
                       and float(five_close.iloc[-1]) >= float(five_ma5.iloc[-1]))
    if not (touched and held_above and rebounding and ma20_not_falling and five_ma5_rising):
        return False, "等待MA20上方第一次回踩守住并回升，且五分钟MA5继续向上", 0.0
    stop = float(live["low"]) - max(atr1 * .15, live_price * .0003)
    return True, (
        "激进型第三阶段追多：一分钟已经站上MA20，当前尚未收盘K线第一次回踩MA20守住并回升，"
        "五分钟MA5继续向上，按市价追多"
    ), stop


def aggressive_intrabar_ma20_break_short_setup(
    one_minute_closed: pd.DataFrame, one_minute_live: pd.DataFrame,
    five_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Aggressive mirror: sell a live 1m break of a flat/turning MA20.

    This deliberately does not wait for the current one-minute candle to
    close.  It is restricted to a bearish transition at a local rally top and
    refuses a late chase once price is more than 0.45 one-minute ATR below
    MA20.
    """
    if min(len(one_minute_closed), len(five_minute)) < 24 or one_minute_live is None or one_minute_live.empty:
        return False, "盘中MA20快空等待一分钟历史、当前K线和五分钟背景", 0.0
    one = one_minute_closed.sort_values("date").reset_index(drop=True)
    live = one_minute_live.sort_values("date").iloc[-1]
    five = five_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma20 = close.rolling(20).mean()
    atr1 = float(_atr(one).tail(14).mean())
    five_close = five["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    atr5 = float(_atr(five).tail(14).mean())
    if not all(pd.notna(value) and value > 0 for value in (atr1, atr5)):
        return False, "盘中MA20快空等待有效ATR", 0.0

    one_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr1
    five_slope = float(five_ma20.iloc[-1] - five_ma20.iloc[-3]) / atr5
    recent = one.iloc[-4:]
    had_bull_then_bears = any(
        float(recent.iloc[i]["close"]) > float(recent.iloc[i]["open"])
        and all(float(recent.iloc[j]["close"]) < float(recent.iloc[j]["open"])
                for j in range(i + 1, len(recent)))
        for i in range(len(recent) - 1)
    )
    local_top = float(recent["high"].max()) >= float(one.iloc[-12:]["high"].max()) - atr1 * .15
    live_price = float(live["close"])
    live_open = float(live["open"])
    live_ma20 = float((close.iloc[-19:].sum() + live_price) / 20.0)
    crossed_now = live_open >= live_ma20 and live_price <= live_ma20 - atr1 * .03
    continued_below = (live_open <= live_ma20 + atr1 * .10
                       and live_price <= live_ma20 - atr1 * .05)
    bearish_now = live_price < live_open and live_open - live_price >= atr1 * .10
    not_late = live_ma20 - live_price <= atr1 * .45
    five_not_strongly_rising = five_slope <= .05
    if not (one_slope <= .03 and five_not_strongly_rising and had_bull_then_bears
            and local_top and bearish_now and (crossed_now or continued_below) and not_late):
        return False, (
            f"等待顶部阳转阴后当前一分钟盘中下穿/保持MA20下方；"
            f"1分钟MA20斜率={one_slope:.3f} ATR，5分钟MA20斜率={five_slope:.3f} ATR，"
            "距MA20不得超过0.45倍一分钟ATR"
        ), 0.0
    structural_high = max(float(one.iloc[-12:]["high"].max()), float(live["high"]))
    stop = structural_high + max(atr1 * .15, live_price * .0003)
    phase = "盘中向下穿过MA20" if crossed_now else "开线后继续运行在MA20下方"
    return True, (
        f"激进型盘中MA20快空：顶部阳线后已出现阴线转弱，1分钟MA20走平转下，"
        f"当前一分钟尚未收盘但已{phase}，且未远离MA20超过0.45 ATR，立即以局部小止损市价做空"
    ), stop


def _timeframe_stage(frame: pd.DataFrame) -> str:
    """Describe one timeframe as base/start, expansion, exhaustion, or reversal."""
    if frame is None or len(frame) < 24:
        return "数据不足"
    data = frame.sort_values("date").reset_index(drop=True)
    close = data["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(n).mean() for n in (5, 10, 20))
    atr_value = float(_atr(data).iloc[-1])
    spread = abs(float(ma5.iloc[-1] - ma20.iloc[-1])) / max(atr_value, 1e-9)
    direction = 1 if ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] else (
        -1 if ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] else 0)
    latest = data.iloc[-1]
    counter = (direction > 0 and float(latest["close"]) < float(latest["open"])) or (
        direction < 0 and float(latest["close"]) > float(latest["open"]))
    if direction and counter and abs(float(latest["close"] - latest["open"])) >= atr_value * .25:
        return "顶部/底部衰竭反转"
    if direction and spread >= .65:
        return "趋势加速发散"
    if direction:
        return "趋势启动/延续"
    return "横盘蓄势/等待"


def four_stage_market_description(one_minute: pd.DataFrame, five_minute: pd.DataFrame,
                                  fifteen_minute: pd.DataFrame | None = None,
                                  thirty_minute: pd.DataFrame | None = None,
                                  one_hour: pd.DataFrame | None = None,
                                  four_hour: pd.DataFrame | None = None) -> str:
    """Six-timeframe view from macro environment down to execution timing."""
    return (f"六周期框架｜4小时:{_timeframe_stage(four_hour)}｜"
            f"1小时:{_timeframe_stage(one_hour)}｜"
            f"30分钟:{_timeframe_stage(thirty_minute)}｜"
            f"15分钟:{_timeframe_stage(fifteen_minute)}｜"
            f"5分钟:{_timeframe_stage(five_minute)}｜1分钟:{_timeframe_stage(one_minute)}")


def top_color_reversal_short_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame | None = None,
    thirty_minute: pd.DataFrame | None = None,
    one_hour: pd.DataFrame | None = None,
    four_hour: pd.DataFrame | None = None,
) -> tuple[bool, str, float]:
    """Earliest top short: first closed red candle after a green top candle.

    This is deliberately independent from MA5/MA20 crossings.  Location,
    rejection strength and a structural stop replace the old MA-slope gate.
    """
    if len(one_minute) < 24 or len(five_minute) < 24:
        return False, "顶部颜色反转等待足够K线", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    five = five_minute.sort_values("date").reset_index(drop=True)
    latest, prior, first = one.iloc[-1], one.iloc[-2], one.iloc[-3]
    atr1 = float(_atr(one).tail(14).mean())
    atr5 = float(_atr(five).iloc[-1])
    if not pd.notna(atr1) or not pd.notna(atr5) or atr1 <= 0 or atr5 <= 0:
        return False, "顶部颜色反转ATR不足", 0.0
    prior_green = float(first["close"]) > float(first["open"])
    latest_red = (float(prior["close"]) < float(prior["open"])
                  and float(latest["close"]) < float(latest["open"]))
    local_high = float(one.iloc[-13:-1]["high"].max())
    at_top = float(first["high"]) >= local_high - atr1 * .10
    five_low, five_high = float(five.tail(12)["low"].min()), float(five.tail(12)["high"].max())
    five_location = ((float(first["high"]) - five_low) / max(five_high - five_low, 1e-9))
    fifteen_location = 0.0
    if fifteen_minute is not None and len(fifteen_minute) >= 12:
        fifteen = fifteen_minute.sort_values("date").tail(12)
        lo15, hi15 = float(fifteen["low"].min()), float(fifteen["high"].max())
        fifteen_location = (float(first["high"]) - lo15) / max(hi15 - lo15, 1e-9)
    body = float(latest["open"] - latest["close"])
    rejection = body >= atr1 * .18 and float(latest["close"]) < float(prior["close"])
    volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    volume_mean = float(volume.iloc[-22:-2].mean())
    evidence = (body >= atr1 * .30 or volume_mean <= 0
                or float(volume.iloc[-1]) >= volume_mean * .90
                or float(latest["low"]) < float(prior["low"]))
    short_break = float(latest["close"]) < min(float(prior["low"]), float(first["low"]))
    if not (prior_green and latest_red and short_break and at_top
            and five_location >= .72 and fifteen_location >= .65 and rejection and evidence):
        return False, "等待顶部绿转红首阴线及5分钟上部结构确认", 0.0
    stop = max(float(first["high"]), float(prior["high"]), float(latest["high"]), local_high) + max(atr1 * .15, float(latest["close"]) * .0003)
    return True, (
        f"顶部颜色反转第一触发：前一根1分钟阳线创局部高点，紧随已收盘阴线转弱；"
        f"即使仍在MA5上方且MA20向上也允许小风险抢先做空｜"
        f"{four_stage_market_description(one, five, fifteen_minute, thirty_minute, one_hour, four_hour)}"
    ), stop


def bottom_color_reversal_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame | None = None,
    thirty_minute: pd.DataFrame | None = None,
    one_hour: pd.DataFrame | None = None,
    four_hour: pd.DataFrame | None = None,
) -> tuple[bool, str, float]:
    """Exact mirrored early bottom-long setup."""
    five_closed = five_minute.sort_values("date").reset_index(drop=True)
    if "confirm" in five_closed:
        five_closed = five_closed[five_closed["confirm"].astype(bool)].reset_index(drop=True)
    if len(five_closed) < 5:
        return False, "底部早多等待足够的已收盘5分钟K线", 0.0
    prior_low = float(five_closed.iloc[-5:-2]["low"].astype(float).min())
    recent_low = float(five_closed.iloc[-2:]["low"].astype(float).min())
    if recent_low < prior_low:
        return False, "底部早多仍在5分钟创新低，第一根颜色反转只记录候选", 0.0
    mirrored_one = one_minute.copy()
    mirrored_five = five_minute.copy()
    mirrored_fifteen = fifteen_minute.copy() if fifteen_minute is not None else None
    mirrored_thirty = thirty_minute.copy() if thirty_minute is not None else None
    mirrored_hour = one_hour.copy() if one_hour is not None else None
    mirrored_four_hour = four_hour.copy() if four_hour is not None else None
    for frame in (mirrored_one, mirrored_five, mirrored_fifteen, mirrored_thirty, mirrored_hour, mirrored_four_hour):
        if frame is None:
            continue
        old_open, old_high, old_low, old_close = (frame[c].astype(float).copy() for c in ("open", "high", "low", "close"))
        frame["open"], frame["high"], frame["low"], frame["close"] = -old_open, -old_low, -old_high, -old_close
    active, reason, mirrored_stop = top_color_reversal_short_setup(
        mirrored_five, mirrored_one, mirrored_fifteen, mirrored_thirty, mirrored_hour, mirrored_four_hour)
    if not active:
        return False, reason.replace("顶部", "底部").replace("做空", "做多"), 0.0
    return True, reason.replace("顶部", "底部").replace("阳线创局部高点", "阴线创局部低点").replace("阴线转弱", "阳线转强").replace("做空", "做多"), -mirrored_stop


def direct_rollover_first_ma20_long_setup(
    five_minute: pd.DataFrame, one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    return _direct_rollover_first_ma_cross_setup(five_minute, one_minute, 1, 20)


def direct_rollover_first_ma20_short_setup(
    five_minute: pd.DataFrame,
    one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Independent first-cross short branch for a direct 5m top rollover."""
    rollover, rollover_reason = _five_minute_direct_rollover_short(five_minute)
    if not rollover or len(one_minute) < 24:
        return False, rollover_reason or "5分钟尚未形成上涨顶部直接转弱", 0.0
    one = one_minute.sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma20 = close.rolling(20).mean()
    atr1 = float(_atr(one).tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return False, "顶部直接转弱已出现，但1分钟ATR不足", 0.0
    slope = float(ma20.iloc[-1] - ma20.iloc[-3]) / atr1
    if slope > .02:
        return False, f"顶部直接转弱已出现，但1分钟MA20仍上升 {slope:.3f} ATR", 0.0
    volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    for i in range(len(one) - 1, max(20, len(one) - 6) - 1, -1):
        candle = one.iloc[i]
        previous = one.iloc[i - 3:i]
        body = float(candle["close"] - candle["open"])
        average = float(ma20.iloc[i])
        volume_mean = float(volume.iloc[max(0, i - 20):i].mean())
        volume_ok = volume_mean <= 0 or float(volume.iloc[i]) >= volume_mean
        closed_break = float(candle["open"]) >= average and float(candle["close"]) < average
        wick_break = (
            float(candle["low"]) < average
            and float(candle["close"]) <= average + atr1 * .10
            and volume_ok
        )
        if body <= -atr1 * .30 and (closed_break or wick_break):
            signal_close = float(candle["close"])
            if signal_close - float(one.iloc[-1]["close"]) > atr1 * .35:
                return False, "首次下穿确认后已下跌超过0.35 ATR，取消迟到追空", 0.0
            protection = one.iloc[max(0, i - 5):i + 1]
            stop = float(protection["high"].max()) + max(atr1 * .15, signal_close * .0003)
            confirmation = "收盘下穿MA20" if closed_break else "影线下穿且收盘紧贴MA20上方"
            return True, (
                f"{rollover_reason}；首次已收盘1分钟阴线{confirmation}，"
                f"1分钟MA20斜率{slope:.3f} ATR，独立分支排队做空"
            ), stop
    return False, f"{rollover_reason}；等待首次1分钟阴线有效下穿MA20", 0.0


def three_bear_ma20_rollover_short_setup(
    five_minute: pd.DataFrame,
    one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Confirm an early short with three weakening closed 1m candles.

    This is stricter than a single MA20 cross: all three candles must be
    bearish with descending highs, the first must close through MA20, the next
    two must remain below it, and both 1m/5m MA20 must be flat-to-falling.
    """
    if len(five_minute) < 24 or len(one_minute) < 24:
        return False, "三阴转跌等待足够的已收盘K线", 0.0
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    five_close = five["close"].astype(float)
    one_close = one["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    one_ma20 = one_close.rolling(20).mean()
    atr5 = float(_atr(five).tail(14).mean())
    atr1 = float(_atr(one).tail(14).mean())
    if not all(pd.notna(value) and value > 0 for value in (atr1, atr5)):
        return False, "三阴转跌等待有效ATR", 0.0
    one_slope = float(one_ma20.iloc[-1] - one_ma20.iloc[-4]) / atr1
    five_slope = float(five_ma20.iloc[-1] - five_ma20.iloc[-3]) / atr5
    if one_slope > .03 or five_slope > .05:
        return False, (
            f"三阴转跌尚未成立：1分钟MA20斜率={one_slope:.3f} ATR，"
            f"5分钟MA20斜率={five_slope:.3f} ATR"
        ), 0.0
    candles = one.iloc[-3:]
    bearish = all(float(row["close"]) < float(row["open"]) for _, row in candles.iterrows())
    highs = candles["high"].astype(float).tolist()
    weakening_highs = highs[0] > highs[1] > highs[2]
    first = candles.iloc[0]
    first_cross = (
        float(first["open"]) >= float(one_ma20.iloc[-3])
        and float(first["close"]) < float(one_ma20.iloc[-3])
    )
    held_below = all(
        float(one.iloc[i]["close"]) < float(one_ma20.iloc[i])
        for i in range(len(one) - 3, len(one))
    )
    cumulative_body = sum(
        float(row["open"] - row["close"]) for _, row in candles.iterrows()
    )
    five_supports = float(five_close.iloc[-1]) < float(five_ma20.iloc[-1]) + atr5 * .10
    if not (bearish and weakening_highs and first_cross and held_below
            and cumulative_body >= atr1 * .45 and five_supports):
        return False, "等待三个降低高点的阴线：首阴实体下穿MA20，后两阴收盘持续位于MA20下方", 0.0
    protection = one.iloc[-8:]
    stop = float(protection["high"].astype(float).max()) + max(
        atr1 * .15, float(candles.iloc[-1]["close"]) * .0003)
    return True, (
        "一分钟三阴确认转跌：三个阴线高点依次降低，首阴实体下穿MA20，"
        "后两阴收盘持续位于MA20下方；1分钟MA20走平转下且5分钟MA20未上升，"
        "第三根确认阴线附近抢先小风险做空"
    ), stop


def aggressive_first_bear_ma20_short_setup(
    five_minute: pd.DataFrame,
    one_minute: pd.DataFrame,
) -> tuple[bool, str, float]:
    """Aggressive-only first closed bearish body through a flattening MA20."""
    if len(five_minute) < 24 or len(one_minute) < 24:
        return False, "激进型首阴下穿等待足够的已收盘K线", 0.0
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    five_close = five["close"].astype(float)
    one_close = one["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    one_ma20 = one_close.rolling(20).mean()
    atr5 = float(_atr(five).tail(14).mean())
    atr1 = float(_atr(one).tail(14).mean())
    if not all(pd.notna(value) and value > 0 for value in (atr1, atr5)):
        return False, "激进型首阴下穿等待有效ATR", 0.0
    one_slope = float(one_ma20.iloc[-1] - one_ma20.iloc[-4]) / atr1
    five_slope = float(five_ma20.iloc[-1] - five_ma20.iloc[-3]) / atr5
    candle = one.iloc[-1]
    average = float(one_ma20.iloc[-1])
    body = float(candle["open"] - candle["close"])
    crossed = float(candle["open"]) >= average and float(candle["close"]) < average
    five_supports = float(five_close.iloc[-1]) <= float(five_ma20.iloc[-1]) + atr5 * .10
    if not (one_slope <= .03 and five_slope <= .05 and crossed
            and body >= atr1 * .20 and five_supports):
        return False, (
            f"等待首根有效阴线实体下穿1分钟MA20；当前1分钟MA20斜率={one_slope:.3f} ATR，"
            f"5分钟MA20斜率={five_slope:.3f} ATR"
        ), 0.0
    protection = one.iloc[-10:]
    stop = float(protection["high"].astype(float).max()) + max(
        atr1 * .15, float(candle["close"]) * .0003)
    return True, (
        "激进型首阴下穿MA20早空：第一根已收盘阴线实体下穿1分钟MA20，"
        "1分钟MA20走平转下且5分钟MA20未上升；止损置于本轮局部高点上方"
    ), stop


def _five_minute_ma20_failed_retest(
    five_minute: pd.DataFrame,
    candidate_direction: int,
) -> bool:
    """Recognize a closed 5m MA20 rejection before overriding the 15m guard.

    A 1m MA20 cross alone is deliberately insufficient.  The newest closed 5m
    candle must itself reject MA20 with a meaningful body, finish on the
    candidate side of MA5/MA10/MA20, and close near its directional extreme.
    """
    if not candidate_direction or len(five_minute) < 24:
        return False
    frame = five_minute.sort_values("date").reset_index(drop=True)
    close = frame["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    atr5 = float(_atr(frame).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0:
        return False
    candle = frame.iloc[-1]
    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close_price = float(candle["close"])
    average = float(ma20.iloc[-1])
    candle_range = max(high - low, 1e-9)
    if candidate_direction < 0:
        return bool(
            high >= average - atr5 * .05
            and open_price >= average
            and close_price < average
            and close_price < float(ma5.iloc[-1])
            and close_price < float(ma10.iloc[-1])
            and open_price - close_price >= atr5 * .20
            and (close_price - low) / candle_range <= .40
        )
    return bool(
        low <= average + atr5 * .05
        and open_price <= average
        and close_price > average
        and close_price > float(ma5.iloc[-1])
        and close_price > float(ma10.iloc[-1])
        and close_price - open_price >= atr5 * .20
        and (high - close_price) / candle_range <= .40
    )


def _higher_timeframe_candidate_block(
    fifteen_minute: pd.DataFrame | None,
    candidate_direction: int,
) -> str:
    """Stop an ordinary early candidate from fading a strong 15m MA trend.

    Explicit terminal-acceleration and wick-sweep reversal branches run before
    ``candidate_reversal_setup`` and retain their own high/low extreme gates.
    This guard only prevents a small 1m pullback from turning an otherwise
    aligned higher-timeframe trend into an immediate counter-trend market entry.
    """
    if fifteen_minute is None or len(fifteen_minute) < 23 or not candidate_direction:
        return ""
    frame = fifteen_minute.sort_values("date").reset_index(drop=True)
    close = frame["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    bullish = (
        float(ma5.iloc[-1]) > float(ma10.iloc[-1]) > float(ma20.iloc[-1])
        and float(ma20.iloc[-1]) > float(ma20.iloc[-3])
    )
    bearish = (
        float(ma5.iloc[-1]) < float(ma10.iloc[-1]) < float(ma20.iloc[-1])
        and float(ma20.iloc[-1]) < float(ma20.iloc[-3])
    )
    if candidate_direction < 0 and bullish:
        return "15分钟MA5、MA10、MA20保持多头排列且MA20上升，禁止仅凭1分钟回落抢先做空"
    if candidate_direction > 0 and bearish:
        return "15分钟MA5、MA10、MA20保持空头排列且MA20下降，禁止仅凭1分钟反弹抢先做多"
    return ""


def _candidate_ma20_retest_signal(
    one: pd.DataFrame,
    ma5: pd.Series,
    ma10: pd.Series,
    ma20: pd.Series,
    atr1: float,
    direction: int,
) -> int | None:
    """Second candidate entry after price returns near 1m MA10/MA20."""
    start = max(20, len(one) - 15)
    for i in range(len(one) - 1, max(start + 2, len(one) - 4) - 1, -1):
        candle = one.iloc[i]
        body = float(candle["close"] - candle["open"])
        candle_range = max(float(candle["high"] - candle["low"]), 1e-9)
        retest_found = False
        for j in range(max(start, i - 10), i):
            price = float(one.iloc[j]["high"] if direction < 0 else one.iloc[j]["low"])
            band = (float(ma10.iloc[j]) + float(ma20.iloc[j])) / 2
            if abs(price - band) <= atr1 * .35:
                retest_found = True
                break
        if not retest_found:
            continue
        if direction < 0:
            confirmed = (
                float(ma5.iloc[i]) < float(ma10.iloc[i])
                and body <= -atr1 * .25
                and float(candle["close"]) < float(one.iloc[i-2:i]["low"].min())
                and float(candle["close"] - candle["low"]) / candle_range <= .40
            )
        else:
            confirmed = (
                float(ma5.iloc[i]) > float(ma10.iloc[i])
                and body >= atr1 * .25
                and float(candle["close"]) > float(one.iloc[i-2:i]["high"].max())
                and float(candle["high"] - candle["close"]) / candle_range <= .40
            )
        if confirmed:
            return i
    return None


def _weakening_structure_candidate(five_minute: pd.DataFrame) -> tuple[int, str]:
    """Detect lower highs / higher lows before MA20 reversal confirmation."""
    f = five_minute.sort_values("date").reset_index(drop=True)
    if len(f) < 30:
        return 0, ""
    close = f["close"].astype(float)
    ma20 = close.rolling(20).mean()
    atr5 = float(_atr(f).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0:
        return 0, ""
    recent = f.tail(16)
    first, second = recent.iloc[:8], recent.iloc[8:]
    first_high, second_high = float(first["high"].max()), float(second["high"].max())
    first_low, second_low = float(first["low"].min()), float(second["low"].min())
    recent_ma = ma20.tail(16)
    above = int((recent["close"].astype(float).to_numpy() > recent_ma.to_numpy()).sum())
    # A meaningful lower high/higher low is required; tiny equal-high noise is
    # not enough to arm the fast candidate.
    if above >= 9 and second_high <= first_high - atr5 * .20:
        return -1, "5分钟上涨顶部形成较低高点并开始走弱，进入快速做空候选区"
    if above <= 7 and second_low >= first_low + atr5 * .20:
        return 1, "5分钟下跌底部形成较高低点并开始走强，进入快速做多候选区"
    return 0, ""


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    close = frame["close"].astype(float)
    previous = close.shift(1)
    return pd.concat((
        frame["high"].astype(float) - frame["low"].astype(float),
        (frame["high"].astype(float) - previous).abs(),
        (frame["low"].astype(float) - previous).abs(),
    ), axis=1).max(axis=1).rolling(window).mean()


def five_minute_consolidation(
    five_minute: pd.DataFrame, *, lookback: int = 12,
) -> tuple[bool, str]:
    """Detect a compressed 5m range using closed candles only."""
    f = five_minute.sort_values("date").reset_index(drop=True)
    if len(f) < max(24, lookback):
        return False, "5分钟横盘样本不足"
    close = f["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    atr5 = float(_atr(f).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0:
        return False, "5分钟ATR不足"
    recent_close = close.tail(lookback)
    recent_ma20 = ma20.tail(lookback)
    sides = (recent_close - recent_ma20).apply(
        lambda value: 1 if value > 0 else -1 if value < 0 else 0)
    nonzero = [int(value) for value in sides if value]
    crossings = sum(left != right for left, right in zip(nonzero, nonzero[1:]))
    travelled = float(recent_close.diff().abs().sum())
    efficiency = abs(float(recent_close.iloc[-1] - recent_close.iloc[0])) / max(travelled, 1e-9)
    ma_spread_atr = (
        max(float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1]))
        - min(float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1]))
    ) / atr5
    ma20_slope_atr = abs(float(ma20.iloc[-1] - ma20.iloc[-4])) / atr5
    is_consolidating = (
        crossings >= 3 and efficiency <= .38
        and ma_spread_atr <= .55 and ma20_slope_atr <= .18
    )
    reason = (
        f"5分钟横盘压缩：近{lookback}根穿越MA20共{crossings}次，"
        f"方向效率{efficiency:.2f}，均线带宽{ma_spread_atr:.2f} ATR，"
        f"MA20斜率{ma20_slope_atr:.2f} ATR"
    )
    return is_consolidating, reason


def classify_trend_regime(
    five_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame | None = None,
    *,
    history_window: int = 8,
    confirmation_bars: int = 4,
    minimum_distance_atr: float = .10,
    minimum_slope_atr: float = .05,
    block_consolidation: bool = False,
) -> TrendRegime:
    """Shared MA20 trend/reversal state machine using closed candles only."""
    f = five_minute.sort_values("date").reset_index(drop=True)
    minimum = 20 + history_window + confirmation_bars
    if len(f) < minimum:
        return TrendRegime(0, "insufficient_data", "5分钟数据不足，不能判断趋势反转")
    close = f["close"].astype(float)
    ma20 = close.rolling(20).mean()
    atr = _atr(f)

    latest_confirmed: TrendRegime | None = None
    for i in range(20 + history_window + confirmation_bars - 1, len(f)):
        start = i - confirmation_bars + 1
        before = f.iloc[start-history_window:start]
        before_ma = ma20.iloc[start-history_window:start]
        before_close = before["close"].astype(float)
        prior_above = int((before_close.to_numpy() > before_ma.to_numpy()).sum())
        prior_below = history_window - prior_above
        bars = f.iloc[start:i + 1]
        bars_close = bars["close"].astype(float)
        bars_ma = ma20.iloc[start:i + 1]
        atr_value = float(atr.iloc[i])
        if not pd.notna(atr_value) or atr_value <= 0:
            continue
        distances = (bars_close.to_numpy() - bars_ma.to_numpy()) / atr_value
        ma_change = (float(ma20.iloc[i]) - float(ma20.iloc[start - 1])) / atr_value
        body_top = before[["open", "close"]].astype(float).max(axis=1).max()
        body_bottom = before[["open", "close"]].astype(float).min(axis=1).min()
        bull_structure = bool((bars_close > body_top).any() and float(bars_close.iloc[-1]) > body_top)
        bear_structure = bool((bars_close < body_bottom).any() and float(bars_close.iloc[-1]) < body_bottom)
        bull_distance = int((distances >= minimum_distance_atr).sum()) >= confirmation_bars - 1
        bear_distance = int((distances <= -minimum_distance_atr).sum()) >= confirmation_bars - 1
        if (prior_below >= 6 and bool((distances > 0).all()) and bull_distance
                and ma_change >= minimum_slope_atr and bull_structure):
            latest_confirmed = TrendRegime(
                1, "bullish_reversal_confirmed",
                "5分钟反转成功：连续4根有效站上MA20、MA20转升并以实体收盘突破前8根实体结构；等待后续5分钟回踩（首次有效回踩）",
                i, confirmation_bars,
            )
        elif (prior_above >= 6 and bool((distances < 0).all()) and bear_distance
              and ma_change <= -minimum_slope_atr and bear_structure):
            latest_confirmed = TrendRegime(
                -1, "bearish_reversal_confirmed",
                "5分钟反转成功：连续4根有效跌破MA20、MA20转降并以实体收盘跌破前8根实体结构；等待后续5分钟首次反抽",
                i, confirmation_bars,
            )

    # A newly forming reversal must take precedence over an older confirmed
    # regime.  Otherwise a historical confirmation can mask today's first
    # one-to-three closes through MA20 and the early-entry branch never sees
    # its candidate state.
    recent = f.tail(confirmation_bars)
    recent_ma = ma20.tail(confirmation_bars)
    recent_close = recent["close"].astype(float)
    above = 0
    for value, average in zip(reversed(recent_close.tolist()), reversed(recent_ma.tolist())):
        if value > average:
            above += 1
        else:
            break
    below = 0
    for value, average in zip(reversed(recent_close.tolist()), reversed(recent_ma.tolist())):
        if value < average:
            below += 1
        else:
            break

    def prior_side_count(run: int) -> tuple[int, int]:
        end = len(f) - run
        start = max(20, end - history_window)
        prior_frame = f.iloc[start:end]
        prior_average = ma20.iloc[start:end]
        prior_above_count = int((prior_frame["close"].astype(float).to_numpy()
                                 > prior_average.to_numpy()).sum())
        return prior_above_count, len(prior_frame) - prior_above_count

    consolidating, consolidation_reason = five_minute_consolidation(f)
    if block_consolidation and consolidating:
        return TrendRegime(0, "ranging",
                           f"{consolidation_reason}；不确认新趋势，不启用趋势反转候选下单")

    if 0 < below < confirmation_bars:
        prior_above, _ = prior_side_count(below)
        if prior_above >= 6:
            return TrendRegime(-1, "bearish_reversal_candidate",
                               f"上涨趋势出现{below}根已收盘K线跌到MA20下方，进入反转候选区；旧趋势追多暂停，允许1分钟严格确认后小风险抢先做空",
                               None, below)
    if 0 < above < confirmation_bars:
        _, prior_below = prior_side_count(above)
        if prior_below >= 6:
            return TrendRegime(1, "bullish_reversal_candidate",
                               f"下跌趋势出现{above}根已收盘K线站到MA20上方，进入反转候选区；旧趋势追空暂停，允许1分钟严格确认后小风险抢先做多",
                               None, above)

    if latest_confirmed is not None:
        if fifteen_minute is not None and len(fifteen_minute) >= 23:
            h = fifteen_minute.sort_values("date").reset_index(drop=True)
            h_close = h["close"].astype(float)
            h_ma = h_close.rolling(20).mean()
            strongly_bullish = float(h_close.iloc[-1]) > float(h_ma.iloc[-1]) and float(h_ma.iloc[-1]) > float(h_ma.iloc[-3])
            strongly_bearish = float(h_close.iloc[-1]) < float(h_ma.iloc[-1]) and float(h_ma.iloc[-1]) < float(h_ma.iloc[-3])
            if latest_confirmed.direction < 0 and strongly_bullish:
                return TrendRegime(-1, "bearish_reversal_waiting_higher_timeframe",
                                   "5分钟已满足看跌反转，但15分钟仍强势向上；进入等待区，不允许按新下跌趋势开仓",
                                   latest_confirmed.confirmation_index, confirmation_bars)
            if latest_confirmed.direction > 0 and strongly_bearish:
                return TrendRegime(1, "bullish_reversal_waiting_higher_timeframe",
                                   "5分钟已满足看涨反转，但15分钟仍强势向下；进入等待区，不允许按新上涨趋势开仓",
                                   latest_confirmed.confirmation_index, confirmation_bars)
        return latest_confirmed

    prior = f.iloc[-history_window-confirmation_bars:-confirmation_bars]
    prior_ma = ma20.iloc[-history_window-confirmation_bars:-confirmation_bars]
    prior_above = int((prior["close"].astype(float).to_numpy() > prior_ma.to_numpy()).sum())
    prior_below = history_window - prior_above
    if prior_above >= 6 and below:
        label = "反转候选区" if below < confirmation_bars else "反转等待区"
        return TrendRegime(-1, "bearish_reversal_candidate", f"上涨趋势出现{below}根已收盘K线跌到MA20下方，进入{label}；等待有效距离、MA20转降和实体结构跌破", None, below)
    if prior_below >= 6 and above:
        label = "反转候选区" if above < confirmation_bars else "反转等待区"
        return TrendRegime(1, "bullish_reversal_candidate", f"下跌趋势出现{above}根已收盘K线站到MA20上方，进入{label}；等待有效距离、MA20转升和实体结构突破", None, above)

    # Retire the original fixed-window fallback (for example 6 of the latest
    # 8 closes on one side of MA20).  The current regime is owned by confirmed
    # local swing structure; MA5/MA20 only describe the turn and timing.
    structure = five_minute_price_structure_regime(f)
    if structure.direction > 0:
        return TrendRegime(1, "bullish_trend", structure.reason)
    if structure.direction < 0:
        return TrendRegime(-1, "bearish_trend", structure.reason)
    return TrendRegime(0, "ranging", structure.reason)
