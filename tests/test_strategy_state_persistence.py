import pytest

from quantbot.state import (
    InvalidStrategyTransition,
    StateStore,
    StrategyRevisionConflict,
)
from quantbot.strategy_control import StrategyState


def test_runtime_state_defaults_safe_and_survives_restart(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = StateStore(path)
    initial = store.strategy_runtime_state("account01", "aggressive")
    assert initial.state is StrategyState.STOPPED
    assert initial.revision == 0
    store.transition_strategy_state(
        "account01", "aggressive", StrategyState.RUNNING,
        actor="operator", reason_code="operator_start", request_id="req-1",
        expected_revision=0, snapshot={"positions": 0},
    )
    store.close()

    reopened = StateStore(path)
    restored = reopened.strategy_runtime_state("account01", "aggressive")
    assert restored.state is StrategyState.RUNNING
    assert restored.revision == 1


def test_transition_is_idempotent_and_audited_once(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    first = store.transition_strategy_state(
        "account01", "aggressive", StrategyState.PAUSED,
        actor="operator", reason_code="maintenance", request_id="same-request",
        expected_revision=0, snapshot={"open_orders": 2},
    )
    replay = store.transition_strategy_state(
        "account01", "aggressive", StrategyState.PAUSED,
        actor="operator", reason_code="maintenance", request_id="same-request",
        expected_revision=0,
    )
    assert replay == first
    history = store.strategy_state_history("account01", "aggressive")
    assert len(history) == 1
    assert history[0]["snapshot_json"] == '{"open_orders": 2}'


def test_stale_revision_and_invalid_transition_do_not_mutate_state(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.transition_strategy_state(
        "account01", "aggressive", StrategyState.RUNNING,
        actor="operator", reason_code="start", request_id="start",
        expected_revision=0,
    )
    with pytest.raises(StrategyRevisionConflict):
        store.transition_strategy_state(
            "account01", "aggressive", StrategyState.PAUSED,
            actor="operator", reason_code="stale", request_id="stale",
            expected_revision=0,
        )
    with pytest.raises(InvalidStrategyTransition):
        store.transition_strategy_state(
            "account01", "aggressive", StrategyState.ERROR_LOCKED,
            actor="operator", reason_code="not-risk", request_id="bad-lock",
            expected_revision=1,
        )
    current = store.strategy_runtime_state("account01", "aggressive")
    assert current.state is StrategyState.RUNNING
    assert current.revision == 1
    assert len(store.strategy_state_history("account01", "aggressive")) == 1


def test_risk_unlock_and_close_completion_require_evidence(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    locked = store.transition_strategy_state(
        "account01", "aggressive", StrategyState.ERROR_LOCKED,
        actor="risk", reason_code="daily_loss", request_id="lock",
        expected_revision=0,
    )
    with pytest.raises(InvalidStrategyTransition):
        store.transition_strategy_state(
            "account01", "aggressive", StrategyState.PAUSED,
            actor="recovery", reason_code="unchecked", request_id="unlock-bad",
            expected_revision=locked.revision,
        )
    unlocked = store.transition_strategy_state(
        "account01", "aggressive", StrategyState.PAUSED,
        actor="recovery", reason_code="health_ok", request_id="unlock",
        expected_revision=locked.revision, health_check_passed=True,
    )
    closing = store.transition_strategy_state(
        "account01", "aggressive", StrategyState.CLOSING,
        actor="operator", reason_code="close", request_id="close",
        expected_revision=unlocked.revision,
    )
    with pytest.raises(InvalidStrategyTransition):
        store.transition_strategy_state(
            "account01", "aggressive", StrategyState.STOPPED,
            actor="system", reason_code="not_reconciled", request_id="stop-bad",
            expected_revision=closing.revision,
        )
    stopped = store.transition_strategy_state(
        "account01", "aggressive", StrategyState.STOPPED,
        actor="system", reason_code="reconciled", request_id="stop",
        expected_revision=closing.revision, close_reconciled=True,
    )
    assert stopped.state is StrategyState.STOPPED
    assert stopped.revision == 4
