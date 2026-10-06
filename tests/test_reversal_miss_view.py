import json

from quantbot.reversal_miss_view import (prune_reversal_miss_history,
                                         read_reversal_misses,
                                         reversal_feed_freshness,
                                         reversal_miss_table_row)
from quantbot.state import StateStore


def test_reversal_miss_list_links_latest_rejection_reason(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    key = "ETH-USDT-SWAP|price_reversal_zone|1m|-1|2026-09-02T06:19:00"
    store.record_market_pattern(
        event_key=key, instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:1m", direction=-1,
        confirmed_bar_time="2026-09-02T06:19:00", status="watching_three_stage",
        entry_reference=2416.22, stop_reference=2422.00,
        features={"strategy_version": "v-test", "five_minute_shape_confirmed": True},
    )
    store.record_event("validation_observation", {
        "strategy_version": "v-test", "price_reversal_zone_keys": [key],
        "reason": "early reversal runway rejected: 近期5分钟实体支撑2414.01仅剩2.21点",
    })
    store.record_market_pattern(
        event_key="stage2", instrument="ETH-USDT-SWAP",
        pattern_type="one_minute_launch_freeze:price_break_ma5", direction=-1,
        confirmed_bar_time="2026-09-02T06:20:00", status="pending",
        entry_reference=2415.00, stop_reference=2422.00, features={},
    )
    store.resolve_market_pattern(key, outcome_status="missed_before_next_reversal",
                                 outcome={"summary": "下一普通K线反转区已经形成但前区未成交"})
    store.close()

    rows = read_reversal_misses(database)
    assert rows[0]["time"] == "2026-09-02 14:19:00"
    assert rows[0]["direction"] == "扫顶做空"
    assert rows[0]["result"] == "确认漏单"
    assert rows[0]["stage2"] == "已触发 14:20:00"
    assert rows[0]["reason_class"] == "局部高点扫顶反转做空｜第二阶段漏单"
    assert "新规则改为第二阶段立即执行" in rows[0]["reason"]
    assert rows[0]["market_shape"] == "局部高点扫顶反转做空｜旧版未按双周期均线重算"
    assert len(reversal_miss_table_row(rows[0])) == 16


def test_reversal_review_keeps_only_latest_thirty_rows(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    for index in range(35):
        at = f"2026-09-03T{index // 60:02d}:{index % 60:02d}:00+00:00"
        store.record_market_pattern(
            event_key=f"zone-{index}", instrument="ETH-USDT-SWAP",
            pattern_type="price_reversal_zone:1m", direction=1,
            confirmed_bar_time=at, status="watching_three_stage",
            entry_reference=2400.0 + index, stop_reference=2399.0,
            features={"strategy_version": "v-test"},
        )
    store.close()
    assert prune_reversal_miss_history(database, keep=30) == 5
    rows = read_reversal_misses(database)
    assert len(rows) == 30
    assert rows[0]["entry"] == "2434.00"
    freshness = reversal_feed_freshness(database)
    assert freshness["zone"] == "2026-09-03 08:34:00"


def test_review_inherits_prior_mature_top_for_later_mixed_five_minute_zone(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="mature-top", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:1m", direction=-1,
        confirmed_bar_time="2026-09-04T19:52:00+00:00", status="watching_three_stage",
        entry_reference=2456.0, stop_reference=2458.0,
        features={"strategy_version": "v148", "market_shape_code": "true_top_reversal",
                  "market_shape_label": "真正顶部反转启动"},
    )
    store.resolve_market_pattern(
        "mature-top", outcome_status="missed_before_next_reversal",
        outcome={"summary": "首单漏掉"})
    store.record_market_pattern(
        event_key="five-confirm", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:5m", direction=-1,
        confirmed_bar_time="2026-09-04T20:05:00+00:00", status="watching_three_stage",
        entry_reference=2455.0, stop_reference=2460.0,
        features={"strategy_version": "v148", "market_shape_code": "mixed_structure_candidate",
                  "market_shape_label": "混合结构候选（非真正反转）",
                  "five_minute_half_cover": True},
    )
    store.close()
    rows = read_reversal_misses(database)
    five = next(row for row in rows if row["timeframe"] == "5m")
    assert five["direction"] == "扫顶做空"
    assert five["market_shape"].startswith("局部高点扫顶反转做空｜")
    assert "顶部反转区已生效" in five["market_shape"]


def test_opposite_order_cannot_resolve_reversal_zone(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="long-zone", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:1m", direction=1,
        confirmed_bar_time="2026-09-03T11:24:00+00:00", status="watching_three_stage",
        entry_reference=2402.08, stop_reference=2400.00, features={},
    )
    assert not store.resolve_market_pattern_for_order(
        "long-zone", direction=-1,
        outcome={"order_id": "short-order", "strategy_version": "v123"},
    )
    assert store.pending_market_patterns("ETH-USDT-SWAP")[0]["event_key"] == "long-zone"
    store.close()


def test_later_same_side_order_cannot_backfill_stale_reversal_zone(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="stale-short-zone", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:5m", direction=-1,
        confirmed_bar_time="2026-09-04T16:55:00+00:00", status="watching_three_stage",
        entry_reference=2459.60, stop_reference=2468.00, features={},
    )
    assert not store.resolve_market_pattern_for_order(
        "stale-short-zone", direction=-1,
        submitted_bar_time="2026-09-04T17:35:00+00:00",
        outcome={"order_id": "later-short"},
    )
    assert store.pending_market_patterns("ETH-USDT-SWAP")[0]["event_key"] == "stale-short-zone"
    assert store.resolve_market_pattern_for_order(
        "stale-short-zone", direction=-1,
        submitted_bar_time="2026-09-04T17:03:00+00:00",
        outcome={"order_id": "current-chain-short"},
    )
    store.close()


def test_review_flags_historical_opposite_order_link(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="long-zone", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:1m", direction=1,
        confirmed_bar_time="2026-09-03T11:24:00+00:00", status="watching_three_stage",
        entry_reference=2402.08, stop_reference=2400.00, features={},
    )
    store.resolve_market_pattern(
        "long-zone", outcome_status="triggered_order_submitted",
        outcome={"order_id": "short-order", "order_direction": -1,
                 "submitted_at_utc": "2026-09-03T11:24:57+00:00"},
    )
    store.close()
    row = read_reversal_misses(database)[0]
    assert row["result"] == "关联异常"
    assert row["reason_class"] == "异向订单误关联"
    assert row["order_side"] == "做空"
    assert row["order_id"] == "short-order"
    assert row["submitted_at"] == "2026-09-03 19:24:57"


def test_unconfirmed_down_colour_is_not_called_a_sweep_top(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="pullback", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:1m", direction=-1,
        confirmed_bar_time="2026-09-02T12:14:00+00:00", status="watching_three_stage",
        entry_reference=2381.33, stop_reference=2382.74,
        features={"strategy_version": "v116", "five_minute_shape_confirmed": False},
    )
    store.close()

    rows = read_reversal_misses(database)
    assert rows[0]["direction"] == "回踩候选（五分钟未确认）"


def test_true_bottom_reversal_uses_sweep_bottom_label(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="bottom", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:1m", direction=1,
        confirmed_bar_time="2026-09-02T16:00:00+00:00", status="watching_three_stage",
        entry_reference=2360.00, stop_reference=2355.00,
        features={"strategy_version": "v118", "market_shape_code": "true_bottom_reversal",
                  "market_shape_label": "真正底部反转启动"},
    )
    store.close()

    rows = read_reversal_misses(database)
    assert rows[0]["direction"] == "扫底做多"
    assert rows[0]["market_shape"] == "真正低位扫底反转做多｜真正底部反转启动"


def test_downtrend_continuation_is_not_called_sweep_top(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="continuation", instrument="ETH-USDT-SWAP",
        pattern_type="price_reversal_zone:1m", direction=-1,
        confirmed_bar_time="2026-09-02T16:05:00+00:00", status="watching_three_stage",
        entry_reference=2375.00, stop_reference=2380.00,
        features={"strategy_version": "v118",
                  "market_shape_code": "downtrend_continuation_short",
                  "market_shape_label": "下跌趋势中继反抽做空"},
    )
    store.close()

    rows = read_reversal_misses(database)
    assert rows[0]["direction"] == "反抽追空"
    assert rows[0]["market_shape"] == "5分钟周期下跌趋势中的反抽高点追空｜下跌趋势中继反抽做空"


def test_legacy_average_zone_remains_visible_but_is_marked_audit_only(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.record_market_pattern(
        event_key="legacy-ha", instrument="ETH-USDT-SWAP",
        pattern_type="heikin_reversal_zone:1m", direction=-1,
        confirmed_bar_time="2026-09-02T16:05:00+00:00", status="watching_three_stage",
        entry_reference=2375.00, stop_reference=2380.00,
        features={"strategy_version": "v141",
                  "market_shape_code": "true_top_reversal",
                  "market_shape_label": "真正顶部反转启动"},
    )
    store.close()

    rows = read_reversal_misses(database)
    assert rows[0]["market_shape"].startswith("历史平均K线记录（仅审计）｜")
    assert rows[0]["stage1"] == "历史：平均K线反转（仅审计）"
    assert "不参与当前方向或新订单放行" in rows[0]["reason"]


def test_every_submitted_lifecycle_is_visible_without_reversal_zone(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.open_trade_lifecycle(
        trade_uid="trade-1", strategy_id="strategy_01", strategy_version="v145",
        instrument="ETH-USDT-SWAP", direction=-1,
        signal_time="2026-09-04T18:28:00+00:00",
        signal_reason="one-minute pullback short", signal_context={"entry_kind": "continuation"},
        order_id="order-1", algo_id="algo-1", entry_reference=2447.82,
        stop_price=2451.27, trailing_activation=2445.0, trailing_callback=.5,
        branch="early_one_minute_frozen_stage_launch",
    )
    store.close()

    row = read_reversal_misses(database)[0]
    assert row["time"] == "2026-09-05 02:28:00"
    assert row["result"] == "已下单"
    assert row["order_id"] == "order-1"
    assert row["market_shape"].startswith("真实下单｜局部高点扫顶反转做空｜")
    assert "分类：局部高点扫顶反转做空；分类规则：" in row["reason"]
    assert row["reason"].endswith("one-minute pullback short")


def test_submitted_order_uses_persisted_five_class_identity(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.open_trade_lifecycle(
        trade_uid="trade-parent", strategy_id="strategy_01", strategy_version="v157",
        instrument="ETH-USDT-SWAP", direction=-1,
        signal_time="2026-09-06T03:25:00+00:00", signal_reason="first bearish turn above MA5",
        signal_context={
            "entry_classification_code": "higher_timeframe_trend_continuation_short",
            "entry_classification_label": "上一级别周期下降趋势中的反抽高点追空",
            "entry_classification_category": "higher_timeframe_trend_continuation",
            "entry_trend_source_timeframe": "parent",
        },
        order_id="order-parent", algo_id="algo-parent", entry_reference=2499.95,
        stop_price=2502.95, trailing_activation=2497.0, trailing_callback=.5,
        branch="downtrend_continuation_short",
    )
    store.close()

    row = read_reversal_misses(database)[0]
    assert "上一级别周期下降趋势中的反抽高点追空" in row["market_shape"]
    assert row["reason_class"] == "上一级别周期下降趋势中的反抽高点追空｜真实订单补录"
    assert row["reason"].startswith("分类：上一级别周期下降趋势中的反抽高点追空；分类规则：")

