import pandas as pd
import pytest
import inspect
from pathlib import Path
from types import SimpleNamespace

from quantbot.range_pivot import action_for_position, confirmed_structure_extremes, confirmed_wick_pivots, execution_data_gate, exhaustion_top_short_setup, five_minute_ma_deviation, five_minute_reversal_zone_allows, high_sweep_reject_short_setup, local_post_impulse_range, long_short_order_mapping, low_sweep_reclaim_long_setup, ma_cross_direction, one_minute_sweep_wick, range_activity_allows, range_structure_stop, reversal_safety_filter, shifted_pivots, staged_range_edge_trigger
from quantbot.range_execution import range_protection_from_actual_entry, rounded_exit_prices
from quantbot.range_execution import execute_range_pivot_tick


@pytest.fixture(autouse=True)
def _isolate_range_execution_from_shared_sniper(monkeypatch):
    monkeypatch.setattr(
        "quantbot.range_execution.execute_structure_sniper_tick",
        lambda *_args, **_kwargs: SimpleNamespace(action="observe", reason=""),
    )


def test_local_post_impulse_range_excludes_remote_spike_origin():
    closes = [100.0] * 20 + [80.0, 81.0, 82.0, 81.5, 82.5]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-20", periods=len(closes), freq="5min"),
        "open": [100.0] * 20 + [100.0, 80.5, 81.0, 82.0, 81.5],
        "high": [101.0] * 20 + [101.0, 82.0, 83.0, 83.0, 83.5],
        "low": [99.0] * 20 + [78.0, 79.5, 80.5, 80.0, 81.0],
        "close": closes, "volume": [100.0] * len(closes),
    })
    ok, resistance, support, _ = local_post_impulse_range(frame, 3.0)
    assert ok
    assert support == 78.0
    assert resistance == 83.5


def test_staged_bottom_triggers_ma5_then_ma20_pullback_without_redefining_support():
    closes = [100.0] * 20 + [99.2, 99.0, 99.1, 99.3, 99.8]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-20", periods=len(closes), freq="min"),
        "open": [value - .05 for value in closes],
        "high": [value + .10 for value in closes],
        "low": [value - .10 for value in closes],
        "close": closes, "volume": [100.0] * len(closes),
    })
    frame.loc[frame.index[-1], "open"] = 99.60
    direction, reason = staged_range_edge_trigger(frame, 98.9, 103.0, .30)
    assert direction == 1
    assert "MA5" in reason or "MA20" in reason


def test_staged_top_uses_mirrored_ma5_confirmation():
    closes = [100.0] * 20 + [100.8, 101.0, 100.9, 100.7, 100.2]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-20", periods=len(closes), freq="min"),
        "open": [value + .05 for value in closes],
        "high": [value + .10 for value in closes],
        "low": [value - .10 for value in closes],
        "close": closes, "volume": [100.0] * len(closes),
    })
    frame.loc[frame.index[-1], "open"] = 100.40
    direction, reason = staged_range_edge_trigger(frame, 97.0, 101.1, .30)
    assert direction == -1
    assert "MA5" in reason or "MA20" in reason


def test_staged_top_first_bearish_close_through_ma20_is_parallel_short_trigger():
    closes = [100.0] * 20 + [102.0, 102.0, 101.5, 100.5, 99.5]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-21", periods=len(closes), freq="min"),
        "open": [value for value in closes],
        "high": [value + .10 for value in closes],
        "low": [value - .10 for value in closes],
        "close": closes,
        "volume": [100.0] * len(closes),
    })
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [100.3, 100.4, 99.4, 99.5]
    direction, reason = staged_range_edge_trigger(frame, 96.5, 102.1, .50)
    assert direction == -1
    assert "首次收盘跌破MA20" in reason


def test_staged_top_ma5_cross_has_priority_when_same_candle_also_breaks_ma20():
    closes = [100.0] * 20 + [101.0, 101.2, 101.1, 101.0, 99.5]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-21", periods=len(closes), freq="min"),
        "open": closes,
        "high": [value + .10 for value in closes],
        "low": [value - .10 for value in closes],
        "close": closes,
        "volume": [100.0] * len(closes),
    })
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [101.0, 101.1, 99.4, 99.5]
    direction, reason = staged_range_edge_trigger(frame, 97.0, 101.2, .50)
    assert direction == -1
    assert "第一优先" in reason and "MA5" in reason


def test_short_structure_stop_is_above_upper_wick_and_long_is_below_lower_wick():
    assert range_structure_stop(-1, 90, 110, 89, 112, 2, .75) == pytest.approx(112.5)
    assert range_structure_stop(1, 90, 110, 88, 111, 2, .75) == pytest.approx(87.5)


def test_extreme_top_short_trailing_can_activate_at_one_r():
    stop, activation, callback = range_protection_from_actual_entry(
        110.0, -1, 112.0, 1.0, allow_one_r=True)
    assert stop == "112.00"
    assert activation == "109.00"
    assert callback == "0.06"


def test_low_sweep_reclaim_enters_before_slow_ma_cross():
    frame = market(30)
    frame.loc[:, ["open", "high", "low", "close", "volume"]] = [100.0, 100.4, 99.6, 100.0, 10.0]
    # A capitulation sweep followed by the first closed bullish reclaim.  The
    # slow moving averages have not had time to turn yet.
    frame.loc[28, ["open", "high", "low", "close", "volume"]] = [99.8, 100.0, 97.0, 97.8, 40.0]
    frame.loc[29, ["open", "high", "low", "close", "volume"]] = [97.8, 99.5, 97.6, 99.4, 24.0]
    triggered, reason, stop = low_sweep_reclaim_long_setup(frame, support=97.2, five_atr=2.0)
    assert triggered
    assert "提前做多" in reason
    assert stop < 97.0


def test_low_sweep_without_closed_bullish_reclaim_stays_candidate_only():
    frame = market(30)
    frame.loc[:, ["open", "high", "low", "close", "volume"]] = [100.0, 100.4, 99.6, 100.0, 10.0]
    frame.loc[28, ["open", "high", "low", "close", "volume"]] = [99.8, 100.0, 97.0, 97.8, 40.0]
    frame.loc[29, ["open", "high", "low", "close", "volume"]] = [97.8, 98.2, 97.2, 97.5, 20.0]
    triggered, reason, stop = low_sweep_reclaim_long_setup(frame, support=97.2, five_atr=2.0)
    assert not triggered
    assert "等待" in reason
    assert stop == 0.0


def test_high_sweep_reject_enters_before_slow_ma_cross():
    frame = market(30)
    frame.loc[:, ["open", "high", "low", "close", "volume"]] = [100.0, 100.4, 99.6, 100.0, 10.0]
    frame.loc[28, ["open", "high", "low", "close", "volume"]] = [100.2, 103.0, 100.0, 102.2, 40.0]
    frame.loc[29, ["open", "high", "low", "close", "volume"]] = [102.2, 102.4, 100.5, 100.6, 24.0]
    triggered, reason, stop = high_sweep_reject_short_setup(frame, resistance=102.8, five_atr=2.0)
    assert triggered
    assert "提前做空" in reason
    assert stop > 103.0


def test_high_sweep_without_closed_bearish_rejection_stays_candidate_only():
    frame = market(30)
    frame.loc[:, ["open", "high", "low", "close", "volume"]] = [100.0, 100.4, 99.6, 100.0, 10.0]
    frame.loc[28, ["open", "high", "low", "close", "volume"]] = [100.2, 103.0, 100.0, 102.2, 40.0]
    frame.loc[29, ["open", "high", "low", "close", "volume"]] = [102.2, 102.8, 102.0, 102.5, 20.0]
    triggered, reason, stop = high_sweep_reject_short_setup(frame, resistance=102.8, five_atr=2.0)
    assert not triggered
    assert "等待" in reason
    assert stop == 0.0
from quantbot.range_pivot import RangePivotSignal
from quantbot.config import load_config
from quantbot.okx import OkxCredentials
from quantbot.state import RangePivotIntent, StateStore


def market(n=80):
    close = [100 + i * .1 for i in range(n)]
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="min"),
        "symbol": ["ETH-USDT-SWAP"] * n,
        "open": close, "high": [x + 1 for x in close], "low": [x - 1 for x in close],
        "close": close, "volume": [1] * n,
    })


def test_strategy02_stop_structure_uses_highest_and_lowest_wick():
    frame = market(12)
    frame.loc[3, "high"] = 120.0
    frame.loc[6, "low"] = 80.0
    high, low = confirmed_structure_extremes(frame, 8)
    assert high == 120.0
    assert low == 80.0


def test_strategy02_rejects_tiny_sideways_candles():
    frame = market(12)
    frame.loc[:, "open"] = 100.0
    frame.loc[:, "high"] = 100.08
    frame.loc[:, "low"] = 99.92
    frame.loc[:, "close"] = 100.02
    allowed, reason = range_activity_allows(frame, 8, 1.0)
    assert not allowed
    assert "横盘" in reason


def test_strategy02_accepts_large_impulse_and_wick_structure():
    frame = market(12)
    frame.loc[:, ["open", "high", "low", "close"]] = [100.0, 100.4, 99.6, 100.1]
    frame.loc[5, ["open", "high", "low", "close"]] = [100.0, 103.0, 99.0, 102.0]
    allowed, _ = range_activity_allows(frame, 8, 1.0)
    assert allowed


def test_strategy02_rejects_narrow_five_minute_reversal_zone_even_in_frequency_mode():
    allowed, reason = five_minute_reversal_zone_allows(1880.19, 1879.46, .44)
    assert not allowed
    assert "横盘震荡" in reason


def test_strategy02_accepts_meaningful_five_minute_reversal_zone():
    allowed, reason = five_minute_reversal_zone_allows(1885.0, 1878.0, 1.5)
    assert allowed
    assert "高低区有效" in reason


def test_five_minute_ma_deviation_uses_closed_ma5_ma10_ma20_band():
    frame = market(30)
    frame.loc[:, ["open", "high", "low", "close"]] = [100.0, 100.5, 99.5, 100.0]
    far_high, far_low, high_distance, low_distance = five_minute_ma_deviation(
        frame, resistance=104.0, support=96.0, current_atr=2.0)
    assert far_high and far_low
    assert high_distance == 2.0 and low_distance == 2.0


def test_five_minute_ma_deviation_does_not_trigger_near_ma_band():
    frame = market(30)
    frame.loc[:, ["open", "high", "low", "close"]] = [100.0, 100.5, 99.5, 100.0]
    far_high, far_low, _, _ = five_minute_ma_deviation(
        frame, resistance=101.0, support=99.0, current_atr=2.0)
    assert not far_high and not far_low


def test_one_minute_long_wick_sweep_closes_back_inside_five_minute_zone():
    high_sweep, low_sweep = one_minute_sweep_wick(
        pd.Series({"open": 103.5, "high": 105.0, "low": 102.8, "close": 103.2}),
        resistance=104.0, support=96.0, one_minute_atr=1.0)
    assert high_sweep and not low_sweep


def test_one_minute_small_touch_is_not_a_sweep_wick():
    high_sweep, low_sweep = one_minute_sweep_wick(
        pd.Series({"open": 103.9, "high": 104.1, "low": 103.7, "close": 103.8}),
        resistance=104.0, support=96.0, one_minute_atr=1.0)
    assert not high_sweep and not low_sweep


def test_extreme_ma20_distance_and_one_minute_breakdown_confirms_top_short():
    five = market(30).iloc[::5].copy().reset_index(drop=True)
    five = market(30)
    five["date"] = pd.date_range("2026-01-01", periods=30, freq="5min")
    five.loc[:, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
    five.loc[29, ["open", "high", "low", "close"]] = [108.0, 112.0, 99.0, 100.0]
    fifteen = five.copy()
    fifteen["date"] = pd.date_range("2026-01-01", periods=30, freq="15min")
    one = market(30)
    one.loc[26:29, ["open", "high", "low", "close"]] = [108.0, 109.0, 107.0, 108.0]
    one.loc[28, ["open", "high", "low", "close"]] = [110.0, 112.0, 109.0, 110.0]
    one.loc[29, ["open", "high", "low", "close"]] = [110.0, 110.2, 103.0, 103.2]
    confirmed, reason, stop = exhaustion_top_short_setup(five, one, fifteen)
    assert confirmed
    assert "摸顶做空" in reason
    assert stop > 112.0


def test_high_distance_without_one_minute_breakdown_does_not_guess_the_top():
    five = market(30)
    five["date"] = pd.date_range("2026-01-01", periods=30, freq="5min")
    five.loc[:, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
    five.loc[29, ["open", "high", "low", "close"]] = [108.0, 112.0, 99.0, 100.0]
    one = market(30)
    fifteen = five.copy()
    confirmed, reason, _ = exhaustion_top_short_setup(five, one, fifteen)
    assert not confirmed
    assert "1分钟" in reason


@pytest.mark.parametrize("lookback", [5, 8, 10])
def test_shifted_pivots_exclude_current_candle_for_all_supported_lookbacks(lookback):
    frame = market(20)
    frame.loc[19, "high"] = 999
    frame.loc[19, "low"] = 1
    highs, lows = shifted_pivots(frame, lookback)
    assert highs.iloc[-1] == frame.high.iloc[-lookback-1:-1].max()
    assert lows.iloc[-1] == frame.low.iloc[-lookback-1:-1].min()
    assert highs.iloc[-1] != 999
    assert lows.iloc[-1] != 1


def test_confirmed_wick_pivots_use_longest_5m_wicks_and_exclude_current():
    frame = market(12)
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[:, "high"] = 101.0
    frame.loc[:, "low"] = 99.0
    frame.loc[7, "high"] = 110.0  # longest confirmed upper wick
    frame.loc[9, "low"] = 88.0    # longest confirmed lower wick
    frame.loc[11, "high"] = 999.0 # current candle must be excluded
    frame.loc[11, "low"] = 1.0
    resistance, support = confirmed_wick_pivots(frame, 8)
    assert resistance == 110.0
    assert support == 88.0


def test_confirmed_wick_pivots_prefer_latest_candle_when_wicks_tie():
    frame = market(8)
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[:, "high"] = 101.0
    frame.loc[:, "low"] = 99.0
    frame.loc[2, "high"] = frame.loc[5, "high"] = 105.0
    frame.loc[2, "low"] = frame.loc[5, "low"] = 95.0
    assert confirmed_wick_pivots(frame, 5) == (105.0, 95.0)


def test_high_reversal_closes_long_before_opening_short():
    assert action_for_position(-1, long_contracts=1) == (
        "close_long", "先平多；确认多仓和相关委托归零后才能开空")
    assert action_for_position(-1, long_contracts=0) == ("open_short", "高点反转条件成立")


def test_low_reversal_closes_short_before_opening_long():
    assert action_for_position(1, short_contracts=1)[0] == "close_short"
    assert action_for_position(1, short_contracts=0)[0] == "open_long"


def test_ma_cross_uses_only_confirmed_rows():
    frame = market(11)
    frame["close"] = [10, 10, 10, 10, 10, 9, 9, 9, 9, 9, 15]
    assert ma_cross_direction(frame, 2, 5) == 1


def test_range_intent_is_idempotent_and_reversal_state_survives_restart(tmp_path):
    db = tmp_path / "state.sqlite3"
    intent = RangePivotIntent("ETH-USDT-SWAP", "strategy_02", "range_pivot_reversal_v1", "1m",
                              "2026-01-01T00:00:00", "close_long", -1, 110, 2, 112, 100, "abc")
    store = StateStore(db)
    assert store.record_range_pivot_intent(intent)
    assert not store.record_range_pivot_intent(intent)
    store.save_reversal_state(intent.instrument, intent.strategy_version, "open_short", intent.confirmed_bar_time)
    store.close()
    reopened = StateStore(db)
    assert reopened.load_reversal_state(intent.instrument, intent.strategy_version) == ("open_short", intent.confirmed_bar_time)
    reopened.close()


def test_strategy02_branch_statistics_never_mix_extreme_and_continuation(tmp_path):
    store = StateStore(tmp_path / "branch-stats.sqlite3")
    for branch, pnl in (("relative_extreme_reversal", 3.0), ("trend_continuation", -1.0)):
        store.open_trade_lifecycle(
            trade_uid=branch, strategy_id="strategy_02", strategy_version="range_pivot_reversal_v10",
            instrument="ETH-USDT-SWAP", direction=1, signal_time="2026-08-13T00:00:00+00:00",
            signal_reason="test", signal_context={"branch": branch}, order_id=branch, algo_id="",
            entry_reference=100, stop_price=99, trailing_activation=102, trailing_callback=.1,
            branch=branch,
        )
        store.close_trade_lifecycle(branch, close_price=101, gross_pnl=pnl, total_fees=-.1,
                                    exit_reason="test")
    stats = {row["branch"]: row for row in store.strategy_branch_statistics("strategy_02")}
    assert stats["relative_extreme_reversal"]["trades"] == 1
    assert stats["relative_extreme_reversal"]["net_pnl"] == 2.9
    assert stats["trend_continuation"]["trades"] == 1
    assert stats["trend_continuation"]["net_pnl"] == -1.1
    store.close()


def test_strong_trend_blocks_countertrend_reversal():
    direction, reason = reversal_safety_filter(-1, trend_direction=1, adx_value=20, max_adx=22,
                                                reward_risk=2, minimum_reward_risk=1.5,
                                                expected_profit_pct=.01, round_trip_cost_pct=.001)
    assert direction == 0 and "强上涨" in reason


def test_high_adx_blocks_range_reversal():
    direction, reason = reversal_safety_filter(1, trend_direction=0, adx_value=30, max_adx=22,
                                                reward_risk=2, minimum_reward_risk=1.5,
                                                expected_profit_pct=.01, round_trip_cost_pct=.001)
    assert direction == 0 and "ADX" in reason


def test_cost_filter_blocks_insufficient_edge_after_rebate_and_slippage():
    direction, reason = reversal_safety_filter(1, trend_direction=0, adx_value=10, max_adx=22,
                                                reward_risk=2, minimum_reward_risk=1.5,
                                                expected_profit_pct=.001, round_trip_cost_pct=.0006,
                                                cost_multiple=2)
    assert direction == 0 and "交易成本" in reason


@pytest.mark.parametrize("kwargs", [
    {"network_ok": False, "market_fresh": True, "positions_known": True, "orders_known": True},
    {"network_ok": True, "market_fresh": False, "positions_known": True, "orders_known": True},
    {"network_ok": True, "market_fresh": True, "positions_known": False, "orders_known": True},
    {"network_ok": True, "market_fresh": True, "positions_known": True, "orders_known": False},
])
def test_query_or_market_failure_safely_rejects_new_entry(kwargs):
    assert execution_data_gate(**kwargs)[0] is False


def test_long_short_mode_order_mapping_is_explicit_and_reduce_only_on_closes():
    assert long_short_order_mapping("open_long") == {"side": "buy", "posSide": "long", "reduceOnly": False}
    assert long_short_order_mapping("close_long") == {"side": "sell", "posSide": "long", "reduceOnly": True}
    assert long_short_order_mapping("open_short") == {"side": "sell", "posSide": "short", "reduceOnly": False}
    assert long_short_order_mapping("close_short") == {"side": "buy", "posSide": "short", "reduceOnly": True}


def test_range_long_exit_prices_are_directionally_safe_and_rounded():
    assert rounded_exit_prices(100, 1, 98.123, 101.234) == ("98.12", "101.24", "0.05")


def test_range_short_exit_prices_are_directionally_safe_and_rounded():
    assert rounded_exit_prices(100, -1, 101.231, 98.127) == ("101.24", "98.12", "0.05")


def test_range_rejects_stale_protection_geometry():
    with pytest.raises(ValueError):
        rounded_exit_prices(100, 1, 101, 102)


def test_range_take_profit_activation_is_at_least_one_point_five_r():
    stop, activation, callback = range_protection_from_actual_entry(100, 1, 98, 1.5)
    assert stop == "98.00"
    assert activation == "103.00"
    assert callback == "0.05"


def test_range_demo_execution_submits_open_and_server_trailing_protection(monkeypatch, tmp_path):
    from types import SimpleNamespace
    signal_value = RangePivotSignal(
        1, "open_long", "test low reversal", pd.Timestamp("2026-01-01T00:10:00"),
        115, 95, 95, 2, 10, "震荡", True, 98, 103, 104, 100, 2, "fingerprint",
    )
    captured = {}
    # Keep this execution-path test deterministic and Demo/offline: range_execution
    # normally asks the public history endpoint for 1m/5m/15m context.
    monkeypatch.setattr("quantbot.range_execution.okx_history_market", lambda *_args, **_kwargs: market())

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def require_swap_trading_mode(self): return {}
        def safety_snapshot(self): return {"positions": [], "orders": [], "algo_orders": []}
        def orphaned_owned_trailing_orders(self, snapshot, prefix): return []
        def unprotected_positions(self, snapshot): return []
        def _request(self, method, path, *args, **kwargs):
            assert path == "/api/v5/market/ticker"
            return {"data": [{"last": "100"}]}
        def place_demo_market_order(self, *args, **kwargs):
            captured["open"] = (args, kwargs)
            return {"data": [{"ordId": "open-1"}]}
        def place_demo_trailing_order(self, *args, **kwargs):
            captured["trailing"] = (args, kwargs)
            return {"data": [{"algoId": "trail-1"}]}

    monkeypatch.setattr("quantbot.range_execution.observe_range_pivot_once", lambda cfg: signal_value)
    monkeypatch.setattr("quantbot.range_execution.OkxDemoClient", FakeClient)
    monkeypatch.setattr("quantbot.range_execution.spike_circuit_breaker",
                        lambda *_: SimpleNamespace(blocked=False, reason=""))
    # This test verifies Demo submission and server-side protection wiring.
    # Profit-space boundary behavior is covered separately; keeping it here
    # would make the test depend on randomly generated ATR fixture values.
    monkeypatch.setattr("quantbot.range_execution.tradeable_profit_space",
                        lambda *args, **kwargs: (True, "test profit space", 0.0))
    cfg = load_config(Path("configs/range-pivot.toml"))
    result = execute_range_pivot_tick(cfg, OkxCredentials("k", "s", "p"), tmp_path / "state.sqlite3")
    assert result.action == "submitted", result.reason
    assert result.order_id == "open-1" and result.algo_id == ""
    assert captured["open"][1]["position_side"] == "long"
    assert captured["open"][1]["stop_loss_trigger_type"] == "mark"
    assert captured["open"][1]["take_profit_trigger_type"] == "last"
    assert captured["open"][1]["take_profit_price"] is None
    assert "trailing" not in captured


def test_ready_top_short_coexists_with_protected_long_and_cancels_resting_support_line(monkeypatch, tmp_path):
    signal_value = RangePivotSignal(
        -1, "open_short", "top MA5 break", pd.Timestamp("2026-01-01T00:10:00"),
        110, 90, 110, 2, 10, "range", True, 112, 104, 104, 108, 2, "parallel-short",
    )
    captured = {"cancelled": [], "submitted": False}

    class FakeClient:
        def __init__(self, *args, **kwargs): self.pending = True
        def require_swap_trading_mode(self): return {}
        def safety_snapshot(self):
            orders = ([{"ordId": "support-long", "clOrdId": "QBRSNP202608210020L"}]
                      if self.pending else [])
            return {
                "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "1"}],
                "orders": orders,
                "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "algoId": "long-sl"}],
            }
        def orphaned_owned_trailing_orders(self, snapshot, prefix): return []
        def unprotected_positions(self, snapshot): return []
        def cancel_orders(self, orders):
            captured["cancelled"].extend(item["ordId"] for item in orders)
            self.pending = False
        def _request(self, method, path, *args, **kwargs): return {"data": [{"last": "108"}]}
        def place_demo_market_order(self, *args, **kwargs):
            captured["submitted"] = kwargs["position_side"] == "short"
            return {"data": [{"ordId": "short-1"}]}

    monkeypatch.setattr("quantbot.range_execution.observe_range_pivot_once", lambda cfg: signal_value)
    monkeypatch.setattr("quantbot.range_execution.OkxDemoClient", FakeClient)
    monkeypatch.setattr("quantbot.range_execution.okx_history_market", lambda *_args, **_kwargs: market())
    monkeypatch.setattr(
        "quantbot.range_execution.execute_structure_sniper_tick",
        lambda *_args, **_kwargs: SimpleNamespace(action="armed", reason="keep lines", order_ids=("support-long",)),
    )
    monkeypatch.setattr("quantbot.range_execution.tradeable_profit_space",
                        lambda *args, **kwargs: (True, "ok", 0.0))
    cfg = load_config(Path("configs/range-pivot.toml"))
    result = execute_range_pivot_tick(cfg, OkxCredentials("k", "s", "p"), tmp_path / "state.sqlite3")
    assert result.action == "submitted", result.reason
    assert captured == {"cancelled": ["support-long"], "submitted": True}


def test_strategy02_execution_has_no_channel_position_hard_gate():
    source = inspect.getsource(execute_range_pivot_tick)
    assert "entry_zone_allows" not in source
    assert "latest_location" not in source
    assert "禁止中部追单" not in source
    assert "低位禁止做空" not in source


def test_range_orphaned_trailing_is_cancelled_before_observation(monkeypatch, tmp_path):
    signal_value = RangePivotSignal(
        0, "observe", "no signal", pd.Timestamp("2026-01-01T00:10:00"),
        110, 90, 0, 2, 10, "震荡", False, 0, 0, 0, 0, 0, "fingerprint-orphan",
    )
    captured = {"snapshots": 0, "cancelled": []}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def require_swap_trading_mode(self): return {}
        def safety_snapshot(self):
            captured["snapshots"] += 1
            if captured["snapshots"] == 1:
                return {"positions": [], "orders": [], "algo_orders": [{
                    "instId": "ETH-USDT-SWAP", "posSide": "long", "ordType": "move_order_stop",
                    "algoClOrdId": "QBRG202608112200LT", "algoId": "trail-orphan",
                }]}
            return {"positions": [], "orders": [], "algo_orders": []}
        def orphaned_owned_trailing_orders(self, snapshot, prefix):
            return [item for item in snapshot["algo_orders"] if item["algoClOrdId"].startswith(prefix)]
        def cancel_algo_orders(self, orders): captured["cancelled"].extend(orders)
        def unprotected_positions(self, snapshot): return []

    monkeypatch.setattr("quantbot.range_execution.observe_range_pivot_once", lambda cfg: signal_value)
    monkeypatch.setattr("quantbot.range_execution.OkxDemoClient", FakeClient)
    market = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=100, freq="min"),
        "open": [100.] * 100, "close": [100.] * 100,
        "high": [101.] * 100, "low": [99.] * 100, "volume": [100.] * 100,
    })
    monkeypatch.setattr("quantbot.range_execution.okx_history_market", lambda *a, **k: market.copy())
    cfg = load_config(Path("configs/range-pivot.toml"))
    result = execute_range_pivot_tick(cfg, OkxCredentials("k", "s", "p"), tmp_path / "state.sqlite3")
    assert result.action == "observe"
    assert captured["snapshots"] == 2
    assert [item["algoId"] for item in captured["cancelled"]] == ["trail-orphan"]
