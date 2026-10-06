import pandas as pd

from quantbot.reversal_conditions import reversal_three_stage_condition_audit


def frame(prices, start="2026-09-15T00:00:00Z"):
    return pd.DataFrame({
        "date": pd.date_range(start=start, periods=len(prices), freq="min", tz="UTC"),
        "open": [p + .2 for p in prices], "high": [p + .5 for p in prices],
        "low": [p - .5 for p in prices], "close": prices, "volume": [100] * len(prices),
    })


def test_original_three_stage_audit_has_all_seven_named_conditions():
    one = frame([110 - i * .3 for i in range(27)] + [101.0, 100.5, 101.2])
    five = frame([120 - i for i in range(28)] + [92.0, 92.6])
    five.loc[28, ["open", "close"]] = [94.0, 92.0]
    five.loc[29, ["open", "close"]] = [92.0, 93.0]
    audit = reversal_three_stage_condition_audit(one, five, 1)
    assert audit["total"] == 7
    names = {item["name"] for item in audit["conditions"]}
    assert "1m价格进入MA5内侧" in names
    assert "5m相邻反向实体覆盖至少45%" in names


def test_top_short_uses_mirrored_direction_labels_and_checks():
    prices = [90 + i * .3 for i in range(27)] + [99.0, 99.5, 98.8]
    audit = reversal_three_stage_condition_audit(frame(prices), frame(prices), -1)
    assert audit["direction"] == -1
    assert all(isinstance(item["matched"], bool) for item in audit["conditions"])
