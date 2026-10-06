from quantbot.ma_endpoint_view import ma_endpoint_table_row, read_ma_endpoint_lineage
from quantbot.state import StateStore
from quantbot import validation_execution


def _record(store, key, timeframe, direction, at, price, half=True, slow=None,
            fan=True):
    if slow is None:
        slow = bool(half and timeframe == "5m")
    return store.record_ma_endpoint(
        event_key=key, instrument="ETH-USDT-SWAP", timeframe=timeframe,
        direction=direction, confirmed_bar_time=at, endpoint_price=price,
        extreme_price=price, ma5=price, ma10=price - direction,
        ma20=price - 2 * direction, half_cover_confirmed=half,
        slow_ma_cross_confirmed=slow, fan_endpoint_confirmed=fan)


def test_four_level_endpoint_rows_pair_nearest_parent_and_keep_noise(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    try:
        _record(store, "one-noise", "1m", -1, "2026-09-07T00:00:00+00:00", 100)
        _record(store, "one-paired", "1m", -1, "2026-09-07T02:00:00+00:00", 110)
        _record(store, "five-paired", "5m", -1, "2026-09-07T02:05:00+00:00", 111)
        _record(store, "fifteen", "15m", -1, "2026-09-07T02:20:00+00:00", 112)
        _record(store, "hour", "1H", -1, "2026-09-07T03:00:00+00:00", 113)
        rows = {row["event_key"]: row for row in store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
    finally:
        store.close()
    assert rows["one-noise"]["pair_status"] == "unpaired"
    assert rows["one-paired"]["parent_event_key"] == "five-paired"
    assert rows["five-paired"]["parent_event_key"] == "fifteen"
    assert rows["fifteen"]["parent_event_key"] == "hour"
    view = {row["event_key"]: row for row in read_ma_endpoint_lineage(database)}
    assert "1分钟K线上涨趋势中的回踩追多触发点" in ma_endpoint_table_row(view["one-noise"])[15]


def test_nearby_one_and_five_minute_endpoints_do_not_pair_without_five_half_cover(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "one-bottom", "1m", 1, "2026-09-07T22:21:00+08:00", 2483, half=False)
        _record(store, "five-bottom", "5m", 1, "2026-09-07T22:20:00+08:00", 2482, half=False)
        rows = {row["event_key"]: row for row in store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
        assert rows["one-bottom"]["pair_status"] == "unpaired"
        assert rows["one-bottom"]["zone_status"] == "forming"
        assert rows["one-bottom"]["parent_event_key"] is None
    finally:
        store.close()


def test_legacy_local_high_low_without_ma5_ma20_endpoint_cannot_pair(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "legacy-one-low", "1m", 1,
                "2026-09-08T02:03:00+08:00", 2494, fan=False)
        _record(store, "five-low", "5m", 1,
                "2026-09-08T02:05:00+08:00", 2493, half=True, fan=True)
        rows = {row["event_key"]: row for row in
                store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
        assert rows["legacy-one-low"]["pair_status"] == "unpaired"
        assert rows["legacy-one-low"]["identity_code"] == "legacy_unverified_noise"
        assert store.latest_confirmed_one_five_pair(
            "ETH-USDT-SWAP", 1, require_ma5_cross=False) is None
    finally:
        store.close()


def test_half_cover_pairs_without_five_minute_ma_or_slow_cross(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "one-bottom", "1m", 1, "2026-09-07T23:10:00+08:00", 2480, half=False)
        _record(store, "five-half-only", "5m", 1, "2026-09-07T23:15:00+08:00", 2483,
                half=True, slow=False)
        rows = {row["event_key"]: row for row in store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
        assert rows["one-bottom"]["pair_status"] == "paired"
        assert store.latest_confirmed_one_five_pair(
            "ETH-USDT-SWAP", 1, require_ma5_cross=False) is not None
    finally:
        store.close()


def test_stale_endpoint_zone_is_not_extended_into_a_later_reversal(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        assert _record(store, "bottom-start", "1m", 1, "2026-09-07T22:10:00+08:00", 2483, half=False)
        assert _record(store, "bottom-lower", "1m", 1, "2026-09-07T22:40:00+08:00", 2475, half=False)
        rows = store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")
        assert len(rows) == 2
        assert rows[0]["event_key"] == "bottom-lower"
        assert rows[0]["extreme_price"] == 2475
        assert rows[0]["zone_last_seen_at"] == "2026-09-07T22:40:00+08:00"
    finally:
        store.close()


def test_one_minute_pair_requires_ma5_cross_for_strict_confirmation(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "one-bottom", "1m", 1, "2026-09-07T22:40:00+08:00", 2475, half=False)
        _record(store, "five-bottom", "5m", 1, "2026-09-07T22:45:00+08:00", 2475, half=True)
        assert store.latest_confirmed_one_five_pair(
            "ETH-USDT-SWAP", 1, require_ma5_cross=False) is not None
        assert store.latest_confirmed_one_five_pair(
            "ETH-USDT-SWAP", 1, require_ma5_cross=True) is None
        assert store.confirm_recent_endpoint_ma5_cross(
            "ETH-USDT-SWAP", 1, "2026-09-07T22:46:00+08:00") == "one-bottom"
        assert store.latest_confirmed_one_five_pair(
            "ETH-USDT-SWAP", 1, require_ma5_cross=True) is not None
    finally:
        store.close()


def test_local_trial_keeps_five_minute_exit_and_upgrades_identity_after_pair(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "one-bottom", "1m", 1, "2026-09-07T22:40:00+08:00", 2475, half=False)
        _record(store, "five-bottom", "5m", 1, "2026-09-07T22:45:00+08:00", 2475,
                half=True, slow=True)
        store.confirm_recent_endpoint_ma5_cross(
            "ETH-USDT-SWAP", 1, "2026-09-07T22:46:00+08:00")
        store.open_trade_lifecycle(
            trade_uid="local-trial", strategy_id="strategy_01",
            strategy_version=validation_execution.VALIDATION_VERSION,
            instrument="ETH-USDT-SWAP", direction=1,
            signal_time="2026-09-07T22:42:00+08:00", signal_reason="5m half cover",
            signal_context={
                "entry_classification_category": "local_endpoint_reversal",
                "entry_classification_label": "上一级5分钟底部局部反转做多",
                "ma5_exit_timeframe": "5m", "five_minute_ma5_core_hold": True,
            }, order_id="order-1", algo_id="", entry_reference=2478,
            stop_price=2474, trailing_activation=2484, trailing_callback=1,
            branch="five_minute_bottom_local_reversal_half_cover_long",
        )
        promoted = validation_execution.promote_newly_paired_local_trials(
            store, object(), "ETH-USDT-SWAP", 1)
        assert promoted == ("local-trial",)
        lifecycle = store.open_trade_lifecycles(validation_execution.VALIDATION_VERSION)[0]
        import json
        context = json.loads(lifecycle["signal_context_json"])
        assert context["ma5_exit_timeframe"] == "5m"
        assert context["five_minute_ma5_core_hold"] is True
        assert context["entry_classification_category"] == "true_endpoint_reversal"
        assert context["entry_classification_label"] == "真正低位扫底反转做多"
        assert context["sweep_trial_promoted"] is True
    finally:
        store.close()


def test_old_endpoint_pair_cannot_relabel_later_continuation_trade():
    lifecycle = {"signal_time": "2026-09-16T12:22:00Z"}
    old_pair = {"confirmed_bar_time": "2026-09-16T11:48:00Z"}
    fresh_pair = {"confirmed_bar_time": "2026-09-16T12:20:00Z"}
    assert not validation_execution.lifecycle_pair_is_fresh_for_promotion(
        lifecycle, old_pair)
    assert validation_execution.lifecycle_pair_is_fresh_for_promotion(
        lifecycle, fresh_pair)


def test_closed_local_trial_does_not_block_the_next_distinct_reversal_shape(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        store.open_trade_lifecycle(
            trade_uid="first-local-trial", strategy_id="strategy_01",
            strategy_version=validation_execution.VALIDATION_VERSION,
            instrument="ETH-USDT-SWAP", direction=1,
            signal_time="2026-09-08T00:02:00+08:00", signal_reason="first shape",
            signal_context={"ma5_exit_timeframe": "5m"}, order_id="order-1",
            algo_id="", entry_reference=2470, stop_price=2466,
            trailing_activation=2476, trailing_callback=1,
            branch="five_minute_bottom_local_reversal_half_cover_long",
        )
        assert store.has_open_same_side_trade(
            "strategy_01", "ETH-USDT-SWAP", 1,
            strategy_version=validation_execution.VALIDATION_VERSION)
        store.close_trade_lifecycle(
            "first-local-trial", close_price=2471, gross_pnl=.01,
            total_fees=0.0, exit_reason="five_minute_ma5_take_profit")
        assert not store.has_open_same_side_trade(
            "strategy_01", "ETH-USDT-SWAP", 1,
            strategy_version=validation_execution.VALIDATION_VERSION)
    finally:
        store.close()


def test_endpoint_parent_can_cover_or_stack_multiple_children(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "one-4", "1m", -1, "2026-09-07T02:00:00+00:00", 110)
        _record(store, "one-5", "1m", -1, "2026-09-07T02:04:00+00:00", 111)
        _record(store, "five-high", "5m", -1, "2026-09-07T02:05:00+00:00", 112)
        rows = {row["event_key"]: row for row in store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
        assert "one-5" not in rows  # same persistent endpoint zone, newer low/high updates it
        assert rows["one-4"]["parent_event_key"] == "five-high"
        store.mark_ma_endpoint_trade_upgrades(["one-4"], ["trade-b", "trade-a"])
        rows = {row["event_key"]: row for row in store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
        assert rows["one-4"]["upgraded_trade_uids_json"] == '["trade-a", "trade-b"]'
    finally:
        store.close()


def test_latest_top_lock_releases_prior_bottom_and_owns_downtrend(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "one-bottom", "1m", 1,
                "2026-09-08T04:20:00+08:00", 2486, half=False)
        _record(store, "five-bottom", "5m", 1,
                "2026-09-08T04:25:00+08:00", 2487, half=True, slow=True)
        assert store.latest_active_one_five_pair("ETH-USDT-SWAP")["direction"] == 1

        _record(store, "one-top", "1m", -1,
                "2026-09-08T05:14:00+08:00", 2496, half=False)
        _record(store, "five-top", "5m", -1,
                "2026-09-08T05:15:00+08:00", 2495, half=True, slow=True)
        active = store.latest_active_one_five_pair("ETH-USDT-SWAP")
        rows = {row["event_key"]: row for row in
                store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
        assert active["event_key"] == "one-top"
        assert active["direction"] == -1
        assert rows["one-top"]["zone_status"] == "confirmed"
        assert rows["one-bottom"]["zone_status"] == "released"
        assert store.latest_confirmed_one_five_pair(
            "ETH-USDT-SWAP", 1, require_ma5_cross=False) is None
    finally:
        store.close()


def test_closed_five_minute_endpoint_locks_direction_without_one_minute_pair(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "five-top-only", "5m", -1,
                "2026-09-08T13:15:00+00:00", 2473, half=False, slow=False)
        active = store.latest_active_endpoint_direction_lock("ETH-USDT-SWAP")
        assert active is not None
        assert active["event_key"] == "five-top-only"
        assert active["direction"] == -1
        assert active["identity_code"] == "single_timeframe_direction_lock"

        _record(store, "five-bottom-only", "5m", 1,
                "2026-09-08T13:40:00+00:00", 2445, half=False, slow=False)
        active = store.latest_active_endpoint_direction_lock("ETH-USDT-SWAP")
        rows = {row["event_key"]: row for row in
                store.recent_ma_endpoint_lineage("ETH-USDT-SWAP")}
        assert active["event_key"] == "five-bottom-only"
        assert active["direction"] == 1
        assert rows["five-top-only"]["zone_status"] == "released"
    finally:
        store.close()


def test_single_higher_timeframe_endpoints_lock_child_trends_without_pairs(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        _record(store, "hour-top-only", "1H", -1,
                "2026-09-08T10:00:00+00:00", 2510, half=False, slow=False)
        fifteen_lock = store.latest_active_endpoint_direction_lock(
            "ETH-USDT-SWAP", "15m")
        assert fifteen_lock is not None
        assert fifteen_lock["event_key"] == "hour-top-only"
        assert fifteen_lock["direction"] == -1

        _record(store, "fifteen-top-only", "15m", -1,
                "2026-09-08T11:15:00+00:00", 2495, half=False, slow=False)
        five_lock = store.latest_active_endpoint_direction_lock(
            "ETH-USDT-SWAP", "5m")
        assert five_lock is not None
        assert five_lock["event_key"] == "fifteen-top-only"
        assert five_lock["direction"] == -1
        assert store.latest_active_endpoint_direction_lock(
            "ETH-USDT-SWAP", "1m") is None

        _record(store, "five-bottom-newer", "5m", 1,
                "2026-09-08T11:30:00+00:00", 2470, half=False, slow=False)
        one_lock = store.latest_active_endpoint_direction_lock(
            "ETH-USDT-SWAP", "1m")
        assert one_lock["event_key"] == "five-bottom-newer"
        assert one_lock["direction"] == 1
        assert store.latest_active_endpoint_direction_lock(
            "ETH-USDT-SWAP", "15m")["direction"] == -1
    finally:
        store.close()
