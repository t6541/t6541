import sqlite3
from pathlib import Path

from quantbot.state import StateStore


RETIRED_TABLES = {
    "market_research_labels",
    "market_research_snapshots",
    "external_research_signals",
    "webhook_trade_signals",
    "ma_deviation_touches",
    "ma_experiment_demo_orders",
}


def test_startup_retires_only_obsolete_business_tables(tmp_path):
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    for table in RETIRED_TABLES:
        connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        connection.execute(f'INSERT INTO "{table}" VALUES (1)')
    connection.commit()
    connection.close()

    store = StateStore(path)
    try:
        tables = {
            row[0] for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        store.close()

    assert not (tables & RETIRED_TABLES)
    assert {
        "trade_lifecycle", "range_pivot_intents",
        "market_pattern_observations", "loss_reviews",
    } <= tables


def test_retired_runtime_modules_are_removed():
    package = Path(__file__).parents[1] / "src" / "quantbot"
    for name in (
        "ma_deviation_experiment.py",
        "ma_experiment_execution.py", "pinets_comparison.py", "pinets_live.py",
        "pinets_research.py", "snapshot_view.py", "webhook_live.py",
        "research_snapshots.py", "luxalgo_research.py",
    ):
        assert not (package / name).exists()


def test_retired_integration_assets_and_state_apis_are_removed():
    root = Path(__file__).parents[1]
    for name in (
        "pinets-research", "edge-extension-v0.7.85",
        "edge_snapshot_sidecar.py", "COPY-TRADINGVIEW-WEBHOOK-MESSAGE.ps1",
        "START-TEMP-WEBHOOK-PINGGY.ps1", "START-TEMP-WEBHOOK-RECEIVER.ps1",
        "START-TEMP-WEBHOOK-TUNNEL.ps1", "TEST-TEMP-WEBHOOK.ps1",
        "tools/build-pinets-comparison.py",
    ):
        assert not (root / name).exists()

    for name in (
        "record_ma_deviation_touch", "pending_ma_deviation_touches",
        "advance_ma_deviation_touch", "ma_deviation_statistics",
        "recent_ma_deviation_touches", "record_ma_experiment_demo_order",
        "ma_experiment_demo_orders", "update_ma_experiment_demo_order",
        "ma_experiment_demo_statistics",
    ):
        assert not hasattr(StateStore, name)


def test_package_excludes_old_node_and_research_bundle():
    spec = (Path(__file__).parents[1] / "quantbot_v096.spec").read_text(encoding="utf-8")
    assert "node.exe" not in spec
    assert "pinets-research" not in spec
    assert "CodexQuantBot-v0.7.282-Flat-Base-Reconcile" in spec
    assert "uac_admin=True" in spec

