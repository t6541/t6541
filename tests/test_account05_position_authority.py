"""Exchange-authoritative sizing and durable virtual-only cleanup."""
from decimal import Decimal as D

import pytest

from test_extreme_profit_sweep import setup_case, add_lot
from quantbot.account05_execution import import_manual_orders, reconcile_virtual_position, ManualInventoryPending
from quantbot.account05_state import Account05StateStore
from quantbot.account05_signals import Account05Signals
from quantbot.account05_strategy import PositionSide, Trend15m


def quiet(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr('quantbot.account05_execution.evaluate_account05_signals',
        lambda *_a, **_k: Account05Signals(Trend15m.UNCLEAR, '横盘', ()))
    return case


def test_manual_correction_survives_refresh_restart_and_delayed_close(tmp_path, monkeypatch):
    client, ledger, _, _ = quiet(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'manual', '2722', manual=True, size='1.88')
    client._position('short', -D('.04'))
    import_manual_orders(client, ledger, require_fresh=True)
    reconcile_virtual_position(client, ledger)
    assert ledger.get_lot(lot.lot_id).remaining_size == D('1.84')
    ledger.close()
    ledger = Account05StateStore(tmp_path / 'lots.sqlite3')
    for _ in range(2):
        import_manual_orders(client, ledger, require_fresh=True)
        reconcile_virtual_position(client, ledger)
        assert ledger.get_lot(lot.lot_id).remaining_size == D('1.84')
    client.history.append(dict(ordId='delayed-close', clOrdId='', posSide='short', side='buy',
        state='filled', accFillSz='.04', avgPx='2699', cTime='1791330001000'))
    import_manual_orders(client, ledger, require_fresh=True)
    reconcile_virtual_position(client, ledger)
    assert ledger.get_lot(lot.lot_id).remaining_size == D('1.84')
    assert ledger.recovery_pool()['total_net_profit'] == 0


def test_flat_cleans_manual_auto_isolated_and_old_base_without_fake_profit(tmp_path, monkeypatch):
    client, ledger, _, tick = quiet(tmp_path, monkeypatch)
    manual = add_lot(client, ledger, 'manual', '2722', manual=True)
    auto = add_lot(client, ledger, 'auto', '2722')
    base = add_lot(client, ledger, 'base', '2722', kind='base')
    isolated = add_lot(client, ledger, 'isolated', '2722')
    ledger.mark_take_profit_missing(isolated.lot_id)
    ledger.mark_lot_reconcile_required(isolated.lot_id)
    client.positions = []
    tick()
    for lot in (manual, auto, base, isolated):
        assert ledger.get_lot(lot.lot_id).status == 'closed'
        assert ledger.get_lot(lot.lot_id).remaining_size == 0
        assert ledger.virtual_excluded_size(lot.lot_id) == lot.original_size
    assert not client.entries and not client.take_profits
    assert ledger.recovery_pool()['total_net_profit'] == 0
    # A new position on this side must not resurrect old history as virtual exposure.
    client._position('short', D('.18'))
    import_manual_orders(client, ledger, require_fresh=True)
    assert ledger.get_lot(manual.lot_id).remaining_size == 0
    assert ledger.get_lot(manual.lot_id).status == 'closed'
    assert '非成交' in next(row for row in ledger.entry_review_lot_rows()
        if row['lot_id'] == auto.lot_id)['exit_reason']


def test_loss_remains_when_only_winner_is_eligible(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    loser = add_lot(client, ledger, 'older-loser', '2690', size='1')
    winner = add_lot(client, ledger, 'newer-winner', '2722', size='1')
    client._position('short', -D('.16'))
    tick()
    assert [e[2] for e in client.trace if e[0] == 'reduce'] == [D('.84')]
    assert ledger.get_lot(loser.lot_id).remaining_size == 1
    assert ledger.virtual_excluded_size(winner.lot_id) == D('.16')
    assert D(next(r['pos'] for r in client.positions if r['posSide']=='short')) == 1


def test_flat_cancels_confirmed_live_exit_before_reusing_side(tmp_path, monkeypatch):
    client, ledger, _, tick = quiet(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'pending', '2722', size='.36')
    ledger.claim_order_intent(signal_id=f'exit:{lot.lot_id}', client_order_id='A5EXold',
        side=lot.side, kind='addon', requested_size=D('.36'))
    ledger.update_order_intent(f'exit:{lot.lot_id}', 'submitted', exchange_order_id='old')
    client.orders['old'] = dict(ordId='old', state='live', accFillSz='0')
    client.positions = []
    tick()
    assert ('cancel', 'old') in client.events
    assert client.orders['old']['state'] == 'canceled'
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert ledger.recovery_pool()['total_net_profit'] == 0


def test_active_uncertain_exit_is_not_resized_or_repeated(tmp_path, monkeypatch):
    client, ledger, _, tick = quiet(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'pending', '2722', size='1.88')
    ledger.claim_order_intent(signal_id=f'exit:{lot.lot_id}', client_order_id='A5EXold',
        side=lot.side, kind='addon', requested_size=D('1.88'))
    ledger.update_order_intent(f'exit:{lot.lot_id}', 'unknown')
    client._position('short', -D('.04'))
    assert tick().action == 'reconcile_wait'
    assert ledger.get_lot(lot.lot_id).remaining_size == D('1.88')
    assert not client.entries and not client.take_profits


def test_missing_snapshot_never_means_flat(tmp_path, monkeypatch):
    client, ledger, _, _ = quiet(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'auto', '2722')
    client.raw_snapshot = lambda: {}
    with pytest.raises(ManualInventoryPending, match='快照缺失'):
        reconcile_virtual_position(client, ledger)
    assert ledger.get_lot(lot.lot_id).remaining_size == D('.18')
    assert ledger.virtual_excluded_size(lot.lot_id) == 0


def test_flat_retires_both_old_and_replacement_base_protection(tmp_path, monkeypatch):
    client, ledger, _, tick = quiet(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'old-base', '2722', kind='base', size='.36')
    ledger.attach_take_profit(lot.lot_id, 'old-tp')
    ledger.plan_take_profit_replacement(lot.lot_id, old_order_id='old-tp',
        new_client_order_id='A5RPnew', new_price=D('2700'))
    ledger.mark_replacement_new_live(lot.lot_id, 'new-tp')
    client.orders['old-tp'] = dict(ordId='old-tp', state='live', accFillSz='0')
    client.orders['new-tp'] = dict(ordId='new-tp', clOrdId='A5RPnew', state='live', accFillSz='0')
    client.positions = []
    tick()
    assert client.orders['old-tp']['state'] == client.orders['new-tp']['state'] == 'canceled'
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert ledger.get_lot(lot.lot_id).take_profit_algo_id is None
    assert not ledger.pending_take_profit_replacements()
    assert ledger.virtual_excluded_size(lot.lot_id) == D('.36')
    assert not client.entries and not client.take_profits


def test_flat_known_base_fill_is_confirmed_not_labeled_virtual_cleanup(tmp_path, monkeypatch):
    client, ledger, _, tick = quiet(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'old-base', '2722', kind='base', size='.36')
    ledger.attach_take_profit(lot.lot_id, 'old-tp')
    client.orders['old-tp'] = dict(ordId='old-tp', state='filled', accFillSz='.36', avgPx='2700')
    client.positions = []
    tick()
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert ledger.get_lot(lot.lot_id).original_size == D('.36')
    assert ledger.virtual_excluded_size(lot.lot_id) == 0


@pytest.mark.parametrize('prior', ['0', '.13'])
def test_cancelled_exit_gap_closes_effective_remaining_without_false_fill(tmp_path, monkeypatch, prior):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'pending', '2722', size='1.88')
    ledger.claim_order_intent(signal_id=f'exit:{lot.lot_id}', client_order_id='A5EXold',
        side=lot.side, kind='addon', requested_size=D('1.88'))
    ledger.update_order_intent(f'exit:{lot.lot_id}', 'submitted', exchange_order_id='old')
    client.orders['old'] = dict(ordId='old', clOrdId='A5EXold', state='canceled',
        accFillSz=prior, avgPx='2699')
    client._position('short', -D(prior)-D('.04'))
    tick()
    assert [e[2] for e in client.trace if e[0] == 'reduce'] == [D('1.84')-D(prior)]
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert ledger.virtual_excluded_size(lot.lot_id) == D('.04')
    expected = (D('2722')-D('2699'))*D('1.84')*D('.1')-(D('2722')+D('2699'))*D('1.84')*D('.1')*D('.0005')
    assert ledger.recovery_pool()['total_net_profit'] == expected


@pytest.mark.parametrize('state', ['filled', 'canceled'])
def test_terminal_short_fill_plus_flat_retires_gap_and_credits_real_fill_once(tmp_path, monkeypatch, state):
    client, ledger, _, tick = quiet(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'pending', '2722', size='1.88')
    ledger.claim_order_intent(signal_id=f'exit:{lot.lot_id}', client_order_id='A5EXold',
        side=lot.side, kind='addon', requested_size=D('1.88'))
    ledger.update_order_intent(f'exit:{lot.lot_id}', 'submitted', exchange_order_id='old')
    client.orders['old'] = dict(ordId='old', state=state, accFillSz='1.84', avgPx='2699')
    client.positions = []
    tick()
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert ledger.virtual_excluded_size(lot.lot_id) == D('.04')
    expected = (D('2722')-D('2699'))*D('1.84')*D('.1')-(D('2722')+D('2699'))*D('1.84')*D('.1')*D('.0005')
    assert ledger.recovery_pool()['total_net_profit'] == expected
    tick()
    assert ledger.recovery_pool()['total_net_profit'] == expected
    assert not client.entries and not client.take_profits


def test_manual_reduction_during_market_request_books_only_capped_real_fill(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'race', '2722', size='1.88')
    place = client.place_market_reduce
    def server_caps_reduce_only(**payload):
        client._position('short', -D('.04'))
        # The server's reduce-only fill cannot exceed its remaining position.
        return place(**{**payload, 'size': D('1.84')})
    client.place_market_reduce = server_caps_reduce_only
    assert tick().action == 'submitted'
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert ledger.virtual_excluded_size(lot.lot_id) == D('.04')
    assert [e[2] for e in client.trace if e[0]=='reduce'] == [D('1.84')]
    expected = (D('2722')-D('2699'))*D('1.84')*D('.1')-(D('2722')+D('2699'))*D('1.84')*D('.1')*D('.0005')
    assert ledger.recovery_pool()['total_net_profit'] == expected
    assert len(client.entries) == 1
