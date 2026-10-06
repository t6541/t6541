import pandas as pd
import pytest

from quantbot.backtest import run_backtest
from quantbot.data import validate_market_data
from quantbot.risk import constrain_weights


def _market():
    dates = pd.bdate_range("2024-01-01", periods=4)
    close = [100, 110, 121, 133.1]
    return validate_market_data(pd.DataFrame({"date": dates, "symbol": "A", "open": close, "high": close, "low": close, "close": close, "volume": 1000}))


def test_signal_is_applied_one_period_later_and_costed():
    market = _market()
    targets = pd.DataFrame({"A": [1, 1, 1, 1]}, index=market.date)
    result = run_backtest(market, targets, 1000, fee_bps=10, slippage_bps=0, annualization=252, max_drawdown=.9)
    assert result.daily.iloc[0].net_return == 0
    assert result.daily.iloc[1].net_return == pytest.approx(.099)
    assert result.metrics["total_cost"] == pytest.approx(.001)


def test_risk_caps_position_and_gross_exposure():
    weights = pd.DataFrame({"A": [.8], "B": [.8]})
    constrained = constrain_weights(weights, max_position=.4, max_gross=.6, max_industry=.5)
    assert constrained.abs().sum(axis=1).iloc[0] == pytest.approx(.6)
    assert constrained.abs().max().max() <= .4


def test_fee_rebate_reduces_fee_but_not_slippage():
    market = _market()
    targets = pd.DataFrame({"A": [1, 1, 1, 1]}, index=market.date)
    result = run_backtest(market, targets, 1000, fee_bps=10, slippage_bps=5, annualization=252, max_drawdown=.9, fee_rebate_rate=.4)
    assert result.metrics["gross_fee"] == pytest.approx(.001)
    assert result.metrics["fee_rebate"] == pytest.approx(.0004)
    assert result.metrics["net_fee"] == pytest.approx(.0006)
    assert result.metrics["total_slippage"] == pytest.approx(.0005)
    assert result.metrics["total_cost"] == pytest.approx(.0011)
