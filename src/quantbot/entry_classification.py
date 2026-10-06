from __future__ import annotations


TRUE_ENDPOINT = "true_endpoint_reversal"
LOCAL_ENDPOINT = "local_endpoint_reversal"
STAGE3_RECOVERY = "reversal_stage3_recovery"
CURRENT_TREND_CONTINUATION = "current_timeframe_trend_continuation"
HIGHER_TREND_CONTINUATION = "higher_timeframe_trend_continuation"


_LABELS = {
    -1: {
        TRUE_ENDPOINT: "真正高位扫顶反转做空",
        LOCAL_ENDPOINT: "局部高点扫顶反转做空",
        STAGE3_RECOVERY: "反转三阶段补漏追空",
        CURRENT_TREND_CONTINUATION: "5分钟周期下跌趋势中的反抽高点追空",
        HIGHER_TREND_CONTINUATION: "上一级15分钟下跌趋势中的反抽追空（5分钟真正高位）",
    },
    1: {
        TRUE_ENDPOINT: "真正低位扫底反转做多",
        LOCAL_ENDPOINT: "局部低点扫底反转做多",
        STAGE3_RECOVERY: "反转三阶段补漏追多",
        CURRENT_TREND_CONTINUATION: "5分钟周期上涨趋势中的回踩低点追多",
        HIGHER_TREND_CONTINUATION: "上一级15分钟上涨趋势中的回踩追多（5分钟真正低位）",
    },
}

_RULES = {
    TRUE_ENDPOINT: (
        "本周期MA5/MA10/MA20发散末端形成扫高/扫低反转；一分钟先试单，"
        "同方向1m+5m确认后升级为持久趋势核心"),
    LOCAL_ENDPOINT: (
        "本周期局部高点/低点：第一阶段新鲜极值后反向K线可试单；"
        "第二阶段价格进入MA5内侧直接核对；前两阶段未成交时新鲜MA5/MA10交叉补漏"),
    STAGE3_RECOVERY: (
        "第一阶段反转区成立且第二阶段订单漏掉后，仅由本周期MA5/MA10交叉补漏"),
    CURRENT_TREND_CONTINUATION: (
        "5分钟趋势已成立；一分钟末端未能与5分钟配对时，按5分钟方向在MA5外沿追单"),
    HIGHER_TREND_CONTINUATION: (
        "上一级15分钟趋势已成立；此处同时是5分钟真正高位/低位反转点，"
        "首次反抽/回踩转向即可触发，不等待下穿/上穿MA5"),
}


def entry_category_label(direction: int, category: str) -> str:
    """Return the operator-facing label for one of the five mirrored entries."""
    return _LABELS.get(int(direction), {}).get(category, "未分类入场")


def classify_entry_category(
        direction: int, *, stage3_recovery: bool = False,
        true_endpoint_reversal: bool = False,
        trend_continuation: bool = False,
        higher_timeframe_trend: bool = False,
        higher_timeframe_source: str = "15m",
        five_minute_local_reversal: bool = False) -> dict[str, object]:
    """Assign one durable, mutually exclusive identity to an entry.

    Stage 3 describes *how* a missed reversal was recovered, so it owns the
    identity even when its original endpoint was a true high/low.  A trend
    continuation is higher-timeframe only when a persisted parent endpoint
    already owns the same direction; otherwise it belongs to this timeframe.
    """
    direction = int(direction)
    if direction not in {-1, 1}:
        return {"code": "unclassified", "category": "unclassified",
                "label": "未分类入场", "direction": direction,
                "trend_source_timeframe": "-", "rule": "没有可执行的五类身份"}
    if five_minute_local_reversal:
        category = LOCAL_ENDPOINT
    elif stage3_recovery:
        category = STAGE3_RECOVERY
    elif true_endpoint_reversal:
        category = TRUE_ENDPOINT
    elif trend_continuation:
        category = (HIGHER_TREND_CONTINUATION if higher_timeframe_trend
                    else CURRENT_TREND_CONTINUATION)
    else:
        category = LOCAL_ENDPOINT
    side = "short" if direction < 0 else "long"
    label = entry_category_label(direction, category)
    rule = _RULES[category]
    trend_source = (
        str(higher_timeframe_source) if category == HIGHER_TREND_CONTINUATION
        else "5m" if category == CURRENT_TREND_CONTINUATION else "-")
    if five_minute_local_reversal:
        label = ("上一级5分钟顶部局部反转做空" if direction < 0
                 else "上一级5分钟底部局部反转做多")
        rule = (
            "一分钟仍在原趋势内，但上一级5分钟局部顶底出现反向实体半覆盖；"
            "由5分钟确认局部反转，不要求一分钟先穿过MA5，也不冒充真正双周期反转")
        trend_source = "5m"
    if category == HIGHER_TREND_CONTINUATION and trend_source == "5m":
        label = ("上一级5分钟顶部反抽追空" if direction < 0
                 else "上一级5分钟底部回踩做多")
        rule = (
            "一分钟三均线末端位于上一级5分钟回踩/反抽位置；五分钟反向K线"
            "必须覆盖前一根反向实体至少一半，但不要求一分钟先穿过MA5")
    return {
        "code": f"{category}_{side}",
        "category": category,
        "label": label,
        "direction": direction,
        "rule": rule,
        "trend_source_timeframe": trend_source,
    }


def classify_review_shape(direction: int, shape_code: str, *,
                          stage3_recovery: bool = False,
                          higher_timeframe_trend: bool = False) -> dict[str, object]:
    """Classify a review-zone row when no persisted order identity exists."""
    if str(shape_code) == "sideways_neutral" and not stage3_recovery:
        direction = int(direction)
        side = "short" if direction < 0 else "long"
        label = ("横盘震荡偏空候选（尚未满足下单）" if direction < 0
                 else "横盘震荡偏多候选（尚未满足下单）")
        return {
            "code": f"sideways_directional_candidate_{side}",
            "category": "non_entry_observation",
            "label": label,
            "direction": direction,
            "trend_source_timeframe": "-",
            "rule": ("复查必须保留事件自身的偏多或偏空判断；横盘方向候选只有在后续形成"
                     "对应局部顶底并满足新鲜MA5位置/触发规则后才允许下单"),
        }
    true_shape = "true_top_reversal" if int(direction) < 0 else "true_bottom_reversal"
    continuation_shapes = {
        "downtrend_continuation_short", "downtrend_continuation_range",
        "uptrend_continuation_long",
    }
    return classify_entry_category(
        direction,
        stage3_recovery=stage3_recovery,
        true_endpoint_reversal=str(shape_code) == true_shape,
        trend_continuation=str(shape_code) in continuation_shapes,
        higher_timeframe_trend=higher_timeframe_trend,
    )
