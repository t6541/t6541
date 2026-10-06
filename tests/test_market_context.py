import pandas as pd

from quantbot.market_context import exchange_market_context
from quantbot.validation_execution import indicator_confirmation_gate


def _frame(direction=1, periods=80):
    close = pd.Series([100.0 + direction * index * .08 for index in range(periods)])
    return pd.DataFrame({"date": pd.date_range("2026-08-30", periods=periods, freq="1min"),
        "open": close - direction * .02, "high": close + .12, "low": close - .12,
        "close": close, "volume": [100.0] * periods})


def test_exchange_context_exposes_auditable_primitives_without_luxalgo_parity_claim():
    context = exchange_market_context(_frame(), 1)
    assert context["source"] == "okx_confirmed_candles"
    assert context["supertrend_direction"] == context["structure_bias"] == 1
    assert context["smc_compatible_only"] is True
    assert context["luxalgo_parity_claimed"] is False
    assert context["premium_discount_zone"] in {"premium", "discount", "equilibrium"}
    assert -1.0 <= context["chaikin_money_flow_20"] <= 1.0
    assert "choch_direction" in context and "equal_high" in context


def test_two_timeframe_three_way_context_conflict_rejects_existing_long_candidate():
    allowed, reason, metrics = indicator_confirmation_gate(
        _frame(-1), _frame(-1), _frame(1), 1, entry_class="reversal", minimum_volume_ratio=.1)
    assert not allowed
    assert "多维强冲突" in reason
    assert metrics["1m"]["market_context"]["supertrend_direction"] == -1
