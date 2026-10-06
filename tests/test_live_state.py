from datetime import datetime, timedelta, timezone

import pytest

from quantbot.live_state import LiveStateStore, approval_phrase, candidate_identifier
from quantbot.live_strategy import LIVE_PROFILES, LiveCandidate


def _candidate(profile="conservative", bar="2026-08-24T09:00:00+00:00"):
    return LiveCandidate(
        True, "test", 1, 2460.0, 2459.0, 2461.5, "0.05", 12.30, 0.005, 1.5,
        bar, profile=profile,
    )


def test_candidate_ledger_is_idempotent_and_approval_is_one_time(tmp_path):
    store = LiveStateStore(tmp_path / "conservative" / "state.sqlite3", "conservative")
    now = datetime(2026, 8, 24, 9, tzinfo=timezone.utc)
    candidate = _candidate()
    candidate_id = store.register_candidate(candidate, now=now)
    assert candidate_id == candidate_identifier("conservative", candidate)
    assert store.register_candidate(candidate, now=now + timedelta(seconds=1)) == candidate_id
    assert store.daily_candidate_count("2026-08-24") == 1
    with pytest.raises(ValueError, match="Exact"):
        store.approve_once(candidate_id, "YES", now=now)
    payload = store.approve_once(candidate_id, approval_phrase(candidate_id), now=now)
    assert payload["contracts"] == "0.05"
    with pytest.raises(ValueError, match="already consumed"):
        store.approve_once(candidate_id, approval_phrase(candidate_id), now=now)


def test_all_three_automatic_accounts_have_no_candidate_cooldown(tmp_path):
    now = datetime(2026, 8, 24, 9, tzinfo=timezone.utc)
    conservative = LiveStateStore(tmp_path / "conservative.sqlite3", "conservative")
    conservative.register_candidate(_candidate(bar="a"), now=now)
    conservative.register_candidate(_candidate(bar="b"), now=now + timedelta(seconds=1))
    prudent = LiveStateStore(tmp_path / "prudent.sqlite3", "prudent")
    prudent.register_candidate(_candidate("prudent", "a"), now=now)
    assert prudent.daily_candidate_count("2026-08-24") == 1


def test_rejects_ineligible_and_wrong_profile(tmp_path):
    store = LiveStateStore(tmp_path / "state.sqlite3", "conservative")
    with pytest.raises(ValueError, match="Ineligible"):
        store.register_candidate(LiveCandidate(False, "wait", profile="conservative"))
    with pytest.raises(ValueError, match="mismatch"):
        store.register_candidate(_candidate("prudent"))


def test_aggressive_automatic_cycle_has_no_time_cooldown(tmp_path):
    store = LiveStateStore(tmp_path / "aggressive.sqlite3", "aggressive")
    now = datetime(2026, 8, 24, 9, tzinfo=timezone.utc)
    assert store.claim_automatic_cycle(now=now) == 1
    assert store.claim_automatic_cycle(now=now + timedelta(seconds=1)) == 2
    assert store.claim_automatic_cycle(now=now + timedelta(seconds=2)) == 3


def test_automatic_cycle_accepts_legacy_naive_utc_timestamp(tmp_path):
    store = LiveStateStore(tmp_path / "aggressive-legacy.sqlite3", "aggressive")
    now = datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc)
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO engine_cycles VALUES (?,?,?)",
            ("aggressive", "2026-01-01", "2026-01-01T12:00:00"),
        )
    assert store.claim_automatic_cycle(now=now) == 2


def test_automatic_candidates_do_not_stop_at_one_hundred_cycles(tmp_path, monkeypatch):
    monkeypatch.setitem(LIVE_PROFILES["aggressive"], "cooldown_minutes", 0)
    store = LiveStateStore(tmp_path / "aggressive-unlimited-candidates.sqlite3", "aggressive")
    now = datetime(2026, 8, 24, 0, tzinfo=timezone.utc)
    for index in range(101):
        assert store.claim_automatic_cycle(now=now + timedelta(seconds=index)) == index + 1
