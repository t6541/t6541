import json
from pathlib import Path


def test_stage_one_unifies_all_live_review_sections_at_one_cutoff():
    root = Path(__file__).resolve().parents[1]
    registry = json.loads(
        (root / "knowledge-base" / "live-optimization-stages.json").read_text(encoding="utf-8")
    )
    stage = registry["stages"][0]
    assert stage["stage"] == "stage_1"
    assert stage["cutoff_beijing"] == "2026-09-01T16:45:00+08:00"
    assert stage["live_research"]["snapshots"]["last_rowid"] == 2278
    assert stage["trade_details"]["last_rowid"] == 51
    assert stage["loss_reviews"]["last_rowid"] == 46
    assert stage["shared_experiment"]["touches"]["last_id"] == 6243
    assert stage["shared_experiment"]["demo_orders"]["last_rowid"] == 91
    assert stage["next_stage"] == "stage_2"
