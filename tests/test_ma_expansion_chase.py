import pandas as pd

from quantbot.ma_expansion_chase import dual_timeframe_ma_expansion_chase


def market(direction=1, periods=50, freq="5min"):
    values = []
    for i in range(periods):
        base = 100 + direction * (i * .12 + max(0, i - 28) ** 1.35 * .05)
        values.append({"date": pd.Timestamp("2026-01-01") + pd.Timedelta(freq) * i,
                       "open": base - direction * .04, "high": base + 1.2,
                       "low": base - 1.2, "close": base, "volume": 100 + i})
    return pd.DataFrame(values)


def test_bullish_dual_timeframe_expansion_chases_without_pullback():
    five, one = market(1, freq="5min"), market(1, freq="1min")
    direction, reason, stop = dual_timeframe_ma_expansion_chase(five, one)
    assert direction == 1
    assert "不等待回踩" in reason
    assert stop < one.iloc[-1]["close"]


def test_bearish_dual_timeframe_expansion_chases_without_retest():
    five, one = market(-1, freq="5min"), market(-1, freq="1min")
    direction, reason, stop = dual_timeframe_ma_expansion_chase(five, one)
    assert direction == -1
    assert "不等待反抽" in reason
    assert stop > one.iloc[-1]["close"]


def test_mismatched_timeframes_do_not_chase():
    direction, _, _ = dual_timeframe_ma_expansion_chase(
        market(1, freq="5min"), market(-1, freq="1min"))
    assert direction == 0
