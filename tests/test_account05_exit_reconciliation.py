"""Offline regressions for per-addon exit isolation and base protection."""
from decimal import Decimal as D

import pytest

from test_extreme_profit_sweep import setup_case, add_lot
from quantbot.account05_signals import Account05Signals, ExtremeRotationTrigger
from quantbot.account05_strategy import Trend15m, PositionSide
from quantbot.okx import OkxError


def case(tmp_path, monkeypatch, state, filled="0"):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr("quantbot.account05_execution.evaluate_account05_signals",
        lambda *_a, **_k: Account05Signals(Trend15m.UNCLEAR, "横盘", ()))
    lot = add_lot(client, ledger, "cancelled", "2722.22", size="0.36")
    # Other owned exposure ensures a full fill doesn't trigger the separate flat-side cleanup.
    add_lot(client, ledger, "other", "2690", size="0.18")
    ledger.claim_order_intent(signal_id=f"exit:{lot.lot_id}", client_order_id="A5EXcancelled",
        side=lot.side, kind="addon_ma5_exit", requested_size=lot.original_size)
    ledger.update_order_intent(f"exit:{lot.lot_id}", "submitted", exchange_order_id="old")
    client.orders["old"] = dict(ordId="old", clOrdId="A5EXcancelled", state=state,
                                accFillSz=filled, avgPx="2699")
    client._position("short", -D(filled))
    return client, ledger, tick, lot


@pytest.mark.parametrize("state", ["canceled", "mmp_canceled"])
@pytest.mark.parametrize("filled", ["0", "0.13"])
def test_cancelled_exit_keeps_residual_without_fault_or_resubmission(tmp_path, monkeypatch, state, filled):
    client, ledger, tick, lot = case(tmp_path, monkeypatch, state, filled)
    result = tick()
    assert ledger.get_lot(lot.lot_id).remaining_size == D("0.36") - D(filled)
    assert ledger.get_lot(lot.lot_id).status != "filled_unprotected"
    assert ledger.order_intent(f"exit:{lot.lot_id}")["status"] == "reconcile_required"
    assert "待对账" in result.reason
    for _ in range(2):
        tick()
    assert ledger.get_lot(lot.lot_id).remaining_size == D("0.36") - D(filled)
    assert not client.entries
    assert not client.take_profits
    assert not [e for e in client.trace if e[0] == "reduce"]


def test_terminal_cancel_with_full_fill_is_closed_and_credited_once(tmp_path, monkeypatch):
    client, ledger, tick, lot = case(tmp_path, monkeypatch, "canceled", "0.36")
    tick()
    assert ledger.get_lot(lot.lot_id).status == "closed"
    pool = ledger.recovery_pool()
    assert pool["total_net_profit"] > 0
    tick()
    assert ledger.recovery_pool() == pool
    assert not client.entries


def test_isolated_exit_is_rechecked_read_only_and_adopts_confirmed_fill(tmp_path, monkeypatch):
    client, ledger, tick, lot = case(tmp_path, monkeypatch, "canceled", "0.13")
    tick()
    client.orders["old"].update(state="filled", accFillSz="0.36")
    client._position("short", -D("0.23"))
    tick()
    assert ledger.get_lot(lot.lot_id).status == "closed"
    assert not client.entries
    assert not client.take_profits


def test_unknown_exit_state_retains_lot_without_resubmitting(tmp_path, monkeypatch):
    client, ledger, tick, lot = case(tmp_path, monkeypatch, "unexpected")
    tick()
    assert ledger.get_lot(lot.lot_id).remaining_size == D("0.36")
    assert ledger.order_intent(f"exit:{lot.lot_id}")["status"] == "reconcile_required"
    assert not client.entries


def test_cumulative_fill_cannot_reopen_already_reduced_quantity(tmp_path, monkeypatch):
    client, ledger, tick, lot = case(tmp_path, monkeypatch, "canceled", "0.13")
    tick()
    client.orders["old"]["accFillSz"] = "0"
    tick()
    assert ledger.get_lot(lot.lot_id).remaining_size == D("0.23")
    assert not client.entries


def test_next_extreme_reconciles_cancelled_partial_before_reverse(tmp_path, monkeypatch):
    client, ledger, tick, lot = case(tmp_path, monkeypatch, "canceled", "0.13")
    tick()
    monkeypatch.setattr("quantbot.account05_execution.evaluate_account05_signals",
        lambda *_a, **_k: Account05Signals(Trend15m.UNCLEAR, "横盘", (),
            ExtremeRotationTrigger(1, "2026-10-07 01:00:00", "底部极值", "next-extreme")))
    tick()
    assert ledger.get_lot(lot.lot_id).status == "closed"
    assert [e[2] for e in client.trace if e[0] == "reduce"] == [D("0.23")]
    assert len(client.entries) == 1


def test_uncovered_base_still_blocks_entries_and_reports_detail(tmp_path, monkeypatch):
    client, ledger, tick, _ = case(tmp_path, monkeypatch, "live")
    ledger.record_filled_lot(lot_id="unprotected-base", signal_id="base",
        side=PositionSide.LONG, kind="base", entry_order_id="base-order",
        entry_price=D(2700), size=D("0.36"), take_profit_price=D(2715),
        take_profit_pct=D("0.005"))
    client._position("long", D("0.10"))
    with pytest.raises(OkxError, match="不足以覆盖当前基础仓"):
        tick()
    assert not client.entries
    assert ledger.get_lot("unprotected-base").status == "filled_unprotected"
