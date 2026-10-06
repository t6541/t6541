"""Durable identities extracted from the 134 profitable-position study.

The frozen study could classify 77 positions into four repeatable families:
21 bottom reversals, 24 top reversals, 15 pullback longs and 17 throwback
shorts.  The remaining 57 are not promoted because their original trigger
could not be reconstructed reliably.
"""
from __future__ import annotations

from dataclasses import dataclass


WINNING_TEMPLATE_EVIDENCE = {
    "bottom_reversal_long": 21,
    "top_reversal_short": 24,
    "pullback_long": 15,
    "throwback_short": 17,
}


@dataclass(frozen=True)
class WinningTemplate:
    code: str
    evidence_count: int
    rule: str


def winning_template_for(classification: dict[str, object]) -> WinningTemplate:
    direction = int(classification.get("direction") or 0)
    category = str(classification.get("category") or "")
    continuation = category in {
        "current_timeframe_trend_continuation",
        "higher_timeframe_trend_continuation",
    }
    if continuation:
        code = "pullback_long" if direction > 0 else "throwback_short"
        rule = ("已有趋势中的真实回踩后首次重新转强" if direction > 0 else
                "已有下降趋势中的真实反抽后首次重新转弱")
    else:
        code = "bottom_reversal_long" if direction > 0 else "top_reversal_short"
        rule = ("底部或外沿衰竭后价格进入MA5有效内侧" if direction > 0 else
                "顶部或外沿衰竭后价格进入MA5有效内侧")
    return WinningTemplate(code, WINNING_TEMPLATE_EVIDENCE.get(code, 0), rule)
