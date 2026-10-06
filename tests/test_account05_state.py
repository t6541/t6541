from decimal import Decimal

import pytest

from quantbot.account05_state import Account05StateStore
from quantbot.account05_strategy import PositionSide


def _record(store, lot_id="A05-L-001", signal_id="signal-1", order_id="order-1"):
    return store.record_filled_lot(
        lot_id=lot_id, signal_id=signal_id, side=PositionSide.LONG,
        kind="addon", entry_order_id=order_id, entry_price=Decimal("2500"),
        size=Decimal("0.04"), take_profit_price=Decimal("2515.50"))


def test_each_addon_has_independent_persistent_take_profit_identity(tmp_path):
    path = tmp_path / "account05" / "strategy.sqlite3"
    store = Account05StateStore(path)
    first = _record(store)
    assert first.status == "filled_unprotected"
    assert store.unprotected_lots() == [first]
    protected = store.attach_take_profit(first.lot_id, "algo-1")
    assert protected.take_profit_algo_id == "algo-1"
    store.close()

    reopened = Account05StateStore(path)
    assert reopened.get_lot(first.lot_id) == protected
    assert reopened.unprotected_lots() == []


def test_signal_replay_is_idempotent_but_cannot_change_lot_identity(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    first = _record(store)
    assert _record(store) == first
    with pytest.raises(ValueError):
        _record(store, lot_id="different")


def test_partial_and_complete_tp_fill_reduce_only_the_owned_lot(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = _record(store)
    store.attach_take_profit(lot.lot_id, "algo-1")
    partial = store.apply_take_profit_fill(lot.lot_id, Decimal("0.01"))
    assert partial.remaining_size == Decimal("0.03")
    assert partial.status == "partially_closed"
    closed = store.apply_take_profit_fill(lot.lot_id, Decimal("0.03"))
    assert closed.remaining_size == 0
    assert closed.status == "closed"
    assert store.open_addon_count(PositionSide.LONG) == 0


def test_overfill_requires_reconciliation_instead_of_corrupting_ledger(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = _record(store)
    with pytest.raises(ValueError, match="exceeds"):
        store.apply_take_profit_fill(lot.lot_id, Decimal("0.05"))
    after = store.get_lot(lot.lot_id)
    assert after.remaining_size == Decimal("0.04")
    assert after.status == "reconcile_required"


def test_open_addon_limit_is_counted_per_direction(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    _record(store)
    store.record_filled_lot(
        lot_id="A05-S-001", signal_id="signal-2", side=PositionSide.SHORT,
        kind="addon", entry_order_id="order-2", entry_price=Decimal("2500"),
        size=Decimal("0.04"), take_profit_price=Decimal("2484.50"))
    assert store.open_addon_count(PositionSide.LONG) == 1
    assert store.open_addon_count(PositionSide.SHORT) == 1


def test_exchange_cumulative_fill_reconciliation_is_absolute(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = _record(store)
    store.attach_take_profit(lot.lot_id, "algo-1")
    first = store.sync_take_profit_fill(lot.lot_id, Decimal("0.01"))
    replay = store.sync_take_profit_fill(lot.lot_id, Decimal("0.01"))
    assert first.remaining_size == replay.remaining_size == Decimal("0.03")
    closed = store.sync_take_profit_fill(lot.lot_id, Decimal("0.04"))
    assert closed.status == "closed"


def test_continuously_true_shape_is_claimed_once_and_rearms_after_disappearing(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    shape = (("下跌趋势反抽追空", PositionSide.SHORT, "10:41"),)
    expected = {("下跌趋势反抽追空", PositionSide.SHORT)}
    assert store.claim_new_signal_shapes(shape, "10:40", "10:40") == expected
    assert store.claim_new_signal_shapes(shape, "10:40", "10:40") == set()
    # A transient five-second miss and reappearance cannot create another fill.
    assert store.claim_new_signal_shapes((), "10:40", "10:40") == set()
    assert store.claim_new_signal_shapes(shape, "10:40", "10:40") == set()
    # It must remain absent through a new closed 1m candle before rearming.
    assert store.claim_new_signal_shapes((), "10:41", "10:40") == set()
    assert store.claim_new_signal_shapes((), "10:42", "10:40") == set()
    new_shape = (("下跌趋势反抽追空", PositionSide.SHORT, "10:43"),)
    # A new 1m structure still cannot double-fill inside the original 5m bar.
    assert store.claim_new_signal_shapes(new_shape, "10:42", "10:40") == set()
    assert store.claim_new_signal_shapes(new_shape, "10:44", "10:45") == expected


def test_distinct_reversal_anchor_rearms_on_later_five_minute_bar(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    first = (("局部底部反转做多", PositionSide.LONG, "10:12"),)
    second = (("局部底部反转做多", PositionSide.LONG, "10:14"),)
    expected = {('局部底部反转做多', PositionSide.LONG)}
    assert store.claim_new_signal_shapes(first, "10:12", "10:10") == expected
    assert store.claim_new_signal_shapes(second, "10:14", "10:10") == set()
    assert store.claim_new_signal_shapes(second, "10:15", "10:15") == expected
    assert store.claim_new_signal_shapes(second, "10:15", "10:15") == set()


def test_recovery_dashboard_keeps_local_and_exchange_exit_books_separate(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    lot = _record(store)
    store.record_addon_exit_accounting(
        lot.lot_id, exit_order_id="exit-1", exit_price=Decimal("2512"),
        local_gross_pnl=Decimal("0.048"),
        local_fee_estimate=Decimal("0.010024"),
        local_net_pnl=Decimal("0.037976"),
        exchange_realized_pnl=Decimal("-0.02"),
        exchange_fee=Decimal("-0.004"))
    store.sync_take_profit_fill(lot.lot_id, lot.original_size)
    row = store.recovery_lot_rows()[0]
    assert row["exit_order_id"] == "exit-1"
    assert Decimal(row["local_net_pnl"]) == Decimal("0.037976")
    assert Decimal(row["exchange_realized_pnl"]) == Decimal("-0.02")
    assert Decimal(row["exchange_fee"]) == Decimal("-0.004")


def test_fixed_addon_order_list_only_returns_point_zero_one_addons(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    for suffix, kind, size in (("fixed", "addon", "0.01"),
                               ("legacy", "addon", "0.04"),
                               ("base", "base", "0.01"),
                               ("recovery", "addon", "0.01")):
        store.record_filled_lot(
            lot_id=f"lot-{suffix}", signal_id=(
                f"addon:{suffix}" if suffix != "recovery" else "recovery-addon:recovery"),
            side=PositionSide.LONG, kind=kind,
            entry_order_id=f"entry-{suffix}", entry_price=Decimal("2500"),
            size=Decimal(size), take_profit_price=Decimal("2510"))
    rows = store.fixed_addon_lot_rows()
    assert [row["lot_id"] for row in rows] == ["lot-fixed"]
    assert store.open_fixed_addon_count(PositionSide.LONG) == 2
    assert store.open_recovery_slot_count(PositionSide.LONG) == 1


def test_all_identities_share_one_direction_lock_inside_same_five_minute_bar(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    assert store.claim_side_five_entry(PositionSide.LONG, "13:10")
    assert not store.claim_side_five_entry(PositionSide.LONG, "13:10")
    assert store.claim_side_five_entry(PositionSide.SHORT, "13:10")
    assert store.claim_side_five_entry(PositionSide.LONG, "13:15")


@pytest.mark.parametrize("first,second", [
    ("1分钟超级趋势首次翻空补漏做空", "局部顶部做空"),
    ("反转三阶段补漏做空", "5分钟超级趋势首次翻空追空"),
])
def test_supertrend_and_top_reversal_each_get_one_short_in_same_bar(tmp_path, first, second):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    assert store.claim_side_five_entry(PositionSide.SHORT, "13:10", first)
    assert store.claim_side_five_entry(PositionSide.SHORT, "13:10", second)
    assert not store.claim_side_five_entry(PositionSide.SHORT, "13:10", first)
    assert not store.claim_side_five_entry(PositionSide.SHORT, "13:10", second)
    assert not store.claim_side_five_entry(PositionSide.SHORT, "13:10", "下跌趋势反抽追空")
    store.close()


@pytest.mark.parametrize("first,second", [
    ("超级趋势支撑早触发追多", "上涨趋势回踩追多"),
    ("上涨趋势回踩追多", "超级趋势支撑早触发追多"),
])
def test_supertrend_support_and_trend_pullback_each_get_one_long(tmp_path, first, second):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    assert store.claim_side_five_entry(PositionSide.LONG, "13:10", first)
    assert store.claim_side_five_entry(PositionSide.LONG, "13:10", second)
    assert not store.claim_side_five_entry(PositionSide.LONG, "13:10", first)
    assert not store.claim_side_five_entry(PositionSide.LONG, "13:10", second)
    assert not store.claim_side_five_entry(PositionSide.LONG, "13:10", "局部底部做多")
    store.close()


def test_recovery_program_snapshots_trapped_longs_and_counts_only_recovery_fills(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    for index, kind in enumerate(("base", "addon", "addon")):
        store.record_filled_lot(
            lot_id=f"long-{index}", signal_id=f"legacy-long-{index}",
            side=PositionSide.LONG, kind=kind,
            entry_order_id=f"legacy-entry-{index}",
            entry_price=Decimal("2770"), size=Decimal("0.03"),
            take_profit_price=Decimal("2780"))
    recovery = store.activate_recovery_program(
        initial_equity=Decimal("95.5"), target_equity=Decimal("100"))
    assert recovery["phase"] == "balance_short"
    assert store.recovery_trapped_open_count() == 3

    store.claim_order_intent(
        signal_id="addon:ordinary", client_order_id="ordinary",
        side=PositionSide.LONG, kind="addon", requested_size=Decimal("0.03"))
    store.update_order_intent(
        "addon:ordinary", "filled", exchange_order_id="ordinary-order")
    store.claim_order_intent(
        signal_id="recovery-addon:first", client_order_id="recovery-first",
        side=PositionSide.LONG, kind="addon", requested_size=Decimal("0.03"))
    store.update_order_intent(
        "recovery-addon:first", "filled", exchange_order_id="recovery-order")
    assert store.recovery_extra_used(PositionSide.LONG) == 1
    assert store.recovery_extra_used(PositionSide.SHORT) == 0
    assert store.set_recovery_phase("adjust")["active"] == 1
    completed = store.set_recovery_phase("complete")
    assert completed["active"] == 0


def test_recovery_pool_splits_profit_and_can_pay_or_pair_losses(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    pool = store.credit_recovery_profit(Decimal("1.00"))
    assert pool["pool_balance"] == Decimal("0.8000")
    assert pool["reserve_balance"] == Decimal("0.2000")
    pool = store.debit_recovery_loss(Decimal("0.30"))
    assert pool["pool_balance"] == Decimal("0.5000")
    assert pool["total_loss_paid"] == Decimal("0.30")
    pool = store.record_paired_recovery(Decimal("0.50"), Decimal("0.40"))
    assert pool["pool_balance"] == Decimal("0.5800")
    assert pool["reserve_balance"] == Decimal("0.2200")
    assert pool["total_net_profit"] == Decimal("1.50")
    assert pool["total_loss_paid"] == Decimal("0.70")


def test_recovery_pool_rejects_zero_loss_debit(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    store.credit_recovery_profit(Decimal("1.00"))
    with pytest.raises(ValueError, match="invalid recovery loss debit"):
        store.debit_recovery_loss(Decimal("0"))


def test_filled_recovery_loss_records_full_deficit_as_negative_pool(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    store.credit_recovery_profit(Decimal("0.000483505"))
    pool = store.settle_filled_recovery_loss(Decimal("0.001767470"))
    assert pool["pool_balance"] == Decimal("-0.0013806660")
    assert pool["reserve_balance"] == Decimal("0.0000967010")
    assert pool["total_loss_paid"] == Decimal("0.001767470")
    assert pool["total_uncovered_loss"] == Decimal("0")


def test_paired_fill_slippage_records_full_deficit_as_negative_pool(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    store.credit_recovery_profit(Decimal("0.10"))
    pool = store.record_paired_recovery(Decimal("0.02"), Decimal("0.20"))
    assert pool["pool_balance"] == Decimal("-0.10")
    assert pool["reserve_balance"] == Decimal("0.02")
    assert pool["total_uncovered_loss"] == Decimal("0")


def test_same_addon_exit_event_is_claimed_once_per_side(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    assert store.claim_addon_exit_event(PositionSide.LONG, "bar-1")
    assert not store.claim_addon_exit_event(PositionSide.LONG, "bar-1")
    assert store.claim_addon_exit_event(PositionSide.SHORT, "bar-1")
    assert store.claim_addon_exit_event(PositionSide.LONG, "bar-2")


def test_extreme_rotation_event_is_independent_and_idempotent(tmp_path):
    store = Account05StateStore(tmp_path / "strategy.sqlite3")
    assert store.claim_extreme_rotation_event("top:2026-09-25T01:00", -1)
    assert store.claim_extreme_rotation_event("top:2026-09-25T01:00", -1)
    store.complete_extreme_rotation_event("top:2026-09-25T01:00")
    assert not store.claim_extreme_rotation_event("top:2026-09-25T01:00", -1)
    assert store.claim_extreme_rotation_event("bottom:2026-09-25T02:00", 1)
