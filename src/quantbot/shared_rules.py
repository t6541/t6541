from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    name: str
    family: str
    statistics_branch: str


# Stable IDs survive strategy renames.  A future combined strategy selects
# these IDs instead of copying implementations from strategy 01/02/03.
RULE_CATALOG = {
    item.rule_id: item for item in (
        RuleDefinition("extreme.relative.reversal", "相对极值反转", "reversal", "relative_extreme_reversal"),
        RuleDefinition("extreme.low.sweep_reclaim", "低位扫损收回早触发", "reversal", "relative_extreme_reversal"),
        RuleDefinition("extreme.high.sweep_reject", "高位扫高回落早触发", "reversal", "relative_extreme_reversal"),
        RuleDefinition("trend.ma20.retest", "MA20回踩续势", "continuation", "trend_continuation"),
        RuleDefinition("trend.breakout.retest", "结构突破回踩", "continuation", "trend_continuation"),
        RuleDefinition("trend.pullback.chase", "趋势回抽续势（多空镜像）", "continuation", "trend_continuation"),
        RuleDefinition("trend.regime.reversal", "趋势反转候选与确认状态机", "regime", "trend_regime"),
        RuleDefinition("trend.regime.candidate_entry", "反转候选区抢先确认", "reversal", "candidate_reversal"),
        RuleDefinition("extreme.terminal.acceleration", "末端加速反转", "reversal", "terminal_reversal"),
        RuleDefinition("risk.structure.runway", "结构盈利空间", "risk", "risk_filter"),
        RuleDefinition("extreme.structure.sniper", "结构极值预埋狙击", "reversal", "structure_sniper"),
        RuleDefinition("entry.staged.limit_market", "全阶段限价预埋与市价兜底", "execution", "staged_entry"),
        RuleDefinition("entry.queue.arbitration", "多规则排队与成交仲裁", "execution", "entry_queue"),
    )
}


STRATEGY_RULE_SELECTION = {
    # Documentation/selection target for the future combined executor.  It
    # owns no credentials or live execution path yet; current Demo validation
    # remains isolated in strategy_01/02/03.
    "shared_strategy": tuple(RULE_CATALOG),
    "strategy_01": tuple(RULE_CATALOG),
    "strategy_02": (
        "extreme.relative.reversal", "extreme.low.sweep_reclaim", "extreme.high.sweep_reject", "trend.pullback.chase",
        "trend.regime.reversal",
        "trend.regime.candidate_entry",
        "extreme.terminal.acceleration",
        "risk.structure.runway",
        "extreme.structure.sniper",
    ),
    "strategy_03": (
        "extreme.low.sweep_reclaim", "extreme.high.sweep_reject", "trend.ma20.retest", "trend.breakout.retest", "trend.pullback.chase",
        "trend.regime.reversal",
        "trend.regime.candidate_entry",
        "extreme.terminal.acceleration",
        "risk.structure.runway",
        "extreme.structure.sniper",
    ),
}


def selected_rules(strategy_id: str) -> tuple[RuleDefinition, ...]:
    """Return reusable rules selected by a strategy or future composite."""
    return tuple(RULE_CATALOG[key] for key in STRATEGY_RULE_SELECTION[strategy_id])
