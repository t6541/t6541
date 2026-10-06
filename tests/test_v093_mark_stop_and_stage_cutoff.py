import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_formal_and_resting_demo_entries_use_mark_price_stops():
    execution_sources = (
        ROOT / "src" / "quantbot" / "validation_execution.py",
        ROOT / "src" / "quantbot" / "range_execution.py",
        ROOT / "src" / "quantbot" / "ma20_execution.py",
    )
    for source in execution_sources:
        text = source.read_text(encoding="utf-8")
        assert 'stop_loss_trigger_type="last"' not in text
        assert 'stop_loss_trigger_type="mark"' in text

    okx_source = (ROOT / "src" / "quantbot" / "okx.py").read_text(encoding="utf-8")
    assert '"slOrdPx": "-1", "slTriggerPxType": "last"' not in okx_source
    assert okx_source.count('"slOrdPx": "-1", "slTriggerPxType": "mark"') >= 1


def test_stage_two_handoff_has_stable_incremental_watermarks():
    registry = json.loads((ROOT / "knowledge-base" / "data-review-stages.json").read_text(encoding="utf-8"))
    stage = registry["stages"][0]
    assert stage["cutoff_recorded_at"] == "2026-08-23T00:32:22+08:00"
    assert stage["trade_details"]["next_new_order_filter"] == "rowid > 127"
    assert stage["loss_reviews"]["next_filter"] == "rowid > 290"
    assert stage["shared_observation"]["next_filter"] == "id > 3582"
    assert stage["shared_demo_validation"]["next_filter"] == "rowid > 66"
