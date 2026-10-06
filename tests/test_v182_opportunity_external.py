from datetime import datetime, timedelta, timezone
import json

import pytest

from quantbot.external_execution import latest_external_signal, parse_external_signal, pinets_market_signal
from quantbot.state import StateStore


def test_daily_order_quota_is_absent_from_current_execution_source():
    from pathlib import Path
    from quantbot import validation_execution
    source = Path(validation_execution.__file__).read_text(encoding="utf-8")
    assert "opportunity_first" not in source
    assert "AGGRESSIVE_DAILY_ORDER_TARGET" not in source
    assert '"daily_target"' not in source


def test_external_inputs_are_research_only_in_live_execution_source():
    from pathlib import Path
    from quantbot import validation_execution
    source = Path(validation_execution.__file__).read_text(encoding="utf-8")
    assert "external_execution_entry = False" in source
    assert "external_signal_research_only" in source

def payload(**changes):
    base = {"event_id": "edge-001", "source": "edge", "direction": "short",
            "occurred_at": datetime.now(timezone.utc).isoformat(), "price": 2500,
            "stop_loss": 2503, "take_profit": 2495,
            "structure": "top_reversal_short", "indicators": {"ma5_turn": "down"}}
    base.update(changes)
    return base

def test_external_sources_require_fresh_explicit_protected_direction():
    signal = parse_external_signal(payload())
    assert signal.source == "edge" and signal.direction == -1
    with pytest.raises(ValueError):
        parse_external_signal(payload(direction="observe"))
    with pytest.raises(ValueError):
        parse_external_signal(payload(occurred_at=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()))
    with pytest.raises(ValueError):
        parse_external_signal(payload(stop_loss=2499))

def test_external_inbox_selects_freshest_and_claims_once(tmp_path):
    database = tmp_path / "strategy.sqlite3"
    inbox = tmp_path / "external-execution-signals.jsonl"
    now = datetime.now(timezone.utc)
    older = payload(event_id="pine-1", source="pinets", direction="long", price=2500,
                    stop_loss=2497, take_profit=2505,
                    occurred_at=(now-timedelta(seconds=20)).isoformat())
    newer = payload(event_id="hook-2", source="webhook", occurred_at=now.isoformat())
    inbox.write_text(json.dumps(older)+"\n"+json.dumps(newer)+"\n", encoding="utf-8")
    assert latest_external_signal(database, now=now).event_id == "hook-2"
    store = StateStore(database)
    try:
        assert not store.external_execution_claimed("hook-2")
        store.set_external_execution_status("hook-2", "webhook", "submitted", "ordId=1")
        assert store.external_execution_claimed("hook-2")
    finally:
        store.close()

def test_pinets_consensus_produces_auditable_direction():
    import pandas as pd
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    def frame(step):
        prices = [2400 + i * step for i in range(60)]
        return pd.DataFrame({"date": pd.date_range(end=now, periods=60, freq="min", tz="UTC"),
                             "open": prices, "high": [p+1 for p in prices],
                             "low": [p-1 for p in prices], "close": prices})
    signal = pinets_market_signal(frame(1.0), frame(2.0), now=now)
    assert signal is not None and signal.direction == 1 and signal.source == "pinets"
    assert signal.indicators["score"] >= signal.indicators["required_abs_score"]
