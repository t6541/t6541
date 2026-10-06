from datetime import datetime, timedelta, timezone

import pandas as pd

from quantbot.trade_cycle_rules import CYCLE_RULE_SECTIONS
from quantbot.validation_execution import (
    first_stage_top_short_candidate,
    fresh_confirmed_bottom_releases_old_top,
    fresh_confirmed_top_releases_old_bottom,
    latest_top_anchor_time,
    recent_frozen_cover_supports_fresh_turn,
)


def test_latest_top_anchor_orders_mixed_database_string_and_live_timestamp():
    live = pd.Timestamp("2026-09-15T13:08:00Z")
    stored = "2026-09-15T13:09:00+00:00"
    assert latest_top_anchor_time([live, stored]) == pd.Timestamp(stored)
    assert latest_top_anchor_time([None]) is None


def test_six_cycle_cards_each_have_distinct_conditions_and_protection():
    assert [name for name, _ in CYCLE_RULE_SECTIONS] == [
        "局部底部做多", "局部顶部做空", "真正底部做多", "真正顶部做空",
        "上涨趋势回踩追多", "下跌趋势反抽追空",
    ]
    assert all(len(rows) >= 4 and all(len(row) == 3 for row in rows)
               for _, rows in CYCLE_RULE_SECTIONS)


def test_new_confirmed_top_releases_older_bottom_only_with_live_cover_and_downtrend():
    old = datetime(2026, 9, 15, 13, 1, tzinfo=timezone.utc)
    top = old + timedelta(minutes=7)
    base = dict(five_cover=True, old_bias=1, fifteen_direction=-1,
                hour_direction=-1, latest_top_time=top, old_lock_time=old)
    assert fresh_confirmed_top_releases_old_bottom(**base)
    assert not fresh_confirmed_top_releases_old_bottom(**{**base, "five_cover": False})
    assert fresh_confirmed_top_releases_old_bottom(**{**base, "hour_direction": 1})
    assert fresh_confirmed_top_releases_old_bottom(**{**base, "fifteen_direction": 1})
    assert not fresh_confirmed_top_releases_old_bottom(**{**base, "latest_top_time": old})
    assert fresh_confirmed_bottom_releases_old_top(
        five_cover=True, old_bias=-1, latest_bottom_time=top, old_lock_time=old)
    assert not fresh_confirmed_bottom_releases_old_top(
        five_cover=False, old_bias=-1, latest_bottom_time=top, old_lock_time=old)
    assert not fresh_confirmed_bottom_releases_old_top(
        five_cover=True, old_bias=-1, latest_bottom_time=old, old_lock_time=old)


def test_first_stage_top_probe_requires_fresh_anchor_at_ma5_outer_edge():
    now = datetime(2026, 9, 15, 13, 8, tzinfo=timezone.utc)
    closes = [100 + i * .05 for i in range(20)] + [100.75]
    opens = [value - .1 for value in closes[:-1]] + [100.95]
    frame = pd.DataFrame({
        "date": [now - timedelta(minutes=20-i) for i in range(21)],
        "open": opens, "close": closes,
        "high": [max(o, c) + .2 for o, c in zip(opens, closes)],
        "low": [min(o, c) - .2 for o, c in zip(opens, closes)],
    })
    anchor = [{"direction": -1, "stage": "confirmed_local_top", "time": now}]
    ok, _, stop = first_stage_top_short_candidate(
        anchor, frame, fifteen_direction=-1, hour_direction=-1)
    assert ok and stop > float(frame.iloc[-1]["close"])
    assert not first_stage_top_short_candidate(
        [], frame, fifteen_direction=-1, hour_direction=-1)[0]
    assert not first_stage_top_short_candidate(
        anchor, frame, fifteen_direction=1, hour_direction=-1)[0]


def test_frozen_five_minute_cover_can_release_old_lock_for_fresh_one_minute_bottom():
    start = pd.Timestamp("2026-09-16T08:10:00Z")
    frame = pd.DataFrame({
        "date": [start + pd.Timedelta(minutes=i) for i in range(6)],
        "open": [2385.0, 2385.8, 2386.1, 2386.2, 2386.4, 2387.0],
        "close": [2385.8, 2386.1, 2386.2, 2386.4, 2387.0, 2388.0],
        "low": [2384.7, 2385.4, 2385.8, 2386.0, 2386.2, 2386.7],
        "high": [2386.0, 2386.4, 2386.5, 2386.7, 2387.2, 2388.2],
    })
    anchor = {"five_bar_time": start.isoformat(),
              "one_anchor_time": (start + pd.Timedelta(minutes=2)).isoformat(),
              "stop_reference": 2384.25}
    kwargs = dict(anchor=anchor, one_minute=frame, direction=1,
                  latest_turn_time=start + pd.Timedelta(minutes=5),
                  old_lock_time=start - pd.Timedelta(minutes=25))
    assert recent_frozen_cover_supports_fresh_turn(**kwargs)
    assert not recent_frozen_cover_supports_fresh_turn(
        **{**kwargs, "latest_turn_time": start + pd.Timedelta(minutes=1)})
    broken = frame.copy()
    broken.loc[3, "low"] = 2384.2
    assert not recent_frozen_cover_supports_fresh_turn(
        **{**kwargs, "one_minute": broken})
    assert not recent_frozen_cover_supports_fresh_turn(
        **{**kwargs, "one_minute": pd.concat([frame, frame.iloc[-1:].assign(
            date=[start + pd.Timedelta(minutes=11)])])})
