import numpy as np
import pandas as pd

from quantbot.account05_signals import (
    classify_fifteen_minute_trend, evaluate_account05_signals,
    _extreme_rotation, _one_minute_reversal, _trend_chase, _supertrend_chop,
    _order_block_zone, _supertrend_support_early_long,
    _ma20_order_block_pullback_long, _five_minute_top_rejection_early_short,
    _cover_45, _five_minute_first_supertrend_short, _supertrend_state)
from quantbot.account05_signals import (
    _late_directional_entry_allowed, _one_minute_supertrend_support_bias)
from quantbot.account05_strategy import Trend15m


def _market(closes):
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="15min"),
        "open": close - .2, "high": close + 1, "low": close - 1,
        "close": close, "volume": 100,
    })


def test_fifteen_minute_trend_requires_ma_slope_and_price_structure():
    trend, reason = classify_fifteen_minute_trend(_market(np.linspace(100, 160, 60)))
    assert trend is Trend15m.UP
    assert "高低点抬高" in reason
    trend, reason = classify_fifteen_minute_trend(_market(np.linspace(160, 100, 60)))
    assert trend is Trend15m.DOWN
    assert "高低点降低" in reason


def test_flat_fifteen_minute_market_is_unclear():
    trend, _ = classify_fifteen_minute_trend(_market(np.full(60, 120.0)))
    assert trend is Trend15m.UNCLEAR


def test_late_short_chase_is_blocked_without_a_new_rebound():
    falling = _market([120 - i for i in range(30)])
    assert not _late_directional_entry_allowed(falling, -1)


def test_fresh_rebound_short_is_not_blocked_by_ma_order():
    values = [120 - i for i in range(26)] + [94, 96, 95, 93]
    frame = _market(values)
    frame.loc[27, "high"] = 99.0
    assert _late_directional_entry_allowed(frame, -1)


def test_base_regime_uses_five_minute_trend_not_fifteen_minute_trend():
    five_up = _market(np.linspace(100, 160, 60))
    fifteen_down = _market(np.linspace(160, 100, 60))
    signals = evaluate_account05_signals(
        _market(np.linspace(100, 120, 60)), five_up, fifteen_down,
        _market(np.full(60, 120.0)))
    assert signals.trend_5m is Trend15m.UP
    assert signals.trend_15m is Trend15m.UP  # compatibility alias
    assert "高低点抬高" in signals.trend_reason


def test_three_vs_twelve_uses_bodies_and_accepts_near_lower_second_top():
    values = [98.0] * 10 + [98.4, 98.8, 99.2, 99.6, 100.0, 99.7, 99.4,
              99.2, 99.1, 99.0, 99.4, 99.85, 99.95, 98.8]
    frame = _market(values)
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame.loc[13, "high"] = 110.0
    frame.loc[22, "high"] = 111.0
    ok, _terminal, reason = _one_minute_reversal(frame, -1)
    assert ok
    assert "不计上下影线" in reason


def test_uptrend_chase_rejects_falling_five_minute_candle_and_running_flash():
    one = _market(np.linspace(100, 110, 30))
    one.loc[27, ["open", "close"]] = [109.0, 108.2]
    one.loc[28, ["open", "close"]] = [108.2, 107.8]
    # Latest row is still running and briefly green; it must be ignored.
    one.loc[29, ["open", "close"]] = [107.8, 108.4]
    five = _market(np.linspace(100, 112, 30))
    five.loc[29, ["open", "close"]] = [112.0, 109.0]
    ok, _ = _trend_chase(one, five, 1, True)
    assert not ok


def test_uptrend_chase_accepts_deep_end_dual_timeframe_recovery(monkeypatch):
    monkeypatch.setattr("quantbot.account05_signals._cover_45", lambda *_args, **_kwargs: (True, .60))
    one = _market([100] * 22 + [98.8, 98.2, 98.0, 98.4, 99.2, 100.4, 101.2, 102.0])
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="min")
    one.loc[28, ["open", "close", "high", "low"]] = [99.0, 100.0, 100.4, 98.6]
    one.loc[29, ["open", "close", "high", "low"]] = [100.0, 101.5, 101.8, 99.8]
    five = _market([100] * 22 + [98.0, 97.0, 99.0, 102.0])
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    five.loc[27, ["open", "close", "high", "low"]] = [98.0, 97.0, 98.2, 96.8]
    five.loc[28, ["open", "close", "high", "low"]] = [97.0, 100.0, 100.4, 96.8]
    five.loc[29, ["open", "close", "high", "low"]] = [100.0, 102.0, 102.2, 99.8]
    ok, reason = _trend_chase(one, five, 1, True)
    assert ok and "深位反转追多" in reason


def test_trend_chase_accepts_supertrend_support_pullback(monkeypatch):
    one = _market([100] * 24 + [101, 102, 101.5, 102.2, 102.5, 102.8])
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="min")
    five = _market([100] * 24 + [101, 102, 103, 104, 105, 106])
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 102.0, 1, 101.5))
    ok, reason = _trend_chase(one, five, 1, True)
    assert ok and "超级趋势线支撑" in reason


def test_early_supertrend_long_does_not_wait_for_ma5_or_five_minute_cover(monkeypatch):
    one = _market([103.0] * 38 + [102.0, 100.2])
    five = _market([103.0] * 40)
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="min")
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    one.loc[39, ["open", "high", "low", "close"]] = [100.1, 100.4, 99.9, 100.2]
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 100.0, 1, 100.0))
    monkeypatch.setattr("quantbot.account05_signals._cover_45",
                        lambda *_args, **_kwargs: (False, 0.0))
    assert one.close.iloc[-1] < one.close.iloc[-6:-1].mean()
    signals = evaluate_account05_signals(one, five, five)
    assert signals.triggers[0].identity == "超级趋势支撑早触发追多"
    assert signals.triggers[0].anchor_time == str(one.date.iloc[-1])


def test_early_supertrend_long_requires_live_reclaim_but_not_five_minute_direction(monkeypatch):
    one = _market([103.0] * 39 + [99.8])
    five = _market([103.0] * 40)
    one.loc[39, ["open", "high", "low", "close"]] = [100.1, 100.4, 99.7, 99.8]
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 100.0, 1, 100.0))
    assert not _supertrend_support_early_long(one, five)[0]
    one.loc[39, "close"] = 100.2
    assert _supertrend_support_early_long(one, five)[0]


def test_early_supertrend_long_can_rebound_near_support_without_touch(monkeypatch):
    one = _market([103.0] * 39 + [100.2])
    five = _market([103.0] * 40)
    one.loc[39, ["open", "high", "low", "close"]] = [100.02, 100.23, 99.85, 100.2]
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 100.0, 1, 100.0))
    assert _supertrend_support_early_long(one, five)[0]
    one.loc[39, ["open", "high", "low", "close"]] = [108.7, 108.9, 108.6, 108.8]
    assert not _supertrend_support_early_long(one, five)[0]


def test_early_supertrend_long_accepts_red_candle_recovery_without_five_minute_confirmation(monkeypatch):
    one = _market([103.0] * 39 + [100.4])
    one.loc[39, ["open", "high", "low", "close"]] = [100.6, 100.7, 99.9, 100.4]
    five = _market([103.0] * 38 + [99.0, 100.0])
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 100.0, 1, 100.0))
    assert _supertrend_support_early_long(one, five)[0]
    one.loc[39, ["low", "close"]] = [96.0, 96.2]
    assert not _supertrend_support_early_long(one, five)[0]


def test_supertrend_support_long_rejects_late_entry_far_above_five_minute_support(monkeypatch):
    one = _market([108.0] * 38 + [109.5, 109.8])
    one.loc[39, ["open", "high", "low", "close"]] = [109.7, 109.9, 109.7, 109.8]
    five = _market([100.0] * 40)

    def state(frame, *_args, **_kwargs):
        return (1, 109.5, 1, 109.0) if float(frame.close.iloc[-1]) > 105 else (1, 100.0, 1, 99.0)

    monkeypatch.setattr("quantbot.account05_signals._supertrend_state", state)
    ok, reason = _supertrend_support_early_long(one, five)
    assert not ok
    assert "尚未靠近超级趋势支撑" in reason


def test_first_bottom_supertrend_flip_can_seed_long_before_five_minute_turn(monkeypatch):
    one = _market([103.0] * 38 + [99.8, 100.2])
    one.loc[39, ["open", "high", "low", "close"]] = [100.1, 100.4, 99.9, 100.2]
    five = _market([110.0] * 40)

    def state(frame, *_args, **_kwargs):
        return (1, 100.0, -1, 101.0) if float(frame.close.iloc[-1]) < 105 else (1, 110.0, 1, 109.0)

    monkeypatch.setattr("quantbot.account05_signals._supertrend_state", state)
    ok, reason = _supertrend_support_early_long(one, five)
    assert ok and "首次翻多" in reason


def test_supertrend_support_retest_rejects_0426_late_price(monkeypatch):
    one = _market([2682.0] * 39 + [2679.3])
    five = _market([2682.0] * 40)
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 2678.12, 1, 2678.12))
    # The marked lower end can rebound before the candle turns green.
    one.loc[39, ["open", "high", "low", "close"]] = [2678.2, 2678.5, 2677.9, 2678.3]
    assert _supertrend_support_early_long(one, five)[0]
    one.loc[39, ["open", "high", "low", "close"]] = [2678.2, 2678.5, 2677.9, 2678.3]
    assert _supertrend_support_early_long(one, five)[0]
    # The actual 04:26 fill had already risen well away from the support.
    one.loc[39, ["open", "high", "low", "close"]] = [2680.5, 2681.1, 2679.2, 2680.94]
    assert not _supertrend_support_early_long(one, five)[0]


def test_ma20_order_block_pullback_can_trigger_before_ma5_and_cover(monkeypatch):
    one = _market([105.0] * 35 + [102.0] * 4 + [101.6])
    five = _market([100.0] * 30 + [103.0] * 8 + [102.0, 101.8])
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="min")
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    five.loc[39, ["open", "high", "low", "close"]] = [101.5, 102.0, 101.2, 101.8]
    one.loc[39, ["open", "high", "low", "close"]] = [101.3, 101.8, 101.1, 101.6]
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 99.0, 1, 99.0))
    monkeypatch.setattr("quantbot.account05_signals._order_block_zone",
                        lambda *_args, **_kwargs: (101.0, 102.0))
    monkeypatch.setattr("quantbot.account05_signals._cover_45",
                        lambda *_args, **_kwargs: (False, 0.0))
    assert one.close.iloc[-1] < one.close.iloc[-6:-1].mean()
    ok, reason = _ma20_order_block_pullback_long(one, five)
    assert ok and "不等待MA5上穿" in reason
    assert any(x.identity == "5分钟MA20订单块回踩早触发追多"
               for x in evaluate_account05_signals(one, five, five).triggers)
    one.loc[39, ["open", "close"]] = [101.7, 101.5]
    assert not _ma20_order_block_pullback_long(one, five)[0]


def test_live_five_minute_top_rejection_skips_doji_and_enters_before_cover(monkeypatch):
    one = _market([100.0] * 34 + [103, 105, 107, 109, 109, 107])
    five = _market([100.0] * 34 + [103, 105, 107, 109, 109, 107])
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="min")
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    five.loc[38, ["open", "close"]] = [109.0, 109.0]  # intermediate doji
    five.loc[39, ["open", "high", "low", "close"]] = [109.0, 110.0, 106.0, 107.0]
    one.loc[39, ["open", "high", "low", "close"]] = [109.0, 110.0, 106.0, 107.0]
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (-1, 110.0, -1, 110.0))
    assert not _cover_45(five, -1)[0]
    ok, reason = _five_minute_top_rejection_early_short(one, five)
    assert ok and "十字线" in reason
    assert not any(x.identity == "5分钟高位拒绝早触发追空"
                   for x in evaluate_account05_signals(one, five, five).triggers)
    one.loc[39, "close"] = 109.5
    assert not _five_minute_top_rejection_early_short(one, five)[0]


def test_live_five_minute_top_rejection_catches_first_small_retrace():
    one = _market([100.0] * 34 + [103, 105, 107, 109, 109, 109.0])
    five = _market([100.0] * 34 + [103, 105, 107, 109, 109, 109.0])
    five.loc[39, ["open", "high", "low", "close"]] = [109.5, 110.0, 109.0, 109.4]
    one.loc[39, ["open", "high", "low", "close"]] = [109.5, 110.0, 109.0, 109.0]
    assert _five_minute_top_rejection_early_short(one, five)[0]
    one.loc[39, ["open", "high", "low", "close"]] = [103.0, 110.0, 102.0, 102.5]
    assert not _five_minute_top_rejection_early_short(one, five)[0]


def test_first_five_minute_supertrend_flip_short_is_independent_of_cover(monkeypatch):
    one = _market([100.0] * 40)
    five = _market([100.0] * 40)
    one.loc[39, ["open", "high", "low", "close"]] = [100.5, 100.6, 99.9, 100.0]
    five.loc[39, ["open", "high", "low", "close"]] = [101.0, 101.1, 99.9, 100.0]
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **kwargs: ((-1, 105.0, 1, 100.3)
                                                if kwargs.get("include_running")
                                                else (1, 100.3, 1, 99.0)))
    monkeypatch.setattr("quantbot.account05_signals._supertrend_chop",
                        lambda *_args, **_kwargs: False)
    monkeypatch.setattr("quantbot.account05_signals._one_minute_supertrend_support_bias",
                        lambda *_args: False)
    assert not _cover_45(five, -1)[0]
    ok, reason = _five_minute_first_supertrend_short(one, five)
    assert ok and "首次翻空" in reason
    assert not any(item.identity == "5分钟超级趋势首次翻空追空"
                   for item in evaluate_account05_signals(one, five, five).triggers)
    one.loc[39, "close"] = 95.0
    assert not _five_minute_first_supertrend_short(one, five)[0]


def test_first_five_minute_supertrend_flip_requires_new_switch(monkeypatch):
    one = _market([100.0] * 40)
    five = _market([100.0] * 40)
    one.loc[39, ["open", "close"]] = [100.5, 100.0]
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (-1, 100.5, -1, 101.0))
    assert not _five_minute_first_supertrend_short(one, five)[0]


def test_running_supertrend_state_reports_prior_support_on_real_flip():
    five = _market(np.linspace(100, 120, 40))
    five.loc[39, ["open", "high", "low", "close"]] = [120.0, 120.2, 112.0, 112.5]
    direction, line, prior_direction, prior_support = _supertrend_state(
        five, include_running=True)
    assert prior_direction == 1 and direction == -1
    assert line > prior_support > float(five.close.iloc[-1])


def test_order_block_zone_is_available_for_directional_confirmation():
    frame = _market([100] * 25 + [101, 102, 101.5, 103])
    frame.loc[26, "open"] = 103.0
    frame.loc[27, "open"] = 100.0
    zone = _order_block_zone(frame, 1)
    assert zone is not None and zone[0] != zone[1]


def test_supertrend_chop_waiting_zone_blocks_new_chase(monkeypatch):
    frame = _market([100, 101, 99, 101, 99, 101, 99, 101] * 5)
    assert isinstance(_supertrend_chop(frame), bool)


def test_bottom_support_band_pauses_shorts_but_keeps_longs(monkeypatch):
    one = _market(np.linspace(100, 101, 40))
    five = _market(np.linspace(100, 102, 40))
    fifteen = _market(np.linspace(100, 102, 40))
    one["date"] = pd.date_range("2026-01-01", periods=len(one), freq="min")
    five["date"] = pd.date_range("2026-01-01", periods=len(five), freq="5min")
    fifteen["date"] = pd.date_range("2026-01-01", periods=len(fifteen), freq="15min")
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 100.0, 1, 99.0))
    assert _one_minute_supertrend_support_bias(one, five)
    monkeypatch.setattr("quantbot.account05_signals._supertrend_support_early_long",
                        lambda *_args: (True, "支撑回踩多"))
    monkeypatch.setattr("quantbot.account05_signals._ma20_order_block_pullback_long",
                        lambda *_args: (False, ""))
    for name in (
        "_one_minute_first_supertrend_short",
        "_one_minute_supertrend_resistance_retest_short",
        "_five_minute_top_rejection_early_short",
        "_five_minute_first_supertrend_short",
    ):
        monkeypatch.setattr(f"quantbot.account05_signals.{name}",
                            lambda *_args: (True, "错误方向候选"))
    monkeypatch.setattr("quantbot.account05_signals._one_minute_reversal",
                        lambda *_args: (True, False, "反转候选"))
    monkeypatch.setattr("quantbot.account05_signals._cover_45",
                        lambda *_args: (True, .6))
    monkeypatch.setattr("quantbot.account05_signals._late_directional_entry_allowed",
                        lambda *_args: True)
    monkeypatch.setattr("quantbot.account05_signals._trend_chase",
                        lambda *_args: (True, "趋势候选"))
    monkeypatch.setattr("quantbot.account05_signals._extreme_rotation",
                        lambda *_args: type("Extreme", (), {"direction": -1})())

    signals = evaluate_account05_signals(one, five, fifteen)

    assert signals.triggers
    assert any(item.direction > 0 for item in signals.triggers)
    assert any(item.direction < 0 for item in signals.triggers)
    assert any("追多" in item.identity or "底部" in item.identity
               for item in signals.triggers)
    assert signals.extreme_rotation is not None
    assert signals.extreme_rotation.direction == -1


def test_bullish_supertrend_does_not_suppress_shorts_far_above_bottom_support(monkeypatch):
    one = _market(np.linspace(100, 110, 40))
    five = _market(np.full(40, 100.0))
    one["high"] = one["close"] + 1
    one["low"] = one["close"] - 1
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (1, 100.0, 1, 99.0))
    assert not _one_minute_supertrend_support_bias(one, five)


def test_bearish_supertrend_side_does_not_activate_bottom_long_bias(monkeypatch):
    frame = _market(np.linspace(100, 90, 40))
    five = _market(np.linspace(100, 90, 40))
    monkeypatch.setattr("quantbot.account05_signals._supertrend_state",
                        lambda *_args, **_kwargs: (-1, 101.0, -1, 101.0))
    assert not _one_minute_supertrend_support_bias(frame, five)


def test_extreme_rotation_accepts_running_one_minute_terminal_flash(monkeypatch):
    monkeypatch.setattr(
        "quantbot.account05_signals._large_extreme",
        lambda *_args, **_kwargs: (True, "发散末端且远离MA20"))
    one = _market(np.linspace(100, 120, 30))
    one.loc[20:27, ["open", "close"]] = [119.8, 120.0]
    one.loc[28, ["open", "close"]] = [120.0, 117.0]
    one.loc[29, ["open", "close"]] = [117.0, 121.0]  # running terminal flash
    trigger = _extreme_rotation(one, _market(np.linspace(100, 120, 40)),
                                _market(np.linspace(100, 120, 40)))
    assert trigger is not None
    assert trigger.direction == -1
    assert "不等待收盘" in trigger.reason


def test_extreme_rotation_uses_running_one_and_five_without_fifteen_gate():
    one = _market(np.linspace(100, 120, 39).tolist() + [130.0])
    five = _market(np.linspace(100, 120, 39).tolist() + [130.0])
    fifteen = _market(np.linspace(130, 100, 40))
    trigger = _extreme_rotation(one, five, fifteen)
    assert trigger is not None and trigger.direction == -1
    assert "5分钟运行中" in trigger.reason
    assert "15分钟" not in trigger.reason
    assert trigger.structure_key == str(five.date.iloc[-1])


def test_extreme_rotation_rejects_one_minute_flash_without_five_minute_extreme():
    one = _market(np.linspace(100, 120, 39).tolist() + [130.0])
    five = _market([100.0] * 40)
    assert _extreme_rotation(one, five, five) is None
