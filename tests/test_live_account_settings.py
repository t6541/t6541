from dataclasses import replace

import pytest

from quantbot.config import (AppConfig, BacktestConfig, DataConfig, FactorConfig,
                             OkxConfig, OutputConfig, RiskConfig, StrategyConfig)
from quantbot.live_account_settings import (LiveAccountSettings, apply_account_strategy,
                                             normalize_order_contracts)


def _config():
    return AppConfig(DataConfig(), FactorConfig(), StrategyConfig(), BacktestConfig(),
                     RiskConfig(), OkxConfig(), OutputConfig())


@pytest.mark.parametrize("raw, expected", [("0.01", "0.01"), ("0.05", "0.05"), ("0.15", "0.15")])
def test_order_contracts_are_normalized_and_persisted_per_account(tmp_path, raw, expected):
    store = LiveAccountSettings(tmp_path / raw / "state.sqlite3")
    assert store.set_order_contracts(raw) == expected
    assert LiveAccountSettings(store.path).order_contracts() == expected


@pytest.mark.parametrize("raw", ["", "abc", "0", "0.001", "1.01"])
def test_order_contracts_reject_invalid_or_non_lot_values(raw):
    with pytest.raises(ValueError):
        normalize_order_contracts(raw)


def test_account_settings_are_isolated(tmp_path):
    one = LiveAccountSettings(tmp_path / "account01.sqlite3")
    two = LiveAccountSettings(tmp_path / "account02.sqlite3")
    one.set_order_contracts("0.15")
    assert two.order_contracts() == "0.05"


def test_account05_capital_and_point_target_are_persistent_and_isolated(tmp_path):
    five = LiveAccountSettings(tmp_path / "account05" / "state.sqlite3")
    four = LiveAccountSettings(tmp_path / "account04" / "state.sqlite3")
    assert five.set_operating_capital_usdt("100") == "100.00"
    assert five.set_addon_take_profit_points("20") == "20.00"
    assert LiveAccountSettings(five.path).operating_capital_usdt() == "100.00"
    assert LiveAccountSettings(five.path).addon_take_profit_points() == "20.00"
    assert four.addon_take_profit_points() == "10.00"
    with pytest.raises(ValueError, match="小单止盈点数"):
        five.set_addon_take_profit_points("9.99")
    five._set("addon_take_profit_points", "4.00")
    assert five.addon_take_profit_points() == "10.00"


@pytest.mark.parametrize("capital", ["", "0", "-1", "nan", "1000001"])
def test_account05_rejects_invalid_operating_capital(tmp_path, capital):
    with pytest.raises(ValueError):
        LiveAccountSettings(tmp_path / "state.sqlite3").set_operating_capital_usdt(capital)


def test_shared_strategy_and_account_override(tmp_path):
    settings = LiveAccountSettings(tmp_path / "state.sqlite3")
    base = _config()
    shared, source = apply_account_strategy(base, settings)
    assert shared == base and source == "共享策略"
    settings.set_strategy_overrides({"strategy": {"minimum_reward_risk": 2.0},
                                     "risk": {"min_cost_edge_multiple": 3.0}})
    settings.set_strategy_source("account")
    account, source = apply_account_strategy(base, settings)
    assert source == "单账户策略覆盖"
    assert account.strategy.minimum_reward_risk == 2.0
    assert account.risk.min_cost_edge_multiple == 3.0
    assert base.strategy.minimum_reward_risk != account.strategy.minimum_reward_risk
