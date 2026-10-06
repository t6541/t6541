from decimal import Decimal

from quantbot.account05_signal_quality import (
    SignalQualityInput, choose_single_identity, quality_gate,
)
from quantbot.account05_strategy import Trend15m


def item(identity="局部底部做多", direction=1, *, five=Trend15m.DOWN,
         fifteen=Trend15m.DOWN, hour=Trend15m.UNCLEAR,
         target="101", stop="99.4", fresh=True):
    return SignalQualityInput(
        identity, direction, five, fifteen, hour, Decimal("100"),
        Decimal(target), Decimal(stop), Decimal("0.20"), fresh, "bar-1")


def test_local_bottom_requires_down_five_minute_background():
    assert quality_gate(item()).allowed
    assert not quality_gate(item(five=Trend15m.UNCLEAR)).allowed


def test_trend_long_requires_five_fifteen_and_hour_alignment():
    candidate = item("上涨趋势回踩追多", 1, five=Trend15m.UP,
                     fifteen=Trend15m.UP, hour=Trend15m.UP)
    assert quality_gate(candidate).allowed
    assert not quality_gate(item(
        "上涨趋势回踩追多", 1, five=Trend15m.UP,
        fifteen=Trend15m.UP, hour=Trend15m.UNCLEAR)).allowed


def test_cost_and_reward_risk_gates_reject_tiny_churn():
    assert not quality_gate(item(target="100.50")).allowed
    assert not quality_gate(item(target="100.80", stop="99.4")).allowed


def test_requires_fresh_closed_one_minute_event():
    assert not quality_gate(item(fresh=False)).allowed


def test_true_reversal_owns_same_bar_over_local_identity():
    local = item()
    true = item("真正底部做多")
    assert choose_single_identity((local, true)) == true
