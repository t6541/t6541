import pytest

from quantbot.ma20_execution import early_low_sweep_risk_plan, ma20_entry_zone, ma20_exit_prices


def test_ma20_long_exit_prices_are_ordered():
    stop, activation, callback = ma20_exit_prices(2000, 1)
    assert float(stop) < 2000 < float(activation)
    assert float(callback) == pytest.approx(2.0)


def test_ma20_short_exit_prices_are_ordered():
    stop, activation, callback = ma20_exit_prices(2000, -1)
    assert float(activation) < 2000 < float(stop)
    assert float(callback) == pytest.approx(2.0)


def test_short_waits_for_upper_35_percent_of_retest_range():
    low, high = ma20_entry_zone(1887.68, 1891.44, -1)
    assert low == pytest.approx(1890.124)
    assert high > 1891.44


def test_long_waits_for_lower_35_percent_of_retest_range():
    low, high = ma20_entry_zone(1887.68, 1891.44, 1)
    assert low < 1887.68
    assert high == pytest.approx(1888.996)


def test_structure_stop_is_beyond_retest_high_for_short():
    stop, activation, callback = ma20_exit_prices(1890.50, -1, 1887.68, 1891.44)
    assert float(stop) > 1891.44
    assert float(activation) < 1890.50
    assert float(callback) == pytest.approx(1.90)


def test_early_low_sweep_uses_original_structure_and_activates_at_1_2r():
    plan = early_low_sweep_risk_plan(100.0, 99.9, 98.9, 98.8, 1.0, 2.5)
    assert plan.allowed
    assert plan.stop < 98.8
    assert plan.activation == pytest.approx(100 + plan.risk * 1.2)


def test_early_low_sweep_rejects_late_or_excessively_wide_entry():
    late = early_low_sweep_risk_plan(101.0, 100.0, 99.0, 98.8, 1.0, 3.0)
    wide = early_low_sweep_risk_plan(100.0, 99.9, 98.8, 95.0, 1.0, 3.0)
    assert not late.allowed
    assert not wide.allowed
