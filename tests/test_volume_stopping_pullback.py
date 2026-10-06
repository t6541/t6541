import pandas as pd

from quantbot.extreme_entries import (
    volume_stopping_pullback_long_setup,
    waterfall_exhaustion_rebound_long_setup,
)


def _frame(closes, frequency, spread=.25, volume=100.0):
    return pd.DataFrame({
        "date": pd.date_range("2026-08-17", periods=len(closes), freq=frequency),
        "open": closes,
        "high": [value + spread for value in closes],
        "low": [value - spread for value in closes],
        "close": closes,
        "volume": [volume] * len(closes),
    })


def _markets(recovery_volume=180.0):
    five = _frame([100 + index * .25 for index in range(36)], "5min")
    five.loc[32, ["open", "high", "low", "close", "volume"]] = [108.2, 108.3, 104.6, 105.0, 220.0]
    five.loc[33, ["open", "high", "low", "close"]] = [105.0, 105.3, 104.7, 105.1]
    five.loc[34, ["open", "high", "low", "close"]] = [105.1, 105.4, 104.8, 105.2]
    five.loc[35, ["open", "high", "low", "close"]] = [105.2, 105.5, 104.9, 105.3]
    one = _frame([104.80 + index * .002 for index in range(25)], "1min", spread=.03)
    one.loc[20, ["open", "high", "low", "close", "volume"]] = [104.84, 104.88, 104.70, 104.76, 180.0]
    one.loc[21, ["open", "high", "low", "close", "volume"]] = [104.76, 104.82, 104.72, 104.78, 100.0]
    one.loc[22, ["open", "high", "low", "close", "volume"]] = [104.78, 104.84, 104.75, 104.80, 100.0]
    one.loc[23, ["open", "high", "low", "close", "volume"]] = [104.80, 104.86, 104.77, 104.82, 100.0]
    one.loc[24, ["open", "high", "low", "close", "volume"]] = [104.82, 105.08, 104.80, 105.05, recovery_volume]
    return five, one


def test_dual_timeframe_volume_stopping_pullback_triggers_with_frozen_ma20_target():
    five, one = _markets()
    triggered, reason, stop, target = volume_stopping_pullback_long_setup(five, one)
    assert triggered
    assert "5分钟放量止跌" in reason
    assert stop < 104.70
    assert target > 105.05
    assert target - 105.05 >= (105.05 - stop) * 1.5


def test_one_minute_recovery_without_volume_does_not_trigger():
    five, one = _markets(recovery_volume=80.0)
    triggered, reason, stop, target = volume_stopping_pullback_long_setup(five, one)
    assert not triggered
    assert "1分钟放量阳线" in reason
    assert stop == target == 0.0


def test_waterfall_exhaustion_can_trigger_small_v_rebound_without_prior_uptrend():
    five = _frame([112 - index * .12 for index in range(36)], "5min", spread=.45)
    five.loc[32, ["open", "high", "low", "close", "volume"]] = [108.0, 108.2, 101.8, 102.4, 260.0]
    five.loc[33, ["open", "high", "low", "close", "volume"]] = [102.4, 103.0, 102.0, 102.7, 120.0]
    five.loc[34, ["open", "high", "low", "close", "volume"]] = [102.7, 103.2, 102.1, 102.9, 110.0]
    five.loc[35, ["open", "high", "low", "close", "volume"]] = [102.9, 103.4, 102.2, 103.1, 100.0]
    one = _frame([102.5 + index * .005 for index in range(25)], "1min", spread=.04)
    one.loc[20, ["open", "high", "low", "close", "volume"]] = [102.60, 102.65, 101.90, 102.05, 220.0]
    one.loc[21, ["open", "high", "low", "close", "volume"]] = [102.05, 102.25, 101.95, 102.15, 100.0]
    one.loc[22, ["open", "high", "low", "close", "volume"]] = [102.15, 102.35, 102.05, 102.25, 100.0]
    one.loc[23, ["open", "high", "low", "close", "volume"]] = [102.25, 102.40, 102.15, 102.30, 100.0]
    one.loc[24, ["open", "high", "low", "close", "volume"]] = [102.30, 102.85, 102.25, 102.80, 180.0]
    triggered, reason, stop, target = waterfall_exhaustion_rebound_long_setup(five, one)
    assert triggered, reason
    assert stop < 101.90
    assert target > 102.80
    assert target - 102.80 >= (102.80 - stop) * 1.5
