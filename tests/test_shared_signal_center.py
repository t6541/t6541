import sqlite3

import pandas as pd

from quantbot.shared_signal_center import (
    DOWNTREND_PULLBACK_REJECT,
    ONE_MINUTE_MA20_CROSS_DOWN,
    PRESSURE_SHORT_SIGNAL,
    TOP_BEARISH_ACCUMULATION,
    _downtrend_pullback_reject,
    _resolve_pattern_outcomes,
    _top_bearish_accumulation,
    scan_shared_advance_signals,
)
from quantbot.state import StateStore


def _pressure_short_frames():
    fifteen_dates = pd.date_range("2026-08-17", periods=13, freq="15min", tz="UTC")
    fifteen_close = pd.Series([101, 102, 103, 104, 105, 106, 107, 108, 107, 106, 107, 106, 104])
    fifteen = pd.DataFrame({
        "date": fifteen_dates,
        "open": fifteen_close - .2,
        "high": fifteen_close + 1.5,
        "low": fifteen_close - 1.0,
        "close": fifteen_close,
        "volume": 100.0,
    })
    fifteen.loc[7, "high"] = 111.0

    five_dates = pd.date_range("2026-08-17 02:00", periods=25, freq="5min", tz="UTC")
    five_close = pd.Series([104 + i * .12 for i in range(19)] + [106.5, 107.0, 107.5, 106.8, 105.0, 104.5])
    five = pd.DataFrame({
        "date": five_dates,
        "open": five_close + .15,
        "high": five_close + .8,
        "low": five_close - .8,
        "close": five_close,
        "volume": 100.0,
    })
    five.loc[21, "high"] = 109.0

    one_dates = pd.date_range("2026-08-17 03:00", periods=32, freq="min", tz="UTC")
    one_close = pd.Series([105.0 + (i % 3) * .05 for i in range(25)] + [105.2, 105.1, 105.0, 103.2, 103.0, 102.8, 102.7])
    one = pd.DataFrame({
        "date": one_dates,
        "open": one_close + .05,
        "high": one_close + .35,
        "low": one_close - .35,
        "close": one_close,
        "volume": 100.0,
    })
    one.loc[28, "open"] = 105.3
    return fifteen, five, one


def test_advance_scan_records_cross_and_deduplicates_executable_signal(tmp_path):
    path = tmp_path / "shared-scan.sqlite3"
    fifteen, five, one = _pressure_short_frames()
    store = StateStore(path)

    first = scan_shared_advance_signals(
        store,
        instrument="ETH-USDT-SWAP",
        one_minute=one,
        five_minute=five,
        fifteen_minute=fifteen,
    )
    second = scan_shared_advance_signals(
        store,
        instrument="ETH-USDT-SWAP",
        one_minute=one,
        five_minute=five,
        fifteen_minute=fifteen,
    )
    store.close()

    assert first.recorded_candidates >= 1
    assert first.actionable_signals == 1
    assert second.recorded_candidates == 0
    assert second.actionable_signals == 0

    connection = sqlite3.connect(path)
    signal_types = {
        row[0] for row in connection.execute(
            "SELECT DISTINCT signal_type FROM shared_signal_events"
        )
    }
    connection.close()
    assert ONE_MINUTE_MA20_CROSS_DOWN in signal_types
    assert PRESSURE_SHORT_SIGNAL in signal_types


def _one_minute_frame(closes):
    close = pd.Series(closes, dtype=float)
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-23", periods=len(close), freq="min", tz="UTC"),
        "open": close.shift(1).fillna(close.iloc[0] - .1),
        "high": close + .18,
        "low": close - .18,
        "close": close,
        "volume": 100.0,
    })
    return frame


def test_top_bearish_candles_are_accumulated_instead_of_testing_only_one_bar():
    closes = [100 + index * .20 for index in range(25)] + [105.4, 104.9, 105.0, 104.5]
    one = _one_minute_frame(closes)
    one.loc[25, ["open", "high", "close"]] = [104.8, 105.65, 105.4]
    one.loc[26, ["open", "close"]] = [105.35, 104.9]
    one.loc[27, ["open", "close"]] = [104.9, 105.0]
    one.loc[28, ["open", "close"]] = [104.95, 104.5]

    active, reason, stop, features = _top_bearish_accumulation(one)

    assert active is True
    assert "累计转弱" in reason
    assert features["bearish_count"] == 2
    assert stop > one["high"].tail(4).max()


def test_single_bearish_bar_does_not_become_top_accumulation_signal():
    closes = [100 + index * .20 for index in range(27)] + [105.6, 105.2]
    one = _one_minute_frame(closes)
    one.loc[28, ["open", "close"]] = [105.7, 105.2]
    assert _top_bearish_accumulation(one)[0] is False


def test_downtrend_bullish_pullback_followed_by_bearish_rejection_uses_local_stop():
    closes = [105 - index * .25 for index in range(28)] + [98.3, 97.55]
    one = _one_minute_frame(closes)
    one.loc[28, ["open", "high", "low", "close"]] = [97.5, 98.5, 97.4, 98.3]
    one.loc[29, ["open", "high", "low", "close"]] = [98.25, 98.35, 97.4, 97.55]

    active, reason, stop, features = _downtrend_pullback_reject(one)

    assert active is True
    assert DOWNTREND_PULLBACK_REJECT in {
        DOWNTREND_PULLBACK_REJECT, TOP_BEARISH_ACCUMULATION
    }
    assert "反抽失败" in reason
    assert stop > one["high"].tail(6).max()
    assert stop < 100


def test_pattern_observation_is_persistent_and_deduplicated(tmp_path):
    store = StateStore(tmp_path / "patterns.sqlite3")
    kwargs = dict(
        event_key="ETH-USDT-SWAP|top_bearish_accumulation|-1|2026-08-23T00:28:00+00:00",
        instrument="ETH-USDT-SWAP", pattern_type=TOP_BEARISH_ACCUMULATION,
        direction=-1, confirmed_bar_time="2026-08-23T00:28:00+00:00",
        status="actionable", entry_reference=104.5, stop_reference=105.8,
        features={"bearish_count": 2},
    )
    assert store.record_market_pattern(**kwargs) is True
    assert store.record_market_pattern(**kwargs) is False
    row = store.connection.execute("SELECT * FROM market_pattern_observations").fetchone()
    assert row["outcome_status"] == "pending"
    assert '"bearish_count": 2' in row["features_json"]
    store.close()


def test_pattern_experience_gets_fixed_horizon_outcome_without_online_tuning(tmp_path):
    store = StateStore(tmp_path / "outcomes.sqlite3")
    confirmed = "2026-08-23T00:05:00+00:00"
    store.record_market_pattern(
        event_key="pattern-1", instrument="ETH-USDT-SWAP",
        pattern_type=TOP_BEARISH_ACCUMULATION, direction=-1,
        confirmed_bar_time=confirmed, status="actionable",
        entry_reference=100.0, stop_reference=101.0, features={"bearish_count": 2},
    )
    one = _one_minute_frame([100.0] * 6 + [99.8 - index * .1 for index in range(15)])
    one.loc[6:, "low"] = one.loc[6:, "close"] - .2
    _resolve_pattern_outcomes(store, "ETH-USDT-SWAP", one)
    row = store.connection.execute(
        "SELECT outcome_status,outcome_json FROM market_pattern_observations WHERE event_key='pattern-1'"
    ).fetchone()
    assert row["outcome_status"] == "favorable_1r"
    assert '"horizon_bars": 15' in row["outcome_json"]
    store.close()
