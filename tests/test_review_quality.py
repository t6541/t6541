import json
from types import SimpleNamespace

import pandas as pd
import pytest

from quantbot.review_quality import (estimated_roundtrip_points, profit_stop,
                                    protect_owned_profit, reversal_location)
from quantbot.state import StateStore
from quantbot.validation_execution import _reconcile_closed_validation_trades


def lifecycle(store, uid="E", version="old", direction=1):
    store.open_trade_lifecycle(
        trade_uid=uid, strategy_id="strategy_01", strategy_version=version,
        instrument="ETH-USDT-SWAP", direction=direction,
        signal_time="2026-09-10T00:00:00+00:00", signal_reason="test", signal_context={},
        order_id=uid, algo_id="", entry_reference=100, stop_price=98 if direction > 0 else 102,
        trailing_activation=105, trailing_callback=1)


def test_cost_estimate_and_mirrored_profit_protection():
    assert estimated_roundtrip_points(2500) == pytest.approx(2.75)
    for direction in (1, -1):
        stop = 100 - direction * 2
        assert profit_stop(100, stop, 100 + direction, direction) is None
        assert profit_stop(100, stop, 100 + direction * 1.2, direction) == pytest.approx(100 + direction * .11)
        assert profit_stop(100, stop, 100 + direction * 4, direction) == pytest.approx(100 + direction * 2.055)


def test_region_rejects_middle_and_accepts_fresh_edge_without_wait():
    frame = pd.DataFrame({"date": pd.date_range("2026-09-10", periods=20, freq="min"),
                          "open": [50.] * 20, "close": [50.] * 20,
                          "high": [60.] * 20, "low": [40.] * 20})
    frame.loc[0, "high"], frame.loc[1, "low"] = 100., 0.
    assert not reversal_location(frame, frame, 1)[0]
    assert not reversal_location(frame, frame, -1)[0]
    frame.loc[19, "low"] = 1
    assert reversal_location(frame, frame, 1)[0]
    frame.loc[19, "high"] = 99
    assert reversal_location(frame, frame, -1)[0]


def test_trial_claim_survives_reopen_and_new_anchor_allowed(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    assert store.claim_reversal_trial("ETH", 1, "anchor", "first")
    store.close()
    store = StateStore(path)
    assert not store.claim_reversal_trial("ETH", 1, "anchor", "different_version")
    assert store.claim_reversal_trial("ETH", 1, "new_anchor", "next")
    store.close()


def test_profit_stop_requires_exact_owner_and_never_loosens(tmp_path):
    store = StateStore(tmp_path / "s.db")
    lifecycle(store)
    calls = []
    client = SimpleNamespace(tighten_active_stop_loss=lambda a, p: calls.append(p) or {"code": "0", "data": [{"sCode": "0"}]})
    snapshot = {"positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "1"}],
                "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "algoId": "1",
                                 "algoClOrdId": "OTHER", "slTriggerPx": "98"}]}
    assert protect_owned_profit(client, store, snapshot, "ETH-USDT-SWAP", 104) == 0
    snapshot["algo_orders"][0]["algoClOrdId"] = "EP"
    assert protect_owned_profit(client, store, snapshot, "ETH-USDT-SWAP", 104) == 1
    assert calls == ["102.05"]
    assert protect_owned_profit(client, store, snapshot, "ETH-USDT-SWAP", 103) == 0
    store.close()


def test_failed_protection_keeps_original_and_records_failure(tmp_path):
    store = StateStore(tmp_path / "s.db")
    lifecycle(store)
    client = SimpleNamespace(tighten_active_stop_loss=lambda a, p: {"data": [{"sCode": "1"}]})
    order = {"instId": "ETH-USDT-SWAP", "posSide": "long", "algoId": "1",
             "algoClOrdId": "EP", "slTriggerPx": "98"}
    snapshot = {"positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "1"}],
                "algo_orders": [order]}
    assert protect_owned_profit(client, store, snapshot, "ETH-USDT-SWAP", 104) == 0
    assert order["slTriggerPx"] == "98"
    assert store.connection.execute("SELECT count(*) FROM events WHERE event_type='profit_protection_failed'").fetchone()[0] == 1
    store.close()


def test_cross_version_reconciliation_reserves_partial_fill_across_scans(tmp_path):
    store = StateStore(tmp_path / "s.db")
    lifecycle(store, "E1", "old")
    lifecycle(store, "E2", "new")
    fills = [
        {"clOrdId": "E1", "ts": "1000", "tradeId": "a", "fillSz": "1", "fee": "-.1"},
        {"clOrdId": "E2", "ts": "2000", "tradeId": "b", "fillSz": "1", "fee": "-.1"},
        {"clOrdId": "exit", "ts": "3000", "tradeId": "c", "fillSz": "2", "fee": "-.2",
         "fillPx": "101", "fillPnl": "2", "side": "sell", "posSide": "long"},
    ]
    client = SimpleNamespace(recent_fills=lambda i: fills)
    assert set(_reconcile_closed_validation_trades(store, client, "ETH-USDT-SWAP")) == {"E1", "E2"}
    rows = store.strategy_trade_lifecycles("ETH-USDT-SWAP")
    assert sum(r["net_pnl"] for r in rows) == pytest.approx(1.6)
    assert rows[0]["close_time"] == "1970-01-01T00:00:03+00:00"
    lifecycle(store, "E3")
    assert _reconcile_closed_validation_trades(store, client, "ETH-USDT-SWAP") == ()
    assert store.strategy_trade_lifecycles("ETH-USDT-SWAP")[-1]["status"] == "open"
    store.close()


def test_cover_requires_spatial_overlap_not_just_body_length():
    from quantbot.validation_execution import fresh_single_candle_half_cover
    frame = pd.DataFrame({"date": pd.date_range("2026-09-10", periods=2, freq="5min"),
                          "open": [100., 80.], "close": [90., 89.]})
    assert not fresh_single_candle_half_cover(frame, None, 1)[0]
    frame.loc[1, ["open", "close"]] = [90., 94.5]
    assert fresh_single_candle_half_cover(frame, None, 1)[0]
    frame.loc[1, "close"] = 94.2
    assert not fresh_single_candle_half_cover(frame, None, 1)[0]
    frame[["open", "close"]] = 200 - frame[["open", "close"]]
    assert not fresh_single_candle_half_cover(frame, None, -1)[0]
    frame.loc[1, "close"] = 105.5
    assert fresh_single_candle_half_cover(frame, None, -1)[0]


def test_fill_history_paginates_using_bill_id():
    from quantbot.okx import OkxDemoClient
    calls = []
    first = [{"billId": str(200-i), "tradeId": str(i)} for i in range(100)]
    def request(method, path, params, private):
        calls.append(dict(params))
        return {"data": first if len(calls) == 1 else [{"billId": "99", "tradeId": "101"}]}
    client = SimpleNamespace(_request=request)
    fills = OkxDemoClient.reconciliation_fills(client, "ETH-USDT-SWAP")
    assert len(fills) == 101 and calls[1]["after"] == "101"
