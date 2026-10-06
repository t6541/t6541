from datetime import datetime, timezone

import pytest

from quantbot.live_audit import LiveAuditCredentials
from quantbot.live_execution import OkxLiveManualClient, execute_approved_minimum_order
from quantbot.live_state import LiveStateStore, approval_phrase
from quantbot.live_strategy import LiveCandidate


class CapturingClient(OkxLiveManualClient):
    def __init__(self):
        super().__init__(LiveAuditCredentials("key", "secret", "pass"))
        self.payloads = []

    def _post_order(self, payload):
        self.payloads.append(payload)
        return {"ordId": "123", "clOrdId": payload["clOrdId"]}


def _candidate(direction=1):
    prices = (2460, 2459, 2461.5) if direction == 1 else (2460, 2461, 2458.5)
    return LiveCandidate(
        True, "test", direction, *prices, "0.05", 12.30, .005, 1.5,
        "2026-08-24T09:00:00+00:00", profile="conservative",
    )


def _audit():
    return {
        "execution_permitted": False, "open_positions": [],
        "account": {"acctLv": "2", "posMode": "long_short_mode"},
        "leverage": [{"posSide": "long", "lever": "100"}, {"posSide": "short", "lever": "100"}],
        "minimum_order": {"api_size_contracts": "0.01"},
    }


@pytest.mark.parametrize("direction,side,pos_side", [(1, "buy", "long"), (-1, "sell", "short")])
def test_exact_approval_submits_one_protected_minimum_order(tmp_path, direction, side, pos_side):
    store = LiveStateStore(tmp_path / "state.sqlite3", "conservative")
    candidate_id = store.register_candidate(_candidate(direction), now=datetime(2026, 8, 24, 9, tzinfo=timezone.utc))
    client = CapturingClient()
    result = execute_approved_minimum_order(store, client, candidate_id, approval_phrase(candidate_id), _audit())
    assert result["ordId"] == "123"
    assert len(client.payloads) == 1
    payload = client.payloads[0]
    assert payload["instId"] == "ETH-USDT-SWAP"
    assert payload["tdMode"] == "cross"
    assert payload["side"] == side and payload["posSide"] == pos_side
    assert payload["ordType"] == "market" and payload["sz"] == "0.05"
    assert payload["attachAlgoOrds"][0]["slTriggerPxType"] == "mark"
    assert payload["attachAlgoOrds"][0]["slOrdPx"] == "-1"
    with pytest.raises(ValueError, match="already consumed"):
        execute_approved_minimum_order(store, client, candidate_id, approval_phrase(candidate_id), _audit())
    assert len(client.payloads) == 1


def test_preflight_rejection_does_not_consume_approval(tmp_path):
    store = LiveStateStore(tmp_path / "state.sqlite3", "conservative")
    candidate_id = store.register_candidate(_candidate(), now=datetime(2026, 8, 24, 9, tzinfo=timezone.utc))
    bad = _audit()
    bad["open_positions"] = [{"posSide": "long", "pos": "0.01"}]
    client = CapturingClient()
    with pytest.raises(ValueError, match="same-side position"):
        execute_approved_minimum_order(store, client, candidate_id, approval_phrase(candidate_id), bad)
    assert client.payloads == []
    execute_approved_minimum_order(store, client, candidate_id, approval_phrase(candidate_id), _audit())


def test_opposite_side_position_allows_independent_hedge_order(tmp_path):
    store = LiveStateStore(tmp_path / "state.sqlite3", "conservative")
    candidate_id = store.register_candidate(_candidate(-1), now=datetime(2026, 8, 24, 9, tzinfo=timezone.utc))
    audit = _audit()
    audit["open_positions"] = [{"posSide": "long", "pos": "0.01"}]
    client = CapturingClient()
    execute_approved_minimum_order(store, client, candidate_id, approval_phrase(candidate_id), audit)
    assert client.payloads[0]["posSide"] == "short"


def test_rejects_nonminimum_size_without_post():
    candidate = _candidate().__dict__.copy()
    candidate["contracts"] = "2.5"
    client = CapturingClient()
    with pytest.raises(ValueError, match="0.05"):
        client.place_minimum_protected_order(candidate, candidate_id="ABC", exchange_min_size="0.01")
    assert client.payloads == []
