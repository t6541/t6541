import pandas as pd

from quantbot.entry_risk import structure_profit_runway
from quantbot.shared_rules import RULE_CATALOG, selected_rules


def market():
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [100.0] * 30, "high": [101.0] * 30,
        "low": [99.0] * 30, "close": [100.5] * 30,
    })


def test_shared_runway_is_symmetric_for_short_support():
    frame = market()
    frame.loc[18:28, ["open", "close", "high", "low"]] = [90.5, 90.0, 91.0, 89.0]
    allowed, _, support = structure_profit_runway(frame, 95.0, -1, 1.0)
    assert allowed
    assert support == 90.0


def test_nearest_runway_rejects_short_chasing_into_support():
    frame = market()
    frame.loc[18:28, ["open", "close", "high", "low"]] = [92.0, 91.5, 93.0, 91.0]
    frame.loc[28, ["open", "close", "high", "low"]] = [94.8, 94.7, 95.2, 94.5]
    allowed, reason, support = structure_profit_runway(
        frame, 95.0, -1, 3.0, minimum_r=1.2, minimum_atr=.8,
        nearest_boundary=True,
    )
    assert not allowed
    assert support == 94.7
    assert "0.30" in reason


def test_nearest_runway_rejects_long_chasing_into_resistance():
    frame = market()
    frame.loc[18:28, ["open", "close", "high", "low"]] = [108.0, 108.5, 109.0, 107.0]
    frame.loc[28, ["open", "close", "high", "low"]] = [105.2, 105.3, 105.5, 104.8]
    allowed, _, resistance = structure_profit_runway(
        frame, 105.0, 1, 3.0, minimum_r=1.2, minimum_atr=.8,
        nearest_boundary=True,
    )
    assert not allowed
    assert resistance == 105.3


def test_nearest_runway_allows_early_reversal_with_clear_room():
    frame = market()
    frame.loc[18:28, ["open", "close", "high", "low"]] = [90.5, 90.0, 91.0, 89.0]
    allowed, _, support = structure_profit_runway(
        frame, 95.0, -1, 3.0, minimum_r=1.2, minimum_atr=.8,
        nearest_boundary=True,
    )
    assert allowed
    assert support == 90.0


def test_rule_catalog_keeps_strategy_selection_composable():
    ids = {rule.rule_id for rule in selected_rules("strategy_03")}
    assert "trend.ma20.retest" in ids
    assert "trend.regime.reversal" in ids
    assert "risk.structure.runway" in ids
    assert len(RULE_CATALOG) == len(set(RULE_CATALOG))


def test_early_low_sweep_rule_is_available_to_all_strategies():
    for strategy_id in ("strategy_01", "strategy_02", "strategy_03"):
        ids = {rule.rule_id for rule in selected_rules(strategy_id)}
        assert "extreme.low.sweep_reclaim" in ids


def test_early_high_sweep_rule_is_available_to_all_strategies():
    for strategy_id in ("strategy_01", "strategy_02", "strategy_03"):
        ids = {rule.rule_id for rule in selected_rules(strategy_id)}
        assert "extreme.high.sweep_reject" in ids


def test_future_shared_strategy_collects_all_current_rule_ids():
    ids = {rule.rule_id for rule in selected_rules("shared_strategy")}
    assert ids == set(RULE_CATALOG)
    assert "extreme.structure.sniper" in ids
    assert "risk.spike.circuit_breaker" not in ids
