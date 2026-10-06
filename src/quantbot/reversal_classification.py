from __future__ import annotations

import pandas as pd

from .price_reversal import recent_ma_fan_endpoint


def _prepared(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
    close = result["close"].astype(float)
    for window in (5, 10, 20):
        result[f"ma{window}"] = close.rolling(window).mean()
    return result.dropna(subset=["ma5", "ma10", "ma20"]).reset_index(drop=True)


def _stack(row: pd.Series) -> str:
    ma5, ma10, ma20 = (float(row[name]) for name in ("ma5", "ma10", "ma20"))
    if ma5 > ma10 > ma20:
        return "bullish"
    if ma5 < ma10 < ma20:
        return "bearish"
    return "mixed"


def _recent_has(frame: pd.DataFrame, stack: str, bars: int) -> bool:
    return any(_stack(row) == stack for _, row in frame.tail(bars).iterrows())


def five_minute_weakening_cover(frame: pd.DataFrame, direction: int,
                                *, approximate_ratio: float = .45,
                                required_anchor_index: int | None = None,
                                ) -> tuple[bool, int | None, float]:
    """Accept 1-3 opposite 5m bodies cumulatively weakening one impulse body."""
    ordered = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if len(ordered) < 2 or direction not in {-1, 1}:
        return False, None, 0.0
    for follow_count in range(1, min(3, len(ordered) - 1) + 1):
        anchor_index = len(ordered) - follow_count - 1
        if required_anchor_index is not None and anchor_index != required_anchor_index:
            continue
        anchor = ordered.iloc[anchor_index]
        anchor_body = float(anchor["close"] - anchor["open"])
        if (direction < 0 and anchor_body <= 0) or (direction > 0 and anchor_body >= 0):
            continue
        following = ordered.iloc[anchor_index + 1:]
        opposite_bodies = (
            (following["open"].astype(float) - following["close"].astype(float)).clip(lower=0)
            if direction < 0 else
            (following["close"].astype(float) - following["open"].astype(float)).clip(lower=0)
        )
        ratio = float(opposite_bodies.sum()) / max(abs(anchor_body), 1e-9)
        latest = following.iloc[-1]
        latest_turns = (float(latest["close"]) < float(latest["open"]) if direction < 0
                        else float(latest["close"]) > float(latest["open"]))
        if latest_turns:
            return ratio >= approximate_ratio, anchor_index, ratio
    return False, None, 0.0


def _five_minute_half_cover(frame: pd.DataFrame, direction: int) -> bool:
    return five_minute_weakening_cover(frame, direction)[0]


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    previous = frame["close"].astype(float).shift(1)
    return pd.concat((
        frame["high"].astype(float) - frame["low"].astype(float),
        (frame["high"].astype(float) - previous).abs(),
        (frame["low"].astype(float) - previous).abs(),
    ), axis=1).max(axis=1).rolling(window).mean()


def _sideways_metrics(frame: pd.DataFrame, bars: int) -> dict[str, float]:
    recent = frame.tail(bars)
    close = recent["close"].astype(float)
    travelled = float(close.diff().abs().sum())
    efficiency = abs(float(close.iloc[-1] - close.iloc[0])) / max(travelled, 1e-9)
    low = float(recent["low"].astype(float).min())
    high = float(recent["high"].astype(float).max())
    position = (float(close.iloc[-1]) - low) / max(high - low, 1e-9)
    return {"efficiency": efficiency, "position": position}


def two_timeframe_sideways_zone(one_minute: pd.DataFrame,
                                five_minute: pd.DataFrame) -> tuple[bool, dict[str, float]]:
    """Detect 1m+5m compression before stale directional labels are applied."""
    one, five = _prepared(one_minute), _prepared(five_minute)
    if len(one) < 20 or len(five) < 20:
        return False, {}
    one_metrics = _sideways_metrics(one, 20)
    five_metrics = _sideways_metrics(five, 12)
    close = five["close"].astype(float)
    ma5, ma10, ma20 = (five[f"ma{period}"].astype(float) for period in (5, 10, 20))
    atr5 = float(_atr(five).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0:
        return False, {}
    sides = [1 if value > average else -1 if value < average else 0
             for value, average in zip(close.tail(12), ma20.tail(12))]
    nonzero = [value for value in sides if value]
    crossings = sum(left != right for left, right in zip(nonzero, nonzero[1:]))
    spread = (max(float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1]))
              - min(float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1]))) / atr5
    slope = abs(float(ma20.iloc[-1] - ma20.iloc[-4])) / atr5
    background_window = min(24, len(five) - 1)
    background_ma20_change = (
        float(ma20.iloc[-1] - ma20.iloc[-1 - background_window]) / atr5
        if background_window >= 4 else 0.0)
    bearish_stack_seen = any(
        _stack(row) == "bearish"
        for _, row in five.tail(36).iterrows())
    prior_drawdown_atr = (
        float(five.tail(48)["high"].astype(float).max() - close.iloc[-1]) / atr5)
    downtrend_background = bool(
        bearish_stack_seen
        and (background_ma20_change <= -.35 or prior_drawdown_atr >= 3.0))
    sideways = (one_metrics["efficiency"] <= .48
                and five_metrics["efficiency"] <= .42
                and crossings >= 1 and spread <= .65 and slope <= .24)
    return sideways, {
        "one_position": one_metrics["position"],
        "five_position": five_metrics["position"],
        "one_efficiency": one_metrics["efficiency"],
        "five_efficiency": five_metrics["efficiency"],
        "five_crossings": float(crossings),
        "five_ma_spread_atr": spread,
        "five_ma20_slope_atr": slope,
        "background_ma20_change_atr": background_ma20_change,
        "prior_drawdown_atr": prior_drawdown_atr,
        "downtrend_background": float(downtrend_background),
    }


def classify_reversal_context(one_minute: pd.DataFrame, five_minute: pd.DataFrame,
                               direction: int) -> dict[str, object]:
    """Classify a 1m trigger without making 5m colour an execution gate.

    A genuine launch reversal must have the old trend's complete MA stack on
    both 1m and 5m shortly before the turn.  Otherwise a same-direction 5m
    regime is a continuation pullback/rebound.  Mixed history remains an
    explicit candidate instead of being mislabeled as a sweep reversal.
    """
    sideways, sideways_metrics = two_timeframe_sideways_zone(
        one_minute, five_minute)
    one, five = _prepared(one_minute), _prepared(five_minute)
    if one.empty or five.empty or direction not in {-1, 1}:
        return {
            "market_shape_code": "insufficient_data",
            "market_shape_label": "资料不足（未重算）",
            "one_minute_ma_stack": "unknown",
            "five_minute_ma_stack": "unknown",
            "five_minute_half_cover": False,
        }

    one_stack, five_stack = _stack(one.iloc[-1]), _stack(five.iloc[-1])
    prior_stack = "bullish" if direction < 0 else "bearish"
    one_endpoint = recent_ma_fan_endpoint(one_minute, direction, lookback=8)
    five_endpoint = recent_ma_fan_endpoint(five_minute, direction, lookback=6)
    true_reversal = bool(one_endpoint and five_endpoint)
    if sideways:
        if bool(sideways_metrics.get("downtrend_background")):
            code, label = "downtrend_continuation_range", "下跌中续横盘区（反抽追空）"
        else:
            code, label = "sideways_neutral", "横盘震荡区（中性）"
    elif true_reversal:
        code = "true_top_reversal" if direction < 0 else "true_bottom_reversal"
        label = "真正顶部反转启动" if direction < 0 else "真正底部反转启动"
    elif direction < 0 and (five_stack == "bearish"
                            or float(five.iloc[-1]["ma5"]) < float(five.iloc[-1]["ma20"])):
        code, label = "downtrend_continuation_short", "下跌趋势中继反抽做空"
    elif direction > 0 and (five_stack == "bullish"
                            or float(five.iloc[-1]["ma5"]) > float(five.iloc[-1]["ma20"])):
        code, label = "uptrend_continuation_long", "上涨趋势中继回踩做多"
    else:
        code, label = "mixed_structure_candidate", "混合结构候选（非真正反转）"

    half_cover = _five_minute_half_cover(five, direction)
    evidence = (
        f"1m均线={one_stack}，5m均线={five_stack}；"
        f"反转前双周期完整{('多头' if prior_stack == 'bullish' else '空头')}排列="
        f"{'是' if true_reversal else '否'}；5m前实体过半反包={'是' if half_cover else '否'}"
    )
    return {
        "market_shape_code": code,
        "market_shape_label": label,
        "market_shape_evidence": evidence,
        "one_minute_ma_stack": one_stack,
        "five_minute_ma_stack": five_stack,
        "five_minute_half_cover": half_cover,
        "one_minute_ma_fan_endpoint": bool(one_endpoint),
        "five_minute_ma_fan_endpoint": bool(five_endpoint),
        **sideways_metrics,
    }
