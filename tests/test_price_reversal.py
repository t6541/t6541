import pandas as pd

from quantbot.price_reversal import (latest_price_reversal, recent_ma_fan_endpoint,
                                     recent_price_reversal_confirms)


def _frame(opens, closes, *, freq="1min"):
    return pd.DataFrame({
        "date": pd.date_range("2026-09-06", periods=len(opens), freq=freq, tz="UTC"),
        "open": opens,
        "high": [max(o, c) + .2 for o, c in zip(opens, closes)],
        "low": [min(o, c) - .2 for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [100.0] * len(opens),
    })


def test_raw_half_cover_at_local_high_detects_early_short_without_average_candle():
    opens = [100.0] * 6 + [100.0, 101.0]
    closes = [100.0] * 6 + [101.0, 100.4]
    frame = _frame(opens, closes)
    frame.loc[frame.index[-2], "high"] = 101.3
    frame.loc[frame.index[-1], "high"] = 101.4

    reversal = latest_price_reversal(frame)

    assert reversal is not None
    assert reversal["direction"] == -1
    assert "普通K线" in reversal["trigger"]


def test_raw_sweep_reclaim_detects_bottom_directly_from_exchange_ohlc():
    frame = _frame(
        [102, 101, 100, 99, 98, 97, 96, 95],
        [101, 100, 99, 98, 97, 96, 95, 96],
    )
    frame.loc[frame.index[-1], ["low", "high", "close"]] = [93.0, 96.4, 96.0]

    reversal = latest_price_reversal(frame)

    assert reversal is not None
    assert reversal["direction"] == 1
    assert reversal["extreme"] == 93.0
    assert reversal["trigger"] == "普通K线扫低收回"


def test_recent_five_minute_confirmation_uses_raw_colour_turn_and_ma5():
    frame = _frame(
        [10, 9, 8, 7, 6, 5, 5, 6],
        [9, 8, 7, 6, 5, 5, 8, 11],
        freq="5min",
    )
    assert recent_price_reversal_confirms(frame, 1)
    assert not recent_price_reversal_confirms(frame, -1)


def test_bottom_endpoint_requires_close_below_ma5_and_ma20_not_just_a_local_low():
    closes = [100.0] * 20 + [101, 102, 103, 104, 105, 104, 103, 102]
    frame = _frame(closes, closes)
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [102.0, 102.2, 100.8, 101.2]

    assert recent_ma_fan_endpoint(frame, 1) is None


def test_bottom_endpoint_allows_ma10_cross_when_ma5_ma20_outer_rule_holds():
    closes = ([100.0] * 20
              + [98.15, 96.33, 95.81, 96.27, 96.96, 95.25,
                 95.14, 96.31, 97.08, 96.85, 95.37, 93.86])
    frame = _frame(closes, closes)

    endpoint = recent_ma_fan_endpoint(frame, 1)

    assert endpoint is not None
    assert endpoint["ma5"] > endpoint["ma10"]  # MA10交叉不否定底部
    assert closes[-1] < endpoint["ma5"] < endpoint["ma20"]


def test_top_endpoint_is_exact_mirror_and_allows_ma10_cross():
    closes = ([100.0] * 20
              + [98.80, 97.34, 95.67, 97.31, 98.79, 97.33,
                 98.10, 97.88, 99.86, 100.21, 101.16, 103.02])
    frame = _frame(closes, closes)

    endpoint = recent_ma_fan_endpoint(frame, -1)

    assert endpoint is not None
    assert endpoint["ma10"] < endpoint["ma20"]  # MA10交叉不否定顶部
    assert closes[-1] > endpoint["ma5"] > endpoint["ma20"]
