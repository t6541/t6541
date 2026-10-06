from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

import pandas as pd

from quantbot.config import load_config
from quantbot.validation_execution import (
    execute_five_second_top_short_tick,
    five_second_top_short_signal,
)


def top_rows(start="2026-09-19T10:00:00Z", offset=0):
    begin = pd.Timestamp(start) + pd.Timedelta(minutes=offset)
    rows = []
    for index in range(28):
        close = 100 + index * .2
        rows.append([begin + pd.Timedelta(minutes=index), close - .10,
                     close + .20, close - .25, close, 10])
    rows.append([begin + pd.Timedelta(minutes=28), 105.20, 106.00, 105.10, 105.80, 15])
    rows.append([begin + pd.Timedelta(minutes=29), 105.80, 105.90, 105.10, 105.20, 18])
    return rows


def lower_high_rows(bearish_count=1):
    """Secondary-high weakening that remains above MA5 and never engulfs."""
    begin = pd.Timestamp("2026-09-19T12:00:00Z")
    rows = []
    for index in range(22):
        close = 100 + index * .08
        rows.append([begin + pd.Timedelta(minutes=index), close - .04,
                     close + .16, close - .18, close, 10])
    # Primary high, retreat, then a lower-high pullback near/above MA5.
    rows.extend([
        [begin + pd.Timedelta(minutes=22), 101.68, 103.00, 101.60, 102.60, 14],
        [begin + pd.Timedelta(minutes=23), 102.60, 102.68, 101.80, 101.92, 14],
        [begin + pd.Timedelta(minutes=24), 101.92, 102.04, 101.62, 101.72, 12],
        [begin + pd.Timedelta(minutes=25), 101.72, 101.92, 101.68, 101.88, 10],
        [begin + pd.Timedelta(minutes=26), 101.88, 102.42, 101.84, 102.30, 13],
    ])
    reds = [
        [102.30, 102.38, 102.10, 102.22],
        [102.22, 102.28, 102.05, 102.14],
        [102.14, 102.20, 102.00, 102.06],
    ]
    for offset, (open_, high, low, close) in enumerate(reds[:bearish_count], 27):
        rows.append([begin + pd.Timedelta(minutes=offset), open_, high, low, close, 11])
    return rows


def frame(rows):
    return pd.DataFrame(rows, columns=(
        "date", "open", "high", "low", "close", "volume"))


def api_rows(rows):
    result = []
    for item in rows:
        stamp = int(pd.Timestamp(item[0]).timestamp() * 1000)
        result.append([str(stamp), *(str(value) for value in item[1:]),
                       "0", "0", "0"])
    return result


class PublicClient:
    def __init__(self, rows, five_minute_rows=None):
        self.rows = rows
        self.five_minute_rows = five_minute_rows or rows

    def current_candles(self, *_args, **kwargs):
        rows = self.five_minute_rows if kwargs.get("bar") == "5m" else self.rows
        return api_rows(rows)


class PrivateClient:
    opening_unit_contracts = "0.05"

    def __init__(self):
        self.orders = []

    def fast_entry_snapshot(self, _instrument):
        return {"positions": [], "orders": [], "algo_orders": []}

    def place_demo_market_order(self, side, contracts, stop, **kwargs):
        self.orders.append((side, contracts, stop, kwargs))
        return {"code": "0", "data": [{"ordId": "fast-order-1", "sCode": "0"}]}


def test_first_bearish_cover_creates_small_three_candle_stop():
    signal = five_second_top_short_signal(frame(top_rows()), frame(top_rows()))
    assert signal is not None
    assert signal.cover_ratio >= .45
    assert signal.stop > signal.entry
    assert signal.stop - signal.entry < 2.0 * signal.atr
    assert "T10:28:00" in signal.anchor_time


def test_each_new_local_top_has_an_independent_anchor():
    first = five_second_top_short_signal(frame(top_rows()), frame(top_rows()))
    second = five_second_top_short_signal(
        frame(top_rows(offset=5)), frame(top_rows(offset=5)))
    assert first is not None and second is not None
    assert first.anchor_time != second.anchor_time


def test_lower_high_first_weakening_fires_before_ma5_cross_without_engulfing():
    signal = five_second_top_short_signal(
        frame(lower_high_rows(1)), frame(lower_high_rows(1)))
    assert signal is not None
    assert signal.setup_type == "lower_high_throwback_weakening"
    assert signal.bearish_count == 1
    assert signal.cover_ratio < .45
    assert signal.entry > signal.ma5
    assert signal.primary_high > signal.secondary_high
    assert signal.primary_high - signal.secondary_high <= signal.atr * 2.0
    assert "T12:26:00" in signal.anchor_time


def test_second_bearish_keeps_anchor_and_third_is_blocked_after_ma_loss():
    first = five_second_top_short_signal(
        frame(lower_high_rows(1)), frame(lower_high_rows(1)))
    second = five_second_top_short_signal(
        frame(lower_high_rows(2)), frame(lower_high_rows(2)))
    third = five_second_top_short_signal(
        frame(lower_high_rows(3)), frame(lower_high_rows(3)))
    assert first is not None and second is not None
    assert first.setup_type == second.setup_type == "lower_high_throwback_weakening"
    assert first.bearish_count == 1 and second.bearish_count == 2
    assert first.anchor_time == second.anchor_time
    assert second.entry > second.ma5
    assert first.stop == second.stop
    assert second.stop - second.entry < 2.0 * second.atr
    # This third candle has already lost the fast averages; it is now the
    # downswing/bottom area and cannot open a new short.
    assert third is None


def test_fast_tick_submits_and_persists_latency_audit(tmp_path):
    cfg = load_config("configs/eth-trend.toml")
    cfg = replace(cfg, okx=replace(cfg.okx, validation_contracts=1))
    private = PrivateClient()
    database = tmp_path / "strategy.sqlite3"
    result = execute_five_second_top_short_tick(
        cfg, database, client=private, public_client=PublicClient(top_rows()))
    assert result.action == "submitted"
    assert result.direction == -1
    assert private.orders[0][0] == "sell"
    assert private.orders[0][3]["position_side"] == "short"
    with sqlite3.connect(database) as connection:
        event = connection.execute(
            "SELECT payload_json FROM events WHERE event_type='five_second_fast_short_submitted'"
        ).fetchone()
        lifecycle = connection.execute(
            "SELECT branch,signal_context_json FROM trade_lifecycle"
        ).fetchone()
    assert event is not None and '"within_five_seconds": true' in event[0]
    assert lifecycle[0] == "five_second_top_weakening_short"
    assert '"ma5_exit_timeframe": "1m"' in lifecycle[1]


def test_same_top_is_not_submitted_twice(tmp_path):
    cfg = load_config("configs/eth-trend.toml")
    private = PrivateClient()
    database = tmp_path / "strategy.sqlite3"
    first = execute_five_second_top_short_tick(
        cfg, database, client=private, public_client=PublicClient(top_rows()))
    assert first.action == "submitted"
    # Model the lifecycle being closed while the exact same top remains on the
    # screen.  Its anchor claim must still prevent another order.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE trade_lifecycle SET status='closed'")
        connection.commit()
    second = execute_five_second_top_short_tick(
        cfg, database, client=private, public_client=PublicClient(top_rows()))
    assert second.action in {"duplicate", "observe"}
    assert len(private.orders) == 1


def test_lower_high_requires_two_consecutive_live_scans(tmp_path):
    cfg = load_config("configs/eth-trend.toml")
    private = PrivateClient()
    database = tmp_path / "strategy.sqlite3"
    public = PublicClient(lower_high_rows(1))
    first = execute_five_second_top_short_tick(
        cfg, database, client=private, public_client=public)
    second = execute_five_second_top_short_tick(
        cfg, database, client=private, public_client=public)
    assert first.action == "observe"
    assert "下一秒复核" in first.reason
    assert second.action == "submitted"
    assert len(private.orders) == 1


def test_late_bearish_sequence_cannot_become_a_new_bottom_short():
    rows = lower_high_rows(3)
    # Destroy the first red's top-side weakening evidence while leaving the
    # later candles near MA5.  Later red candles must not manufacture a signal.
    rows[-3][4] = rows[-3][1] - .005
    signal = five_second_top_short_signal(frame(rows), frame(rows))
    assert signal is None


def test_fast_short_is_blocked_below_one_minute_ma20_at_bottom():
    rows = lower_high_rows(1)
    rows[-1][4] = 101.20
    signal = five_second_top_short_signal(frame(rows), frame(lower_high_rows(1)))
    assert signal is None


def test_fast_short_is_blocked_below_five_minute_ma_stack():
    one = lower_high_rows(1)
    five = lower_high_rows(1)
    for row in five[-5:]:
        row[1] += 2.0
        row[2] += 2.0
        row[3] += 2.0
        row[4] += 2.0
    signal = five_second_top_short_signal(frame(one), frame(five))
    assert signal is None


def test_distant_rebound_is_not_mislabeled_as_secondary_high():
    rows = lower_high_rows(1)
    rows[22][2] = 104.00
    signal = five_second_top_short_signal(frame(rows), frame(lower_high_rows(1)))
    assert signal is None
