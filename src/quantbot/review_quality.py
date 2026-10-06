"""Auditable post-review entry and profit-protection gates (no exchange writes)."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import pandas as pd

OBSERVE_ONLY_BRANCHES = frozenset()


def reversal_location(one, five, direction: int) -> tuple[bool, str, str]:
    """A recent extreme must be at an edge, not a turn in the range middle.

    6/3 are trailing windows, never a requirement to wait for future bars.
    The extreme timestamp also owns the single trial across candle changes.
    """
    if direction not in (-1, 1) or len(one) < 20 or len(five) < 20:
        return False, "反转位置数据不足", ""
    anchor = ""
    for frame, width, label in ((one, 6, "1m"), (five, 3, "5m")):
        frame = frame.sort_values("date").tail(20)
        lo, hi = float(frame.low.min()), float(frame.high.max())
        if not math.isfinite(hi - lo) or hi <= lo:
            return False, "反转区域无有效振幅", ""
        recent = frame.tail(width)
        index = recent.low.idxmin() if direction > 0 else recent.high.idxmax()
        extreme = float(recent.loc[index, "low" if direction > 0 else "high"])
        location = (extreme - lo) / (hi - lo)
        if (direction > 0 and location > .25) or (direction < 0 and location < .75):
            return False, f"{label}反转锚点位于区间{location:.0%}，不在底部/顶部边缘", ""
        if label == "1m":
            anchor = pd.Timestamp(recent.loc[index, "date"]).isoformat()
    return True, "1m及5m局部极值均位于近期区域边缘", anchor


def estimated_roundtrip_points(entry: float, taker_rate: float = .0005,
                               slippage_rate: float = .0001) -> float:
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError("invalid entry price")
    return entry * (2 * taker_rate + slippage_rate)


def profit_stop(entry: float, original_stop: float, current: float,
                direction: int) -> float | None:
    """Use current executable evidence, never lifecycle historical MFE.

    At costs + 0.5R protect costs; after costs + 1R retain half the
    net excursion. Caller must identify the exact server stop and tighten only.
    """
    if direction not in (-1, 1) or not all(
            math.isfinite(x) and x > 0 for x in (entry, original_stop, current)):
        return None
    risk = direction * (entry - original_stop)
    costs = estimated_roundtrip_points(entry)
    move = direction * (current - entry)
    if risk <= 0 or move < costs + .5 * risk:
        return None
    retained = costs if move < costs + risk else costs + (move - costs) * .5
    return entry + direction * retained


def protect_owned_profit(client, store, snapshot, instrument: str, current: float) -> int:
    """Only exact client IDs may amend protection. Missing identity is audited."""
    changed = 0
    positions = {(str(p.get("instId")), str(p.get("posSide"))) for p in
                 snapshot.get("positions", []) if abs(float(p.get("pos") or 0)) > 0}
    for row in store.strategy_trade_lifecycles(instrument):
        if row["status"] != "open":
            continue
        direction = int(row["direction"])
        side = "long" if direction > 0 else "short"
        if (instrument, side) not in positions:
            continue
        proposed = profit_stop(float(row["entry_reference"] or 0),
                               float(row["stop_price"] or 0), current, direction)
        if proposed is None:
            continue
        uid = str(row["trade_uid"])
        stops = [a for a in snapshot.get("algo_orders", [])
                 if a.get("instId") == instrument and a.get("posSide") == side
                 and str(a.get("algoClOrdId") or a.get("attachAlgoClOrdId") or "") == uid[:31] + "P"
                 and float(a.get("slTriggerPx") or 0) > 0
                 and a.get("ordType") != "move_order_stop"]
        if len(stops) != 1:
            store.record_event("profit_protection_unresolved", {
                "trade_uid": uid, "reason": "不能唯一匹配服务器止损，等待对账"})
            continue
        target = Decimal(str(proposed)).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN if direction > 0 else ROUND_UP)
        if direction * (float(target) - float(stops[0]["slTriggerPx"])) < .01:
            continue
        try:
            response = client.tighten_active_stop_loss(stops[0], str(target))
            if str(response.get("code", "")) != "0" or not response.get("data") or any(
                    str(item.get("sCode", "")) != "0" for item in response.get("data", [])):
                raise ValueError("exchange rejected protective amendment")
            store.record_event("profit_protection_tightened", {
                "trade_uid": uid, "stop": str(target), "observed_price": current,
                "original_risk_stop": row["stop_price"]})
            stops[0]["slTriggerPx"] = str(target)
            changed += 1
        except Exception as exc:
            store.record_event("profit_protection_failed", {
                "trade_uid": uid, "error_type": type(exc).__name__,
                "reason": "收紧失败，保留原服务器止损，下轮重新核验"})
    return changed
