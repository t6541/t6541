import pandas as pd

from quantbot.waterfall_hold import waterfall_hold_signal, waterfall_trailing_prices


def _frame(closes, *, last_open=None, last_volume=100.0):
    rows = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close + .2
        rows.append({
            "date": pd.Timestamp("2026-08-17") + pd.Timedelta(minutes=index),
            "open": open_price, "high": max(open_price, close) + .15,
            "low": min(open_price, close) - .15, "close": close,
            "volume": 10.0,
        })
    if last_open is not None:
        rows[-1]["open"] = last_open
        rows[-1]["high"] = max(last_open, rows[-1]["close"]) + .15
        rows[-1]["low"] = min(last_open, rows[-1]["close"]) - .15
    rows[-1]["volume"] = last_volume
    return pd.DataFrame(rows)


def test_volume_backed_multi_timeframe_waterfall_activates_short_hold():
    one = _frame([104 - index * .16 for index in range(30)])
    five = _frame([112 - index * .25 for index in range(29)] + [101.0], last_open=104.0, last_volume=30)
    fifteen = _frame([120 - index * .3 for index in range(30)])
    result = waterfall_hold_signal(one, five, fifteen, -1)
    assert result.active
    assert result.volume_ratio >= 1.5
    assert "瀑布持仓生效" in result.reason


def test_normal_low_volume_decline_does_not_activate_waterfall_hold():
    one = _frame([104 - index * .10 for index in range(30)])
    five = _frame([110 - index * .15 for index in range(30)], last_volume=10)
    fifteen = _frame([120 - index * .2 for index in range(30)])
    assert not waterfall_hold_signal(one, five, fifteen, -1).active


def test_waterfall_trailing_is_never_tighter_than_existing_protection():
    activation, callback = waterfall_trailing_prices(1880, -1, 1884, 1878, .5, 3)
    assert activation <= 1876
    assert callback >= 1.95
