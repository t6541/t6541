from quantbot.hedge_entry import same_side_entry_conflicts


def test_opposite_protected_position_does_not_block_hedged_entry():
    snapshot = {
        "positions": [{"posSide": "long", "pos": "1"}],
        "orders": [],
        "algo_orders": [{"posSide": "long", "algoId": "protection"}],
    }
    assert same_side_entry_conflicts(snapshot, -1) == []


def test_same_side_position_or_opening_order_blocks_duplicate_entry():
    position = {"posSide": "short", "pos": "1"}
    order = {"posSide": "short", "clOrdId": "ordinary-short", "reduceOnly": "false"}
    snapshot = {"positions": [position], "orders": [order], "algo_orders": []}
    assert same_side_entry_conflicts(snapshot, -1) == [position, order]


def test_own_structure_lines_are_not_same_side_duplicate_conflicts():
    snapshot = {
        "positions": [],
        "orders": [{"posSide": "short", "clOrdId": "QBRSNP202608210020S"}],
        "algo_orders": [],
    }
    assert same_side_entry_conflicts(snapshot, -1, sniper_prefix="QBRSNP") == []
