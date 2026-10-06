import json
from pathlib import Path


def test_stage_one_loss_review_cutoff_is_immutable_and_auditable():
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "knowledge-base" / "loss-review-stages.json").read_text(encoding="utf-8"))
    stage = registry["stages"][0]
    assert stage["stage"] == "stage_1"
    assert stage["loss_review_rows"] == 239
    assert stage["closed_at"] == "2026-08-20T13:19:42.948000+08:00"
    assert stage["last_trade_uid"] == "OKX-strategy_03-3295783939"
    assert stage["database_integrity"] == "ok"


def test_stage_two_cutoff_covers_all_three_review_sections():
    root = Path(__file__).resolve().parents[1]
    loss_registry = json.loads((root / "knowledge-base" / "loss-review-stages.json").read_text(encoding="utf-8"))
    loss_stage = loss_registry["stages"][1]
    assert loss_stage["stage"] == "stage_2"
    assert loss_stage["last_rowid"] == 290
    assert loss_stage["next_stage_filter"] == "rowid > 290"

    registry = json.loads((root / "knowledge-base" / "data-review-stages.json").read_text(encoding="utf-8"))
    stage = registry["stages"][0]
    assert stage["stage"] == "stage_2"
    assert stage["trade_details"]["last_rowid"] == 127
    assert stage["loss_reviews"]["last_rowid"] == 290
    assert stage["shared_observation"]["last_id"] == 3582
    assert stage["shared_demo_validation"]["last_rowid"] == 66
    assert stage["next_stage"] == "stage_3"
