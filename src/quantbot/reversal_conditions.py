from __future__ import annotations

import pandas as pd

from .entry_risk import latest_atr


def reversal_three_stage_condition_audit(one_minute: pd.DataFrame,
                                         five_minute: pd.DataFrame,
                                         direction: int) -> dict:
    """Freeze the original 1m/5m three-stage reversal checklist at entry time."""
    one = one_minute.sort_values("date").reset_index(drop=True)
    five = five_minute.sort_values("date").reset_index(drop=True)
    if direction not in {-1, 1} or len(one) < 21 or len(five) < 21:
        return {"direction": direction, "complete": False, "conditions": [],
                "summary": "反转三阶段数据不足"}
    close1 = one["close"].astype(float)
    ma5 = close1.rolling(5).mean(); ma10 = close1.rolling(10).mean(); ma20 = close1.rolling(20).mean()
    price = float(close1.iloc[-1]); atr1 = max(latest_atr(one), 1e-9)
    ma5_slope = float(ma5.iloc[-1] - ma5.iloc[-2])
    recent3 = one.tail(3); prior12 = one.iloc[-15:-3]
    recent_body_low = float(recent3[["open", "close"]].astype(float).min(axis=1).min())
    prior_body_low = float(prior12[["open", "close"]].astype(float).min(axis=1).min())
    recent_body_high = float(recent3[["open", "close"]].astype(float).max(axis=1).max())
    prior_body_high = float(prior12[["open", "close"]].astype(float).max(axis=1).max())
    price_inside = direction * (price - float(ma5.iloc[-1])) >= 0
    ma5_turn = direction * ma5_slope >= -0.02 * atr1
    local_extreme = recent_body_low <= prior_body_low if direction > 0 else recent_body_high >= prior_body_high
    ordered = (float(ma5.iloc[-1]) < float(ma10.iloc[-1]) < float(ma20.iloc[-1])
               if direction > 0 else float(ma5.iloc[-1]) > float(ma10.iloc[-1]) > float(ma20.iloc[-1]))
    outer = (price <= min(float(ma5.iloc[-1]), float(ma20.iloc[-1])) + .35 * atr1
             if direction > 0 else price >= max(float(ma5.iloc[-1]), float(ma20.iloc[-1])) - .35 * atr1)

    close5 = five["close"].astype(float); fma5 = close5.rolling(5).mean(); fma20 = close5.rolling(20).mean()
    current, previous = five.iloc[-1], five.iloc[-2]
    current_body = abs(float(current["close"]) - float(current["open"]))
    previous_body = abs(float(previous["close"]) - float(previous["open"]))
    opposite_previous = (float(previous["close"]) < float(previous["open"]) if direction > 0
                         else float(previous["close"]) > float(previous["open"]))
    correct_color = (float(current["close"]) > float(current["open"]) if direction > 0
                     else float(current["close"]) < float(current["open"]))
    cover_ratio = current_body / max(previous_body, 1e-9) if opposite_previous else 0.0
    five_outer = (max(float(current["open"]), float(current["close"])) < float(fma5.iloc[-1])
                  and float(current["close"]) < float(fma20.iloc[-1]) if direction > 0 else
                  min(float(current["open"]), float(current["close"])) > float(fma5.iloc[-1])
                  and float(current["close"]) > float(fma20.iloc[-1]))
    values = (
        ("1m价格进入MA5内侧", price_inside, f"价格={price:.2f} MA5={ma5.iloc[-1]:.2f}"),
        ("1m MA5走平并向反转方向拐弯", ma5_turn, f"MA5斜率={ma5_slope:.4f}"),
        ("最近3根实体形成12根局部底/顶", local_extreme, f"近3实体={recent_body_low:.2f}/{recent_body_high:.2f} 旧12={prior_body_low:.2f}/{prior_body_high:.2f}"),
        ("1m位于三均线发散末端", outer, f"MA5={ma5.iloc[-1]:.2f} MA20={ma20.iloc[-1]:.2f}"),
        ("1m反转前均线排列", ordered, f"MA5/10/20={ma5.iloc[-1]:.2f}/{ma10.iloc[-1]:.2f}/{ma20.iloc[-1]:.2f}"),
        ("5m反向实体仍在MA5与MA20外侧", five_outer, f"收盘={current['close']} MA5={fma5.iloc[-1]:.2f} MA20={fma20.iloc[-1]:.2f}"),
        ("5m相邻反向实体覆盖至少45%", correct_color and opposite_previous and cover_ratio >= .45, f"覆盖={cover_ratio:.1%}"),
    )
    conditions = [{"name": name, "matched": bool(ok), "evidence": evidence} for name, ok, evidence in values]
    matched = sum(item["matched"] for item in conditions)
    return {"direction": direction, "complete": matched == len(conditions),
            "matched": matched, "total": len(conditions), "conditions": conditions,
            "summary": f"反转三阶段原始规则 {matched}/{len(conditions)}项符合"}
