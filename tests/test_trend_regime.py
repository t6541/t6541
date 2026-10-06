import pandas as pd

from quantbot.trend_regime import (candidate_reversal_setup, classify_trend_regime,
                                   aggressive_two_timeframe_intrabar_ma5_reversal_setup,
                                   aggressive_three_timeframe_intrabar_ma5_reversal_setup,
                                   aggressive_downtrend_local_high_ma5_short_setup,
                                   aggressive_uptrend_local_low_ma5_long_setup,
                                   aggressive_fifteen_minute_recovery_long_setup,
                                   aggressive_intrabar_doji_ma5_cross_long_setup,
                                   aggressive_intrabar_ma20_break_short_setup,
                                   aggressive_range_top_doji_ma5_intrabar_short_setup,
                                   aggressive_five_minute_high_half_cover_short_setup,
                                   aggressive_five_minute_low_half_cover_long_setup,
                                   aggressive_top_weakening_ma5_short_setup,
                                   top_weakening_ma5_short_trigger,
                                   aggressive_expanded_ma_top_ma5_short_setup,
                                   aggressive_local_top_intrabar_bear_engulf_short_setup,
                                   aggressive_first_ma20_retest_intrabar_long_setup,
                                   aggressive_delayed_five_ma5_bottom_recovery_long_setup,
                                   aggressive_small_bottom_ma5_rebound_long_setup,
                                   aggressive_weak_top_ma20_retest_short_setup,
                                   bottom_color_reversal_long_setup,
                                   direct_rollover_first_ma5_long_setup,
                                   direct_rollover_first_ma5_short_setup,
                                   direct_rollover_first_ma20_short_setup,
                                   aggressive_first_bear_ma20_short_setup,
                                   three_bear_ma20_rollover_short_setup,
                                   five_minute_consolidation,
                                   five_minute_price_structure_regime,
                                   four_stage_market_description,
                                   top_color_reversal_short_setup)


def _live_bar(open_price, close_price, low, high):
    return pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 13:15")],
        "open": [open_price], "high": [high], "low": [low],
        "close": [close_price], "volume": [150.0],
    })


def test_three_timeframe_intrabar_ma5_reversal_longs_without_closed_slow_ma_confirmation():
    closes = [102 - i * .20 for i in range(25)] + [96.0, 95.8, 95.7, 95.6, 95.5]
    one, five, fifteen = (market(closes, spread=.30) for _ in range(3))

    direction, reason, stop = aggressive_three_timeframe_intrabar_ma5_reversal_setup(
        one, _live_bar(95.70, 96.00, 95.65, 96.10),
        five, _live_bar(95.68, 96.02, 95.62, 96.12),
        fifteen, _live_bar(95.66, 96.04, 95.60, 96.14))

    assert direction == 1, reason
    assert "不等待五分钟、十五分钟收盘" in reason
    assert "不检查MA10或MA20" in reason and "逐级接管" in reason
    assert stop < 96.00


def test_two_timeframe_ma5_small_swing_triggers_before_fifteen_minute_confirmation():
    closes = [102 - i * .20 for i in range(25)] + [96.0, 95.8, 95.7, 95.6, 95.5]
    one, five, fifteen = (market(closes, spread=.30) for _ in range(3))
    one_live = _live_bar(95.70, 96.00, 95.65, 96.10)
    five_live = _live_bar(95.68, 96.02, 95.62, 96.12)
    fifteen_not_ready = _live_bar(95.70, 95.65, 95.55, 95.75)

    small_direction, small_reason, _ = aggressive_two_timeframe_intrabar_ma5_reversal_setup(
        one, one_live, five, five_live)
    large_direction, large_reason, _ = aggressive_three_timeframe_intrabar_ma5_reversal_setup(
        one, one_live, five, five_live, fifteen, fifteen_not_ready)

    assert small_direction == 1, small_reason
    assert "局部双周期MA5回踩追多" in small_reason and "止盈只使用一分钟MA5拐弯" in small_reason
    assert large_direction == 0
    assert "三周期大波段" in large_reason


def test_two_timeframe_long_accepts_later_bearish_candles_still_above_flat_rising_ma5():
    closes = [102 - i * .20 for i in range(25)] + [96.0, 95.8, 95.7, 95.6, 95.5]
    one, five = (market(closes, spread=.30) for _ in range(2))
    direction, reason, stop = aggressive_two_timeframe_intrabar_ma5_reversal_setup(
        one, _live_bar(96.12, 96.00, 95.92, 96.18),
        five, _live_bar(96.15, 96.02, 95.94, 96.20))

    assert direction == 1, reason
    assert "不限K线阴阳" in reason and "不要求本根刚好穿线" in reason
    assert stop < 96.00


def test_two_timeframe_long_does_not_require_five_minute_to_remain_near_initial_low():
    closes = [102 - i * .20 for i in range(25)] + [96.0, 95.8, 95.7, 95.6, 95.5]
    one, five = (market(closes, spread=.30) for _ in range(2))

    direction, reason, stop = aggressive_two_timeframe_intrabar_ma5_reversal_setup(
        one, _live_bar(95.70, 96.00, 95.65, 96.10),
        five, _live_bar(99.70, 100.00, 99.60, 100.10))

    assert direction == 1, reason
    assert "不要求五分钟/十五分钟仍贴近最初局部极值" in reason
    assert stop < 96.00


def test_two_timeframe_long_rejects_when_current_price_falls_back_below_ma5():
    closes = [102 - i * .20 for i in range(25)] + [96.0, 95.8, 95.7, 95.6, 95.5]
    one, five = (market(closes, spread=.30) for _ in range(2))
    direction, reason, _ = aggressive_two_timeframe_intrabar_ma5_reversal_setup(
        one, _live_bar(96.12, 95.45, 95.40, 96.18),
        five, _live_bar(96.15, 96.02, 95.94, 96.20))

    assert direction == 0
    assert "价格须仍在MA5有效侧" in reason


def test_two_timeframe_ma5_small_swing_short_is_mirrored():
    closes = [98 + i * .20 for i in range(25)] + [104.0, 104.2, 104.3, 104.4, 104.5]
    one, five = (market(closes, spread=.30) for _ in range(2))
    direction, reason, stop = aggressive_two_timeframe_intrabar_ma5_reversal_setup(
        one, _live_bar(104.30, 104.00, 103.90, 104.35),
        five, _live_bar(104.32, 103.98, 103.88, 104.38))

    assert direction == -1, reason
    assert "局部双周期MA5反抽追空" in reason and "不升级到五分钟MA5" in reason
    assert stop > 104.00


def test_three_timeframe_intrabar_ma5_reversal_has_mirrored_short_rule():
    closes = [98 + i * .20 for i in range(25)] + [104.0, 104.2, 104.3, 104.4, 104.5]
    one, five, fifteen = (market(closes, spread=.30) for _ in range(3))

    direction, reason, stop = aggressive_three_timeframe_intrabar_ma5_reversal_setup(
        one, _live_bar(104.30, 104.00, 103.90, 104.35),
        five, _live_bar(104.32, 103.98, 103.88, 104.38),
        fifteen, _live_bar(104.34, 103.96, 103.86, 104.40))

    assert direction == -1, reason
    assert "汇合反转做空" in reason and "MA5斜率不作门槛" in reason
    assert stop > 104.00


def test_one_minute_ma5_cross_is_remembered_until_five_and_fifteen_finish_intrabar():
    one_closes = [102 - i * .20 for i in range(19)] + [96.0] + [96.05 + i * .01 for i in range(10)]
    one = market(one_closes, spread=.30)
    one["date"] = pd.date_range("2026-01-01 12:54", periods=30, freq="1min")
    one.loc[19, ["open", "high", "low", "close"]] = [95.70, 96.10, 95.65, 96.00]
    base = [102 - i * .20 for i in range(25)] + [96.0, 95.8, 95.7, 95.6, 95.5]
    five, fifteen = market(base, spread=.30), market(base, spread=.30)
    five["date"] = pd.date_range("2026-01-01 10:45", periods=30, freq="5min")
    fifteen["date"] = pd.date_range("2026-01-01 05:45", periods=30, freq="15min")

    direction, reason, _ = aggressive_three_timeframe_intrabar_ma5_reversal_setup(
        one, pd.DataFrame({"date": [pd.Timestamp("2026-01-01 13:24")], "open": [96.13],
                           "high": [96.20], "low": [96.08], "close": [96.16], "volume": [100.0]}),
        five, pd.DataFrame({"date": [pd.Timestamp("2026-01-01 13:20")], "open": [95.68],
                            "high": [96.12], "low": [95.62], "close": [96.02], "volume": [150.0]}),
        fifteen, pd.DataFrame({"date": [pd.Timestamp("2026-01-01 13:15")], "open": [95.66],
                               "high": [96.14], "low": [95.60], "close": [96.04], "volume": [150.0]}))

    assert direction == 1, reason
    assert "30分钟候选窗口内先后" in reason


def test_three_timeframe_ma5_alignment_is_refreshed_while_price_remains_valid():
    one_closes = [102 - i * .20 for i in range(19)] + [96.0] + [96.05 + i * .01 for i in range(10)]
    one = market(one_closes, spread=.30)
    one["date"] = pd.date_range("2026-01-01 12:54", periods=30, freq="1min")
    one.loc[19, ["open", "high", "low", "close"]] = [95.70, 96.10, 95.65, 96.00]
    base = [102 - i * .20 for i in range(25)] + [96.0, 95.8, 95.7, 95.6, 95.5]
    five, fifteen = market(base, spread=.30), market(base, spread=.30)
    five["date"] = pd.date_range("2026-01-01 11:05", periods=30, freq="5min")
    fifteen["date"] = pd.date_range("2026-01-01 06:00", periods=30, freq="15min")

    direction, reason, _ = aggressive_three_timeframe_intrabar_ma5_reversal_setup(
        one, pd.DataFrame({"date": [pd.Timestamp("2026-01-01 13:45")], "open": [96.30],
                           "high": [96.40], "low": [96.25], "close": [96.35], "volume": [100.0]}),
        five, pd.DataFrame({"date": [pd.Timestamp("2026-01-01 13:40")], "open": [95.68],
                            "high": [96.12], "low": [95.62], "close": [96.02], "volume": [150.0]}),
        fifteen, pd.DataFrame({"date": [pd.Timestamp("2026-01-01 13:30")], "open": [95.66],
                               "high": [96.14], "low": [95.60], "close": [96.04], "volume": [150.0]}))

    assert direction == 1, reason
    assert "有效站位" in reason


def test_local_top_live_bear_engulfs_prior_bull_before_close():
    one = market([100.0] * 24 + [100.05, 100.18], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [100.02, 100.22, 100.00, 100.18]
    live = market([99.94], spread=.03)
    live["date"] = [pd.Timestamp("2026-01-01 00:26:30")]
    live.loc[0, ["open", "high", "low", "close"]] = [100.19, 100.21, 99.92, 99.94]
    active, reason, stop = aggressive_local_top_intrabar_bear_engulf_short_setup(one, live)
    assert active
    assert "尚未收盘" in reason and "超过前阳线全部长度" in reason and "并行" in reason
    assert stop > 100.22


def test_local_top_live_bear_does_not_trigger_before_full_engulfment():
    one = market([100.0] * 24 + [100.05, 100.18], spread=.08)
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [100.02, 100.22, 100.00, 100.18]
    live = market([100.08], spread=.03)
    live.loc[0, ["open", "high", "low", "close"]] = [100.19, 100.21, 100.06, 100.08]
    active, reason, _ = aggressive_local_top_intrabar_bear_engulf_short_setup(one, live)
    assert not active
    assert "前阳线全长" in reason


def test_local_top_bear_engulf_is_blocked_when_ma20_rises_steeply():
    one = market([100.0 + i * .15 for i in range(26)], spread=.08)
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [103.70, 104.02, 103.68, 103.90]
    live = market([103.52], spread=.03)
    live.loc[0, ["open", "high", "low", "close"]] = [103.92, 103.96, 103.50, 103.52]
    active, reason, _ = aggressive_local_top_intrabar_bear_engulf_short_setup(one, live)
    assert not active
    assert "MA20斜率" in reason and "明显上涨时禁止做空" in reason


def test_range_top_doji_live_ma5_break_is_independent_intrabar_short():
    closes = [100.0, 100.2, 99.9, 100.15, 99.95] * 5 + [100.05, 100.22, 100.30, 100.29]
    one = market(closes, spread=.10)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-3], ["open", "high", "low", "close"]] = [100.12, 100.34, 100.10, 100.30]
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [100.28, 100.35, 100.20, 100.22]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [100.29, 100.36, 100.23, 100.29]
    ma5 = float(one["close"].astype(float).rolling(5).mean().iloc[-1])
    live = market([ma5 - .08], spread=.03)
    live["date"] = [pd.Timestamp("2026-01-01 00:29:30")]
    live.loc[0, ["open", "high", "low", "close"]] = [ma5 + .05, ma5 + .07, ma5 - .10, ma5 - .08]
    five = market([100.0, 100.2, 99.9, 100.15, 99.95] * 6, spread=.20)
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")

    active, reason, stop = aggressive_range_top_doji_ma5_intrabar_short_setup(one, live, five)

    assert active
    assert "横盘高点" in reason and "尚未收盘" in reason and "并行" in reason
    assert stop > float(one.tail(12)["high"].max())


def test_range_top_doji_live_ma5_break_rejects_late_short():
    closes = [100.0, 100.2, 99.9, 100.15, 99.95] * 5 + [100.05, 100.22, 100.30, 100.29]
    one = market(closes, spread=.10)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [100.29, 100.36, 100.23, 100.29]
    ma5 = float(one["close"].astype(float).rolling(5).mean().iloc[-1])
    live = market([ma5 - .50], spread=.03)
    live.loc[0, ["open", "high", "low", "close"]] = [ma5 + .05, ma5 + .07, ma5 - .52, ma5 - .50]
    five = market([100.0, 100.2, 99.9, 100.15, 99.95] * 6, spread=.20)
    active, reason, _ = aggressive_range_top_doji_ma5_intrabar_short_setup(one, live, five)
    assert not active
    assert "0.45" in reason


def intrabar_ma20_short_frames(late=False):
    one = market([100.18] * 25 + [100.10, 100.28, 100.18, 100.08], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-4], ["open", "high", "low", "close"]] = [100.02, 100.14, 99.98, 100.10]
    one.loc[one.index[-3], ["open", "high", "low", "close"]] = [100.10, 100.34, 100.08, 100.28]
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [100.27, 100.30, 100.14, 100.18]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [100.18, 100.20, 100.04, 100.08]
    current_ma20 = float(one["close"].astype(float).rolling(20).mean().iloc[-1])
    live_close = current_ma20 - (.35 if late else .04)
    live = market([live_close], spread=.03)
    live["date"] = [pd.Timestamp("2026-01-01 00:29:30")]
    live.loc[0, ["open", "high", "low", "close"]] = [current_ma20 + .03, current_ma20 + .06, live_close - .02, live_close]
    five = market([101.0 - i * .02 for i in range(30)], spread=.15)
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    return one, live, five


def test_aggressive_intrabar_ma20_break_short_does_not_wait_for_close():
    one, live, five = intrabar_ma20_short_frames()
    active, reason, stop = aggressive_intrabar_ma20_break_short_setup(one, live, five)
    assert active
    assert "尚未收盘" in reason and "局部小止损市价做空" in reason
    assert stop > float(one.tail(12)["high"].max())


def test_aggressive_intrabar_ma20_break_short_rejects_late_chase():
    one, live, five = intrabar_ma20_short_frames(late=True)
    active, reason, _ = aggressive_intrabar_ma20_break_short_setup(one, live, five)
    assert not active
    assert "0.45" in reason


def downtrend_frame(freq="5min"):
    frame = market([120 - i * .25 for i in range(30)], spread=.25)
    frame["date"] = pd.date_range("2026-01-01", periods=len(frame), freq=freq)
    return frame


def test_aggressive_small_bottom_rebound_enters_on_first_bullish_ma5_reclaim():
    one = market([110 - i * .08 for i in range(21)] + [107.2, 107.18, 107.85], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-7:-3], ["open", "high", "low", "close"]] = [
        [107.35, 107.43, 107.22, 107.30],
        [107.30, 107.38, 107.17, 107.25],
        [107.25, 107.33, 107.12, 107.20],
        [107.22, 107.30, 107.10, 107.18],
    ]
    one.loc[one.index[-3], ["open", "high", "low", "close"]] = [108.4, 108.45, 107.0, 107.2]
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [107.18, 107.30, 107.02, 107.20]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [107.18, 107.95, 107.12, 107.85]
    active, reason, stop = aggressive_small_bottom_ma5_rebound_long_setup(
        downtrend_frame(), one, downtrend_frame("15min"))
    assert active
    assert "下跌中的反抽" in reason
    assert stop < 107.0


def test_aggressive_downtrend_local_high_short_uses_first_red_ma5_rollover():
    one = market([108 - i * .06 for i in range(20)] + [106.7, 107.0, 107.3, 107.2], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [106.8, 107.55, 106.75, 107.3]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [107.42, 107.55, 107.15, 107.20]
    active, reason, stop = aggressive_downtrend_local_high_ma5_short_setup(
        downtrend_frame(), one, downtrend_frame("15min"))
    assert active
    assert "MA5外沿提前续空" in reason and "不等待下穿MA5" in reason
    assert 107.42 < stop < 107.55


def test_aggressive_uptrend_local_low_long_is_price_mirror_of_early_short():
    short_one = market([108 - i * .06 for i in range(20)] + [106.7, 107.0, 107.3, 107.2], spread=.08)
    short_one["date"] = pd.date_range("2026-01-01", periods=len(short_one), freq="1min")
    short_one.loc[short_one.index[-2], ["open", "high", "low", "close"]] = [106.8, 107.55, 106.75, 107.3]
    short_one.loc[short_one.index[-1], ["open", "high", "low", "close"]] = [107.42, 107.55, 107.15, 107.20]

    def mirror(frame):
        result = frame.copy()
        result["open"] = 214.0 - frame["open"]
        result["close"] = 214.0 - frame["close"]
        result["high"] = 214.0 - frame["low"]
        result["low"] = 214.0 - frame["high"]
        return result

    long_one = mirror(short_one)
    active, reason, stop = aggressive_uptrend_local_low_ma5_long_setup(
        mirror(downtrend_frame()), long_one, mirror(downtrend_frame("15min")))
    assert active, reason
    assert "MA5外沿提前续多" in reason and "不等待上穿MA5" in reason
    assert float(long_one.tail(3)[["open", "close"]].min(axis=1).min()) > stop


def test_15m_decline_alone_authorizes_first_red_throwback_and_three_candle_stop():
    one = market([108 - i * .06 for i in range(20)] + [106.7, 107.0, 107.3, 107.2], spread=.08)
    one["date"] = pd.date_range("2026-09-15", periods=len(one), freq="1min")
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [106.8, 107.55, 106.75, 107.3]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [107.42, 107.55, 107.15, 107.20]
    flat_five = market([105.0] * 30)
    active, reason, stop = aggressive_downtrend_local_high_ma5_short_setup(
        flat_five, one, downtrend_frame("15min"))
    assert active, reason
    assert "15分钟下降趋势已确认" in reason
    assert float(one.tail(3)[["open", "close"]].max(axis=1).max()) < stop < float(one.tail(3)["high"].max())


def test_downtrend_local_high_short_does_not_require_fifteen_minute_alignment():
    one = market([108 - i * .06 for i in range(20)] + [106.7, 107.0, 107.3, 107.2], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [106.8, 107.55, 106.75, 107.3]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [107.42, 107.55, 107.15, 107.20]
    fifteen_not_down = market([100 + i * .2 for i in range(40)])
    fifteen_not_down["date"] = pd.date_range(
        "2026-01-01", periods=len(fifteen_not_down), freq="15min")

    active, reason, stop = aggressive_downtrend_local_high_ma5_short_setup(
        downtrend_frame(), one, fifteen_not_down)
    assert active, reason
    assert "15m optional reference not aligned" in reason
    assert 107.42 < stop < 107.55


def test_downtrend_pullback_half_cover_shorts_before_ma5_break():
    one = market([108 - i * .06 for i in range(20)] + [106.7, 107.0, 107.3, 107.2], spread=.08)
    one["date"] = pd.date_range("2026-09-05", periods=len(one), freq="1min")
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [106.8, 107.60, 106.75, 107.3]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [107.42, 107.55, 107.15, 107.20]
    ma5 = one["close"].astype(float).rolling(5).mean().iloc[-1]
    assert float(one.iloc[-1]["close"]) > float(ma5)
    assert 107.42 - 107.20 < (107.30 - 106.80) * .50

    active, reason, stop = aggressive_downtrend_local_high_ma5_short_setup(
        downtrend_frame(), one, downtrend_frame("15min"))
    assert active, reason
    assert "不要求单根覆盖50%" in reason and "不等待下穿MA5" in reason
    assert 107.42 < stop < 107.60


def test_downtrend_local_high_first_red_remains_executable_with_stale_opposite_bias():
    one = market([108 - i * .06 for i in range(20)] + [106.7, 107.0, 107.3, 107.2], spread=.08)
    one["date"] = pd.date_range("2026-09-06", periods=len(one), freq="1min")
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [106.8, 107.60, 106.75, 107.3]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [107.42, 107.55, 107.15, 107.20]
    active, reason, _ = aggressive_downtrend_local_high_ma5_short_setup(
        downtrend_frame(), one, downtrend_frame("15min"), durable_downtrend=False)
    assert active, reason
    assert "当前首根转弱阴线即提前做空" in reason


def test_fifteen_and_hour_downtrend_short_local_high_before_one_minute_ma5_break():
    one = market([108 - i * .08 for i in range(20)] + [106.1, 106.25, 106.45, 106.35], spread=.05)
    one["date"] = pd.date_range("2026-09-08", periods=len(one), freq="1min")
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [106.20, 106.62, 106.18, 106.48]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [106.50, 106.58, 106.31, 106.34]
    ma5 = one["close"].astype(float).rolling(5).mean().iloc[-1]
    assert float(one.iloc[-1]["close"]) >= ma5
    flat_five = market([105.0] * 30)
    active, reason, stop = aggressive_downtrend_local_high_ma5_short_setup(
        flat_five, one, downtrend_frame("15min"), downtrend_frame("1h"))
    assert active, reason
    assert "15分钟和1小时同步下跌" in reason
    assert "不等待下穿MA5" in reason
    assert 106.50 < stop < 106.62


def test_delayed_fourth_five_minute_ma5_reclaim_accepts_single_v_bottom():
    # The fourth bull reclaims MA5 while it is still below MA10.  MA10 must not
    # be an implicit confirmation gate for this independent recovery entry.
    five = market([110 - i * .30 for i in range(26)] + [101.5, 104.0, 102.5, 103.0], spread=.25)
    for i in range(len(five) - 4, len(five)):
        five.loc[i, "open"] = float(five.loc[i, "close"]) - .20
    # One isolated V low followed by a steady recovery: deliberately no second
    # bottom, proving that a double bottom is one option rather than a mandate.
    one_closes = ([105 - i * .10 for i in range(30)]
                  + [101.0, 101.6, 101.8, 102.0, 102.15, 102.25, 102.32, 102.38,
                     102.43, 102.47, 102.50, 102.53, 102.55, 102.57, 102.60])
    one = market(one_closes, spread=.12)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[30, ["open", "high", "low", "close", "volume"]] = [101.5, 101.6, 100.7, 101.0, 260.0]

    active, reason, stop = aggressive_delayed_five_ma5_bottom_recovery_long_setup(five, one)

    assert active, reason
    assert "单V形" in reason and "第4根" in reason
    assert "不要求同时上穿MA10" in reason
    close5 = five["close"].astype(float)
    assert close5.iloc[-1] < close5.rolling(10).mean().iloc[-1]
    assert stop < float(one.iloc[-1]["close"])


def test_weakening_top_gets_second_short_chance_on_ma20_retest_rejection():
    closes = ([98.0 + i * .10 for i in range(18)]
              + [100.0, 101.0, 100.5, 101.5, 101.0, 101.3, 100.8, 100.4,
                 100.2, 100.0, 99.8, 99.7, 99.8, 99.9, 100.0, 99.9, 99.8, 99.7])
    one = market(closes, spread=.10)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    # Make the two intended peaks explicit and leave a confirmed MA5 break
    # between the peak and the later retry.
    one.loc[one.index[21], "high"] = 101.70
    one.loc[one.index[23], "high"] = 101.42
    live = market([100.08], spread=.06)
    live["date"] = [one.iloc[-1]["date"] + pd.Timedelta(minutes=1)]
    live.loc[live.index[-1], ["open", "high", "low", "close"]] = [100.20, 100.25, 100.02, 100.08]
    active, reason, stop = aggressive_weak_top_ma20_retest_short_setup(one, live)
    assert active
    assert "第二切入点" in reason and "回抽一分钟MA20" in reason
    assert stop > float(live.iloc[-1]["high"])


def test_aggressive_fifteen_minute_recovery_long_does_not_wait_for_slow_ma_flip():
    fifteen = market([120 - i * .25 for i in range(22)] + [114.8, 116.0], spread=.25)
    fifteen["date"] = pd.date_range("2026-01-01", periods=len(fifteen), freq="15min")
    fifteen.loc[fifteen.index[-2], ["open", "high", "low", "close"]] = [113.8, 115.0, 113.6, 114.8]
    fifteen.loc[fifteen.index[-1], ["open", "high", "low", "close"]] = [114.7, 116.2, 114.6, 116.0]
    five = market([110.0] * 14 + [109.8, 109.6, 109.5, 109.7, 110.0, 110.3, 110.6, 110.9, 111.2, 111.5], spread=.18)
    one = market([109.0] * 14 + [108.8, 108.7, 108.9, 109.1, 109.3, 109.5, 109.7, 109.9, 110.1, 110.3], spread=.10)
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    active, reason, stop = aggressive_fifteen_minute_recovery_long_setup(one, five, fifteen)
    assert active
    assert "不等待十五分钟旧均线完全翻多" in reason
    assert stop < float(one.tail(12)["low"].min())


def test_fifteen_minute_recovery_requires_two_closed_bulls():
    fifteen = market([120 - i * .25 for i in range(22)] + [114.8, 114.5], spread=.25)
    five = market([110.0] * 14 + [109.8, 109.6, 109.5, 109.7, 110.0, 110.3, 110.6, 110.9, 111.2, 111.5], spread=.18)
    one = market([109.0] * 14 + [108.8, 108.7, 108.9, 109.1, 109.3, 109.5, 109.7, 109.9, 110.1, 110.3], spread=.10)
    active, _, _ = aggressive_fifteen_minute_recovery_long_setup(one, five, fifteen)
    assert not active


def test_intrabar_bull_cross_after_bottom_doji_enters_before_close():
    one = market([110 - i * .08 for i in range(14)]
                 + [108.70, 108.60, 108.55, 108.50, 108.48, 108.46, 108.44, 108.46, 108.48, 108.50], spread=.06)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [108.56, 108.60, 108.38, 108.58]
    live = market([108.56], spread=.03)
    live["date"] = [one.iloc[-1]["date"] + pd.Timedelta(minutes=1)]
    live.loc[live.index[-1], ["open", "high", "low", "close"]] = [108.53, 108.58, 108.50, 108.56]
    active, reason, stop = aggressive_intrabar_doji_ma5_cross_long_setup(one, live)
    assert active
    assert "单根阳线有效站上MA5" in reason and "立即小风险追多" in reason
    assert stop < float(one.tail(10)["low"].min())


def test_intrabar_ma5_cross_rejects_late_extended_bull():
    one = market([110 - i * .08 for i in range(18)] + [108.55, 108.50, 108.46, 108.44, 108.48, 108.50], spread=.06)
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [108.48, 108.54, 108.43, 108.50]
    live = market([110.0], spread=.03)
    live.loc[live.index[-1], ["open", "high", "low", "close"]] = [108.49, 110.1, 108.48, 110.0]
    active, _, _ = aggressive_intrabar_doji_ma5_cross_long_setup(one, live)
    assert not active


def test_stage_three_buys_first_intrabar_ma20_retest_that_holds():
    one = market([110.0] * 20 + [109.8, 109.7, 109.9, 110.2, 110.5, 110.7], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    five = market([110.0] * 18 + [109.8, 109.9, 110.1, 110.3, 110.5, 110.7], spread=.18)
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    live = market([110.14], spread=.05)
    live.loc[live.index[-1], ["open", "high", "low", "close"]] = [110.08, 110.20, 110.04, 110.14]
    active, reason, stop = aggressive_first_ma20_retest_intrabar_long_setup(one, live, five)
    assert active
    assert "第三阶段追多" in reason and "第一次回踩MA20" in reason
    assert stop < float(live.iloc[-1]["low"])


def test_stage_three_never_repeats_after_a_completed_ma20_retest():
    one = market([110.0] * 20 + [109.8, 109.7, 109.9, 110.2, 110.05, 110.5], spread=.08)
    five = market([110.0] * 18 + [109.8, 109.9, 110.1, 110.3, 110.5, 110.7], spread=.18)
    live = market([110.48], spread=.05)
    active, reason, _ = aggressive_first_ma20_retest_intrabar_long_setup(one, live, five)
    assert not active
    assert "不重复追多" in reason


def market(closes, *, spread=.2):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(closes), freq="5min"),
        "open": closes,
        "high": [v + spread for v in closes],
        "low": [v - spread for v in closes],
        "close": closes,
        "volume": [100.0] * len(closes),
    })


def color_top_frames():
    five = market([100 + i * .12 for i in range(27)] + [103.30, 103.28, 103.26], spread=.35)
    one = market([100 + i * .12 for i in range(27)] + [103.8, 103.45, 103.10], spread=.12)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-3], ["open", "high", "low", "close", "volume"]] = [103.25, 104.05, 103.2, 103.8, 120]
    one.loc[one.index[-2], ["open", "high", "low", "close", "volume"]] = [103.78, 103.85, 103.25, 103.45, 140]
    one.loc[one.index[-1], ["open", "high", "low", "close", "volume"]] = [103.44, 103.50, 103.00, 103.10, 140]
    return five, one


def test_top_color_reversal_triggers_before_ma_cross_with_five_timeframe_evidence():
    five, one = color_top_frames()
    active, reason, stop = top_color_reversal_short_setup(five, one, five, five, five, five)
    assert active
    assert stop > float(one.iloc[-3]["high"])
    assert "即使仍在MA5上方且MA20向上" in reason
    assert all(label in reason for label in ("4小时", "1小时", "30分钟", "15分钟", "5分钟", "1分钟"))


def test_bottom_color_reversal_is_exact_mirror():
    five, one = color_top_frames()
    for frame in (five, one):
        old = {column: frame[column].copy() for column in ("open", "high", "low", "close")}
        frame["open"], frame["high"], frame["low"], frame["close"] = (
            -old["open"], -old["low"], -old["high"], -old["close"])
    active, reason, stop = bottom_color_reversal_long_setup(five, one, five, five, five, five)
    assert active
    assert stop < float(one.iloc[-3]["low"])
    assert "做多" in reason


def test_five_timeframe_description_has_every_shared_period():
    five, one = color_top_frames()
    text = four_stage_market_description(one, five, five, five, five, five)
    assert all(label in text for label in ("4小时", "1小时", "30分钟", "15分钟", "5分钟", "1分钟"))


def test_one_to_three_opposite_closes_are_candidate_not_confirmed():
    frame = market(list(range(100, 140)) + [120, 119, 118])
    result = classify_trend_regime(frame)
    assert result.state == "bearish_reversal_candidate"
    assert result.opposite_closes == 3


def test_four_closes_without_effective_distance_remain_waiting():
    base = list(range(100, 140))
    frame = market(base + [120.00, 119.99, 119.98, 119.97], spread=3.0)
    result = classify_trend_regime(frame)
    assert result.state == "bearish_reversal_candidate"
    assert "等待" in result.reason


def test_confirmed_bullish_reversal_uses_body_structure_and_slope():
    frame = market(list(range(140, 100, -1)) + [120, 121, 122, 123])
    result = classify_trend_regime(frame)
    assert result.state == "bullish_reversal_confirmed"
    assert result.direction == 1


def test_higher_timeframe_opposition_keeps_reversal_waiting():
    five = market(list(range(100, 140)) + [120, 119, 118, 117])
    fifteen = market(list(range(100, 150)))
    result = classify_trend_regime(five, fifteen)
    assert result.state == "bearish_reversal_waiting_higher_timeframe"


def test_sideways_is_ranging():
    result = classify_trend_regime(market([100, 101] * 30))
    assert result.state == "ranging"


def test_post_rally_compression_blocks_fast_reversal_churn(monkeypatch):
    rally = [100 + i * .8 for i in range(36)]
    compression = ([128.4, 129.0, 128.5, 129.1] * 4
                   + [128.6, 129.0, 128.55, 129.05, 128.6, 129.0,
                      128.65, 128.95, 128.55, 129.05, 128.6, 129.0])
    five = market(rally + compression, spread=.35)
    consolidating, detail = five_minute_consolidation(five)
    assert consolidating
    assert "横盘压缩" in detail
    assert classify_trend_regime(five, block_consolidation=True).state == "ranging"
    monkeypatch.setattr("quantbot.trend_regime._weakening_structure_candidate",
                        lambda frame: (-1, "5分钟顶部降低、走弱快速候选"))
    direction, reason, stop = candidate_reversal_setup(
        five, one_minute_bear_confirmation(), block_consolidation=True)
    assert direction == 0
    assert stop == 0.0
    assert "快速反转候选只观察不下单" in reason


def one_minute_bear_confirmation():
    closes = [130 - i * .12 for i in range(27)] + [126.4, 126.0, 124.8]
    frame = market(closes, spread=.18)
    frame["date"] = pd.date_range("2026-01-01", periods=len(frame), freq="1min")
    frame.loc[frame.index[-1], "open"] = 126.0
    frame.loc[frame.index[-1], "high"] = 126.1
    frame.loc[frame.index[-1], "low"] = 124.7
    frame.loc[frame.index[-1], "volume"] = 250.0
    return frame


def test_candidate_entry_uses_closed_one_minute_breakdown():
    five = market(list(range(100, 140)) + [120, 119])
    direction, reason, stop = candidate_reversal_setup(five, one_minute_bear_confirmation())
    assert direction == -1
    assert "候选" in reason
    assert stop > 124.8


def test_bear_candidate_is_blocked_by_strong_fifteen_minute_uptrend():
    five = market(list(range(100, 140)) + [120, 119])
    fifteen = market([100 + i * .5 for i in range(50)])
    direction, reason, stop = candidate_reversal_setup(
        five, one_minute_bear_confirmation(), fifteen
    )
    assert direction == 0
    assert "15分钟MA5、MA10、MA20保持多头排列" in reason
    assert stop == 0.0


def test_closed_five_minute_ma20_failed_retest_can_override_strong_fifteen_minute_guard():
    five = market([100 + i * .5 for i in range(40)] + [113.8])
    latest = five.index[-1]
    five.loc[latest, ["open", "high", "low", "close", "volume"]] = [115.2, 115.3, 113.7, 113.8, 250.0]
    assert classify_trend_regime(five).state == "bearish_reversal_candidate"
    fifteen = market([100 + i * .5 for i in range(50)])

    direction, reason, stop = candidate_reversal_setup(
        five, one_minute_bear_confirmation(), fifteen
    )

    assert direction == -1
    assert "5分钟反抽MA20上穿失败" in reason
    assert stop > 124.8


def test_five_minute_failed_retest_still_waits_without_one_minute_breakdown():
    five = market([100 + i * .5 for i in range(40)] + [113.8])
    latest = five.index[-1]
    five.loc[latest, ["open", "high", "low", "close", "volume"]] = [115.2, 115.3, 113.7, 113.8, 250.0]
    fifteen = market([100 + i * .5 for i in range(50)])
    one = market([120 + i * .05 for i in range(30)])
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")

    direction, reason, stop = candidate_reversal_setup(five, one, fifteen)

    assert direction == 0
    assert "等待1分钟" in reason
    assert stop == 0.0


def test_bear_candidate_remains_available_when_fifteen_minute_is_not_bullish():
    five = market(list(range(100, 140)) + [120, 119])
    fifteen = market([130 - i * .2 for i in range(50)])
    direction, _, stop = candidate_reversal_setup(
        five, one_minute_bear_confirmation(), fifteen
    )
    assert direction == -1
    assert stop > 124.8


def test_bull_candidate_is_blocked_by_strong_fifteen_minute_downtrend():
    five = market(list(range(140, 100, -1)) + [120, 121])
    fifteen = market([140 - i * .5 for i in range(50)])
    one = one_minute_bear_confirmation().copy()
    one[["open", "high", "low", "close"]] = 250 - one[["open", "low", "high", "close"]].to_numpy()
    direction, reason, stop = candidate_reversal_setup(five, one, fifteen)
    assert direction == 0
    assert "15分钟MA5、MA10、MA20保持空头排列" in reason
    assert stop == 0.0


def test_current_candidate_overrides_older_confirmed_regime():
    closes = list(range(140, 100, -1)) + [120, 121, 122, 123]
    closes += list(range(124, 145)) + [132, 131]
    result = classify_trend_regime(market(closes))
    assert result.state == "bearish_reversal_candidate"
    assert result.opposite_closes == 2


def test_candidate_does_not_enter_without_one_minute_breakdown():
    five = market(list(range(100, 140)) + [120, 119])
    one = market([120 + i * .05 for i in range(30)])
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    direction, _, _ = candidate_reversal_setup(five, one)
    assert direction == 0


def five_minute_bear_impulse_cross():
    closes = [100 + i * .25 for i in range(36)]
    frame = market(closes)
    i = frame.index[-1]
    frame.loc[i, ["open", "high", "low", "close"]] = [109.0, 109.1, 105.9, 106.0]
    return frame


def one_minute_bear_confirmation_before_tail():
    closes = [110 - i * .05 for i in range(30)]
    frame = market(closes, spread=.10)
    frame["date"] = pd.date_range("2026-01-01", periods=len(frame), freq="1min")
    signal = frame.index[-3]
    frame.loc[signal, ["open", "high", "low", "close", "volume"]] = [109.2, 109.3, 107.7, 107.75, 250.0]
    frame.loc[signal + 1, ["open", "high", "low", "close", "volume"]] = [107.8, 107.95, 107.7, 107.82, 80.0]
    frame.loc[signal + 2, ["open", "high", "low", "close", "volume"]] = [107.82, 107.9, 107.75, 107.80, 80.0]
    return frame


def test_direct_rollover_first_ma20_break_is_independent_short_branch():
    active, reason, stop = direct_rollover_first_ma20_short_setup(
        five_minute_bear_impulse_cross(), one_minute_bear_confirmation_before_tail())
    assert active
    assert "独立分支排队做空" in reason
    assert stop > 107.8


def test_three_bear_ma20_rollover_confirms_early_short():
    five = market([110.0] * 21 + [109.9, 109.8, 109.6], spread=.3)
    one = market([110.0] * 21 + [110.1, 109.65, 109.25], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-3], ["open", "high", "low", "close"]] = [110.15, 110.20, 109.85, 109.90]
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [109.88, 109.94, 109.48, 109.55]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [109.52, 109.58, 109.10, 109.20]

    active, reason, stop = three_bear_ma20_rollover_short_setup(five, one)

    assert active
    assert "三阴确认转跌" in reason
    assert stop > 109.94


def test_aggressive_first_bear_cross_enters_without_waiting_three_candles():
    five = market([110.0] * 21 + [109.9, 109.8, 109.7], spread=.3)
    one = market([110.0] * 23 + [109.65], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [110.15, 110.20, 109.55, 109.65]

    active, reason, stop = aggressive_first_bear_ma20_short_setup(five, one)

    assert active
    assert "首阴下穿MA20早空" in reason
    assert stop > 110.20


def test_three_bear_ma20_rollover_rejects_second_candle_reclaim():
    five = market([110.0] * 21 + [109.9, 109.8, 109.6], spread=.3)
    one = market([110.0] * 21 + [109.9, 110.2, 109.2], spread=.08)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-3], ["open", "high", "low", "close"]] = [110.15, 110.20, 109.85, 109.90]
    one.loc[one.index[-2], ["open", "high", "low", "close"]] = [110.30, 110.10, 110.00, 110.20]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [109.52, 109.58, 109.10, 109.20]

    active, _, _ = three_bear_ma20_rollover_short_setup(five, one)

    assert not active


def test_direct_rollover_ma5_is_an_earlier_independent_priority_branch():
    active, reason, stop = direct_rollover_first_ma5_short_setup(
        five_minute_bear_impulse_cross(), one_minute_bear_confirmation_before_tail())
    assert active
    assert "第一优先" in reason and "MA5" in reason
    assert stop > 107.8


def test_bottom_stage_two_requires_ma20_above_and_rejects_middle_high_long():
    five = five_minute_bear_impulse_cross().copy()
    one = one_minute_bear_confirmation_before_tail().copy()
    for frame in (five, one):
        old_open = frame["open"].astype(float).copy()
        old_high = frame["high"].astype(float).copy()
        old_low = frame["low"].astype(float).copy()
        old_close = frame["close"].astype(float).copy()
        frame["open"] = 250 - old_open
        frame["high"] = 250 - old_low
        frame["low"] = 250 - old_high
        frame["close"] = 250 - old_close
    # Lift the latest bullish confirmation above 1m MA20.  The old rule
    # incorrectly called this a bottom stage-2 long even though the move had
    # already reached the middle/local-high area.
    close = one["close"].astype(float)
    ma20 = float(close.rolling(20).mean().iloc[-1])
    i = one.index[-1]
    one.loc[i, ["open", "high", "low", "close"]] = [
        ma20 - .10, ma20 + .65, ma20 - .20, ma20 + .55]

    active, reason, _ = direct_rollover_first_ma5_long_setup(five, one)

    assert not active
    assert "已站上MA20" in reason
    assert "中部/局部高点" in reason


def test_bottom_stage_two_allows_single_ma5_reclaim_below_ma20_near_low(monkeypatch):
    monkeypatch.setattr(
        "quantbot.trend_regime._five_minute_direct_rollover_long",
        lambda _: (True, "5分钟原下跌排列底部止跌"),
    )
    values = [110 - i * .1 for i in range(25)] + [107.0, 107.2, 107.4, 107.6, 107.8]
    one = market(values, spread=.10)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [107.30, 107.90, 107.20, 107.80]

    active, reason, stop = direct_rollover_first_ma5_long_setup(
        five_minute_bear_impulse_cross(), one)

    assert active, reason
    assert "底部反转第二阶段" in reason
    assert "单根阳线站上MA5" in reason
    assert stop < 107.20


def test_bottom_stage_two_allows_two_bull_v_recovery_to_ma10(monkeypatch):
    monkeypatch.setattr(
        "quantbot.trend_regime._five_minute_direct_rollover_long",
        lambda _: (True, "5分钟原下跌排列底部止跌"),
    )
    monkeypatch.setattr(
        "quantbot.trend_regime._atr",
        lambda frame: pd.Series([10.0] * len(frame), index=frame.index),
    )
    values = [110 - i * .1 for i in range(25)] + [107.0, 106.8, 107.0, 107.2, 107.4]
    one = market(values, spread=.10)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    anchor, first, second = one.index[-3:]
    # The two bullish candles reach MA10 without covering the whole prior bear;
    # V recovery remains valid and does not replace the independent engulf path.
    one.loc[anchor, ["open", "high", "low", "close"]] = [108.20, 108.25, 106.75, 106.85]
    one.loc[first, ["open", "high", "low", "close"]] = [106.82, 107.25, 106.78, 107.20]
    one.loc[second, ["open", "high", "low", "close"]] = [107.18, 107.70, 107.15, 107.62]

    active, reason, stop = direct_rollover_first_ma5_long_setup(
        five_minute_bear_impulse_cross(), one)

    assert active, reason
    assert "两根阳线V形收复至MA10" in reason
    assert stop < 106.78


def test_direct_rollover_allows_strong_wick_break_only_when_close_hugs_falling_ma20():
    one = one_minute_bear_confirmation_before_tail()
    close = one["close"].astype(float)
    average = float(close.rolling(20).mean().iloc[-1])
    i = one.index[-1]
    one.loc[i, ["open", "high", "low", "close", "volume"]] = [
        average + .35, average + .40, average - .30, average + .06, 300.0]
    active, reason, stop = direct_rollover_first_ma20_short_setup(
        five_minute_bear_impulse_cross(), one)
    assert active
    assert "影线下穿" in reason
    assert stop > average


def test_direct_rollover_does_not_replace_original_candidate_rule():
    five = market(list(range(100, 140)) + [120, 119])
    direct, _, _ = direct_rollover_first_ma20_short_setup(five, one_minute_bear_confirmation())
    candidate, _, _ = candidate_reversal_setup(five, one_minute_bear_confirmation())
    assert not direct
    assert candidate == -1


def test_candidate_recovers_one_minute_ma20_cross_before_tail():
    direction, reason, stop = candidate_reversal_setup(
        five_minute_bear_impulse_cross(), one_minute_bear_confirmation_before_tail()
    )
    assert direction == -1
    assert "候选" in reason
    assert stop > 107.8


def test_long_five_minute_ma20_cross_still_requires_one_minute_break():
    one = market([107 + i * .03 for i in range(30)])
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    direction, _, _ = candidate_reversal_setup(five_minute_bear_impulse_cross(), one)
    assert direction == 0


def test_fast_candidate_cancels_when_price_has_already_run_too_far():
    one = one_minute_bear_confirmation_before_tail()
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [106.6, 106.75, 106.55, 106.65]
    direction, reason, _ = candidate_reversal_setup(five_minute_bear_impulse_cross(), one)
    assert direction == 0
    assert "迟到追单" in reason


def test_far_candidate_rearms_after_retest_near_one_minute_ma20():
    one = one_minute_bear_confirmation_before_tail()
    close = one["close"].astype(float)
    current_ma20 = float(close.rolling(20).mean().iloc[-1])
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [
        current_ma20 + .08, current_ma20 + .12, current_ma20 - .08, current_ma20 - .03
    ]
    direction, reason, _ = candidate_reversal_setup(five_minute_bear_impulse_cross(), one)
    assert direction == -1
    assert "做空" in reason


def test_five_minute_higher_high_and_higher_low_define_bullish_structure():
    size = 30
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=size, freq="5min"),
        "open": [100.0] * size, "close": [100.0] * size,
        "high": [101.0] * size, "low": [99.0] * size,
    })
    frame.loc[[5, 13, 21], "close"] = [105.0, 107.0, 109.0]
    frame.loc[[9, 17, 25], "close"] = [95.0, 97.0, 99.0]
    regime = five_minute_price_structure_regime(frame)
    assert regime.direction == 1
    assert regime.state == "bullish_structure"


def test_top_weakening_ma5_short_uses_lower_highs_without_waiting_for_ma20_downturn():
    close = [100.0] * 22 + [101, 102, 103, 104, 102, 103, 103.5, 102.5, 102.5, 103, 101]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close[:], "high": [value + .8 for value in close],
        "low": [value - .8 for value in close], "close": close,
    })
    one.loc[one.index[-3], ["open", "close"]] = [102.5, 102.52]
    one.loc[one.index[-2], ["open", "close"]] = [103.5, 103.51]
    one.loc[one.index[-1], ["open", "close", "high", "low"]] = [103.0, 102.0, 103.2, 101.7]
    active, reason, stop = aggressive_top_weakening_ma5_short_setup(one, pd.DataFrame())
    assert active
    assert "局部高点降低" in reason and "跌破MA5" in reason
    assert 104.3 < stop < 104.8


def test_top_weakening_ma5_break_remains_valid_for_three_bars_below_ma5():
    close = [100.0] * 20 + [101, 102, 103, 104, 103, 102, 101, 100.5]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [value + .20 for value in close],
        "high": [value + .35 for value in close], "low": [value - .35 for value in close],
        "close": close,
    })
    active, reason, _, _ = top_weakening_ma5_short_trigger(one)
    assert active and "第3根K线仍在MA5下方" in reason


def test_top_weakening_entry_does_not_reuse_three_bar_trigger_at_lower_edge():
    close = [100.0] * 22 + [101, 102, 103, 104, 102, 103, 103.5, 102.5, 102.5, 103, 101]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close[:], "high": [value + .8 for value in close],
        "low": [value - .8 for value in close], "close": close,
    })
    one.loc[one.index[-3], ["open", "close"]] = [102.5, 102.52]
    one.loc[one.index[-2], ["open", "close"]] = [103.5, 102.4]
    one.loc[one.index[-1], ["open", "close", "high", "low"]] = [102.0, 101.0, 102.2, 100.7]

    active, reason, _ = aggressive_top_weakening_ma5_short_setup(one, pd.DataFrame())

    assert not active
    assert "旧顶部授权不得在后续下沿补追" in reason


def test_fresh_death_cross_accepts_wick_below_ma5_without_bearish_body():
    close = [100.0] * 20 + [101, 102, 103, 104, 103, 102, 101, 100.5, 100.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [value - .10 for value in close],
        "high": [value + .25 for value in close], "low": [value - .25 for value in close],
        "close": close,
    })
    active, reason, _, _ = top_weakening_ma5_short_trigger(one)
    assert active and "小死叉" in reason and "下影线" in reason


def test_top_weakening_does_not_call_ma5_noise_below_ma20_a_local_high():
    close = [110.0] * 22 + [101, 102, 103, 104, 102, 103, 103.5, 102.5, 102.5, 103, 101]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close[:], "high": [value + .8 for value in close],
        "low": [value - .8 for value in close], "close": close,
    })
    one.loc[one.index[-3], ["open", "close"]] = [102.5, 102.52]
    one.loc[one.index[-2], ["open", "close"]] = [103.5, 103.51]
    one.loc[one.index[-1], ["open", "close", "high", "low"]] = [103.0, 102.0, 103.2, 101.7]
    active, reason, _ = aggressive_top_weakening_ma5_short_setup(one, pd.DataFrame())
    assert not active
    assert "局部高点必须位于MA20上方" in reason


def test_double_top_ma5_break_rejects_a_lower_edge_short():
    close = [102.0] * 24 + [102.0, 102.5, 103.5, 102.5, 102.0, 102.4, 103.45, 102.8, 101.7]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close[:], "high": [value + .45 for value in close],
        "low": [value - .45 for value in close], "close": close,
    })
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [102.5, 102.6, 101.5, 101.7]

    active, reason, stop = aggressive_top_weakening_ma5_short_setup(one, pd.DataFrame())

    assert not active
    assert "双顶" in reason and "旧顶部授权不得在后续下沿补追" in reason
    assert stop == 0.0


def test_head_and_shoulders_ma5_break_rejects_a_lower_edge_short():
    close = ([102.0] * 25
             + [102.2, 102.8, 103.0, 102.7, 102.2]
             + [102.0] * 4
             + [102.4, 103.3, 103.8, 103.2, 102.4]
             + [102.0] * 4
             + [102.3, 102.8, 103.0, 102.7, 101.9])
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close[:], "high": [value + .40 for value in close],
        "low": [value - .40 for value in close], "close": close,
    })
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [102.5, 102.6, 101.7, 101.9]

    active, reason, stop = aggressive_top_weakening_ma5_short_setup(one, pd.DataFrame())

    assert not active
    assert "头肩顶" in reason and "旧顶部授权不得在后续下沿补追" in reason
    assert stop == 0.0


def test_expanded_mas_local_top_red_cross_ma5_is_an_independent_short():
    close = [100.0] * 24 + [101, 102, 103, 104, 105, 106, 107, 108, 109, 106]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close[:], "high": [value + .5 for value in close],
        "low": [value - .5 for value in close], "close": close,
    })
    one.loc[one.index[-2], "open"] = 108.0
    one.loc[one.index[-1], ["open", "close", "high", "low"]] = [109.0, 106.0, 109.1, 105.8]
    active, reason, stop = aggressive_expanded_ma_top_ma5_short_setup(one, pd.DataFrame())
    assert active
    assert "均线原本向上发散" in reason and "跌破MA5" in reason
    assert stop > 109.1


def test_five_minute_high_half_cover_short_does_not_wait_for_ma5_cross():
    five = market([100.0] * 22 + [100.2, 104.0, 103.0], spread=.20)
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [102.0, 104.3, 101.8, 104.0]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [104.0, 104.1, 102.8, 103.0]
    one = market([102.0] * 20 + [102.1], spread=.12)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-1], "open"] = 102.25
    one.loc[one.index[-1], "open"] = 102.25
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [102.2, 102.25, 102.0, 102.1]

    current_five_ma5 = float(five["close"].rolling(5).mean().iloc[-1])
    assert float(five.iloc[-1]["close"]) > current_five_ma5

    active, reason, stop = aggressive_five_minute_high_half_cover_short_setup(five, one)

    assert active, reason
    assert "5分钟阴线覆盖相邻前阳线实体" in reason
    assert "MA5外沿的首根新鲜转弱阴线" in reason
    assert 102.25 < stop < 102.5


def test_five_minute_local_lower_high_half_cover_is_not_tied_to_24_bar_high():
    five = market([100.0] * 19 + [112.0, 101.0, 102.0, 104.0, 103.0], spread=.20)
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [102.0, 104.3, 101.8, 104.0]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [104.0, 104.1, 102.8, 103.0]
    one = market([102.0] * 20 + [102.1], spread=.12)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")
    one.loc[one.index[-1], "open"] = 102.25

    active, reason, stop = aggressive_five_minute_high_half_cover_short_setup(five, one)

    assert active, reason
    assert 102.25 < stop < 102.5


def test_five_minute_low_half_cover_long_mirrors_top_confirmation():
    five = market([100.0] * 22 + [100.0, 96.0, 97.0], spread=.20)
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [98.0, 98.2, 95.7, 96.0]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [96.0, 97.2, 95.9, 97.1]
    one = market([97.0] * 20 + [97.2], spread=.12)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="1min")

    active, reason, stop = aggressive_five_minute_low_half_cover_long_setup(five, one)

    assert active, reason
    assert stop < 95.7


def test_two_five_minute_bulls_do_not_replace_one_minute_mixed_cluster():
    five = market([100.0] * 22 + [96.0, 96.8, 97.2], spread=.20)
    five.loc[five.index[-3], ["open", "high", "low", "close"]] = [98.0, 98.2, 95.7, 96.0]
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [96.0, 97.0, 95.9, 96.8]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [96.8, 97.3, 96.7, 97.2]
    one = market([97.0] * 20 + [97.2], spread=.12)

    active, reason, stop = aggressive_five_minute_low_half_cover_long_setup(five, one)

    assert not active
    assert "adjacent pullback bear" in reason
    assert stop == 0.0


def test_two_five_minute_bears_do_not_replace_one_minute_mixed_cluster():
    five = market([100.0] * 22 + [104.0, 103.4, 103.0], spread=.20)
    five.loc[five.index[-3], ["open", "high", "low", "close"]] = [102.0, 104.3, 101.8, 104.0]
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [104.0, 104.1, 103.3, 103.4]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [103.4, 103.5, 102.8, 103.0]
    one = market([103.0] * 20 + [103.1], spread=.12)
    one.loc[one.index[-1], "open"] = 103.25

    active, reason, stop = aggressive_five_minute_high_half_cover_short_setup(five, one)

    assert not active
    assert stop == 0.0


def test_five_minute_top_cover_uses_nearest_qualifying_impulse_for_stop():
    five = market([100.0] * 20 + [110.0, 103.0, 104.0, 103.0], spread=.20)
    five.loc[five.index[-4], ["open", "high", "low", "close"]] = [100.0, 110.4, 99.8, 110.0]
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [102.0, 104.3, 101.8, 104.0]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [104.0, 104.1, 102.8, 103.0]
    one = market([103.0] * 20 + [103.1], spread=.12)
    one.loc[one.index[-1], "open"] = 103.25

    active, reason, stop = aggressive_five_minute_high_half_cover_short_setup(five, one)

    assert active, reason
    assert 103.25 < stop < 103.5


def test_mixed_six_bar_five_minute_bottom_cluster_is_valid():
    five = market([100.0] * 18 + [96.0, 96.5, 96.2, 96.8, 96.6, 97.2], spread=.20)
    five.loc[five.index[-6], ["open", "high", "low", "close"]] = [98.0, 98.2, 95.6, 96.0]
    mixed = [(96.0, 96.5), (96.5, 96.2), (96.2, 96.8), (96.8, 96.6), (96.6, 97.2)]
    for index, (open_, close) in zip(five.index[-5:], mixed):
        five.loc[index, ["open", "high", "low", "close"]] = [
            open_, max(open_, close) + .2, min(open_, close) - .2, close]
    one = market([97.0] * 20 + [97.2], spread=.12)

    active, reason, stop = aggressive_five_minute_low_half_cover_long_setup(five, one)

    assert active, reason
    assert "mixed" in reason and "six" in reason
    assert 96.0 < stop < 96.4


def test_mixed_six_bar_five_minute_top_cluster_is_valid():
    five = market([100.0] * 18 + [104.0, 103.6, 103.8, 103.3, 103.5, 103.0], spread=.20)
    five.loc[five.index[-6], ["open", "high", "low", "close"]] = [102.0, 104.4, 101.8, 104.0]
    mixed = [(104.0, 103.6), (103.6, 103.8), (103.8, 103.3), (103.3, 103.5), (103.5, 103.0)]
    for index, (open_, close) in zip(five.index[-5:], mixed):
        five.loc[index, ["open", "high", "low", "close"]] = [
            open_, max(open_, close) + .2, min(open_, close) - .2, close]
    one = market([103.0] * 20 + [103.1], spread=.12)
    one.loc[one.index[-1], "open"] = 103.25

    active, reason, stop = aggressive_five_minute_high_half_cover_short_setup(five, one)

    assert active, reason
    assert 103.25 < stop < 103.5


def test_five_minute_high_half_cover_rejects_late_short_below_one_minute_ma5():
    five = market([100.0] * 22 + [100.2, 104.0, 103.0], spread=.20)
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [102.0, 104.3, 101.8, 104.0]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [104.0, 104.1, 102.8, 103.0]
    one = market([102.0] * 20 + [101.0], spread=.12)

    active, reason, stop = aggressive_five_minute_high_half_cover_short_setup(five, one)

    assert not active
    assert "没有在MA5外沿形成当前新鲜阴线转弱" in reason
    assert stop == 0.0


def test_price_crosses_inside_flat_falling_ma5_with_45_percent_five_minute_cover():
    five = market([100.0] * 22 + [100.2, 104.0, 103.0], spread=.20)
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [102.0, 104.3, 101.8, 104.0]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [104.0, 104.1, 102.8, 103.0]
    one = market([102.5] * 17 + [102.45, 102.40, 102.35, 101.90], spread=.08)
    one["date"] = pd.date_range("2026-09-15 13:50", periods=len(one), freq="1min")
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [102.38, 102.42, 101.85, 101.90]
    ma5 = one["close"].astype(float).rolling(5).mean()
    assert one.iloc[-1]["close"] < ma5.iloc[-1]
    assert ma5.iloc[-1] <= ma5.iloc[-2]

    active, reason, stop = aggressive_five_minute_high_half_cover_short_setup(
        five, one, downtrend_frame("15min"), downtrend_frame("1h"))

    assert active, reason
    assert "15分钟+1小时下降趋势反抽追空" in reason
    assert "45" in reason or "50%" in reason
    assert stop > one.tail(3)["high"].max()
