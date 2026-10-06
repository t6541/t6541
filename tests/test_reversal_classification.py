import pandas as pd

from quantbot.reversal_classification import (classify_reversal_context,
                                               five_minute_weakening_cover,
                                               two_timeframe_sideways_zone)


def _market(closes):
    return pd.DataFrame({
        "date": pd.date_range("2026-09-02", periods=len(closes), freq="min", tz="UTC"),
        "open": closes, "high": [x + 1 for x in closes],
        "low": [x - 1 for x in closes], "close": closes, "volume": 1.0,
    })


def test_both_prior_bull_stacks_are_true_top_reversal():
    rising = list(range(100, 130))
    one = _market(rising + [128, 126])
    five = _market(rising + [128, 124])
    result = classify_reversal_context(one, five, -1)
    assert result["market_shape_code"] == "true_top_reversal"
    assert result["market_shape_label"] == "真正顶部反转启动"


def test_bearish_five_minute_context_is_continuation_short():
    falling = list(range(140, 100, -1))
    one = _market(falling + [104, 108, 105])
    five = _market(falling + [103, 105, 102])
    result = classify_reversal_context(one, five, -1)
    assert result["market_shape_code"] == "downtrend_continuation_short"
    assert result["market_shape_label"] == "下跌趋势中继反抽做空"


def test_five_minute_half_cover_is_evidence_not_a_gate():
    rising = list(range(100, 130))
    one = _market(rising + [129, 128])
    five = _market(rising + [129, 128])
    result = classify_reversal_context(one, five, -1)
    assert result["market_shape_code"] == "true_top_reversal"
    assert result["five_minute_half_cover"] is False


def test_two_five_minute_bodies_can_cumulatively_confirm_approximate_half_cover():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-09-08", periods=4, freq="5min", tz="UTC"),
        "open": [99.5, 100.0, 104.0, 103.4],
        "high": [100.1, 104.2, 104.1, 103.5],
        "low": [99.4, 99.9, 103.2, 102.0],
        "close": [100.0, 104.0, 103.3, 102.1],
        "volume": [1.0] * 4,
    })
    allowed, anchor_index, ratio = five_minute_weakening_cover(frame, -1)
    assert allowed
    assert anchor_index == 1
    assert ratio >= .45


def test_two_timeframe_compression_is_classified_as_neutral_sideways_first():
    oscillation = [100.0, 100.4, 99.8, 100.3, 99.9] * 8
    one = _market(oscillation)
    five = _market(oscillation)
    sideways, metrics = two_timeframe_sideways_zone(one, five)
    assert sideways, metrics

    result = classify_reversal_context(one, five, 1)
    assert result["market_shape_code"] == "sideways_neutral"
    assert result["market_shape_label"] == "横盘震荡区（中性）"


def test_post_selloff_compression_is_downtrend_continuation_range():
    selloff_then_range = (
        [130.0 - index * 1.2 for index in range(22)]
        + [104.8, 105.4, 104.5, 105.2, 104.6] * 6
    )
    one = _market([104.7, 105.1, 104.6, 105.0, 104.5] * 8)
    five = _market(selloff_then_range)
    result = classify_reversal_context(one, five, -1)
    assert result["market_shape_code"] == "downtrend_continuation_range"
    assert result["market_shape_label"] == "下跌中续横盘区（反抽追空）"
