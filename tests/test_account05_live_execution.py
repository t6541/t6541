from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantbot.account05_execution import (
    execute_account05_tick, _addon_ma5_exit_reason, _close_addon_on_ma5,
    _local_addon_net)
from quantbot.account05_signals import (
    Account05Signals, Account05Trigger, ExtremeRotationTrigger)
from quantbot.account05_live import Account05LiveClient
from quantbot.account05_state import Account05StateStore
from quantbot.account05_strategy import PositionSide, Trend15m
from quantbot.live_account_settings import LiveAccountSettings
from quantbot.live_audit import LiveAuditCredentials
from quantbot.okx import OkxError


def _market(values, freq="1min"):
    close = np.asarray(values, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq=freq),
        "open": close - .1, "high": close + 1, "low": close - 1,
        "close": close, "volume": 100,
    })


AUDIT = {
    "instrument": {"ctVal": "0.1", "lotSz": "0.01", "minSz": "0.01"},
    "market": {"markPx": "2500", "last": "2500"},
}


class FakeClient:
    def __init__(self, positions=None):
        self.positions = positions or []
        self.entries = []
        self.take_profits = []
        self.orders = {}
        self.counter = 0
        self.events = []

    def raw_snapshot(self):
        return {"positions": self.positions, "orders": []}

    def place_market_entry(self, **payload):
        self.counter += 1
        order_id = f"E{self.counter}"
        self.entries.append(payload)
        self.orders[order_id] = {
            "ordId": order_id, "state": "filled", "avgPx": "2500",
            "accFillSz": str(payload["size"]),
        }
        return {"ordId": order_id}

    def place_lot_take_profit(self, **payload):
        self.counter += 1
        order_id = f"T{self.counter}"
        self.take_profits.append(payload)
        self.events.append(("place_tp", order_id))
        self.orders[order_id] = {
            "ordId": order_id, "clOrdId": payload["client_order_id"],
            "state": "live", "accFillSz": "0"}
        return {"ordId": order_id}

    def place_market_reduce(self, **payload):
        self.counter += 1
        order_id = f"X{self.counter}"
        self.orders[order_id] = {
            "ordId": order_id, "state": "filled", "avgPx": "2500",
            "accFillSz": str(payload["size"]),
        }
        return {"ordId": order_id}

    def order(self, inst_id, *, order_id="", client_order_id=""):
        if order_id:
            order = self.orders[order_id]
            if order.get("state") == "canceled":
                from quantbot.okx import OkxError
                raise OkxError("OKX Live GET error 51603: Order does not exist")
            return order
        for order in self.orders.values():
            if order.get("clOrdId") == client_order_id:
                return order
        from quantbot.okx import OkxError
        raise OkxError("OKX Live GET error 51603: Order does not exist")

    def cancel_order(self, inst_id, order_id):
        self.events.append(("cancel", order_id))
        self.orders[order_id]["state"] = "canceled"
        return {"ordId": order_id, "sCode": "0"}

    def amend_order_price(self, inst_id, order_id, price, request_id):
        self.events.append(("amend", order_id))
        self.orders[order_id]["px"] = str(price)
        return {"ordId": order_id, "reqId": request_id, "sCode": "0"}


def test_local_addon_profit_uses_virtual_lot_entry_not_okx_side_average(tmp_path):
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = ledger.record_filled_lot(
        lot_id="local-long-addon", signal_id="local-long-addon",
        side=PositionSide.LONG, kind="addon", entry_order_id="entry",
        entry_price=Decimal("2600"), size=Decimal("0.01"),
        take_profit_price=Decimal("2600"))
    spec = SimpleNamespace(ct_val=Decimal("0.1"))

    # Even if OKX's merged long average is still above 2700, the local lot
    # attribution is positive from its own 2600 entry to its 2610 exit.
    assert _local_addon_net(lot, Decimal("2610"), spec) == Decimal("0.0073950")


def test_addon_exit_channel_hard_rejects_base_lot(tmp_path):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    base = ledger.record_filled_lot(
        lot_id="protected-base", signal_id="protected-base",
        side=PositionSide.LONG, kind="base", entry_order_id="base-entry",
        entry_price=Decimal("2700"), size=Decimal("0.12"),
        take_profit_price=Decimal("2713.50"),
        take_profit_pct=Decimal("0.005"))
    before = ledger.credit_recovery_profit(Decimal("1"))

    with pytest.raises(OkxError, match="基础仓只能由自身止盈单退出"):
        _close_addon_on_ma5(client, ledger, base, "不应执行")

    assert client.counter == 0
    assert ledger.get_lot(base.lot_id).status != "closed"
    assert ledger.recovery_pool() == before


def test_confirmed_unprotected_base_tp_is_restored_without_new_entry(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.36", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for index, size in enumerate((Decimal("0.22"), Decimal("0.14"))):
        ledger.record_filled_lot(
            lot_id=f"unprotected-base-{index}",
            signal_id=f"unprotected-base-{index}",
            side=PositionSide.LONG, kind="base",
            entry_order_id=f"entry-{index}", entry_price=Decimal("2500"),
            size=size, take_profit_price=Decimal("2512.50"),
            take_profit_pct=Decimal("0.005"))
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "震荡", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)

    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))

    assert result.action == "submitted"
    assert client.entries == []
    assert [item["size"] for item in client.take_profits] == [Decimal("0.14")]
    assert ledger.get_lot("unprotected-base-0").status == "reconcile_required"
    assert ledger.unprotected_lots() == []


def test_unprotected_base_tp_recovery_stops_when_exchange_cannot_cover_ledger(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.10", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    ledger.record_filled_lot(
        lot_id="uncovered-base", signal_id="uncovered-base",
        side=PositionSide.LONG, kind="base", entry_order_id="entry",
        entry_price=Decimal("2500"), size=Decimal("0.22"),
        take_profit_price=Decimal("2512.50"),
        take_profit_pct=Decimal("0.005"))
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "震荡", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)

    with pytest.raises(OkxError, match="不足以覆盖当前基础仓"):
        execute_account05_tick(
            client=client, ledger=ledger, settings=settings, audit=AUDIT,
            one=_market(flat), five=_market(flat, "5min"),
            fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert client.entries == []
    assert ledger.get_lot("uncovered-base").status == "filled_unprotected"
    assert client.take_profits == []


def test_zero_exchange_position_quarantines_stale_unprotected_base_and_rebuilds(
        monkeypatch, tmp_path):
    client = FakeClient([])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    stale = ledger.record_filled_lot(
        lot_id="flat-stale-base", signal_id="flat-stale-base",
        side=PositionSide.LONG, kind="base", entry_order_id="entry",
        entry_price=Decimal("2500"), size=Decimal("0.22"),
        take_profit_price=Decimal("2512.5"), take_profit_pct=Decimal("0.005"))
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "震荡", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert client.entries[0]["position_side"] == "long"
    assert client.entries[0]["size"] == Decimal("0.24")
    assert ledger.get_lot(stale.lot_id).status == "reconcile_required"


def test_stale_unprotected_duplicate_base_does_not_block_current_covered_base(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.14", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    old = ledger.record_filled_lot(
        lot_id="stale-unprotected", signal_id="stale-unprotected",
        side=PositionSide.LONG, kind="base", entry_order_id="old-entry",
        entry_price=Decimal("2400"), size=Decimal("0.22"),
        take_profit_price=Decimal("2412"), take_profit_pct=Decimal("0.005"))
    current = ledger.record_filled_lot(
        lot_id="current-unprotected", signal_id="current-unprotected",
        side=PositionSide.LONG, kind="base", entry_order_id="current-entry",
        entry_price=Decimal("2500"), size=Decimal("0.14"),
        take_profit_price=Decimal("2512.5"), take_profit_pct=Decimal("0.005"))
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "震荡", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert ledger.get_lot(old.lot_id).status == "reconcile_required"
    assert ledger.get_lot(current.lot_id).status == "tp_live"


def test_large_extreme_closes_profitable_same_direction_addon_then_opens_point_one_percent_reverse(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.27", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.24", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for side in (PositionSide.LONG, PositionSide.SHORT):
        base = ledger.record_filled_lot(
            lot_id=f"base-{side.value}", signal_id=f"base-{side.value}",
            side=side, kind="base", entry_order_id=f"base-entry-{side.value}",
            entry_price=Decimal("2500"), size=Decimal("0.24"),
            take_profit_price=(Decimal("2512.50") if side is PositionSide.LONG
                               else Decimal("2487.50")),
            take_profit_pct=Decimal("0.005"))
        ledger.attach_take_profit(base.lot_id, f"base-tp-{side.value}")
        client.orders[f"base-tp-{side.value}"] = {
            "ordId": f"base-tp-{side.value}", "state": "live", "accFillSz": "0"}
    addon = ledger.record_filled_lot(
        lot_id="profitable-long", signal_id="addon:old-long",
        side=PositionSide.LONG, kind="addon", entry_order_id="addon-entry",
        entry_price=Decimal("2400"), size=Decimal("0.01"),
        take_profit_price=Decimal("2410"))
    ledger.mark_addon_ma5_managed(addon.lot_id)
    loser = ledger.record_filled_lot(
        lot_id="losing-long", signal_id="addon:losing-long",
        side=PositionSide.LONG, kind="addon", entry_order_id="losing-entry",
        entry_price=Decimal("2600"), size=Decimal("0.02"),
        take_profit_price=Decimal("2610"))
    ledger.mark_addon_ma5_managed(loser.lot_id)
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(
            Trend15m.UNCLEAR, "震荡", (),
            ExtremeRotationTrigger(-1, "2026-01-01 01:00:00", "三周期大极值顶部")))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert ledger.get_lot(addon.lot_id).status != "closed"
    assert ledger.get_lot(loser.lot_id).status != "closed"
    assert ledger.order_intent(f"exit:{loser.lot_id}") is None
    profit_order = client.take_profits[-1]
    assert profit_order["price"] == Decimal("2410.01")
    profit_order_id = next(order_id for order_id, order in client.orders.items()
                           if order.get("clOrdId") == profit_order["client_order_id"])
    client.orders[profit_order_id].update(
        state="filled", avgPx="2410.01", accFillSz="0.01")
    execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert ledger.get_lot(addon.lot_id).status == "closed"
    assert ledger.get_lot(loser.lot_id).status != "closed"
    assert client.entries[-1]["position_side"] == "short"
    assert client.entries[-1]["size"] == Decimal("0.04")
    assert any(lot.kind == "addon" and lot.side is PositionSide.SHORT
               and lot.original_size == Decimal("0.04")
               for lot in ledger.open_lots())


@pytest.mark.parametrize(("direction", "reverse_side"), [
    (-1, PositionSide.SHORT),
    (1, PositionSide.LONG),
])
def test_large_extreme_opens_point_one_percent_reverse_without_releasable_source_lot(
        monkeypatch, tmp_path, direction, reverse_side):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(
            Trend15m.UNCLEAR, "震荡", (),
            ExtremeRotationTrigger(
                direction, "2026-01-01 01:00:00", "三周期大极值")))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)

    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))

    assert result.action == "submitted"
    assert "提交盈利小单限价止盈0笔" in result.reason
    assert client.entries[-1]["position_side"] == reverse_side.value
    assert client.entries[-1]["size"] == Decimal("0.04")


def test_large_extreme_reverse_is_not_blocked_by_ordinary_addon_slot_limit(
        monkeypatch, tmp_path):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for index in range(18):
        lot = ledger.record_filled_lot(
            lot_id=f"short-addon-{index}", signal_id=f"short-addon-{index}",
            side=PositionSide.SHORT, kind="addon",
            entry_order_id=f"short-entry-{index}",
            entry_price=Decimal("2600"), size=Decimal("0.01"),
            take_profit_price=Decimal("2590"))
        ledger.mark_addon_ma5_managed(lot.lot_id)
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(
            Trend15m.UNCLEAR, "震荡", (),
            ExtremeRotationTrigger(-1, "2026-01-01 01:00:00", "三周期大极值顶部")))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)

    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))

    assert result.action == "submitted"
    assert client.entries[-1]["position_side"] == "short"
    assert client.entries[-1]["size"] == Decimal("0.04")


def test_empty_account_opens_neutral_hedge_and_one_full_tp_per_side(tmp_path):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert [(item["position_side"], item["size"]) for item in client.entries] == [
        ("long", Decimal("0.24")), ("short", Decimal("0.24"))]
    assert len(client.take_profits) == 2
    assert {item["price"] for item in client.take_profits} == {
        Decimal("2512.50"), Decimal("2487.50")}
    assert all(lot.take_profit_algo_id for lot in ledger.open_lots())


def test_clear_trend_starts_both_fixed_base_sides_at_six_tenths(monkeypatch, tmp_path):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UP, "明确上涨", ()))
    flat = np.full(120, 2500.0)
    execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert [(item["position_side"], item["size"]) for item in client.entries] == [
        ("long", Decimal("0.24")), ("short", Decimal("0.24"))]
    assert len(client.take_profits) == 2
    assert {item["price"] for item in client.take_profits} == {
        Decimal("2517.50"), Decimal("2482.50")}
    assert all(lot.take_profit_pct == Decimal("0.007")
               for lot in ledger.open_lots() if lot.kind == "base")


def test_full_base_tp_reopens_even_when_same_side_addon_position_remains(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.04", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.48", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    long_base = ledger.record_filled_lot(
        lot_id="base:long:old-T1", signal_id="base:long:old-T1",
        side=PositionSide.LONG, kind="base", entry_order_id="long-base-entry",
        entry_price=Decimal("2500"), size=Decimal("0.24"),
        take_profit_price=Decimal("2512.50"), take_profit_pct=Decimal("0.005"))
    ledger.attach_take_profit(long_base.lot_id, "long-base-tp")
    client.orders["long-base-tp"] = {
        "ordId": "long-base-tp", "state": "filled", "accFillSz": "0.24"}
    short_base = ledger.record_filled_lot(
        lot_id="base:short:old-T1", signal_id="base:short:old-T1",
        side=PositionSide.SHORT, kind="base", entry_order_id="short-base-entry",
        entry_price=Decimal("2500"), size=Decimal("0.48"),
        take_profit_price=Decimal("2487.50"), take_profit_pct=Decimal("0.005"))
    ledger.attach_take_profit(short_base.lot_id, "short-base-tp")
    client.orders["short-base-tp"] = {
        "ordId": "short-base-tp", "state": "live", "accFillSz": "0"}
    addon = ledger.record_filled_lot(
        lot_id="addon:long:kept-T1", signal_id="addon:long:kept-T1",
        side=PositionSide.LONG, kind="addon", entry_order_id="addon-entry",
        entry_price=Decimal("2490"), size=Decimal("0.04"),
        take_profit_price=Decimal("2490"))
    ledger.mark_addon_ma5_managed(addon.lot_id)
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "震荡", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert [(item["position_side"], item["size"]) for item in client.entries] == [
        ("long", Decimal("0.24"))]
    assert ledger.base_rebuild_plan(PositionSide.LONG)["status"] == "stage1_filled"


@pytest.mark.parametrize(("trend", "expected_size"), [
    (Trend15m.UNCLEAR, Decimal("0.24")),
    (Trend15m.UP, Decimal("0.24")),
])
def test_missing_long_rebuilds_from_actual_short_total(
        monkeypatch, tmp_path, trend, expected_size):
    # Stage one is always 0.6%; a new precise structure owns the other 0.6%.
    client = FakeClient([{"posSide": "short", "pos": "0.48", "upl": "-0.9"}])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(trend, "test", ()))
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert [(item["position_side"], item["size"]) for item in client.entries] == [
        ("long", expected_size)]


def test_addon_only_exchange_position_does_not_suppress_missing_long_base(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.01", "upl": "0", "avgPx": "2702.43"},
        {"posSide": "short", "pos": "0.58", "upl": "0", "avgPx": "2683"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    base = ledger.record_filled_lot(
        lot_id="base:long:old", signal_id="base:long:old",
        side=PositionSide.LONG, kind="base", entry_order_id="base-entry",
        entry_price=Decimal("2775"), size=Decimal("0.30"),
        take_profit_price=Decimal("2788.87"), take_profit_pct=Decimal("0.005"))
    ledger.sync_take_profit_fill(base.lot_id, Decimal("0.15"))
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "震荡", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert any(item["position_side"] == "long" and item["size"] > Decimal("0.01")
               for item in client.entries)


def test_loss_recovery_first_rebuilds_missing_short_at_capped_twelve_tenths(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.72", "upl": "-4.5", "avgPx": "2773.84"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    base = ledger.record_filled_lot(
        lot_id="trapped-base", signal_id="trapped-base", side=PositionSide.LONG,
        kind="base", entry_order_id="base-entry", entry_price=Decimal("2773.84"),
        size=Decimal("0.54"), take_profit_price=Decimal("2787.71"),
        take_profit_pct=Decimal("0.005"))
    ledger.attach_take_profit(base.lot_id, "base-tp")
    client.orders["base-tp"] = {
        "ordId": "base-tp", "state": "live", "accFillSz": "0"}
    for index in range(6):
        lot = ledger.record_filled_lot(
            lot_id=f"trapped-addon-{index}", signal_id=f"trapped-addon-{index}",
            side=PositionSide.LONG, kind="addon",
            entry_order_id=f"addon-entry-{index}", entry_price=Decimal("2770"),
            size=Decimal("0.03"), take_profit_price=Decimal("2780"))
        ledger.mark_addon_ma5_managed(lot.lot_id)
    old_short = ledger.record_filled_lot(
        lot_id="closed-short-base", signal_id="closed-short-base",
        side=PositionSide.SHORT, kind="base", entry_order_id="closed-short-entry",
        entry_price=Decimal("2727"), size=Decimal("0.48"),
        take_profit_price=Decimal("2713"), take_profit_pct=Decimal("0.005"))
    ledger.apply_take_profit_fill(old_short.lot_id, old_short.original_size)
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.DOWN, "test", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    audit = {**AUDIT, "usdt_balance": {"eq": "95.5"}}
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=audit,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert [(item["position_side"], item["size"]) for item in client.entries] == [
        ("short", Decimal("0.24"))]
    recovery = ledger.recovery_program()
    assert recovery is not None and recovery["phase"] == "balance_short"
    assert ledger.recovery_trapped_open_count() == 7


def test_staged_rebuild_second_half_waits_for_new_precise_shape(monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.24", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.48", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    old = ledger.record_filled_lot(
        lot_id="old-long-base", signal_id="old-long-base",
        side=PositionSide.LONG, kind="base", entry_order_id="old-entry",
        entry_price=Decimal("2500"), size=Decimal("0.48"),
        take_profit_price=Decimal("2512.5"), take_profit_pct=Decimal("0.005"))
    ledger.apply_take_profit_fill(old.lot_id, old.original_size)
    stage_one = ledger.record_filled_lot(
        lot_id="stage-one", signal_id="stage-one", side=PositionSide.LONG,
        kind="base", entry_order_id="stage-one-entry", entry_price=Decimal("2500"),
        size=Decimal("0.24"), take_profit_price=Decimal("2512.5"),
        take_profit_pct=Decimal("0.005"))
    ledger.attach_take_profit(stage_one.lot_id, "stage-one-tp")
    client.orders["stage-one-tp"] = {
        "ordId": "stage-one-tp", "state": "live", "accFillSz": "0"}
    ledger.plan_base_rebuild(
        side=PositionSide.LONG, signal_key="planned-stage-one",
        target_margin_pct=Decimal("0.012"),
        first_margin_pct=Decimal("0.006"), take_profit_pct=Decimal("0.005"))
    ledger.set_base_rebuild_status(PositionSide.LONG, "stage1_filled")
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(
            Trend15m.UP, "test",
            (Account05Trigger("新的上涨回踩做多", 1, "bar-new", "test"),)))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "submitted"
    assert [(item["position_side"], item["size"]) for item in client.entries] == [
        ("long", Decimal("0.24"))]
    assert ledger.base_rebuild_plan(PositionSide.LONG)["status"] == "complete"


def test_older_stale_base_rows_do_not_trigger_duplicate_rebuild(monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "short", "pos": "0.24", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for lot_id, size in (("old-short-base", Decimal("0.48")),
                         ("current-short-base", Decimal("0.24"))):
        lot = ledger.record_filled_lot(
            lot_id=lot_id, signal_id=lot_id, side=PositionSide.SHORT,
            kind="base", entry_order_id=f"{lot_id}-entry",
            entry_price=Decimal("2500"), size=size,
            take_profit_price=Decimal("2487.5"),
            take_profit_pct=Decimal("0.005"))
        ledger.attach_take_profit(lot.lot_id, f"{lot_id}-tp")
        client.orders[f"{lot_id}-tp"] = {
            "ordId": f"{lot_id}-tp", "state": "live", "accFillSz": "0"}
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "震荡", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)

    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))

    assert not any(item["position_side"] == "short" for item in client.entries)


def test_twenty_percent_combined_loss_freezes_every_new_order(tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.4", "upl": "-11"},
        {"posSide": "short", "pos": "0.2", "upl": "-9"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "loss_limit_blocked"
    assert "-20.00 USDT" in result.reason
    assert "-20.0000 USDT" in result.reason
    assert client.entries == []


def test_same_persistent_shape_does_not_fill_toward_six_lot_limit(monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.2", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.2", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    signal = Account05Signals(
        Trend15m.DOWN, "test",
        (Account05Trigger("下跌趋势反抽追空", -1, "bar-1", "test"),),
    )
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: signal,
    )
    flat = np.full(120, 2500.0)
    kwargs = dict(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"),
    )
    first = execute_account05_tick(**kwargs)
    second = execute_account05_tick(**kwargs)
    assert first.action == "submitted"
    assert second.action == "observe"
    assert len(client.entries) == 1
    assert ledger.open_addon_count(PositionSide.SHORT) == 1


def test_different_long_identities_cannot_both_fill_in_same_five_minute_bar(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.2", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.2", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    holder = {"signal": Account05Signals(
        Trend15m.UP, "test",
        (Account05Trigger("真正底部做多", 1, "13:10", "test"),))}
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: holder["signal"])
    flat = np.full(120, 2500.0)
    kwargs = dict(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    execute_account05_tick(**kwargs)
    holder["signal"] = Account05Signals(
        Trend15m.UP, "test",
        (Account05Trigger("局部底部做多", 1, "13:12", "test"),))
    second = execute_account05_tick(**kwargs)
    assert second.action == "observe"
    assert len(client.entries) == 1


def test_ma20_block_early_entry_keeps_reversal_fallback_unclaimed(monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.2", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.2", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    signal = Account05Signals(Trend15m.UP, "test", (
        Account05Trigger("5分钟MA20订单块回踩早触发追多", 1, "13:10", "test"),
        Account05Trigger("反转三阶段补漏做多", 1, "13:10", "test")))
    monkeypatch.setattr("quantbot.account05_execution.evaluate_account05_signals",
                        lambda *_args, **_kwargs: signal)
    flat = np.full(120, 2500.0)
    kwargs = dict(client=client, ledger=ledger, settings=settings, audit=AUDIT,
                  one=_market(flat), five=_market(flat, "5min"),
                  fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    result = execute_account05_tick(**kwargs)
    assert result.action == "submitted"
    assert len(client.entries) == 1
    assert ledger.claim_new_signal_shapes(
        (("反转三阶段补漏做多", PositionSide.LONG, "13:10"),),
        "13:10", "13:10") == {("反转三阶段补漏做多", PositionSide.LONG)}


@pytest.mark.parametrize("identity", (
    "5分钟高位拒绝早触发追空", "5分钟超级趋势首次翻空追空"))
def test_five_minute_top_rejection_uses_recovery_short_slot(
        monkeypatch, tmp_path, identity):
    client = FakeClient([
        {"posSide": "long", "pos": "0.2", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.2", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    signal = Account05Signals(Trend15m.UP, "test", (
        Account05Trigger(identity, -1, "13:10", "test"),))
    monkeypatch.setattr("quantbot.account05_execution.evaluate_account05_signals",
                        lambda *_args, **_kwargs: signal)
    flat = np.full(120, 2500.0)
    kwargs = dict(client=client, ledger=ledger, settings=settings, audit=AUDIT,
                  one=_market(flat), five=_market(flat, "5min"),
                  fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    first = execute_account05_tick(**kwargs)
    second = execute_account05_tick(**kwargs)
    assert first.action == "submitted" and second.action == "observe"
    assert ledger.open_recovery_slot_count(PositionSide.SHORT) == 1
    assert len(client.entries) == 1


def test_eighteen_open_addons_is_capacity_wait_not_twenty_percent_stop(monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.2", "upl": "0.02"},
        {"posSide": "short", "pos": "0.2", "upl": "-0.34"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for index in range(18):
        lot_id = f"old-{index}"
        ledger.record_filled_lot(
            lot_id=lot_id, signal_id=lot_id, side=PositionSide.SHORT,
            kind="addon", entry_order_id=f"entry-{index}",
            entry_price=Decimal("2500"), size=Decimal("0.01"),
            take_profit_price=Decimal("2490"))
        tp_id = f"tp-{index}"
        ledger.attach_take_profit(lot_id, tp_id)
        client.orders[tp_id] = {"ordId": tp_id, "state": "live", "accFillSz": "0"}
    signal = Account05Signals(
        Trend15m.DOWN, "test",
        (Account05Trigger("新的局部顶部做空", -1, "bar-new", "test"),),
    )
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: signal,
    )
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    assert result.action == "capacity_blocked"
    assert "18个循环槽位" in result.reason
    assert "20%" not in result.reason
    assert client.entries == []


def test_addon_tp_close_reprices_same_side_base_tp_new_first(monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.21", "upl": "0", "avgPx": "2450"},
        {"posSide": "short", "pos": "0.20", "upl": "0", "avgPx": "2550"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for tier, price in ((1, "2512.50"), (2, "2517.50")):
        lot_id = f"base:long:bar-T{tier}"
        ledger.record_filled_lot(
            lot_id=lot_id, signal_id=lot_id, side=PositionSide.LONG,
            kind="base", entry_order_id=f"base-entry-{tier}",
            entry_price=Decimal("2500"), size=Decimal("0.10"),
            take_profit_price=Decimal(price))
        old_id = f"base-old-{tier}"
        ledger.attach_take_profit(lot_id, old_id)
        client.orders[old_id] = {"ordId": old_id, "state": "live", "accFillSz": "0"}
    addon_id = "addon:long:shape-T1"
    ledger.record_filled_lot(
        lot_id=addon_id, signal_id=addon_id, side=PositionSide.LONG,
        kind="addon", entry_order_id="addon-entry", entry_price=Decimal("2440"),
        size=Decimal("0.01"), take_profit_price=Decimal("2450"))
    ledger.attach_take_profit(addon_id, "addon-tp")
    client.orders["addon-tp"] = {
        "ordId": "addon-tp", "state": "filled", "accFillSz": "0.01"}
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "test", ()),
    )
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))

    assert client.orders["base-old-1"]["px"] == "2462.25"
    assert client.orders["base-old-2"]["px"] == "2462.25"
    assert ("amend", "base-old-1") in client.events
    assert ("amend", "base-old-2") in client.events
    assert ledger.get_lot("base:long:bar-T1").take_profit_algo_id == "base-old-1"
    assert ledger.get_lot("base:long:bar-T2").take_profit_algo_id == "base-old-2"
    assert ledger.get_lot(addon_id).status == "closed"


def test_addon_entry_fill_immediately_reprices_same_side_base_tp(monkeypatch, tmp_path):
    class AverageMovingClient(FakeClient):
        def place_market_entry(self, **payload):
            order = super().place_market_entry(**payload)
            for position in self.positions:
                if position.get("posSide") == payload["position_side"]:
                    position["avgPx"] = "2475"
            return order

    client = AverageMovingClient([
        {"posSide": "long", "pos": "0.20", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.20", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for tier, price in ((1, "2512.50"), (2, "2517.50")):
        lot_id = f"base:long:bar-T{tier}"
        ledger.record_filled_lot(
            lot_id=lot_id, signal_id=lot_id, side=PositionSide.LONG,
            kind="base", entry_order_id=f"base-entry-{tier}",
            entry_price=Decimal("2500"), size=Decimal("0.10"),
            take_profit_price=Decimal(price))
        old_id = f"base-old-{tier}"
        ledger.attach_take_profit(lot_id, old_id)
        client.orders[old_id] = {"ordId": old_id, "state": "live", "accFillSz": "0"}
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(
            Trend15m.UP, "test",
            (Account05Trigger("新的局部底部做多", 1, "bar-new", "test"),)),
    )
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    flat = np.full(120, 2500.0)
    result = execute_account05_tick(
        client=client, ledger=ledger, settings=settings, audit=AUDIT,
        one=_market(flat), five=_market(flat, "5min"),
        fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))

    assert result.action == "submitted"
    assert client.orders["base-old-1"]["px"] == "2492.33"
    assert client.orders["base-old-2"]["px"] == "2492.33"
    assert ("amend", "base-old-1") in client.events
    assert ("amend", "base-old-2") in client.events


def test_legacy_auto_canceled_new_tp_falls_back_to_amending_old_tp(tmp_path):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot_id = "base:long:legacy-T2"
    ledger.record_filled_lot(
        lot_id=lot_id, signal_id=lot_id, side=PositionSide.LONG,
        kind="base", entry_order_id="entry", entry_price=Decimal("2750"),
        size=Decimal("0.11"), take_profit_price=Decimal("2763.73"))
    ledger.attach_take_profit(lot_id, "old-tp")
    client.orders["old-tp"] = {
        "ordId": "old-tp", "state": "live", "accFillSz": "0", "px": "2763.73"}
    ledger.plan_take_profit_replacement(
        lot_id, old_order_id="old-tp", new_client_order_id="A5RPLEGACY",
        new_price=Decimal("2764.63"))
    ledger.mark_replacement_new_live(lot_id, "auto-canceled-new")
    client.orders["auto-canceled-new"] = {
        "ordId": "auto-canceled-new", "state": "canceled", "accFillSz": "0"}

    from quantbot.account05_execution import _resume_base_take_profit_replacements
    _resume_base_take_profit_replacements(client, ledger)

    updated = ledger.get_lot(lot_id)
    assert updated.take_profit_algo_id == "old-tp"
    assert updated.take_profit_price == Decimal("2764.63")
    assert client.orders["old-tp"]["px"] == "2764.63"
    assert ledger.pending_take_profit_replacements() == []


def test_http_554_amend_timeout_defers_reconciliation_without_fault(tmp_path):
    class TimeoutAmendClient(FakeClient):
        def amend_order_price(self, *args, **kwargs):
            raise OkxError("OKX Live HTTP 554: Response Timeout by EdgeOne")

    client = TimeoutAmendClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot_id = "base:long:http554"
    ledger.record_filled_lot(
        lot_id=lot_id, signal_id=lot_id, side=PositionSide.LONG,
        kind="base", entry_order_id="entry", entry_price=Decimal("2750"),
        size=Decimal("0.11"), take_profit_price=Decimal("2763.73"))
    ledger.attach_take_profit(lot_id, "old-tp")
    client.orders["old-tp"] = {
        "ordId": "old-tp", "state": "live", "accFillSz": "0", "px": "2763.73"}
    ledger.plan_take_profit_replacement(
        lot_id, old_order_id="old-tp", new_client_order_id="A5RP554",
        new_price=Decimal("2764.63"))

    from quantbot.account05_execution import _resume_base_take_profit_replacements
    _resume_base_take_profit_replacements(client, ledger)

    assert client.orders["old-tp"]["px"] == "2763.73"
    assert ledger.pending_take_profit_replacements()


class RecordingAccount05Client(Account05LiveClient):
    def __init__(self):
        super().__init__(LiveAuditCredentials("key", "secret", "pass"))
        self.calls = []

    def _request(self, method, path, payload=None, private=False, retry_safe_post=False):
        self.calls.append((method, path, payload, private))
        return {"code": "0", "data": [{"ordId": f"O{len(self.calls)}"}]}


def test_live_payloads_have_real_sizes_independent_tp_and_no_stop_loss():
    client = RecordingAccount05Client()
    client.place_market_entry(
        inst_id="ETH-USDT-SWAP", side="buy", position_side="long",
        size=Decimal("0.24"), client_order_id="A5ENTRY")
    client.place_lot_take_profit(
        inst_id="ETH-USDT-SWAP", position_side="long", size=Decimal("0.12"),
        price=Decimal("2512.50"), client_order_id="A5TP")
    entry, take_profit = client.calls
    assert entry[2]["sz"] == "0.24"
    assert take_profit[2]["sz"] == "0.12"
    assert take_profit[2]["reduceOnly"] == "true"
    assert not any(key.lower().startswith("sl") or "stop" in key.lower()
                   for _, _, payload, _ in client.calls for key in payload)


def test_addon_fast_terminal_exits_before_ma5_turn():
    lot = SimpleNamespace(side=PositionSide.SHORT, entry_price=Decimal("110"))
    one = _market([102] * 18 + [101.8, 101.5, 101.0, 100.2, 99.0, 97.5])
    five = _market([102] * 24, "5min")
    reason = _addon_ma5_exit_reason(lot, one, five)
    assert "快速单边" in reason and "不等MA5拐弯" in reason


def test_addon_fast_terminal_accepts_qualified_expansion_before_ma5_turn():
    lot = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("94"))
    one = _market([100] * 15 + [101, 102, 103, 104, 105])
    one.loc[19, "open"] = 102.0
    one.loc[19, "high"] = 106.0
    one.loc[19, "low"] = 101.0
    five = _market([100] * 24, "5min")
    reason = _addon_ma5_exit_reason(lot, one, five)
    assert "合格扩张K线" in reason and "不等MA5拐弯" in reason


def test_addon_fast_terminal_requires_strictly_more_than_ten_points():
    one = _market([102] * 18 + [101.8, 101.5, 101.0, 100.2, 99.0, 97.5])
    five = _market([102] * 24, "5min")
    exactly_ten = SimpleNamespace(side=PositionSide.SHORT, entry_price=Decimal("107.5"))
    above_ten = SimpleNamespace(side=PositionSide.SHORT, entry_price=Decimal("107.51"))
    assert _addon_ma5_exit_reason(exactly_ten, one, five) == ""
    assert "快速单边" in _addon_ma5_exit_reason(above_ten, one, five)


def test_addon_fast_terminal_does_not_exit_on_short_quiet_candle():
    lot = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("100"))
    one = _market([100] * 15 + [101, 102, 103, 104, 105])
    one.loc[19, "open"] = 104.9
    one.loc[19, "high"] = 105.2
    one.loc[19, "low"] = 104.8
    five = _market([100] * 24, "5min")
    assert _addon_ma5_exit_reason(lot, one, five) == ""


def test_addon_uses_one_minute_turn_until_five_minute_has_taken_over():
    lot = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("92.99"))
    one = _market([99] * 15 + [100, 101, 102, 103, 104, 105, 105, 104.8, 104.2, 103])
    five = _market([100] * 24, "5min")
    assert "1分钟MA5" in _addon_ma5_exit_reason(
        lot, one, five, Decimal("0"))


def test_addon_ma5_profit_exit_requires_strictly_more_than_ten_but_keeps_risk_exit():
    one = _market([99] * 15 + [100, 101, 102, 103, 104, 105, 105, 104.8, 104.2, 103])
    five = _market([100] * 24, "5min")
    exactly_ten = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("93"))
    below_ten = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("94"))
    small_loss = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("104"))
    unlocked = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("92.9"))
    assert _addon_ma5_exit_reason(exactly_ten, one, five) == ""
    assert _addon_ma5_exit_reason(below_ten, one, five) == ""
    assert _addon_ma5_exit_reason(small_loss, one, five) == ""  # one reversed closed bar is insufficient
    assert "1分钟MA5" in _addon_ma5_exit_reason(unlocked, one, five)


def test_addon_ma5_turn_does_not_recycle_one_to_three_point_profit():
    one = _market([99] * 15 + [100, 101, 102, 103, 104, 105, 105, 104.8, 104.2, 103])
    five = _market([100] * 24, "5min")
    small_profit = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("100"))
    assert _addon_ma5_exit_reason(small_profit, one, five) == ""


def test_addon_loss_stays_open_despite_one_or_five_minute_ma5_reversal():
    lot = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("105"))
    base = [100] * 15 + [101, 102, 103, 104, 105, 105, 105, 105, 105]
    five_flat = _market([100] * 24, "5min")
    assert _addon_ma5_exit_reason(lot, _market(base + [103]), five_flat) == ""
    assert _addon_ma5_exit_reason(lot, _market(base + [103, 102]), five_flat) == ""
    assert _addon_ma5_exit_reason(
        lot, _market([100] * 24), _market(base + [103], "5min")) == ""


@pytest.mark.parametrize("size", [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")])
def test_account05_losing_small_lot_does_not_submit_reduce_order(
        monkeypatch, tmp_path, size):
    client = FakeClient([
        {"posSide": "long", "pos": str(Decimal("0.24") + size),
         "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.24", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for side in (PositionSide.LONG, PositionSide.SHORT):
        base = ledger.record_filled_lot(
            lot_id=f"base-{side.value}", signal_id=f"base-{side.value}",
            side=side, kind="base", entry_order_id=f"base-entry-{side.value}",
            entry_price=Decimal("2500"), size=Decimal("0.24"),
            take_profit_price=Decimal("2512.5"), take_profit_pct=Decimal("0.005"))
        tp = f"base-tp-{side.value}"
        ledger.attach_take_profit(base.lot_id, tp)
        client.orders[tp] = {"ordId": tp, "state": "live", "accFillSz": "0"}
    lot = ledger.record_filled_lot(
        lot_id=f"loser-{size}", signal_id=f"recovery-addon:测试亏损:{size}",
        side=PositionSide.LONG, kind="addon", entry_order_id=f"entry-{size}",
        entry_price=Decimal("2510"), size=size,
        take_profit_price=Decimal("2520"))
    ledger.mark_addon_ma5_managed(lot.lot_id)
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "test", ()))
    one = _market([2500] * 15 + [2501, 2502, 2503, 2504, 2505,
                                2505, 2505, 2504, 2503, 2500])
    five = _market([2500] * 24, "5min")
    execute_account05_tick(
        client=client, ledger=ledger,
        settings=LiveAccountSettings(tmp_path / "state.sqlite3"),
        audit=AUDIT, one=one, five=five,
        fifteen=_market([2500] * 24, "15min"),
        one_hour=_market([2500] * 24, "1h"))
    assert ledger.get_lot(lot.lot_id).status != "closed"
    assert ledger.order_intent(f"exit:{lot.lot_id}") is None


def test_account05_profit_exit_uses_strict_limit_and_books_only_after_fill(
        monkeypatch, tmp_path):
    client = FakeClient([
        {"posSide": "long", "pos": "0.24", "upl": "0", "avgPx": "2500"},
        {"posSide": "short", "pos": "0.26", "upl": "0", "avgPx": "2500"},
    ])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    for side in (PositionSide.LONG, PositionSide.SHORT):
        base = ledger.record_filled_lot(
            lot_id=f"base-{side.value}", signal_id=f"base-{side.value}",
            side=side, kind="base", entry_order_id=f"base-entry-{side.value}",
            entry_price=Decimal("2500"), size=Decimal("0.24"),
            take_profit_price=Decimal("2512.5"), take_profit_pct=Decimal("0.005"))
        tp = f"base-tp-{side.value}"
        ledger.attach_take_profit(base.lot_id, tp)
        client.orders[tp] = {"ordId": tp, "state": "live", "accFillSz": "0"}
    lot = ledger.record_filled_lot(
        lot_id="profitable-short", signal_id="recovery-addon:测试盈利:short",
        side=PositionSide.SHORT, kind="addon", entry_order_id="profit-entry",
        entry_price=Decimal("2515"), size=Decimal("0.02"),
        take_profit_price=Decimal("2505"))
    ledger.mark_addon_ma5_managed(lot.lot_id)
    monkeypatch.setattr(
        "quantbot.account05_execution.evaluate_account05_signals",
        lambda *_args, **_kwargs: Account05Signals(Trend15m.UNCLEAR, "test", ()))
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    one = _market([2502] * 18 + [2501.8, 2501.5, 2501, 2500.2, 2499, 2497.5])
    five = _market([2502] * 24, "5min")

    def cycle():
        return execute_account05_tick(
            client=client, ledger=ledger, settings=settings, audit=AUDIT,
            one=one, five=five, fifteen=_market([2500] * 24, "15min"),
            one_hour=_market([2500] * 24, "1h"))

    cycle()
    assert len(client.take_profits) == 1
    assert client.take_profits[0]["price"] == Decimal("2504.99")
    assert ledger.get_lot(lot.lot_id).status != "closed"
    cycle()
    assert len(client.take_profits) == 1  # pending order is never duplicated
    order_id = next(order_id for order_id, order in client.orders.items()
                    if order.get("clOrdId") == client.take_profits[0]["client_order_id"])
    client.orders[order_id].update(state="filled", avgPx="2504.99", accFillSz="0.02")
    cycle()
    assert ledger.get_lot(lot.lot_id).status == "closed"
    assert Decimal(ledger.connection.execute(
        "SELECT local_net_pnl FROM account05_lots WHERE lot_id=?", (lot.lot_id,)
    ).fetchone()[0]) > 0


def test_addon_ma5_reversal_ignores_running_candle():
    lot = SimpleNamespace(side=PositionSide.LONG, entry_price=Decimal("105"))
    base = [100] * 15 + [101, 102, 103, 104, 105, 105, 105, 105, 105]
    one = _market(base + [103, 102])
    one.loc[len(one) - 1, "date"] = pd.Timestamp.now(tz="UTC").floor("min").tz_localize(None)
    five = _market([100] * 24, "5min")
    assert _addon_ma5_exit_reason(lot, one, five) == ""

    five_running = _market(base + [103], "5min")
    five_running.loc[len(five_running) - 1, "date"] = (
        pd.Timestamp.now(tz="UTC").floor("5min").tz_localize(None))
    assert _addon_ma5_exit_reason(lot, _market([100] * 24), five_running) == ""


def test_addon_ma5_exit_intent_is_valid_and_restart_idempotent(tmp_path):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = ledger.record_filled_lot(
        lot_id="addon-long-1", signal_id="addon-long-1",
        side=PositionSide.LONG, kind="addon", entry_order_id="entry-1",
        entry_price=Decimal("2500"), size=Decimal("0.04"),
        take_profit_price=Decimal("2510"))
    ledger.mark_addon_ma5_managed(lot.lot_id)
    result = _close_addon_on_ma5(client, ledger, lot, "1分钟MA5向下拐弯")
    assert result.order_id.startswith("X")
    assert ledger.get_lot(lot.lot_id).status == "closed"
    intent = ledger.order_intent("exit:addon-long-1")
    assert intent is not None
    assert intent["kind"] == "addon_ma5_exit"
    assert intent["status"] == "filled"


def test_addon_exit_retries_same_durable_intent_after_safe_clock_get_timeout(tmp_path):
    class RecoveringClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.reduce_attempts = []

        def place_market_reduce(self, **payload):
            self.reduce_attempts.append(payload["client_order_id"])
            if len(self.reduce_attempts) == 1:
                raise OkxError(
                    "OKX Live pre-order server-time synchronization GET failed "
                    "after explicit rejection: SSL handshake timed out")
            return super().place_market_reduce(**payload)

    client = RecoveringClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = ledger.record_filled_lot(
        lot_id="addon-short-clock", signal_id="addon-short-clock",
        side=PositionSide.SHORT, kind="addon", entry_order_id="entry-clock",
        entry_price=Decimal("2500"), size=Decimal("0.04"),
        take_profit_price=Decimal("2490"))
    ledger.mark_addon_ma5_managed(lot.lot_id)

    with pytest.raises(OkxError, match="server-time synchronization GET failed"):
        _close_addon_on_ma5(client, ledger, lot, "1分钟MA5向上拐弯")
    intent = ledger.order_intent("exit:addon-short-clock")
    assert intent["status"] == "rejected"

    result = _close_addon_on_ma5(client, ledger, lot, "1分钟MA5向上拐弯")
    assert result.order_id.startswith("X")
    assert len(set(client.reduce_attempts)) == 1
    assert ledger.get_lot(lot.lot_id).status == "closed"


def test_addon_exit_recovers_v263_claimed_intent_without_exchange_id(tmp_path):
    client = FakeClient()
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = ledger.record_filled_lot(
        lot_id="addon-long-v263", signal_id="addon-long-v263",
        side=PositionSide.LONG, kind="addon", entry_order_id="entry-v263",
        entry_price=Decimal("2500"), size=Decimal("0.04"),
        take_profit_price=Decimal("2510"))
    ledger.mark_addon_ma5_managed(lot.lot_id)
    signal_id = "exit:addon-long-v263"
    ledger.claim_order_intent(
        signal_id=signal_id, client_order_id="A5EXLEGACY",
        side=PositionSide.LONG, kind="addon_ma5_exit",
        requested_size=lot.remaining_size)

    result = _close_addon_on_ma5(client, ledger, lot, "1分钟MA5向下拐弯")
    assert result.order_id.startswith("X")
    assert ledger.order_intent(signal_id)["status"] == "filled"
    assert ledger.get_lot(lot.lot_id).status == "closed"


def test_reduce_51169_reconciles_stale_side_instead_of_fault_stopping(tmp_path):
    class AlreadyFlatClient(FakeClient):
        def place_market_reduce(self, **payload):
            raise OkxError(
                "OKX Live POST error 1: All operations failed "
                "(sCode=51169 sMsg=Order failed because you don't have any "
                "positions in this direction for this contract to reduce or close.)")

    client = AlreadyFlatClient([])
    ledger = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = ledger.record_filled_lot(
        lot_id="stale-addon-long", signal_id="stale-addon-long",
        side=PositionSide.LONG, kind="addon", entry_order_id="stale-entry",
        entry_price=Decimal("2500"), size=Decimal("0.04"),
        take_profit_price=Decimal("2510"))
    ledger.mark_addon_ma5_managed(lot.lot_id)
    stale_base = ledger.record_filled_lot(
        lot_id="stale-base-long", signal_id="stale-base-long",
        side=PositionSide.LONG, kind="base", entry_order_id="stale-base-entry",
        entry_price=Decimal("2500"), size=Decimal("0.20"),
        take_profit_price=Decimal("2512.5"), take_profit_pct=Decimal("0.005"))

    result = _close_addon_on_ma5(client, ledger, lot, "MA5转坏")
    assert result.order_id == ""
    assert ledger.get_lot(lot.lot_id).status == "closed"
    assert ledger.get_lot(stale_base.lot_id).status == "closed"
    intent = ledger.order_intent("exit:stale-addon-long")
    assert intent["status"] == "filled"
    assert "51169" in intent["detail"]
