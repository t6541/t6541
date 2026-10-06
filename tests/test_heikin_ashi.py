import pandas as pd

from quantbot.heikin_ashi import heikin_ashi_frame, latest_heikin_reversal


def test_heikin_ashi_uses_average_close_and_recursive_open():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3, freq="min", tz="UTC"),
        "open": [10, 12, 11], "high": [13, 13, 14], "low": [9, 10, 10],
        "close": [12, 11, 13], "volume": [1, 2, 3],
    })
    result = heikin_ashi_frame(frame)
    assert result.iloc[0].close == 11
    assert result.iloc[0].open == 11
    assert result.iloc[1].open == 11
    assert list(result.volume) == [1, 2, 3]


def test_latest_heikin_reversal_reports_raw_extreme():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5, freq="min", tz="UTC"),
        "open": [12, 11, 10, 9, 8], "high": [13, 12, 11, 10, 12],
        "low": [10, 9, 8, 7, 7.5], "close": [11, 10, 9, 8, 11],
        "volume": [1] * 5,
    })
    reversal = latest_heikin_reversal(frame)
    assert reversal is not None
    assert reversal["direction"] == 1
    assert reversal["extreme"] == 7
