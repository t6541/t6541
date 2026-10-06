import pandas as pd

from quantbot.price_reversal import recent_ma_fan_endpoint

from quantbot.primary_timeframe import (
    _five_minute_endpoint_half_cover_trigger,
    _five_minute_closed_ma5_one_minute_ma20_break_long_trigger,
    _five_minute_higher_low_ma5_long_trigger,
    _five_minute_uptrend_pullback_resume_long_trigger,
    _recent_double_bottom,
    confirmed_price_structure,
    five_minute_primary_entry_gate,
)


def test_equal_shape_lows_with_intervening_rebound_form_double_bottom():
    frame = _trend_frame([100.0] * 8, "5min")
    frame.loc[:, "high"] = [101.0, 100.5, 100.1, 101.0, 102.0, 100.6, 100.2, 101.2]
    frame.loc[:, "low"] = [99.8, 99.2, 98.0, 99.0, 100.0, 99.1, 98.05, 99.0]
    assert _recent_double_bottom(frame, 2.0)


def test_closed_five_ma5_reclaim_and_first_one_ma20_break_enters_before_fan_or_retest():
    five = _trend_frame([100.0] * 20 + [99.0, 98.5, 98.8, 99.0, 99.6, 100.2], "5min")
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [99.4, 99.5, 98.8, 99.0]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [99.0, 100.5, 98.9, 100.2]
    one = _trend_frame([100.0] * 20 + [99.2, 99.0, 98.9, 99.0, 99.1, 99.2], "1min")
    live_one = _trend_frame([99.9], "1min")
    live_one.loc[0, ["open", "high", "low", "close"]] = [99.18, 100.0, 99.15, 99.9]

    allowed, reason, stop, candle_time, _ = _five_minute_closed_ma5_one_minute_ma20_break_long_trigger(
        five, one, live_one, 1,
    )

    assert allowed, reason
    assert "不等待MA20回踩" in reason
    assert "不等待MA5/MA10/MA20发散" in reason
    assert stop < 98.9
    assert candle_time == five.iloc[-1]["date"]


def _trend_frame(closes: list[float], freq: str) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2026-08-27", periods=len(closes), freq=freq),
        "open": [value - .15 for value in closes],
        "high": [value + .45 for value in closes],
        "low": [value - .45 for value in closes],
        "close": closes,
        "volume": [100.0] * len(closes),
    })


def test_mature_uptrend_ma5_ma10_pullback_resumes_long_intrabar_without_ma20_retest():
    five_close = [100 + i * .25 for i in range(24)] + [107, 109, 111, 110.2, 109.8, 110.1]
    five = _trend_frame(five_close, "5min")
    fifteen = _trend_frame([90 + i * .55 for i in range(30)], "15min")
    one = _trend_frame([108 + i * .08 for i in range(30)], "1min")
    live_five = _trend_frame([110.65], "5min")
    live_five.loc[0, ["open", "low", "high"]] = [110.05, 109.9, 110.8]
    live_one = _trend_frame([110.7], "1min")
    live_one.loc[0, ["open", "low", "high"]] = [110.15, 110.0, 110.85]
    allowed, reason, stop, _, _ = _five_minute_uptrend_pullback_resume_long_trigger(
        five, live_five, one, live_one, fifteen, 1,
    )
    assert allowed
    assert "不要求再次回踩一分钟MA20" in reason
    assert stop < 109.9


def test_mature_uptrend_pullback_resume_rejects_late_chase_far_above_ma5():
    five_close = [100 + i * .25 for i in range(24)] + [107, 109, 111, 110.2, 109.8, 110.1]
    five = _trend_frame(five_close, "5min")
    fifteen = _trend_frame([90 + i * .55 for i in range(30)], "15min")
    one = _trend_frame([108 + i * .08 for i in range(30)], "1min")
    live_five = _trend_frame([114.0], "5min")
    live_five.loc[0, ["open", "low", "high"]] = [110.05, 109.9, 114.2]
    live_one = _trend_frame([114.0], "1min")
    live_one.loc[0, "open"] = 113.0
    allowed, reason, *_ = _five_minute_uptrend_pullback_resume_long_trigger(
        five, live_five, one, live_one, fifteen, 1,
    )
    assert not allowed
    assert "0.45 ATR" in reason


def test_higher_five_minute_lows_and_sustained_one_minute_above_ma20_trigger_live_long():
    five_close = [100.0] * 18 + [98, 99, 100, 99, 100, 101, 100, 101, 102, 101, 102, 102.5]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": five_close, "high": [v + .4 for v in five_close],
        "low": [v - .4 for v in five_close], "close": five_close, "volume": [100.0] * 30,
    })
    live_five = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:30")], "open": [102.0], "high": [103.7],
        "low": [101.8], "close": [103.5], "volume": [150.0],
    })
    one_close = [100.0] * 20 + [101, 102, 103, 102, 103, 104, 105, 106, 107, 108]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="1min"),
        "open": one_close, "high": [v + .2 for v in one_close],
        "low": [v - .2 for v in one_close], "close": one_close, "volume": [50.0] * 30,
    })
    live_one = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 00:30")], "open": [108.0], "high": [109.0],
        "low": [107.9], "close": [108.8], "volume": [60.0],
    })
    allowed, reason, stop, candle_time, _ = _five_minute_higher_low_ma5_long_trigger(
        five, live_five, one, live_one, 1,
    )
    assert allowed
    assert "连续运行在其上方" in reason
    assert stop < 101.8
    assert candle_time == pd.Timestamp("2026-01-01 02:30")


def test_early_bottom_reversal_does_not_require_one_minute_ma20_retest():
    five_close = [100.0] * 18 + [98, 99, 100, 99, 100, 101, 100, 101, 102, 101, 102, 102.5]
    five = _trend_frame(five_close, "5min")
    live_five = _trend_frame([103.5], "5min")
    live_five.loc[0, ["open", "low", "high"]] = [102.0, 101.8, 103.7]
    # Monotonic lift above MA20: no pivot/retest exists in the latest segment.
    one = _trend_frame([100.0] * 20 + [101, 102, 103, 104, 105, 106, 107, 108, 109, 110], "1min")
    live_one = _trend_frame([110.8], "1min")
    live_one.loc[0, ["open", "low", "high"]] = [110.0, 109.9, 111.0]
    allowed, reason, stop, *_ = _five_minute_higher_low_ma5_long_trigger(
        five, live_five, one, live_one, 1,
    )
    assert allowed
    assert "无需等待回踩MA20" in reason
    assert stop < 101.8


def _structure(direction: int, freq: str = "15min") -> pd.DataFrame:
    size = 30
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=size, freq=freq),
        "open": [100.0] * size,
        "close": [100.0] * size,
        "high": [101.0] * size,
        "low": [99.0] * size,
    })
    if direction > 0:
        frame.loc[[5, 13, 21], "high"] = [105.0, 107.0, 109.0]
        frame.loc[[9, 17, 25], "low"] = [95.0, 97.0, 99.0]
    elif direction < 0:
        frame.loc[[5, 13, 21], "high"] = [109.0, 107.0, 105.0]
        frame.loc[[9, 17, 25], "low"] = [99.0, 97.0, 95.0]
    else:
        frame.loc[[5, 13, 21], "high"] = [105.0, 107.0, 106.0]
        frame.loc[[9, 17, 25], "low"] = [95.0, 96.0, 97.0]
    return frame


def _closing_structure(direction: int, freq: str = "15min") -> pd.DataFrame:
    frame = _structure(0, freq)
    if direction > 0:
        frame.loc[[5, 13, 21], "close"] = [104.0, 106.0, 108.0]
        frame.loc[[9, 17, 25], "close"] = [96.0, 97.0, 98.0]
    elif direction < 0:
        frame.loc[[5, 13, 21], "close"] = [108.0, 106.0, 104.0]
        frame.loc[[9, 17, 25], "close"] = [98.0, 97.0, 96.0]
    else:
        frame.loc[[5, 13, 21], "close"] = [104.0, 106.0, 105.0]
        frame.loc[[9, 17, 25], "close"] = [96.0, 97.0, 98.0]
    return frame


def _five_minute_long_trigger() -> pd.DataFrame:
    frame = _structure(1, "5min")
    frame.loc[28, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
    frame.loc[29, ["open", "high", "low", "close"]] = [100.0, 102.0, 99.1, 101.8]
    return frame


def _one_minute() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="1min"),
        "open": [100.0] * 30,
        "high": [101.0] * 30,
        "low": [99.0] * 30,
        "close": [100.0] * 29 + [100.5],
    })


def test_closing_highs_and_lows_are_primary_trend_definition():
    assert confirmed_price_structure(_closing_structure(1), "15分钟").direction == 1
    assert confirmed_price_structure(_closing_structure(-1), "1小时").direction == -1
    assert confirmed_price_structure(_closing_structure(0), "15分钟").direction == 0


def test_wicks_cannot_override_sideways_closing_price_structure():
    frame = _structure(1)
    result = confirmed_price_structure(frame, "15分钟")
    assert result.direction == 0
    assert "收盘" in result.reason


def test_option_c_allows_one_supportive_and_one_ranging_timeframe():
    gate = five_minute_primary_entry_gate(
        _five_minute_long_trigger(), _structure(1), _structure(0, "1h"), _one_minute(), 1,
    )
    assert gate.allowed
    assert "方案C通过" in gate.reason
    assert gate.stop < 99.1


def test_option_c_keeps_opposed_higher_structure_as_context_only():
    gate = five_minute_primary_entry_gate(
        _five_minute_long_trigger(), _closing_structure(1), _closing_structure(-1, "1h"), _one_minute(), 1,
    )
    assert gate.allowed
    assert "不机械拦截" in gate.reason
    assert "1小时收盘价下跌" in gate.reason


def _fan_endpoint_one_minute(direction: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    if direction < 0:
        closes = [96.0] * 20 + [96.5, 97.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0]
        live_values = [103.0, 103.1, 102.35, 102.5]
    else:
        closes = [104.0] * 20 + [103.5, 103.0, 102.0, 101.0, 100.0, 99.0, 98.0, 97.0]
        live_values = [97.0, 97.5, 96.9, 97.45]
    closed = _trend_frame(closes, "1min")
    live = pd.DataFrame({
        "date": [closed.iloc[-1]["date"] + pd.Timedelta(minutes=1)],
        "open": [live_values[0]], "high": [live_values[1]],
        "low": [live_values[2]], "close": [live_values[3]], "volume": [100.0],
    })
    return closed, live


def test_one_minute_top_fan_endpoint_and_five_minute_half_cover_short_before_ma5_cross():
    five = _trend_frame([100.0] * 23 + [102.0], "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [100.0, 102.3, 99.9, 102.0]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [102.0], "high": [102.2], "low": [100.8], "close": [100.9], "volume": [100.0],
    })
    one, live_one = _fan_endpoint_one_minute(-1)
    allowed, reason, stop, _, _ = _five_minute_endpoint_half_cover_trigger(
        five, live_five, one, live_one, -1,
    )
    assert allowed, reason
    assert "不等待收盘" in reason
    assert "MA5或慢均线穿越" in reason
    assert stop > 103.1


def test_one_minute_bottom_fan_endpoint_and_five_minute_half_cover_long_is_mirrored():
    five = _trend_frame([100.0] * 23 + [98.0], "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [100.0, 100.1, 97.7, 98.0]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [98.0], "high": [99.2], "low": [97.8], "close": [99.1], "volume": [100.0],
    })
    one, live_one = _fan_endpoint_one_minute(1)
    allowed, reason, stop, _, _ = _five_minute_endpoint_half_cover_trigger(
        five, live_five, one, live_one, 1,
    )
    assert allowed, reason
    assert "不等待收盘" in reason
    assert "MA5或慢均线穿越" in reason
    assert stop < 96.9


def test_five_minute_half_cover_without_full_ma_confirmation_is_local_reversal_identity():
    five = _trend_frame([100.0] * 23 + [98.0], "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [100.0, 100.1, 97.7, 98.0]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [98.0], "high": [99.2], "low": [97.8], "close": [99.1], "volume": [100.0],
    })
    one, live_one = _fan_endpoint_one_minute(1)
    gate = five_minute_primary_entry_gate(
        five, _structure(-1), _structure(-1, "1h"), one, 1,
        live_five, live_one,
    )
    assert gate.allowed
    assert gate.trigger_code == "five_minute_bottom_local_reversal_half_cover_long"


def test_one_minute_endpoint_trial_does_not_wait_for_five_minute_endpoint_or_lock():
    five = _trend_frame([100.0] * 24, "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [100.0, 100.25, 99.95, 100.20]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [100.20], "high": [100.22], "low": [100.05],
        "close": [100.10], "volume": [100.0],
    })
    assert recent_ma_fan_endpoint(five, -1, lookback=6) is None
    one, live_one = _fan_endpoint_one_minute(-1)
    allowed, reason, stop, _, _ = _five_minute_endpoint_half_cover_trigger(
        five, live_five, one, live_one, -1)
    assert allowed, reason
    assert "自身末端锁定、周期配对" in reason
    assert stop > 103.1


def test_endpoint_does_not_trigger_before_five_minute_half_cover():
    five = _trend_frame([100.0] * 23 + [102.0], "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [100.0, 102.3, 99.9, 102.0]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [102.0], "high": [102.2], "low": [101.2], "close": [101.2], "volume": [100.0],
    })
    one, live_one = _fan_endpoint_one_minute(-1)
    allowed, reason, *_ = _five_minute_endpoint_half_cover_trigger(
        five, live_five, one, live_one, -1,
    )
    assert not allowed
    assert "约半覆盖" in reason


def test_five_minute_endpoint_can_use_a_following_cover_within_three_bars():
    five = _trend_frame([100.0] * 20 + [101.0, 103.0, 101.0, 102.0], "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [101.0, 102.2, 100.9, 102.0]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [102.0], "high": [102.1], "low": [101.2],
        "close": [101.4], "volume": [100.0],
    })
    one, live_one = _fan_endpoint_one_minute(-1)
    allowed, reason, *_ = _five_minute_endpoint_half_cover_trigger(
        five, live_five, one, live_one, -1)
    assert allowed, reason
    assert "一至三根反向K线" in reason


def test_local_reversal_accepts_intrabar_five_minute_half_cover_for_compact_stop():
    five = _trend_frame([100.0] * 23 + [98.0], "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [100.0, 100.1, 97.7, 98.0]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [98.0], "high": [99.2], "low": [97.8], "close": [99.1], "volume": [100.0],
    })
    one, live_one = _fan_endpoint_one_minute(1)
    allowed, reason, *_ = _five_minute_endpoint_half_cover_trigger(
        five, live_five, one, live_one, 1)
    assert allowed, reason
    assert "不等待收盘" in reason


def test_one_minute_cannot_authorize_without_five_minute_trigger():
    five = _five_minute_long_trigger()
    five.loc[29, ["open", "high", "low", "close"]] = [101.0, 101.2, 99.8, 100.0]
    gate = five_minute_primary_entry_gate(
        five, _structure(1), _structure(0, "1h"), _one_minute(), 1,
    )
    assert not gate.allowed
    assert "不接受一分钟独立做多" in gate.reason


def test_live_five_minute_ma5_break_can_trigger_before_close():
    five = _structure(0, "5min")
    five.loc[26, ["open", "high", "low", "close"]] = [100.0, 102.5, 99.8, 102.0]
    five.loc[27, ["open", "high", "low", "close"]] = [102.0, 102.1, 101.2, 101.4]
    five.loc[28, ["open", "high", "low", "close"]] = [101.5, 101.7, 101.3, 101.5]
    five.loc[29, ["open", "high", "low", "close"]] = [101.4, 101.9, 101.3, 101.8]
    live_five = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:30")], "open": [101.8], "high": [102.0],
        "low": [99.2], "close": [99.5], "volume": [100.0],
    })
    live_one = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:32")], "open": [100.5], "high": [100.6],
        "low": [99.3], "close": [99.5], "volume": [20.0],
    })
    gate = five_minute_primary_entry_gate(
        five, _structure(1), _structure(1, "1h"), _one_minute(), -1,
        live_five, live_one,
    )
    assert gate.allowed
    assert "盘中快空" in gate.reason
    assert "不等待五分钟收盘" in gate.reason
    assert gate.candle_time == pd.Timestamp("2026-01-01 02:30")


def test_live_five_minute_break_requires_one_minute_confirmation():
    five = _structure(0, "5min")
    five.loc[26, ["open", "high", "low", "close"]] = [100.0, 102.5, 99.8, 102.0]
    five.loc[27, ["open", "high", "low", "close"]] = [102.0, 102.1, 101.2, 101.4]
    five.loc[28, ["open", "high", "low", "close"]] = [101.5, 101.7, 101.3, 101.5]
    five.loc[29, ["open", "high", "low", "close"]] = [101.4, 101.9, 101.3, 101.8]
    live_five = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:30")], "open": [101.8], "high": [102.0],
        "low": [99.2], "close": [99.5], "volume": [100.0],
    })
    bullish_one = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:32")], "open": [99.4], "high": [100.6],
        "low": [99.3], "close": [100.5], "volume": [20.0],
    })
    gate = five_minute_primary_entry_gate(
        five, _structure(1), _structure(1, "1h"), _one_minute(), -1,
        live_five, bullish_one,
    )
    assert not gate.allowed
    assert "一分钟同步确认" in gate.reason


def test_downtrend_live_five_minute_ma20_pullback_rejection_can_short():
    five = _structure(-1, "5min")
    five.loc[:, "close"] = [112.0 - i * .4 for i in range(len(five))]
    five.loc[:, "open"] = five["close"] + .10
    five.loc[:, "high"] = five[["open", "close"]].max(axis=1) + .20
    five.loc[:, "low"] = five[["open", "close"]].min(axis=1) - .20
    ma20 = float(five["close"].tail(19).sum() / 19.0)
    live_five = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:30")], "open": [ma20 + .20],
        "high": [ma20 + .30], "low": [ma20 - .50], "close": [ma20 - .40], "volume": [100.0],
    })
    one = _one_minute()
    one.loc[one.index[-6]:, "high"] = 100.0
    live_one = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:32")], "open": [101.0], "high": [101.2],
        "low": [99.0], "close": [99.2], "volume": [20.0],
    })
    gate = five_minute_primary_entry_gate(
        five, _structure(-1), _structure(-1, "1h"), one, -1, live_five, live_one,
    )
    assert gate.allowed
    assert "反抽MA20快空" in gate.reason
    assert "一分钟局部高点" in gate.reason


def test_lower_high_short_allows_ma5_above_ma20_when_live_fifteen_confirms():
    close = [120 - i * .5 for i in range(25)] + [110, 111, 112, 113, 114]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [v + .1 for v in close], "high": [v + .3 for v in close],
        "low": [v - .3 for v in close], "close": close, "volume": [100.0] * 30,
    })
    live_five = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:30")], "open": [112.0], "high": [112.2],
        "low": [108.5], "close": [109.0], "volume": [100.0],
    })
    one = _one_minute()
    live_one = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:32")], "open": [101.0], "high": [101.2],
        "low": [99.0], "close": [99.2], "volume": [20.0],
    })
    fifteen = _structure(0)
    fifteen.loc[:, ["open", "high", "low", "close"]] = [100.0, 100.3, 99.7, 100.0]
    fifteen.loc[fifteen.index[-1], ["open", "high", "low", "close"]] = [99.0, 102.3, 98.8, 102.0]
    live_fifteen = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-01 02:30")], "open": [102.0], "high": [103.0],
        "low": [98.8], "close": [99.0], "volume": [200.0],
    })
    gate = five_minute_primary_entry_gate(
        five, fifteen, _structure(1, "1h"), one, -1,
        live_five, live_one, live_fifteen,
    )
    assert gate.allowed
    assert "十五分钟长阴覆盖前阳线一半并下穿MA20" in gate.reason
def test_high_uptrend_pullback_cannot_impersonate_five_minute_local_bottom():
    five = _trend_frame([96.0 + i * .25 for i in range(23)] + [101.0], "5min")
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [102.0, 102.1, 100.7, 101.0]
    live_five = pd.DataFrame({
        "date": [five.iloc[-1]["date"] + pd.Timedelta(minutes=5)],
        "open": [101.0], "high": [101.7], "low": [100.9], "close": [101.6], "volume": [100.0],
    })
    one, live_one = _fan_endpoint_one_minute(1)
    allowed, reason, *_ = _five_minute_endpoint_half_cover_trigger(
        five, live_five, one, live_one, 1)
    assert not allowed
    assert "5m bottom local reversal identity rejected" in reason

