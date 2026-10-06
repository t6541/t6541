"""Research-only quality gates for the redesigned account-05 identities."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .account05_strategy import Trend15m


REVERSAL_PRIORITY = {
    "真正底部做多": 30, "真正顶部做空": 30,
    "局部底部做多": 20, "局部顶部做空": 20,
    "上涨趋势回踩追多": 10, "下跌趋势反抽追空": 10,
}


@dataclass(frozen=True)
class SignalQualityInput:
    identity: str
    direction: int
    trend_5m: Trend15m
    trend_15m: Trend15m
    trend_1h: Trend15m
    entry_price: Decimal
    target_price: Decimal
    stop_price: Decimal
    estimated_round_trip_cost: Decimal
    fresh_closed_one_minute_event: bool
    five_minute_bar: str


@dataclass(frozen=True)
class SignalQualityDecision:
    allowed: bool
    reason: str


def quality_gate(item: SignalQualityInput) -> SignalQualityDecision:
    if item.identity not in REVERSAL_PRIORITY or item.direction not in (-1, 1):
        return SignalQualityDecision(False, "未知账户05身份或方向")
    if not item.fresh_closed_one_minute_event:
        return SignalQualityDecision(False, "缺少新的已收盘1分钟结构事件")
    reward = Decimal(item.direction) * (item.target_price - item.entry_price)
    risk = Decimal(item.direction) * (item.entry_price - item.stop_price)
    if reward <= 0 or risk <= 0:
        return SignalQualityDecision(False, "目标价或结构止损方向无效")
    if reward < item.estimated_round_trip_cost * Decimal("3"):
        return SignalQualityDecision(False, "第一合理目标空间不足往返成本3倍")
    if reward < risk * Decimal("1.5"):
        return SignalQualityDecision(False, "预计收益风险比低于1.5")
    if item.identity == "局部底部做多":
        valid = (item.trend_5m is Trend15m.DOWN
                 and item.trend_15m is not Trend15m.UP)
    elif item.identity == "真正底部做多":
        valid = (item.trend_5m is Trend15m.DOWN
                 and item.trend_15m is not Trend15m.UP)
    elif item.identity == "局部顶部做空":
        valid = (item.trend_5m is Trend15m.UP
                 and item.trend_15m is not Trend15m.DOWN)
    elif item.identity == "真正顶部做空":
        valid = (item.trend_5m is Trend15m.UP
                 and item.trend_15m is not Trend15m.DOWN)
    elif item.identity == "上涨趋势回踩追多":
        valid = (item.trend_5m is Trend15m.UP
                 and item.trend_15m is Trend15m.UP
                 and item.trend_1h is Trend15m.UP)
    else:
        valid = (item.trend_5m is Trend15m.DOWN
                 and item.trend_15m is Trend15m.DOWN)
    if not valid:
        return SignalQualityDecision(False, "多周期背景不符合该身份")
    return SignalQualityDecision(True, "新结构、多周期背景、成本空间和收益风险比均通过")


def choose_single_identity(items: tuple[SignalQualityInput, ...]) -> SignalQualityInput | None:
    """Choose one owner for a bar; a local-to-true upgrade is not a second trade."""
    allowed = [item for item in items if quality_gate(item).allowed]
    if not allowed:
        return None
    return max(allowed, key=lambda item: REVERSAL_PRIORITY[item.identity])
