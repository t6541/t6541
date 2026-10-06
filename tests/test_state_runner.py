from datetime import date

from quantbot.intraday import DailyRiskState
from quantbot.state import SignalIntent, StateStore
from quantbot.shared_signal_center import publish_extreme_signal, recover_extreme_signal
from quantbot.runner import observation_loop, retry_delay_seconds


def test_ma_cross_records_are_deduplicated_and_comparable(tmp_path):
    store = StateStore(tmp_path / "crosses.sqlite3")
    payload = dict(
        instrument="ETH-USDT-SWAP", timeframe="1m",
        cross_time="2026-08-29T13:50:00+00:00", direction=1,
        cross_name="小金叉", price=2434.2, range_position=.22,
        valid_local_extreme=True, decision="有效候选", reason="局部低点",
    )
    assert store.record_ma_cross(**payload)
    assert not store.record_ma_cross(**payload)
    row = store.latest_ma_cross("ETH-USDT-SWAP", "1m", 1)
    assert row is not None and row["cross_name"] == "小金叉"


def test_state_store_restores_daily_risk(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = StateStore(path)
    expected = DailyRiskState(-.002, 1, 2, False)
    store.save_daily_risk(expected, date(2026, 8, 9))
    store.close()
    reopened = StateStore(path)
    assert reopened.load_daily_risk(date(2026, 8, 9)) == expected
    reopened.close()


def test_signal_intent_is_idempotent_across_restart(tmp_path):
    path = tmp_path / "state.sqlite3"
    intent = SignalIntent("ETH-USDT-SWAP", "2026-08-09T08:00:00", 1, "v1", .002)
    first = StateStore(path)
    assert first.record_intent(intent)
    first.close()
    second = StateStore(path)
    assert not second.record_intent(intent)
    second.close()


def test_shared_signal_is_persisted_once_and_recovered_by_other_strategy(tmp_path):
    store = StateStore(tmp_path / "shared.sqlite3")
    kwargs = dict(
        instrument="ETH-USDT-SWAP", strategy_id="strategy_03", direction=-1,
        confirmed_bar_time="2026-08-17T00:36:00+00:00", reason="高位扫高回落",
        stop_price=1893.77,
    )
    assert publish_extreme_signal(store, **kwargs)
    assert not publish_extreme_signal(store, **kwargs)
    row = recover_extreme_signal(
        store, instrument="ETH-USDT-SWAP", strategy_id="strategy_01")
    assert row is not None
    assert row["direction"] == -1
    assert row["source_strategy"] == "strategy_03"
    assert recover_extreme_signal(
        store, instrument="ETH-USDT-SWAP", strategy_id="strategy_03") is None
    store.close()


def test_three_order_limit_counter_is_mirrored_for_long_and_short(tmp_path):
    store = StateStore(tmp_path / "direction-limit.sqlite3")
    for direction, suffix in ((1, "long"), (-1, "short")):
        # An opposite-direction intent separates the two trend sequences.
        if direction < 0:
            separator = SignalIntent("ETH-USDT-SWAP", "2026-08-12T09:59:00", 1, "limit-v1", .002)
            assert store.record_intent(separator)
            store.update_intent_status(separator, "submitted")
        for index in range(3):
            intent = SignalIntent(
                "ETH-USDT-SWAP", f"2026-08-12T10:{index:02d}:00-{suffix}",
                direction, "limit-v1", .002,
            )
            assert store.record_intent(intent)
            store.update_intent_status(intent, "submitted")
        assert store.consecutive_submitted_direction_count("limit-v1", direction) == 3
    store.close()


def test_observation_loop_rejects_busy_polling(tmp_path):
    try:
        next(observation_loop(None, tmp_path / "state.sqlite3", interval_seconds=5, iterations=1))
    except ValueError as exc:
        assert "at least 30 seconds" in str(exc)
    else:
        raise AssertionError("busy polling must be rejected")


def test_retry_delay_is_exponential_and_capped():
    assert retry_delay_seconds(1) == 2
    assert retry_delay_seconds(2) == 4
    assert retry_delay_seconds(6) == 60
    assert retry_delay_seconds(20) == 60
