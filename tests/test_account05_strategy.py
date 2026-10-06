from decimal import Decimal

import pytest

from quantbot.account05_strategy import (
    Account05Config,
    ContractSpec,
    PositionSide,
    Trend15m,
    addon_entry_plan,
    recovery_slot_entry_plan,
    extreme_rotation_entry_plan,
    addon_permission,
    addon_price_zone_allows,
    initial_entry_plans,
    replacement_margin_pct,
    take_profit_price,
    take_profit_price_by_points,
    addon_profit_points,
    addon_take_profit_threshold,
    addon_take_profit_unlocked,
)


SPEC = ContractSpec(
    ct_val=Decimal("0.1"), lot_size=Decimal("0.01"), min_size=Decimal("0.01"))


@pytest.mark.parametrize(
    ("trend", "long_notional", "short_notional"),
    [
        (Trend15m.UP, Decimal("120"), Decimal("60")),
        (Trend15m.DOWN, Decimal("60"), Decimal("120")),
        (Trend15m.UNCLEAR, Decimal("60"), Decimal("60")),
    ],
)
def test_initial_hedge_ratios_follow_fifteen_minute_trend(
        trend, long_notional, short_notional):
    plans = initial_entry_plans(Account05Config(), trend, Decimal("2500"), SPEC)
    assert plans[0].side is PositionSide.LONG
    assert plans[0].notional_usdt == long_notional
    assert plans[1].side is PositionSide.SHORT
    assert plans[1].notional_usdt == short_notional


def test_contract_conversion_uses_exchange_metadata_and_rounds_down():
    long_plan, short_plan = initial_entry_plans(
        Account05Config(), Trend15m.UP, Decimal("2738"), SPEC)
    assert long_plan.contracts == Decimal("0.43")
    assert short_plan.contracts == Decimal("0.21")
    assert long_plan.contracts * SPEC.ct_val * Decimal("2738") <= Decimal("120")


def test_operating_capital_is_configurable():
    config = Account05Config(operating_capital_usdt=Decimal("307.67"))
    long_plan, _ = initial_entry_plans(config, Trend15m.UP, Decimal("2738"), SPEC)
    assert long_plan.margin_usdt == Decimal("3.69204")
    assert long_plan.notional_usdt == Decimal("369.20400")


def test_replacement_order_uses_60_when_unclear_and_120_only_with_trend():
    assert replacement_margin_pct(Trend15m.UNCLEAR, PositionSide.LONG) == Decimal("0.006")
    assert replacement_margin_pct(Trend15m.UP, PositionSide.LONG) == Decimal("0.012")
    assert replacement_margin_pct(Trend15m.UP, PositionSide.SHORT) == Decimal("0.006")
    assert replacement_margin_pct(Trend15m.DOWN, PositionSide.SHORT) == Decimal("0.012")


def test_replacement_balances_against_actual_opposite_total():
    opposite = Decimal("0.012")
    assert replacement_margin_pct(
        Trend15m.UNCLEAR, PositionSide.LONG, opposite) == Decimal("0.012")
    assert replacement_margin_pct(
        Trend15m.UP, PositionSide.LONG, opposite) == Decimal("0.012")
    assert replacement_margin_pct(
        Trend15m.UP, PositionSide.SHORT, Decimal("0.018")) == Decimal("0.012")
    assert replacement_margin_pct(
        Trend15m.UP, PositionSide.LONG, Decimal("0.029")) == Decimal("0.012")


def test_rebuilt_base_never_exceeds_twelve_tenths_percent_even_with_trend():
    assert replacement_margin_pct(
        Trend15m.DOWN, PositionSide.SHORT, Decimal("0.018")) == Decimal("0.012")
    assert replacement_margin_pct(
        Trend15m.UP, PositionSide.LONG, Decimal("0.018")) == Decimal("0.012")


def test_addon_waits_for_opposite_average_zone_or_more_favourable_price():
    assert not addon_price_zone_allows(
        PositionSide.SHORT, Decimal("2758"), Decimal("2774"))
    assert addon_price_zone_allows(
        PositionSide.SHORT, Decimal("2772"), Decimal("2774"))
    assert addon_price_zone_allows(
        PositionSide.SHORT, Decimal("2780"), Decimal("2774"))
    assert not addon_price_zone_allows(
        PositionSide.LONG, Decimal("2760"), Decimal("2742"))
    assert addon_price_zone_allows(
        PositionSide.LONG, Decimal("2744"), Decimal("2742"))


def test_base_and_addon_take_profit_prices_are_side_correct_and_tick_aligned():
    assert take_profit_price(
        Decimal("2500"), PositionSide.LONG, Decimal("0.005"), Decimal("0.01")) == Decimal("2512.50")
    assert take_profit_price(
        Decimal("2500"), PositionSide.SHORT, Decimal("0.007"), Decimal("0.01")) == Decimal("2482.50")
    assert take_profit_price(
        Decimal("2738.61"), PositionSide.LONG, Decimal("0.0062"), Decimal("0.01")) == Decimal("2755.59")


def test_each_addon_uses_fixed_point_zero_one_contracts():
    plan = addon_entry_plan(
        Account05Config(), PositionSide.LONG, Decimal("2500"), SPEC)
    assert plan.contracts == Decimal("0.01")
    assert plan.notional_usdt == Decimal("2.500")
    assert plan.margin_usdt == Decimal("0.025")
    assert plan.margin_pct == Decimal("0.00025")


def test_fixed_addon_contracts_do_not_change_with_mark_price():
    low = addon_entry_plan(
        Account05Config(), PositionSide.LONG, Decimal("1500"), SPEC)
    high = addon_entry_plan(
        Account05Config(), PositionSide.SHORT, Decimal("4500"), SPEC)
    assert low.contracts == Decimal("0.01")
    assert high.contracts == Decimal("0.01")
    assert low.notional_usdt == Decimal("1.500")
    assert high.notional_usdt == Decimal("4.500")


def test_recovery_slot_budget_is_separate_from_fixed_small_order():
    config = Account05Config(operating_capital_usdt=Decimal("200"))
    recovery = recovery_slot_entry_plan(config, PositionSide.LONG, Decimal("2500"), SPEC)
    fixed = addon_entry_plan(config, PositionSide.LONG, Decimal("2500"), SPEC)
    assert recovery.contracts == Decimal("0.02")
    assert recovery.margin_pct == Decimal("0.00025")
    assert fixed.contracts == Decimal("0.01")


def test_extreme_rotation_uses_point_one_percent_margin():
    plan = extreme_rotation_entry_plan(
        Account05Config(), PositionSide.SHORT, Decimal("2680"), SPEC)
    assert plan.margin_usdt == Decimal("0.100")
    assert plan.notional_usdt == Decimal("10.000")
    assert plan.contracts == Decimal("0.03")


def test_fixed_addon_rejects_exchange_minimum_above_point_zero_one():
    spec = ContractSpec(
        ct_val=Decimal("0.1"), lot_size=Decimal("0.01"),
        min_size=Decimal("0.1"))
    with pytest.raises(ValueError, match="below minimum"):
        addon_entry_plan(
            Account05Config(), PositionSide.LONG, Decimal("2500"), spec)


def test_fixed_addon_rejects_size_not_aligned_to_exchange_lot():
    spec = ContractSpec(
        ct_val=Decimal("0.1"), lot_size=Decimal("0.03"),
        min_size=Decimal("0.01"))
    with pytest.raises(ValueError, match="not aligned"):
        addon_entry_plan(
            Account05Config(), PositionSide.LONG, Decimal("2500"), spec)


def test_addon_take_profit_uses_configurable_absolute_points():
    assert take_profit_price_by_points(
        Decimal("2738.61"), PositionSide.LONG, Decimal("10"), Decimal("0.01")) == Decimal("2748.61")
    assert take_profit_price_by_points(
        Decimal("2738.61"), PositionSide.SHORT, Decimal("10"), Decimal("0.01")) == Decimal("2728.61")
    assert take_profit_price_by_points(
        Decimal("2738.61"), PositionSide.LONG, Decimal("20"), Decimal("0.01")) == Decimal("2758.61")


def test_addon_profit_unlock_is_strictly_above_ten_price_points():
    assert addon_profit_points(
        Decimal("100"), Decimal("110"), PositionSide.LONG) == Decimal("10")
    assert not addon_take_profit_unlocked(
        Decimal("100"), Decimal("110"), PositionSide.LONG, Decimal("4"))
    assert addon_take_profit_unlocked(
        Decimal("100"), Decimal("110.01"), PositionSide.LONG, Decimal("4"))
    assert addon_take_profit_unlocked(
        Decimal("110.01"), Decimal("100"), PositionSide.SHORT, Decimal("4"))
    assert addon_take_profit_threshold(Decimal("20")) == Decimal("20")
    assert not addon_take_profit_unlocked(
        Decimal("100"), Decimal("110.01"), PositionSide.LONG, Decimal("20"))


def test_addon_limits_count_side_margin_and_combined_floating_loss():
    config = Account05Config()
    assert addon_permission(
        config, addon_count=17, side_margin_used_usdt=Decimal("1.7"),
        combined_unrealized_pnl_usdt=Decimal("-19.99")).allowed
    assert addon_permission(
        config, addon_count=36, side_margin_used_usdt=Decimal("1.7"),
        combined_unrealized_pnl_usdt=Decimal("0")).reason_code == "side_addon_count_limit_reached"
    assert addon_permission(
        config, addon_count=0, side_margin_used_usdt=Decimal("3.56"),
        combined_unrealized_pnl_usdt=Decimal("0")).reason_code == "side_margin_limit_reached"
    assert addon_permission(
        config, addon_count=0, side_margin_used_usdt=Decimal("1"),
        combined_unrealized_pnl_usdt=Decimal("-20")).reason_code == "combined_floating_loss_limit_reached"
