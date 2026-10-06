from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class StructureProtectionPlan:
    allowed: bool
    reason: str
    stop: float = 0.0
    risk: float = 0.0
    activation: float = 0.0
    callback: float = 0.0


def tradeable_profit_space(
    entry: float, direction: int, stop: float, target: float, atr_1m: float, *,
    minimum_r: float = 1.5, minimum_atr: float = .8, minimum_price_pct: float = .0012,
    minimum_gross_points: float = 0.0, estimated_cost_points: float = 0.0,
    minimum_net_points: float = 0.0, quote_tolerance_points: float = 0.02,
) -> tuple[bool, str, float]:
    """Reject tiny-profit orders; never manufacture room by moving the target."""
    if direction not in {-1, 1} or min(entry, stop, target, atr_1m) <= 0:
        return False, "最小可交易利润空间数据不足", 0.0
    risk = (entry - stop) if direction > 0 else (stop - entry)
    reward = (target - entry) if direction > 0 else (entry - target)
    cost_adjusted_required = estimated_cost_points + minimum_net_points
    required = max(risk * minimum_r, atr_1m * minimum_atr, entry * minimum_price_pct,
                   minimum_gross_points, cost_adjusted_required)
    if risk <= 0 or reward <= 0:
        return False, "止盈止损方向错误，放弃下单", required
    tolerance = max(0.0, quote_tolerance_points, estimated_cost_points * .02)
    if reward + tolerance + 1e-9 < required:
        return False, (
            f"预计利润空间仅{reward:.2f}点，低于共享最低{required:.2f}点"
            f"（至少{minimum_r:.1f}R、{minimum_atr:.1f}倍1分钟ATR、价格{minimum_price_pct:.2%}三者取高），放弃小空间订单"
        ), required
    net_room = reward - estimated_cost_points
    return True, (f"预计毛空间{reward:.2f}点，扣除手续费和滑点估算"
                  f"{estimated_cost_points:.2f}点后净空间{net_room:.2f}点，"
                  f"达到共享最低{required:.2f}点"
                  + (f"（含{tolerance:.2f}点报价/成本容差）"
                     if reward < required else "")), required


def adaptive_profit_requirements(
    risk: float, atr_1m: float, estimated_cost_points: float, *,
    fast_structure: bool,
) -> tuple[float, float, float]:
    """Return R, ATR and cost floors without an arbitrary fixed-point gate."""
    if min(risk, atr_1m, estimated_cost_points) < 0:
        raise ValueError("risk, ATR and estimated cost must be non-negative")
    if fast_structure:
        # A compact early reversal/continuation may have less than three points
        # of room, but still needs 1.2R, meaningful movement and 1.5x costs.
        return 1.2, .6, estimated_cost_points * 1.5
    return 1.8, .8, estimated_cost_points * 3.0


def latest_atr(frame, period: int = 14) -> float:
    ordered = frame.sort_values("date").reset_index(drop=True)
    if len(ordered) < period + 1:
        return 0.0
    high, low = ordered["high"].astype(float), ordered["low"].astype(float)
    previous = ordered["close"].astype(float).shift(1)
    tr = pd.concat((high - low, (high - previous).abs(), (low - previous).abs()), axis=1).max(axis=1)
    return float(tr.tail(period).mean())


def structure_protection_plan(
    entry: float, direction: int, stop: float, atr_1m: float, atr_5m: float, *,
    activation_r: float = 1.5, max_atr_1m: float = 1.5, max_atr_5m: float = .8,
) -> StructureProtectionPlan:
    """Reject an uneconomic structural stop instead of silently widening risk."""
    if direction not in {-1, 1} or min(entry, stop, atr_1m, atr_5m) <= 0:
        return StructureProtectionPlan(False, "结构止盈止损数据不足")
    risk = (entry - stop) if direction > 0 else (stop - entry)
    if risk <= 0:
        return StructureProtectionPlan(False, "结构止损方向错误")
    if risk > atr_1m * max_atr_1m or risk > atr_5m * max_atr_5m:
        return StructureProtectionPlan(
            False,
            f"结构止损距离{risk:.2f}点（1m {risk / atr_1m:.2f} ATR / 5m {risk / atr_5m:.2f} ATR）过远，放弃入场",
        )
    activation = entry + direction * risk * activation_r
    return StructureProtectionPlan(
        True, f"结构风险{risk:.2f}点，移动止盈在{activation_r:.1f}R启动",
        stop, risk, activation, max(entry * .0005, risk * .15),
    )


def structure_profit_runway(
    five_minute_market, entry_price: float, direction: int, risk: float, *,
    lookback: int = 12, minimum_r: float = 1.2, minimum_atr: float = 0.8,
    nearest_boundary: bool = False, estimated_cost_points: float = 0.0,
    minimum_cost_multiple: float = 0.0,
) -> tuple[bool, str, float]:
    """Shared symmetric structure-space gate for trend-following entries."""
    if direction not in {-1, 1}:
        raise ValueError("direction must be long or short")
    frame = five_minute_market.sort_values("date").reset_index(drop=True)
    if len(frame) < max(21, lookback + 1) or entry_price <= 0 or risk <= 0:
        return False, "结构盈利空间数据不足", 0.0
    high, low = frame["high"].astype(float), frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous = close.shift(1)
    true_range = pd.concat(
        (high - low, (high - previous).abs(), (low - previous).abs()), axis=1
    ).max(axis=1)
    atr = float(true_range.tail(14).mean())
    prior = frame.iloc[-lookback - 1:-1]
    body_high = prior[["open", "close"]].astype(float).max(axis=1)
    body_low = prior[["open", "close"]].astype(float).min(axis=1)
    required = max(risk * minimum_r, atr * minimum_atr,
                   estimated_cost_points * minimum_cost_multiple)
    if direction > 0:
        levels = body_high[body_high > entry_price]
        if levels.empty:
            return True, "上方未发现近期5分钟实体压力", float("inf")
        boundary = float(levels.min() if nearest_boundary else levels.max())
        room, label = boundary - entry_price, "实体压力"
    else:
        levels = body_low[body_low < entry_price]
        if levels.empty:
            return True, "下方未发现近期5分钟实体支撑", float("-inf")
        boundary = float(levels.max() if nearest_boundary else levels.min())
        room, label = entry_price - boundary, "实体支撑"
    if room < required:
        return False, (
            f"近期5分钟{label}{boundary:.2f}仅剩{room:.2f}点，"
            f"低于所需{required:.2f}点，剩余利润空间不足"
        ), boundary
    return True, f"结构盈利空间{room:.2f}点，满足最低{required:.2f}点", boundary
