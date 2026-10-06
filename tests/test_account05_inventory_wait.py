"""Read-only position/history disagreement must wait and retry, not trade."""
from decimal import Decimal as D

import pytest

from test_extreme_profit_sweep import setup_case, add_lot
from quantbot.account05_strategy import PositionSide
from quantbot.account05_execution import import_manual_orders


@pytest.mark.parametrize('direction', [1, -1])
def test_exchange_quantity_wins_and_only_real_exit_is_credited(tmp_path, monkeypatch, direction):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch, direction)
    side = PositionSide.SHORT if direction == 1 else PositionSide.LONG
    price = '2722' if direction == 1 else '2670'
    lot = add_lot(client, ledger, 'manual', price, manual=True, side=side, size='1.88')
    client._position(side.value, -D('.04'))
    assert tick().action == 'submitted'
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert ledger.virtual_excluded_size(lot.lot_id) == D('.04')
    assert [e[2] for e in client.trace if e[0] == 'reduce'] == [D('1.84')]
    gross = abs(D(price) - client.price) * D('1.84') * D('.1')
    fee = (D(price) + client.price) * D('1.84') * D('.1') * D('.0005')
    assert ledger.recovery_pool()['total_net_profit'] == gross - fee
    pool = ledger.recovery_pool()
    tick()
    assert ledger.recovery_pool() == pool
    assert len(client.entries) == 1


def test_known_automatic_order_without_client_id_is_not_imported_twice(tmp_path, monkeypatch):
    client, ledger, _, _ = setup_case(tmp_path, monkeypatch)
    add_lot(client, ledger, 'auto-1', '2722', size='.04')
    client.history.append(dict(ordId='auto', clOrdId='', posSide='short', side='sell',
        state='filled', accFillSz='.04', avgPx='2722', cTime='1791330000000'))
    assert import_manual_orders(client, ledger, require_fresh=True) == 0
    assert len(ledger.open_lots()) == 1
    assert ledger.connection.execute("SELECT 1 FROM account05_lots WHERE lot_id='manual:auto'").fetchone() is None


def test_known_partial_exit_is_read_before_position_coverage(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    lot = add_lot(client, ledger, 'auto', '2722', size='1.88')
    ledger.claim_order_intent(signal_id=f'exit:{lot.lot_id}', client_order_id='A5EXold',
        side=lot.side, kind='addon', requested_size=D('1.88'))
    ledger.update_order_intent(f'exit:{lot.lot_id}', 'submitted', exchange_order_id='old')
    client.orders['old'] = dict(ordId='old', clOrdId='A5EXold', state='partially_filled',
        accFillSz='.04', avgPx='2699')
    client._position('short', -D('.04'))
    assert tick().action == 'submitted'
    assert [e[2] for e in client.trace if e[0] == 'reduce'] == [D('1.84')]
    assert ledger.get_lot(lot.lot_id).status == 'closed'
