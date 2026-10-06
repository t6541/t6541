"""Conservative Live phase-1 strategy allow-list and candidate scanner.

This module creates candidates only.  It cannot submit an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .entry_risk import latest_atr, structure_protection_plan, tradeable_profit_space
from .trend_continuation import (
    downtrend_ma5_pullback_short_setup,
    downtrend_pullback_short_setup,
    trend_entry_runway,
    uptrend_ma5_pullback_long_setup,
    uptrend_pullback_long_setup,
)
from .validation_execution import validation_market_context
from .primary_timeframe import five_minute_primary_entry_gate


LIVE_POLICY_VERSION = "live-5m-primary-option-c-v1"
LIVE_ALLOWED_STRATEGY = "strategy_01"
LIVE_ALLOWED_BRANCH = "five_minute_primary_trigger"
LIVE_MAX_PLANNED_LOSS_USDT = 0.20
LIVE_MIN_REWARD_RISK = 1.5

# Aggressive Live is deliberately mapped to the already-tested Demo strategy
# branches instead of inventing new signals for production.  The separate
# activation gate is added only after its third sub-account passes an audit.
AGGRESSIVE_LIVE_BRANCHES = (
    "structure_pressure_post_only_short",
    "structure_support_post_only_long",
    "early_high_sweep_reject_short",
    "early_low_sweep_reclaim_long",
    "trend_continuation_market_entry",
    "candidate_reversal_early_entry",
    "terminal_acceleration_reversal",
    "ma20_breakout_first_pullback",
)

LIVE_PROFILES = {
    "aggressive": {"label": "账户01", "max_loss": 0.0,
                   "daily_loss": 0.0, "max_successful_trades": 0, "cooldown_minutes": 0},
    "conservative": {"label": "账户02", "max_loss": 0.0,
                     "daily_loss": 0.0, "max_successful_trades": 0, "cooldown_minutes": 0},
    "prudent": {"label": "账户03", "max_loss": 0.0,
                "daily_loss": 0.0, "max_successful_trades": 0, "cooldown_minutes": 0},
    "external_observer": {"label": "账户04", "max_loss": 0.00,
                          "daily_loss": 0.00, "max_successful_trades": 0,
                          "cooldown_minutes": 0, "execution_enabled": False,
                          "purpose": "第三方量化策略观察账户"},
    "clone_research": {"label": "账户05", "max_loss": 0.0,
                       "daily_loss": 0.0, "max_successful_trades": 1000,
                       "cooldown_minutes": 0, "execution_enabled": True,
                       "purpose": "独立双向对冲＋反转/追单小单策略"},
}


def _higher_timeframe_background(directions: dict[str, int], direction: int) -> str:
    """Describe 1H/4H context; 30m is intentionally removed."""
    higher = tuple(int(directions.get(tf, 0)) for tf in ("1H", "4H"))
    supportive = sum(value == direction for value in higher)
    opposed = sum(value == -direction for value in higher)
    if supportive == 2:
        return "大周期背景顺风（1小时、4小时同向）"
    if opposed == 2:
        return "大周期背景逆风（1小时、4小时反向）；4小时只提示，1小时由方案A结构门处理"
    return f"大周期背景混合（顺风{supportive}个、逆风{opposed}个），只作风险参考"


@dataclass(frozen=True)
class LiveCandidate:
    eligible: bool
    reason: str
    direction: int = 0
    entry_reference: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    contracts: str = "0.05"
    estimated_notional_usdt: float = 0.0
    planned_loss_usdt: float = 0.0
    reward_risk: float = 0.0
    confirmed_bar_time: str = ""
    strategy_id: str = LIVE_ALLOWED_STRATEGY
    branch: str = LIVE_ALLOWED_BRANCH
    policy_version: str = LIVE_POLICY_VERSION
    profile: str = "prudent"


def _continuation_setup(markets) -> tuple[int, str, float]:
    checks = (
        downtrend_ma5_pullback_short_setup(markets["5m"], markets["1m"], markets["15m"]),
        uptrend_ma5_pullback_long_setup(markets["5m"], markets["1m"], markets["15m"]),
        downtrend_pullback_short_setup(markets["5m"], markets["1m"], markets["15m"]),
        uptrend_pullback_long_setup(markets["5m"], markets["1m"], markets["15m"]),
    )
    for direction, reason, stop in checks:
        if direction:
            return direction, reason, stop
    return 0, "等待5分钟趋势回踩与1分钟二次确认", 0.0


def scan_conservative_live_candidate(audit_report: dict, profile: str = "prudent") -> LiveCandidate:
    if profile not in LIVE_PROFILES:
        raise ValueError("Unknown Live risk profile")
    profile_spec = LIVE_PROFILES[profile]
    if not profile_spec.get("execution_enabled", True):
        return LiveCandidate(False, f"{profile_spec['label']}仅用于观察/研究，本软件自动下单已锁定", profile=profile)
    if audit_report.get("execution_permitted") is not False:
        return LiveCandidate(False, "只读审计状态异常，拒绝生成实盘候选")
    account = audit_report.get("account", {})
    if account.get("acctLv") != "2" or account.get("posMode") != "long_short_mode":
        return LiveCandidate(False, "账户必须为单币种保证金模式和双向持仓模式")
    leverage = audit_report.get("leverage", [])
    sides = {str(item.get("posSide")): str(item.get("lever")) for item in leverage}
    if sides.get("long") != "100" or sides.get("short") != "100":
        return LiveCandidate(False, "多空全仓杠杆必须先在OKX确认均为100×")

    signals, markets, _, _, _ = validation_market_context_from_public()
    direction, reason, structural_stop = _continuation_setup(markets)
    directions = {bar: int(item.direction) for bar, item in signals.items()}
    primary_gate = None
    if not direction and profile == "aggressive":
        live_five = markets.get("5m_live")
        live_direction = -1
        if live_five is not None and not live_five.empty:
            live_bar = live_five.sort_values("date").iloc[-1]
            live_direction = -1 if float(live_bar["close"]) < float(live_bar["open"]) else 1
        for candidate_direction in (live_direction, -live_direction):
            candidate_gate = five_minute_primary_entry_gate(
                markets["5m"], markets["15m"], markets["1H"], markets["1m"], candidate_direction,
                    markets.get("5m_live"), markets.get("1m_live"), markets.get("15m_live"),
            )
            if candidate_gate.allowed:
                direction = candidate_direction
                reason = "五分钟MA20反抽独立主触发"
                primary_gate = candidate_gate
                break
    if not direction:
        return LiveCandidate(False, reason)
    if primary_gate is None:
        primary_gate = five_minute_primary_entry_gate(
            markets["5m"], markets["15m"], markets["1H"], markets["1m"], direction,
            markets.get("5m_live"), markets.get("1m_live"), markets.get("15m_live"),
        )
    if not primary_gate.allowed:
        return LiveCandidate(False, primary_gate.reason, profile=profile)
    structural_stop = primary_gate.stop
    existing_sides = {str(item.get("posSide", "")) for item in audit_report.get("open_positions", [])
                      if abs(float(item.get("pos") or 0)) > 0}
    candidate_side = "long" if direction > 0 else "short"
    if candidate_side in existing_sides:
        return LiveCandidate(False, "同方向已有持仓，禁止无依据重复叠加", profile=profile)
    background_reason = _higher_timeframe_background(directions, direction)
    reason = f"{primary_gate.reason}｜一分钟原候选仅作辅助｜{reason}｜{background_reason}"

    entry = getattr(primary_gate, "reference_price", 0.0) or float(
        markets["5m"].sort_values("date").iloc[-1]["close"])
    runway_ok, runway_reason, _ = trend_entry_runway(markets["5m"], entry, direction)
    if not runway_ok:
        return LiveCandidate(False, runway_reason)
    protection = structure_protection_plan(
        entry, direction, structural_stop, latest_atr(markets["1m"]), latest_atr(markets["5m"])
    )
    if not protection.allowed:
        return LiveCandidate(False, protection.reason)
    risk = abs(entry - protection.stop)
    target = entry + direction * risk * LIVE_MIN_REWARD_RISK
    space_ok, space_reason, rr = tradeable_profit_space(
        entry, direction, protection.stop, target, latest_atr(markets["1m"]),
        minimum_r=LIVE_MIN_REWARD_RISK,
    )
    if not space_ok:
        return LiveCandidate(False, space_reason)

    instrument = audit_report["instrument"]
    contracts = 0.05
    contract_value = float(instrument["ctVal"])
    notional = contracts * contract_value * entry
    planned_loss = contracts * contract_value * risk
    max_loss = float(profile_spec["max_loss"])
    if max_loss > 0 and planned_loss > max_loss:
        return LiveCandidate(
            False, f"固定0.05张的结构止损预计亏损{planned_loss:.4f} USDT，超过{max_loss:.2f} USDT上限",
            profile=profile,
        )
    bar_time = str((getattr(primary_gate, "candle_time", None) or signals["5m"].candle_time).isoformat())
    return LiveCandidate(
        True, reason, direction, entry, protection.stop, target,
        f"{contracts:.2f}", notional, planned_loss, rr, bar_time,
        profile=profile,
    )


def validation_market_context_from_public():
    """Indirection kept explicit so tests can prove policy without network calls."""
    class _LivePublicConfig:
        pass

    # validation_market_context currently only reads the instrument.
    cfg = _LivePublicConfig()
    cfg.okx = _LivePublicConfig()
    cfg.okx.instruments = ("ETH-USDT-SWAP",)
    return validation_market_context(cfg)
