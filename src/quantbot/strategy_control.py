"""Auditable strategy lifecycle rules.

This module is deliberately independent from exchange I/O.  Callers use the
same permission decision immediately before creating or increasing exposure;
position protection and exits must never be routed through this gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StrategyState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    NO_ADD = "no_add"
    SINGLE_CYCLE = "single_cycle"
    CLOSING = "closing"
    ERROR_LOCKED = "error_locked"


class ExposureAction(StrEnum):
    OPEN = "open"
    ADD = "add"


@dataclass(frozen=True)
class ExposurePermission:
    allowed: bool
    reason_code: str


_OPERATOR_TRANSITIONS: dict[StrategyState, frozenset[StrategyState]] = {
    StrategyState.STOPPED: frozenset({StrategyState.RUNNING, StrategyState.PAUSED}),
    StrategyState.RUNNING: frozenset({
        StrategyState.PAUSED, StrategyState.NO_ADD, StrategyState.SINGLE_CYCLE,
        StrategyState.CLOSING, StrategyState.STOPPED,
    }),
    StrategyState.PAUSED: frozenset({
        StrategyState.RUNNING, StrategyState.NO_ADD, StrategyState.SINGLE_CYCLE,
        StrategyState.CLOSING, StrategyState.STOPPED,
    }),
    StrategyState.NO_ADD: frozenset({
        StrategyState.RUNNING, StrategyState.PAUSED, StrategyState.SINGLE_CYCLE,
        StrategyState.CLOSING, StrategyState.STOPPED,
    }),
    StrategyState.SINGLE_CYCLE: frozenset({
        StrategyState.RUNNING, StrategyState.PAUSED, StrategyState.NO_ADD,
        StrategyState.CLOSING, StrategyState.STOPPED,
    }),
    StrategyState.CLOSING: frozenset(),
    StrategyState.ERROR_LOCKED: frozenset({StrategyState.CLOSING}),
}


def transition_allowed(current: StrategyState, target: StrategyState, *,
                       actor: str, health_check_passed: bool = False,
                       close_reconciled: bool = False) -> bool:
    """Return whether a lifecycle transition is valid.

    System/risk may always lock a strategy.  Unlocking an error requires a
    fresh health check, while completing CLOSING requires exchange
    reconciliation.  Repeating the current state is an idempotent success.
    """
    current, target = StrategyState(current), StrategyState(target)
    actor = str(actor).strip().lower()
    if current == target:
        return True
    if target == StrategyState.ERROR_LOCKED:
        return actor in {"system", "risk", "recovery"}
    if current == StrategyState.ERROR_LOCKED:
        if target == StrategyState.PAUSED:
            return actor in {"system", "recovery"} and health_check_passed
        return target == StrategyState.CLOSING and actor in {"operator", "system", "risk"}
    if current == StrategyState.CLOSING:
        return (target == StrategyState.STOPPED and close_reconciled
                and actor in {"system", "recovery"})
    return actor == "operator" and target in _OPERATOR_TRANSITIONS[current]


def exposure_permission(state: StrategyState, action: ExposureAction, *,
                        has_open_exposure: bool, cycle_admitted: bool = False) -> ExposurePermission:
    """Decide whether a strategy may create or increase exposure."""
    state, action = StrategyState(state), ExposureAction(action)
    if state == StrategyState.RUNNING:
        return ExposurePermission(True, "running_allows_exposure")
    if state == StrategyState.NO_ADD:
        if action == ExposureAction.OPEN and not has_open_exposure:
            return ExposurePermission(True, "no_add_allows_new_base_cycle")
        return ExposurePermission(False, "no_add_blocks_exposure_increase")
    if state == StrategyState.SINGLE_CYCLE:
        if cycle_admitted or has_open_exposure:
            return ExposurePermission(True, "single_cycle_already_admitted")
        if action == ExposureAction.OPEN:
            return ExposurePermission(True, "single_cycle_admits_one_cycle")
        return ExposurePermission(False, "single_cycle_requires_base_entry")
    return ExposurePermission(False, f"state_{state.value}_blocks_new_exposure")


def single_cycle_should_pause(state: StrategyState, *, positions_empty: bool,
                              owned_orders_empty: bool) -> bool:
    """Only complete a single cycle after both positions and owned orders clear."""
    return (StrategyState(state) == StrategyState.SINGLE_CYCLE
            and positions_empty and owned_orders_empty)
