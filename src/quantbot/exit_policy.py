from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitPolicy:
    mode: str
    take_profit_trigger_type: str
    reason: str

    @property
    def uses_trailing(self) -> bool:
        return self.mode == "trailing"


@dataclass(frozen=True)
class FixedProtection:
    stop: float
    take_profit: float
    risk: float
    reward: float


TREND_CONTINUATION_KINDS = frozenset({
    "trend_continuation",
    "downtrend_continuation_short",
    "uptrend_continuation_long",
})

ONE_MINUTE_MA5_EXIT_KINDS = frozenset({
    "one_minute_ma_fan_endpoint_five_minute_half_cover_short",
    "one_minute_ma_fan_endpoint_five_minute_half_cover_long",
})


def shared_exit_policy(entry_kind: str, *, higher_timeframe_trend_confirmed: bool,
                       waterfall_confirmed: bool = False) -> ExitPolicy:
    """Choose one common exit mode for strategies 01/02/03.

    Short-horizon reversal/retest entries realize their planned target as soon
    as last price touches it.  Only an explicitly identified continuation with
    higher-timeframe support may use a trailing exit to pursue a larger move.
    """
    if entry_kind in ONE_MINUTE_MA5_EXIT_KINDS:
        return ExitPolicy(
            "ma5_turn", "local_closed_1m_ma5_turn",
            "One-minute MA fan endpoint probe exits when the closed 1m MA5 turns; "
            "only a later durable multi-timeframe reversal may upgrade to the 5m MA5.",
        )
    if (entry_kind in TREND_CONTINUATION_KINDS and higher_timeframe_trend_confirmed
            and waterfall_confirmed):
        return ExitPolicy("trailing", "server_trailing", "六周期同向续势或瀑布确认：服务器追踪持有。")
    return ExitPolicy(
        "ma5_turn", "local_closed_5m_ma5_turn",
        "保留服务器结构止损；正常止盈参照已收盘5分钟K线MA5，MA5由原方向转为走平或反向拐弯时必须止盈",
    )


def normalize_fixed_protection(
    entry: float, direction: int, stop: float, proposed_take_profit: float, atr_1m: float,
) -> FixedProtection:
    """Enforce meaningful absolute and relative space for all fixed exits."""
    if direction not in {-1, 1} or entry <= 0 or atr_1m <= 0:
        raise ValueError("固定止盈止损参数无效")
    minimum_risk = max(entry * .0020, atr_1m * 2.0)
    current_risk = abs(entry - stop)
    risk = max(current_risk, minimum_risk)
    normalized_stop = entry - direction * risk
    proposed_reward = direction * (proposed_take_profit - entry)
    reward = max(proposed_reward, entry * .0030, atr_1m * 3.0, risk * 1.5)
    return FixedProtection(
        normalized_stop, entry + direction * reward, risk, reward,
    )
