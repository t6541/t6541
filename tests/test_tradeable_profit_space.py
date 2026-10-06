import pytest

from quantbot.entry_risk import adaptive_profit_requirements, tradeable_profit_space


def test_real_cost_gate_requires_gross_and_net_room():
    allowed, reason, required = tradeable_profit_space(
        100.0, 1, 98.5, 103.0, 1.0, minimum_r=1.0,
        minimum_atr=0.0, minimum_price_pct=0.0,
        minimum_gross_points=3.5, estimated_cost_points=1.0,
        minimum_net_points=1.0,
    )
    assert not allowed and required == 3.5
    allowed, reason, required = tradeable_profit_space(
        100.0, 1, 98.5, 104.0, 1.0, minimum_r=1.0,
        minimum_atr=0.0, minimum_price_pct=0.0,
        minimum_gross_points=3.5, estimated_cost_points=1.0,
        minimum_net_points=1.0,
    )
    assert allowed and "净空间3.00点" in reason


def test_rejects_tiny_absolute_profit_even_when_r_multiple_is_enough():
    allowed, reason, required = tradeable_profit_space(
        1873.0, 1, 1872.9, 1873.2, 0.20,
    )
    assert not allowed
    assert required == 1873.0 * 0.0012
    assert "利润空间" in reason


def test_accepts_target_after_all_three_shared_floors():
    allowed, reason, required = tradeable_profit_space(
        1873.0, -1, 1874.0, 1870.5, 0.50,
    )
    assert allowed
    assert required == 2.2476
    assert "达到" in reason


def test_rejects_wrong_direction_target():
    allowed, reason, _ = tradeable_profit_space(
        100.0, -1, 101.0, 100.5, 1.0,
    )
    assert not allowed
    assert "方向错误" in reason


def test_fast_structure_profit_floor_combines_r_atr_and_cost_without_fixed_three_points():
    minimum_r, minimum_atr, cost_floor = adaptive_profit_requirements(
        risk=1.0, atr_1m=1.0, estimated_cost_points=1.2,
        fast_structure=True,
    )
    assert (minimum_r, minimum_atr) == (1.2, 0.6)
    assert cost_floor == pytest.approx(1.8)
    allowed, _, required = tradeable_profit_space(
        100.0, 1, 99.0, 102.0, 1.0,
        minimum_r=minimum_r, minimum_atr=minimum_atr,
        minimum_price_pct=0.0, minimum_gross_points=cost_floor,
        estimated_cost_points=1.2,
    )
    assert allowed and required == pytest.approx(1.8)


def test_adaptive_profit_floor_rejects_room_below_cost_buffer():
    minimum_r, minimum_atr, cost_floor = adaptive_profit_requirements(
        risk=.5, atr_1m=.5, estimated_cost_points=1.2,
        fast_structure=True,
    )
    allowed, _, required = tradeable_profit_space(
        100.0, -1, 100.5, 98.5, .5,
        minimum_r=minimum_r, minimum_atr=minimum_atr,
        minimum_price_pct=0.0, minimum_gross_points=cost_floor,
        estimated_cost_points=1.2,
    )
    assert not allowed and required == pytest.approx(1.8)


def test_quote_tolerance_accepts_two_cent_boundary_noise_only():
    allowed, reason, required = tradeable_profit_space(
        100.0, 1, 98.0, 104.0, 1.0,
        minimum_r=2.01, minimum_atr=0.0, minimum_price_pct=0.0,
        quote_tolerance_points=0.02,
    )
    assert allowed and required == pytest.approx(4.02)
    assert "0.02点报价/成本容差" in reason
    rejected, _, _ = tradeable_profit_space(
        100.0, 1, 98.0, 103.99, 1.0,
        minimum_r=2.01, minimum_atr=0.0, minimum_price_pct=0.0,
        quote_tolerance_points=0.02,
    )
    assert not rejected


def test_user_two_point_floor_accepts_exactly_two_and_rejects_less():
    accepted, _, required = tradeable_profit_space(
        100.0, 1, 99.5, 102.0, 1.0,
        minimum_r=0.0, minimum_atr=0.0, minimum_price_pct=0.0,
        minimum_gross_points=2.0, estimated_cost_points=0.0,
        quote_tolerance_points=0.0,
    )
    assert accepted and required == 2.0
    rejected, _, _ = tradeable_profit_space(
        100.0, 1, 99.5, 101.99, 1.0,
        minimum_r=0.0, minimum_atr=0.0, minimum_price_pct=0.0,
        minimum_gross_points=2.0, estimated_cost_points=0.0,
        quote_tolerance_points=0.0,
    )
    assert not rejected
