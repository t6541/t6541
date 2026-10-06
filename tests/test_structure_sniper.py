import pandas as pd
import pytest
from datetime import datetime, timezone

from types import SimpleNamespace

from quantbot.state import StateStore
from quantbot.structure_sniper import (_active_risk_exit_reason, _candidate_waiting_layers, _confirmed_pivots,
                                       _apply_final_sniper_gates,
                                       _completed_bars_after_fill, _disaster_stop,
                                       _post_fill_decision,
                                       _ma20_breakout_first_pullback_limit_layer,
                                       _one_minute_stopping_limit_layer,
                                       _pressure_zone_advance_short_layer,
                                       SniperLevel, SniperPlan, build_structure_sniper_plan, execute_structure_sniper_tick,
                                       staged_entry_phase)


def _frame(rows=120):
    index = pd.date_range("2026-08-15", periods=rows, freq="min", tz="UTC")
    close = pd.Series([100 + ((i % 12) - 6) * .08 for i in range(rows)], index=index)
    frame = pd.DataFrame({"open": close, "high": close + .25, "low": close - .25, "close": close})
    frame["date"] = index
    frame.iloc[-20, frame.columns.get_loc("high")] = 104
    frame.iloc[-12, frame.columns.get_loc("low")] = 96
    return frame


def _pressure_short_frames():
    fifteen_dates = pd.date_range("2026-08-17", periods=13, freq="15min", tz="UTC")
    fifteen_close = pd.Series([101, 102, 103, 104, 105, 106, 107, 108, 107, 106, 107, 106, 104])
    fifteen = pd.DataFrame({
        "date": fifteen_dates, "open": fifteen_close - .2,
        "high": fifteen_close + 1.5, "low": fifteen_close - 1.0,
        "close": fifteen_close, "volume": 100.0,
    })
    fifteen.loc[7, "high"] = 111.0

    five_dates = pd.date_range("2026-08-17 02:00", periods=25, freq="5min", tz="UTC")
    five_close = pd.Series([104 + i * .12 for i in range(19)] + [106.5, 107.0, 107.5, 106.8, 105.0, 104.5])
    five = pd.DataFrame({
        "date": five_dates, "open": five_close + .15,
        "high": five_close + .8, "low": five_close - .8,
        "close": five_close, "volume": 100.0,
    })
    five.loc[21, "high"] = 109.0

    one_dates = pd.date_range("2026-08-17 03:00", periods=32, freq="min", tz="UTC")
    one_close = pd.Series([105.0 + (i % 3) * .05 for i in range(25)] + [105.2, 105.1, 105.0, 103.2, 103.0, 102.8, 102.7])
    one = pd.DataFrame({
        "date": one_dates, "open": one_close + .05,
        "high": one_close + .35, "low": one_close - .35,
        "close": one_close, "volume": 100.0,
    })
    one.loc[22, "low"] = 99.0
    one.loc[28, "open"] = 105.3
    return fifteen, five, one


def test_shared_pressure_short_uses_real_body_zone_and_preserves_long_side():
    fifteen, five, one = _pressure_short_frames()
    existing_long = SniperLevel(1, 100.0, 99.0, 102.0, 2.0, "existing")
    base = SniperPlan(False, "base", "5m", long=existing_long, long_levels=(existing_long,))
    plan = _pressure_zone_advance_short_layer(base, fifteen, five, one)
    assert plan.allowed
    assert plan.short.direction == -1
    assert plan.short.entry < float(fifteen.iloc[:-1]["high"].max())
    assert plan.short.stop > plan.short.entry > plan.short.take_profit
    assert plan.short.reward_risk >= 1.5
    assert plan.long == existing_long


def test_structure_sniper_builds_confirmed_two_sided_plan():
    frame = _frame()
    plan = build_structure_sniper_plan(frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    assert plan.allowed
    assert plan.short.direction == -1 and plan.long.direction == 1
    assert plan.short.stop > plan.short.entry > plan.short.take_profit
    assert plan.long.stop < plan.long.entry < plan.long.take_profit
    assert plan.short.reward_risk >= 1.5 and plan.long.reward_risk >= 1.5


def test_final_sniper_gate_rejects_overlay_with_noise_sized_protection():
    narrow_long = SniperLevel(1, 100.0, 99.9, 101.0, 10.0, "narrow")
    plan = SniperPlan(True, "overlay", "1m", long=narrow_long,
                      long_levels=(narrow_long,))
    gated = _apply_final_sniper_gates(
        plan, atr_1m=.5, adx_15m=20, ma20_slope_atr=.02, regime_direction=0)
    assert not gated.allowed
    assert "保护/利润空间不足" in gated.reason


def test_final_sniper_gate_keeps_both_protected_lines_in_strong_trend():
    short = SniperLevel(-1, 105.0, 105.4, 101.0, 10.0, "short")
    long = SniperLevel(1, 95.0, 94.6, 99.0, 10.0, "long")
    plan = SniperPlan(True, "two-sided", "5m", short, long, (short,), (long,))
    gated = _apply_final_sniper_gates(
        plan, atr_1m=.1, adx_15m=40, ma20_slope_atr=.25, regime_direction=1)
    assert gated.allowed
    assert gated.long is not None
    assert gated.short is not None


def test_disaster_stop_is_outer_bounded_and_preserves_normal_stop():
    level = SniperLevel(-1, 100.0, 100.4, 98.0, 5.0, "s")
    disaster = _disaster_stop(level, .1)
    assert disaster == pytest.approx(100.7)
    too_wide = SniperLevel(-1, 100.0, 102.0, 95.0, 2.5, "s")
    assert _disaster_stop(too_wide, .1) is None


def test_post_fill_waits_for_two_complete_bars_then_tightens_or_exits():
    dates = pd.date_range("2026-08-23 00:40", periods=4, freq="min", tz="UTC")
    one = pd.DataFrame({"date": dates, "close": [100.0, 99.8, 99.6, 99.7]})
    filled = datetime(2026, 8, 23, 0, 40, 30, tzinfo=timezone.utc)
    assert _completed_bars_after_fill(one.iloc[:2], filled) == 1
    assert _completed_bars_after_fill(one.iloc[:3], filled) == 2
    assert _post_fill_decision(-1, 100, 100.5, 99.7, one.iloc[:2], 1) == "observe"
    assert _post_fill_decision(-1, 100, 100.5, 99.7, one.iloc[:3], 2) == "tighten"
    invalid = one.copy()
    invalid.loc[invalid.index[-1], "close"] = 100.7
    assert _post_fill_decision(-1, 100, 100.5, 100.7, invalid, 2) == "exit"


def test_filled_sniper_keeps_disaster_stop_then_tightens_after_two_bars(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_sniper_protection(
        client_order_id="QBRSNPTESTS1", order_id="ENTRY1",
        strategy_id="strategy_02", strategy_version="v49",
        instrument="ETH-USDT-SWAP", direction=-1, entry_price=100,
        normal_stop=100.5, disaster_stop=101.0,
    )
    store.close()
    filled = datetime(2026, 8, 23, 0, 40, 30, tzinfo=timezone.utc)
    one = pd.DataFrame({
        "date": pd.date_range("2026-08-23 00:40", periods=3, freq="min", tz="UTC"),
        "open": [100, 99.9, 99.8], "high": [100.2, 100.1, 100.0],
        "low": [99.7, 99.6, 99.5], "close": [99.9, 99.8, 99.7],
    })
    calls = []
    class Client:
        def tighten_active_stop_loss(self, algo, stop): calls.append((algo["algoId"], stop))
    snapshot = {
        "orders": [],
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1",
                       "markPx": "99.7", "cTime": str(int(filled.timestamp() * 1000))}],
        "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "short",
                         "algoId": "SL1", "algoClOrdId": "QBRSNPTESTS1P"}],
    }
    result = execute_structure_sniper_tick(
        Client(), snapshot, "ETH-USDT-SWAP", "QBR", one, one, one,
        database=database, strategy_id="strategy_02", strategy_version="v49",
    )
    assert result.action == "stop_tightened"
    assert calls == [("SL1", "100.50")]


def test_two_filled_sniper_layers_converge_to_one_after_two_complete_bars(tmp_path):
    database = tmp_path / "state.sqlite3"
    filled = datetime(2026, 8, 23, 0, 40, 30, tzinfo=timezone.utc)
    store = StateStore(database)
    for client_id, entry in (("QBRSNPLAYERWORSES1", 99.0), ("QBRSNPLAYERBETTERS2", 101.0)):
        store.record_sniper_protection(
            client_order_id=client_id, order_id=client_id + "O",
            strategy_id="strategy_02", strategy_version="v49",
            instrument="ETH-USDT-SWAP", direction=-1, entry_price=entry,
            normal_stop=102.0, disaster_stop=103.0,
        )
        store.update_sniper_protection(
            client_id, status="observing", filled_at_utc=filled.isoformat(),
            entry_bar_time=filled.replace(second=0, microsecond=0).isoformat())
    store.close()
    one = pd.DataFrame({
        "date": pd.date_range("2026-08-23 00:40", periods=3, freq="min", tz="UTC"),
        "open": [100, 99.9, 99.8], "high": [100.2, 100.1, 100.0],
        "low": [99.7, 99.6, 99.5], "close": [99.9, 99.8, 99.7],
    })
    closed = []
    class Client:
        def close_demo_position_market(self, contracts, **kwargs):
            closed.append((contracts, kwargs["position_side"]))
    snapshot = {
        "orders": [],
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "2",
                       "markPx": "99.7", "cTime": str(int(filled.timestamp() * 1000))}],
        "algo_orders": [
            {"instId": "ETH-USDT-SWAP", "posSide": "short", "algoId": "SL1",
             "algoClOrdId": "QBRSNPLAYERWORSES1P"},
            {"instId": "ETH-USDT-SWAP", "posSide": "short", "algoId": "SL2",
             "algoClOrdId": "QBRSNPLAYERBETTERS2P"},
        ],
    }

    result = execute_structure_sniper_tick(
        Client(), snapshot, "ETH-USDT-SWAP", "QBR", one, one, one,
        database=database, strategy_id="strategy_02", strategy_version="v49")

    assert result.action == "layer_consolidated"
    assert closed == [(1, "short")]
    store = StateStore(database)
    assert store.sniper_protection("QBRSNPLAYERWORSES1")["status"] == "exit_requested"
    assert store.sniper_protection("QBRSNPLAYERBETTERS2")["status"] == "observing"
    store.close()


def test_missing_local_protection_state_recovers_from_entry_snapshot_and_okx(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_entry_snapshot(
        snapshot_uid="QBRSNPRECOVERS1", strategy_id="strategy_02", strategy_version="v49",
        order_id="ENTRY-R", signal_time="2026-08-23T00:40:00+00:00", direction=-1,
        branch="structure_sniper", trigger_reason="local high",
        context={"normal_stop": 100.5, "disaster_stop": 101.0},
        entry_reference=100.0, stop_price=101.0, take_profit_price=98.0,
        status="pending_limit",
    )
    store.close()
    filled = datetime(2026, 8, 23, 0, 40, 30, tzinfo=timezone.utc)
    one = pd.DataFrame({
        "date": pd.date_range("2026-08-23 00:40", periods=2, freq="min", tz="UTC"),
        "open": [100, 99.9], "high": [100.2, 100.1], "low": [99.7, 99.6],
        "close": [99.9, 99.8],
    })
    snapshot = {
        "orders": [],
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1",
                       "markPx": "99.8", "cTime": str(int(filled.timestamp() * 1000))}],
        "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "short",
                         "algoId": "SL-R", "algoClOrdId": "QBRSNPRECOVERS1P"}],
    }

    result = execute_structure_sniper_tick(
        SimpleNamespace(), snapshot, "ETH-USDT-SWAP", "QBR", one, one, one,
        database=database, strategy_id="strategy_02", strategy_version="v49",
    )

    assert result.action == "protection_state_recovered"
    store = StateStore(database)
    recovered = store.sniper_protection("QBRSNPRECOVERS1")
    lifecycle = store.connection.execute(
        "SELECT * FROM trade_lifecycle WHERE trade_uid='QBRSNPRECOVERS1'").fetchone()
    store.close()
    assert recovered is not None and recovered["status"] == "observing"
    assert recovered["normal_stop"] == pytest.approx(100.5)
    assert lifecycle is not None
    assert lifecycle["branch"] == "structure_sniper"
    assert lifecycle["entry_reference"] == pytest.approx(100.0)


def test_filled_sniper_without_matching_server_stop_returns_visible_warning(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_sniper_protection(
        client_order_id="QBRSNPWARNINGS1", order_id="ENTRY-W",
        strategy_id="strategy_02", strategy_version="v49",
        instrument="ETH-USDT-SWAP", direction=-1, entry_price=100,
        normal_stop=100.5, disaster_stop=101.0,
    )
    store.close()
    one = _frame()
    snapshot = {
        "orders": [], "algo_orders": [],
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1",
                       "markPx": "99.8", "cTime": "0"}],
    }

    result = execute_structure_sniper_tick(
        SimpleNamespace(), snapshot, "ETH-USDT-SWAP", "QBR", one, one, one,
        database=database, strategy_id="strategy_02", strategy_version="v49",
    )

    assert result.action == "protection_recovery_warning"
    assert "未找到匹配的服务器灾难止损" in result.reason


def test_stale_pending_sniper_does_not_claim_newer_protected_same_side_position(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_sniper_protection(
        client_order_id="QBRSNPOLDL1", order_id="OLD-ENTRY",
        strategy_id="strategy_02", strategy_version="v49",
        instrument="ETH-USDT-SWAP", direction=1, entry_price=100,
        normal_stop=99.5, disaster_stop=98.5,
    )
    store.close()
    current_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    snapshot = {
        "orders": [],
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "1",
                       "markPx": "101", "cTime": str(current_ms),
                       "closeOrderAlgo": [{"posSide": "long", "algoId": "CURRENT-SL",
                                           "algoClOrdId": "QBVALCURRENTSTOP"}]}],
        "algo_orders": [],
    }

    result = execute_structure_sniper_tick(
        SimpleNamespace(), snapshot, "ETH-USDT-SWAP", "QBR", _frame(), _frame(), _frame(),
        database=database, strategy_id="strategy_02", strategy_version="v49")

    assert result.action != "protection_recovery_warning"
    store = StateStore(database)
    assert store.sniper_protection("QBRSNPOLDL1")["status"] == "closed"
    store.close()


def test_structure_sniper_never_early_exits_position_owned_by_normal_lifecycle(
        tmp_path, monkeypatch):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.open_trade_lifecycle(
        trade_uid="QBVALNORMAL1", strategy_id="strategy_01", strategy_version="v234",
        instrument="ETH-USDT-SWAP", direction=1,
        signal_time="2026-09-19T08:59:00+00:00", signal_reason="5m pullback long",
        signal_context={"protection_mode": "ma5_turn"}, order_id="ENTRY1", algo_id="SL1",
        entry_reference=2644.49, stop_price=2642.09,
        trailing_activation=2648.49, trailing_callback=1.32,
        branch="early_one_minute_frozen_stage_launch",
    )
    store.close()
    monkeypatch.setattr(
        "quantbot.structure_sniper._active_risk_exit_reason",
        lambda *args, **kwargs: "1分钟MA5微观止盈不得越权执行",
    )
    monkeypatch.setattr(
        "quantbot.structure_sniper.build_structure_sniper_plan",
        lambda *args, **kwargs: SniperPlan(False, "no new setup", "5m"),
    )
    closed = []

    class Client:
        def close_demo_position_market(self, *args, **kwargs):
            closed.append((args, kwargs))

    snapshot = {
        "orders": [], "algo_orders": [],
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "1",
                       "avgPx": "2644.49", "markPx": "2649.57", "cTime": "0"}],
    }
    result = execute_structure_sniper_tick(
        Client(), snapshot, "ETH-USDT-SWAP", "QBVAL", _frame(), _frame(), _frame(),
        database=database, strategy_id="strategy_01", strategy_version="v235",
    )

    assert result.action != "early_exit"
    assert closed == []


def test_strategy02_can_select_latest_confirmed_local_five_minute_pivots():
    frame = _frame()
    frame.iloc[-30, frame.columns.get_loc("high")] = 106
    frame.iloc[-28, frame.columns.get_loc("low")] = 94
    frame.iloc[-6, frame.columns.get_loc("high")] = 103
    frame.iloc[-5, frame.columns.get_loc("low")] = 97
    strongest_high, strongest_low = _confirmed_pivots(frame, selection="strongest")
    latest_high, latest_low = _confirmed_pivots(frame, selection="latest")
    assert float(strongest_high["high"]) == 106
    assert float(strongest_low["low"]) == 94
    assert latest_high.name > strongest_high.name
    assert latest_low.name > strongest_low.name


def test_strategy02_resting_order_targets_five_minute_ma5_ma10_band():
    structure = _frame()
    one_minute = _frame()
    five_minute_target = _frame()
    five_minute_target.loc[:, "close"] = 101.0
    plan = build_structure_sniper_plan(
        structure, one_minute, timeframe="5m", adx_15m=20,
        ma20_slope_atr=.02, target_ma_frame=five_minute_target)
    assert plan.allowed
    assert plan.short.take_profit == 101.0
    assert plan.long.take_profit == 101.0


def test_strategy02_can_prepare_two_recent_local_levels_per_direction():
    frame = _frame()
    frame.iloc[-7:-1, frame.columns.get_loc("high")] = 101.0
    frame.iloc[-7:-1, frame.columns.get_loc("low")] = 99.0
    for offset, high, low in ((-18, 103.8, 96.2), (-8, 104.2, 95.8)):
        frame.iloc[offset, frame.columns.get_loc("high")] = high
        frame.iloc[offset, frame.columns.get_loc("low")] = low
    plan = build_structure_sniper_plan(
        frame, _frame(), timeframe="5m", adx_15m=20, ma20_slope_atr=.02,
        pivot_selection="latest", max_levels_per_direction=2)
    assert plan.allowed
    assert 1 <= len(plan.short_levels) <= 2
    assert 1 <= len(plan.long_levels) <= 2
    assert len({level.entry for level in plan.short_levels}) == len(plan.short_levels)
    assert len({level.entry for level in plan.long_levels}) == len(plan.long_levels)


def test_structure_sniper_keeps_two_sided_plan_in_strong_fifteen_minute_trend():
    frame = _frame()
    plan = build_structure_sniper_plan(frame, frame, timeframe="15m", adx_15m=40, ma20_slope_atr=.02)
    assert plan.allowed
    assert plan.short is not None and plan.long is not None


def test_bearish_waiting_stage_queues_reversal_shorts_and_bull_continuation_longs():
    frame = _frame()
    base = build_structure_sniper_plan(frame, frame, timeframe="5m", adx_15m=20,
                                       ma20_slope_atr=.02)
    plan = _candidate_waiting_layers(
        base, frame, frame, SimpleNamespace(state="bearish_reversal_candidate"))
    ma20 = float(frame.iloc[:-1]["close"].rolling(20).mean().iloc[-1])
    assert plan.long is not None and plan.long_levels
    assert len(plan.short_levels) == 2
    assert min(level.entry for level in plan.short_levels) > ma20
    assert all(level.reward_risk >= 1.5 for level in plan.short_levels)
    assert all(level.entry < level.take_profit for level in plan.long_levels)
    assert all(level.reward_risk >= 1.5 for level in plan.long_levels)


def test_staged_entry_phase_always_maps_to_one_supported_queue_stage():
    phase = staged_entry_phase(_frame(), _frame())
    assert phase in {"V型极值捕捉", "反转等候期", "反转确认第一单", "趋势回踩第二/第三单"}


def test_closed_one_minute_volume_stopping_bar_prefers_resting_long():
    five, one = _frame(), _frame()
    one["volume"] = 10.0
    pos = len(one) - 2
    one.iloc[pos, [one.columns.get_loc(name) for name in
                   ("open", "high", "low", "close", "volume")]] = [99.5, 99.7, 96.0, 99.0, 30.0]
    base = build_structure_sniper_plan(five, one, timeframe="5m", adx_15m=20,
                                       ma20_slope_atr=.02)
    plan = _one_minute_stopping_limit_layer(
        base, five, one, SimpleNamespace(state="ranging", direction=0))
    assert plan.long is not None
    assert plan.long.entry < float(one.iloc[-1]["close"])
    assert plan.long.take_profit > plan.long.entry
    assert "优先" in plan.reason


def test_confirmed_bearish_regime_allows_only_deep_deviation_stopping_limit():
    five, one = _frame(), _frame()
    one["volume"] = 10.0
    pos = len(one) - 2
    one.iloc[pos, [one.columns.get_loc(name) for name in
                   ("open", "high", "low", "close", "volume")]] = [99.5, 99.7, 96.0, 99.0, 30.0]
    base = build_structure_sniper_plan(five, one, timeframe="5m", adx_15m=20,
                                       ma20_slope_atr=.02)
    plan = _one_minute_stopping_limit_layer(
        base, five, one,
        SimpleNamespace(state="bearish_reversal_confirmed", direction=-1))
    assert plan.long is not None
    assert plan.short is None
    assert "深度乖离" in plan.reason


def test_first_five_minute_ma20_breakout_pullback_is_pre_embedded_and_invalidates():
    dates = pd.date_range("2026-08-17", periods=37, freq="5min", tz="UTC")
    closes = [100.0] * 28 + [100.05, 100.65, 100.95, 101.25, 101.45, 101.30, 101.40, 101.55, 101.60]
    five = pd.DataFrame({"date": dates, "open": closes, "high": [x + .25 for x in closes],
                         "low": [x - .25 for x in closes], "close": closes, "volume": 10.0})
    one = _frame()
    one.loc[:, "close"] = 101.8
    one.loc[:, "open"] = 101.8
    one.loc[:, "high"] = 102.0
    one.loc[:, "low"] = 101.6
    base = SniperPlan(False, "base", "")
    plan = _ma20_breakout_first_pullback_limit_layer(base, five, one)
    assert plan.allowed and plan.long is not None and plan.short is None
    assert plan.long.entry < 101.8
    assert plan.long.take_profit - plan.long.entry >= 1.5 * (plan.long.entry - plan.long.stop)

    invalid = five.copy()
    invalid.iloc[-2, invalid.columns.get_loc("close")] = 99.5
    invalid.iloc[-2, invalid.columns.get_loc("open")] = 100.0
    assert not _ma20_breakout_first_pullback_limit_layer(base, invalid, one).allowed


def test_active_exit_requires_adverse_evidence_and_applies_to_both_entry_types():
    dates5 = pd.date_range("2026-08-17", periods=32, freq="5min", tz="UTC")
    falling = [110 - i * .35 for i in range(32)]
    five = pd.DataFrame({"date": dates5, "open": [x + .1 for x in falling],
                         "high": [x + .2 for x in falling], "low": [x - .2 for x in falling],
                         "close": falling, "volume": 10.0})
    dates1 = pd.date_range("2026-08-17", periods=40, freq="min", tz="UTC")
    one_close = [105 - i * .12 for i in range(40)]
    one = pd.DataFrame({"date": dates1, "open": [x + .05 for x in one_close],
                        "high": [x + .1 for x in one_close], "low": [x - .1 for x in one_close],
                        "close": one_close, "volume": 10.0})
    assert _active_risk_exit_reason("long", five, one, 5)
    assert not _active_risk_exit_reason("short", five, one, 5)


def test_sideways_timeout_alone_does_not_force_exit():
    five = _frame(50)
    one = _frame(80)
    five["volume"] = one["volume"] = 10.0
    assert _active_risk_exit_reason("long", five, one, 60) == ""
    assert _active_risk_exit_reason("short", five, one, 60) == ""


def test_active_exit_does_not_crystallize_a_loss_before_server_stop():
    dates = pd.date_range("2026-08-20", periods=40, freq="min", tz="UTC")
    falling = [110 - i * .2 for i in range(40)]
    one = pd.DataFrame({"date": dates, "open": [x + .1 for x in falling],
                        "high": [x + .2 for x in falling], "low": [x - .2 for x in falling],
                        "close": falling, "volume": 100.0})
    five = one.iloc[:32].copy()
    assert _active_risk_exit_reason(
        "long", five, one, 10, position_profitable=False) == ""


def test_profitable_long_must_exit_on_closed_bearish_five_minute_ma5_turn():
    dates = pd.date_range("2026-08-20", periods=30, freq="5min", tz="UTC")
    close = [100 + i * .5 for i in range(20)] + [110.0] * 10
    five = pd.DataFrame({"date": dates, "open": close, "high": [x + .3 for x in close],
                         "low": [x - .3 for x in close], "close": close, "volume": 100.0})
    five.loc[28, "open"] = 110.3
    one = five.copy()
    reason = _active_risk_exit_reason("long", five, one, 10, position_profitable=True)
    assert "MA5" in reason and "强制止盈" in reason


def test_profitable_short_uses_mirrored_five_minute_ma5_turn_exit():
    dates = pd.date_range("2026-08-20", periods=30, freq="5min", tz="UTC")
    close = [120 - i * .5 for i in range(20)] + [110.0] * 10
    five = pd.DataFrame({"date": dates, "open": close, "high": [x + .3 for x in close],
                         "low": [x - .3 for x in close], "close": close, "volume": 100.0})
    five.loc[28, "open"] = 109.7
    one = five.copy()
    reason = _active_risk_exit_reason("short", five, one, 10, position_profitable=True)
    assert "MA5" in reason and "强制止盈" in reason


def test_profitable_fast_spike_tip_exits_before_ma5_turn_and_mirrors():
    dates = pd.date_range("2026-08-20", periods=36, freq="1min", tz="UTC")
    close = [100 + i * .25 for i in range(36)]
    one = pd.DataFrame({"date": dates, "open": close,
                        "high": [x + .2 for x in close], "low": [x - .2 for x in close],
                        "close": close, "volume": 100.0})
    one.loc[34, ["open", "high", "low", "close", "volume"]] = [
        108.4, 111.0, 108.1, 108.5, 250.0]
    five = one.copy()
    long_reason = _active_risk_exit_reason("long", five, one, 5, position_profitable=True)
    assert "针" in long_reason and "早于MA5" in long_reason
    mirrored_one = one.copy()
    mirrored_one["open"] = 220 - one["open"]
    mirrored_one["high"] = 220 - one["low"]
    mirrored_one["low"] = 220 - one["high"]
    mirrored_one["close"] = 220 - one["close"]
    short_reason = _active_risk_exit_reason(
        "short", mirrored_one.copy(), mirrored_one, 5, position_profitable=True)
    assert "针" in short_reason and "早于MA5" in short_reason


def test_structure_sniper_keeps_opposite_line_after_fill_and_allows_hedge(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper._apply_final_sniper_gates", lambda plan, **_: plan)
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    prepared = build_structure_sniper_plan(
        frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    monkeypatch.setattr("quantbot.structure_sniper.build_structure_sniper_plan", lambda *args, **kwargs: prepared)
    placed, cancelled = [], []

    class Client:
        def place_demo_sniper_limit_order(self, *args, **kwargs):
            placed.append((args, kwargs))
            return {"data": [{"ordId": str(len(placed))}]}

        def cancel_orders(self, orders):
            cancelled.extend(orders)
            return {"data": []}

    empty = {"positions": [], "orders": [], "algo_orders": []}
    result = execute_structure_sniper_tick(Client(), empty, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "placed"
    assert len(placed) == 2
    assert {call[0][0] for call in placed} == {"buy", "sell"}

    pending = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1"}],
        "orders": [{"instId": "ETH-USDT-SWAP", "ordId": "2", "clOrdId": "QBRSNP08152000L"}],
        "algo_orders": [],
    }
    result = execute_structure_sniper_tick(Client(), pending, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "armed"
    assert cancelled == []

    one_short = {"positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1"}],
                 "orders": [], "algo_orders": []}
    before = len(placed)
    result = execute_structure_sniper_tick(Client(), one_short, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "placed"
    assert len(placed) == before + 1
    assert placed[-1][1]["position_side"] == "long"

    two_short = {"positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "2"}],
                 "orders": [], "algo_orders": []}
    result = execute_structure_sniper_tick(Client(), two_short, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "placed"
    assert placed[-1][1]["position_side"] == "long"


def test_structure_sniper_keeps_valid_order_and_reanchors_moved_structure(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper._apply_final_sniper_gates", lambda plan, **_: plan)
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    prepared = build_structure_sniper_plan(
        frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    monkeypatch.setattr(
        "quantbot.structure_sniper.build_structure_sniper_plan", lambda *args, **kwargs: prepared)
    placed, cancelled = [], []

    class Client:
        def place_demo_sniper_limit_order(self, *args, **kwargs):
            placed.append((args, kwargs))
            return {"data": [{"ordId": str(len(placed))}]}

        def cancel_orders(self, orders):
            cancelled.extend(orders)
            return {"data": []}

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    valid = {"positions": [], "algo_orders": [], "orders": [
        {"ordId": "S", "clOrdId": "QBRSNP1S", "posSide": "short",
         "px": str(prepared.short.entry), "cTime": str(now_ms)},
        {"ordId": "L", "clOrdId": "QBRSNP1L", "posSide": "long",
         "px": str(prepared.long.entry), "cTime": str(now_ms)},
    ]}
    result = execute_structure_sniper_tick(
        Client(), valid, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "armed"
    assert not cancelled and not placed

    moved = {**valid, "orders": [dict(item, px="90") for item in valid["orders"]]}
    result = execute_structure_sniper_tick(
        Client(), moved, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "reanchored"
    assert len(cancelled) == 2 and len(placed) == 2
    assert "动态重锚" in result.reason


def test_structure_sniper_reanchor_places_new_before_cancelling_old(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper._apply_final_sniper_gates", lambda plan, **_: plan)
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    prepared = build_structure_sniper_plan(frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    monkeypatch.setattr("quantbot.structure_sniper.build_structure_sniper_plan", lambda *args, **kwargs: prepared)
    events = []
    class Client:
        def place_demo_sniper_limit_order(self, *args, **kwargs):
            events.append("place")
            return {"data": [{"ordId": f"N{len(events)}"}]}
        def cancel_orders(self, orders):
            events.append("cancel-old" if str(orders[0].get("ordId", "")).startswith("O") else "cancel-new")
            return {"data": []}
    pending = {"positions": [], "algo_orders": [], "orders": [
        {"ordId": "OS", "clOrdId": "QBRSNP1S", "posSide": "short", "px": "90"},
        {"ordId": "OL", "clOrdId": "QBRSNP1L", "posSide": "long", "px": "90"},
    ]}
    result = execute_structure_sniper_tick(Client(), pending, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "reanchored"
    assert events == ["place", "place", "cancel-old"]


def test_structure_sniper_failed_reanchor_keeps_old_and_cleans_partial_new(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper._apply_final_sniper_gates", lambda plan, **_: plan)
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    prepared = build_structure_sniper_plan(frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    monkeypatch.setattr("quantbot.structure_sniper.build_structure_sniper_plan", lambda *args, **kwargs: prepared)
    cancelled, calls = [], 0
    class Client:
        def place_demo_sniper_limit_order(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("temporary placement failure")
            return {"data": [{"ordId": "NEW"}]}
        def cancel_orders(self, orders):
            cancelled.extend(str(item.get("ordId", "")) for item in orders)
            return {"data": []}
    pending = {"positions": [], "algo_orders": [], "orders": [
        {"ordId": "OS", "clOrdId": "QBRSNP1S", "posSide": "short", "px": "90"},
        {"ordId": "OL", "clOrdId": "QBRSNP1L", "posSide": "long", "px": "90"},
    ]}
    with pytest.raises(RuntimeError, match="temporary placement failure"):
        execute_structure_sniper_tick(Client(), pending, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert cancelled == ["NEW"]


def test_structure_sniper_missing_new_order_id_never_cancels_old(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper._apply_final_sniper_gates", lambda plan, **_: plan)
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    prepared = build_structure_sniper_plan(
        frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    monkeypatch.setattr(
        "quantbot.structure_sniper.build_structure_sniper_plan", lambda *args, **kwargs: prepared)
    cancelled = []

    class Client:
        def place_demo_sniper_limit_order(self, *args, **kwargs):
            return {"data": [{"ordId": ""}]}

        def cancel_orders(self, orders):
            cancelled.extend(str(item.get("ordId", "")) for item in orders)
            return {"data": []}

    pending = {"positions": [], "algo_orders": [], "orders": [
        {"ordId": "OS", "clOrdId": "QBRSNP1S", "posSide": "short", "px": "90"},
        {"ordId": "OL", "clOrdId": "QBRSNP1L", "posSide": "long", "px": "90"},
    ]}
    with pytest.raises(RuntimeError, match="returned no order id"):
        execute_structure_sniper_tick(
            Client(), pending, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert cancelled == []


def test_structure_sniper_cancels_old_lines_when_current_plan_is_invalid(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    monkeypatch.setattr("quantbot.structure_sniper.build_structure_sniper_plan",
                        lambda *args, **kwargs: SniperPlan(False, "temporary", "5m"))
    cancelled = []
    class Client:
        def cancel_orders(self, orders): cancelled.extend(orders)
    pending = {"positions": [], "algo_orders": [], "orders": [
        {"ordId": "OS", "clOrdId": "QBRSNP1S", "posSide": "short", "px": "104"},
        {"ordId": "OL", "clOrdId": "QBRSNP1L", "posSide": "long", "px": "96"},
    ]}
    result = execute_structure_sniper_tick(Client(), pending, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "invalidated"
    assert result.order_ids == ("OS", "OL")
    assert [item["ordId"] for item in cancelled] == ["OS", "OL"]


def test_contract_suspension_becomes_waiting_state_and_suppresses_repeat_posts(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper._apply_final_sniper_gates", lambda plan, **_: plan)
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    prepared = build_structure_sniper_plan(frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    monkeypatch.setattr("quantbot.structure_sniper.build_structure_sniper_plan", lambda *args, **kwargs: prepared)
    monkeypatch.setattr("quantbot.structure_sniper._CONTRACT_SUSPENDED_UNTIL", {})
    calls = 0
    class Client:
        def place_demo_sniper_limit_order(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("OKX order rejected: sCode=51022 sMsg=Contract suspended.")
        def cancel_orders(self, orders): return {"data": []}
    empty = {"positions": [], "orders": [], "algo_orders": []}
    first = execute_structure_sniper_tick(Client(), empty, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    second = execute_structure_sniper_tick(Client(), empty, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert first.action == second.action == "suspended"
    assert calls == 1
    assert "自动" in second.reason


def test_structure_sniper_expires_valid_orders_after_thirty_minutes(monkeypatch):
    frame = _frame()
    monkeypatch.setattr("quantbot.structure_sniper._apply_final_sniper_gates", lambda plan, **_: plan)
    monkeypatch.setattr("quantbot.structure_sniper.adx", lambda *_: pd.Series([20.0]))
    prepared = build_structure_sniper_plan(
        frame, frame, timeframe="5m", adx_15m=20, ma20_slope_atr=.02)
    monkeypatch.setattr(
        "quantbot.structure_sniper.build_structure_sniper_plan", lambda *args, **kwargs: prepared)
    placed, cancelled = [], []

    class Client:
        def place_demo_sniper_limit_order(self, *args, **kwargs):
            placed.append((args, kwargs))
            return {"data": [{"ordId": str(len(placed))}]}

        def cancel_orders(self, orders):
            cancelled.extend(orders)
            return {"data": []}

    old_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 1_801_000
    pending = {"positions": [], "algo_orders": [], "orders": [
        {"ordId": "S", "clOrdId": "QBRSNP1S", "posSide": "short",
         "px": str(prepared.short.entry), "cTime": str(old_ms)},
        {"ordId": "L", "clOrdId": "QBRSNP1L", "posSide": "long",
         "px": str(prepared.long.entry), "cTime": str(old_ms)},
    ]}
    result = execute_structure_sniper_tick(
        Client(), pending, "ETH-USDT-SWAP", "QBR", frame, frame, frame)
    assert result.action == "expired"
    assert [item["ordId"] for item in cancelled] == ["S", "L"]
    assert not placed
    assert "30分钟" in result.reason
