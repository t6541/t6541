import pandas as pd

from quantbot.data import synthetic_market, validate_market_data
from quantbot.factors import compute_factors
from quantbot.strategy import dual_ma_trend_weights


def test_data_is_deduplicated_and_sorted():
    frame = synthetic_market("2024-01-01", "2024-01-10", ("A",), 1)
    duplicated = pd.concat([frame.iloc[::-1], frame.iloc[[0]]])
    cleaned = validate_market_data(duplicated)
    assert not cleaned.duplicated(["date", "symbol"]).any()
    assert cleaned["date"].is_monotonic_increasing


def test_factor_has_no_future_dependency():
    market = synthetic_market("2023-01-01", "2024-06-01", ("A", "B", "C"), 7)
    cutoff = pd.Timestamp("2024-01-31")
    before = compute_factors(market[market.date <= cutoff], 20, 20, 60)
    after = compute_factors(market, 20, 20, 60)
    pd.testing.assert_frame_equal(before, after.loc[after.index.get_level_values("date") <= cutoff])


def test_dual_ma_signal_has_no_future_dependency():
    market = synthetic_market("2022-01-01", "2024-06-01", ("ETH-USDT-SWAP",), 11)
    cutoff = pd.Timestamp("2024-01-31")
    kwargs = dict(
        fast_window=10,
        slow_window=30,
        volatility_window=10,
        max_annualized_volatility=10.0,
        stop_loss_pct=0.05,
        long_weight=0.3,
        short_weight=0.3,
        annualization=252,
    )
    before = dual_ma_trend_weights(market[market.date <= cutoff], **kwargs)
    after = dual_ma_trend_weights(market, **kwargs)
    pd.testing.assert_frame_equal(before, after.loc[after.index <= cutoff])


def test_dual_ma_strategy_supports_long_and_short():
    dates = pd.bdate_range("2023-01-01", periods=180)
    prices = list(range(100, 190)) + list(range(190, 100, -1))
    market = pd.DataFrame(
        {
            "date": dates,
            "symbol": "ETH-USDT-SWAP",
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": 1000,
        }
    )
    weights = dual_ma_trend_weights(
        market,
        fast_window=5,
        slow_window=20,
        volatility_window=5,
        max_annualized_volatility=10.0,
        stop_loss_pct=0.20,
        long_weight=0.3,
        short_weight=0.3,
        annualization=252,
    )
    assert (weights > 0).any().any()
    assert (weights < 0).any().any()
    assert weights.abs().max().max() <= 0.3
