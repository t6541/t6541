from quantbot.strategy_control import (
    ExposureAction,
    StrategyState,
    exposure_permission,
    single_cycle_should_pause,
    transition_allowed,
)


def test_paused_blocks_new_exposure_but_gate_is_not_used_for_exits():
    decision = exposure_permission(
        StrategyState.PAUSED, ExposureAction.OPEN, has_open_exposure=False)
    assert not decision.allowed
    assert decision.reason_code == "state_paused_blocks_new_exposure"


def test_no_add_allows_only_a_new_base_cycle_when_flat():
    assert exposure_permission(
        StrategyState.NO_ADD, ExposureAction.OPEN, has_open_exposure=False).allowed
    assert not exposure_permission(
        StrategyState.NO_ADD, ExposureAction.ADD, has_open_exposure=True).allowed
    assert not exposure_permission(
        StrategyState.NO_ADD, ExposureAction.OPEN, has_open_exposure=True).allowed


def test_single_cycle_requires_base_entry_then_allows_same_cycle_management():
    assert exposure_permission(
        StrategyState.SINGLE_CYCLE, ExposureAction.OPEN,
        has_open_exposure=False, cycle_admitted=False).allowed
    assert exposure_permission(
        StrategyState.SINGLE_CYCLE, ExposureAction.ADD,
        has_open_exposure=True, cycle_admitted=True).allowed
    assert not exposure_permission(
        StrategyState.SINGLE_CYCLE, ExposureAction.ADD,
        has_open_exposure=False, cycle_admitted=False).allowed


def test_single_cycle_completes_only_after_positions_and_orders_are_clear():
    assert not single_cycle_should_pause(
        StrategyState.SINGLE_CYCLE, positions_empty=True, owned_orders_empty=False)
    assert not single_cycle_should_pause(
        StrategyState.SINGLE_CYCLE, positions_empty=False, owned_orders_empty=True)
    assert single_cycle_should_pause(
        StrategyState.SINGLE_CYCLE, positions_empty=True, owned_orders_empty=True)


def test_risk_lock_cannot_be_bypassed_by_operator_resume():
    assert transition_allowed(
        StrategyState.RUNNING, StrategyState.ERROR_LOCKED, actor="risk")
    assert not transition_allowed(
        StrategyState.ERROR_LOCKED, StrategyState.RUNNING,
        actor="operator", health_check_passed=True)
    assert not transition_allowed(
        StrategyState.ERROR_LOCKED, StrategyState.PAUSED,
        actor="recovery", health_check_passed=False)
    assert transition_allowed(
        StrategyState.ERROR_LOCKED, StrategyState.PAUSED,
        actor="recovery", health_check_passed=True)


def test_closing_finishes_only_after_exchange_reconciliation():
    assert not transition_allowed(
        StrategyState.CLOSING, StrategyState.STOPPED,
        actor="system", close_reconciled=False)
    assert transition_allowed(
        StrategyState.CLOSING, StrategyState.STOPPED,
        actor="system", close_reconciled=True)


def test_repeating_state_is_idempotent():
    assert transition_allowed(
        StrategyState.PAUSED, StrategyState.PAUSED, actor="operator")
