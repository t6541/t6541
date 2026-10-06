from datetime import datetime, timedelta, timezone

from quantbot.state import StateStore
from quantbot.validation_execution import active_exit_requires_fee_gate, confirmed_ma5_turn_exit, fee_aware_active_exit_allows


def _open(store: StateStore, uid: str = "T1", direction: int = 1) -> None:
    store.open_trade_lifecycle(
        trade_uid=uid, strategy_id="strategy_01", strategy_version="v165",
        instrument="ETH-USDT-SWAP", direction=direction,
        signal_time=datetime.now(timezone.utc).isoformat(), signal_reason="test",
        signal_context={}, order_id="O1", algo_id="A1", entry_reference=2500.0,
        stop_price=2496.0 if direction > 0 else 2504.0,
        trailing_activation=2505.0, trailing_callback=.5, branch="test",
    )


def test_fee_gate_rejects_gross_profit_that_would_still_be_net_negative():
    allowed, reason = fee_aware_active_exit_allows(2500.0, 2501.5, 1)
    assert not allowed
    assert "预计往返手续费" in reason


def test_fee_gate_requires_cost_plus_minimum_net_profit():
    assert fee_aware_active_exit_allows(2500.0, 2504.0, 1)[0]
    assert fee_aware_active_exit_allows(2500.0, 2496.0, -1)[0]


def test_confirmed_ma5_turn_is_a_risk_exit_and_cannot_be_vetoed_by_fee_gate():
    assert confirmed_ma5_turn_exit(
        "激进型空单主动止盈：一分钟MA5已由下降转为走平或向上拐弯", -1)
    assert confirmed_ma5_turn_exit(
        "多单主动止盈（快速行情MA5止盈）：MA5上升转为走平或向下拐弯", 1)
    assert not confirmed_ma5_turn_exit("普通提前锁利", -1)
    assert not active_exit_requires_fee_gate(
        "激进型空单主动止盈：一分钟MA5已由下降转为走平或向上拐弯", -1)
    assert active_exit_requires_fee_gate("普通提前锁利", -1)


def test_trade_excursion_persists_mfe_and_mae(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    _open(store)
    store.update_trade_excursion("T1", observed_high=2506.0, observed_low=2498.0,
                                 observed_price=2503.0)
    row = store.connection.execute(
        "SELECT * FROM trade_lifecycle WHERE trade_uid='T1'").fetchone()
    assert row["mfe_points"] == 6.0
    assert row["mae_points"] == 2.0
    assert row["last_observed_price"] == 2503.0
    store.close()


def test_stop_followup_checkpoints_are_filled_without_touching_trade_state(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    _open(store)
    store.close_trade_lifecycle("T1", close_price=2496.0, gross_pnl=-.02,
                                total_fees=-.012, exit_reason="stop_loss")
    old = (datetime.now(timezone.utc) - timedelta(minutes=21)).isoformat()
    store.connection.execute("UPDATE trade_lifecycle SET close_time=? WHERE trade_uid='T1'", (old,))
    store.connection.commit()
    store.update_stop_followups(2510.0)
    row = store.connection.execute(
        "SELECT * FROM trade_lifecycle WHERE trade_uid='T1'").fetchone()
    assert row["status"] == "closed"
    assert row["stop_followup_price_5m"] == 2510.0
    assert row["stop_followup_price_10m"] == 2510.0
    assert row["stop_followup_price_20m"] == 2510.0
    store.close()
