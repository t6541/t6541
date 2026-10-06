from pathlib import Path

from quantbot.trade_cycle_rules import build_entry_rule_audit


def test_entry_audit_separates_execution_gates_from_other_template_evidence():
    audit = build_entry_rule_audit(
        direction=-1, classification={"label": "局部顶部做空", "category": "顶部反转",
                                      "rule": "近3根局部高点"},
        entry_kind="aggressive_five_minute_high_half_cover_short",
        three_stage_audit={"conditions": [{"name": "1m旧模板", "matched": False,
                                            "evidence": "另一个模板尚未形成"}]},
        cover_ok=True, position_ok=True, profit_space_ok=True,
        indicator_ok=True, stop_side_ok=True)
    assert audit["complete"]
    assert len(audit["conditions"]) == 6
    assert audit["three_stage_conditions"][0]["matched"] is False


def test_lifecycle_dashboard_has_distinct_long_short_and_order_panels():
    source = (Path(__file__).parents[1] / "src/quantbot/win32desktop.py").read_text(encoding="utf-8")
    assert '"生命周期"' in source
    assert "def _show_lifecycle_dashboard()" in source
    assert 'HANDLES["lifecycle_dashboard_long"]' in source
    assert 'HANDLES["lifecycle_dashboard_short"]' in source
    assert 'HANDLES["lifecycle_dashboard_orders"]' in source
    assert "user32.ShowWindow(hwnd, SW_MAXIMIZE)" in source
    assert 'context.get("entry_rule_audit")' in source
