from __future__ import annotations

from dataclasses import dataclass

RISK_PROFILES = ("aggressive", "conservative", "prudent")
PROFILE_LABELS = {"aggressive": "激进型", "conservative": "保守型", "prudent": "稳妥型"}


@dataclass(frozen=True)
class RiskProfileSpec:
    key: str
    label: str
    description: str
    enabled_rule_families: tuple[str, ...]
    max_daily_frequency: str


PROFILE_SPECS = {
    "aggressive": RiskProfileSpec("aggressive", "激进型", "允许提前反转、候选抢先和趋势规则；仍受全部安全风控约束。", ("reversal", "continuation", "regime", "risk"), "高"),
    "conservative": RiskProfileSpec("conservative", "保守型", "只做趋势确认、突破回踩和MA20回踩，不接极值提前单。", ("continuation", "regime", "risk"), "中"),
    "prudent": RiskProfileSpec("prudent", "稳妥型", "只接受多周期确认后的突破/回踩，并要求完整盈利空间检查。", ("continuation", "risk"), "低"),
}


def normalize_profile(value: str | None) -> str:
    value = str(value or "aggressive").strip().lower()
    if value not in RISK_PROFILES:
        raise ValueError(f"risk_profile must be one of {RISK_PROFILES}")
    return value


def profile_spec(value: str | None) -> RiskProfileSpec:
    return PROFILE_SPECS[normalize_profile(value)]


def profile_allows_rule(profile: str | None, rule_family: str) -> bool:
    return rule_family in profile_spec(profile).enabled_rule_families


def profile_allows_entry(profile: str | None, entry_kind: str) -> bool:
    kind = str(entry_kind or "standard").lower()
    if any(token in kind for token in ("early", "extreme", "terminal", "candidate")):
        family = "reversal"
    elif any(token in kind for token in ("pullback", "breakout", "retest", "continuation")):
        family = "continuation"
    elif "regime" in kind or "flip" in kind:
        family = "regime"
    else:
        family = "reversal"
    return profile_allows_rule(profile, family)


def profile_display(value: str | None) -> str:
    return PROFILE_LABELS[normalize_profile(value)]
