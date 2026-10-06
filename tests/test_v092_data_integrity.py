from quantbot.state import StateStore


def test_external_loss_never_matches_future_snapshot_with_different_offset(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.record_entry_snapshot(
        snapshot_uid="FUTURE", strategy_id="strategy_02", strategy_version="v2",
        order_id="open-future", signal_time="2026-08-16T01:30:00+00:00", direction=1,
        branch="structure_sniper", trigger_reason="future", context={},
        entry_reference=1880, stop_price=1876, take_profit_price=1886,
    )
    store.record_external_loss(
        trade_uid="CLOSE1", strategy_id="strategy_02",
        close_time="2026-08-16T09:00:00+08:00", direction=1,
        gross_pnl=-1, fee=-.01, evidence="OKX close",
    )
    assert store.loss_reviews()[0]["trigger_snapshot"] != "future"
    store.close()
