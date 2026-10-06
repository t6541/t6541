import pandas as pd
from types import SimpleNamespace

import quantbot.trend_continuation as trend_continuation

from quantbot.trend_continuation import (
    downtrend_ma5_pullback_short_setup,
    aggressive_double_rejection_ma5_short_setup,
    downtrend_pullback_short_setup,
    three_step_pullback_structure,
    trend_entry_runway,
    uptrend_ma5_pullback_long_setup,
    uptrend_pullback_long_setup,
)


def test_three_descending_five_minute_rebound_highs_are_explicit_short_structure():
    closes = ([108.0] * 5) + ([106.0] * 5) + ([104.0] * 5)
    frame = candles(closes, "5min")
    frame.loc[:4, "high"] = 110.0
    frame.loc[5:9, "high"] = 108.0
    frame.loc[10:14, "high"] = 106.0
    active, reason = three_step_pullback_structure(frame, -1)
    assert active
    assert "三个反抽高点依次降低" in reason


def test_flat_five_minute_rebound_highs_do_not_arm_short():
    frame = candles([100.0] * 25, "5min")
    active, _ = three_step_pullback_structure(frame, -1)
    assert not active


def candles(closes, frequency):
    return pd.DataFrame({
        "date": pd.date_range("2026-08-12", periods=len(closes), freq=frequency),
        "open": [value + .05 for value in closes],
        "high": [value + .20 for value in closes],
        "low": [value - .20 for value in closes],
        "close": closes,
    })


def continuation_market():
    five = candles([120 - index * .35 for index in range(40)], "5min")
    five["high"] = five["close"] + 1.0
    five["low"] = five["close"] - 1.0
    fifteen = candles([130 - index * .45 for index in range(40)], "15min")
    one = candles([112 - index * .10 for index in range(40)], "1min")
    # A brief rally reaches the falling 1m averages, followed by a decisive
    # lower-low rejection candle.  The setup remains close enough to 5m MA20.
    one.loc[35:38, "high"] += .75
    one.loc[35:38, "close"] += .45
    one.loc[39, ["open", "high", "low", "close"]] = [108.65, 108.75, 107.75, 107.85]
    five.loc[39, ["open", "high", "low", "close"]] = [107.15, 107.35, 106.75, 106.95]
    return five, one, fifteen


def mirrored_continuation_market():
    mirrored = []
    for frame in continuation_market():
        result = frame.copy()
        result["open"] = 250.0 - frame["open"]
        result["high"] = 250.0 - frame["low"]
        result["low"] = 250.0 - frame["high"]
        result["close"] = 250.0 - frame["close"]
        mirrored.append(result)
    return tuple(mirrored)


def test_five_and_fifteen_minute_downtrend_can_trigger_one_minute_pullback_short():
    active, reason, stop = downtrend_pullback_short_setup(*continuation_market())
    assert active
    assert "追空" in reason
    assert stop > 107.85


def test_higher_timeframe_bearish_waiting_does_not_blanket_block_pullback_short(monkeypatch):
    monkeypatch.setattr(
        trend_continuation, "classify_trend_regime",
        lambda *_: SimpleNamespace(
            state="bullish_reversal_waiting_higher_timeframe",
            reason="五分钟看涨反转等候，但十五分钟仍为强下跌",
        ),
    )
    active, reason, _ = downtrend_pullback_short_setup(*continuation_market())
    assert active, reason


def test_continuation_short_requires_fifteen_minute_confirmation():
    five, one, fifteen = continuation_market()
    fifteen["close"] = [100 + index * .4 for index in range(len(fifteen))]
    fifteen["open"] = fifteen["close"] - .05
    fifteen["high"] = fifteen["close"] + .2
    fifteen["low"] = fifteen["close"] - .2
    active, _, _ = downtrend_pullback_short_setup(five, one, fifteen)
    assert not active


def test_five_and_fifteen_minute_uptrend_can_trigger_one_minute_pullback_long():
    active, reason, stop = uptrend_pullback_long_setup(*mirrored_continuation_market())
    assert active, reason
    assert "追多" in reason
    assert stop < 142.15


def test_continuation_long_requires_fifteen_minute_confirmation():
    five, one, fifteen = mirrored_continuation_market()
    fifteen["close"] = [150 - index * .4 for index in range(len(fifteen))]
    fifteen["open"] = fifteen["close"] + .05
    fifteen["high"] = fifteen["close"] + .2
    fifteen["low"] = fifteen["close"] - .2
    active, _, _ = uptrend_pullback_long_setup(five, one, fifteen)
    assert not active


def test_continuation_short_rejects_low_chasing_beyond_one_atr():
    five, one, fifteen = continuation_market()
    one.loc[39, ["open", "high", "low", "close"]] = [95.0, 95.1, 93.8, 94.0]
    active, reason, _ = downtrend_pullback_short_setup(five, one, fifteen)
    assert not active
    assert "剩余利润空间不足" in reason


def test_pullback_short_allows_temporary_fast_average_lift():
    five, one, fifteen = continuation_market()
    # A rebound near the arrow can lift MA5 above MA10 while the falling MA20
    # and the sequence of lower highs still define the bearish background.
    five.loc[37:39, "close"] += [0.15, 0.25, 0.35]
    five.loc[37:39, "open"] = five.loc[37:39, "close"] + .10
    five.loc[37:39, "high"] = five.loc[37:39, "close"] + .45
    five.loc[37:39, "low"] = five.loc[37:39, "close"] - .45
    one.loc[38, ["open", "high", "low", "close"]] = [108.80, 108.90, 108.40, 108.60]
    one.loc[39, ["open", "high", "low", "close"]] = [109.05, 109.15, 108.00, 108.10]
    active, reason, _ = downtrend_pullback_short_setup(five, one, fifteen)
    assert active, reason


def test_pullback_breakdown_confirmation_survives_two_polling_bars():
    five, one, fifteen = continuation_market()
    confirmation = one.loc[39].copy()
    one.loc[37, ["open", "high", "low", "close"]] = confirmation
    one.loc[38, ["open", "high", "low", "close"]] = [108.00, 108.10, 107.92, 108.02]
    one.loc[39, ["open", "high", "low", "close"]] = [108.04, 108.14, 107.98, 108.08]
    active, reason, _ = downtrend_pullback_short_setup(five, one, fifteen)
    assert active, reason
    assert "2根前" in reason


def test_shared_gate_blocks_the_strategy_one_late_short_pattern_and_mirrors_long():
    five, _, _ = continuation_market()
    ma20 = float(five["close"].rolling(20).mean().iloc[-1])
    # Reproduce the defect class: entry is nearly 2 ATR below the falling
    # MA20, even though a wide structural stop could still be below 2.5 ATR.
    short_ok, short_reason, atr_value = trend_entry_runway(five, ma20 - 1.8 * 2.0, -1)
    assert not short_ok
    assert "剩余利润空间不足" in short_reason
    long_ok, long_reason, _ = trend_entry_runway(five, ma20 + atr_value * 1.8, 1)
    assert not long_ok
    assert "高位追多" in long_reason


def ma5_early_continuation_market():
    five, one, fifteen = continuation_market()
    five_ma20 = float(five["close"].rolling(20).mean().iloc[-1])
    values = [five_ma20 + .5, five_ma20 + .3, five_ma20 + .1,
              five_ma20 - .1, five_ma20 - .2, five_ma20 - .3]
    index = five.index[-6:]
    five.loc[index, "close"] = values
    five.loc[index, "open"] = five.loc[index, "close"] + .05
    five.loc[index, "high"] = five.loc[index, "close"] + .5
    five.loc[index, "low"] = five.loc[index, "close"] - .5
    aligned_five_ma20 = float(five["close"].rolling(20).mean().iloc[-1])
    delta = aligned_five_ma20 - float(one["close"].rolling(20).mean().iloc[-1])
    for column in ("open", "high", "low", "close"):
        one[column] = one[column].astype(float) + delta
    one_ma20 = float(one["close"].rolling(20).mean().iloc[-2])
    values = [one_ma20 - 1, one_ma20, one_ma20 + 1, one_ma20 + 2,
              one_ma20 + 3, one_ma20 + 1, one_ma20 - 1]
    index = one.index[-7:]
    one.loc[index, "close"] = values
    one.loc[index, "open"] = one.loc[index, "close"] - .05
    one.loc[index, "high"] = one.loc[index, "close"] + .2
    one.loc[index, "low"] = one.loc[index, "close"] - .2
    one.loc[index[-1], ["open", "high", "low", "close"]] = [
        one_ma20 + 2, one_ma20 + 2.2, one_ma20 - 1.2, one_ma20 - 1]
    return five, one, fifteen


def test_ma5_early_continuation_short_and_long_are_mirrored():
    short, short_reason, short_stop = downtrend_ma5_pullback_short_setup(
        *ma5_early_continuation_market())
    assert short, short_reason
    assert "MA5提前追空" in short_reason and short_stop > 0
    mirrored = []
    for frame in ma5_early_continuation_market():
        result = frame.copy()
        result["open"] = 250 - frame["open"]
        result["high"] = 250 - frame["low"]
        result["low"] = 250 - frame["high"]
        result["close"] = 250 - frame["close"]
        mirrored.append(result)
    long, long_reason, long_stop = uptrend_ma5_pullback_long_setup(*mirrored)
    assert long, long_reason
    assert "MA5提前追多" in long_reason and long_stop > 0


def test_ma5_early_short_rejects_first_dip_after_a_v_rebound_above_ma20():
    five, one, fifteen = ma5_early_continuation_market()
    # The old branch would treat this large V rebound's first red MA5 cross
    # as a continuation short.  Price is still above MA10 and only MA5 has
    # bent, so v0.7.28 must wait for a genuine short-term rollover.
    base_index = one.index[-20:-10]
    one.loc[base_index, "close"] = 100
    one.loc[base_index, "open"] = 100.2
    one.loc[base_index, "high"] = 100.4
    one.loc[base_index, "low"] = 99.6
    values = [100, 100, 100, 100, 120, 118, 117, 116, 115, 114]
    index = one.index[-10:]
    one.loc[index, "close"] = values
    one.loc[index, "open"] = [value + .2 for value in values]
    one.loc[index, "high"] = [value + .4 for value in values]
    one.loc[index, "low"] = [value - .4 for value in values]
    one.loc[one.index[-1], ["open", "high", "low", "close"]] = [120, 120.2, 113.8, 114]

    active, reason, _ = downtrend_ma5_pullback_short_setup(five, one, fifteen)

    assert not active
    assert "MA20加速上行" in reason
    assert "禁止按普通反抽高点提前追空" in reason


def test_aggressive_double_rejection_short_triggers_on_second_red_ma5_bend():
    five = candles([120 - i * .30 for i in range(40)], "5min")
    five["high"] = five["close"] + 2.0
    five["low"] = five["close"] - 2.0
    fifteen = candles([130 - i * .40 for i in range(40)], "15min")
    one = candles([112 - i * .08 for i in range(32)] + [109.6] * 8, "1min")
    one["volume"] = 100.0
    # Pullback above 1m MA20, then two green/red rejection pairs. The second
    # red is the newest closed candle and bends MA5 down without a higher high.
    one.loc[34, ["open", "high", "low", "close"]] = [109.5, 110.2, 109.4, 110.0]
    one.loc[35, ["open", "high", "low", "close"]] = [110.0, 110.1, 109.6, 109.7]
    one.loc[36, ["open", "high", "low", "close"]] = [109.7, 110.0, 109.6, 109.9]
    one.loc[37, ["open", "high", "low", "close"]] = [109.9, 109.95, 109.55, 109.65]
    one.loc[38, ["open", "high", "low", "close"]] = [109.65, 109.9, 109.6, 109.8]
    one.loc[39, ["open", "high", "low", "close"]] = [109.8, 109.85, 109.2, 109.3]

    active, reason, stop = aggressive_double_rejection_ma5_short_setup(five, one, fifteen)

    assert active, reason
    assert "双局部高点追空" in reason
    assert stop > 110.0
