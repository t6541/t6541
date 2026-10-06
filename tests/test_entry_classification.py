from quantbot.entry_classification import (
    CURRENT_TREND_CONTINUATION,
    HIGHER_TREND_CONTINUATION,
    LOCAL_ENDPOINT,
    STAGE3_RECOVERY,
    TRUE_ENDPOINT,
    classify_entry_category,
    classify_review_shape,
    entry_category_label,
)


def test_five_short_categories_have_exact_distinct_operator_labels():
    assert entry_category_label(-1, TRUE_ENDPOINT) == "真正高位扫顶反转做空"
    assert entry_category_label(-1, LOCAL_ENDPOINT) == "局部高点扫顶反转做空"
    assert entry_category_label(-1, STAGE3_RECOVERY) == "反转三阶段补漏追空"
    assert entry_category_label(-1, CURRENT_TREND_CONTINUATION) == "5分钟周期下跌趋势中的反抽高点追空"
    assert entry_category_label(-1, HIGHER_TREND_CONTINUATION) == "上一级15分钟下跌趋势中的反抽追空（5分钟真正高位）"


def test_long_categories_are_the_exact_mirror_of_short_categories():
    assert entry_category_label(1, TRUE_ENDPOINT) == "真正低位扫底反转做多"
    assert entry_category_label(1, LOCAL_ENDPOINT) == "局部低点扫底反转做多"
    assert entry_category_label(1, STAGE3_RECOVERY) == "反转三阶段补漏追多"
    assert entry_category_label(1, CURRENT_TREND_CONTINUATION) == "5分钟周期上涨趋势中的回踩低点追多"
    assert entry_category_label(1, HIGHER_TREND_CONTINUATION) == "上一级15分钟上涨趋势中的回踩追多（5分钟真正低位）"


def test_stage3_identity_precedes_endpoint_and_higher_trend_labels():
    result = classify_entry_category(
        -1, stage3_recovery=True, true_endpoint_reversal=True,
        trend_continuation=True, higher_timeframe_trend=True)
    assert result["category"] == STAGE3_RECOVERY
    assert result["label"] == "反转三阶段补漏追空"
    assert "MA5/MA10交叉补漏" in result["rule"]


def test_persisted_parent_trend_distinguishes_higher_from_current_continuation():
    current = classify_entry_category(-1, trend_continuation=True)
    parent = classify_entry_category(
        -1, trend_continuation=True, higher_timeframe_trend=True)
    assert current["category"] == CURRENT_TREND_CONTINUATION
    assert parent["category"] == HIGHER_TREND_CONTINUATION
    assert current["label"] != parent["label"]
    assert current["trend_source_timeframe"] == "5m"
    assert parent["trend_source_timeframe"] == "15m"
    assert "5分钟趋势已成立" in current["rule"]
    assert "上一级15分钟" in parent["rule"]
    assert "不等待下穿/上穿MA5" in parent["rule"]


def test_five_minute_bottom_half_cover_is_local_reversal_not_pullback():
    result = classify_entry_category(1, five_minute_local_reversal=True)
    assert result["category"] == LOCAL_ENDPOINT
    assert result["label"] == "上一级5分钟底部局部反转做多"
    assert result["trend_source_timeframe"] == "5m"
    assert "不要求一分钟先穿过MA5" in result["rule"]
    assert "回踩做多" not in result["label"]


def test_sideways_noise_is_visible_but_never_claimed_as_one_of_five_entries():
    result = classify_review_shape(-1, "sideways_neutral")
    assert result["category"] == "non_entry_observation"
    assert result["label"] == "横盘震荡偏空候选（尚未满足下单）"
    assert "中性" not in result["label"]
    assert "偏多或偏空判断" in result["rule"]
