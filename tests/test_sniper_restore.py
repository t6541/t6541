from quantbot.okx import OkxCredentials
from quantbot.sniper_restore import restore_demo_structure_snipers
from types import SimpleNamespace


def test_startup_restore_reconciles_saved_demo_accounts_only(tmp_path, monkeypatch):
    calls = []

    class Client:
        def __init__(self, credentials, timeout):
            self.credentials = credentials

        def require_swap_trading_mode(self):
            calls.append("demo_mode_checked")

        def safety_snapshot(self):
            return {"positions": [], "orders": [], "algo_orders": []}

    monkeypatch.setattr("quantbot.sniper_restore.OkxDemoClient", Client)
    monkeypatch.setattr("quantbot.sniper_restore.okx_history_market",
                        lambda instruments, bar, total: f"{bar}-market")

    class Result:
        action = "placed"
        reason = "缺少的Demo结构预埋单已补挂"
        order_ids = ("demo-order-1", "demo-order-2")

    def execute(client, snapshot, instrument, prefix, five, fifteen, one, **kwargs):
        calls.append((prefix, five, fifteen, one, kwargs["strategy_id"]))
        return Result()

    monkeypatch.setattr("quantbot.sniper_restore.execute_structure_sniper_tick", execute)
    credential = OkxCredentials("demo", "secret", "pass")
    results = restore_demo_structure_snipers(
        (("strategy_01", credential, "QBVAL", "v1"),
         ("strategy_02", None, "QBR", "v2")),
        "ETH-USDT-SWAP", tmp_path / "state.sqlite3",
    )
    assert results[0].action == "placed"
    assert results[0].order_ids == ("demo-order-1", "demo-order-2")
    assert results[1].action == "skipped"
    assert calls[0] == "demo_mode_checked"
    assert calls[1][:4] == ("QBVAL", "5m-market", "15m-market", "1m-market")
    assert not any(call == "ordinary_strategy_entry" for call in calls)


def test_strategy02_restore_reapplies_range_and_direction_gates(tmp_path, monkeypatch):
    captured = {}

    class Client:
        def __init__(self, credentials, timeout): pass
        def require_swap_trading_mode(self): pass
        def safety_snapshot(self):
            return {"positions": [], "orders": [], "algo_orders": []}

    monkeypatch.setattr("quantbot.sniper_restore.OkxDemoClient", Client)
    monkeypatch.setattr("quantbot.sniper_restore.okx_history_market",
                        lambda instruments, bar, total: f"{bar}-market")
    monkeypatch.setattr("quantbot.sniper_restore.confirmed_wick_pivots",
                        lambda frame, lookback: (110.0, 90.0))
    monkeypatch.setattr("quantbot.sniper_restore.latest_atr", lambda frame: 2.0)
    monkeypatch.setattr("quantbot.sniper_restore.five_minute_reversal_zone_allows",
                        lambda resistance, support, atr: (True, "ok"))
    monkeypatch.setattr("quantbot.sniper_restore.five_minute_ma_deviation",
                        lambda frame, resistance, support, atr: (True, False, 0.0, 0.0))
    monkeypatch.setattr("quantbot.sniper_restore.local_post_impulse_range",
                        lambda frame, atr: (False, 0.0, 0.0, "none"))

    class Result:
        action = "armed"
        reason = "ok"

    def execute(*args, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr("quantbot.sniper_restore.execute_structure_sniper_tick", execute)
    cfg = SimpleNamespace(strategy=SimpleNamespace(pivot_lookback=8))
    result = restore_demo_structure_snipers(
        (("strategy_02", OkxCredentials("demo", "secret", "pass"), "QBR", "v38"),),
        "ETH-USDT-SWAP", tmp_path / "state.sqlite3", range_config=cfg,
    )
    assert result[0].action == "armed"
    assert captured["allowed_directions"] == (-1, 1)
