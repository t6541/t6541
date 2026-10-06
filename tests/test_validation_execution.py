import json

import pandas as pd

import quantbot.validation_execution as validation_execution
from quantbot.validation_execution import (bottom_anchor_below_bearish_ma_stack,
                                            high_half_cover_bypasses_ma5_chase,
                                            high_reversal_three_timeframe_ma_exhaustion,
                                            low_reversal_two_timeframe_ma_exhaustion,
                                            sideways_top_short_bypasses_ma5_chase)

from quantbot.intraday import TimeframeSignal
from quantbot.okx import OkxDemoClient
from quantbot.validation_execution import aggressive_long_ma5_exit_reason, aggressive_short_ma5_exit_reason, breakout_long_protection, breakout_pullback_long_setup, confirm_unprotected_positions, continuation_pullback_reached_ma5_ma10, continuation_stage_location_allowed, dual_timeframe_ma20_cross_continuation_setup, emergency_close_unprotected_quantity, five_minute_ma20_takeover_confirmed, five_minute_ma5_trend_holds, five_minute_price_reversal_confirms, fresh_endpoint_suspends_opposite_trend, frozen_launch_direction_allowed, hierarchical_entry_direction, indicator_confirmation_gate, latched_stage_two_resume_candidate, local_extreme_cross_location_allows, long_pressure_runway, ma20_pullback_long_setup, ma20_pullback_short_setup, ma5_anchor_allows_exit, ma5_local_extreme_anchor, ma5_ma10_cross_observation, matching_current_tick_reversal_anchors, matching_frozen_launch_candidates, merge_closed_and_live_market, multi_timeframe_weakness_continuation_short_setup, one_minute_big_cross_launch, one_minute_launch_freeze_observations, one_minute_launch_quality_gate, one_minute_local_extreme_ma5_ma10_cross_setup, one_minute_ma5_ma20_early_launch, original_strategy_reversal_entry_allowed, reversal_range_location_gate, reversal_runway_required, reversal_stop_outside_recent_structure, safe_strategy01_contracts_for_stop, staged_launch_is_fresh, staged_launch_risk_allows, stage_three_micro_structure_stop, strategy01_contracts_for_stop, strategy01_dynamic_stop, strategy01_local_stop, three_point_launch_stop, top_anchor_above_bullish_ma_stack, validation_exit_prices, waterfall_micro_pullback_stop


def test_fresh_bottom_suspends_old_short_trend_before_ma20_takeover():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-17T08:30:00Z", periods=5, freq="min"),
        "open": [100, 99, 98, 99, 100], "high": [101, 100, 100, 101, 102],
        "low": [97, 98, 98.5, 99, 99.5], "close": [99, 98.5, 99.5, 100, 101],
    })
    endpoint = {"direction": 1, "confirmed_bar_time": "2026-09-17T08:30:00Z",
                "extreme_price": 97.0}
    direction, reason = fresh_endpoint_suspends_opposite_trend(
        endpoint=endpoint, old_bias=-1, now="2026-09-17T08:34:00Z",
        one_minute=frame)
    assert direction == 1
    assert "暂停旧方向趋势追单" in reason


def test_upgraded_five_minute_top_keeps_direct_short_entry_ownership():
    owns = validation_execution.five_minute_top_cover_owns_direct_entry
    assert owns(-1, {"five_minute_half_cover": True})
    assert not owns(-1, {"five_minute_half_cover": False})
    assert not owns(1, {"five_minute_half_cover": True})


def test_bottom_reversal_first_entry_requires_both_timeframes_below_ma20():
    dates_1m = pd.date_range("2026-09-17T14:30:00Z", periods=25, freq="min")
    one = pd.DataFrame({
        "date": dates_1m, "open": [100.0] * 25, "high": [101.0] * 25,
        "low": [98.0] * 25, "close": [100.0] * 24 + [95.0],
    })
    dates_5m = pd.date_range("2026-09-17T12:50:00Z", periods=25, freq="5min")
    five_above = pd.DataFrame({
        "date": dates_5m, "open": [100.0] * 25, "high": [102.0] * 25,
        "low": [98.0] * 25, "close": [100.0] * 24 + [105.0],
    })
    allowed, reason = validation_execution.bottom_reversal_below_both_ma20(
        one, five_above, dates_1m[-1])
    assert not allowed
    assert "5m收盘105.00未低于MA20" in reason
    five_below = five_above.copy()
    five_below.loc[five_below.index[-1], "close"] = 95.0
    allowed, reason = validation_execution.bottom_reversal_below_both_ma20(
        one, five_below, dates_1m[-1])
    assert allowed
    assert "双周期MA20位置通过" in reason


def test_dual_ma20_bottom_gate_does_not_reject_independent_uptrend_pullback():
    required = validation_execution.bottom_reversal_ma20_gate_required
    assert required({"direction": 1, "market_shape_code": "true_bottom_reversal"})
    assert required({"direction": 1, "market_shape_code": "local_bottom_reversal"})
    assert not required({"direction": 1, "market_shape_code": "uptrend_continuation_long"})
    assert not required({"direction": -1, "market_shape_code": "true_top_reversal"})


def test_dual_ma20_bottom_gate_only_applies_before_stage_two_launch():
    required = validation_execution.bottom_reversal_ma20_gate_required
    for stage in ("price_reclaim_ma5", "price_above_flat_rising_ma5",
                  "latched_stage2_resume", "small_golden_cross"):
        assert not required({"direction": 1,
                             "market_shape_code": "local_bottom_reversal",
                             "stage": stage})


def test_closed_five_minute_uptrend_releases_old_short_only_after_confirmed_higher_low():
    dates = pd.date_range("2026-09-18T00:00:00Z", periods=32, freq="5min")
    close = [100 - .25 * i for i in range(21)] + [95.0, 96.0, 97.0, 98.0,
             99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    frame = pd.DataFrame({"date": dates, "open": close,
                          "high": [value + .4 for value in close],
                          "low": [value - .4 for value in close], "close": close})
    confirmed = validation_execution.confirmed_five_minute_uptrend_pullback_context
    assert confirmed(frame)
    assert not confirmed(frame, dates[-1])  # a newer opposite endpoint still owns the decision
    early = frame.copy()
    early.loc[early.index[-1], "close"] = 94.0
    assert not confirmed(early)


def test_uptrend_structure_survives_a_temporary_five_minute_ma5_dip():
    dates = pd.date_range("2026-09-18T00:00:00Z", periods=40, freq="5min")
    close = [100 + .3 * i for i in range(37)] + [111.4, 111.2, 109.9]
    frame = pd.DataFrame({"date": dates, "open": close,
                          "high": [value + .2 for value in close],
                          "low": [value - .2 for value in close], "close": close})
    ma5 = frame.close.rolling(5).mean()
    assert ma5.iloc[-1] < ma5.iloc[-2]
    confirmed = validation_execution.confirmed_five_minute_uptrend_pullback_context
    assert confirmed(frame)
    broken = frame.copy()
    broken.loc[broken.index[-1], "low"] = 100.0
    assert not confirmed(broken)


def test_confirmed_uptrend_votes_allow_221_but_not_210():
    allowed = validation_execution.continuation_indicator_votes_allowed
    assert not allowed({"1m": 2, "5m": 2, "15m": 1})
    assert allowed({"1m": 2, "5m": 2, "15m": 1}, confirmed_uptrend_pullback=True)
    assert not allowed({"1m": 2, "5m": 1, "15m": 0}, confirmed_uptrend_pullback=True)


def test_fresh_uptrend_reclaim_requires_nearby_ma_edge_and_no_chase():
    dates = pd.date_range("2026-09-18T00:00:00Z", periods=30, freq="min")
    close = [100 + .1 * i for i in range(27)] + [102.5, 102.7, 102.75]
    frame = pd.DataFrame({"date": dates, "open": close, "close": close,
                          "high": [value + .3 for value in close],
                          "low": [value - .3 for value in close]})
    frame.loc[frame.index[-2], "low"] = 101.9
    assert validation_execution.fresh_uptrend_ma_edge_reclaim(frame)[0]
    falling = frame.copy()
    falling.loc[falling.index[-1], "close"] = 102.5
    assert not validation_execution.fresh_uptrend_ma_edge_reclaim(falling)[0]
    chased = frame.copy()
    chased.loc[chased.index[-1], "close"] = 104.0
    assert not validation_execution.fresh_uptrend_ma_edge_reclaim(chased)[0]
    no_touch = frame.copy()
    no_touch["low"] = no_touch["close"] + .3
    assert not validation_execution.fresh_uptrend_ma_edge_reclaim(no_touch)[0]


def test_broken_fresh_bottom_does_not_suspend_old_short_trend():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-17T08:30:00Z", periods=3, freq="min"),
        "open": [100, 99, 98], "high": [101, 100, 99],
        "low": [97, 96.5, 97.5], "close": [99, 98, 98.5],
    })
    endpoint = {"direction": 1, "confirmed_bar_time": "2026-09-17T08:30:00Z",
                "extreme_price": 97.0}
    direction, _ = fresh_endpoint_suspends_opposite_trend(
        endpoint=endpoint, old_bias=-1, now="2026-09-17T08:32:00Z",
        one_minute=frame)
    assert direction == 0
from quantbot.state import StateStore


def test_three_stage_reversal_does_not_require_old_five_minute_runway():
    assert reversal_runway_required(early_freeze_stage_entry=True) is False
    assert reversal_runway_required(early_freeze_stage_entry=False) is True


def test_three_one_minute_body_stops_ignore_long_wicks_in_both_directions():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-16 07:18", periods=3, freq="min"),
        "open": [100.0, 101.0, 100.5], "close": [101.0, 100.5, 100.0],
        "high": [110.0, 112.0, 111.0], "low": [90.0, 88.0, 89.0],
    })
    short_ok, short_stop, _ = validation_execution.latest_three_one_minute_body_stop(
        frame, 100.0, -1)
    long_ok, long_stop, _ = validation_execution.latest_three_one_minute_body_stop(
        frame, 101.0, 1)
    assert short_ok and 101.0 < short_stop < 110.0
    assert long_ok and 90.0 < long_stop < 100.0


def _reversal_row(direction, branch, status="submitted"):
    return {"direction": direction, "branch": branch, "status": status}


def test_bottom_trial_must_be_accepted_before_it_exits_short_layers():
    rows = [_reversal_row(1, "five_minute_bottom_local_reversal_half_cover_long")]
    assert validation_execution.opposite_reversal_exit_confirmation(-1, rows, None) == (
        "opposite_reversal_trial_order_accepted")
    assert not validation_execution.opposite_reversal_exit_confirmation(1, rows, None)


def test_top_trial_acceptance_is_exact_mirror_for_longs():
    rows = [_reversal_row(-1, "five_minute_top_local_reversal_half_cover_short", "open")]
    assert validation_execution.opposite_reversal_exit_confirmation(1, rows, None) == (
        "opposite_reversal_trial_order_accepted")
    assert not validation_execution.opposite_reversal_exit_confirmation(-1, rows, None)


def test_live_half_cover_without_order_or_confirmed_shape_does_not_exit():
    assert not validation_execution.opposite_reversal_exit_confirmation(-1, [], None)


def test_missed_bottom_order_still_exits_short_after_shape_lock_confirms():
    lock = {"direction": 1, "confirmed_at": "2026-09-09T17:55:00Z"}
    assert validation_execution.opposite_reversal_exit_confirmation(
        -1, [], lock, "2026-09-09T17:10:00Z") == (
            "opposite_reversal_shape_confirmed_after_missed_entry")


def test_missed_bottom_lock_cannot_close_five_second_fast_short():
    lock = {"direction": 1, "confirmed_at": "2026-09-19T11:27:32Z"}
    assert not validation_execution.opposite_reversal_exit_confirmation(
        -1, [], lock, "2026-09-19T11:25:00Z",
        "five_second_top_weakening_short")


def test_accepted_bottom_trial_can_close_five_second_fast_short():
    rows = [_reversal_row(1, "five_minute_bottom_local_reversal_half_cover_long")]
    assert validation_execution.opposite_reversal_exit_confirmation(
        -1, rows, None, "2026-09-19T11:25:00Z",
        "five_second_top_weakening_short") == "opposite_reversal_trial_order_accepted"


def test_stale_shape_lock_before_held_trade_does_not_exit_it():
    lock = {"direction": 1, "confirmed_at": "2026-09-09T16:00:00Z"}
    assert not validation_execution.opposite_reversal_exit_confirmation(
        -1, [], lock, "2026-09-09T17:10:00Z")


def _cover_frame(open_values, close_values, freq="5min"):
    return pd.DataFrame({
        "date": pd.date_range("2026-09-09T17:45:00Z", periods=len(open_values), freq=freq),
        "open": open_values, "close": close_values,
        "high": [max(a, b) + .2 for a, b in zip(open_values, close_values)],
        "low": [min(a, b) - .2 for a, b in zip(open_values, close_values)],
        "volume": [100.0] * len(open_values),
    })


def test_fresh_bearish_half_cover_is_basic_short_confirmation():
    closed = _cover_frame([100.0, 104.0], [104.0, 103.8])
    live = _cover_frame([104.0], [101.9])
    live["date"] = pd.to_datetime(["2026-09-09T17:55:00Z"])
    assert validation_execution.fresh_directional_half_cover(closed, live, -1)[0]
    assert not validation_execution.fresh_directional_half_cover(closed, live, 1)[0]


def test_fresh_bullish_half_cover_is_basic_long_confirmation():
    closed = _cover_frame([104.0, 100.0], [100.0, 100.2])
    live = _cover_frame([100.0], [102.0])
    live["date"] = pd.to_datetime(["2026-09-09T17:55:00Z"])
    assert validation_execution.fresh_directional_half_cover(closed, live, 1)[0]


def test_trend_chase_does_not_require_five_minute_adjacent_cover():
    one = _cover_frame([100.0] * 6, [101.0] * 6, "1min")
    five = _cover_frame([100.0], [104.0])
    five_live = _cover_frame([104.0], [101.9])
    five_live["date"] = pd.to_datetime(["2026-09-09T17:55:00Z"])
    allowed, reason = validation_execution.directional_entry_half_cover_gate(
        one, None, five, five_live, -1, trend_continuation=True)
    assert allowed
    assert "不要求5分钟相邻实体覆盖45%" in reason


def test_top_reversal_cannot_bypass_live_five_minute_adjacent_cover():
    one = _cover_frame([104.0, 103.0], [103.0, 101.0], "1min")
    five = _cover_frame([100.0], [104.0])
    five_live = _cover_frame([104.0], [103.0])
    five_live["date"] = pd.to_datetime(["2026-09-09T17:55:00Z"])
    allowed, reason = validation_execution.directional_entry_half_cover_gate(
        one, None, five, five_live, -1, trend_continuation=False)
    assert not allowed
    assert "mandatory live 5m bearish" in reason


def test_frozen_new_top_cluster_cover_is_not_rejected_by_adjacent_candle_recheck():
    one = _cover_frame([104.0, 103.0], [103.0, 101.0], "1min")
    five = _cover_frame([104.0, 103.0], [102.0, 101.0])
    allowed, reason = validation_execution.directional_entry_half_cover_gate(
        one, None, five, None, -1, trend_continuation=False,
        frozen_cluster_cover={"five_bar_time": "2026-09-18T22:35:00+00:00"},
    )
    assert allowed
    assert "累计45%空头覆盖" in reason
    assert "不必重复" in reason


def test_pullback_long_is_rejected_when_execution_quote_has_run_to_local_top():
    one = _trend_frame([100.0] * 20 + [100.2, 100.4, 100.5, 100.6, 100.7], "1min")
    allowed, reason = validation_execution.trend_continuation_execution_price_ok(
        one, 1, signal_price=100.7, execution_price=102.0)
    assert not allowed
    assert "禁止在局部顶部追多" in reason


def test_pullback_long_executes_while_quote_remains_near_signal_and_ma5():
    one = _trend_frame([100.0] * 20 + [99.8, 99.9, 100.0, 100.1, 100.2], "1min")
    allowed, reason = validation_execution.trend_continuation_execution_price_ok(
        one, 1, signal_price=100.2, execution_price=100.22)
    assert allowed
    assert "仍在回踩窗口" in reason


def test_bottom_reversal_mirrors_mandatory_live_five_minute_cover():
    one = _cover_frame([100.0, 101.0], [101.0, 103.0], "1min")
    five = _cover_frame([104.0], [100.0])
    five_live = _cover_frame([100.0], [101.9])
    five_live["date"] = pd.to_datetime(["2026-09-09T17:55:00Z"])
    allowed, reason = validation_execution.directional_entry_half_cover_gate(
        one, None, five, five_live, 1, trend_continuation=False)
    assert allowed
    assert "mandatory live 5m bullish" in reason


def test_live_five_minute_42pct_cover_remains_below_mandatory_threshold():
    bearish = _cover_frame([100.0, 104.0], [104.0, 102.32])
    bullish = _cover_frame([104.0, 100.0], [100.0, 101.68])
    assert not validation_execution.fresh_single_candle_half_cover(
        bearish, None, -1)[0]
    assert not validation_execution.fresh_single_candle_half_cover(
        bullish, None, 1)[0]


def test_first_live_cover_freezes_compact_stop_and_later_ticks_cannot_widen_it(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    one = _trend_frame([100.0] * 20 + [103.0, 102.0, 101.0], "1min")
    five = _cover_frame([100.0, 104.0], [104.0, 101.9])
    first = validation_execution.freeze_live_five_minute_cover_anchor(
        store, "ETH-USDT-SWAP", one, five, -1)
    assert first is not None
    first_stop = float(first["stop_reference"])
    later = one.copy()
    later.loc[later.index[-1], "high"] = 120.0
    second = validation_execution.freeze_live_five_minute_cover_anchor(
        store, "ETH-USDT-SWAP", later, five, -1)
    assert second is not None
    assert float(second["stop_reference"]) == first_stop
    events = store.connection.execute(
        "SELECT created_at_utc,payload_json FROM events WHERE event_type=?",
        ("five_minute_cover_first_threshold_reached",)).fetchall()
    assert len(events) == 1
    assert json.loads(events[0]["payload_json"])["first_reached_at_utc"] == first["created_at_utc"]
    assert "52%" in json.loads(events[0]["payload_json"])["cover_evidence"]


def test_fresh_top_independent_chain_freezes_three_candle_cover(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-18T09:04:00Z", periods=20, freq="1min"),
        "open": [117.0] * 12 + [118.0, 119.0, 120.0, 119.8, 119.0, 118.2, 117.2, 116.2],
        "high": [117.5] * 12 + [119.2, 120.2, 120.5, 120.1, 119.3, 118.5, 117.5, 116.5],
        "low": [116.5] * 12 + [117.8, 118.8, 119.5, 118.7, 117.8, 116.8, 115.8, 114.8],
        "close": [117.2] * 12 + [119.0, 120.0, 119.8, 119.0, 118.2, 117.2, 116.2, 115.2],
        "volume": [100.0] * 20,
    })
    five = pd.DataFrame({
        "date": pd.date_range("2026-09-18T09:10:00Z", periods=3, freq="5min"),
        "open": [116.0, 120.0, 119.0],
        "high": [120.2, 120.5, 119.2],
        "low": [115.8, 118.8, 115.0],
        "close": [120.0, 119.0, 115.2],
        "volume": [100.0] * 3,
    })
    assert not validation_execution.fresh_single_candle_half_cover(
        five, None, -1)[0]
    frozen, reason = validation_execution.freeze_fresh_top_cluster_cover_anchor(
        store, "ETH-USDT-SWAP", one, five, "2026-09-18T09:18:00Z")
    assert frozen is not None
    assert "up-to-3-candle bearish turn" in reason
    assert frozen["one_anchor_time"] == "2026-09-18T09:18:00+00:00"
    assert float(frozen["stop_reference"]) > 120.5
    event = store.connection.execute(
        "SELECT payload_json FROM events WHERE event_type=?",
        ("fresh_top_independent_cover_frozen",)).fetchone()
    assert event is not None


def test_one_minute_six_candle_window_measures_body_price_overlap():
    closed = _cover_frame(
        [104.0, 102.0, 100.0, 99.2, 99.7],
        [102.0, 100.0, 99.0, 99.7, 100.1], "1min")
    live = _cover_frame([100.1], [101.6], "1min")
    live["date"] = pd.to_datetime(["2026-09-09T17:50:00Z"])
    allowed, reason = validation_execution.fresh_directional_half_cover(
        closed, live, 1, cluster_size=6)
    assert allowed and "up-to-6-candle" in reason and "100%" in reason


def test_cluster_does_not_require_latest_candle_to_cover_previous_alone():
    closed = _cover_frame([104.0, 102.0], [102.0, 102.8])
    live = _cover_frame([102.8], [103.3])
    live["date"] = pd.to_datetime(["2026-09-09T17:55:00Z"])
    assert validation_execution.fresh_directional_half_cover(
        closed, live, 1, cluster_size=3)[0]


def test_recovery_window_rejects_opposite_latest_colour():
    one = _cover_frame([104.0, 102.0, 100.0], [102.0, 103.0, 103.6], "1min")
    one_live = _cover_frame([103.6], [103.5], "1min")
    one_live["date"] = pd.to_datetime(["2026-09-09T17:48:00Z"])
    five = _cover_frame([104.0, 100.0], [100.0, 102.0])
    five_live = _cover_frame([102.0], [101.9])
    five_live["date"] = pd.to_datetime(["2026-09-09T18:00:00Z"])
    assert not validation_execution.fresh_directional_half_cover(
        one, one_live, 1, cluster_size=6)[0]
    assert not validation_execution.fresh_directional_half_cover(
        five, five_live, 1, cluster_size=3)[0]


def test_six_candle_window_cannot_sum_unrelated_bodies_into_282_percent():
    zone = _cover_frame([100, 101, 100, 101, 100, 99],
                        [101, 100, 101, 100, 99, 98], "1min")
    allowed, reason = validation_execution.fresh_directional_half_cover(
        zone, None, -1, cluster_size=6)
    assert allowed
    assert "100%" in reason and "282%" not in reason


def test_1439_original_body_replay_rejects_old_short_at_new_bottom():
    # OKX public raw 1m OHLC, UTC 06:27–06:38 = Beijing 14:27–14:38.
    raw = [(2400.83,2400.01),(2400.00,2400.31),(2400.36,2400.42),
           (2399.94,2399.50),(2399.50,2397.84),(2397.83,2397.91),
           (2398.18,2399.95),(2399.94,2397.60),(2397.59,2396.49),
           (2396.49,2395.65),(2395.65,2393.78),(2393.81,2393.46)]
    frame = _cover_frame([v[0] for v in raw], [v[1] for v in raw], "1min")
    allowed, reason = validation_execution.recovery_has_fresh_entry_location(frame, -1)
    assert not allowed and "lower" not in reason
    assert validation_execution.recovery_has_fresh_entry_location(frame, 1)[0]


def _trend_frame(closes, freq):
    return pd.DataFrame({
        "date": pd.date_range("2026-09-09T16:00:00Z", periods=len(closes), freq=freq),
        "open": [value - .2 for value in closes],
        "high": [value + .4 for value in closes],
        "low": [value - .4 for value in closes],
        "close": closes,
        "volume": [100.0] * len(closes),
    })


def test_three_timeframe_fast_long_does_not_wait_for_cluster_completion():
    one = _trend_frame([100.0] * 16 + [98.0, 97.0, 98.0, 99.0, 100.0], "1min")
    five = _trend_frame([100.0] * 18 + [102.0, 98.0], "5min")
    five.loc[five.index[-1], "open"] = 102.0
    five_live = _cover_frame([98.0], [99.9])
    five_live["date"] = pd.to_datetime([five.iloc[-1]["date"] + pd.Timedelta(minutes=5)])
    fifteen = _trend_frame([102.0, 100.0, 98.0], "15min")
    fifteen["open"] = fifteen["close"] + .5
    fifteen_live = _cover_frame([98.0], [98.2], "15min")
    fifteen_live["date"] = pd.to_datetime([fifteen.iloc[-1]["date"] + pd.Timedelta(minutes=15)])
    direction, reason, stop, mode = validation_execution.three_timeframe_reversal_confirmation(
        one, None, five, five_live, fifteen, fifteen_live)
    assert direction == 1 and mode == "fast"
    assert "without a half-cover requirement" in reason
    assert stop < float(one.iloc[-1]["close"])


def test_five_minute_ma20_cross_confirms_existing_bottom_trend_lock():
    closes = [100.0] * 20 + [105.0]
    frame = _trend_frame(closes, "5min")
    direction, reason, confirmed_at = validation_execution.five_minute_ma20_trend_takeover(frame)
    assert direction == 1
    assert "lock the new trend" in reason
    assert confirmed_at == frame.iloc[-1]["date"]


def _one_minute_lock_frame(closes):
    dates = pd.date_range("2026-09-08T21:20:00Z", periods=len(closes), freq="1min")
    return pd.DataFrame({
        "date": dates, "open": closes,
        "high": [value + .4 for value in closes],
        "low": [value - .4 for value in closes],
        "close": closes, "volume": [100.0] * len(closes),
    })


def test_fresh_bottom_reversal_locks_uptrend_after_ma20_turn_without_retest():
    frame = _one_minute_lock_frame([100.0] * 20 + [96.0, 97.0, 99.0, 102.0, 104.0, 106.0])
    state, reason, confirmed_at = validation_execution.one_minute_reversal_trend_lock_state(
        frame, 1, frame.iloc[20]["date"], 95.5)
    assert state == "confirmed"
    assert "MA20已经同向拐弯" in reason
    assert confirmed_at == frame.iloc[-1]["date"]


def test_failed_fresh_bottom_lock_returns_to_parent_downtrend():
    frame = _one_minute_lock_frame([100.0] * 20 + [96.0, 99.0, 102.0, 104.0, 94.0])
    state, reason, _ = validation_execution.one_minute_reversal_trend_lock_state(
        frame, 1, frame.iloc[20]["date"], 95.5, already_confirmed=True)
    assert state == "failed"
    assert "反转极值" in reason


def test_persisted_45pct_bottom_anchor_recovers_missed_long_and_locks_trend(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    one = _one_minute_lock_frame(
        [100.0] * 20 + [96.0, 97.0, 99.0, 102.0, 104.0, 106.0])
    anchor_time = pd.Timestamp(one.iloc[20]["date"]).isoformat()
    store.freeze_directional_cover_anchor(
        instrument="ETH-USDT-SWAP", direction=1,
        five_bar_time=anchor_time, one_anchor_time=anchor_time,
        stop_reference=95.0)
    fifteen = _cover_frame([102.0, 100.0], [100.0, 101.0], "15min")
    allowed, reason, anchor, lock_state, lock_time, extreme = (
        validation_execution.persisted_directional_cover_recovery(
            store, "ETH-USDT-SWAP", one, fifteen, 1))
    assert not allowed
    assert "location=0" in reason
    assert anchor["five_bar_time"] == anchor_time
    assert lock_state == "confirmed" and lock_time == one.iloc[-1]["date"]
    assert extreme > 95.0


def test_persisted_45pct_top_anchor_is_exact_short_mirror(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    one = _one_minute_lock_frame(
        [100.0] * 20 + [104.0, 103.0, 101.0, 98.0, 96.0, 94.0])
    anchor_time = pd.Timestamp(one.iloc[20]["date"]).isoformat()
    store.freeze_directional_cover_anchor(
        instrument="ETH-USDT-SWAP", direction=-1,
        five_bar_time=anchor_time, one_anchor_time=anchor_time,
        stop_reference=105.0)
    fifteen = _cover_frame([98.0, 100.0], [100.0, 99.0], "15min")
    allowed, reason, anchor, lock_state, _, extreme = (
        validation_execution.persisted_directional_cover_recovery(
            store, "ETH-USDT-SWAP", one, fifteen, -1))
    assert not allowed
    assert "location=0" in reason
    assert anchor["direction"] == -1
    assert lock_state == "confirmed"
    assert extreme < 105.0


def test_persisted_cover_recovery_expires_and_never_ignores_broken_stop(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    one = _one_minute_lock_frame(
        [100.0] * 20 + [96.0, 97.0, 99.0, 102.0, 104.0, 106.0])
    fifteen = _cover_frame([102.0, 100.0], [100.0, 101.0], "15min")
    old_time = (pd.Timestamp(one.iloc[-1]["date"]) - pd.Timedelta(minutes=16)).isoformat()
    store.freeze_directional_cover_anchor(
        instrument="ETH-USDT-SWAP", direction=1,
        five_bar_time=old_time, one_anchor_time=old_time,
        stop_reference=95.0)
    result = validation_execution.persisted_directional_cover_recovery(
        store, "ETH-USDT-SWAP", one, fifteen, 1)
    assert not result[0] and result[3] == "expired"

    fresh_time = pd.Timestamp(one.iloc[20]["date"]).isoformat()
    store.freeze_directional_cover_anchor(
        instrument="ETH-USDT-SWAP", direction=1,
        five_bar_time=fresh_time, one_anchor_time=fresh_time,
        stop_reference=96.1)
    result = validation_execution.persisted_directional_cover_recovery(
        store, "ETH-USDT-SWAP", one, fifteen, 1)
    assert not result[0] and result[3] == "failed"
    assert "stop was broken" in result[1]


def test_latest_directional_cover_anchor_survives_store_reopen(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = StateStore(path)
    try:
        store.freeze_directional_cover_anchor(
            instrument="ETH-USDT-SWAP", direction=1,
            five_bar_time="2026-09-10T03:00:00+00:00",
            one_anchor_time="2026-09-10T03:01:00+00:00", stop_reference=2475.0)
        store.freeze_directional_cover_anchor(
            instrument="ETH-USDT-SWAP", direction=1,
            five_bar_time="2026-09-10T03:05:00+00:00",
            one_anchor_time="2026-09-10T03:06:00+00:00", stop_reference=2477.0)
    finally:
        store.close()
    reopened = StateStore(path)
    try:
        latest = reopened.latest_directional_cover_anchor(
            instrument="ETH-USDT-SWAP", direction=1)
    finally:
        reopened.close()
    assert latest["five_bar_time"] == "2026-09-10T03:05:00+00:00"
    assert float(latest["stop_reference"]) == 2477.0


def test_lagging_uptrend_arrows_do_not_reject_short_reversal_stage():
    allowed, reason = validation_execution.trend_alignment_allows_reversal_stage(
        -1, 1, 1, "price_below_flat_falling_ma5", False)
    assert allowed and "不参与反转入场" in reason


def test_shape_label_does_not_replace_reversal_stage_direction():
    allowed, reason = validation_execution.trend_alignment_allows_reversal_stage(
        1, 1, 1, "price_reclaim_ma5", False, "sideways_neutral")
    assert allowed and "不参与反转入场" in reason

    allowed, reason = validation_execution.trend_alignment_allows_reversal_stage(
        -1, 1, 1, "small_death_cross", False, "sideways_neutral")
    assert allowed and "不参与反转入场" in reason


def test_sideways_half_cover_ma5_break_reenters_instead_of_waiting_pullback():
    assert sideways_top_short_bypasses_ma5_chase(
        -1, "sideways_neutral", True, "price_break_ma5", "[MA5_CHASE]far")
    assert sideways_top_short_bypasses_ma5_chase(
        -1, "sideways_neutral", True, "small_death_cross", "[LATE_LAUNCH]late")
    assert not sideways_top_short_bypasses_ma5_chase(
        1, "sideways_neutral", True, "price_reclaim_ma5", "[MA5_CHASE]far")
    assert not sideways_top_short_bypasses_ma5_chase(
        -1, "sideways_neutral", False, "price_break_ma5", "[MA5_CHASE]far")
    assert sideways_top_short_bypasses_ma5_chase(
        -1, "downtrend_continuation_range", True,
        "price_break_ma5", "[MA5_CHASE]far")


def test_aligned_downtrend_prefers_pullback_short():
    allowed, reason = validation_execution.trend_alignment_allows_reversal_stage(
        -1, -1, -1, "price_below_flat_falling_ma5", False)
    assert allowed
    assert "不参与反转入场" in reason


def test_countertrend_stage_is_not_vetoed_by_timeframe_arrows():
    assert validation_execution.trend_alignment_allows_reversal_stage(
        -1, 1, 1, "price_break_ma5", False, "true_top_reversal")[0]
    assert validation_execution.trend_alignment_allows_reversal_stage(
        -1, 1, 1, "price_break_ma5", True, "true_top_reversal")[0]
    assert validation_execution.trend_alignment_allows_reversal_stage(
        -1, 1, 1, "price_break_ma5", True, "mixed_reversal_candidate")[0]


def test_confirmed_top_plus_two_short_stages_override_lagging_uptrend_labels():
    allowed, reason = validation_execution.trend_alignment_allows_reversal_stage(
        -1, 1, 1, "price_break_ma5", False, "mixed_reversal_candidate",
        same_time_stage2_count=2, confirmed_reversal_anchor=True)
    assert allowed
    assert "不参与反转入场" in reason


def test_single_countertrend_stage_is_not_blocked_by_lagging_arrows():
    assert validation_execution.trend_alignment_allows_reversal_stage(
        -1, 1, 1, "price_break_ma5", False, "mixed_reversal_candidate",
        same_time_stage2_count=1, confirmed_reversal_anchor=True)[0]


def test_quality_gate_uses_confirmed_history_plus_current_live_candle():
    closed = pd.DataFrame({
        "date": pd.date_range("2026-09-03T04:25:00Z", periods=21, freq="1min"),
        "open": [100.0] * 21, "high": [100.5] * 21,
        "low": [99.5] * 21, "close": [100.0] * 21,
        "volume": [100.0] * 21,
    })
    live = pd.DataFrame({
        "date": [pd.Timestamp("2026-09-03T04:46:00Z")],
        "open": [100.0], "high": [101.2], "low": [99.9],
        "close": [101.0], "volume": [130.0],
    })
    combined = merge_closed_and_live_market(closed, live)
    assert len(combined) == 22
    assert combined.iloc[-1]["date"] == live.iloc[-1]["date"]
    allowed, reason = one_minute_launch_quality_gate(combined, 1, 100.0)
    assert allowed, reason


def test_live_candle_replaces_same_timestamp_confirmed_placeholder():
    closed = pd.DataFrame({
        "date": pd.date_range("2026-09-03T04:25:00Z", periods=22, freq="1min"),
        "close": [100.0] * 22,
    })
    live = pd.DataFrame({
        "date": [closed.iloc[-1]["date"]], "close": [101.25],
    })
    combined = merge_closed_and_live_market(closed, live)
    assert len(combined) == len(closed)
    assert float(combined.iloc[-1]["close"]) == 101.25


def test_top_anchor_above_bullish_stack_marks_reversal_launch_zone():
    close = [100.0 + index * .4 for index in range(24)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=24, freq="1min", tz="UTC"),
        "open": close, "high": [value + .5 for value in close],
        "low": [value - .2 for value in close], "close": close,
    })
    allowed, reason = top_anchor_above_bullish_ma_stack(
        frame, frame.iloc[-1]["date"])
    assert allowed, reason
    assert "above MA5, MA10 and MA20" in reason


def test_midrange_top_without_bullish_stack_cannot_override_trend_gate():
    close = [100.0] * 20 + [101.0, 99.0, 101.0, 99.0]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=24, freq="1min", tz="UTC"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
    })
    assert not top_anchor_above_bullish_ma_stack(
        frame, frame.iloc[-1]["date"])[0]


def test_top_above_all_mas_allows_override_while_ma10_and_ma20_converge():
    close = [100.0] * 20 + [103.0, 102.0, 101.8, 102.2]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=24, freq="1min", tz="UTC"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
    })
    frame.loc[frame.index[-1], "high"] = 104.0
    allowed, reason = top_anchor_above_bullish_ma_stack(
        frame, frame.iloc[-1]["date"])
    assert allowed, reason
    assert "above MA5, MA10 and MA20" in reason


def test_bottom_below_all_mas_allows_mirrored_downtrend_override():
    close = [104.0 - index * .2 for index in range(24)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=24, freq="1min", tz="UTC"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
    })
    frame.loc[frame.index[-1], "low"] = 96.0
    allowed, reason = bottom_anchor_below_bearish_ma_stack(
        frame, frame.iloc[-1]["date"])
    assert allowed, reason
    assert "below MA5, MA10 and MA20" in reason


def test_three_timeframe_high_exhaustion_bypasses_only_ma5_timing_rejections():
    def rising_frame(periods, freq):
        close = [100.0 + index * .4 for index in range(periods)]
        return pd.DataFrame({
            "date": pd.date_range("2026-09-04", periods=periods, freq=freq, tz="UTC"),
            "open": [value - .2 for value in close],
            "high": [value + .8 for value in close],
            "low": [value - .5 for value in close], "close": close,
        })

    one = rising_frame(30, "1min")
    five = rising_frame(30, "5min")
    fifteen = rising_frame(30, "15min")
    allowed, reason = high_reversal_three_timeframe_ma_exhaustion(
        one, five, fifteen, one.iloc[-1]["date"])

    assert allowed, reason
    assert "5m mature highest-zone" in reason
    assert "15m mature highest-zone" in reason
    assert high_half_cover_bypasses_ma5_chase(True, allowed, "[MA5_CHASE]far")
    assert high_half_cover_bypasses_ma5_chase(True, allowed, "[LATE_LAUNCH]late")
    assert high_half_cover_bypasses_ma5_chase(
        True, allowed, "价格已跌回冻结点反向一侧，前置启动失效")
    assert not high_half_cover_bypasses_ma5_chase(False, allowed, "[MA5_CHASE]far")
    assert not high_half_cover_bypasses_ma5_chase(True, allowed, "stale stage")


def test_new_bullish_ma_stack_startup_is_not_mature_high_exhaustion():
    def startup_frame(periods, freq):
        close = [100.0] * (periods - 2) + [100.1, 100.3]
        return pd.DataFrame({
            "date": pd.date_range("2026-09-04", periods=periods, freq=freq, tz="UTC"),
            "open": close, "high": [value + .2 for value in close],
            "low": [value - .2 for value in close], "close": close,
        })

    one = startup_frame(30, "1min")
    five = startup_frame(30, "5min")
    fifteen = startup_frame(30, "15min")
    allowed, reason = high_reversal_three_timeframe_ma_exhaustion(
        one, five, fifteen, one.iloc[-1]["date"])
    assert not allowed
    assert "startup" in reason


def test_mature_one_and_five_minute_high_is_not_vetoed_by_fifteen_minute():
    def rising(periods, freq):
        close = [100.0 + index * .4 for index in range(periods)]
        return pd.DataFrame({
            "date": pd.date_range("2026-09-04", periods=periods, freq=freq, tz="UTC"),
            "open": [value - .2 for value in close],
            "high": [value + .8 for value in close],
            "low": [value - .5 for value in close], "close": close,
        })

    one, five = rising(30, "1min"), rising(30, "5min")
    flat = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=30, freq="15min", tz="UTC"),
        "open": [100.0] * 30, "high": [100.2] * 30,
        "low": [99.8] * 30, "close": [100.0] * 30,
    })
    allowed, reason = high_reversal_three_timeframe_ma_exhaustion(
        one, five, flat, one.iloc[-1]["date"])
    assert allowed, reason
    assert "15m optional reference is not aligned" in reason


def test_bottom_long_requires_same_mature_low_on_one_and_five_minute():
    def falling(periods, freq):
        close = [120.0 - index * .4 for index in range(periods)]
        return pd.DataFrame({
            "date": pd.date_range("2026-09-04", periods=periods, freq=freq, tz="UTC"),
            "open": [value + .2 for value in close],
            "high": [value + .5 for value in close],
            "low": [value - .8 for value in close], "close": close,
        })

    one, five = falling(30, "1min"), falling(30, "5min")
    allowed, reason = low_reversal_two_timeframe_ma_exhaustion(
        one, five, one.iloc[-1]["date"])
    assert allowed, reason
    assert "1m mature lowest-zone" in reason and "5m mature lowest-zone" in reason

    five_midtrend = five.copy()
    five_midtrend["close"] = 100.0
    five_midtrend["open"] = 100.0
    five_midtrend["high"] = 100.2
    five_midtrend["low"] = 99.8
    allowed, reason = low_reversal_two_timeframe_ma_exhaustion(
        one, five_midtrend, one.iloc[-1]["date"])
    assert not allowed
    assert "mid-trend noise" in reason


def test_stage_three_recovery_prefers_fresh_micro_swing_over_remote_high():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=20, freq="1min"),
        "open": [100.0] * 20,
        "high": [110.0] + [101.0] * 19,
        "low": [99.5] * 20,
        "close": [100.0] * 20,
    })
    allowed, stop, reason = stage_three_micro_structure_stop(
        frame, 100.0, -1)
    assert allowed, reason
    assert 101.0 < stop < 103.0
    assert "4-bar micro swing" in reason


def test_continuation_location_rejects_local_top_long_after_staged_entry_qualifies():
    close = [100.0 + index * .2 for index in range(30)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-03", periods=30, freq="1min"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
    })
    allowed, reason = continuation_stage_location_allowed(
        frame, 1, "uptrend_continuation_long")
    assert not allowed and "局部末端" in reason
    assert continuation_stage_location_allowed(
        frame, -1, "true_top_reversal")[0]


def test_continuation_location_keeps_real_pullback_long_and_throwback_short():
    close = [100.0 + index * .2 for index in range(30)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-03", periods=30, freq="1min"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
    })
    frame.loc[frame.index[-1], "close"] = 102.0
    assert continuation_stage_location_allowed(
        frame, 1, "uptrend_continuation_long")[0]
    frame.loc[frame.index[-1], "close"] = 104.8
    assert continuation_stage_location_allowed(
        frame, -1, "downtrend_continuation_short")[0]


def test_uptrend_continuation_requires_pullback_below_both_ma5_and_ma10():
    close = [100.0 + index * .2 for index in range(20)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-05", periods=20, freq="1min"),
        "open": close, "high": [value + .15 for value in close],
        "low": [value - .15 for value in close], "close": close,
    })
    allowed, reason = continuation_pullback_reached_ma5_ma10(frame, 1)
    assert not allowed and "入场收盘不得高于MA5" in reason

    frame.loc[frame.index[-2], "low"] = 101.0
    frame.loc[frame.index[-1], "close"] = 103.0
    allowed, reason = continuation_pullback_reached_ma5_ma10(frame, 1)
    assert allowed and "MA5和MA10下方" in reason

    frame.loc[frame.index[-1], "close"] = 110.0
    frame.loc[frame.index[-1], "low"] = 109.8
    allowed, reason = continuation_pullback_reached_ma5_ma10(frame, 1)
    assert not allowed and "历史旧低点不能" in reason


def test_resolved_true_top_plus_later_five_minute_cover_keeps_short_bias():
    patterns = [
        {"pattern_type": "price_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-04T19:52:00Z",
         "features_json": '{"market_shape_code":"true_top_reversal","five_minute_half_cover":true}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": -1,
         "confirmed_bar_time": "2026-09-04T20:05:00Z",
         "features_json": '{"market_shape_code":"mixed_structure_candidate","five_minute_half_cover":true,"five_minute_ma20_reversal_confirmed":true}'},
    ]
    direction, reason = validation_execution.durable_dual_reversal_zone_bias(
        patterns, "2026-09-04T20:20:00Z")
    assert direction == -1
    assert "首单漏掉或止损不清除" in reason


def test_newer_same_side_true_top_pair_replaces_older_top_anchor():
    patterns = [
        {"pattern_type": "price_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T01:00:00Z", "entry_reference": 2500.0,
         "features_json": '{"market_shape_code":"true_top_reversal","five_minute_half_cover":true,"five_minute_ma20_reversal_confirmed":true}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T01:05:00Z", "entry_reference": 2501.0,
         "features_json": '{"market_shape_code":"true_top_reversal","five_minute_half_cover":true,"five_minute_ma20_reversal_confirmed":true}'},
        {"pattern_type": "price_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T03:40:00Z", "entry_reference": 2513.0,
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T03:45:00Z", "entry_reference": 2511.0,
         "features_json": '{"market_shape_code":"true_top_reversal","five_minute_half_cover":true,"five_minute_ma20_reversal_confirmed":true}'},
    ]
    direction, reason = validation_execution.durable_dual_reversal_zone_bias(
        patterns, "2026-09-06T03:50:00Z")
    assert direction == -1
    assert "2026-09-06T03:45:00" in reason
    assert "同方向更新的真正高位/低位覆盖旧锚点" in reason


def test_three_timeframe_endpoint_is_capped_at_1m_5m_15m_and_ignores_1h_4h():
    patterns = [
        {"pattern_type": "price_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T00:45:00Z",
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T00:50:00Z",
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "price_reversal_zone:15m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T01:00:00Z",
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "price_reversal_zone:1H", "direction": 1,
         "confirmed_bar_time": "2026-09-06T01:00:00Z",
         "features_json": '{"market_shape_code":"true_bottom_reversal"}'},
        {"pattern_type": "price_reversal_zone:4H", "direction": 1,
         "confirmed_bar_time": "2026-09-06T01:00:00Z",
         "features_json": '{"market_shape_code":"true_bottom_reversal"}'},
    ]

    direction, reason = validation_execution.durable_three_timeframe_reversal_zone_bias(
        patterns, "2026-09-06T01:10:00Z")

    assert direction == -1
    assert "15分钟" in reason and "1小时和4小时不参与" in reason


def test_bottom_owned_uptrend_blocks_countertrend_short_after_ma5_break():
    assert validation_execution.countertrend_short_before_ma5_allowed(
        durable_bias=1, direction=-1, price=100.1, ma5=100.0)
    assert not validation_execution.countertrend_short_before_ma5_allowed(
        durable_bias=1, direction=-1, price=99.9, ma5=100.0)
    assert validation_execution.countertrend_short_before_ma5_allowed(
        durable_bias=-1, direction=-1, price=99.9, ma5=100.0)


def test_fresh_confirmed_reversal_replaces_old_opposite_bias_in_both_directions():
    for direction in (-1, 1):
        assert validation_execution.fresh_confirmed_reversal_overrides_old_bias(
            direction=direction, directional_cover_ok=True,
            three_timeframe_reversal=True)
        assert not validation_execution.fresh_confirmed_reversal_overrides_old_bias(
            direction=direction, directional_cover_ok=False,
            three_timeframe_reversal=True)


def test_same_parent_direction_local_turn_prefers_pullback_or_throwback():
    assert validation_execution.local_candidate_prefers_trend_continuation(
        direction=-1, five_direction=-1, fifteen_direction=1,
        local_candidate=True, confirmed_reversal=False)
    assert validation_execution.local_candidate_prefers_trend_continuation(
        direction=1, five_direction=-1, fifteen_direction=1,
        local_candidate=True, confirmed_reversal=False)
    assert not validation_execution.local_candidate_prefers_trend_continuation(
        direction=-1, five_direction=-1, fifteen_direction=-1,
        local_candidate=True, confirmed_reversal=True)
    assert not validation_execution.local_candidate_prefers_trend_continuation(
        direction=-1, five_direction=1, fifteen_direction=1,
        durable_parent_direction=-1, local_candidate=True,
        confirmed_reversal=True)


def test_confirmed_uptrend_pullback_uses_local_runway_after_each_flat_reset():
    assert validation_execution.confirmed_uptrend_pullback_uses_local_runway(
        direction=1, locked_trend_pullback=True,
        fresh_rising_pullback=False)
    assert validation_execution.confirmed_uptrend_pullback_uses_local_runway(
        direction=1, locked_trend_pullback=False,
        fresh_rising_pullback=True)
    assert not validation_execution.confirmed_uptrend_pullback_uses_local_runway(
        direction=-1, locked_trend_pullback=True,
        fresh_rising_pullback=True)


def test_fifteen_minute_parent_trend_preserves_fresh_pullback_before_stale_lock_gate():
    for direction in (-1, 1):
        assert validation_execution.parent_trend_takeover_preserves_fresh_stage(
            direction=direction, fifteen_direction=direction,
            local_stage=True, pending_takeover_direction=direction)
        assert not validation_execution.parent_trend_takeover_preserves_fresh_stage(
            direction=direction, fifteen_direction=-direction,
            local_stage=True, pending_takeover_direction=direction)


def test_three_timeframe_short_is_no_longer_observe_only():
    assert "three_timeframe_fast_reversal_short" not in validation_execution.OBSERVE_ONLY_BRANCHES
    assert "three_timeframe_reversal_recovery_short" not in validation_execution.OBSERVE_ONLY_BRANCHES


def test_20260916_late_short_is_rejected_after_signal_has_already_run():
    allowed, reason = validation_execution.fresh_reversal_execution_location(
        direction=-1, signal_price=2413.06, execution_price=2409.75,
        atr1=2.5, signal_time="2026-09-16T12:22:00Z",
        execution_time="2026-09-16T12:24:00Z")
    assert not allowed
    assert "3.31点" in reason
    assert "禁止迟到追单" in reason


def test_fresh_reversal_execution_location_allows_immediate_turn():
    allowed, reason = validation_execution.fresh_reversal_execution_location(
        direction=-1, signal_price=2413.06, execution_price=2412.60,
        atr1=2.5, signal_time="2026-09-16T12:22:00Z",
        execution_time="2026-09-16T12:22:35Z")
    assert allowed
    assert "首次反转成交窗口" in reason


def test_confirmed_bottom_is_not_rejected_only_because_cover_took_121_seconds():
    allowed, reason = validation_execution.fresh_reversal_execution_location(
        direction=1, signal_price=2430.28, execution_price=2430.08,
        atr1=2.0, signal_time="2026-09-17T11:48:00Z",
        execution_time="2026-09-17T11:50:01Z")
    assert allowed
    assert "121秒" in reason


def test_confirmation_window_still_expires_after_three_minutes():
    allowed, reason = validation_execution.fresh_reversal_execution_location(
        direction=1, signal_price=2430.28, execution_price=2430.08,
        atr1=2.0, signal_time="2026-09-17T11:48:00Z",
        execution_time="2026-09-17T11:51:01Z")
    assert not allowed
    assert "超过180秒" in reason


def test_primary_gate_flags_are_initialized_before_parent_trend_reclassification():
    source = open(validation_execution.__file__, encoding="utf-8").read()
    decision_block = source.index("parent_trend_reclassifies_local_entry =")
    assert source.index("five_minute_local_reversal_entry = False") < decision_block
    assert source.index("endpoint_half_cover_primary_entry = False") < decision_block


def test_newer_dual_bottom_changes_direction_even_if_older_three_timeframe_top_exists():
    patterns = [
        {"pattern_type": "price_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T00:00:00Z", "entry_reference": 110.0,
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T00:05:00Z", "entry_reference": 109.0,
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "price_reversal_zone:15m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T00:15:00Z", "entry_reference": 108.0,
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "price_reversal_zone:1m", "direction": 1,
         "confirmed_bar_time": "2026-09-06T01:00:00Z", "entry_reference": 100.0,
         "features_json": '{"market_shape_code":"true_bottom_reversal"}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": 1,
         "confirmed_bar_time": "2026-09-06T01:05:00Z", "entry_reference": 101.0,
         "features_json": '{"market_shape_code":"true_bottom_reversal","five_minute_half_cover":true,"five_minute_ma20_reversal_confirmed":true}'},
    ]

    dual_direction, _ = validation_execution.durable_dual_reversal_zone_bias(
        patterns, "2026-09-06T01:10:00Z")
    three_direction, _ = validation_execution.durable_three_timeframe_reversal_zone_bias(
        patterns, "2026-09-06T01:10:00Z")

    assert dual_direction == 1
    assert three_direction == -1


def test_relaxed_lowest_ma_endpoint_plus_later_five_confirmation_starts_long_regime():
    patterns = [
        {"pattern_type": "price_reversal_zone:1m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T23:03:00Z",
         "features_json": '{"market_shape_code":"downtrend_continuation_range",'
                          '"prior_drawdown_atr":4.1,"one_position":0.20}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T23:20:00Z",
         "features_json": '{"market_shape_code":"downtrend_continuation_range",'
                          '"five_minute_shape_confirmed":true,"five_minute_half_cover":true,'
                          '"five_minute_ma20_reversal_confirmed":true,"five_position":0.67}'},
    ]
    direction, _ = validation_execution.durable_dual_reversal_zone_bias(
        patterns, "2026-09-04T23:25:00Z")
    assert direction == 1


def test_relaxed_bottom_endpoint_is_invalid_after_its_low_is_broken():
    patterns = [
        {"pattern_type": "price_reversal_zone:1m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T20:10:00Z", "entry_reference": 100.0,
         "features_json": '{"market_shape_code":"downtrend_continuation_range",'
                          '"prior_drawdown_atr":4.0,"one_position":0.20}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T20:20:00Z", "entry_reference": 101.0,
         "features_json": '{"market_shape_code":"downtrend_continuation_range",'
                          '"five_minute_shape_confirmed":true,"five_position":0.70}'},
        {"pattern_type": "price_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-04T20:30:00Z", "entry_reference": 99.5,
         "features_json": '{"market_shape_code":"mixed_structure_candidate"}'},
    ]
    direction, _ = validation_execution.durable_dual_reversal_zone_bias(
        patterns, "2026-09-04T20:40:00Z")
    assert direction == 0


def test_relaxed_bottom_endpoint_survives_sub_basis_point_reference_noise():
    patterns = [
        {"pattern_type": "price_reversal_zone:1m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T23:03:00Z", "entry_reference": 2449.68,
         "features_json": '{"market_shape_code":"downtrend_continuation_range",'
                          '"prior_drawdown_atr":4.1,"one_position":0.20}'},
        {"pattern_type": "price_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-04T23:18:00Z", "entry_reference": 2449.54,
         "features_json": '{"market_shape_code":"mixed_structure_candidate"}'},
        {"pattern_type": "price_reversal_zone:5m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T23:20:00Z", "entry_reference": 2452.80,
         "features_json": '{"market_shape_code":"downtrend_continuation_range",'
                          '"five_minute_shape_confirmed":true,"five_minute_half_cover":true,'
                          '"five_minute_ma20_reversal_confirmed":true,"five_position":0.67}'},
    ]
    direction, _ = validation_execution.durable_dual_reversal_zone_bias(
        patterns, "2026-09-04T23:25:00Z")
    assert direction == 1


def test_qualified_specialised_top_short_bypasses_legacy_observe_only_gate():
    assert validation_execution.specialised_top_short_bypasses_observe_only_gate(
        -1, five_high_cover=True)
    assert validation_execution.specialised_top_short_bypasses_observe_only_gate(
        -1, top_weakening=True)
    assert not validation_execution.specialised_top_short_bypasses_observe_only_gate(
        1, five_high_cover=True)
    assert not validation_execution.specialised_top_short_bypasses_observe_only_gate(-1)


def test_fresh_opposite_local_turn_blocks_late_continuation_revival():
    blocked = validation_execution.fresh_opposite_local_turn_blocks_continuation
    assert blocked(direction=-1, trend_continuation_entry=True,
                   opposite_local_turn=True)
    assert blocked(direction=1, trend_continuation_entry=True,
                   opposite_local_turn=True)
    assert not blocked(direction=-1, trend_continuation_entry=True,
                       opposite_local_turn=False)
    assert not blocked(direction=-1, trend_continuation_entry=False,
                       opposite_local_turn=True)


def test_five_minute_bearish_rollover_continues_without_bullish_cover():
    five_close = [120 - index * .6 for index in range(22)]
    five = pd.DataFrame({
        "date": pd.date_range("2026-09-17T11:15:00Z", periods=22, freq="5min"),
        "open": [value + .35 for value in five_close],
        "high": [value + .7 for value in five_close],
        "low": [value - .5 for value in five_close],
        "close": five_close,
    })
    one_close = [111 - index * .18 for index in range(24)]
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-17T12:40:00Z", periods=24, freq="min"),
        "open": [value + .12 for value in one_close],
        "high": [value + .3 for value in one_close],
        "low": [value - .3 for value in one_close],
        "close": one_close,
    })
    live = pd.DataFrame({
        "date": [pd.Timestamp("2026-09-17T13:05:00Z")],
        "open": [five_close[-1] + .1], "high": [five_close[-1] + .2],
        "low": [five_close[-1] - .8], "close": [five_close[-1] - .6],
    })
    direction, reason, stop = (
        validation_execution.five_minute_bearish_rollover_continuation_short_setup(
            one, five, live, "2026-09-17T13:06:00Z"))
    assert direction == -1
    assert "不要求当前5分钟阴线覆盖前一根阳线" in reason
    assert stop > float(live.iloc[-1]["close"])


def test_five_minute_bearish_rollover_only_enters_near_new_bar_boundary():
    five_close = [120 - index * .6 for index in range(22)]
    five = pd.DataFrame({
        "date": pd.date_range("2026-09-17T11:15:00Z", periods=22, freq="5min"),
        "open": [value + .35 for value in five_close],
        "high": [value + .7 for value in five_close], "low": [value - .5 for value in five_close],
        "close": five_close,
    })
    one_close = [110 - index * .18 for index in range(24)]
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-17T12:40:00Z", periods=24, freq="min"),
        "open": [value + .12 for value in one_close], "high": [value + .3 for value in one_close],
        "low": [value - .3 for value in one_close], "close": one_close,
    })
    live = pd.DataFrame({"date": [pd.Timestamp("2026-09-17T13:05:00Z")],
                         "open": [107.2], "high": [107.3], "low": [106.1], "close": [106.3]})
    direction, reason, _ = validation_execution.five_minute_bearish_rollover_continuation_short_setup(
        one, five, live, "2026-09-17T13:08:01Z")
    assert direction == 0
    assert "前2分钟" in reason


def test_downtrend_continuation_mirrors_fast_ma_pullback_location():
    close = [104.0 - index * .2 for index in range(20)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-05", periods=20, freq="1min"),
        "open": close, "high": [value + .15 for value in close],
        "low": [value - .15 for value in close], "close": close,
    })
    allowed, reason = continuation_pullback_reached_ma5_ma10(frame, -1)
    assert not allowed and "入场收盘不得低于MA5" in reason

    frame.loc[frame.index[-2], "high"] = 103.0
    frame.loc[frame.index[-1], "close"] = 101.0
    allowed, reason = continuation_pullback_reached_ma5_ma10(frame, -1)
    assert allowed and "MA5和MA10上方" in reason

    frame.loc[frame.index[-1], "close"] = 90.0
    frame.loc[frame.index[-1], "high"] = 90.2
    allowed, reason = continuation_pullback_reached_ma5_ma10(frame, -1)
    assert not allowed and "历史旧高点不能" in reason


def test_locked_dual_regime_ma5_edge_pullback_bypasses_generic_gate_both_sides():
    allowed = validation_execution.durable_regime_continuation_primary_gate_allowed
    assert allowed(trend_continuation_entry=True, local_high_short=True,
                   direction=-1, durable_bias=-1, location_ok=True)
    assert allowed(trend_continuation_entry=True, local_high_short=False,
                   direction=1, durable_bias=1, location_ok=True)
    assert not allowed(trend_continuation_entry=True, local_high_short=True,
                       direction=-1, durable_bias=0, location_ok=True)
    assert not allowed(trend_continuation_entry=True, local_high_short=True,
                       direction=-1, durable_bias=-1, location_ok=False)


def test_durable_regime_allows_three_layers_but_not_fourth_or_pending_order():
    allowed = validation_execution.durable_regime_continuation_addon_allowed
    assert allowed(core_reversal_open=False, early_dual_entry=False,
                   durable_bias=-1, direction=-1, position_layers=1,
                   pending_open_order=False)
    assert allowed(core_reversal_open=False, early_dual_entry=False,
                   durable_bias=-1, direction=-1, position_layers=2,
                   pending_open_order=False)
    assert not allowed(core_reversal_open=False, early_dual_entry=False,
                       durable_bias=-1, direction=-1, position_layers=3,
                       pending_open_order=False)
    assert not allowed(core_reversal_open=False, early_dual_entry=False,
                       durable_bias=-1, direction=-1, position_layers=2,
                       pending_open_order=True)


def test_closed_trend_addons_release_capacity_for_later_fresh_opportunities():
    """The three-layer ceiling is concurrent, never a lifetime entry counter."""
    primary = validation_execution.durable_regime_continuation_primary_gate_allowed
    addon = validation_execution.durable_regime_continuation_addon_allowed

    # After all earlier layers are closed, a later fresh pullback can become a
    # new primary trend-continuation entry; no historical-entry count is used.
    assert primary(trend_continuation_entry=True, local_high_short=False,
                   direction=1, durable_bias=1, location_ok=True)
    # If one new layer is still open, another independent structure may add;
    # only the current concurrent exposure and pending orders consume capacity.
    assert addon(core_reversal_open=False, early_dual_entry=False,
                 durable_bias=1, direction=1, position_layers=1,
                 pending_open_order=False)


def test_fresh_local_reversal_shape_can_add_second_and_third_layer_only():
    allowed = validation_execution.repeated_local_reversal_addon_allowed
    assert allowed(local_reversal_entry=True, durable_bias=1, direction=1,
                   position_layers=1, pending_open_order=False)
    assert allowed(local_reversal_entry=True, durable_bias=1, direction=1,
                   position_layers=2, pending_open_order=False)
    assert not allowed(local_reversal_entry=True, durable_bias=1, direction=1,
                       position_layers=3, pending_open_order=False)
    assert not allowed(local_reversal_entry=True, durable_bias=1, direction=1,
                       position_layers=2, pending_open_order=True)
    assert not allowed(local_reversal_entry=True, durable_bias=-1, direction=1,
                       position_layers=1, pending_open_order=False)
    assert not allowed(local_reversal_entry=False, durable_bias=1, direction=1,
                       position_layers=1, pending_open_order=False)


def test_only_actual_matching_ma_spread_endpoint_entry_uses_five_minute_ma5_exit():
    eligible = validation_execution.medium_trend_five_ma5_hold_eligible
    assert eligible(durable_bias=1, direction=1,
                    confirmed_endpoint_reversal_entry=True)
    assert eligible(durable_bias=-1, direction=-1,
                    confirmed_endpoint_reversal_entry=True)
    assert not eligible(durable_bias=0, direction=1,
                        confirmed_endpoint_reversal_entry=True)
    assert not eligible(durable_bias=1, direction=-1,
                        confirmed_endpoint_reversal_entry=True)
    assert not eligible(durable_bias=1, direction=1,
                        confirmed_endpoint_reversal_entry=False)


def test_sweep_trial_promotes_only_after_both_one_and_five_minute_clear_slow_mas():
    dates = pd.date_range("2026-09-05", periods=20, freq="1min")
    confirmed = pd.DataFrame({
        "date": dates,
        "open": list(range(100, 120)), "high": list(range(101, 121)),
        "low": list(range(99, 119)), "close": list(range(100, 120)),
    })
    assert validation_execution.sweep_trial_endpoint_confirmed(
        confirmed, confirmed, 1)
    unconfirmed = confirmed.copy()
    unconfirmed.loc[unconfirmed.index[-1], "close"] = 100
    assert not validation_execution.sweep_trial_endpoint_confirmed(
        confirmed, unconfirmed, 1)


def test_sweep_trial_endpoint_confirmation_is_long_short_symmetric():
    dates = pd.date_range("2026-09-05", periods=20, freq="1min")
    falling = pd.DataFrame({
        "date": dates,
        "open": list(range(120, 100, -1)), "high": list(range(121, 101, -1)),
        "low": list(range(119, 99, -1)), "close": list(range(120, 100, -1)),
    })
    assert validation_execution.sweep_trial_endpoint_confirmed(
        falling, falling, -1)


def _endpoint_pattern(timeframe: str, direction: int, at: str):
    shape = "true_bottom_reversal" if direction > 0 else "true_top_reversal"
    return {
        "pattern_type": f"price_reversal_zone:{timeframe}",
        "direction": direction,
        "confirmed_bar_time": at,
        "features_json": json.dumps({
            "market_shape_code": shape,
            "five_minute_half_cover": timeframe == "5m",
            "five_minute_ma20_reversal_confirmed": timeframe == "5m",
        }),
        "entry_reference": 100.0,
    }


def test_persisted_dual_record_promotes_original_probe_to_five_minute_ma5():
    patterns = [
        _endpoint_pattern("1m", 1, "2026-09-06T00:05:00Z"),
        _endpoint_pattern("5m", 1, "2026-09-06T00:10:00Z"),
    ]
    promoted, reason = validation_execution.persisted_dual_endpoint_promotes_sweep_trial(
        patterns, "2026-09-06T00:12:00Z", 1,
        "early_one_minute_frozen_stage_launch", {})
    assert promoted and "00:10:00" in reason


def test_repeated_matching_endpoint_record_keeps_promotion_valid():
    patterns = [
        _endpoint_pattern("1m", 1, "2026-09-06T00:05:00Z"),
        _endpoint_pattern("5m", 1, "2026-09-06T00:10:00Z"),
        _endpoint_pattern("1m", 1, "2026-09-06T00:20:00Z"),
        _endpoint_pattern("5m", 1, "2026-09-06T00:25:00Z"),
    ]
    promoted, reason = validation_execution.persisted_dual_endpoint_promotes_sweep_trial(
        patterns, "2026-09-06T00:30:00Z", 1,
        "early_low_sweep_reclaim", {"five_minute_ma5_core_hold": True})
    assert promoted and "00:25:00" in reason


def test_persisted_dual_record_does_not_promote_continuation_addon():
    patterns = [
        _endpoint_pattern("1m", 1, "2026-09-06T00:05:00Z"),
        _endpoint_pattern("5m", 1, "2026-09-06T00:10:00Z"),
    ]
    promoted, reason = validation_execution.persisted_dual_endpoint_promotes_sweep_trial(
        patterns, "2026-09-06T00:12:00Z", 1,
        "uptrend_continuation_long", {"core_reversal_continuation_addon": True})
    assert not promoted and "1m MA5" in reason


def test_layered_exit_selects_latest_one_minute_addon_before_five_minute_core():
    rows = [
        {"trade_uid": "core", "signal_context_json": json.dumps({
            "five_minute_ma5_core_hold": True})},
        {"trade_uid": "addon-1", "signal_context_json": json.dumps({
            "five_minute_ma5_core_hold": False,
            "core_reversal_continuation_addon": True})},
        {"trade_uid": "addon-2", "signal_context_json": json.dumps({
            "five_minute_ma5_core_hold": False,
            "core_reversal_continuation_addon": True})},
    ]
    selected, use_five = validation_execution.select_ma5_exit_lifecycle(rows)
    assert selected["trade_uid"] == "addon-2"
    assert use_five is False


def test_layered_exit_uses_five_minute_only_after_addons_are_gone():
    rows = [{"trade_uid": "core", "signal_context_json": json.dumps({
        "five_minute_ma5_core_hold": True})}]
    selected, use_five = validation_execution.select_ma5_exit_lifecycle(rows)
    assert selected["trade_uid"] == "core"
    assert use_five is True


def test_short_probe_stop_is_moved_beyond_recent_swing_instead_of_capped_at_1_5():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-03", periods=12, freq="1min"),
        "open": [100.0] * 12, "high": [100.8] * 11 + [102.0],
        "low": [99.5] * 12, "close": [100.0] * 12,
    })
    allowed, stop, reason = reversal_stop_outside_recent_structure(
        frame, 100.0, -1, 101.0)
    assert allowed
    assert stop == 100.02
    assert "实体边界" in reason


def test_probe_is_rejected_when_structure_outside_stop_exceeds_maximum_risk():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-03", periods=12, freq="1min"),
        "open": [100.0] * 12, "high": [104.0] * 12,
        "low": [99.5] * 12, "close": [100.0] * 12,
    })
    allowed, stop, reason = reversal_stop_outside_recent_structure(
        frame, 100.0, -1, 101.5)
    assert allowed and stop == 100.02


def test_aligned_trend_pullback_keeps_structure_stop_beyond_three_points():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-03", periods=12, freq="1min"),
        "open": [100.0] * 12, "high": [101.0] * 12,
        "low": [95.5] * 12, "close": [100.0] * 12,
    })
    allowed, stop, _ = reversal_stop_outside_recent_structure(
        frame, 100.0, 1, 96.0, maximum_points=float("inf"))
    assert allowed
    assert stop == 99.98


def test_staged_launch_rejects_trigger_after_structure_has_moved_on():
    allowed, reason = staged_launch_is_fresh(
        pd.Timestamp("2026-09-03 21:51:00"), pd.Timestamp("2026-09-03 21:53:00"))
    assert not allowed
    assert "三阶段末端" in reason


def test_staged_launch_freshness_normalizes_mixed_timezone_inputs():
    allowed, reason = staged_launch_is_fresh(
        pd.Timestamp("2026-09-18T09:12:00Z"),
        pd.Timestamp("2026-09-18 09:13:00"))
    assert allowed
    assert "60秒" in reason


def test_minimum_contract_rejects_remote_staged_stop():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-03", periods=21, freq="1min"),
        "open": [100.0] * 21, "high": [101.0] * 21,
        "low": [99.0] * 21, "close": [100.0] * 21,
    })
    allowed, reason = staged_launch_risk_allows(
        frame, 100.0, 90.0, 1, calculated_contracts=1)
    assert not allowed
    assert "最小1张" in reason


def test_qualified_five_minute_local_reversal_is_not_rejected_by_old_two_atr_cap():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-08", periods=21, freq="1min"),
        "open": [100.0] * 21, "high": [101.0] * 21,
        "low": [99.0] * 21, "close": [100.0] * 21,
    })
    allowed, reason = staged_launch_risk_allows(
        frame, 100.0, 90.0, 1, maximum_atr=float("inf"),
        calculated_contracts=1)
    assert allowed
    assert "风险匹配" in reason


def test_original_strategy_live_entry_is_only_reversal_stage_two_or_three():
    assert original_strategy_reversal_entry_allowed(
        1, early_freeze_stage_entry=True, frozen_big_cross_entry=False)
    assert original_strategy_reversal_entry_allowed(
        -1, early_freeze_stage_entry=False, frozen_big_cross_entry=True)
    assert not original_strategy_reversal_entry_allowed(
        -1, early_freeze_stage_entry=False, frozen_big_cross_entry=False)
    assert not original_strategy_reversal_entry_allowed(
        0, early_freeze_stage_entry=True, frozen_big_cross_entry=False)
    assert original_strategy_reversal_entry_allowed(
        1, early_freeze_stage_entry=True, frozen_big_cross_entry=False,
        five_minute_heikin_confirmed=False)


def test_same_tick_stage_one_anchor_immediately_unlocks_stage_two_both_directions():
    for direction, anchor, trigger in (
        (1, "relative_local_bottom_sweep", "price_reclaim_ma5"),
        (-1, "relative_local_top_sweep", "price_break_ma5"),
    ):
        observations = [
            {"stage": anchor, "direction": direction,
             "time": pd.Timestamp("2026-09-02T13:40:00Z"), "price": 100.0},
            {"stage": trigger, "direction": direction,
             "time": pd.Timestamp("2026-09-02T13:40:00Z"), "price": 100.1},
        ]
        matches = matching_current_tick_reversal_anchors(
            observations, direction, observations[1]["time"])
        assert len(matches) == 1 and matches[0]["stage"] == anchor


def test_same_minute_opposite_top_blocks_long_but_not_later_short_stage():
    observations = [
        {"stage": "confirmed_local_top", "direction": -1,
         "time": pd.Timestamp("2026-09-03T17:47:00Z")},
        {"stage": "relative_local_bottom_sweep", "direction": 1,
         "time": pd.Timestamp("2026-09-03T17:47:00Z")},
        {"stage": "price_above_flat_rising_ma5", "direction": 1,
         "time": pd.Timestamp("2026-09-03T17:47:00Z")},
    ]
    blocked, _ = validation_execution.same_minute_opposing_anchor_blocks_stage(
        observations, 1, pd.Timestamp("2026-09-03T17:47:00Z"))
    assert blocked
    blocked, _ = validation_execution.same_minute_opposing_anchor_blocks_stage(
        observations, -1, pd.Timestamp("2026-09-03T17:49:00Z"))
    assert not blocked


def test_two_independent_short_stages_override_one_transient_opposite_bottom():
    observations = [
        {"stage": "confirmed_local_bottom", "direction": 1,
         "time": pd.Timestamp("2026-09-03T18:23:00Z")},
        {"stage": "price_below_flat_falling_ma5", "direction": -1,
         "time": pd.Timestamp("2026-09-03T18:23:00Z")},
        {"stage": "price_break_ma5", "direction": -1,
         "time": pd.Timestamp("2026-09-03T18:23:00Z")},
    ]
    for stage in observations[1:]:
        blocked, reason = validation_execution.same_minute_opposing_anchor_blocks_stage(
            observations, -1, stage["time"])
        assert not blocked
        assert "two independent" in reason


def test_five_minute_heikin_colour_flip_confirms_matching_one_minute_reversal():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-02", periods=8, freq="5min"),
        "open":  [10, 9, 8, 7, 6, 5, 5, 6],
        "high":  [11, 10, 9, 8, 7, 8, 10, 13],
        "low":   [8, 7, 6, 5, 4, 4, 4, 5],
        "close": [9, 8, 7, 6, 5, 5, 8, 11],
        "volume": [100.0] * 8,
    })
    assert five_minute_price_reversal_confirms(frame, 1)
    assert not five_minute_price_reversal_confirms(frame, -1)


def test_five_minute_bearish_colour_does_not_confirm_short_while_ma5_rises():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-02", periods=8, freq="5min"),
        "open":  [10, 11, 12, 13, 14, 15, 17, 17],
        "high":  [12, 13, 14, 15, 16, 18, 18, 18],
        "low":   [9, 10, 11, 12, 13, 14, 15, 14],
        "close": [11, 12, 13, 14, 15, 17, 17, 15],
        "volume": [100.0] * 8,
    })
    assert not five_minute_price_reversal_confirms(frame, -1)


def test_five_minute_first_bearish_turn_confirms_short_with_nearly_flat_ma5():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-02", periods=8, freq="5min"),
        "open":  [10, 11, 12, 13, 14, 15, 15, 14],
        "high":  [12, 13, 14, 15, 16, 17, 16, 15],
        "low":   [9, 10, 11, 12, 13, 14, 13, 12],
        "close": [11, 12, 13, 14, 15, 15, 14, 13.8],
        "volume": [100.0] * 8,
    })
    assert five_minute_price_reversal_confirms(frame, -1)


def test_transient_missing_protection_is_cleared_by_fresh_okx_snapshot():
    position = {"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1",
                "closeOrderAlgo": []}
    stale = {"positions": [position], "orders": [], "algo_orders": []}
    protected = {
        "positions": [position], "orders": [],
        "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "short",
                         "ordType": "conditional", "state": "live", "algoId": "SL",
                         "slTriggerPx": "2500", "sz": "1"}],
    }

    class Client:
        unprotected_positions = staticmethod(OkxDemoClient.unprotected_positions)
        def safety_snapshot(self): return protected

    current, missing = confirm_unprotected_positions(Client(), stale)
    assert current is protected and missing == []


def test_real_missing_protection_requires_three_independent_snapshots():
    position = {"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "1",
                "closeOrderAlgo": []}
    stale = {"positions": [position], "orders": [], "algo_orders": []}

    class Client:
        calls = 0
        unprotected_positions = staticmethod(OkxDemoClient.unprotected_positions)
        def safety_snapshot(self):
            self.calls += 1
            return stale

    client = Client()
    _current, missing = confirm_unprotected_positions(client, stale)
    assert len(missing) == 1 and missing[0]["protection_deficit"] == 1
    assert client.calls == 2


def test_closed_layer_tagged_stop_is_removed_but_open_layer_stop_remains():
    open_rows = [{"trade_uid": "QBVALOPENL", "direction": 1, "stop_price": 2496.83}]
    closed_rows = [{"trade_uid": "QBVALCLOSEDL", "direction": 1, "stop_price": 2495.51}]
    snapshot = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": ".05",
                       "closeOrderAlgo": []}],
        "orders": [],
        "algo_orders": [
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "ordType": "conditional",
             "algoId": "KEEP", "algoClOrdId": "QBVALOPENLP", "slTriggerPx": "2496.83"},
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "ordType": "conditional",
             "algoId": "DROP", "algoClOrdId": "QBVALCLOSEDLP", "slTriggerPx": "2495.51"},
        ],
    }
    assert validation_execution.stale_layer_protective_stops(
        snapshot, open_rows, closed_rows, "ETH-USDT-SWAP") == [
            {"instId": "ETH-USDT-SWAP", "algoId": "DROP"}]


def test_legacy_duplicate_stops_are_reduced_to_open_lifecycle_count():
    open_rows = [{"trade_uid": "QBVALOPENL", "direction": 1, "stop_price": 2511.50}]
    closed_rows = [{"trade_uid": "QBVALCLOSEDL", "direction": 1, "stop_price": 2511.50}]
    snapshot = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": ".05",
                       "closeOrderAlgo": []}],
        "orders": [],
        "algo_orders": [
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "algoId": "LEGACY1",
             "slTriggerPx": "2511.50"},
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "algoId": "LEGACY2",
             "slTriggerPx": "2511.50"},
            {"instId": "ETH-USDT-SWAP", "posSide": "short", "algoId": "OTHER",
             "slTriggerPx": "2511.50"},
        ],
    }
    stale = validation_execution.stale_layer_protective_stops(
        snapshot, open_rows, closed_rows, "ETH-USDT-SWAP")
    assert stale == [{"instId": "ETH-USDT-SWAP", "algoId": "LEGACY2"}]


def test_real_position_size_caps_even_database_open_layer_protection():
    open_rows = [
        {"trade_uid": "QBVALOPEN1L", "direction": 1, "stop_price": 2500.0},
        {"trade_uid": "QBVALOPEN2L", "direction": 1, "stop_price": 2499.0},
    ]
    snapshot = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": ".05",
                       "closeOrderAlgo": []}],
        "orders": [],
        "algo_orders": [
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "algoId": "KEEP",
             "algoClOrdId": "QBVALOPEN1LP", "slTriggerPx": "2500", "sz": ".05"},
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "algoId": "EXCESS",
             "algoClOrdId": "QBVALOPEN2LP", "slTriggerPx": "2499", "sz": ".05"},
        ],
    }
    stale = validation_execution.stale_layer_protective_stops(
        snapshot, open_rows, [], "ETH-USDT-SWAP")
    assert stale == [{"instId": "ETH-USDT-SWAP", "algoId": "EXCESS"}]


def test_uncovered_second_layer_is_immediately_closed_reduce_only(tmp_path):
    database = tmp_path / "state.sqlite3"
    calls = []

    class Client:
        def close_demo_position_market(self, contracts, **kwargs):
            calls.append((contracts, kwargs))
            return {"code": "0", "data": [{"ordId": "EMERGENCY-CLOSE"}]}

    closed = emergency_close_unprotected_quantity(Client(), database, [{
        "instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "2",
        "markPx": "2406", "protection_covered": 1, "protection_deficit": 1,
    }])
    assert len(closed) == 1
    assert calls[0][0] == 1
    assert calls[0][1]["position_side"] == "short"
    assert calls[0][1]["enabled"] is True


def _small_cross_frame(direction: int, middle: bool = False):
    base = [100.5] * 4 + [100.0, 100.2, 100.4, 100.3, 100.1, 99.9, 99.7,
                            99.5, 99.3, 99.15, 99.05, 99.0, 99.05, 99.1,
                            99.2, 99.3, 99.45]
    if middle:
        base = [98.0, 102.0] + [100.0] * 9 + [99.9, 99.8, 99.7, 99.6, 99.5,
                                             99.55, 99.65, 99.8, 100.0, 100.2]
    if direction < 0:
        base = [200.0 - value for value in base]
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(base), freq="1min"),
        "open": [value - .08 * direction for value in base],
        "high": [value + .18 for value in base], "low": [value - .18 for value in base],
        "close": base, "volume": [100.0] * len(base),
    })


def test_one_minute_small_cross_accepts_only_matching_local_extreme():
    for direction, label in ((1, "局部低点小金叉"), (-1, "局部高点小死叉")):
        frame = _small_cross_frame(direction)
        actual, reason, stop = one_minute_local_extreme_ma5_ma10_cross_setup(frame)
        assert actual == direction and label in reason
        assert (stop < frame.iloc[-1]["close"] if direction > 0 else
                stop > frame.iloc[-1]["close"])


def test_one_minute_small_cross_rejects_middle_of_range():
    frame = _small_cross_frame(1, middle=True)
    direction, reason, stop = one_minute_local_extreme_ma5_ma10_cross_setup(frame)
    assert direction == 0 and stop == 0.0 and "中部" in reason


def test_five_minute_small_cross_uses_same_local_edge_rule_and_records_timeframe():
    frame = _small_cross_frame(1)
    direction, reason, stop = one_minute_local_extreme_ma5_ma10_cross_setup(
        frame, timeframe_label="五分钟")
    observed = ma5_ma10_cross_observation(frame, None, timeframe="5m")
    assert direction == 1 and stop > 0 and "五分钟局部低点小金叉" in reason
    assert observed and observed["timeframe"] == "5m" and observed["valid"]


def test_valid_trigger_is_not_vetoed_by_removed_38_percent_edge():
    frame = _small_cross_frame(1, middle=True)
    allowed, reason = local_extreme_cross_location_allows(frame, 1)
    assert allowed and "不再受38%位置门否决" in reason


def test_launch_stop_caps_wide_structure_at_three_points_both_directions():
    assert three_point_launch_stop(100.0, 1, 91.0)[:2] == (97.0, True)
    assert three_point_launch_stop(100.0, -1, 109.0)[:2] == (103.0, True)


def test_launch_stop_preserves_tighter_valid_structure():
    stop, replaced, reason = three_point_launch_stop(100.0, 1, 98.25)
    assert stop == 98.25 and not replaced and "继续使用" in reason


def test_launch_stop_accepts_one_and_one_point_five_risk_instead_of_forcing_three():
    assert three_point_launch_stop(100.0, 1, 99.0)[:2] == (99.0, False)
    assert three_point_launch_stop(100.0, 1, 98.5)[:2] == (98.5, False)
    assert three_point_launch_stop(100.0, -1, 101.0)[:2] == (101.0, False)
    assert three_point_launch_stop(100.0, -1, 101.5)[:2] == (101.5, False)


def test_launch_freeze_records_small_cross_without_treating_it_as_big_cross():
    frame = _small_cross_frame(1)
    observations = one_minute_launch_freeze_observations(frame)
    assert any(item["stage"] == "small_golden_cross" and item["direction"] == 1
               for item in observations)
    assert one_minute_big_cross_launch(frame)[0] == 0


def test_launch_freeze_top_reversal_is_exact_directional_mirror():
    long_frame = _small_cross_frame(1)
    short_frame = _small_cross_frame(-1)
    long_items = one_minute_launch_freeze_observations(long_frame)
    short_items = one_minute_launch_freeze_observations(short_frame)
    assert any(item["stage"] == "small_golden_cross" for item in long_items)
    assert any(item["stage"] == "small_death_cross" for item in short_items)
    assert all(item["direction"] == 1 for item in long_items)
    assert all(item["direction"] == -1 for item in short_items)


def test_half_body_cover_freezes_bottom_and_top_mirror_candidates():
    base = [100.0] * 22
    bottom = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=22, freq="1min"),
        "open": base, "high": [100.2] * 22, "low": [99.8] * 22,
        "close": base, "volume": [100.0] * 22,
    })
    bottom.loc[20, ["open", "high", "low", "close"]] = [101.0, 101.1, 99.8, 100.0]
    bottom.loc[21, ["open", "high", "low", "close"]] = [99.9, 100.7, 99.8, 100.6]
    top = bottom.copy()
    for column in ("open", "high", "low", "close"):
        top[column] = 200.0 - bottom[column]
    top[["high", "low"]] = top[["low", "high"]]
    bottom_items = one_minute_launch_freeze_observations(bottom)
    top_items = one_minute_launch_freeze_observations(top)
    assert any(item["stage"] == "bottom_half_bearish_cover" for item in bottom_items)
    assert any(item["stage"] == "top_half_bullish_cover" for item in top_items)
    assert any(item["stage"] == "price_above_flat_rising_ma5" for item in bottom_items)
    assert any(item["stage"] == "price_below_flat_falling_ma5" for item in top_items)


def test_relative_volume_top_is_stage_one_without_previous_candle_engulfment():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=22, freq="1min"),
        "open": [100.0] * 22, "high": [100.4] * 22,
        "low": [99.6] * 22, "close": [100.0] * 22,
        "volume": [100.0] * 22,
    })
    frame.loc[19, ["open", "high", "low", "close"]] = [100.2, 101.2, 100.0, 101.0]
    frame.loc[20, ["open", "high", "low", "close"]] = [101.0, 101.1, 100.7, 100.9]
    frame.loc[21, ["open", "high", "low", "close", "volume"]] = [100.9, 101.15, 100.6, 100.8, 130.0]
    stages = one_minute_launch_freeze_observations(frame)
    relative = [item for item in stages if item["stage"] == "relative_local_top_sweep"]
    assert relative and relative[0]["direction"] == -1
    assert relative[0]["stop"] > 101.2


def test_old_high_is_not_retimestamped_as_current_relative_top_on_bottom_rebound():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=22, freq="1min"),
        "open": [100.0] * 22, "high": [100.4] * 22,
        "low": [99.6] * 22, "close": [100.0] * 22,
        "volume": [100.0] * 22,
    })
    frame.loc[19, ["open", "high", "low", "close"]] = [100.5, 103.0, 100.4, 102.5]
    frame.loc[20, ["open", "high", "low", "close"]] = [102.5, 102.6, 99.4, 99.8]
    frame.loc[21, ["open", "high", "low", "close", "volume"]] = [99.8, 100.5, 99.2, 100.4, 150.0]
    stages = one_minute_launch_freeze_observations(frame)
    assert not any(item["stage"] == "relative_local_top_sweep" for item in stages)
    assert any(item["stage"] == "relative_local_bottom_sweep" for item in stages)


def test_fresh_ma5_turn_triggers_without_rigid_candle_cover():
    close = [100.0] * 17 + [100.0, 100.0, 99.8, 99.6, 100.2]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
        "volume": [100.0] * len(close),
    })
    frame.loc[frame.index[-1], "open"] = 100.1
    observations = one_minute_launch_freeze_observations(frame)
    assert not any(item["stage"] == "bottom_half_bearish_cover" for item in observations)
    assert any(item["stage"] == "price_above_flat_rising_ma5" for item in observations)


def test_launch_quality_rejects_late_chase_and_accepts_first_turn_without_38_percent_gate():
    close = [100.0] * 17 + [99.7, 99.6, 99.8, 100.0, 100.2]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close, "high": [value + .25 for value in close],
        "low": [value - .25 for value in close], "close": close,
        "volume": [100.0] * len(close),
    })
    allowed, reason = one_minute_launch_quality_gate(frame, 1, 99.6)
    assert allowed and "第一次有效转向" in reason
    late = frame.copy()
    late.loc[late.index[-4]:, "close"] = [100.4, 100.9, 101.5, 102.2]
    late["open"] = late["close"]
    late["high"] = late["close"] + .25
    late["low"] = late["close"] - .25
    allowed, reason = one_minute_launch_quality_gate(late, 1, 99.6)
    assert not allowed and ("冻结点" in reason or "MA5" in reason)


def test_missed_ma5_market_window_becomes_one_near_ma5_limit_both_directions():
    for direction in (1, -1):
        close = [100.0] * 21 + [100.0 + direction * 2.0]
        frame = pd.DataFrame({
            "date": pd.date_range("2026-09-04", periods=len(close), freq="1min"),
            "open": close,
            "high": [value + .6 for value in close],
            "low": [value - .6 for value in close],
            "close": close,
            "volume": [100.0] * len(close),
        })
        ma5 = float(frame["close"].rolling(5).mean().iloc[-1])
        stop = ma5 - direction * 1.0
        plan = validation_execution.missed_ma5_pullback_limit_plan(
            frame, direction, stop, "[MA5_CHASE]too far from MA5")
        assert plan.allowed, plan.reason
        assert direction * (float(frame.iloc[-1]["close"]) - plan.entry) > 0
        assert abs(plan.entry - ma5) < 1.0
        assert direction * (plan.entry - plan.stop) > 0
        assert direction * (plan.target - plan.entry) >= 4.0


def test_pullback_limit_is_not_created_for_unrelated_rejection():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=22, freq="1min"),
        "open": [100.0] * 22, "high": [100.6] * 22,
        "low": [99.4] * 22, "close": [100.0] * 21 + [102.0],
    })
    plan = validation_execution.missed_ma5_pullback_limit_plan(
        frame, 1, 99.0, "stale stage")
    assert not plan.allowed


def test_live_rejection_cancels_stale_ma5_pullback_fallback():
    close = [100.0] * 21 + [102.0]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-19 04:00", periods=len(close), freq="1min", tz="UTC"),
        "open": close, "high": [value + .6 for value in close],
        "low": [value - .6 for value in close], "close": close,
    })
    live = pd.DataFrame({
        "date": [pd.Timestamp("2026-09-19 04:22", tz="UTC")],
        "open": [102.4], "high": [102.5], "low": [99.9], "close": [100.1],
    })
    plan = validation_execution.missed_ma5_pullback_limit_plan(
        frame, 1, 99.0, "[MA5_CHASE]too far from MA5", one_minute_live=live)
    assert not plan.allowed
    assert "实时" in plan.reason


def test_late_top_reversal_retest_requires_fresh_signal_and_nearest_room():
    one = _trend_frame([100.0] * 21 + [98.0], "1min")
    five = _trend_frame([100.0] * 30, "5min")
    signal = "2026-09-18T04:21:00Z"
    timely = "2026-09-18T04:23:00Z"
    plan = validation_execution.late_top_reversal_pullback_limit_plan(
        one, five, signal, timely, 101.0)
    assert plan.allowed, plan.reason
    assert plan.stop > plan.entry > float(one.iloc[-1]["close"])
    assert not validation_execution.late_top_reversal_pullback_limit_plan(
        one, five, signal, "2026-09-18T04:25:00Z", 101.0).allowed
    blocked = five.copy()
    blocked.loc[blocked.index[-10:-1], "open"] = 99.0
    blocked.loc[blocked.index[-10:-1], "close"] = 99.0
    assert not validation_execution.late_top_reversal_pullback_limit_plan(
        one, blocked, signal, timely, 101.0).allowed


def test_late_top_ma5_retest_submits_once_with_frozen_stop(tmp_path):
    class Client:
        opening_unit_contracts = "1"

        def __init__(self):
            self.calls = []

        def place_demo_sniper_limit_order(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"code": "0", "data": [{"sCode": "0", "ordId": "ret-1"}]}

    store = StateStore(tmp_path / "state.sqlite3")
    client = Client()
    plan = validation_execution.MissedMa5PullbackPlan(
        True, -1, 100.0, 102.0, 95.0, "有效顶部反抽")
    kwargs = dict(store=store, client=client, snapshot={"orders": [], "positions": []},
                  instrument="ETH-USDT-SWAP", direction=-1,
                  stage_time="2026-09-18T04:21:00Z", plan=plan, atr=1.0,
                  stage="late_top_reversal_ma5_retest",
                  event_type="late_top_reversal_ma5_retest_submitted")
    first = validation_execution.submit_one_ma5_retest_limit(**kwargs)
    second = validation_execution.submit_one_ma5_retest_limit(**kwargs)
    assert first.action == "pullback_limit_submitted"
    assert second.action == "duplicate"
    assert len(client.calls) == 1
    assert client.calls[0][0][2:5] == ("100.00", "102.00", "95.00")
    assert store.connection.execute(
        "SELECT COUNT(*) FROM events WHERE event_type=?",
        ("late_top_reversal_ma5_retest_submitted",)).fetchone()[0] == 1


def test_only_owned_pullback_orders_expire_after_short_ttl():
    snapshot = {"orders": [
        {"clOrdId": "QBVALPB202609040101L", "cTime": "100000", "ordId": "1"},
        {"clOrdId": "QBVALPB202609040102S", "cTime": "190000", "ordId": "2"},
        {"clOrdId": "OTHER", "cTime": "100000", "ordId": "3"},
    ]}
    expired = validation_execution.expired_missed_ma5_pullback_orders(
        snapshot, now_ms=230001, ttl_seconds=120)
    assert [item["ordId"] for item in expired] == ["1"]


def _indicator_frame(direction=1, periods=80, *, volume=100.0):
    base = pd.Series([
        100.0 + direction * (index * .02 + .06 * ((index % 4) - 1.5))
        for index in range(periods)
    ])
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=periods, freq="1min"),
        "open": base - direction * .03, "high": base + .15,
        "low": base - .15, "close": base,
        "volume": [volume] * (periods - 1) + [volume * 1.2],
    })


def test_indicator_confirmation_accepts_aligned_continuation_and_records_metrics():
    one = _indicator_frame()
    five = _indicator_frame()
    fifteen = _indicator_frame()
    allowed, reason, metrics = indicator_confirmation_gate(
        one, five, fifteen, 1, entry_class="continuation")
    assert allowed and "6/9" in reason
    assert set(metrics) == {"1m", "5m", "15m"}
    assert metrics["1m"]["volume_ratio"] == 1.2
    assert metrics["1m"]["last_input_candle_time"] == "2026-01-01T01:19:00"
    assert metrics["1m"]["macd_zero_axis_region"] == "above"
    assert metrics["1m"]["macd_dif"] > metrics["1m"]["macd_dea"]


def test_indicator_confirmation_rejects_low_volume_and_extreme_chase():
    one = _indicator_frame()
    one.loc[one.index[-1], "volume"] = 10.0
    allowed, reason, _ = indicator_confirmation_gate(
        one, _indicator_frame(), _indicator_frame(), 1, entry_class="reversal")
    assert not allowed and "成交量" in reason
    extreme = _indicator_frame()
    extreme["close"] = pd.Series([100.0 + index * .08 for index in range(len(extreme))])
    extreme["open"] = extreme["close"] - .03
    extreme["high"] = extreme["close"] + .15
    extreme["low"] = extreme["close"] - .15
    allowed, reason, _ = indicator_confirmation_gate(
        extreme, _indicator_frame(), _indicator_frame(), 1, entry_class="reversal",
        minimum_volume_ratio=.1)
    assert not allowed and "RSI6" in reason


def test_indicator_confirmation_reversal_allows_early_turn_without_full_high_timeframe_alignment():
    one = _indicator_frame()
    five = _indicator_frame(direction=-1)
    fifteen = _indicator_frame()
    allowed, reason, metrics = indicator_confirmation_gate(
        one, five, fifteen, 1, entry_class="reversal", minimum_volume_ratio=.1)
    assert allowed and metrics["1m"]["votes"] >= 2


def test_confirmed_top_price_structure_can_override_lagging_trend_conflict(monkeypatch):
    contexts = iter((
        {"supertrend_direction": 1, "structure_bias": 1,
         "liquidity_sweep": "high_reject", "range_position": .89,
         "choch_direction": 0},
        {"supertrend_direction": 1, "structure_bias": 1,
         "liquidity_sweep": "none", "range_position": .67,
         "choch_direction": -1},
        {"supertrend_direction": 1, "structure_bias": 1,
         "liquidity_sweep": "none", "range_position": .48,
         "choch_direction": 0},
    ))
    monkeypatch.setattr(
        validation_execution, "exchange_market_context", lambda *_: next(contexts))
    rising = _indicator_frame(direction=1)
    allowed, reason, metrics = indicator_confirmation_gate(
        rising, rising, rising, -1, entry_class="reversal",
        minimum_volume_ratio=.1, price_structure_override=True,
    )
    assert allowed
    assert "price-structure reversal override" in reason
    assert metrics["1m"]["votes"] < 2


def test_price_structure_override_requires_extreme_rejection_and_aligned_five_choch(monkeypatch):
    contexts = iter((
        {"supertrend_direction": 1, "structure_bias": 1,
         "liquidity_sweep": "none", "range_position": .89,
         "choch_direction": 0},
        {"supertrend_direction": 1, "structure_bias": 1,
         "liquidity_sweep": "none", "range_position": .67,
         "choch_direction": -1},
        {"supertrend_direction": 1, "structure_bias": 1,
         "liquidity_sweep": "none", "range_position": .48,
         "choch_direction": 0},
    ))
    monkeypatch.setattr(
        validation_execution, "exchange_market_context", lambda *_: next(contexts))
    rising = _indicator_frame(direction=1)
    allowed, reason, _ = indicator_confirmation_gate(
        rising, rising, rising, -1, entry_class="reversal",
        minimum_volume_ratio=.1, price_structure_override=True,
    )
    assert not allowed
    assert "v0.7.83" in reason


def test_confirmed_local_pivot_can_freeze_without_absolute_sweep():
    close = [100.0] * 18 + [100.4, 100.1, 100.3, 100.5]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
        "volume": [100.0] * len(close),
    })
    frame.loc[20, "low"] = 99.7
    observations = one_minute_launch_freeze_observations(frame)
    assert any(item["stage"] == "confirmed_local_bottom" for item in observations)
    assert not any(item["stage"] == "price_above_flat_rising_ma5" for item in observations)


def test_fresh_big_cross_launch_is_mirrored_for_long_and_short():
    close = [100.0] * 20 + [99.5, 99.4, 99.6, 100.2, 101.5]
    long_frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
        "volume": [100.0] * len(close),
    })
    short_frame = long_frame.copy()
    for column in ("open", "high", "low", "close"):
        short_frame[column] = 200.0 - long_frame[column]
    short_frame[["high", "low"]] = short_frame[["low", "high"]]
    long_direction, long_reason, long_stop = one_minute_big_cross_launch(long_frame)
    short_direction, short_reason, short_stop = one_minute_big_cross_launch(short_frame)
    assert (long_direction, short_direction) == (1, -1)
    assert "大金叉" in long_reason and "大死叉" in short_reason
    assert long_stop < long_frame.iloc[-1]["close"]
    assert short_stop > short_frame.iloc[-1]["close"]


def test_any_matching_freeze_is_enough_and_latest_repeat_refreshes_reference():
    rows = [
        {"pattern_type": "one_minute_launch_freeze:top_sweep_reject", "direction": -1,
         "confirmed_bar_time": "2026-01-01T00:01:00Z", "entry_reference": 105.0},
        {"pattern_type": "one_minute_launch_freeze:small_death_cross", "direction": -1,
         "confirmed_bar_time": "2026-01-01T00:03:00Z", "entry_reference": 104.0},
        {"pattern_type": "one_minute_launch_freeze:top_sweep_reject", "direction": -1,
         "confirmed_bar_time": "2026-01-01T00:05:00Z", "entry_reference": 103.0},
        {"pattern_type": "one_minute_launch_freeze:small_golden_cross", "direction": 1,
         "confirmed_bar_time": "2026-01-01T00:06:00Z", "entry_reference": 102.0},
    ]
    candidates = matching_frozen_launch_candidates(rows, -1, "2026-01-01T00:08:00Z")
    assert len(candidates) == 3
    assert candidates[-1]["entry_reference"] == 103.0
    assert matching_frozen_launch_candidates(rows[:1], -1, "2026-01-01T00:08:00Z")


def test_two_local_bottom_events_keep_independent_stage_execution_chains():
    rows = [
        {"pattern_type": "one_minute_launch_freeze:confirmed_local_bottom",
         "direction": 1, "confirmed_bar_time": "2026-09-19T01:20:00Z",
         "entry_reference": 2604.0, "stop_reference": 2601.0},
        {"pattern_type": "one_minute_launch_freeze:price_reclaim_ma5",
         "direction": 1, "confirmed_bar_time": "2026-09-19T01:23:00Z",
         "entry_reference": 2608.0, "stop_reference": 2601.0},
        {"pattern_type": "one_minute_launch_freeze:confirmed_local_bottom",
         "direction": 1, "confirmed_bar_time": "2026-09-19T01:36:00Z",
         "entry_reference": 2605.0, "stop_reference": 2602.0},
    ]
    first = validation_execution.latest_reversal_anchor_for_stage(
        rows, [], 1, "2026-09-19T01:24:00Z")
    second = validation_execution.latest_reversal_anchor_for_stage(
        rows, [], 1, "2026-09-19T01:39:00Z")
    assert first["confirmed_bar_time"] == "2026-09-19T01:20:00Z"
    assert second["confirmed_bar_time"] == "2026-09-19T01:36:00Z"
    assert first["confirmed_bar_time"] != second["confirmed_bar_time"]


def test_latched_short_stage_two_survives_two_small_bullish_candles_and_resumes():
    rows = [
        {"pattern_type": "one_minute_launch_freeze:relative_local_top_sweep",
         "direction": -1, "confirmed_bar_time": "2026-01-01T02:28:00Z",
         "entry_reference": 105.0, "stop_reference": 106.0},
        {"pattern_type": "one_minute_launch_freeze:price_break_ma5",
         "direction": -1, "confirmed_bar_time": "2026-01-01T02:29:00Z",
         "entry_reference": 103.8, "stop_reference": 106.0},
    ]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01T02:29:00Z", periods=5, freq="1min"),
        "open": [104.2, 103.7, 103.9, 104.1, 103.9],
        "high": [104.4, 104.1, 104.3, 104.2, 104.0],
        "low": [103.6, 103.6, 103.8, 103.8, 103.1],
        "close": [103.8, 103.9, 104.1, 103.9, 103.2],
        "volume": [100.0] * 5,
    })
    candidate = latched_stage_two_resume_candidate(rows, frame)
    assert candidate is not None
    assert candidate["direction"] == -1
    assert candidate["stage"] == "latched_stage2_resume"
    assert candidate["stop"] == 106.0


def test_latched_stage_two_does_not_resume_while_counter_move_remains_above_ma5():
    rows = [
        {"pattern_type": "one_minute_launch_freeze:relative_local_top_sweep",
         "direction": -1, "confirmed_bar_time": "2026-01-01T02:28:00Z",
         "entry_reference": 105.0, "stop_reference": 106.0},
        {"pattern_type": "one_minute_launch_freeze:price_break_ma5",
         "direction": -1, "confirmed_bar_time": "2026-01-01T02:29:00Z",
         "entry_reference": 103.8, "stop_reference": 106.0},
    ]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01T02:29:00Z", periods=5, freq="1min"),
        "open": [103.8, 103.9, 104.0, 104.1, 104.2],
        "high": [104.0, 104.1, 104.2, 104.3, 104.5],
        "low": [103.6, 103.7, 103.8, 103.9, 104.0],
        "close": [103.9, 104.0, 104.1, 104.2, 104.4],
        "volume": [100.0] * 5,
    })
    assert latched_stage_two_resume_candidate(rows, frame) is None


def test_delayed_frozen_cross_ignores_latest_opposite_one_minute_arrow():
    allowed, reason = frozen_launch_direction_allowed(1, -1, False)
    assert allowed and "方向箭头不参与" in reason
    allowed, reason = frozen_launch_direction_allowed(-1, 1, False)
    assert allowed and "方向箭头不参与" in reason


def test_latest_opposite_local_structure_invalidates_old_frozen_cross_both_directions():
    for direction in (1, -1):
        allowed, reason = frozen_launch_direction_allowed(direction, direction, True)
        assert not allowed and "反向局部结构" in reason
        assert frozen_launch_direction_allowed(direction, direction, False)[0]


def test_ma5_ma20_frozen_launch_precedes_complete_big_cross_and_mirrors():
    close = [100.0] * 18 + [98.0, 98.0, 98.0, 99.0, 100.0, 101.0, 102.0]
    long_frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": close, "high": [value + .2 for value in close],
        "low": [value - .2 for value in close], "close": close,
        "volume": [100.0] * len(close),
    })
    short_frame = long_frame.copy()
    for column in ("open", "high", "low", "close"):
        short_frame[column] = 200.0 - long_frame[column]
    short_frame[["high", "low"]] = short_frame[["low", "high"]]
    assert one_minute_ma5_ma20_early_launch(long_frame)[0] == 1
    assert one_minute_ma5_ma20_early_launch(short_frame)[0] == -1
    assert one_minute_big_cross_launch(long_frame)[0] == 0
    assert one_minute_big_cross_launch(short_frame)[0] == 0


def test_bearish_cover_then_small_and_big_death_cross_confirms_continuation_short():
    one_close = [100.0] * 20 + [100.1, 100.0, 99.8, 99.6]
    five_close = [100.0] * 20 + [101.0, 99.8, 99.6, 98.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=24, freq="1min"),
        "open": [value + .1 for value in one_close],
        "high": [value + .3 for value in one_close],
        "low": [value - .3 for value in one_close],
        "close": one_close, "volume": [100.0] * 24,
    })
    five_open = [100.0] * 20 + [100.0, 101.2, 99.9, 99.7]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=24, freq="5min"),
        "open": five_open,
        "high": [max(o, c) + .3 for o, c in zip(five_open, five_close)],
        "low": [min(o, c) - .3 for o, c in zip(five_open, five_close)],
        "close": five_close, "volume": [100.0] * 24,
    })
    direction, reason, stop = multi_timeframe_weakness_continuation_short_setup(one, five)
    assert direction == -1
    assert "5分钟阴线覆盖阳线" in reason and "大死叉" in reason
    assert stop > one.iloc[-1]["close"]


def test_middle_death_cross_without_five_minute_cover_sequence_stays_observation_only():
    close = [100.0] * 20 + [100.1, 100.0, 99.8, 99.6]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=24, freq="1min"),
        "open": [value + .1 for value in close], "high": [value + .3 for value in close],
        "low": [value - .3 for value in close], "close": close, "volume": [100.0] * 24,
    })
    direction, reason, stop = multi_timeframe_weakness_continuation_short_setup(frame, frame)
    assert direction == 0 and stop == 0.0
    assert "等待5分钟" in reason


def test_dual_timeframe_ma20_death_cross_starts_short_near_junction():
    def frame(freq, mirror=False, end="2026-01-01 02:10:00"):
        close = [100.0] * 20 + [99.95, 99.90, 99.85, 99.80]
        if mirror:
            close = [200 - value for value in close]
        return pd.DataFrame({
            "date": pd.date_range(end=end, periods=len(close), freq=freq),
            "open": [value - .05 if mirror else value + .05 for value in close],
            "high": [value + .4 for value in close], "low": [value - .4 for value in close],
            "close": close, "volume": [100.0] * len(close),
        })
    one, five = frame("1min"), frame("5min")
    direction, reason, stop = dual_timeframe_ma20_cross_continuation_setup(
        one, pd.DataFrame(), five, pd.DataFrame())
    assert direction == -1 and "死叉追空" in reason and stop > float(one.iloc[-1]["close"])
    one_up, five_up = frame("1min", True), frame("5min", True)
    direction, reason, stop = dual_timeframe_ma20_cross_continuation_setup(
        one_up, pd.DataFrame(), five_up, pd.DataFrame())
    assert direction == 1 and "金叉追多" in reason and stop < float(one_up.iloc[-1]["close"])


def test_dual_timeframe_ma20_cross_rejects_actual_time_gap_over_fifteen_minutes():
    def frame(freq, end):
        close = [100.0] * 20 + [99.95, 99.90, 99.85, 99.80]
        return pd.DataFrame({
            "date": pd.date_range(end=end, periods=len(close), freq=freq),
            "open": [value + .05 for value in close],
            "high": [value + .4 for value in close], "low": [value - .4 for value in close],
            "close": close, "volume": [100.0] * len(close),
        })
    one = frame("1min", "2026-01-01 02:10:00")
    five = frame("5min", "2026-01-01 02:00:00")
    direction, reason, stop = dual_timeframe_ma20_cross_continuation_setup(
        one, pd.DataFrame(), five, pd.DataFrame())
    assert direction == 0 and stop == 0.0 and "15分钟" in reason


def test_retired_ma5_anchor_never_blocks_an_early_profit_exit():
    close = [100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 99]
    frame = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
                          "close": close})
    anchor = ma5_local_extreme_anchor(frame, -1)
    assert anchor > float(frame["close"].tail(5).mean())
    assert ma5_anchor_allows_exit(frame, -1, anchor)
    recovered = frame.copy()
    recovered.loc[recovered.index[-5:], "close"] = [105, 105, 105, 105, 105]
    assert ma5_anchor_allows_exit(recovered, -1, anchor)


def test_reversal_location_is_audit_only_after_38_percent_gate_removed():
    def frame(count, freq):
        close = [100 + (i % 4) for i in range(count)]
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=count, freq=freq),
            "open": close, "high": [v + .5 for v in close], "low": [v - .5 for v in close],
            "close": close, "volume": [100.0] * count,
        })
    one, five = frame(30, "1min"), frame(12, "5min")
    short_ok, short_reason = reversal_range_location_gate(one, five, 100.2, -1)
    long_ok, long_reason = reversal_range_location_gate(one, five, 102.8, 1)
    assert short_ok and "38%门槛已取消" in short_reason
    assert long_ok and "38%门槛已取消" in long_reason


def test_reversal_location_gate_allows_correct_range_sides():
    close = [100 + (i % 4) for i in range(30)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="1min"),
        "open": close, "high": [v + .5 for v in close], "low": [v - .5 for v in close],
        "close": close, "volume": [100.0] * 30,
    })
    assert reversal_range_location_gate(frame, frame, 103.2, -1)[0]
    assert reversal_range_location_gate(frame, frame, 99.8, 1)[0]


def test_one_minute_best_position_is_not_vetoed_by_remote_five_minute_range():
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=20, freq="1min"),
        "open": [100.0] * 20, "high": [101.0] * 19 + [104.0],
        "low": [99.0] * 20, "close": [100.0] * 19 + [103.5], "volume": 100,
    })
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=10, freq="5min"),
        "open": [100.0] * 10, "high": [120.0] * 10,
        "low": [90.0] * 10, "close": [100.0] * 10, "volume": 100,
    })
    allowed, reason = reversal_range_location_gate(one, five, 103.5, -1)
    assert allowed and "仅作审计" in reason


def test_waterfall_micro_pullback_short_uses_recent_one_minute_high():
    def falling(periods, freq, step):
        close = [120 - i * step for i in range(periods)]
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=periods, freq=freq),
            "open": [v + .08 for v in close], "high": [v + .25 for v in close],
            "low": [v - .20 for v in close], "close": close, "volume": [100.0] * periods,
        })
    one = falling(50, "1min", .10)
    five = falling(50, "5min", .25)
    fifteen = falling(50, "15min", .40)
    entry = float(one.iloc[-1]["close"])
    allowed, reason, stop = waterfall_micro_pullback_stop(one, five, fifteen, entry, -1)
    assert allowed
    assert "微型高点" in reason
    assert entry < stop < float(one.tail(5)["high"].max()) + 1.0


def test_waterfall_micro_pullback_rejects_mixed_timeframes():
    close = [100 + i * .1 for i in range(30)]
    rising = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="1min"),
        "open": close, "high": [v + .2 for v in close], "low": [v - .2 for v in close],
        "close": close, "volume": [100.0] * 30,
    })
    allowed, _, _ = waterfall_micro_pullback_stop(rising, rising, rising, close[-1], -1)
    assert not allowed


def test_waterfall_short_ignores_one_minute_profit_exit_while_five_ma5_falls():
    one_close = [110 - i * .08 for i in range(28)]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=28, freq="1min"),
        "open": [v + .8 for v in one_close], "high": [v + .9 for v in one_close],
        "low": [v - .2 for v in one_close], "close": one_close, "volume": [100.0] * 28,
    })
    five_close = [120 - i * .5 for i in range(30)]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [v + .1 for v in five_close], "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close, "volume": [100.0] * 30,
    })
    assert aggressive_short_ma5_exit_reason(one, 112.0, five) == ""


def test_long_pressure_runway_blocks_buy_immediately_below_resistance():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [100.0] * 30, "high": [101.0] * 30,
        "low": [99.0] * 30, "close": [100.5] * 30,
    })
    frame.loc[18:28, "open"] = 105
    frame.loc[18:28, "close"] = 105.5
    allowed, reason, resistance = long_pressure_runway(frame, 105.0, 2.0)
    assert not allowed
    assert resistance == 105.5
    assert "压力" in reason


def test_long_pressure_runway_allows_clear_room():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [100.0] * 30, "high": [101.0] * 30,
        "low": [99.0] * 30, "close": [100.5] * 30,
    })
    frame.loc[18:28, "open"] = 110
    frame.loc[18:28, "close"] = 110.5
    frame.loc[18:28, "high"] = 111
    frame.loc[18:28, "low"] = 109
    allowed, _, resistance = long_pressure_runway(frame, 105.0, 1.0)
    assert allowed
    assert resistance == 110.5


def signal(bar, direction, confirmed=False):
    return TimeframeSignal(bar, pd.Timestamp("2026-01-01"), direction, 100, 100, 99, .001, confirmed)


def test_validation_long_exit_prices_are_close_and_protective():
    take_profit, stop_loss = validation_exit_prices(2000, 1, .002, .0015)
    assert take_profit == 2004.0
    assert stop_loss == 1997.0


def test_validation_short_exit_prices_are_reversed():
    take_profit, stop_loss = validation_exit_prices(2000, -1, .002, .0015)
    assert take_profit == 1996.0
    assert stop_loss == 2003.0


def test_strategy01_short_stop_ignores_remote_fixed_window_wick():
    close = [100 + i * .02 for i in range(22)]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=22, freq="5min"),
        "high": [v + .4 for v in close], "low": [v - .3 for v in close], "close": close,
    })
    market.loc[19, "high"] = 102.0
    stop, distance_pct, atr_multiple = strategy01_dynamic_stop(market, 100.5, -1)
    assert 100.5 < stop < 102.0
    assert distance_pct > 0
    assert atr_multiple > 0


def test_strategy01_long_stop_mirrors_below_recent_lower_wicks():
    close = [100 - i * .02 for i in range(22)]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=22, freq="5min"),
        "high": [v + .3 for v in close], "low": [v - .4 for v in close], "close": close,
    })
    market.loc[20, "low"] = 98.0
    stop, _, _ = strategy01_dynamic_stop(market, 99.5, 1)
    assert stop < 98.0


def test_strategy01_standard_short_stop_uses_latest_1m_swing_not_old_5m_high():
    close = [105 - i * .12 for i in range(22)]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=22, freq="1min"),
        "high": [v + .25 for v in close], "low": [v - .25 for v in close], "close": close,
    })
    market.loc[2, "high"] = 120.0
    stop, distance_pct, atr_multiple = strategy01_local_stop(market, close[-1], -1)
    assert close[-1] < stop < 105.0
    assert distance_pct > 0
    assert atr_multiple > 0


def test_strategy01_position_size_shrinks_as_stop_widens():
    assert strategy01_contracts_for_stop(10, .003, .0015) == 5
    assert strategy01_contracts_for_stop(10, .006, .0015) == 2
    assert strategy01_contracts_for_stop(1, .003, .0015) == 1


def test_invalid_zero_stop_distance_rejects_candidate_without_exception():
    assert safe_strategy01_contracts_for_stop(1, 0.0, .0015) is None
    assert safe_strategy01_contracts_for_stop(1, .003, 0.0) is None


def test_higher_timeframes_define_trend_while_1m_pullback_waits():
    signals = {"15m": signal("15m", 1), "5m": signal("5m", 1), "1m": signal("1m", -1)}
    direction, reason = hierarchical_entry_direction(signals)
    assert direction == 0
    assert "一分钟仅作通用分支辅助" in reason


def test_direction_label_marks_falling_ma20_as_bearish_waiting_zone():
    from quantbot.validation_execution import timeframe_direction_label

    close = [100.0] * 22 + [99.0, 98.0, 97.0]
    market = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=len(close), freq="5min"),
                           "close": close})
    label = timeframe_direction_label("5m", signal("5m", 1), market)
    assert label == "5m反转观察区↘(MA20向下，短线信号仍向上)"


def test_direction_label_keeps_confirmed_downtrend_and_shows_ma20_slope():
    from quantbot.validation_execution import timeframe_direction_label

    close = [120 - i * .5 for i in range(30)]
    market = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=len(close), freq="5min"),
                           "close": close})
    label = timeframe_direction_label("5m", signal("5m", -1), market)
    assert label == "5m↓(MA20↓)"


def test_hour_direction_label_marks_live_lower_high_as_top_weakening():
    from quantbot.validation_execution import timeframe_direction_label

    close = [100 + i * .5 for i in range(30)]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1h"),
        "open": close, "high": [v + 1.0 for v in close], "low": [v - 1.0 for v in close], "close": close,
    })
    live = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-02 06:00")], "open": [114.0], "high": [114.4],
        "low": [112.0], "close": [112.5],
    })
    assert "顶部弱化" in timeframe_direction_label("1H", signal("1H", 1), market, live)


def test_aggressive_short_exits_when_falling_ma5_bends_up_in_profit():
    close = [110 - i * .2 for i in range(22)] + [105.4, 107.0]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .05 for v in close], "high": [v + .15 for v in close],
        "low": [v - .15 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    reason = aggressive_short_ma5_exit_reason(market, 110.0)
    assert "MA5" in reason and "主动止盈" in reason


def test_aggressive_short_waits_when_one_minute_bends_up_but_five_minute_ma5_keeps_falling():
    close = [110 - i * .2 for i in range(22)] + [105.4, 107.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .05 for v in close], "high": [v + .15 for v in close],
        "low": [v - .15 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    five_close = [120 - i * .3 for i in range(24)]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(five_close), freq="5min"),
        "open": five_close, "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close,
    })
    assert "快速行情MA5止盈" in aggressive_short_ma5_exit_reason(one, 110.0, five)


def test_aggressive_short_exits_after_both_one_and_five_minute_ma5_roll_over():
    close = [110 - i * .2 for i in range(22)] + [105.4, 107.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .05 for v in close], "high": [v + .15 for v in close],
        "low": [v - .15 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    five_close = [120 - i * .3 for i in range(21)] + [114.0, 115.5, 117.0]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(five_close), freq="5min"),
        "open": five_close, "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close,
    })
    reason = aggressive_short_ma5_exit_reason(one, 110.0, five)
    assert "快速行情MA5止盈" in reason and "下降转为走平或向上拐弯" in reason


def test_aggressive_short_does_not_exit_on_first_five_minute_ma5_flattening():
    one_close = [110 - i * .2 for i in range(22)] + [105.4, 107.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(one_close), freq="1min"),
        "open": [v + .05 for v in one_close], "high": [v + .15 for v in one_close],
        "low": [v - .15 for v in one_close], "close": one_close, "volume": [100.0] * len(one_close),
    })
    five_close = [120 - i * .3 for i in range(22)] + [113.9, 114.2]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(five_close), freq="5min"),
        "open": five_close, "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close,
    })

    assert "快速行情MA5止盈" in aggressive_short_ma5_exit_reason(one, 110.0, five)


def test_aggressive_short_uses_fifteen_minute_ma5_after_trend_takeover():
    one_close = [110 - i * .2 for i in range(22)] + [105.4, 107.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(one_close), freq="1min"),
        "open": [v + .05 for v in one_close], "high": [v + .15 for v in one_close],
        "low": [v - .15 for v in one_close], "close": one_close, "volume": [100.0] * len(one_close),
    })
    five_close = [120 - i * .3 for i in range(21)] + [114.0, 115.5, 117.0]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(five_close), freq="5min"),
        "open": five_close, "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close,
    })
    fifteen_close = [130 - i * .5 for i in range(12)]
    fifteen = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(fifteen_close), freq="15min"),
        "open": fifteen_close, "high": [v + .2 for v in fifteen_close],
        "low": [v - .2 for v in fifteen_close], "close": fifteen_close,
    })
    assert "快速行情MA5止盈" in aggressive_short_ma5_exit_reason(one, 110.0, five, fifteen)


def test_aggressive_short_exits_when_fifteen_minute_ma5_bends_after_takeover():
    one_close = [110 - i * .2 for i in range(24)]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(one_close), freq="1min"),
        "open": [v + .05 for v in one_close], "high": [v + .15 for v in one_close],
        "low": [v - .15 for v in one_close], "close": one_close, "volume": [100.0] * len(one_close),
    })
    fifteen_close = [130 - i * .5 for i in range(11)] + [130.0]
    fifteen = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(fifteen_close), freq="15min"),
        "open": fifteen_close, "high": [v + .2 for v in fifteen_close],
        "low": [v - .2 for v in fifteen_close], "close": fifteen_close,
    })
    reason = aggressive_short_ma5_exit_reason(one, 110.0, fifteen_minute=fifteen)
    assert "十五分钟趋势止盈" in reason and "MA5" in reason
    entry_after_latest_fifteen = fifteen.iloc[-1]["date"] + pd.Timedelta(minutes=1)
    assert aggressive_short_ma5_exit_reason(
        one, 110.0, fifteen_minute=fifteen,
        entry_time=entry_after_latest_fifteen) == ""


def test_fifteen_minute_big_death_cross_upgrades_short_exit_to_one_hour_ma5():
    one_close = [110 - i * .2 for i in range(22)] + [105.4, 107.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(one_close), freq="1min"),
        "open": [v + .05 for v in one_close], "high": [v + .15 for v in one_close],
        "low": [v - .15 for v in one_close], "close": one_close, "volume": [100.0] * len(one_close),
    })
    fifteen_close = [140.0] * 10 + [140 - i * 1.0 for i in range(14)]
    fifteen = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(fifteen_close), freq="15min"),
        "open": fifteen_close, "high": [v + .2 for v in fifteen_close],
        "low": [v - .2 for v in fifteen_close], "close": fifteen_close,
    })
    hour_close = [160 - i * .5 for i in range(24)]
    hour_frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(hour_close), freq="1h"),
        "open": hour_close, "high": [v + .2 for v in hour_close],
        "low": [v - .2 for v in hour_close], "close": hour_close,
    })
    assert "快速行情MA5止盈" in aggressive_short_ma5_exit_reason(
        one, 110.0, fifteen_minute=fifteen, one_hour=hour_frame)


def test_short_exits_when_one_hour_ma5_bends_after_fifteen_minute_big_death_cross():
    one_close = [110 - i * .2 for i in range(24)]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(one_close), freq="1min"),
        "open": [v + .05 for v in one_close], "high": [v + .15 for v in one_close],
        "low": [v - .15 for v in one_close], "close": one_close, "volume": [100.0] * len(one_close),
    })
    fifteen_close = [140.0] * 10 + [140 - i * 1.0 for i in range(14)]
    fifteen = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(fifteen_close), freq="15min"),
        "open": fifteen_close, "high": [v + .2 for v in fifteen_close],
        "low": [v - .2 for v in fifteen_close], "close": fifteen_close,
    })
    hour_close = [160 - i * .5 for i in range(23)] + [160.0]
    hour_frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(hour_close), freq="1h"),
        "open": hour_close, "high": [v + .2 for v in hour_close],
        "low": [v - .2 for v in hour_close], "close": hour_close,
    })
    reason = aggressive_short_ma5_exit_reason(
        one, 110.0, fifteen_minute=fifteen, one_hour=hour_frame)
    assert "一小时趋势止盈" in reason and "一小时MA5" in reason


def test_aggressive_long_exits_when_rising_ma5_bends_down_in_profit():
    close = [100 + i * .2 for i in range(22)] + [104.6, 103.0]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v - .05 for v in close], "high": [v + .15 for v in close],
        "low": [v - .15 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    reason = aggressive_long_ma5_exit_reason(market, 100.0)
    assert "多单主动止盈" in reason and "上升转为走平或向下" in reason


def test_aggressive_long_waits_when_one_minute_bends_down_but_five_minute_ma5_keeps_rising():
    close = [100 + i * .2 for i in range(22)] + [104.6, 103.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v - .05 for v in close], "high": [v + .15 for v in close],
        "low": [v - .15 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    five_close = [100 + i * .3 for i in range(24)]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(five_close), freq="5min"),
        "open": five_close, "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close,
    })
    assert "快速行情MA5止盈" in aggressive_long_ma5_exit_reason(one, 100.0, five)


def test_aggressive_long_exits_after_both_one_and_five_minute_ma5_roll_over():
    close = [100 + i * .2 for i in range(22)] + [104.6, 103.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v - .05 for v in close], "high": [v + .15 for v in close],
        "low": [v - .15 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    five_close = [100 + i * .3 for i in range(21)] + [106.0, 104.5, 103.0]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(five_close), freq="5min"),
        "open": five_close, "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close,
    })
    reason = aggressive_long_ma5_exit_reason(one, 100.0, five)
    assert "快速行情MA5止盈" in reason and "上升转为走平或向下拐弯" in reason


def test_aggressive_short_exits_on_volume_climax_long_bear():
    close = [110 - i * .08 for i in range(23)] + [104.0]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .05 for v in close], "high": [v + .15 for v in close],
        "low": [v - .15 for v in close], "close": close, "volume": [100.0] * 23 + [300.0],
    })
    market.loc[market.index[-1], ["open", "high", "low"]] = [108.0, 108.1, 103.8]
    reason = aggressive_short_ma5_exit_reason(market, 110.0)
    assert "异常放量长阴" in reason and "紧急止盈" in reason


def test_aggressive_short_exits_below_second_consecutive_long_bear():
    close = [110.0 - i * .03 for i in range(22)] + [108.8, 107.6]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .04 for v in close], "high": [v + .10 for v in close],
        "low": [v - .10 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    market.loc[market.index[-2], ["open", "high", "low", "close"]] = [109.9, 110.0, 108.7, 108.8]
    market.loc[market.index[-1], ["open", "high", "low", "close"]] = [108.8, 108.9, 107.5, 107.6]
    reason = aggressive_short_ma5_exit_reason(market, 110.0)
    assert "连续两根长阴快速止盈" in reason and "第二根长阴下方" in reason


def test_aggressive_short_catches_ma5_turn_after_one_missed_cycle():
    close = [110 - i * .15 for i in range(20)] + [107.0, 106.8, 106.7, 107.0, 107.5]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .03 for v in close], "high": [v + .12 for v in close],
        "low": [v - .12 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    reason = aggressive_short_ma5_exit_reason(market, 110.0)
    assert "MA5已由下降转为走平或向上拐弯" in reason


def test_aggressive_short_exits_on_deep_lower_wick_exhaustion():
    close = [110 - i * .08 for i in range(24)]
    close[-12:] = [105.2, 105.4, 105.6, 105.8, 106.0, 106.2, 106.4, 106.6, 106.8, 107.0, 107.2, 107.4]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .04 for v in close], "high": [v + .12 for v in close],
        "low": [v - .12 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    market.loc[market.index[-1], ["open", "high", "low", "close"]] = [107.1, 107.5, 104.5, 107.4]
    reason = aggressive_short_ma5_exit_reason(market, 110.0)
    assert "长下影衰竭" in reason and "紧急止盈" in reason


def test_aggressive_short_does_not_take_tiny_wick_profit_while_ma5_still_falling():
    close = [110 - i * .08 for i in range(24)]
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="1min"),
        "open": [v + .04 for v in close], "high": [v + .12 for v in close],
        "low": [v - .12 for v in close], "close": close, "volume": [100.0] * len(close),
    })
    market.loc[market.index[-1], ["open", "high", "low", "close"]] = [108.25, 108.35, 106.5, 108.0]
    assert aggressive_short_ma5_exit_reason(market, 110.0) == ""


def test_hierarchical_entry_marks_1m_as_auxiliary_only():
    signals = {"15m": signal("15m", 1), "5m": signal("5m", 1), "1m": signal("1m", 1, True)}
    direction, reason = hierarchical_entry_direction(signals)
    assert direction == 1
    assert "一分钟仅提供辅助证据" in reason


def test_old_six_timeframe_base_position_no_longer_bypasses_five_minute_trigger():
    signals = {
        bar: signal(bar, -1, False)
        for bar in ("4H", "1H", "30m", "15m", "5m", "1m")
    }
    direction, reason = hierarchical_entry_direction(signals)
    assert direction == 0
    assert "五分钟收盘或盘中独立触发" in reason
    assert "一分钟仅作通用分支辅助" in reason


def test_hierarchical_entry_uses_5m_when_15m_arrow_conflicts():
    signals = {"15m": signal("15m", 1), "5m": signal("5m", -1), "1m": signal("1m", 1, True)}
    direction, reason = hierarchical_entry_direction(signals)
    assert direction == -1
    assert "一分钟仅提供辅助证据" in reason
    assert "仍须通过五分钟主触发" in reason


def test_downtrend_one_minute_ma20_rejection_creates_structural_short():
    five_close = [120 - i * .3 for i in range(40)]
    one_close = [100 - i * .03 for i in range(94)] + [97.5, 97.7, 97.8, 97.6, 97.4, 97.2]
    def frame(close, freq):
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=len(close), freq=freq),
            "open": [v + .05 for v in close], "high": [v + .15 for v in close],
            "low": [v - .15 for v in close], "close": close,
        })
    markets = {"5m": frame(five_close, "5min"), "1m": frame(one_close, "1min")}
    signals = {"15m": signal("15m", -1), "5m": signal("5m", -1), "1m": signal("1m", -1)}
    direction, reason, stop = ma20_pullback_short_setup(signals, markets)
    assert direction == -1
    assert "MA20" in reason
    assert stop > markets["1m"].tail(6)["high"].max()


def test_uptrend_one_minute_ma20_rebound_creates_structural_long():
    five_close = [100 + i * .3 for i in range(40)]
    one_close = [100 + i * .03 for i in range(94)] + [103.0, 102.8, 102.7, 102.9, 103.1, 103.6]
    def frame(close, freq):
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=len(close), freq=freq),
            "open": [v - .05 for v in close], "high": [v + .15 for v in close],
            "low": [v - .15 for v in close], "close": close,
        })
    markets = {"5m": frame(five_close, "5min"), "1m": frame(one_close, "1min")}
    markets["1m"].loc[98, "high"] = 103.25
    markets["1m"].loc[97, "low"] = 101.50
    markets["1m"].loc[99, "open"] = 103.10
    markets["1m"].loc[99, "high"] = 103.70
    markets["1m"].loc[99, "low"] = 103.10
    signals = {"15m": signal("15m", 1), "5m": signal("5m", 1), "1m": signal("1m", 1)}
    direction, reason, stop = ma20_pullback_long_setup(signals, markets)
    assert direction == 1
    assert "MA20" in reason
    assert stop < markets["1m"].tail(6)["low"].min()


def test_five_minute_volume_breakout_then_one_minute_reclaim_triggers_long():
    five_close = [100 + i * .12 for i in range(30)]
    five_close[-2:] = [103.10, 103.20]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [v - .2 for v in five_close], "high": [v + 1 for v in five_close],
        "low": [v - 1 for v in five_close], "close": five_close, "volume": [100] * 28 + [300, 100],
    })
    five.loc[28, "open"] = 101.7
    five.loc[28, "high"] = 104.2
    # The prior eight-bar highest body top is 103.24.  The breakout candle
    # closes below it at 103.10, but its upper wick reaches 104.20.  After that
    # confirmed 5m candle, 1m retests the body level and reclaims it.
    confirmation = five.iloc[-2]["date"] + pd.Timedelta(minutes=5)
    one = pd.DataFrame({
        "date": pd.date_range(confirmation, periods=4, freq="min"),
        "open": [103.5, 103.3, 103.2, 103.3], "high": [103.7, 103.5, 103.45, 103.8],
        "low": [103.2, 103.18, 103.15, 103.25], "close": [103.3, 103.25, 103.3, 103.7],
        "volume": [10, 10, 10, 20],
    })
    fifteen_close = [100 + i * .25 for i in range(30)]
    fifteen = pd.DataFrame({
        "date": pd.date_range("2025-12-31", periods=30, freq="15min"),
        "open": fifteen_close, "high": [v + 1 for v in fifteen_close],
        "low": [v - 1 for v in fifteen_close], "close": fifteen_close, "volume": 100,
    })
    signals = {"15m": signal("15m", 1), "5m": signal("5m", 1), "1m": signal("1m", 1, True)}
    direction, reason, stop = breakout_pullback_long_setup(signals, {"1m": one, "5m": five, "15m": fifteen})
    assert direction == 1
    assert "放量突破" in reason
    assert stop < 103.15


def test_breakout_long_rejects_price_more_than_two_atr_above_ma20():
    five_close = [100 + i * .05 for i in range(30)]
    five_close[-2:] = [110.0, 110.2]
    five = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [v - .7 for v in five_close], "high": [v + .2 for v in five_close],
        "low": [v - .2 for v in five_close], "close": five_close, "volume": [100] * 28 + [300, 100],
    })
    confirmation = five.iloc[-2]["date"] + pd.Timedelta(minutes=5)
    one = pd.DataFrame({"date": pd.date_range(confirmation, periods=3, freq="min"),
                        "open": [101.5, 101.3, 109.5], "high": [101.8, 101.6, 110.2],
                        "low": [101.4, 101.4, 109.4], "close": [101.5, 101.55, 110.1], "volume": 10})
    fifteen_close = [100 + i * .2 for i in range(30)]
    fifteen = pd.DataFrame({"date": pd.date_range("2025-12-31", periods=30, freq="15min"),
                            "open": fifteen_close, "high": [v + 1 for v in fifteen_close],
                            "low": [v - 1 for v in fifteen_close], "close": fifteen_close, "volume": 100})
    signals = {"15m": signal("15m", 1), "5m": signal("5m", 1), "1m": signal("1m", 1, True)}
    direction, reason, _ = breakout_pullback_long_setup(signals, {"1m": one, "5m": five, "15m": fifteen})
    assert direction == 0
    assert "2 ATR" in reason


def test_breakout_long_uses_retest_stop_one_r_activation_and_atr_callback():
    one = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=15, freq="min"),
        "high": [101.0] * 15, "low": [99.0] * 15, "close": [100.0] * 15,
    })
    stop, activation, callback = breakout_long_protection(110.0, 107.0, one)
    assert stop == 107.0
    assert activation == 113.0  # 1R, instead of the old 1.5R at 114.50
    assert callback == 1.0       # max(0.05% of entry, half the 1m ATR)


def test_five_minute_average_trend_keeps_long_while_ma5_and_colour_rise():
    close = [100 + index * .4 for index in range(12)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-02", periods=len(close), freq="5min"),
        "open": [value - .2 for value in close],
        "high": [value + .3 for value in close],
        "low": [value - .4 for value in close],
        "close": close, "volume": [100.0] * len(close),
    })
    assert five_minute_ma5_trend_holds(frame, 1)
    assert not five_minute_ma5_trend_holds(frame, -1)


def test_five_minute_average_trend_keeps_short_while_ma5_and_colour_fall():
    close = [110 - index * .4 for index in range(12)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-02", periods=len(close), freq="5min"),
        "open": [value + .2 for value in close],
        "high": [value + .4 for value in close],
        "low": [value - .3 for value in close],
        "close": close, "volume": [100.0] * len(close),
    })
    assert five_minute_ma5_trend_holds(frame, -1)
    assert not five_minute_ma5_trend_holds(frame, 1)


def test_fast_long_profit_exit_overrides_still_rising_five_minute_ma5():
    close = [100.0] * 18 + [101.0, 103.0, 105.0, 104.0, 102.0, 101.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-05", periods=len(close), freq="min", tz="UTC"),
        "open": close, "high": [value + .3 for value in close],
        "low": [value - .3 for value in close], "close": close, "volume": 100.0,
    })
    five_close = [98.0 + index * .5 for index in range(12)]
    five = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=12, freq="5min", tz="UTC"),
        "open": [value - .2 for value in five_close],
        "high": [value + .3 for value in five_close],
        "low": [value - .3 for value in five_close], "close": five_close, "volume": 100.0,
    })
    reason = aggressive_long_ma5_exit_reason(one, 100.0, five, entry_time=one.iloc[17]["date"])
    assert "快速行情MA5止盈" in reason
    assert aggressive_long_ma5_exit_reason(
        one, 100.0, five, entry_time=one.iloc[17]["date"],
        prefer_five_minute_hold=True) == ""


def test_fast_short_profit_exit_is_mirror_of_long():
    close = [110.0] * 18 + [109.0, 107.0, 105.0, 106.0, 108.0, 109.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-05", periods=len(close), freq="min", tz="UTC"),
        "open": close, "high": [value + .3 for value in close],
        "low": [value - .3 for value in close], "close": close, "volume": 100.0,
    })
    five_close = [112.0 - index * .5 for index in range(12)]
    five = pd.DataFrame({
        "date": pd.date_range("2026-09-04", periods=12, freq="5min", tz="UTC"),
        "open": [value + .2 for value in five_close],
        "high": [value + .3 for value in five_close],
        "low": [value - .3 for value in five_close], "close": five_close, "volume": 100.0,
    })
    reason = aggressive_short_ma5_exit_reason(one, 110.0, five, entry_time=one.iloc[17]["date"])
    assert "快速行情MA5止盈" in reason
    assert aggressive_short_ma5_exit_reason(
        one, 110.0, five, entry_time=one.iloc[17]["date"],
        prefer_five_minute_hold=True) == ""


def test_fast_ma5_exit_does_not_close_a_losing_long():
    close = [100.0] * 18 + [101.0, 103.0, 105.0, 104.0, 99.0, 98.0]
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-05", periods=len(close), freq="min", tz="UTC"),
        "open": close, "high": [value + .3 for value in close],
        "low": [value - .3 for value in close], "close": close, "volume": 100.0,
    })
    assert aggressive_long_ma5_exit_reason(one, 100.0, entry_time=one.iloc[17]["date"]) == ""


def test_fresh_dual_bottom_zones_veto_old_short_chain():
    rows = [
        {"pattern_type": "price_reversal_zone:5m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T18:20:00+00:00",
         "features_json": '{"five_minute_half_cover":true,"five_minute_ma20_reversal_confirmed":true}'},
        {"pattern_type": "price_reversal_zone:1m", "direction": 1,
         "confirmed_bar_time": "2026-09-04T18:26:00+00:00", "features_json": "{}"},
        {"pattern_type": "one_minute_launch_freeze:confirmed_local_top", "direction": -1,
         "confirmed_bar_time": "2026-09-04T18:28:00+00:00"},
    ]
    direction, reason = validation_execution.fresh_dual_reversal_zone_bias(
        rows, "2026-09-04T18:28:00+00:00")
    assert direction == 1
    assert "旧反向冻结链立即失效" in reason


def test_locked_top_allows_death_cross_stage_three_ma5_side_recovery_only():
    assert validation_execution.locked_stage_three_bypasses_ma5_chase(
        "small_death_cross", -1, -1, "[MA5_CHASE] temporary MA5 side mismatch")
    assert not validation_execution.locked_stage_three_bypasses_ma5_chase(
        "small_death_cross", -1, 1, "[MA5_CHASE] temporary MA5 side mismatch")
    assert not validation_execution.locked_stage_three_bypasses_ma5_chase(
        "price_break_ma5", -1, -1, "[MA5_CHASE] temporary MA5 side mismatch")
    assert not validation_execution.locked_stage_three_bypasses_ma5_chase(
        "small_death_cross", -1, -1, "[LATE_LAUNCH] stale")


def test_legacy_heikin_zones_are_audit_only_and_cannot_set_execution_bias():
    rows = [
        {"pattern_type": "heikin_reversal_zone:1m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T01:00:00Z",
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
        {"pattern_type": "heikin_reversal_zone:5m", "direction": -1,
         "confirmed_bar_time": "2026-09-06T01:05:00Z",
         "features_json": '{"market_shape_code":"true_top_reversal"}'},
    ]
    fresh, _ = validation_execution.fresh_dual_reversal_zone_bias(
        rows, "2026-09-06T01:10:00Z")
    durable, _ = validation_execution.durable_dual_reversal_zone_bias(
        rows, "2026-09-06T01:10:00Z")
    assert fresh == 0
    assert durable == 0


def test_early_pullback_cover_long_triggers_before_ma5_cross():
    one_close = [100.0] * 20 + [99.2, 98.6, 98.2, 99.0]
    one_open = one_close.copy()
    one_open[-4:] = [100.0, 99.2, 98.6, 98.2]
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-05", periods=24, freq="min", tz="UTC"),
        "open": one_open, "close": one_close,
        "high": [max(o, c) + .2 for o, c in zip(one_open, one_close)],
        "low": [min(o, c) - .2 for o, c in zip(one_open, one_close)], "volume": 100.0,
    })
    five_close = [100.0] * 20 + [99.5, 99.0, 98.8, 99.1]
    five = pd.DataFrame({
        "date": pd.date_range("2026-09-05", periods=24, freq="5min", tz="UTC"),
        "open": five_close, "close": five_close,
        "high": [v + .2 for v in five_close], "low": [v - .4 for v in five_close],
        "volume": 100.0,
    })
    allowed, reason, stop = validation_execution.early_dual_timeframe_pullback_cover_long_setup(one, five)
    assert allowed
    assert "上穿MA5前" in reason
    assert stop < min(one.tail(4)["low"])

def test_local_reversal_rejects_remote_six_point_stop():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-09", periods=4, freq="1min"),
        "open": [100.0, 99.5, 99.0, 99.2],
        "high": [100.2, 99.8, 99.4, 100.1],
        "low": [94.0, 98.8, 98.7, 99.0],
        "close": [99.5, 99.0, 99.2, 100.0],
    })
    allowed, stop, reason = reversal_stop_outside_recent_structure(
        frame, 100.0, 1, 100.0, lookback=4, maximum_points=3.0)
    assert allowed
    assert stop == 98.98


def test_stage_one_top_short_waits_for_actual_one_and_five_minute_weakness():
    close = [110.0 - index * .05 for index in range(25)]
    open_ = close.copy()
    open_[-1] = close[-1] + .35
    one = pd.DataFrame({
        "date": pd.date_range("2026-09-19T02:09:00Z", periods=25, freq="min"),
        "open": open_, "close": close,
        "high": [max(o, c) + .2 for o, c in zip(open_, close)],
        "low": [min(o, c) - .2 for o, c in zip(open_, close)],
        "volume": 100.0,
    })
    observations = [{
        "direction": -1, "stage": "confirmed_local_top",
        "time": "2026-09-19T02:32:30Z",
    }]
    allowed, _, _ = validation_execution.first_stage_top_short_candidate(
        observations, one, fifteen_direction=-1, hour_direction=-1,
        five_direction=-1, opposite_bottom_active=False)
    assert allowed

    allowed, reason, _ = validation_execution.first_stage_top_short_candidate(
        observations, one, fifteen_direction=-1, hour_direction=-1,
        five_direction=1, opposite_bottom_active=False)
    assert not allowed
    assert "5分钟" in reason

    allowed, reason, _ = validation_execution.first_stage_top_short_candidate(
        observations, one, fifteen_direction=-1, hour_direction=-1,
        five_direction=-1, opposite_bottom_active=True)
    assert not allowed
    assert "底部半覆盖" in reason

    rising = one.copy()
    rising.loc[rising.index[-5]:, "close"] = [105.0, 105.2, 105.4, 105.6, 105.5]
    rising.loc[rising.index[-1], "open"] = 105.8
    allowed, reason, _ = validation_execution.first_stage_top_short_candidate(
        observations, rising, fifteen_direction=-1, hour_direction=-1,
        five_direction=-1, opposite_bottom_active=False)
    assert not allowed
    assert "MA5" in reason



def test_three_stage_five_minute_ma5_takeover_requires_two_closed_ma20_holds():
    dates = pd.date_range("2026-09-19T06:00:00Z", periods=22, freq="5min")
    waiting = pd.DataFrame({"date": dates, "close": [100.0] * 20 + [101.0, 99.0]})
    confirmed = pd.DataFrame({"date": dates, "close": [100.0] * 20 + [101.0, 102.0]})

    assert not five_minute_ma20_takeover_confirmed(waiting, 1)
    assert five_minute_ma20_takeover_confirmed(confirmed, 1)
    assert not five_minute_ma20_takeover_confirmed(confirmed, -1)
