"""Offline exchange simulation for confirmed whole-group extreme reversal."""
from decimal import Decimal as D

import numpy as np
import pytest

from test_account05_live_execution import FakeClient, _market, AUDIT
from quantbot.account05_execution import execute_account05_tick, import_manual_orders
from quantbot.account05_signals import Account05Signals, ExtremeRotationTrigger
from quantbot.account05_state import Account05StateStore
from quantbot.account05_strategy import PositionSide, Trend15m
from quantbot.live_account_settings import LiveAccountSettings
from quantbot.okx import OkxError
from quantbot.account05_live import Account05LiveClient
from quantbot.manual_order_review import manual_order_inventory


class Exchange(FakeClient):
    def __init__(self, price):
        super().__init__()
        self.price = D(price)
        self.trace = []
        self.history = []
        self.fail_close = False
        self.fail_entry = False

    def orders_history(self, *_args, **_kwargs):
        return self.history

    def exit_quote(self, side):
        return self.price

    def order(self, inst_id, *, order_id="", client_order_id=""):
        if order_id:
            result = self.orders[order_id]
        else:
            result = next((v for v in self.orders.values()
                           if v.get("clOrdId") == client_order_id), None)
            if result is None:
                raise OkxError("OKX Live GET error 51603: Order does not exist")
        self.trace.append(("read", result["ordId"], result["state"]))
        return dict(result)

    def _position(self, side, delta):
        row = next((r for r in self.positions if r["posSide"] == side), None)
        if row is None:
            row = dict(posSide=side, pos="0", upl="0", avgPx=str(self.price))
            self.positions.append(row)
        row["pos"] = str(D(row["pos"]) + delta)

    def place_market_reduce(self, **payload):
        response = super().place_market_reduce(**payload)
        self.orders[response["ordId"]].update(
            avgPx=str(self.price), clOrdId=payload["client_order_id"])
        self._position(payload["position_side"], -payload["size"])
        self.trace.append(("reduce", response["ordId"], payload["size"]))
        if self.fail_close:
            self.fail_close = False
            raise OkxError("outcome unknown; 对账")
        return response

    def place_market_entry(self, **payload):
        response = super().place_market_entry(**payload)
        self.orders[response["ordId"]].update(
            avgPx=str(self.price), clOrdId=payload["client_order_id"])
        self._position(payload["position_side"], payload["size"])
        self.trace.append(("entry", response["ordId"], payload["size"]))
        if self.fail_entry:
            self.fail_entry = False
            raise OkxError("outcome unknown; 对账")
        return response


def setup_case(tmp_path, monkeypatch, direction=1, price="2699", points="15"):
    client = Exchange(price)
    ledger = Account05StateStore(tmp_path / "lots.sqlite3")
    settings = LiveAccountSettings(tmp_path / "settings.sqlite3")
    settings.set_addon_take_profit_points(points)
    monkeypatch.setattr("quantbot.account05_execution.evaluate_account05_signals",
        lambda *_a, **_k: Account05Signals(Trend15m.UNCLEAR, "横盘", (),
            ExtremeRotationTrigger(direction, "2026-10-07 00:02:00", "极值", "same-structure")))
    def tick():
        flat = np.full(120, float(client.price))
        audit = {**AUDIT, "market": {"markPx": str(client.price)}}
        return execute_account05_tick(client=client, ledger=ledger, settings=settings,
            audit=audit, one=_market(flat), five=_market(flat, "5min"),
            fifteen=_market(flat, "15min"), one_hour=_market(flat, "1h"))
    return client, ledger, settings, tick


def add_lot(client, ledger, name, price, *, side=PositionSide.SHORT,
            size="0.18", manual=False, kind="addon"):
    signal = f"manual:{name}" if manual else f"addon:{name}"
    lot = ledger.record_filled_lot(lot_id=signal, signal_id=signal, side=side,
        kind=kind, entry_order_id=name, entry_price=D(price), size=D(size),
        take_profit_price=D(price), take_profit_pct=D("0.005") if kind == "base" else D(0))
    if kind == "addon":
        ledger.mark_addon_ma5_managed(lot.lot_id)
    client._position(side.value, D(size))
    if manual:
        client.history.append(dict(ordId=name, clOrdId="", posSide=side.value,
            side="sell" if side is PositionSide.SHORT else "buy", state="filled",
            ordType="limit", accFillSz=size, avgPx=price, cTime="1791330000000"))
    return ledger.get_lot(lot.lot_id)


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("points", ["15", "20"])
def test_all_manual_and_auto_winners_fill_before_one_reverse(tmp_path, monkeypatch, direction, points):
    client, ledger, settings, tick = setup_case(tmp_path, monkeypatch, direction, points=points)
    side = PositionSide.SHORT if direction == 1 else PositionSide.LONG
    sign = D(1) if direction == 1 else D(-1)
    winners = [add_lot(client, ledger, str(i), str(client.price + sign * (D(points) + i + 1)),
                       side=side, manual=i < 4) for i in range(7)]
    boundary = add_lot(client, ledger, "boundary", str(client.price + sign * D(points)), side=side)
    loser = add_lot(client, ledger, "loser", str(client.price - sign * 10), side=side)
    assert tick().action == "submitted"
    assert all(ledger.get_lot(l.lot_id).status == "closed" for l in winners)
    assert ledger.get_lot(boundary.lot_id).remaining_size == boundary.remaining_size
    assert ledger.get_lot(loser.lot_id).remaining_size == loser.remaining_size
    assert len(client.entries) == 1
    entry_index = next(i for i, e in enumerate(client.trace) if e[0] == "entry")
    reductions = [e for e in client.trace if e[0] == "reduce"]
    assert len(reductions) == len(winners)
    for e in reductions:
        assert ("read", e[1], "filled") in client.trace[:entry_index]
    pool = ledger.recovery_pool()
    tick()
    assert len(client.entries) == 1
    assert ledger.recovery_pool() == pool
    assert client.entries[0]["size"] == D(settings.extreme_rotation_contracts())


def test_feedback_prices_all_qualifying_manual_shorts_are_closed(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lots = [add_lot(client, ledger, str(i), price, manual=True) for i, price in enumerate(
        ["2722.22", "2717.45", "2716.1", "2715.4", "2714.23", "2710.08"])]
    tick()
    assert all(ledger.get_lot(l.lot_id).status == "closed" for l in lots[:5])
    assert ledger.get_lot(lots[-1].lot_id).remaining_size == D("0.18")


@pytest.mark.parametrize("fail", ["fail_close", "fail_entry"])
def test_unknown_ack_restart_recovers_without_duplicate_orders(tmp_path, monkeypatch, fail):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    for i in range(3):
        add_lot(client, ledger, str(i), "2722.22", manual=True)
    setattr(client, fail, True)
    with pytest.raises(OkxError, match="unknown"):
        tick()
    ledger.connection.close()
    # A new process must recover persisted targets and client IDs, not rediscover a smaller group.
    ledger.connection = Account05StateStore(tmp_path / "lots.sqlite3").connection
    tick()
    assert len([e for e in client.trace if e[0] == "reduce"]) == 3
    assert len(client.entries) == 1
    pool = ledger.recovery_pool()
    tick()
    assert ledger.recovery_pool() == pool


def test_pending_partial_exit_cancels_then_only_reduces_remaining(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, "pending", "2722.22", size="0.36")
    ledger.claim_order_intent(signal_id=f"exit:{lot.lot_id}", client_order_id="A5EXold",
        side=lot.side, kind="addon", requested_size=D("0.36"))
    ledger.update_order_intent(f"exit:{lot.lot_id}", "submitted", exchange_order_id="old")
    client.orders["old"] = dict(ordId="old", clOrdId="A5EXold", state="partially_filled",
                                accFillSz="0.10", avgPx="2700")
    ledger.sync_take_profit_fill(lot.lot_id, D("0.10"))
    client._position("short", -D("0.10"))
    tick()
    assert ("cancel", "old") in client.events
    assert [e[2] for e in client.trace if e[0] == "reduce"] == [D("0.26")]
    assert ledger.get_lot(lot.lot_id).status == "closed"
    assert len(client.entries) == 1


def test_historical_partial_close_is_quantity_aware_and_not_replayed(tmp_path, monkeypatch):
    client, ledger, _, _ = setup_case(tmp_path, monkeypatch)
    add_lot(client, ledger, "older", "2714.23", manual=True, size="0.36")
    client.history.append(dict(ordId="close", clOrdId="", posSide="short", side="buy",
        state="filled", accFillSz="0.13", avgPx="2694.49", cTime="1791330001000"))
    client._position("short", -D("0.13"))
    newer = add_lot(client, ledger, "newer", "2722.22", manual=True)
    client.history[-1]["cTime"] = "1791330002000"
    for _ in range(3):
        import_manual_orders(client, ledger, require_fresh=True, profit_points=D(15))
        assert ledger.get_lot("manual:older").remaining_size == D("0.23")
        assert ledger.get_lot(newer.lot_id).remaining_size == D("0.18")


def test_profitable_base_retains_its_own_exit_rule(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    base = add_lot(client, ledger, "base", "2722.22", size="0.36", kind="base")
    ledger.attach_take_profit(base.lot_id, "base-tp")
    client.orders["base-tp"] = dict(ordId="base-tp", state="live", accFillSz="0")
    winner = add_lot(client, ledger, "winner", "2722.22", manual=True)
    tick()
    assert ledger.get_lot(winner.lot_id).status == "closed"
    assert ledger.get_lot(base.lot_id).remaining_size == D("0.36")
    assert ("cancel", "base-tp") not in client.events
    assert len([e for e in client.trace if e[0] == "reduce"]) == 1


def test_quote_falls_below_gate_stops_before_reverse(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    add_lot(client, ledger, "winner", "2722.22")
    quotes = iter([D("2699"), D("2710")])
    client.exit_quote = lambda _side: next(quotes)
    with pytest.raises(OkxError, match="报价"):
        tick()
    assert not client.entries
    assert not [e for e in client.trace if e[0] == "reduce"]


def test_cancel_race_uses_final_cumulative_fill(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, "pending", "2722.22", size="0.36")
    ledger.claim_order_intent(signal_id=f"exit:{lot.lot_id}", client_order_id="A5EXold",
        side=lot.side, kind="addon", requested_size=D("0.36"))
    ledger.update_order_intent(f"exit:{lot.lot_id}", "submitted", exchange_order_id="old")
    client.orders["old"] = dict(ordId="old", clOrdId="A5EXold", state="live",
                                accFillSz="0", avgPx="2700")
    def cancel(_inst, oid):
        client.orders[oid].update(state="canceled", accFillSz="0.13")
        client._position("short", -D("0.13"))
    client.cancel_order = cancel
    tick()
    assert [e[2] for e in client.trace if e[0] == "reduce"] == [D("0.23")]
    assert ledger.get_lot(lot.lot_id).status == "closed"


def test_unconfirmed_cancel_does_not_open_reverse(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, "pending", "2722.22")
    ledger.claim_order_intent(signal_id=f"exit:{lot.lot_id}", client_order_id="A5EXold",
        side=lot.side, kind="addon", requested_size=lot.remaining_size)
    ledger.update_order_intent(f"exit:{lot.lot_id}", "submitted", exchange_order_id="old")
    client.orders["old"] = dict(ordId="old", state="live", accFillSz="0")
    client.cancel_order = lambda *_args: {"sCode": "0"}
    with pytest.raises(OkxError, match="未确认结束"):
        tick()
    assert not client.entries
    assert not [e for e in client.trace if e[0] == "reduce"]


def test_known_exit_owner_precedes_fifo():
    orders = [dict(ordId=oid, posSide="short", side="sell", accFillSz="0.18",
                   avgPx=price, cTime=str(ts), clOrdId="") for oid, price, ts in
              [("older", "2710", 1000), ("newer", "2722", 2000)]]
    orders.append(dict(ordId="exit", posSide="short", side="buy", accFillSz="0.18",
                       avgPx="2699", cTime="3000", clOrdId="A5EXtest"))
    entries, _ = manual_order_inventory(orders, [], exit_owners={"exit": "newer"})
    assert entries["older"]["closed"] == 0
    assert entries["newer"]["closed"] == D("0.18")


@pytest.mark.parametrize(("side", "expected"), [("long", "2698"), ("short", "2699")])
def test_profit_gate_uses_executable_bid_or_ask(monkeypatch, side, expected):
    client = object.__new__(Account05LiveClient)
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: {
        "data": [dict(bidPx="2698", askPx="2699", last="2700")]})
    assert client.exit_quote(side) == D(expected)


def test_missing_executable_quote_does_not_fall_back_to_last(monkeypatch):
    client = object.__new__(Account05LiveClient)
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: {"data": [{"last": "2699"}]})
    with pytest.raises(OkxError, match="报价缺失"):
        client.exit_quote("short")


def test_old_importer_false_full_close_is_repaired(tmp_path, monkeypatch):
    client, ledger, _, _ = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, "manual", "2722.22", manual=True, size="0.36")
    ledger.sync_take_profit_fill(lot.lot_id, D("0.36"))
    import_manual_orders(client, ledger, require_fresh=True, profit_points=D(15))
    assert ledger.get_lot(lot.lot_id).remaining_size == D("0.36")


def test_history_cumulative_fill_never_regresses(tmp_path, monkeypatch):
    client, ledger, _, _ = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, "manual", "2722.22", manual=True, size="0.36")
    import_manual_orders(client, ledger, require_fresh=True, profit_points=D(15))
    client.history[0]["accFillSz"] = "0.18"
    import_manual_orders(client, ledger, require_fresh=True, profit_points=D(15))
    assert ledger.get_lot(lot.lot_id).original_size == D("0.36")


def test_quarantined_source_lot_blocks_reversal(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, "uncertain", "2722.22")
    with pytest.raises(ValueError, match="outside"):
        ledger.sync_take_profit_fill(lot.lot_id, lot.original_size + D("0.01"))
    with pytest.raises(OkxError, match="隔离对账"):
        tick()
    assert not client.entries
