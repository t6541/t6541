import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from quantbot.live_condition_log import (chinese_decision_reason,
                                         read_condition_events_for_beijing_day,
                                         read_recent_condition_event_records,
                                         read_recent_condition_events)
from quantbot.validation_execution import (early_downtrend_throwback_short_allowed,
                                            five_minute_live_turn_evidence,
                                            hourly_multiframe_boundary_sample,
                                            lower_timeframe_ma5_pullback_allowed,
                                            three_candle_short_stop_evidence)
from quantbot.validation_execution import higher_timeframe_boundary_evidence
from quantbot.state import StateStore


def test_hourly_boundary_sample_returns_four_values_after_ma20_gate_added():
    dates = pd.date_range("2026-09-17T12:00:00Z", periods=25, freq="min")
    one = pd.DataFrame({"date": dates, "open": [2500.0] * len(dates),
                        "high": [2500.1] * len(dates), "low": [2499.9] * len(dates),
                        "close": [2500.0] * len(dates)})
    five = pd.DataFrame({"date": dates, "close": [2500.0] * len(dates)})
    markets = {"1m": one, "5m": five}
    signals = {key: SimpleNamespace(direction=0) for key in ("5m", "15m", "1H")}

    hour, direction, classification, evidence = hourly_multiframe_boundary_sample(
        markets, signals, dates[-1])

    assert hour.endswith("+08:00")
    assert direction == 0
    assert classification
    assert evidence["five_minute_ma20"] == 2500.0


def test_parent_downtrend_authorizes_early_ma5_edge_short_without_hour_veto():
    base = dict(local_high_short=True, selected_trend_entry=True, direction=-1,
                durable_bias=1, five_direction=1, fifteen_direction=-1,
                hour_direction=1)
    assert early_downtrend_throwback_short_allowed(**base)
    assert not early_downtrend_throwback_short_allowed(
        **{**base, "local_high_short": False})
    assert not early_downtrend_throwback_short_allowed(
        **{**base, "fifteen_direction": 1})
    assert early_downtrend_throwback_short_allowed(
        **{**base, "durable_bias": -1, "five_direction": -1,
           "fifteen_direction": 1})


def test_stage_one_top_can_enter_parent_downtrend_throwback_channel():
    assert early_downtrend_throwback_short_allowed(
        local_high_short=True, selected_trend_entry=True, direction=-1,
        durable_bias=1, five_direction=-1, fifteen_direction=-1,
        hour_direction=-1)


def test_aligned_one_and_five_minute_retest_after_bottom_lock_is_pullback_long():
    assert lower_timeframe_ma5_pullback_allowed(
        ma5_candidate=True, direction=1, one_direction=1, five_direction=1,
        fresh_lock_direction=1)
    assert not lower_timeframe_ma5_pullback_allowed(
        ma5_candidate=True, direction=1, one_direction=1, five_direction=-1,
        fresh_lock_direction=1)
    assert not lower_timeframe_ma5_pullback_allowed(
        ma5_candidate=True, direction=1, one_direction=1, five_direction=1,
        fresh_lock_direction=-1)


def test_live_five_minute_red_candle_is_logged_separately_from_uptrend_vote():
    import pandas as pd
    live = pd.DataFrame({"date": ["2026-09-15T23:20:00Z"],
                         "open": [2411.14], "close": [2409.14]})
    candle_direction, reason = five_minute_live_turn_evidence(live, 1)
    assert candle_direction == -1
    assert "实时5分钟K线=阴线" in reason
    assert "方向箭头=上涨" in reason


def test_three_candle_short_stop_uses_real_body_top_instead_of_upper_wick():
    import pandas as pd
    from quantbot.entry_risk import latest_atr
    rows = pd.DataFrame({
        "date": pd.date_range("2026-09-15T23:03:00Z", periods=18, freq="min"),
        "open": [2405.0] * 18, "close": [2405.0] * 18,
        "high": [2407.0] * 15 + [2412.2, 2414.19, 2411.14],
        "low": [2403.0] * 18,
    })
    rows.loc[15, "close"] = 2409.72
    rows.loc[16, "open"] = 2410.09
    rows.loc[16, "close"] = 2411.20
    rows.loc[17, "open"] = 2411.14
    rows.loc[17, "close"] = 2409.20
    edge, stop, reason = three_candle_short_stop_evidence(rows, None, 2408.51)
    assert edge == 2411.20
    assert stop > edge
    assert stop >= edge + max(latest_atr(rows) * .15, 2408.51 * .0003) - 1e-8
    assert "实体上沿" in reason


def test_hour_and_fifteen_minute_joint_boundary_is_logged_as_evidence():
    from datetime import datetime, timezone
    assert "1小时与15分钟同步换线" in higher_timeframe_boundary_evidence(
        datetime(2026, 9, 15, 20, 0, 45, tzinfo=timezone.utc))


def test_hourly_boundary_sample_is_persisted_once_per_beijing_hour(tmp_path: Path):
    store = StateStore(tmp_path / "sample.sqlite3")
    try:
        kwargs = dict(
            instrument="ETH-USDT-SWAP", beijing_hour="2026-09-16T04:00:00+08:00",
            endpoint_direction=-1, five_direction=-1, fifteen_direction=-1,
            hour_direction=-1, classification="反抽追空共用证据",
            evidence={"reason": "5分钟、15分钟与1小时同步换线"})
        assert store.record_hourly_boundary_sample(**kwargs)
        assert not store.record_hourly_boundary_sample(**kwargs)
        assert store.connection.execute(
            "SELECT COUNT(*) FROM hourly_multiframe_boundary_samples").fetchone()[0] == 1
        assert store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='hourly_multiframe_boundary_sample'").fetchone()[0] == 1
    finally:
        store.close()


def test_live_condition_evidence_is_translated_to_chinese():
    reason = chinese_decision_reason(
        "independent three-timeframe recovery bottom/top reversal long: "
        "1m is on the valid MA5 side; mandatory live 5m bullish adjacent cover: "
        "current candle adjacent-body cover is 0%")
    assert "独立三周期恢复型底部反转做多" in reason
    assert "1分钟价格位于MA5有效一侧" in reason
    assert "实盘5分钟相邻阳线覆盖门" in reason
    assert "current candle" not in reason


def test_condition_log_shows_structure_and_rejection_from_read_only_db(tmp_path: Path):
    database = tmp_path / "strategy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, created_at_utc TEXT, "
                       "event_type TEXT, payload_json TEXT)")
    connection.executemany("INSERT INTO events VALUES(?,?,?,?)", [
        (1, "2026-09-15T14:04:00+00:00", "downtrend_throwback_early_entry_qualified",
         json.dumps({"direction": -1, "reason": "1m外沿阴线转弱"})),
        (2, "2026-09-15T14:05:00+00:00", "directional_fresh_half_cover_rejected",
         json.dumps({"direction": -1, "reason": "当前仅16%"})),
        (3, "2026-09-15T14:06:00+00:00", "market_pattern_observed", "{}"),
    ])
    connection.commit()
    connection.close()
    rows = read_recent_condition_events(database)
    assert len(rows) == 2
    assert rows[0][:3] == ("09-15 22:05:00", "做空｜五分钟相邻覆盖", "拒绝")
    assert rows[1][2] == "结构通过"
    assert sqlite3.connect(database).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    records = read_recent_condition_event_records(database)
    assert [event_id for event_id, _row in records] == [2, 1]
    page = read_condition_events_for_beijing_day(database, date(2026, 9, 15))
    assert [event_id for event_id, _row in page] == [2, 1]
