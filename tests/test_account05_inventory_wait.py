"""Read-only position/history disagreement must wait and retry, not trade."""
from decimal import Decimal as D

import pytest

from test_extreme_profit_sweep import setup_case, add_lot
from quantbot.account05_strategy import PositionSide
from quantbot.account05_execution import import_manual_orders


@pytest.mark.parametrize('direction', [1, -1])
def test_feedback_quantity_gap_waits_without_clipping_or_trading(tmp_path, monkeypatch, direction):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch, direction)
    side = PositionSide.SHORT if direction == 1 else PositionSide.LONG
    price = '2722' if direction == 1 else '2670'
    lot = add_lot(client, ledger, 'manual', price, manual=True, side=side, size='1.88')
    client._position(side.value, -D('.04'))
    for _ in range(2):
        result = tick()
        assert result.action == 'reconcile_wait'
        assert '1.88' in result.reason and '1.84' in result.reason and '0.04' in result.reason
        assert ledger.get_lot(lot.lot_id).remaining_size == D('1.88')
        assert not client.entries
        assert not client.take_profits
        assert not [e for e in client.trace if e[0] == 'reduce']
    # A delayed close record resolves the difference; the original extreme
    # event was never consumed while waiting. It can now complete once.
    client.history.append(dict(ordId='manual-close', clOrdId='', posSide=side.value,
        side='buy' if direction == 1 else 'sell', state='filled',
        accFillSz='.04', avgPx=str(client.price), cTime='1791330001000'))
    assert tick().action == 'submitted'
    assert ledger.get_lot(lot.lot_id).status == 'closed'
    assert [e[2] for e in client.trace if e[0] == 'reduce'] == [D('1.84')]
    assert len(client.entries) == 1
    tick()
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
