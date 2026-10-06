from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
import json
import threading
import time

import pandas as pd

from .entry_classification import classify_entry_category
from .review_quality import (OBSERVE_ONLY_BRANCHES, reversal_location,
                             estimated_roundtrip_points, protect_owned_profit)

from .range_pivot import terminal_acceleration_long_setup, terminal_acceleration_short_setup
from .trend_continuation import (
    downtrend_ma5_pullback_short_setup,
    aggressive_double_rejection_ma5_short_setup,
    downtrend_pullback_short_setup,
    trend_entry_runway,
    uptrend_ma5_pullback_long_setup,
    uptrend_pullback_long_setup,
)
from .spike_guard import spike_circuit_breaker
from .entry_risk import (adaptive_profit_requirements, latest_atr,
                         structure_profit_runway, structure_protection_plan,
                         tradeable_profit_space)
from .exit_policy import normalize_fixed_protection, shared_exit_policy
from .waterfall_hold import waterfall_hold_signal, waterfall_trailing_prices
from .structure_sniper import execute_structure_sniper_tick
from .hedge_entry import same_side_entry_conflicts
from .execution_guards import (blocks_early_countertrend_reversal, matched_closing_fills,
                               ordinary_stop_is_too_close, primary_timeframes_aligned)
from .extreme_entries import (
    shared_high_sweep_reject_short_setup,
    shared_low_sweep_reclaim_long_setup,
    volume_stopping_pullback_long_setup,
)
from .trend_regime import (candidate_reversal_setup, classify_trend_regime, five_minute_price_structure_regime,
                           aggressive_two_timeframe_intrabar_ma5_reversal_setup,
                           aggressive_downtrend_local_high_ma5_short_setup,
                           aggressive_uptrend_local_low_ma5_long_setup,
                           aggressive_fifteen_minute_recovery_long_setup,
                           aggressive_intrabar_doji_ma5_cross_long_setup,
                           aggressive_intrabar_ma20_break_short_setup,
                           aggressive_range_top_doji_ma5_intrabar_short_setup,
                           aggressive_five_minute_high_half_cover_short_setup,
                           aggressive_five_minute_low_half_cover_long_setup,
                           aggressive_top_weakening_ma5_short_setup,
                           aggressive_expanded_ma_top_ma5_short_setup,
                           aggressive_local_top_intrabar_bear_engulf_short_setup,
                           aggressive_first_ma20_retest_intrabar_long_setup,
                           aggressive_delayed_five_ma5_bottom_recovery_long_setup,
                           aggressive_small_bottom_ma5_rebound_long_setup,
                           aggressive_weak_top_ma20_retest_short_setup,
                           bottom_color_reversal_long_setup,
                           direct_rollover_first_ma5_long_setup,
                           direct_rollover_first_ma5_short_setup,
                           direct_rollover_first_ma20_long_setup,
                           direct_rollover_first_ma20_short_setup,
                           aggressive_first_bear_ma20_short_setup,
                           three_bear_ma20_rollover_short_setup,
                           top_color_reversal_short_setup)
from .risk_profiles import profile_allows_entry, profile_display
from .primary_timeframe import (PrimaryEntryGate, _endpoint_full_reversal_confirmation,
                                five_minute_primary_entry_gate)
from .market_context import exchange_market_context
from .research_scoring import label_five_minute_sample
from .price_reversal import (latest_price_reversal, recent_ma_fan_endpoint,
                             recent_price_reversal_confirms)
from .reversal_classification import classify_reversal_context

from .config import AppConfig
from .data import okx_current_unconfirmed_market, okx_history_market
from .intraday import latest_timeframe_signal
from .okx import OkxCredentials, OkxDemoClient
from .state import SignalIntent, StateStore
from .shared_signal_center import publish_extreme_signal, recover_extreme_signal
from .pullback_execution import PULLBACK_TTL_SECONDS, register_pullback
from .winning_templates import winning_template_for
from .external_execution import latest_external_signal, pinets_market_signal
from .reversal_conditions import reversal_three_stage_condition_audit
from .trade_cycle_rules import build_entry_rule_audit
from .order_ids import related_client_order_id, stable_client_order_id

VALIDATION_VERSION = "demo-frequency-validation-v243"

_FAST_SHORT_CONFIRMATION_LOCK = threading.Lock()
_FAST_SHORT_CONFIRMATIONS: dict[str, tuple[float, int]] = {}


@dataclass(frozen=True)
class FiveSecondTopShortSignal:
    """A direct top reversal or a weakening lower-high pullback short."""

    anchor_time: str
    signal_time: str
    entry: float
    stop: float
    cover_ratio: float
    ma5: float
    ma20: float
    five_minute_ma5: float
    five_minute_ma10: float
    five_minute_ma20: float
    primary_high: float
    secondary_high: float
    atr: float
    setup_type: str
    bearish_count: int


def five_second_top_short_signal(
        one_minute: pd.DataFrame, five_minute: pd.DataFrame,
) -> FiveSecondTopShortSignal | None:
    """Detect direct top confirmation and pre-MA5 lower-high weakening.

    A direct primary top keeps the body-cover/MA5 confirmation.  Once price
    has made a lower secondary high, its first bearish candle or a continuing
    two/three-bearish sequence may trigger before crossing MA5.  The structural
    stop is always just outside that pullback cluster.
    """
    if (one_minute is None or len(one_minute) < 21
            or five_minute is None or len(five_minute) < 20):
        return None
    frame = (one_minute.sort_values("date")
             .drop_duplicates("date", keep="last").reset_index(drop=True).copy())
    for name in ("open", "high", "low", "close"):
        frame[name] = frame[name].astype(float)
    previous, current = frame.iloc[-2], frame.iloc[-1]
    previous_body = float(previous["close"] - previous["open"])
    current_body = float(current["open"] - current["close"])
    if current_body <= 0:
        return None
    cover_ratio = current_body / max(previous_body, 1e-9) if previous_body > 0 else 0.0
    close = frame["close"]
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    current_close = float(current["close"])
    atr = latest_atr(frame)
    if atr <= 0:
        return None
    current_ma5 = float(ma5.iloc[-1])
    current_ma10 = float(ma10.iloc[-1])
    current_ma20 = float(ma20.iloc[-1])
    # Direct local-maximum reversal: retain the original body-cover plus MA5
    # confirmation.  A lower-high throwback is a separate setup and must fire
    # before price crosses MA5, otherwise its small structural stop is lost.
    direct_peak = (previous_body > 0 and cover_ratio >= .45
                   and current_close <= current_ma5 + atr * .05)
    bearish_count = 0
    for index in range(len(frame) - 1, max(-1, len(frame) - 4), -1):
        row = frame.iloc[index]
        if float(row["close"]) >= float(row["open"]):
            break
        bearish_count += 1
    cluster_size = max(2, min(4, bearish_count + 1))
    cluster = frame.tail(cluster_size)
    lower_high = float(cluster["high"].max())
    cluster_anchor = cluster.loc[cluster["high"].astype(float).idxmax()]
    older = frame.iloc[-14:-cluster_size]
    prior_high = float(older["high"].max()) if not older.empty else lower_high
    prior_high_index = (int(older["high"].astype(float).idxmax())
                        if not older.empty else int(cluster_anchor.name))
    # The pullback must form a real secondary high below a recent primary
    # high, but still reach the MA5 vicinity/upper side.  The first weakening
    # red candle or a 2/3-red sequence is sufficient; no engulfing and no MA5
    # cross are required.
    first_bearish_index = len(frame) - bearish_count
    origin = frame.iloc[:first_bearish_index + 1]
    origin_current = origin.iloc[-1]
    origin_previous = origin.iloc[-2]
    origin_ma5 = float(origin["close"].rolling(5).mean().iloc[-1])
    origin_cluster = origin.tail(2)
    origin_high = float(origin_cluster["high"].max())
    origin_high_index = int(origin_cluster["high"].astype(float).idxmax())
    origin_older = origin.iloc[-14:-2]
    origin_prior_high = (float(origin_older["high"].max())
                         if not origin_older.empty else origin_high)
    origin_body = float(origin_current["open"] - origin_current["close"])
    # A continuation fallback is valid only when the sequence's first red
    # candle was already a real weakening at the lower high.  The second or
    # third red candle may not create a brand-new signal after price has
    # already fallen to support.
    primary_ma5 = float(ma5.iloc[prior_high_index])
    primary_ma10 = float(ma10.iloc[prior_high_index])
    primary_ma20 = float(ma20.iloc[prior_high_index])
    secondary_ma5 = float(ma5.iloc[origin_high_index])
    secondary_ma10 = float(ma10.iloc[origin_high_index])
    secondary_ma20 = float(ma20.iloc[origin_high_index])
    primary_above_diverging_stack = bool(
        prior_high > max(primary_ma5, primary_ma10, primary_ma20)
        and primary_ma5 > primary_ma10 > primary_ma20
        and primary_ma5 - primary_ma20 >= atr * .08
    )
    secondary_above_diverging_stack = bool(
        origin_high > max(secondary_ma5, secondary_ma10, secondary_ma20)
        and secondary_ma5 > secondary_ma10 > secondary_ma20
        and secondary_ma5 - secondary_ma20 >= atr * .08
    )
    origin_qualified = bool(
        float(origin_previous["close"]) >= float(origin_previous["open"])
        and origin_body >= atr * .03
        and float(origin_current["close"]) < float(origin_previous["close"])
        and origin_prior_high - origin_high >= atr * .12
        and origin_prior_high - origin_high <= atr * 2.0
        and origin_high >= origin_ma5 - atr * .08
        and float(origin_current["close"]) >= origin_ma5 - atr * .35
        and primary_above_diverging_stack
        and secondary_above_diverging_stack
    )
    lower_high_throwback = bool(
        bearish_count in {1, 2, 3}
        and origin_qualified
        and prior_high - lower_high >= atr * .12
        and prior_high - lower_high <= atr * 4.0
        and lower_high >= current_ma5 - atr * .08
        and current_close >= current_ma5 - atr * .35
        and float(ma5.iloc[-1] - ma5.iloc[-3]) <= atr * .35
    )
    if not direct_peak and not lower_high_throwback:
        return None
    anchor_row = previous if direct_peak else cluster_anchor
    anchor_high = max(float(anchor_row["high"]),
                      float(frame.tail(3)["high"].max()))
    recent = frame.iloc[-20:-1]
    recent_low, recent_high = float(recent["low"].min()), float(recent["high"].max())
    position = ((anchor_high - recent_low) /
                max(recent_high - recent_low, atr * .10))
    previous_above_stack = float(previous["high"]) >= max(
        float(ma5.iloc[-2]), float(ma10.iloc[-2]), float(ma20.iloc[-2]))
    if direct_peak and (position < .72 or not previous_above_stack):
        return None
    five = (five_minute.sort_values("date")
            .drop_duplicates("date", keep="last").reset_index(drop=True).copy())
    for name in ("open", "high", "low", "close"):
        five[name] = five[name].astype(float)
    five_close = five["close"]
    five_ma5 = float(five_close.rolling(5).mean().iloc[-1])
    five_ma10 = float(five_close.rolling(10).mean().iloc[-1])
    five_ma20 = float(five_close.rolling(20).mean().iloc[-1])
    five_atr = latest_atr(five)
    five_top = float(five.tail(3)["high"].max())
    # Fast shorts belong to the upper side of both timeframes.  Never open a
    # new short below either MA20.  A lower-high pre-cross entry is stricter:
    # price must still be above all three averages on 1m and 5m, otherwise the
    # move is already a downswing/bottom rather than a fresh top pullback.
    if (current_close < current_ma20 or current_close < five_ma20
            or five_top < max(five_ma5, five_ma10, five_ma20)):
        return None
    if lower_high_throwback and (
            current_close < max(current_ma5, current_ma10, current_ma20)
            or current_close < max(five_ma5, five_ma10, five_ma20)
            or not (current_ma5 > current_ma10 > current_ma20)
            or current_ma5 - current_ma20 < atr * .08
            or not (five_ma5 > five_ma10 > five_ma20)
            or five_ma5 - five_ma20 < five_atr * .08):
        return None
    buffer = max(.01, atr * .05, current_close * .00003)
    stop = round(anchor_high + buffer, 2)
    risk = stop - current_close
    if risk <= 0 or risk > atr * 2.00:
        return None
    return FiveSecondTopShortSignal(
        anchor_time=pd.Timestamp(anchor_row["date"]).isoformat(),
        signal_time=pd.Timestamp(current["date"]).isoformat(),
        entry=round(current_close, 2), stop=stop,
        cover_ratio=round(cover_ratio, 4), ma5=round(current_ma5, 4),
        ma20=round(current_ma20, 4),
        five_minute_ma5=round(five_ma5, 4),
        five_minute_ma10=round(five_ma10, 4),
        five_minute_ma20=round(five_ma20, 4),
        primary_high=round(prior_high, 4),
        secondary_high=round(lower_high, 4),
        atr=round(float(atr), 6),
        setup_type=("direct_peak_ma5_cover" if direct_peak
                    else "lower_high_throwback_weakening"),
        bearish_count=bearish_count,
    )


def five_minute_top_cover_owns_direct_entry(direction: int, stage: dict) -> bool:
    """Keep an upgraded 5m top candidate on its executable branch.

    A one-minute stage can carry the five-minute confirmation. Treating it
    later as a generic one-minute reversal re-applies the rolling-range gate
    and can contradict the already validated MA-stack/top evidence.
    """
    return direction < 0 and bool(stage.get("five_minute_half_cover"))
MISSED_MA5_PULLBACK_PREFIX = "QBVALPB"
MISSED_MA5_PULLBACK_TTL_SECONDS = PULLBACK_TTL_SECONDS

@dataclass(frozen=True)
class MissedMa5PullbackPlan:
    allowed: bool
    direction: int
    entry: float
    stop: float
    target: float
    reason: str


def missed_ma5_pullback_limit_plan(
    one_minute: pd.DataFrame, direction: int, structural_stop: float, quality_reason: str,
    *, maximum_entry_atr_from_ma5: float = .20,
    one_minute_live: pd.DataFrame | None = None,
    five_minute_live: pd.DataFrame | None = None,
) -> MissedMa5PullbackPlan:
    """Convert only a missed near-MA5 stage-2 launch into one resting pullback order."""
    frame = one_minute.sort_values("date").reset_index(drop=True)
    if (direction not in {-1, 1} or len(frame) < 21
            or not quality_reason.startswith(("[MA5_CHASE]", "[LATE_LAUNCH]"))):
        return MissedMa5PullbackPlan(False, direction, 0, 0, 0, "not a missed near-MA5 market window")
    close = frame["close"].astype(float)
    current = float(close.iloc[-1])
    ma5 = float(close.rolling(5).mean().iloc[-1])
    atr1 = latest_atr(frame)
    if atr1 <= 0 or structural_stop <= 0:
        return MissedMa5PullbackPlan(False, direction, 0, 0, 0, "ATR或结构止损不可用")
    # The order waits at the MA5 that was available when the delayed stage was
    # evaluated.  A tiny allowance keeps the limit inside the original MA5
    # pullback structure without following price higher/lower on later ticks.
    entry = ma5 + direction * atr1 * maximum_entry_atr_from_ma5
    if direction * (current - entry) <= 0:
        return MissedMa5PullbackPlan(False, direction, 0, 0, 0, "价格仍在近MA5成交窗口，无需转限价")
    # The confirmed candle can still describe the launch while the live candle
    # has already rejected it.  Never leave a pullback order behind the market
    # once the pullback has reached the intended entry, and do not turn an
    # opposite impulse candle into a passive catch order.  This closes the
    # one-bar spike trap where the market-order chase guard worked but its
    # fallback limit was filled during the reversal seconds later.
    for label, live in (("1分钟", one_minute_live), ("5分钟", five_minute_live)):
        if live is None or live.empty:
            continue
        candle = live.sort_values("date").iloc[-1]
        live_open = float(candle["open"])
        live_close = float(candle["close"])
        live_high = float(candle["high"])
        live_low = float(candle["low"])
        if direction * (live_close - entry) <= 0:
            return MissedMa5PullbackPlan(
                False, direction, 0, 0, 0,
                f"{label}实时价已到达/穿过原MA5挂价，禁止追加滞后限价")
        adverse_body = -direction * (live_close - live_open)
        live_range = max(live_high - live_low, atr1 * .01)
        if adverse_body >= atr1 * .35 and adverse_body / live_range >= .45:
            return MissedMa5PullbackPlan(
                False, direction, 0, 0, 0,
                f"{label}实时K线已出现反向实体，判定急拉/急跌后诱多诱空风险")
    risk = direction * (entry - structural_stop)
    if risk <= 0 or risk > atr1 * 2.0:
        return MissedMa5PullbackPlan(False, direction, 0, 0, 0, "原结构止损与回踩限价不匹配")
    reward = max(4.0, risk * 1.8,
                 3.0 * estimated_roundtrip_points(entry))
    target = entry + direction * reward
    return MissedMa5PullbackPlan(
        True, direction, round(entry, 2), round(structural_stop, 2), round(target, 2),
        f"错过近MA5市价窗口；改在原MA5附近等待一次回踩，限价有效{MISSED_MA5_PULLBACK_TTL_SECONDS}秒",
    )


def late_top_reversal_pullback_limit_plan(
        one_minute: pd.DataFrame, five_minute: pd.DataFrame,
        signal_time, execution_time, structural_stop: float,
) -> MissedMa5PullbackPlan:
    """Offer one short-lived MA5 retest only for a still-fresh top reversal."""
    age = (pd.to_datetime(execution_time, utc=True)
           - pd.to_datetime(signal_time, utc=True)).total_seconds()
    if age < 0 or age > 180:
        return MissedMa5PullbackPlan(False, -1, 0, 0, 0, "顶部信号已超出3分钟有效期")
    plan = missed_ma5_pullback_limit_plan(
        one_minute, -1, structural_stop, "[LATE_LAUNCH]top reversal moved beyond market window")
    if not plan.allowed:
        return plan
    risk = plan.stop - plan.entry
    allowed, reason, boundary = structure_profit_runway(
        five_minute, plan.entry, -1, risk, nearest_boundary=True,
        minimum_r=1.2, minimum_atr=.8)
    room = (plan.entry - boundary) if boundary != float("-inf") else float("inf")
    minimum_room = estimated_roundtrip_points(plan.entry) + 2.0
    if not allowed or room < minimum_room:
        return MissedMa5PullbackPlan(
            False, -1, 0, 0, 0,
            f"MA5限价下方结构空间不足：{reason}；还需覆盖成本并留2点毛空间")
    # An exchange target beyond the nearest support would misstate usable room.
    target = max(plan.target, round(boundary + .01, 2)) if room != float("inf") else plan.target
    return MissedMa5PullbackPlan(True, -1, plan.entry, plan.stop, target,
                                 f"迟到顶部信号仍新鲜，MA5一次反抽限价；{reason}")


def expired_missed_ma5_pullback_orders(snapshot: dict, *, now_ms: int,
                                       ttl_seconds: int = MISSED_MA5_PULLBACK_TTL_SECONDS) -> list[dict]:
    """Return only owned pullback orders whose short waiting window elapsed."""
    expired = []
    for order in snapshot.get("orders", []):
        if not str(order.get("clOrdId") or "").startswith(MISSED_MA5_PULLBACK_PREFIX):
            continue
        try:
            created_ms = int(order.get("cTime") or order.get("uTime") or 0)
        except (TypeError, ValueError):
            created_ms = 0
        if created_ms <= 0 or now_ms - created_ms >= ttl_seconds * 1000:
            expired.append(order)
    return expired


def merge_closed_and_live_market(closed: pd.DataFrame,
                                 live: pd.DataFrame | None) -> pd.DataFrame:
    """Combine confirmed history with the current candle, replacing duplicates."""
    frame = closed.copy()
    if live is not None and not live.empty:
        frame = pd.concat((frame, live), ignore_index=True)
    return (frame.sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True))


def top_anchor_above_bullish_ma_stack(one_minute: pd.DataFrame,
                                      anchor_time) -> tuple[bool, str]:
    """Verify that stage 1 froze a top above all three moving averages.

    At the turning point MA5/MA10 can already be converging.  Requiring their
    old strict bullish order would reject the very rollover this gate exists
    to recognise.
    """
    if len(one_minute) < 20:
        return False, "top launch zone needs at least 20 one-minute candles"
    frame = one_minute.sort_values("date").drop_duplicates("date", keep="last").copy()
    close = frame["close"].astype(float)
    frame["ma5"] = close.rolling(5).mean()
    frame["ma10"] = close.rolling(10).mean()
    frame["ma20"] = close.rolling(20).mean()
    anchor = pd.to_datetime(anchor_time, utc=True)
    dates = pd.to_datetime(frame["date"], utc=True)
    candidates = frame.loc[dates <= anchor]
    if candidates.empty:
        return False, "top launch zone cannot locate the frozen top candle"
    row = candidates.iloc[-1]
    ma5, ma10, ma20 = (float(row[name]) for name in ("ma5", "ma10", "ma20"))
    if not all(pd.notna(value) for value in (ma5, ma10, ma20)):
        return False, "top launch zone moving averages are not ready"
    allowed = float(row["high"]) > max(ma5, ma10, ma20)
    return allowed, (
        f"frozen top high {float(row['high']):.2f}; MA5/MA10/MA20 "
        f"{ma5:.2f}/{ma10:.2f}/{ma20:.2f}; "
        + ("frozen top is above MA5, MA10 and MA20"
           if allowed else "frozen top is not above all three moving averages")
    )


def high_reversal_three_timeframe_ma_exhaustion(
    one_minute: pd.DataFrame, five_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame, anchor_time,
) -> tuple[bool, str]:
    """Confirm a mature 1m+5m top; 15m is an optional strength upgrade."""
    one_allowed, one_reason = top_anchor_above_bullish_ma_stack(
        one_minute, anchor_time)
    details = [one_reason] if one_allowed else []

    def mature_exhaustion_row(label: str, source: pd.DataFrame,
                              candidate_indexes: list[int]) -> tuple[bool, str]:
        if len(source) < 20:
            return False, f"{label} mature exhaustion needs at least 20 candles"
        frame = source.sort_values("date").drop_duplicates(
            "date", keep="last").reset_index(drop=True).copy()
        close = frame["close"].astype(float)
        for period in (5, 10, 20):
            frame[f"ma{period}"] = close.rolling(period).mean()
        for index in reversed(candidate_indexes):
            if index < 19 or index >= len(frame):
                continue
            row = frame.iloc[index]
            ma5, ma10, ma20 = (float(row[name]) for name in ("ma5", "ma10", "ma20"))
            atr = latest_atr(frame.iloc[:index + 1])
            high = float(row["high"])
            recent_high = float(frame.iloc[max(0, index - 19):index + 1]["high"].max())
            mature = (
                all(pd.notna(value) for value in (ma5, ma10, ma20, atr))
                and atr > 0 and ma5 > ma10 > ma20
                # Treat a clustered double/multiple top as the same highest
                # zone; requiring the exact wick would incorrectly classify
                # the first bearish rejection bar as a lower, unrelated top.
                and high >= recent_high - atr * .55
                and ma5 - ma20 >= atr * .45
                and high - ma20 >= atr
            )
            if mature:
                return True, (
                    f"{label} mature highest-zone exhaustion: high {high:.2f}, "
                    f"MA5/MA10/MA20 {ma5:.2f}/{ma10:.2f}/{ma20:.2f}, "
                    f"MA spread {(ma5 - ma20) / atr:.2f} ATR, "
                    f"extension {(high - ma20) / atr:.2f} ATR")
        return False, (
            f"{label} is only an MA-stack startup or is not at the recent highest "
            "expanded zone")

    one = one_minute.sort_values("date").drop_duplicates(
        "date", keep="last").reset_index(drop=True)
    anchor = pd.to_datetime(anchor_time, utc=True)
    one_dates = pd.to_datetime(one["date"], utc=True)
    one_indexes = one.index[one_dates <= anchor].tolist()
    one_mature, one_mature_reason = mature_exhaustion_row(
        "1m", one, one_indexes[-6:] if one_indexes else [])
    if not one_mature:
        return False, one_mature_reason
    details.append(one_mature_reason)

    for label, source in (("5m", five_minute),):
        indexes = list(range(max(0, len(source) - 6), len(source)))
        allowed, reason = mature_exhaustion_row(label, source, indexes)
        if not allowed:
            return False, reason
        details.append(reason)
    fifteen_indexes = list(range(max(0, len(fifteen_minute) - 2), len(fifteen_minute)))
    fifteen_allowed, fifteen_reason = mature_exhaustion_row(
        "15m", fifteen_minute, fifteen_indexes)
    details.append(
        fifteen_reason if fifteen_allowed
        else "15m optional reference is not aligned; 1m+5m entry remains eligible")
    return True, "; ".join(details)


def high_half_cover_bypasses_ma5_chase(
    live_half_cover: bool, two_timeframe_exhaustion: bool, quality_reason: str,
) -> bool:
    """A mature 1m+5m exhaustion reversal enters at half-cover, not MA5."""
    return bool(
        live_half_cover and two_timeframe_exhaustion
        and (quality_reason.startswith(("[MA5_CHASE]", "[LATE_LAUNCH]"))
             or "冻结点反向一侧" in quality_reason)
    )


def sideways_top_short_bypasses_ma5_chase(
    direction: int, market_shape_code: str, five_minute_half_cover: bool,
    stage: str, quality_reason: str,
) -> bool:
    """Do not replace a confirmed range-top breakdown with an unfilled pullback."""
    return bool(
        direction < 0 and market_shape_code in {
            "sideways_neutral", "downtrend_continuation_range"}
        and five_minute_half_cover
        and stage in {"price_break_ma5", "price_below_flat_falling_ma5", "small_death_cross"}
        and quality_reason.startswith(("[MA5_CHASE]", "[LATE_LAUNCH]"))
    )


def low_reversal_two_timeframe_ma_exhaustion(
    one_minute: pd.DataFrame, five_minute: pd.DataFrame, anchor_time,
) -> tuple[bool, str]:
    """Require the 1m and 5m lows to share a mature bearish exhaustion zone."""
    details: list[str] = []

    def mature_low(label: str, source: pd.DataFrame,
                   indexes: list[int]) -> tuple[bool, str]:
        if len(source) < 20:
            return False, f"{label} mature low needs at least 20 candles"
        frame = source.sort_values("date").drop_duplicates(
            "date", keep="last").reset_index(drop=True).copy()
        close = frame["close"].astype(float)
        for period in (5, 10, 20):
            frame[f"ma{period}"] = close.rolling(period).mean()
        for index in reversed(indexes):
            if index < 19 or index >= len(frame):
                continue
            row = frame.iloc[index]
            ma5, ma10, ma20 = (float(row[name]) for name in ("ma5", "ma10", "ma20"))
            atr = latest_atr(frame.iloc[:index + 1])
            low = float(row["low"])
            recent_low = float(frame.iloc[max(0, index - 19):index + 1]["low"].min())
            mature = (
                all(pd.notna(value) for value in (ma5, ma10, ma20, atr))
                and atr > 0 and ma5 < ma10 < ma20
                and low <= recent_low + atr * .55
                and ma20 - ma5 >= atr * .45
                and ma20 - low >= atr
            )
            if mature:
                return True, (
                    f"{label} mature lowest-zone exhaustion: low {low:.2f}, "
                    f"MA5/MA10/MA20 {ma5:.2f}/{ma10:.2f}/{ma20:.2f}, "
                    f"MA spread {(ma20 - ma5) / atr:.2f} ATR, "
                    f"extension {(ma20 - low) / atr:.2f} ATR")
        return False, f"{label} is mid-trend noise or is not at a mature expanded lowest zone"

    one = one_minute.sort_values("date").drop_duplicates(
        "date", keep="last").reset_index(drop=True)
    anchor = pd.to_datetime(anchor_time, utc=True)
    one_dates = pd.to_datetime(one["date"], utc=True)
    one_indexes = one.index[one_dates <= anchor].tolist()
    allowed, reason = mature_low("1m", one, one_indexes[-6:] if one_indexes else [])
    if not allowed:
        return False, reason
    details.append(reason)
    five_indexes = list(range(max(0, len(five_minute) - 6), len(five_minute)))
    allowed, reason = mature_low("5m", five_minute, five_indexes)
    if not allowed:
        return False, reason
    details.append(reason)
    return True, "; ".join(details)


def bottom_anchor_below_bearish_ma_stack(one_minute: pd.DataFrame,
                                         anchor_time) -> tuple[bool, str]:
    """Mirror the top gate: the frozen bottom must sit below all three MAs."""
    if len(one_minute) < 20:
        return False, "bottom launch zone needs at least 20 one-minute candles"
    frame = one_minute.sort_values("date").drop_duplicates("date", keep="last").copy()
    close = frame["close"].astype(float)
    for period in (5, 10, 20):
        frame[f"ma{period}"] = close.rolling(period).mean()
    anchor = pd.to_datetime(anchor_time, utc=True)
    dates = pd.to_datetime(frame["date"], utc=True)
    candidates = frame.loc[dates <= anchor]
    if candidates.empty:
        return False, "bottom launch zone cannot locate the frozen bottom candle"
    row = candidates.iloc[-1]
    ma5, ma10, ma20 = (float(row[name]) for name in ("ma5", "ma10", "ma20"))
    if not all(pd.notna(value) for value in (ma5, ma10, ma20)):
        return False, "bottom launch zone moving averages are not ready"
    allowed = float(row["low"]) < min(ma5, ma10, ma20)
    return allowed, (
        f"frozen bottom low {float(row['low']):.2f}; MA5/MA10/MA20 "
        f"{ma5:.2f}/{ma10:.2f}/{ma20:.2f}; "
        + ("frozen bottom is below MA5, MA10 and MA20"
           if allowed else "frozen bottom is not below all three moving averages")
    )


def stage_three_micro_structure_stop(one_minute: pd.DataFrame, entry: float,
                                     direction: int, *, lookback: int = 4,
                                     maximum_points: float = 3.0) -> tuple[bool, float, str]:
    """Prefer the fresh post-trigger micro swing for a stage-3 recovery stop."""
    frame = one_minute.sort_values("date").tail(lookback)
    if direction not in {-1, 1} or entry <= 0 or len(frame) < lookback:
        return False, 0.0, "stage-3 micro structure is unavailable"
    buffer = max(latest_atr(one_minute) * .12, entry * .0002)
    if direction < 0:
        stop = float(frame["high"].astype(float).max()) + buffer
        risk = stop - entry
    else:
        stop = float(frame["low"].astype(float).min()) - buffer
        risk = entry - stop
    if 0 < risk <= maximum_points:
        return True, round(stop, 2), (
            f"stage-3 recovery uses the latest {lookback}-bar micro swing plus "
            f"{buffer:.2f} buffer; risk {risk:.2f} points")
    return False, stop, (
        f"latest {lookback}-bar micro swing needs {risk:.2f} points; "
        f"use the accepted {maximum_points:.2f}-point launch protection")


def continuation_stage_location_allowed(one_minute: pd.DataFrame, direction: int,
                                        market_shape_code: str, *,
                                        range_bars: int = 30,
                                        edge_fraction: float = .68) -> tuple[bool, str]:
    """Keep a continuation launch on the pullback side of its local range."""
    if market_shape_code not in {
        "uptrend_continuation_long", "downtrend_continuation_short",
    }:
        return True, "真正反转或混合结构不使用普通趋势中继位置门"
    frame = one_minute.sort_values("date").tail(range_bars)
    if direction not in {-1, 1} or len(frame) < range_bars:
        return False, "趋势中继位置数据不足"
    low = float(frame["low"].astype(float).min())
    high = float(frame["high"].astype(float).max())
    if high <= low:
        return False, "趋势中继局部区间无效"
    price = float(frame.iloc[-1]["close"])
    position = (price - low) / (high - low)
    long_allowed = direction > 0 and position <= edge_fraction
    short_allowed = direction < 0 and position >= 1.0 - edge_fraction
    allowed = long_allowed or short_allowed
    if allowed:
        return True, (f"趋势中继位于最近{range_bars}根{position * 100:.0f}%位置；"
                      "仍处于回踩追多/反抽追空允许区")
    boundary = edge_fraction if direction > 0 else 1.0 - edge_fraction
    return False, (f"趋势中继位于最近{range_bars}根{position * 100:.0f}%位置；"
                   f"{'做多高于' if direction > 0 else '做空低于'}允许边界"
                   f"{boundary * 100:.0f}%，属于局部末端，禁止把顶部/底部重新标成趋势回踩")


def continuation_pullback_reached_ma5_ma10(
        one_minute: pd.DataFrame, direction: int, *, lookback: int = 3) -> tuple[bool, str]:
    """Bind a continuation entry to the immediate MA5-edge pullback.

    A low/high somewhere in a wider historical window is not enough.  The
    execution candle must still be on the pullback side of MA5, while one of
    the latest bars has reached beyond both MA5 and MA10.  This keeps the
    early turn entry, but prevents a stale pullback from authorising a later
    chase at the local extreme.
    """
    frame = one_minute.sort_values("date").tail(max(20, lookback + 10)).copy()
    if direction not in {-1, 1} or len(frame) < 10:
        return False, "趋势追单的MA5/MA10位置数据不足"
    close = frame["close"].astype(float)
    frame["_ma5"] = close.rolling(5).mean()
    frame["_ma10"] = close.rolling(10).mean()
    recent = frame.tail(lookback)
    current = recent.iloc[-1]
    current_close = float(current["close"])
    current_ma5 = float(current["_ma5"])
    if direction > 0:
        touched = bool((recent["low"].astype(float)
                        < recent[["_ma5", "_ma10"]].min(axis=1)).any())
        at_edge = current_close <= current_ma5
        reached = touched and at_edge
        reason = ("最近3根1分钟K线已回踩MA5和MA10下方，且当前收盘仍在MA5下沿；"
                  "允许刚转强时追多" if reached else
                  "上涨趋势回踩追多必须由最近3根实时回踩产生，且入场收盘不得高于MA5；"
                  "历史旧低点不能授权高位追多")
    else:
        touched = bool((recent["high"].astype(float)
                        > recent[["_ma5", "_ma10"]].max(axis=1)).any())
        at_edge = current_close >= current_ma5
        reached = touched and at_edge
        reason = ("最近3根1分钟K线已反抽MA5和MA10上方，且当前收盘仍在MA5上沿；"
                  "允许刚转弱时追空" if reached else
                  "下跌趋势反抽追空必须由最近3根实时反抽产生，且入场收盘不得低于MA5；"
                  "历史旧高点不能授权低位追空")
    return reached, reason


def fresh_uptrend_ma_edge_reclaim(one_minute: pd.DataFrame) -> tuple[bool, str]:
    """Accept a fresh 1m reclaim near MA5 after a nearby MA5/MA10 pullback."""
    frame = one_minute.sort_values("date").tail(24).copy()
    if len(frame) < 20:
        return False, "上涨回踩的一分钟均线数据不足"
    close = frame["close"].astype(float)
    ma5, ma10 = close.rolling(5).mean(), close.rolling(10).mean()
    atr1 = latest_atr(frame)
    if atr1 <= 0:
        return False, "上涨回踩的一分钟ATR无效"
    recent = frame.tail(3)
    touched = any(
        float(row["low"]) <= min(float(ma5.loc[index]), float(ma10.loc[index])) + .15 * atr1
        for index, row in recent.iterrows())
    price = float(close.iloc[-1])
    # A touch alone can be a falling candle.  Require a fresh close higher than
    # the previous candle while retaining the tight MA5 chase limit.
    turning_up = price > float(close.iloc[-2])
    allowed = (touched and turning_up and float(ma5.iloc[-1]) <= price
               <= float(ma5.iloc[-1]) + .35 * atr1)
    return allowed, (
        "最近3根一分钟K线回踩MA5/MA10，当前收盘转强站上MA5且未远离超过0.35 ATR"
        if allowed else "回踩未触及MA5/MA10、收盘尚未转强，或已远离MA5超过0.35 ATR")


def trend_continuation_execution_price_ok(
        one_minute: pd.DataFrame, direction: int, signal_price: float,
        execution_price: float, *, maximum_atr: float = .35,
) -> tuple[bool, str]:
    """Recheck a pullback at the executable quote, not only its closed signal bar."""
    frame = one_minute.sort_values("date").tail(40).copy()
    if direction not in {-1, 1} or len(frame) < 20:
        return False, "趋势追单执行价复核数据不足"
    close = frame["close"].astype(float)
    ma5 = float(close.rolling(5).mean().iloc[-1])
    atr1 = latest_atr(frame)
    if atr1 <= 0:
        return False, "趋势追单执行价复核ATR无效"
    chase = direction * (float(execution_price) - float(signal_price))
    ma5_distance = direction * (float(execution_price) - ma5)
    allowed = chase <= maximum_atr * atr1 and ma5_distance <= maximum_atr * atr1
    return allowed, (
        f"执行价仍在回踩窗口：较信号顺向移动{chase / atr1:.2f} ATR，"
        f"距1分钟MA5 {ma5_distance / atr1:.2f} ATR"
        if allowed else
        f"执行价已离开回踩窗口：较信号顺向移动{chase / atr1:.2f} ATR，"
        f"距1分钟MA5 {ma5_distance / atr1:.2f} ATR；超过0.35 ATR，禁止在局部顶部追多/底部追空"
    )


def trend_alignment_allows_reversal_stage(direction: int, one_minute_direction: int,
                                          five_minute_direction: int, stage: str,
                                          five_minute_half_cover: bool,
                                          market_shape_code: str = "",
                                          same_time_stage2_count: int = 0,
                                          confirmed_reversal_anchor: bool = False,
) -> tuple[bool, str]:
    """Prefer pullbacks with an aligned 1m/5m trend; demand proof to fade it."""
    # Timeframe arrows lag exactly where an MA-spread endpoint reverses. They
    # remain visible/auditable but have no authority to veto a staged reversal.
    if direction not in {-1, 1}:
        return False, "候选方向无效"
    return True, (
        "1m/5m方向箭头仅显示与审计，不参与反转入场；"
        "由三均线发散末端反转区及一分钟阶段链决定方向")

    # Historical arrow-alignment policy retained below as unreachable source
    # documentation for release comparison.
    if market_shape_code in {"sideways_neutral", "downtrend_continuation_range"}:
        if direction > 0:
            return False, "1m+5m处于下跌中续/横盘区，禁止使用滞后上涨标签做回踩多"
        if stage in {"price_break_ma5", "price_below_flat_falling_ma5", "small_death_cross"}:
            return True, "1m+5m下跌中续横盘反抽转弱，下穿MA5/小死叉只允许小风险做空"
        return False, "1m+5m处于横盘震荡中性区；未完成上沿转弱确认，保持观察不下单"
    if direction not in {-1, 1}:
        return False, "候选方向无效"
    aligned_trend = (one_minute_direction if one_minute_direction == five_minute_direction
                     and one_minute_direction in {-1, 1} else 0)
    if not aligned_trend or direction == aligned_trend:
        side = "上涨回踩追多" if direction > 0 else "下跌反抽追空"
        return True, f"一分钟与五分钟未形成反向共振，允许{side}"
    strong_reversal = (five_minute_half_cover and market_shape_code in {
        "true_top_reversal", "true_bottom_reversal",
    })
    if strong_reversal:
        return True, "五分钟半实体覆盖与顶部/底部反转形态同时成立，按确认反转处理"
    if confirmed_reversal_anchor and same_time_stage2_count >= 2:
        return True, (
            "confirmed local reversal anchor followed by two independent same-time stage-2 "
            "signals; allow the fresh turn before lagging 1m/5m trend labels change")
    trend_side = "上涨" if aligned_trend > 0 else "下跌"
    preferred = "回踩追多" if aligned_trend > 0 else "反抽追空"
    return False, f"一分钟与五分钟同步{trend_side}，优先{preferred}；普通逆势阶段禁止抢单"


def _is_decisional_reversal_zone(pattern_type: str) -> bool:
    """Only ordinary-candle zones created by v158+ may drive execution."""
    return str(pattern_type).startswith("price_reversal_zone:")


def fresh_dual_reversal_zone_bias(patterns, reference_time, *, maximum_age_minutes: int = 20) -> tuple[int, str]:
    """Return the newest fresh direction confirmed by both 1m and 5m zones."""
    reference = pd.to_datetime(reference_time, utc=True)
    latest: dict[tuple[str, int], pd.Timestamp] = {}
    for row in patterns:
        pattern_type = str(row["pattern_type"])
        if not _is_decisional_reversal_zone(pattern_type):
            continue
        timeframe = pattern_type.rsplit(":", 1)[-1]
        if timeframe not in {"1m", "5m"}:
            continue
        direction = int(row["direction"])
        at = pd.to_datetime(row["confirmed_bar_time"], utc=True)
        try:
            raw_features = row["features_json"]
        except (KeyError, IndexError):
            raw_features = "{}"
        features = json.loads(str(raw_features or "{}"))
        if timeframe == "5m" and not (
                bool(features.get("five_minute_half_cover"))
                and bool(features.get("five_minute_ma20_reversal_confirmed"))):
            continue
        if pd.Timedelta(0) <= reference - at <= pd.Timedelta(minutes=maximum_age_minutes):
            latest[(timeframe, direction)] = max(at, latest.get((timeframe, direction), at))
    candidates = []
    for direction in (-1, 1):
        if ("1m", direction) in latest and ("5m", direction) in latest:
            candidates.append((min(latest[("1m", direction)], latest[("5m", direction)]), direction))
    if not candidates:
        return 0, "no fresh same-direction 1m+5m reversal-zone pair"
    _, direction = max(candidates)
    side = "底部做多" if direction > 0 else "顶部做空"
    return direction, f"1分钟与5分钟新鲜{side}反转区同时有效；旧反向冻结链立即失效"


def durable_dual_reversal_zone_bias(patterns, reference_time) -> tuple[int, str]:
    """Keep a confirmed dual reversal active until a true opposite pair replaces it."""
    reference = pd.to_datetime(reference_time, utc=True)
    zones: list[dict] = []
    for row in patterns:
        pattern_type = str(row["pattern_type"])
        if not _is_decisional_reversal_zone(pattern_type):
            continue
        timeframe = pattern_type.rsplit(":", 1)[-1]
        # The durable direction changes as soon as a fresh 1m+5m opposite
        # endpoint succeeds.  A 15m observation may upgrade that direction,
        # but cannot independently invalidate or replace the dual record.
        if timeframe not in {"1m", "5m"}:
            continue
        at = pd.to_datetime(row["confirmed_bar_time"], utc=True)
        if at > reference:
            continue
        features = json.loads(str(row["features_json"] or "{}"))
        zones.append({
            "timeframe": timeframe,
            "direction": int(row["direction"]), "at": at,
            "shape": str(features.get("market_shape_code") or ""),
            "five_cover": bool(features.get("five_minute_half_cover")),
            "five_ma20": bool(features.get("five_minute_ma20_reversal_confirmed")),
            "five_shape_confirmed": bool(features.get("five_minute_shape_confirmed")),
            "prior_drawdown_atr": float(features.get("prior_drawdown_atr") or 0.0),
            "one_position": float(features.get("one_position") or 0.5),
            "five_position": float(features.get("five_position") or 0.5),
            "entry": float(row["entry_reference"] if "entry_reference" in row.keys() else 0.0),
        })
    candidates: list[tuple[pd.Timestamp, int]] = []
    for anchor in zones:
        direction = anchor["direction"]
        true_shape = "true_top_reversal" if direction < 0 else "true_bottom_reversal"
        relaxed_low_endpoint = bool(
            direction > 0 and anchor["prior_drawdown_atr"] >= 3.0
            and anchor["one_position"] <= 0.35)
        if anchor["shape"] != true_shape and not relaxed_low_endpoint:
            continue
        if (relaxed_low_endpoint and anchor["entry"] > 0
                and any(zone["at"] > anchor["at"] and zone["entry"] > 0
                        # entry_reference is a trigger/body proxy rather than the
                        # exact wick low. Ignore sub-basis-point differences so
                        # an intact horizontal bottom is not erased by noise.
                        and zone["entry"] < anchor["entry"] * (1.0 - .0001)
                        for zone in zones)):
            continue
        window_start = anchor["at"] - pd.Timedelta(minutes=5)
        window_end = anchor["at"] + pd.Timedelta(minutes=30)
        matching = [zone for zone in zones if zone["direction"] == direction
                    and window_start <= zone["at"] <= window_end]
        one = [zone for zone in matching if zone["timeframe"] == "1m"]
        five = [zone for zone in matching if zone["timeframe"] == "5m"
                and zone["five_cover"] and zone["five_ma20"]]
        if one and five:
            candidates.append((max(max(zone["at"] for zone in one),
                                   max(zone["at"] for zone in five)), direction))
    if not candidates:
        return 0, "尚无可持久继承的一分钟+五分钟真正反转区"
    confirmed_at, direction = max(candidates)
    side = "顶部做空" if direction < 0 else "底部做多"
    return direction, (
        f"一分钟+五分钟{side}反转区已于{confirmed_at.isoformat()}生效；"
        "同方向更新的真正高位/低位覆盖旧锚点并持续有效；"
        "首单漏掉或止损不清除该趋势背景，仅由更新的双周期反向真正反转作废")


def durable_three_timeframe_reversal_zone_bias(
        patterns, reference_time, *, synchronization_minutes: int = 30) -> tuple[int, str]:
    """Keep the newest 1m+5m+15m endpoint as the maximum trend level.

    One-hour and four-hour observations are deliberately excluded.  The
    fifteen-minute member upgrades confidence only; execution still steps
    down to the one-minute MA5-edge pullback trigger.
    """
    reference = pd.to_datetime(reference_time, utc=True)
    zones: list[tuple[pd.Timestamp, str, int, str]] = []
    for row in patterns:
        pattern_type = str(row["pattern_type"])
        if not _is_decisional_reversal_zone(pattern_type):
            continue
        timeframe = pattern_type.rsplit(":", 1)[-1]
        if timeframe not in {"1m", "5m", "15m"}:
            continue
        at = pd.to_datetime(row["confirmed_bar_time"], utc=True)
        if at > reference:
            continue
        features = json.loads(str(row["features_json"] or "{}"))
        zones.append((at, timeframe, int(row["direction"]),
                      str(features.get("market_shape_code") or "")))

    window = pd.Timedelta(minutes=synchronization_minutes)
    candidates: list[tuple[pd.Timestamp, int]] = []
    for anchor in (zone for zone in zones if zone[1] == "15m"):
        anchor_time, _, direction, _ = anchor
        true_shape = "true_top_reversal" if direction < 0 else "true_bottom_reversal"
        matching = [zone for zone in zones
                    if zone[2] == direction and abs(zone[0] - anchor_time) <= window]
        by_timeframe = {tf: [zone for zone in matching if zone[1] == tf]
                        for tf in ("1m", "5m", "15m")}
        if not all(by_timeframe.values()):
            continue
        selected = [max(by_timeframe[tf], key=lambda zone: zone[0])
                    for tf in ("1m", "5m", "15m")]
        times = [zone[0] for zone in selected]
        if (max(times) - min(times) <= window
                and any(zone[3] == true_shape for zone in selected)):
            candidates.append((max(times), direction))
    if not candidates:
        return 0, "尚无1分钟+5分钟+15分钟同步的三均线发散末端反转区"
    confirmed_at, direction = max(candidates)
    side = "顶部做空" if direction < 0 else "底部做多"
    return direction, (
        f"1分钟+5分钟+15分钟{side}已于{confirmed_at.isoformat()}同步生效；"
        "15分钟只提升趋势确认级别，实际追单仍由1分钟MA5外沿的首次转弱/转强触发；"
        "1小时和4小时不参与方向判定"
    )


def durable_regime_continuation_primary_gate_allowed(
        *, trend_continuation_entry: bool, local_high_short: bool,
        direction: int, durable_bias: int, location_ok: bool) -> bool:
    """Let a fresh MA5-edge pullback trade the locked durable direction.

    ``local_high_short`` is retained for call compatibility; the locked trend
    now authorises both the original short-side implementation and its missing
    long-side mirror, even when the reversal entry itself never filled.
    """
    del local_high_short
    return bool(trend_continuation_entry and direction in {-1, 1}
                and durable_bias == direction and location_ok)


def countertrend_short_before_ma5_allowed(
        *, durable_bias: int, direction: int, price: float, ma5: float) -> bool:
    """Never chase a discretionary short below 1m MA5 in a durable uptrend."""
    if durable_bias > 0 and direction < 0:
        return price >= ma5
    return True


def fresh_confirmed_reversal_overrides_old_bias(
        *, direction: int, directional_cover_ok: bool,
        three_timeframe_reversal: bool = False,
        confirmed_endpoint_reversal: bool = False,
        endpoint_pair_reversal: bool = False,
        five_minute_local_reversal: bool = False) -> bool:
    """Fresh 1m+5m reversal confirmation replaces an older opposite lock."""
    return bool(direction in {-1, 1} and directional_cover_ok and (
        three_timeframe_reversal or confirmed_endpoint_reversal
        or endpoint_pair_reversal or five_minute_local_reversal))


def local_candidate_prefers_trend_continuation(
        *, direction: int, five_direction: int, fifteen_direction: int,
        local_candidate: bool, confirmed_reversal: bool,
        durable_parent_direction: int = 0) -> bool:
    """Prefer a parent-trend pullback identity even when reversal evidence overlaps."""
    return bool(direction in {-1, 1} and local_candidate
                and not confirmed_reversal
                and direction in {five_direction, fifteen_direction,
                                  durable_parent_direction})


def parent_trend_takeover_preserves_fresh_stage(
        *, direction: int, fifteen_direction: int, local_stage: bool,
        pending_takeover_direction: int = 0) -> bool:
    """Keep a fresh 1m turn when it agrees with the 15m parent trend.

    A stale opposite 5m endpoint may still classify the market while the new
    1m bottom/top is forming.  The parent trend turns that local structure into
    a pullback/throwback candidate before the stale-lock rejection runs.
    """
    return bool(direction in {-1, 1} and local_stage
                and fifteen_direction == direction
                and pending_takeover_direction in {0, direction})


def fresh_confirmed_top_releases_old_bottom(*, five_cover: bool, old_bias: int,
                                            fifteen_direction: int, hour_direction: int,
                                            latest_top_time, old_lock_time) -> bool:
    """A newer 1m top with live 5m cover may trade despite an older bottom lock."""
    # Higher timeframes are background context. A fresh 1m top with a live
    # 5m reversal supersedes the older opposite lock for this entry only.
    # Later entry-location, compact-stop and risk gates still apply.
    del fifteen_direction, hour_direction
    if not (five_cover and old_bias > 0 and latest_top_time is not None):
        return False
    return (old_lock_time is None or
            pd.to_datetime(latest_top_time, utc=True) > pd.to_datetime(old_lock_time, utc=True))


def fresh_confirmed_bottom_releases_old_top(*, five_cover: bool, old_bias: int,
                                            latest_bottom_time, old_lock_time) -> bool:
    """A newer 1m bottom with live 5m cover may trade despite an older top lock."""
    if not (five_cover and old_bias < 0 and latest_bottom_time is not None):
        return False
    return (old_lock_time is None or
            pd.to_datetime(latest_bottom_time, utc=True) > pd.to_datetime(old_lock_time, utc=True))


def recent_frozen_cover_supports_fresh_turn(
        *, anchor: dict | None, one_minute: pd.DataFrame, direction: int,
        latest_turn_time, old_lock_time, maximum_age_minutes: int = 10) -> bool:
    """Let a fresh 1m stage use its already-frozen 5m cover, without 15m/1H gates."""
    if (anchor is None or direction not in {-1, 1} or latest_turn_time is None
            or one_minute is None or one_minute.empty):
        return False
    one = one_minute.sort_values("date")
    now = pd.to_datetime(one.iloc[-1]["date"], utc=True)
    cover_time = pd.to_datetime(anchor["five_bar_time"], utc=True)
    anchor_time = pd.to_datetime(anchor["one_anchor_time"], utc=True)
    turn_time = pd.to_datetime(latest_turn_time, utc=True)
    old_time = pd.to_datetime(old_lock_time, utc=True, errors="coerce")
    if (now < cover_time or now - cover_time > pd.Timedelta(minutes=maximum_age_minutes)
            or turn_time < anchor_time or turn_time > now
            or (not pd.isna(old_time) and turn_time <= old_time)):
        return False
    since_anchor = one[pd.to_datetime(one["date"], utc=True) >= anchor_time]
    if since_anchor.empty:
        return False
    stop = float(anchor["stop_reference"])
    return (float(since_anchor["low"].astype(float).min()) > stop if direction > 0
            else float(since_anchor["high"].astype(float).max()) < stop)


def latest_top_anchor_time(values) -> pd.Timestamp | None:
    """Order database strings and in-memory timestamps on one UTC timeline."""
    normalized = [pd.to_datetime(value, utc=True) for value in values if value is not None]
    return max(normalized) if normalized else None


def early_downtrend_throwback_short_allowed(*, local_high_short: bool,
                                          selected_trend_entry: bool,
                                          direction: int, durable_bias: int,
                                          five_direction: int,
                                          fifteen_direction: int,
                                          hour_direction: int) -> bool:
    """Honor a fresh 1m MA5-edge turn within an established parent decline."""
    del hour_direction  # One hour improves classification but is not an entry veto.
    return bool(local_high_short and selected_trend_entry and direction == -1
                and (fifteen_direction == -1 or
                     (five_direction == -1 and durable_bias == -1)))


def early_parent_trend_turn_allowed(*, local_turn: bool,
                                    selected_trend_entry: bool,
                                    direction: int, durable_bias: int,
                                    five_direction: int,
                                    fifteen_direction: int,
                                    hour_direction: int) -> bool:
    """Symmetric early pullback/throwback permission from the nearest parent trend."""
    del hour_direction  # One hour upgrades classification and is never a veto.
    return bool(local_turn and selected_trend_entry and direction in {-1, 1}
                and (fifteen_direction == direction or
                     (five_direction == direction and durable_bias == direction)))


def fresh_opposite_local_turn_blocks_continuation(
        *, direction: int, trend_continuation_entry: bool,
        opposite_local_turn: bool) -> bool:
    """Block a stale continuation while its fresh opposite turn is forming."""
    return bool(direction in {-1, 1} and trend_continuation_entry
                and opposite_local_turn)


def fresh_endpoint_suspends_opposite_trend(
        *, endpoint: dict | None, old_bias: int, now, one_minute: pd.DataFrame,
        maximum_age_minutes: int = 20) -> tuple[int, str]:
    """Pause stale continuation immediately while a fresh opposite endpoint matures.

    This pending takeover does not grant a trade by itself.  The new reversal
    still needs its own entry and risk gates; MA20 confirmation later promotes
    it to the durable trend lock.
    """
    if endpoint is None or old_bias not in {-1, 1} or one_minute.empty:
        return 0, "没有新鲜相反端点"
    direction = int(endpoint.get("direction", 0))
    if direction not in {-1, 1} or direction == old_bias:
        return 0, "最新端点未挑战旧趋势"
    anchor_time = pd.to_datetime(
        endpoint.get("zone_last_seen_at") or endpoint.get("confirmed_bar_time"),
        utc=True, errors="coerce")
    current_time = pd.to_datetime(now, utc=True, errors="coerce")
    if pd.isna(anchor_time) or pd.isna(current_time) or current_time < anchor_time:
        return 0, "端点时间无效"
    if current_time - anchor_time > pd.Timedelta(minutes=maximum_age_minutes):
        return 0, "相反端点已过期"
    since = one_minute.copy()
    since["date"] = pd.to_datetime(since["date"], utc=True)
    since = since[since["date"] >= anchor_time]
    if since.empty:
        return 0, "端点后行情不足"
    extreme = float(endpoint.get("extreme_price", 0.0))
    if extreme <= 0:
        return 0, "端点极值无效"
    broken = (float(since["low"].astype(float).min()) < extreme if direction > 0
              else float(since["high"].astype(float).max()) > extreme)
    if broken:
        return 0, "新端点极值已被破坏"
    label = "底部" if direction > 0 else "顶部"
    return direction, (f"新鲜一分钟{label}端点尚在MA20确认前，但极值未破；"
                       "立即暂停旧方向趋势追单，保留新反转自身准入检查")


def lower_timeframe_ma5_pullback_allowed(*, ma5_candidate: bool, direction: int,
                                         one_direction: int, five_direction: int,
                                         fresh_lock_direction: int) -> bool:
    """Reclassify an aligned 1m/5m MA5 retest after a fresh reversal lock as continuation."""
    return bool(ma5_candidate and direction in {-1, 1}
                and one_direction == direction and five_direction == direction
                and fresh_lock_direction == direction)


def higher_timeframe_boundary_evidence(moment: datetime) -> str:
    """Describe a fresh 15m/1H candle boundary; it strengthens but never vetoes."""
    local = pd.Timestamp(moment).tz_convert("Asia/Shanghai") if pd.Timestamp(moment).tzinfo else (
        pd.Timestamp(moment).tz_localize("UTC").tz_convert("Asia/Shanghai"))
    if local.minute == 0 and local.second < 120:
        return "当前处于1小时与15分钟同步换线后的前2分钟"
    if local.minute % 15 == 0 and local.second < 120:
        return "当前处于15分钟换线后的前2分钟"
    return "当前不在15分钟换线后的前2分钟"


def five_minute_live_turn_evidence(five_live: pd.DataFrame | None,
                                   five_trend_direction: int) -> tuple[int, str]:
    """Keep the developing candle colour separate from the slower trend vote."""
    if five_live is None or five_live.empty:
        return 0, "实时5分钟K线不可用；方向箭头仅代表趋势统计"
    candle = five_live.sort_values("date").iloc[-1]
    candle_direction = (1 if float(candle["close"]) > float(candle["open"])
                        else -1 if float(candle["close"]) < float(candle["open"]) else 0)
    label = "阳线" if candle_direction > 0 else "阴线" if candle_direction < 0 else "十字线"
    trend_label = "上涨" if five_trend_direction > 0 else "下降" if five_trend_direction < 0 else "中性"
    return candle_direction, (f"实时5分钟K线={label}；5分钟方向箭头={trend_label}"
                              "（已收盘趋势统计，实时转弱/转强先于箭头翻转）")


def three_candle_short_stop_evidence(one_closed: pd.DataFrame, one_live: pd.DataFrame | None,
                                     entry: float) -> tuple[float, float, str]:
    """Audit the short stop from the highest 1m real body, excluding upper wicks."""
    frame = one_closed.sort_values("date").tail(3)
    if frame.empty or entry <= 0:
        return 0.0, 0.0, "最近3根一分钟K线止损证据不足"
    edge = float(frame[["open", "close"]].astype(float).max(axis=1).max())
    if one_live is not None and not one_live.empty:
        live = one_live.sort_values("date").iloc[-1]
        edge = max(edge, float(live["open"]), float(live["close"]))
    buffer = max(latest_atr(one_closed) * .15, entry * .0003)
    stop = edge + buffer
    return edge, stop, (f"行情源最近3根1分钟K线实体上沿={edge:.2f}，缓冲={buffer:.2f}，"
                        f"实体上沿止损={stop:.2f}；上影线不作为本类小止损锚点")


def hourly_multiframe_boundary_sample(markets: dict, signals: dict,
                                      moment: datetime) -> tuple[str, int, str, dict]:
    """Build one auditable 1m endpoint versus joint 5m/15m/1H boundary sample."""
    one = markets["1m"].sort_values("date").reset_index(drop=True)
    close = one["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    endpoint_direction = 0
    if len(one) >= 21:
        top = recent_ma_fan_endpoint(one, -1, lookback=8)
        bottom = recent_ma_fan_endpoint(one, 1, lookback=8)
        endpoint_direction = -1 if top is not None else (1 if bottom is not None else 0)
    five = int(signals["5m"].direction)
    fifteen = int(signals["15m"].direction)
    hour = int(signals["1H"].direction)
    five_frame = markets["5m"].sort_values("date").reset_index(drop=True)
    five_close = five_frame["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    five_ma20_reclaimed = bool(
        len(five_frame) >= 21
        and pd.notna(five_ma20.iloc[-1]) and pd.notna(five_ma20.iloc[-2])
        and float(five_close.iloc[-1]) > float(five_ma20.iloc[-1])
        and float(five_close.iloc[-2]) <= float(five_ma20.iloc[-2])
    )


    if endpoint_direction < 0 and five < 0:
        classification = "一分钟顶部发散末端：反抽追空／局部顶部／真正顶部共用证据"
    elif endpoint_direction > 0 and five > 0:
        classification = (
            "一分钟／五分钟上涨回踩追多；15分钟／1小时局部底部反转待确认"
            if fifteen <= 0 or hour <= 0 else
            "一分钟底部发散末端：回踩追多／局部底部／真正底部共用证据"
        )
    else:
        classification = "一分钟发散末端与上级方向待配对观察"
    local = pd.Timestamp(moment)
    local = (local.tz_localize("UTC") if local.tzinfo is None else local).tz_convert("Asia/Shanghai")
    beijing_hour = local.floor("h").isoformat()
    valid_ma = len(one) >= 20 and all(pd.notna(item.iloc[-1]) for item in (ma5, ma10, ma20))
    evidence = {
        "reason": "5分钟、15分钟与1小时在北京时间整点同步换线；对照1分钟三均线发散末端",
        "observed_beijing": local.isoformat(),
        "one_price": float(close.iloc[-1]),
        "one_ma5": float(ma5.iloc[-1]) if valid_ma else None,
        "one_ma10": float(ma10.iloc[-1]) if valid_ma else None,
        "one_ma20": float(ma20.iloc[-1]) if valid_ma else None,
        "directions": {"1m_endpoint": endpoint_direction, "5m": five,
                       "15m": fifteen, "1H": hour},
        "five_minute_ma20_reclaimed": five_ma20_reclaimed,
        "five_minute_ma20": (float(five_ma20.iloc[-1])
                              if len(five_frame) >= 20 and pd.notna(five_ma20.iloc[-1])
                              else None),
    }
    return beijing_hour, endpoint_direction, classification, evidence


def bottom_reversal_ma20_gate_required(candidate: dict) -> bool:
    """Apply the dual-MA20 location gate only before a bottom has launched."""
    completed_launch_stages = {
        "price_reclaim_ma5", "price_above_flat_rising_ma5",
        "latched_stage2_resume", "small_golden_cross",
    }
    return (int(candidate["direction"]) > 0
            and candidate.get("market_shape_code") != "uptrend_continuation_long"
            and candidate.get("stage") not in completed_launch_stages)


def confirmed_five_minute_uptrend_pullback_context(
        five_minute: pd.DataFrame, latest_opposite_endpoint_time=None) -> bool:
    """Recognise a new rising 5m leg using closed candles, before entry routing.

    This is only a trend identity. A fresh 1m pullback, compact stop and the
    ordinary risk/room gates are still needed before an order can be sent.
    """
    if five_minute is None or len(five_minute) < 30:
        return False
    frame = five_minute.sort_values("date").drop_duplicates("date", keep="last")
    close = frame["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(n).mean() for n in (5, 10, 20))
    # MA5 can dip temporarily during a valid pullback.  The slower MA20 and
    # higher lows establish whether the rising structure still survives.
    if not (close.iloc[-1] > ma20.iloc[-1]
            and close.iloc[-2] > ma20.iloc[-2]
            and ma5.iloc[-1] > ma10.iloc[-1]
            and ma20.iloc[-1] > ma20.iloc[-2]
            and float(frame["low"].iloc[-1]) > float(ma20.iloc[-1])
            and float(frame["low"].astype(float).tail(3).min()) >
            float(frame["low"].astype(float).iloc[-9:-3].min())):
        return False
    if latest_opposite_endpoint_time is not None:
        endpoint = pd.to_datetime(latest_opposite_endpoint_time, utc=True)
        last_closed = pd.to_datetime(frame.iloc[-1]["date"], utc=True)
        if endpoint >= last_closed:
            return False
    return True


def bottom_reversal_below_both_ma20(
        one_minute: pd.DataFrame, five_minute: pd.DataFrame,
        anchor_time) -> tuple[bool, str]:
    """Require a bottom-reversal first entry below both 1m and 5m MA20.

    This gate belongs only to reversal-origin longs.  A separately confirmed
    uptrend pullback keeps its own MA5/MA10 location rules.
    """
    values = []
    anchor = pd.to_datetime(anchor_time, utc=True)
    for label, market in (("1m", one_minute), ("5m", five_minute)):
        if market is None or len(market) < 20:
            return False, f"{label}底部反转MA20数据不足"
        frame = market.sort_values("date").drop_duplicates("date", keep="last").copy()
        frame["ma20"] = frame["close"].astype(float).rolling(20).mean()
        rows = frame[pd.to_datetime(frame["date"], utc=True) <= anchor]
        if rows.empty or pd.isna(rows.iloc[-1]["ma20"]):
            return False, f"{label}底部反转MA20无法计算"
        row = rows.iloc[-1]
        price, ma20 = float(row["close"]), float(row["ma20"])
        values.append((label, price, ma20, price < ma20))
    allowed = all(item[3] for item in values)
    detail = "；".join(
        f"{label}收盘{price:.2f}{'低于' if below else '未低于'}MA20 {ma20:.2f}"
        for label, price, ma20, below in values)
    return allowed, detail + (
        "；底部反转首单双周期MA20位置通过"
        if allowed else "；底部反转首单暂停，等待五分钟也进入MA20下方")


def first_stage_top_short_candidate(observations: list[dict], one_minute: pd.DataFrame,
                                    *, fifteen_direction: int, hour_direction: int,
                                    five_direction: int = 0,
                                    opposite_bottom_active: bool = False) -> tuple[bool, str, float]:
    """The early 1m top probe precedes MA5 crossing and live 5m half-cover."""
    if fifteen_direction >= 0 or hour_direction >= 0 or len(one_minute) < 21:
        return False, "15分钟/1小时下跌背景尚未确认", 0.0
    if five_direction > 0:
        return False, "5分钟已收盘方向仍在转强，顶部第一阶段等待5分钟重新转弱", 0.0
    if opposite_bottom_active:
        return False, "更新的5分钟底部半覆盖做多事件已生效，旧顶部第一阶段空单失效", 0.0
    frame = one_minute.sort_values("date").reset_index(drop=True)
    latest = frame.iloc[-1]
    price = float(latest["close"])
    if price >= float(latest["open"]):
        return False, "第一阶段等待新鲜阴线转弱", 0.0
    atr1 = latest_atr(frame)
    ma5_series = frame["close"].astype(float).rolling(5).mean()
    ma5 = float(ma5_series.iloc[-1])
    ma5_slope = ma5 - float(ma5_series.iloc[-2])
    if price > ma5:
        return False, "第一阶段阴线仍在MA5上方，尚未形成可执行转弱", 0.0
    if ma5_slope > 0:
        return False, "第一阶段MA5仍向上，等待一分钟末端真正转弱", 0.0
    if atr1 <= 0 or price < ma5 - .25 * atr1:
        return False, "第一阶段已离开MA5外沿，禁止迟到追空", 0.0
    anchors = [item for item in observations
               if int(item.get("direction", 0)) < 0
               and item.get("stage") in {"top_sweep_reject", "confirmed_local_top",
                                          "relative_local_top_sweep", "top_half_bullish_cover"}
               and staged_launch_is_fresh(item.get("time"), latest["date"])[0]]
    if not anchors:
        return False, "第一阶段没有新鲜一分钟局部顶部/扫顶锚点", 0.0
    local_high = float(frame.tail(3)[["open", "close"]].astype(float).max(axis=1).max())
    stop = local_high + max(.15 * atr1, price * .0003)
    return True, ("局部顶部反转第一阶段：新鲜1分钟局部高点后阴线在MA5外沿转弱；"
                  "止损取最近3根一分钟实体上沿外加缓冲，不取上影线最高点；"
                  "15分钟与1小时下降趋势反抽背景一致；不等待小死叉或5分钟半覆盖"), stop


def durable_regime_continuation_addon_allowed(
        *, core_reversal_open: bool, early_dual_entry: bool,
        durable_bias: int, direction: int, position_layers: float,
        pending_open_order: bool) -> bool:
    """Permit no more than three layers while the durable reversal regime persists."""
    regime_owns_direction = durable_bias in {-1, 1} and durable_bias == direction
    return bool((core_reversal_open or early_dual_entry or regime_owns_direction)
                and 0 < position_layers < 3 and not pending_open_order)


def repeated_local_reversal_addon_allowed(
        *, local_reversal_entry: bool, durable_bias: int, direction: int,
        position_layers: float, pending_open_order: bool) -> bool:
    """Allow a fresh local reversal shape to become layer two or three.

    A closed earlier trial is not a conflict at all.  While exposure remains,
    only a new 5m-owned local reversal in the established direction may add,
    and the normal three-layer and pending-order guards still apply.
    """
    return bool(
        local_reversal_entry
        and durable_bias in {-1, 1}
        and durable_bias == direction
        and 0 < position_layers < 3
        and not pending_open_order
    )


def confirmed_uptrend_pullback_uses_local_runway(
        *, direction: int, locked_trend_pullback: bool,
        fresh_rising_pullback: bool) -> bool:
    """Use the fresh MA-edge pullback instead of a lagging 5m-MA20 distance.

    In a sustained rally the five-minute MA20 can remain several ATR behind
    price.  Once a long has a durable bullish lock and a freshly validated
    1m MA5/MA10 pullback, that lag is not evidence of a late chase.  The
    local stop, nearby resistance and post-confirmation chase gates remain.
    """
    return bool(direction > 0 and (
        locked_trend_pullback or fresh_rising_pullback))


def specialised_top_short_bypasses_observe_only_gate(
        direction: int, *, five_high_cover: bool = False,
        expanded_ma_top: bool = False, top_weakening: bool = False,
        compact_top: bool = False, intrabar_engulf: bool = False,
        intrabar_range: bool = False, early_high_sweep: bool = False) -> bool:
    """Keep a qualified top-short branch executable before its primary gate."""
    return bool(direction < 0 and any((
        five_high_cover, expanded_ma_top, top_weakening, compact_top,
        intrabar_engulf, intrabar_range, early_high_sweep,
    )))


def medium_trend_five_ma5_hold_eligible(
        *, durable_bias: int, direction: int,
        confirmed_endpoint_reversal_entry: bool) -> bool:
    """Reserve the 5m MA5 exit for the actual dual-timeframe endpoint entry."""
    return bool(durable_bias in {-1, 1} and durable_bias == direction
                and confirmed_endpoint_reversal_entry)


def sweep_trial_endpoint_confirmed(one_minute: pd.DataFrame,
                                   five_minute: pd.DataFrame,
                                   direction: int) -> bool:
    """Promote an early sweep probe only after both 1m and 5m clear MA10/MA20."""
    if direction not in {-1, 1}:
        return False
    for market in (one_minute, five_minute):
        frame = market.sort_values("date")
        if len(frame) < 20:
            return False
        close = frame["close"].astype(float)
        latest = float(close.iloc[-1])
        ma10 = float(close.rolling(10).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        if direction > 0 and not (latest > ma10 and latest > ma20):
            return False
        if direction < 0 and not (latest < ma10 and latest < ma20):
            return False
    return True


def persisted_dual_endpoint_promotes_sweep_trial(
        patterns, reference_time, direction: int, branch: str,
        signal_context: dict | None = None) -> tuple[bool, str]:
    """Promote only the original endpoint probe after a durable 1m+5m record.

    A price snapshot above/below MA10 and MA20 is not durable confirmation: it
    can arrive late, disappear during a normal pullback, or occur without an
    endpoint reversal. The review-list record is the source of truth. Trend
    continuation add-ons are deliberately excluded and remain 1m-MA5 trades.
    """
    context = signal_context or {}
    eligible_branches = {
        "early_one_minute_frozen_stage_launch",
        "frozen_one_minute_big_cross_launch",
        "early_low_sweep_reclaim",
        "early_high_sweep_reject",
    }
    if direction not in {-1, 1}:
        return False, "invalid trial direction"
    if bool(context.get("core_reversal_continuation_addon")):
        return False, "continuation add-on remains managed by 1m MA5"
    if branch not in eligible_branches:
        return False, "entry is not an endpoint trial branch"
    durable_bias, durable_reason = durable_dual_reversal_zone_bias(
        patterns, reference_time)
    if durable_bias != direction:
        return False, durable_reason
    return True, durable_reason


def select_ma5_exit_lifecycle(rows) -> tuple[object | None, bool]:
    """Exit local add-on layers first; leave the 5m-MA5 core until last."""
    classified: list[tuple[object, bool]] = []
    for row in rows:
        context = json.loads(str(row["signal_context_json"] or "{}"))
        classified.append((row, bool(context.get("five_minute_ma5_core_hold"))))
    local_layers = [item for item in classified if not item[1]]
    if local_layers:
        return local_layers[-1][0], False
    core_layers = [item for item in classified if item[1]]
    if core_layers:
        return core_layers[0][0], True
    return None, False


def opposite_reversal_exit_confirmation(
    held_direction: int, opposite_rows: list, trend_lock: dict | None,
    held_entry_time: str | None = None, held_branch: str = "",
) -> str:
    """Return why an opposite reversal may close the old trend layers.

    A live half-cover candidate is deliberately insufficient.  The old side
    is released only after the opposite trial order has been accepted, or
    after a missed trial subsequently becomes a confirmed 1m reversal lock.
    """
    opposite_direction = -int(held_direction)
    expected_branch = (
        "five_minute_bottom_local_reversal_half_cover_long"
        if opposite_direction > 0 else
        "five_minute_top_local_reversal_half_cover_short"
    )
    paired_branch = (
        "one_minute_ma_fan_endpoint_five_minute_half_cover_long"
        if opposite_direction > 0 else
        "one_minute_ma_fan_endpoint_five_minute_half_cover_short"
    )
    if any(
        int(row["direction"]) == opposite_direction
        and str(row["branch"]) in {expected_branch, paired_branch}
        and str(row["status"]) in {"submitted", "open"}
        for row in opposite_rows
    ):
        return "opposite_reversal_trial_order_accepted"
    # The five-second entry deliberately reacts before the slow strategy has
    # finished auditing every candidate.  A later "missed" opposite candidate
    # must therefore not close it: no opposite order was accepted, and the
    # persisted lock can be created from stale half-cover evidence even when
    # its own audit says 1m_turn=0/location=0.  This branch exits on its formal
    # 1m-MA5 turn, or on a real accepted opposite trial above.
    if held_branch == "five_second_top_weakening_short":
        return ""
    if trend_lock is None or int(trend_lock.get("direction", 0)) != opposite_direction:
        return ""
    confirmed_at = pd.to_datetime(trend_lock.get("confirmed_at"), utc=True, errors="coerce")
    held_at = pd.to_datetime(held_entry_time, utc=True, errors="coerce")
    if pd.isna(confirmed_at) or (not pd.isna(held_at) and confirmed_at < held_at):
        return ""
    return "opposite_reversal_shape_confirmed_after_missed_entry"


def fresh_directional_half_cover(
    closed: pd.DataFrame, live: pd.DataFrame | None, direction: int,
    *, minimum_cover: float = 0.45, cluster_size: int = 3,
) -> tuple[bool, str]:
    """Recognize a fresh ordered turn, measuring actual body-price overlap.

    The window locates the opposite impulse and subsequent directional turn;
    unrelated bodies must never be added into a synthetic 282% candle.
    """
    if direction not in {-1, 1} or closed is None or len(closed) < 1:
        return False, "direction or candle history is unavailable"
    completed = closed.sort_values("date").copy()
    if live is not None and not live.empty:
        current = live.sort_values("date").iloc[-1]
        current_time = pd.to_datetime(current["date"], utc=True)
        history = completed.loc[
            pd.to_datetime(completed["date"], utc=True) < current_time]
        if history.empty:
            return False, "no completed candle precedes the live candle"
        zone = pd.concat([history.tail(max(1, cluster_size - 1)),
                          pd.DataFrame([current])], ignore_index=True)
    elif len(completed) >= 2:
        zone = completed.tail(cluster_size)
    else:
        return False, "at least two candles are required"
    zone = zone.reset_index(drop=True)
    signed = zone["close"].astype(float) - zone["open"].astype(float)
    opposite_indexes = [i for i in range(len(zone) - 1)
                        if direction * float(signed.iloc[i]) < 0]
    anchor_index = opposite_indexes[-1] if opposite_indexes else None
    ratio = 0.0
    ordered = anchor_index is not None and direction * float(signed.iloc[-1]) > 0
    if ordered:
        anchor = zone.iloc[anchor_index]
        following = zone.iloc[anchor_index + 1:]
        # The recovery segment must itself point in the new direction.  Use
        # the latest body close against the opposite body's actual price area.
        endpoint = float(following.iloc[-1]["close"])
        start = float(anchor["close"])
        opposing = abs(float(anchor["close"]) - float(anchor["open"]))
        progress = direction * (endpoint - start)
        ratio = min(1.0, max(0.0, progress / opposing)) if opposing > 0 else 0.0
    allowed = bool(ordered and ratio >= minimum_cover)
    side = "bullish" if direction > 0 else "bearish"
    return allowed, (
        f"fresh up-to-{cluster_size}-candle {side} turn overlaps the last opposite body by {ratio:.0%}"
        if allowed else
        f"waiting for fresh up-to-{cluster_size}-candle {side} turn and last-opposite-body overlap (now {ratio:.0%})"
    )


def recovery_has_fresh_entry_location(one: pd.DataFrame, direction: int,
                                      *, lookback: int = 12) -> tuple[bool, str]:
    """Prevent an old top/bottom anchor from reopening at the opposite edge."""
    frame = one.sort_values("date").tail(lookback)
    if len(frame) < 5:
        return False, "recovery location needs five current 1m candles"
    body_high = float(frame[["open", "close"]].astype(float).max(axis=1).max())
    body_low = float(frame[["open", "close"]].astype(float).min(axis=1).min())
    price = float(frame.iloc[-1]["close"])
    position = (price - body_low) / (body_high - body_low) if body_high > body_low else .5
    allowed = position >= .5 if direction < 0 else position <= .5
    return allowed, (f"current 1m body-range position={position:.0%}; "
                     f"{'short requires upper half' if direction < 0 else 'long requires lower half'}")


def fresh_single_candle_half_cover(
    closed: pd.DataFrame, live: pd.DataFrame | None, direction: int,
    *, minimum_cover: float = 0.45,
) -> tuple[bool, str]:
    """Fast path: the current candle covers 45% of its adjacent opposite bar."""
    if direction not in {-1, 1} or closed is None or closed.empty:
        return False, "direction or candle history is unavailable"
    history = closed.sort_values("date")
    if live is not None and not live.empty:
        current = live.sort_values("date").iloc[-1]
        prior_rows = history[pd.to_datetime(history["date"], utc=True)
                             < pd.to_datetime(current["date"], utc=True)]
        if prior_rows.empty:
            return False, "no completed candle precedes the live candle"
        prior = prior_rows.iloc[-1]
    elif len(history) >= 2:
        prior, current = history.iloc[-2], history.iloc[-1]
    else:
        return False, "two adjacent candles are required"
    prior_body = float(prior["close"]) - float(prior["open"])
    current_body = float(current["close"]) - float(current["open"])
    opposing = -direction * prior_body
    recovery = direction * current_body
    overlap = max(0.0, min(float(prior["open"]), float(current["close"]))
                  - max(float(prior["close"]), float(current["open"]))) if direction > 0 else max(
                      0.0, min(float(prior["close"]), float(current["open"]))
                      - max(float(prior["open"]), float(current["close"])))
    ratio = overlap / opposing if opposing > 0 else 0.0
    allowed = opposing > 0 and recovery > 0 and ratio >= minimum_cover
    return bool(allowed), (
        f"current candle covers the adjacent opposite body by {ratio:.0%}"
        if allowed else f"current candle adjacent-body cover is {ratio:.0%}"
    )


def directional_entry_half_cover_gate(
    one_minute: pd.DataFrame, one_minute_live: pd.DataFrame | None,
    five_minute: pd.DataFrame, five_minute_live: pd.DataFrame | None,
    direction: int, *, trend_continuation: bool,
    frozen_cluster_cover: dict | None = None,
) -> tuple[bool, str]:
    """Require 5m adjacent 45% cover only for reversal identities."""
    if trend_continuation:
        side = "反抽追空" if direction < 0 else "回踩追多"
        return True, f"{side}按趋势结构独立准入，不要求5分钟相邻实体覆盖45%"
    if direction < 0 and frozen_cluster_cover is not None:
        return True, (
            "新顶部独立执行链已冻结最多3根5分钟K线累计45%空头覆盖；"
            f"冻结周期={frozen_cluster_cover['five_bar_time']}，"
            "当前相邻K线不必重复制造45%覆盖"
        )
    five_fast, five_fast_reason = fresh_single_candle_half_cover(
        five_minute, five_minute_live, direction)
    # Local and true reversals share this confirmation gate. Trend pullbacks
    # are admitted above by their own higher-timeframe and MA-edge structure.
    side = "bearish" if direction < 0 else "bullish"
    return five_fast, f"mandatory live 5m {side} adjacent cover: {five_fast_reason}"


def latest_local_reversal_extreme(one_minute: pd.DataFrame, direction: int) -> float:
    """Six candles locate evidence; a newer confirmed micro-pivot owns the stop.

    A pivot needs a candle on each side. Include every subsequent wick so this
    never moves protection inside the active structure. Without a newer pivot,
    retain the six-candle extreme rather than inventing a tighter stop.
    """
    if direction not in (-1, 1):
        raise ValueError("invalid reversal direction")
    frame = one_minute.sort_values("date").copy()
    frame["local_stop_ma5"] = frame["close"].astype(float).rolling(5).mean()
    recent = frame.tail(6)
    bodies = recent[["open", "close"]].astype(float) if "open" in recent else recent[["close"]].astype(float)
    values = (bodies.max(axis=1) if direction < 0 else bodies.min(axis=1)).tolist()
    ma5 = recent["local_stop_ma5"].tolist()
    if not values:
        raise ValueError("missing reversal candles")
    for index in range(len(values) - 2, 0, -1):
        if (direction * (values[index] - values[index - 1]) < 0
                and direction * (values[index] - values[index + 1]) < 0
                and direction * (values[index] - ma5[index]) < 0):
            return max(values[index:]) if direction < 0 else min(values[index:])
    return max(values) if direction < 0 else min(values)


def freeze_live_five_minute_cover_anchor(
    store: StateStore, instrument: str, one_minute: pd.DataFrame,
    five_minute: pd.DataFrame, direction: int,
) -> dict | None:
    """Freeze the first compact 1m extreme as soon as live 5m reaches 45%."""
    allowed, cover_evidence = fresh_single_candle_half_cover(five_minute, None, direction)
    if not allowed or one_minute is None or one_minute.empty:
        return None
    one = one_minute.sort_values("date").reset_index(drop=True)
    five = five_minute.sort_values("date").reset_index(drop=True)
    atr1 = latest_atr(one)
    if atr1 <= 0:
        return None
    latest_price = float(one.iloc[-1]["close"])
    buffer = max(atr1 * .15, latest_price * .0003)
    extreme = latest_local_reversal_extreme(one, direction)
    stop = extreme + buffer if direction < 0 else extreme - buffer
    five_bar_time = pd.Timestamp(five.iloc[-1]["date"]).isoformat()
    one_anchor_time = pd.Timestamp(one.iloc[-1]["date"]).isoformat()
    row = store.freeze_directional_cover_anchor(
        instrument=instrument, direction=direction, five_bar_time=five_bar_time,
        one_anchor_time=one_anchor_time, stop_reference=stop,
        cover_evidence=cover_evidence)
    return dict(row)


def freeze_fresh_top_cluster_cover_anchor(
    store: StateStore, instrument: str, one_minute: pd.DataFrame,
    five_minute: pd.DataFrame, top_anchor_time,
    *, maximum_anchor_age_minutes: int = 6,
) -> tuple[dict | None, str]:
    """Freeze a new top independently when the 5m reversal unfolds in stages.

    A small first bearish 5m body followed by a larger bearish body is one
    reversal event even though the final candle is adjacent to another red
    candle.  Tie the cumulative cover to the newest 1m top, keep its compact
    high stop, and never reuse an older top/cover pair.
    """
    if (top_anchor_time is None or one_minute is None or one_minute.empty
            or five_minute is None or five_minute.empty):
        return None, "fresh top or candle history unavailable"
    one = one_minute.sort_values("date").reset_index(drop=True)
    five = five_minute.sort_values("date").reset_index(drop=True)
    now = pd.to_datetime(one.iloc[-1]["date"], utc=True)
    anchor_time = pd.to_datetime(top_anchor_time, utc=True, errors="coerce")
    if pd.isna(anchor_time):
        return None, "fresh top time is invalid"
    age = now - anchor_time
    if age < pd.Timedelta(0) or age > pd.Timedelta(minutes=maximum_anchor_age_minutes):
        return None, "fresh top is outside the independent 5m-cover window"
    allowed, evidence = fresh_directional_half_cover(
        five, None, -1, minimum_cover=.45, cluster_size=3)
    if not allowed:
        return None, evidence
    since_top = one[pd.to_datetime(one["date"], utc=True) >= anchor_time]
    if since_top.empty:
        return None, "no 1m candle belongs to the fresh top"
    atr1 = latest_atr(one)
    if atr1 <= 0:
        return None, "1m ATR unavailable"
    top_high = float(since_top["high"].astype(float).max())
    latest_price = float(one.iloc[-1]["close"])
    buffer = max(atr1 * .15, latest_price * .0003)
    stop = top_high + buffer
    if latest_price >= stop:
        return None, "fresh top has already been invalidated"
    five_bar_time = pd.Timestamp(five.iloc[-1]["date"]).isoformat()
    existing = store.directional_cover_anchor(
        instrument=instrument, direction=-1, five_bar_time=five_bar_time)
    if existing is not None:
        return dict(existing), evidence
    row = store.freeze_directional_cover_anchor(
        instrument=instrument, direction=-1, five_bar_time=five_bar_time,
        one_anchor_time=anchor_time.isoformat(), stop_reference=stop,
        cover_evidence=f"fresh-top independent chain: {evidence}")
    frozen = dict(row)
    store.record_event("fresh_top_independent_cover_frozen", {
        "strategy_version": VALIDATION_VERSION,
        "instrument": instrument,
        "top_anchor_time": anchor_time.isoformat(),
        "five_bar_time": five_bar_time,
        "stop": stop,
        "reason": evidence,
    })
    return frozen, evidence


def persisted_directional_cover_recovery(
    store: StateStore, instrument: str, one_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame, direction: int, *, maximum_age_minutes: int = 15,
) -> tuple[bool, str, dict | None, str, pd.Timestamp | None, float]:
    """Recover a missed first-45% reversal without reviving a stale signal.

    The live 5m adjacent-body cover is still the only event that creates the
    anchor.  This recovery path merely consumes that persisted event after a
    reconnect or a missed order, for at most three 5m bars, while its compact
    1m stop remains intact.  A fresh 1m turn and a same-direction 15m colour
    turn are required.  The returned MA20 state is also usable to lock the new
    trend even when the reversal order itself was missed.
    """
    if direction not in {-1, 1} or one_minute is None or one_minute.empty:
        return False, "recovery direction or 1m history unavailable", None, "waiting", None, 0.0
    row = store.latest_directional_cover_anchor(
        instrument=instrument, direction=direction)
    if row is None:
        return False, "no persisted first-45% 5m cover anchor", None, "waiting", None, 0.0
    anchor = dict(row)
    one = one_minute.sort_values("date").reset_index(drop=True)
    latest_time = pd.to_datetime(one.iloc[-1]["date"], utc=True)
    cover_time = pd.to_datetime(anchor["five_bar_time"], utc=True)
    anchor_time = pd.to_datetime(anchor["one_anchor_time"], utc=True)
    age = latest_time - cover_time
    if age < pd.Timedelta(0) or age > pd.Timedelta(minutes=maximum_age_minutes):
        return False, (
            f"persisted first-45% cover is outside the {maximum_age_minutes}-minute recovery window"
        ), anchor, "expired", latest_time, 0.0
    already_trialed = store.has_reversal_trial_since(
        instrument, direction, str(anchor["one_anchor_time"]))
    since_anchor = one[pd.to_datetime(one["date"], utc=True) >= anchor_time]
    if since_anchor.empty:
        return False, "1m candles after the frozen cover anchor are unavailable", anchor, "waiting", latest_time, 0.0
    stop_reference = float(anchor["stop_reference"])
    anchor_broken = (
        float(since_anchor["low"].astype(float).min()) <= stop_reference
        if direction > 0 else
        float(since_anchor["high"].astype(float).max()) >= stop_reference
    )
    raw_extreme = (
        float(since_anchor["low"].astype(float).min())
        if direction > 0 else
        float(since_anchor["high"].astype(float).max())
    )
    if anchor_broken:
        return False, "frozen compact 1m stop was broken; old cover cannot be revived", anchor, "failed", latest_time, raw_extreme
    if len(one) < 20 or fifteen_minute is None or len(fifteen_minute) < 2:
        return False, "recovery histories are insufficient", anchor, "waiting", latest_time, raw_extreme
    close = one["close"].astype(float)
    ma5 = close.rolling(5).mean()
    one_turn = bool(
        direction * (float(close.iloc[-1]) - float(ma5.iloc[-1])) > 0
        and direction * (float(ma5.iloc[-1]) - float(ma5.iloc[-2])) > 0
    )
    fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
    latest_fifteen_body = float(fifteen.iloc[-1]["close"]) - float(fifteen.iloc[-1]["open"])
    fifteen_turn = direction * latest_fifteen_body > 0
    prior_opposite = any(
        direction * (float(item["close"]) - float(item["open"])) < 0
        for _, item in fifteen.iloc[-4:-1].iterrows()
    )
    cluster_ok, cluster_reason = fresh_directional_half_cover(
        one, None, direction, cluster_size=6)
    location_ok, location_reason = recovery_has_fresh_entry_location(one, direction)
    lock_state, lock_reason, lock_time = one_minute_reversal_trend_lock_state(
        one, direction, anchor_time.isoformat(), raw_extreme)
    allowed = bool(
        not already_trialed
        and one_turn and fifteen_turn and prior_opposite
        and (cluster_ok or lock_state == "confirmed") and location_ok
    )
    side = "bottom-long" if direction > 0 else "top-short"
    reason = (
        f"persisted first-45% {side} recovery: compact stop remains intact; "
        f"1m has turned on the MA5 side; {cluster_reason}; 15m colour turned "
        f"after the opposite move; {location_reason}; MA20 state={lock_state} ({lock_reason})"
        if allowed else
        f"waiting on persisted first-45% {side} recovery: already_trialed={int(already_trialed)}, "
        f"1m_turn={int(one_turn)}, "
        f"15m_turn={int(fifteen_turn and prior_opposite)}, cluster={int(cluster_ok)}, "
        f"location={int(location_ok)} ({location_reason}), "
        f"MA20_state={lock_state}"
    )
    return allowed, reason, anchor, lock_state, lock_time, raw_extreme


def three_timeframe_reversal_confirmation(
    one_minute: pd.DataFrame, one_live: pd.DataFrame | None,
    five_minute: pd.DataFrame, five_live: pd.DataFrame | None,
    fifteen_minute: pd.DataFrame, fifteen_live: pd.DataFrame | None,
) -> tuple[int, str, float, str]:
    """Independent 1m+5m+15m reversal, with fast and recovery paths.

    Fast entry uses one live 5m adjacent-body cover.  The 6x1m/3x5m clusters
    are only a reconnect/backfill path and therefore never delay the fast
    signal.  The 15m candle only has to turn in the new direction after recent
    opposite candles; it does not have to half-cover them.
    """
    if any(frame is None or frame.empty for frame in
           (one_minute, five_minute, fifteen_minute)):
        return 0, "three-timeframe reversal data unavailable", 0.0, ""
    one = merge_closed_and_live_market(one_minute, one_live).sort_values("date")
    five = merge_closed_and_live_market(five_minute, five_live).sort_values("date")
    fifteen = merge_closed_and_live_market(fifteen_minute, fifteen_live).sort_values("date")
    if len(one) < 20 or len(five) < 2 or len(fifteen) < 2:
        return 0, "three-timeframe reversal history is insufficient", 0.0, ""
    one_close = one["close"].astype(float)
    ma5 = float(one_close.tail(5).mean())
    previous = one_close.shift(1)
    true_range = pd.concat([
        one["high"].astype(float) - one["low"].astype(float),
        (one["high"].astype(float) - previous).abs(),
        (one["low"].astype(float) - previous).abs(),
    ], axis=1).max(axis=1)
    atr1 = float(true_range.tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0:
        return 0, "three-timeframe reversal ATR unavailable", 0.0, ""
    for direction in (1, -1):
        one_side = direction * (float(one.iloc[-1]["close"]) - ma5) > 0
        fifteen_turn = direction * (float(fifteen.iloc[-1]["close"])
                                    - float(fifteen.iloc[-1]["open"])) > 0
        prior_opposite = any(
            direction * (float(row["close"]) - float(row["open"])) < 0
            for _, row in fifteen.iloc[-4:-1].iterrows())
        fast5, fast5_reason = fresh_single_candle_half_cover(
            five_minute, five_live, direction)
        recovery1, recovery1_reason = fresh_directional_half_cover(
            one_minute, one_live, direction, cluster_size=6)
        recovery5, recovery5_reason = fresh_directional_half_cover(
            five_minute, five_live, direction, cluster_size=3)
        location_ok, _ = recovery_has_fresh_entry_location(one, direction)
        mode = "fast" if fast5 else "recovery" if recovery1 and recovery5 and location_ok else ""
        if one_side and fifteen_turn and prior_opposite and mode:
            recent = one.tail(4)
            buffer = max(atr1 * .12, float(one.iloc[-1]["close"]) * .0003)
            stop = (float(recent["low"].astype(float).min()) - buffer
                    if direction > 0 else
                    float(recent["high"].astype(float).max()) + buffer)
            side = "long" if direction > 0 else "short"
            evidence = (fast5_reason if mode == "fast" else
                        f"1m {recovery1_reason}; 5m {recovery5_reason}")
            return direction, (
                f"independent three-timeframe {mode} bottom/top reversal {side}: "
                f"1m is on the valid MA5 side; {evidence}; "
                "15m has turned after the opposite decline/rise without a half-cover requirement"
            ), stop, mode
    return 0, "waiting for 1m MA5 side, 5m 45% cover and 15m colour turn", 0.0, ""


def fresh_reversal_execution_location(
        *, direction: int, signal_price: float, execution_price: float,
        atr1: float, signal_time, execution_time,
        maximum_move_atr: float = .35, maximum_age_seconds: float = 180.0,
) -> tuple[bool, str]:
    """Keep a reversal entry at its turn instead of chasing the later impulse."""
    if direction not in {-1, 1} or min(signal_price, execution_price, atr1) <= 0:
        return False, "反转实际提交位置数据不足"
    age = (pd.to_datetime(execution_time, utc=True)
           - pd.to_datetime(signal_time, utc=True)).total_seconds()
    move = direction * (execution_price - signal_price)
    move_atr = move / atr1
    detail = (f"信号价{signal_price:.2f}，核验价{execution_price:.2f}，"
              f"沿做单方向已运行{max(0.0, move):.2f}点/{max(0.0, move_atr):.2f} ATR，"
              f"信号年龄{max(0.0, age):.0f}秒")
    if move_atr > maximum_move_atr:
        return False, f"{detail}；超过{maximum_move_atr:.2f} ATR，禁止迟到追单"
    if age < 0 or age > maximum_age_seconds:
        return False, f"{detail}；超过{maximum_age_seconds:.0f}秒确认窗口，禁止迟到追单"
    return True, f"{detail}；仍在首次反转成交窗口"


def lifecycle_pair_is_fresh_for_promotion(lifecycle, pair, *, maximum_minutes: int = 10) -> bool:
    """Do not relabel a later continuation trade with an old endpoint pair."""
    if pair is None:
        return False
    signal = pd.to_datetime(lifecycle["signal_time"], utc=True)
    pair_time = pd.to_datetime(pair["confirmed_bar_time"], utc=True)
    return pd.Timedelta(0) <= signal - pair_time <= pd.Timedelta(minutes=maximum_minutes)


def five_minute_ma20_trend_takeover(
    five_minute: pd.DataFrame, five_live: pd.DataFrame | None = None,
) -> tuple[int, str, pd.Timestamp | None]:
    """A completed/live 5m close crossing MA20 confirms the prior 1m turn.

    The cross is a trend-lock event, not another entry condition.  Once stored,
    the established direction may own two later fresh pullback add-ons (three
    total layers including the reversal seed).
    """
    frame = merge_closed_and_live_market(five_minute, five_live).sort_values("date")
    if len(frame) < 21:
        return 0, "five-minute MA20 takeover needs 21 candles", None
    close = frame["close"].astype(float)
    ma20 = close.rolling(20).mean()
    for direction in (1, -1):
        crossed = (direction * (float(close.iloc[-2]) - float(ma20.iloc[-2])) <= 0
                   and direction * (float(close.iloc[-1]) - float(ma20.iloc[-1])) > 0)
        if crossed:
            return direction, (
                f"5m close crossed {'above' if direction > 0 else 'below'} MA20; "
                "confirm the existing 1m bottom/top and lock the new trend"
            ), pd.Timestamp(frame.iloc[-1]["date"])
    return 0, "five-minute close has not freshly crossed MA20", None


def promote_newly_paired_local_trials(
        store: StateStore, client: OkxDemoClient, instrument: str,
        direction: int) -> tuple[str, ...]:
    """Upgrade local trial lifecycles in the same scan that locks a true pair."""
    pair = store.latest_confirmed_one_five_pair(
        instrument, direction, require_ma5_cross=True)
    if pair is None:
        return ()
    promoted: list[str] = []
    for lifecycle in store.open_trade_lifecycles(VALIDATION_VERSION):
        if (int(lifecycle["direction"]) != int(direction)
                or str(lifecycle["instrument"]) != instrument):
            continue
        context = json.loads(str(lifecycle["signal_context_json"] or "{}"))
        if bool(context.get("sweep_trial_promoted")):
            continue
        if not lifecycle_pair_is_fresh_for_promotion(lifecycle, pair):
            continue
        algo_id = str(lifecycle["algo_id"] or "").strip()
        if algo_id:
            client.cancel_algo_orders([{"instId": instrument, "algoId": algo_id}])
        context.update({
            "position_class": "ma_spread_endpoint_reversal",
            "ma5_exit_timeframe": "5m",
            "five_minute_ma5_core_hold": True,
            "sweep_trial_promoted": True,
            "sweep_trial_promotion_source": "paired_1m_5m_ma_endpoint_lineage",
            "sweep_trial_promotion_reason": (
                "局部顶底试单已升级：5分钟反向实体半覆盖并站到MA20反转侧，"
                "一分钟末端收盘穿过MA5，1分钟+5分钟真正反转区锁定"),
        })
        upgraded_identity = classify_entry_category(
            direction, true_endpoint_reversal=True)
        context.update({
            "entry_classification_code": upgraded_identity["code"],
            "entry_classification_category": upgraded_identity["category"],
            "entry_classification_label": upgraded_identity["label"],
            "entry_classification_rule": upgraded_identity["rule"],
            "entry_trend_source_timeframe": upgraded_identity["trend_source_timeframe"],
        })
        trade_uid = str(lifecycle["trade_uid"])
        store.update_trade_lifecycle_context(trade_uid, context)
        if algo_id:
            store.clear_trade_trailing_algo(trade_uid)
        store.record_event("sweep_trial_endpoint_promoted", {
            "strategy_version": VALIDATION_VERSION,
            "trade_uid": trade_uid, "direction": int(direction),
            "exit_timeframe": "5m",
            "reason": context["sweep_trial_promotion_reason"],
        })
        promoted.append(trade_uid)
    if promoted:
        store.mark_ma_endpoint_trade_upgrades([str(pair["event_key"])], promoted)
    return tuple(promoted)


def early_dual_timeframe_pullback_cover_long_setup(
        one_minute: pd.DataFrame, five_minute: pd.DataFrame) -> tuple[bool, str, float]:
    """Recognise a three-bar recovery below the MA stack before MA5 is crossed."""
    one = one_minute.sort_values("date").tail(24).copy()
    five = five_minute.sort_values("date").tail(24).copy()
    if len(one) < 20 or len(five) < 20:
        return False, "双周期回踩数据不足", 0.0
    for frame in (one, five):
        close = frame["close"].astype(float)
        frame["_ma5"] = close.rolling(5).mean()
        frame["_ma10"] = close.rolling(10).mean()
        frame["_ma20"] = close.rolling(20).mean()
    one_tail = one.tail(4)
    one_below = bool((one_tail["low"].astype(float)
                      < one_tail[["_ma5", "_ma10"]].min(axis=1)).any())
    latest = one.iloc[-1]
    before_ma5 = float(latest["close"]) <= float(latest["_ma5"])
    bullish = float(latest["close"]) > float(latest["open"])
    cluster = one.tail(4)
    low = float(cluster["low"].min())
    atr1 = latest_atr(one_minute)
    turns_up = (float(latest["close"]) > float(cluster.iloc[-2]["close"])
                and float(latest["close"] - latest["open"]) >= atr1 * .10)
    if not (one_below and before_ma5 and bullish and turns_up):
        return False, "等待MA5/MA10外沿形成止跌转强；不要求单根覆盖前阴线50%", 0.0
        return False, "尚未形成双周期均线下方回踩及三线组合阳线半覆盖", 0.0
    buffer = max(latest_atr(one_minute) * .12, float(latest["close"]) * .0002)
    stop = round(low - buffer, 2)
    return True, (
        "一分钟回踩已到MA5/MA10外沿下方并形成止跌转强；不要求单根覆盖50%，"
        "也不限制MA20所在一侧，在上穿MA5前提前追多"), stop
    return True, (
        "1分钟与5分钟回踩低点同在MA5/MA10/MA20下方；约三根组合已由阳线收回至少一半，"
        "在上穿MA5前提前追多"), stop


def reversal_stop_outside_recent_structure(one_minute: pd.DataFrame, entry: float,
                                           direction: int, candidate_stop: float,
                                           *, lookback: int = 12,
                                           maximum_points: float = 3.0,
                                           atr_buffer: float = .15,
                                           price_buffer: float = .0002,
                                           ) -> tuple[bool, float, str]:
    """Put a probe stop beyond the recent swing, or reject instead of squeezing it."""
    frame = one_minute.sort_values("date").tail(lookback)
    if direction not in {-1, 1} or entry <= 0 or len(frame) < lookback:
        return False, candidate_stop, "反转结构止损数据不足"
    buffer = max(latest_atr(frame) * atr_buffer, entry * price_buffer)
    if direction < 0:
        structural_edge = float(frame[["open", "close"]].astype(float).max(axis=1).max()) + buffer
        stop = structural_edge
        risk = stop - entry
    else:
        structural_edge = float(frame[["open", "close"]].astype(float).min(axis=1).min()) - buffer
        stop = structural_edge
        risk = entry - stop
    if risk <= 0:
        return False, stop, "反转结构止损方向无效"
    if risk > maximum_points:
        return False, stop, (f"最近{lookback}根结构外止损需要{risk:.2f}点，超过"
                             f"{maximum_points:.2f}点最大风险；放弃入场，不把止损压回结构内部")
    return True, round(stop, 2), (f"止损放在最近{lookback}根一分钟K线实体边界外，"
                                  f"含{buffer:.2f}点波动缓冲，风险{risk:.2f}点")


def latest_three_one_minute_body_stop(one_minute: pd.DataFrame, entry: float,
                                      direction: int) -> tuple[bool, float, str]:
    """Use the last three completed one-minute real bodies, never their wicks."""
    frame = one_minute.sort_values("date").tail(3)
    if len(frame) < 3 or direction not in {-1, 1} or entry <= 0:
        return False, 0.0, "最近三根一分钟实体止损数据不足"
    bodies = frame[["open", "close"]].astype(float)
    buffer = max(latest_atr(one_minute) * .15, entry * .0003)
    edge = float(bodies.max(axis=1).max() if direction < 0
                 else bodies.min(axis=1).min())
    stop = round(edge + buffer if direction < 0 else edge - buffer, 2)
    if direction * (entry - stop) <= 0:
        return False, stop, "最近三根一分钟实体止损已不在保护侧"
    return True, stop, f"最近三根一分钟K线实体边界{edge:.2f}，缓冲{buffer:.2f}，止损{stop:.2f}"


def long_pressure_runway(five_minute_market, entry_price: float, risk: float,
                         *, lookback: int = 12, minimum_r: float = 1.2,
                         minimum_atr: float = .8) -> tuple[bool, str, float]:
    """Require real room to the nearest prior 5m body resistance before buying."""
    frame = five_minute_market.sort_values("date").reset_index(drop=True)
    if len(frame) < max(21, lookback + 1) or entry_price <= 0 or risk <= 0:
        return False, "上方利润空间数据不足", 0.0
    close = frame["close"].astype(float)
    previous = close.shift(1)
    tr = pd.concat(((frame["high"].astype(float) - frame["low"].astype(float)),
                    (frame["high"].astype(float) - previous).abs(),
                    (frame["low"].astype(float) - previous).abs()), axis=1).max(axis=1)
    atr_value = float(tr.tail(14).mean())
    prior = frame.iloc[-lookback - 1:-1]
    body_tops = prior[["open", "close"]].astype(float).max(axis=1)
    overhead = body_tops[body_tops > entry_price]
    if overhead.empty:
        return True, "上方未发现最近5分钟实体压力", float("inf")
    resistance = float(overhead.min())
    room = resistance - entry_price
    required = max(risk * minimum_r, atr_value * minimum_atr)
    if room < required:
        return False, (f"最近5分钟实体压力{resistance:.2f}仅剩{room:.2f}点，"
                       f"低于所需{required:.2f}点，禁止在压力位前追多"), resistance
    return True, f"上方压力空间{room:.2f}点，满足最低{required:.2f}点", resistance


def reversal_runway_required(*, early_freeze_stage_entry: bool) -> bool:
    """Do not let an internal 5m level veto a completed 1m launch stage."""
    return not early_freeze_stage_entry


def original_strategy_reversal_entry_allowed(
        direction: int, *, early_freeze_stage_entry: bool,
        frozen_big_cross_entry: bool,
        five_minute_heikin_confirmed: bool = True) -> bool:
    """Keep live execution on 1m reversal stage 2/3 only.

    Five-minute structure is audit context, not an entry veto.  The optional
    argument stays for compatibility with existing callers and audit tests.
    """
    return bool(direction in {-1, 1}
                and (early_freeze_stage_entry or frozen_big_cross_entry))


def five_minute_price_reversal_confirms(
        five_minute: pd.DataFrame, direction: int, *, lookback: int = 3) -> bool:
    """Confirm the companion 5m turn using ordinary exchange candles only."""
    return recent_price_reversal_confirms(five_minute, direction, lookback=lookback)


def five_minute_heikin_reversal_confirms(
        five_minute: pd.DataFrame, direction: int, *, lookback: int = 3) -> bool:
    """Legacy API alias; no Heikin-Ashi values are calculated or consulted."""
    return five_minute_price_reversal_confirms(five_minute, direction, lookback=lookback)


def strategy01_dynamic_stop(
    five_minute_market, entry_price: float, direction: int, *,
    atr_window: int = 14, atr_buffer_multiple: float = .20,
    minimum_buffer_pct: float = .0003,
) -> tuple[float, float, float]:
    """Return a 5m structure stop, its distance ratio, and distance in ATR units.

    Only already supplied/confirmed 5m candles are used.  A short stop sits above
    the highest wick; a long stop mirrors it below the lowest wick.
    """
    if direction not in {-1, 1}:
        raise ValueError("strategy01 stop direction must be long or short")
    frame = five_minute_market.sort_values("date").copy()
    if len(frame) < atr_window + 1:
        raise ValueError("not enough confirmed 5m candles for dynamic stop")
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = float(true_range.tail(atr_window).mean())
    if atr <= 0 or entry_price <= 0:
        raise ValueError("invalid price or ATR for dynamic stop")
    buffer = max(atr * atr_buffer_multiple, entry_price * minimum_buffer_pct)
    # Use the latest confirmed local swing, not the highest/lowest value in an
    # arbitrary fixed number of candles.  Fixed 8/20/40-bar extrema can attach
    # an old spike to a new compact setup and manufacture a false huge risk.
    highs, lows = high.tolist(), low.tolist()
    pivot_highs = [index for index in range(1, len(frame) - 1)
                   if highs[index] > highs[index - 1] and highs[index] >= highs[index + 1]]
    pivot_lows = [index for index in range(1, len(frame) - 1)
                  if lows[index] < lows[index - 1] and lows[index] <= lows[index + 1]]
    if direction < 0:
        leg_start = pivot_lows[-1] if pivot_lows else len(frame) - 2
        current_highs = [index for index in pivot_highs if index > leg_start]
        local_high = highs[current_highs[-1]] if current_highs else max(highs[leg_start:])
        stop = max(local_high, highs[-1]) + buffer
        if stop <= entry_price:
            stop = entry_price + buffer
        distance = stop - entry_price
    else:
        leg_start = pivot_highs[-1] if pivot_highs else len(frame) - 2
        current_lows = [index for index in pivot_lows if index > leg_start]
        local_low = lows[current_lows[-1]] if current_lows else min(lows[leg_start:])
        stop = min(local_low, lows[-1]) - buffer
        if stop >= entry_price:
            stop = entry_price - buffer
        distance = entry_price - stop
    return round(stop, 2), distance / entry_price, distance / atr


def strategy01_local_stop(
    one_minute_market, entry_price: float, direction: int, *,
    atr_window: int = 14, atr_buffer_multiple: float = .15,
) -> tuple[float, float, float]:
    """Use the latest 1m swing for an ordinary aligned entry.

    The generic multi-timeframe branch has no pattern-owned stop.  Reusing an
    eight-bar 5m extreme can therefore attach an old, remote swing to a late
    entry.  Its stop must instead belong to the local 1m setup immediately
    preceding the order.
    """
    if direction not in {-1, 1}:
        raise ValueError("strategy01 local stop direction must be long or short")
    frame = one_minute_market.sort_values("date").copy()
    if len(frame) < atr_window + 1:
        raise ValueError("not enough confirmed 1m candles for local stop")
    high, low = frame["high"].astype(float), frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous = close.shift(1)
    atr = float(pd.concat(((high - low), (high - previous).abs(), (low - previous).abs()), axis=1)
                .max(axis=1).tail(atr_window).mean())
    if atr <= 0 or entry_price <= 0:
        raise ValueError("invalid price or ATR for local stop")
    buffer = max(atr * atr_buffer_multiple, entry_price * .0002)
    highs, lows = high.tolist(), low.tolist()
    pivot_highs = [index for index in range(1, len(frame) - 1)
                   if highs[index] > highs[index - 1] and highs[index] >= highs[index + 1]]
    pivot_lows = [index for index in range(1, len(frame) - 1)
                  if lows[index] < lows[index - 1] and lows[index] <= lows[index + 1]]
    if direction < 0:
        leg_start = pivot_lows[-1] if pivot_lows else len(frame) - 2
        current_highs = [index for index in pivot_highs if index > leg_start]
        local_high = highs[current_highs[-1]] if current_highs else max(highs[leg_start:])
        stop = max(local_high, highs[-1], entry_price) + buffer
        distance = stop - entry_price
    else:
        leg_start = pivot_highs[-1] if pivot_highs else len(frame) - 2
        current_lows = [index for index in pivot_lows if index > leg_start]
        local_low = lows[current_lows[-1]] if current_lows else min(lows[leg_start:])
        stop = min(local_low, lows[-1], entry_price) - buffer
        distance = entry_price - stop
    return round(stop, 2), distance / entry_price, distance / atr


def one_minute_local_extreme_ma5_ma10_cross_setup(
    one_closed: pd.DataFrame, one_live: pd.DataFrame | None = None,
    *, range_bars: int = 16, edge_fraction: float = .38,
    timeframe_label: str = "一分钟",
) -> tuple[int, str, float]:
    """Use a fresh MA5/MA10 cross only at the timeframe's local range edge."""
    frame = one_closed.sort_values("date").copy()
    if one_live is not None and not one_live.empty:
        frame = pd.concat((frame, one_live), ignore_index=True)
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame.reset_index(drop=True)
    if len(frame) < max(21, range_bars + 2):
        return 0, f"{timeframe_label}小金叉/小死叉：K线数据不足", 0.0
    close = frame["close"].astype(float)
    ma5, ma10 = close.rolling(5).mean(), close.rolling(10).mean()
    long_cross = ma5.iloc[-2] <= ma10.iloc[-2] and ma5.iloc[-1] > ma10.iloc[-1]
    short_cross = ma5.iloc[-2] >= ma10.iloc[-2] and ma5.iloc[-1] < ma10.iloc[-1]
    if not (long_cross or short_cross):
        return 0, f"等待{timeframe_label}MA5穿越MA10形成新鲜小金叉/小死叉", 0.0
    direction = 1 if long_cross else -1
    recent = frame.tail(range_bars)
    range_low = float(recent["low"].astype(float).min())
    range_high = float(recent["high"].astype(float).max())
    width = range_high - range_low
    price = float(frame.iloc[-1]["close"])
    if width <= 0:
        return 0, f"{timeframe_label}小交叉：局部区间无效", 0.0
    position = (price - range_low) / width
    at_edge = position <= edge_fraction if direction > 0 else position >= 1 - edge_fraction
    recent_six = frame.tail(6)
    atr1 = latest_atr(frame)
    edge_tolerance = max(atr1 * .15, price * .0001)
    touched_edge = (float(recent_six["low"].astype(float).min()) <= range_low + edge_tolerance
                    if direction > 0 else
                    float(recent_six["high"].astype(float).max()) >= range_high - edge_tolerance)
    if not at_edge or not touched_edge:
        cross_name = "小金叉" if direction > 0 else "小死叉"
        return 0, (f"{timeframe_label}{cross_name}位于局部区间中部{position * 100:.0f}%，"
                   "不是局部低点/高点，有效交叉条件不成立"), 0.0
    latest = frame.iloc[-1]
    directional_candle = direction * (float(latest["close"]) - float(latest["open"])) > 0
    boundary_ma = max(ma5.iloc[-1], ma10.iloc[-1]) if direction > 0 else min(ma5.iloc[-1], ma10.iloc[-1])
    price_confirmed = direction * (price - float(boundary_ma)) >= 0
    if not directional_candle or not price_confirmed:
        return 0, f"{timeframe_label}小交叉已出现，但当前K线方向或收盘位置尚未确认", 0.0
    buffer = max(atr1 * .15, price * .0002)
    stop = (float(recent_six["low"].astype(float).min()) - buffer if direction > 0 else
            float(recent_six["high"].astype(float).max()) + buffer)
    risk = abs(price - stop)
    name = "局部低点小金叉" if direction > 0 else "局部高点小死叉"
    action = "局部小止损市价做多" if direction > 0 else "局部小止损市价做空"
    return direction, (f"{timeframe_label}{name}提前补充反转三阶段：MA5新鲜"
                       f"{'上穿' if direction > 0 else '下穿'}MA10；当前位置为局部区间"
                       f"{position * 100:.0f}%，{action}；结构风险{risk:.2f}点，"
                       "若超过3点则改用下单位置固定3点止损；五分钟后续确认负责升级持有"), round(stop, 2)


def ma5_ma10_cross_observation(closed: pd.DataFrame, live: pd.DataFrame | None,
                               *, timeframe: str, range_bars: int = 16) -> dict | None:
    """Describe the latest fresh small cross, including invalid middle crosses."""
    frame = closed.sort_values("date").copy()
    if live is not None and not live.empty:
        frame = pd.concat((frame, live), ignore_index=True)
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame.reset_index(drop=True)
    if len(frame) < max(21, range_bars + 2):
        return None
    close = frame["close"].astype(float)
    ma5, ma10 = close.rolling(5).mean(), close.rolling(10).mean()
    long_cross = ma5.iloc[-2] <= ma10.iloc[-2] and ma5.iloc[-1] > ma10.iloc[-1]
    short_cross = ma5.iloc[-2] >= ma10.iloc[-2] and ma5.iloc[-1] < ma10.iloc[-1]
    if not (long_cross or short_cross):
        return None
    direction = 1 if long_cross else -1
    recent = frame.tail(range_bars)
    low, high = float(recent["low"].min()), float(recent["high"].max())
    price = float(frame.iloc[-1]["close"])
    position = (price - low) / max(high - low, 1e-9)
    _, reason, stop = one_minute_local_extreme_ma5_ma10_cross_setup(
        closed, live, range_bars=range_bars,
        timeframe_label="一分钟" if timeframe == "1m" else "五分钟")
    return {
        "timeframe": timeframe, "direction": direction,
        "cross_name": "小金叉" if direction > 0 else "小死叉",
        "cross_time": pd.to_datetime(frame.iloc[-1]["date"]), "price": price,
        "range_position": position, "valid": stop > 0, "reason": reason,
    }


def one_minute_launch_freeze_observations(
    closed: pd.DataFrame, live: pd.DataFrame | None = None,
    *, candle_mode: str = "raw",
) -> list[dict]:
    """Return fresh bottom/top launch evidence that must be frozen, not traded."""
    frame = closed.sort_values("date").copy()
    if live is not None and not live.empty:
        frame = pd.concat((frame, live), ignore_index=True)
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame.reset_index(drop=True)
    if len(frame) < 21:
        return []
    if candle_mode != "raw":
        raise ValueError("average candles are audit-only; candle_mode must be raw")
    price_frame = frame
    close = price_frame["close"].astype(float)
    ma5, ma10 = close.rolling(5).mean(), close.rolling(10).mean()
    latest, previous = frame.iloc[-1], frame.iloc[-2]
    price_latest, price_previous = price_frame.iloc[-1], price_frame.iloc[-2]
    price = float(latest["close"])
    trigger_price = float(price_latest["close"])
    atr1 = latest_atr(frame)
    buffer = max(atr1 * .15, price * .0002)
    observations: list[dict] = []
    recent_before = frame.iloc[-10:-1]
    before_previous = frame.iloc[-3]
    confirmed_pivot_low = (float(previous["low"]) < float(before_previous["low"])
                           and float(previous["low"]) <= float(latest["low"])
                           and price > float(previous["close"]))
    confirmed_pivot_high = (float(previous["high"]) > float(before_previous["high"])
                            and float(previous["high"]) >= float(latest["high"])
                            and price < float(previous["close"]))
    if confirmed_pivot_low:
        observations.append({"stage": "confirmed_local_bottom", "direction": 1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(previous["low"]) - buffer})
    if confirmed_pivot_high:
        observations.append({"stage": "confirmed_local_top", "direction": -1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(previous["high"]) + buffer})
    swept_low = float(latest["low"]) < float(recent_before["low"].astype(float).min())
    swept_high = float(latest["high"]) > float(recent_before["high"].astype(float).max())
    span = max(float(latest["high"] - latest["low"]), 1e-9)
    if swept_low and (price - float(latest["low"])) / span >= .55:
        observations.append({"stage": "bottom_sweep_reclaim", "direction": 1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(latest["low"]) - buffer})
    if swept_high and (float(latest["high"]) - price) / span >= .55:
        observations.append({"stage": "top_sweep_reject", "direction": -1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(latest["high"]) + buffer})
    # A stage-1 anchor is deliberately broader than an entry trigger.  The
    # market often turns from a relative local extreme or volume exhaustion
    # without engulfing the previous candle or printing a textbook wick.  It
    # is safe to freeze these shapes because stage 2/3 still has to confirm.
    lookback = frame.tail(12)
    prior_edge = frame.iloc[-12:-3]
    local_high = float(lookback["high"].astype(float).max())
    local_low = float(lookback["low"].astype(float).min())
    edge_tolerance = max(atr1 * .25, price * .00015)
    # The anchor belongs to the current candle.  Do not borrow an extreme from
    # either of the preceding two candles and stamp it with the current time;
    # that can turn a live bottom rebound into a false "current top" anchor.
    near_relative_high = float(latest["high"]) >= local_high - edge_tolerance
    near_relative_low = float(latest["low"]) <= local_low + edge_tolerance
    price_body = float(price_latest["close"] - price_latest["open"])
    previous_price_body = float(price_previous["close"] - price_previous["open"])
    price_turn_down = price_body < 0 and previous_price_body >= 0
    price_turn_up = price_body > 0 and previous_price_body <= 0
    volumes = frame.tail(20)["volume"].astype(float)
    baseline_volume = float(volumes.iloc[:-1].median()) if len(volumes) > 1 else 0.0
    recent_volume = max(float(latest["volume"]), float(previous["volume"]))
    volume_exhaustion = baseline_volume > 0 and recent_volume >= baseline_volume * 1.20
    if near_relative_high and (price_turn_down or volume_exhaustion):
        observations.append({
            "stage": "relative_local_top_sweep", "direction": -1,
            "time": pd.to_datetime(latest["date"]), "price": price,
            "stop": local_high + buffer,
        })
    if near_relative_low and (price_turn_up or volume_exhaustion):
        observations.append({
            "stage": "relative_local_bottom_sweep", "direction": 1,
            "time": pd.to_datetime(latest["date"]), "price": price,
            "stop": local_low - buffer,
        })
    previous_open, previous_close = float(previous["open"]), float(previous["close"])
    latest_open = float(latest["open"])
    previous_body_midpoint = (previous_open + previous_close) / 2.0
    if (previous_close < previous_open and price > latest_open
            and price >= previous_body_midpoint):
        observations.append({"stage": "bottom_half_bearish_cover", "direction": 1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(6)["low"].astype(float).min()) - buffer})
    if (previous_close > previous_open and price < latest_open
            and price <= previous_body_midpoint):
        observations.append({"stage": "top_half_bullish_cover", "direction": -1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(6)["high"].astype(float).max()) + buffer})
    ma5_slope = float(ma5.iloc[-1] - ma5.iloc[-2])
    previous_ma5_slope = float(ma5.iloc[-2] - ma5.iloc[-3])
    # The earliest real order follows the fresh MA5 turn.  Candle-to-candle
    # cover is useful context but is deliberately not a rigid requirement.
    long_ma5_turn = (trigger_price > float(ma5.iloc[-1]) and ma5_slope >= 0
                     and (float(price_previous["close"]) <= float(ma5.iloc[-2])
                          or previous_ma5_slope < 0))
    short_ma5_turn = (trigger_price < float(ma5.iloc[-1]) and ma5_slope <= 0
                      and (float(price_previous["close"]) >= float(ma5.iloc[-2])
                           or previous_ma5_slope > 0))
    if long_ma5_turn:
        observations.append({"stage": "price_above_flat_rising_ma5", "direction": 1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(4)["low"].astype(float).min()) - buffer})
    if short_ma5_turn:
        observations.append({"stage": "price_below_flat_falling_ma5", "direction": -1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(4)["high"].astype(float).max()) + buffer})
    if float(price_previous["close"]) <= float(ma5.iloc[-2]) and trigger_price > float(ma5.iloc[-1]):
        observations.append({"stage": "price_reclaim_ma5", "direction": 1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(6)["low"].astype(float).min()) - buffer})
    if float(price_previous["close"]) >= float(ma5.iloc[-2]) and trigger_price < float(ma5.iloc[-1]):
        observations.append({"stage": "price_break_ma5", "direction": -1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(6)["high"].astype(float).max()) + buffer})
    if float(ma5.iloc[-2]) <= float(ma10.iloc[-2]) and float(ma5.iloc[-1]) > float(ma10.iloc[-1]):
        observations.append({"stage": "small_golden_cross", "direction": 1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(6)["low"].astype(float).min()) - buffer})
    if float(ma5.iloc[-2]) >= float(ma10.iloc[-2]) and float(ma5.iloc[-1]) < float(ma10.iloc[-1]):
        observations.append({"stage": "small_death_cross", "direction": -1,
                             "time": pd.to_datetime(latest["date"]), "price": price,
                             "stop": float(frame.tail(6)["high"].astype(float).max()) + buffer})
    return observations


def one_minute_big_cross_launch(
    closed: pd.DataFrame, live: pd.DataFrame | None = None,
) -> tuple[int, str, float]:
    """Trigger only at the fresh opening of a 1m MA5/MA10/MA20 fan."""
    frame = closed.sort_values("date").copy()
    if live is not None and not live.empty:
        frame = pd.concat((frame, live), ignore_index=True)
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame.reset_index(drop=True)
    if len(frame) < 22:
        return 0, "一分钟大交叉：K线不足", 0.0
    close = frame["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
    for direction in (1, -1):
        ordered = ((direction * (ma5 - ma10) > 0)
                   & (direction * (ma10 - ma20) > 0))
        fresh = bool(ordered.iloc[-1] and not ordered.iloc[-2])
        opening = (direction * float((ma5 - ma10).iloc[-1]) > direction * float((ma5 - ma10).iloc[-2])
                   and direction * float((ma10 - ma20).iloc[-1]) > direction * float((ma10 - ma20).iloc[-2]))
        price_confirmed = direction * (float(close.iloc[-1]) - float(ma5.iloc[-1])) > 0
        if fresh and opening and price_confirmed:
            atr1 = latest_atr(frame)
            stop = (float(frame.tail(6)["low"].astype(float).min()) - max(atr1 * .15, close.iloc[-1] * .0002)
                    if direction > 0 else
                    float(frame.tail(6)["high"].astype(float).max()) + max(atr1 * .15, close.iloc[-1] * .0002))
            return direction, ("一分钟大金叉启动" if direction > 0 else "一分钟大死叉启动"), round(stop, 2)
    return 0, "等待一分钟三均线新鲜大交叉张口", 0.0


def one_minute_ma5_ma20_early_launch(
    closed: pd.DataFrame, live: pd.DataFrame | None = None,
) -> tuple[int, str, float]:
    """Launch from a frozen reversal when MA5 crosses MA20 before MA10 does."""
    frame = closed.sort_values("date").copy()
    if live is not None and not live.empty:
        frame = pd.concat((frame, live), ignore_index=True)
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame.reset_index(drop=True)
    if len(frame) < 22:
        return 0, "一分钟MA5/MA20前置交叉：K线不足", 0.0
    close = frame["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
    for direction in (1, -1):
        crossed = (direction * float((ma5 - ma20).iloc[-2]) <= 0
                   and direction * float((ma5 - ma20).iloc[-1]) > 0)
        fast_ordered = direction * float((ma5 - ma10).iloc[-1]) > 0
        price_confirmed = direction * (float(close.iloc[-1]) - float(ma5.iloc[-1])) > 0
        ma5_advancing = direction * float(ma5.diff().iloc[-1]) > 0
        if crossed and fast_ordered and price_confirmed and ma5_advancing:
            atr1 = latest_atr(frame)
            buffer = max(atr1 * .15, float(close.iloc[-1]) * .0002)
            stop = (float(frame.tail(6)["low"].astype(float).min()) - buffer
                    if direction > 0 else
                    float(frame.tail(6)["high"].astype(float).max()) + buffer)
            return direction, (
                "一分钟MA5上穿MA20前置启动（MA5已在MA10上方）"
                if direction > 0 else
                "一分钟MA5下穿MA20前置启动（MA5已在MA10下方）"
            ), round(stop, 2)
    return 0, "等待一分钟MA5/MA20新鲜前置交叉", 0.0


def matching_frozen_launch_candidates(rows, direction: int, big_cross_time,
                                      *, valid_minutes: int = 30) -> list:
    """Any one matching freeze is enough; repeats refresh rather than reset the chain."""
    cross_utc = pd.to_datetime(big_cross_time, utc=True)
    candidates = [
        row for row in rows
        if str(row["pattern_type"]).startswith("one_minute_launch_freeze:")
        and int(row["direction"]) == direction
        and pd.Timedelta(0) <= cross_utc - pd.to_datetime(row["confirmed_bar_time"], utc=True)
        <= pd.Timedelta(minutes=valid_minutes)
    ]
    return sorted(candidates, key=lambda row: pd.to_datetime(row["confirmed_bar_time"], utc=True))


def matching_current_tick_reversal_anchors(observations, direction: int, stage_time) -> list[dict]:
    """Return same-tick stage-1 anchors available to an immediate stage-2 trigger."""
    anchor_stages = {
        "confirmed_local_bottom", "confirmed_local_top",
        "bottom_sweep_reclaim", "top_sweep_reject",
        "bottom_half_bearish_cover", "top_half_bullish_cover",
        "relative_local_bottom_sweep", "relative_local_top_sweep",
    }
    stage_utc = pd.to_datetime(stage_time, utc=True)
    return [
        item for item in observations
        if int(item["direction"]) == direction
        and item["stage"] in anchor_stages
        and pd.to_datetime(item["time"], utc=True) <= stage_utc
    ]


def latest_reversal_anchor_for_stage(rows, observations, direction: int,
                                     stage_time, *, valid_minutes: int = 30):
    """Bind each stage-2/3 trigger to the newest preceding local extreme.

    Two bottoms (or tops) inside the validity window are two independent
    opportunities.  An older missed/rejected event must not own a later stage
    after a newer local extreme has formed.
    """
    anchor_stages = {
        "confirmed_local_bottom", "confirmed_local_top",
        "bottom_sweep_reclaim", "top_sweep_reject",
        "bottom_half_bearish_cover", "top_half_bullish_cover",
        "relative_local_bottom_sweep", "relative_local_top_sweep",
    }
    candidates = []
    for row in matching_frozen_launch_candidates(
            rows, direction, stage_time, valid_minutes=valid_minutes):
        stage = str(row["pattern_type"]).split(":")[-1]
        if stage in anchor_stages:
            candidates.append((pd.to_datetime(row["confirmed_bar_time"], utc=True), row))
    for item in matching_current_tick_reversal_anchors(
            observations, direction, stage_time):
        candidates.append((pd.to_datetime(item["time"], utc=True), item))
    return max(candidates, key=lambda value: value[0])[1] if candidates else None


def same_minute_opposing_anchor_blocks_stage(observations, direction: int,
                                              stage_time) -> tuple[bool, str]:
    """Do not let an MA5 stage race through an opposite extreme on the same minute."""
    anchor_stages = {
        "confirmed_local_bottom", "confirmed_local_top",
        "bottom_sweep_reclaim", "top_sweep_reject",
        "bottom_half_bearish_cover", "top_half_bullish_cover",
        "relative_local_bottom_sweep", "relative_local_top_sweep",
    }
    stage_utc = pd.to_datetime(stage_time, utc=True)
    directional_stage_names = {
        str(item.get("stage")) for item in observations
        if int(item.get("direction") or 0) == direction
        and pd.to_datetime(item.get("time"), utc=True) == stage_utc
        and item.get("stage") in {
            "price_above_flat_rising_ma5", "price_below_flat_falling_ma5",
            "price_reclaim_ma5", "price_break_ma5", "small_golden_cross",
            "small_death_cross",
        }
    }
    if len(directional_stage_names) >= 2:
        return False, (
            "two independent same-direction stage-2 confirmations formed on the same minute; "
            "direction is resolved despite one transient opposite local label")
    for item in observations:
        if (int(item.get("direction") or 0) == -direction
                and item.get("stage") in anchor_stages
                and pd.to_datetime(item.get("time"), utc=True) >= stage_utc):
            return True, (
                f"opposite local extreme {item['stage']} formed on the same/newer minute; "
                "invalidate the racing MA5 stage")
    return False, "no same-minute opposing local extreme"


def latched_stage_two_resume_candidate(rows, one_minute: pd.DataFrame,
                                       *, valid_minutes: int = 30) -> dict | None:
    """Resume a persisted stage-2 reversal after a brief opposite-colour pullback.

    Once a stage-1 anchor and its MA5 stage-2 confirmation have both been
    recorded, one or more small counter candles must not erase that chain.  A
    later candle that closes back in the candidate direction and on the same
    side of MA5 reactivates it until expiry.  Normal order/position deduplication
    still decides whether an order may actually be submitted.
    """
    frame = one_minute.sort_values("date").drop_duplicates("date", keep="last").copy()
    if len(frame) < 5:
        return None
    latest = frame.iloc[-1]
    at = pd.to_datetime(latest["date"], utc=True)
    close = frame["close"].astype(float)
    ma5 = float(close.rolling(5).mean().iloc[-1])
    if not pd.notna(ma5):
        return None
    anchor_stages = {
        "confirmed_local_bottom", "confirmed_local_top",
        "bottom_sweep_reclaim", "top_sweep_reject",
        "bottom_half_bearish_cover", "top_half_bullish_cover",
        "relative_local_bottom_sweep", "relative_local_top_sweep",
    }
    stage_two = {
        "price_above_flat_rising_ma5", "price_below_flat_falling_ma5",
        "price_reclaim_ma5", "price_break_ma5",
    }
    candidates = []
    for direction in (1, -1):
        matching = matching_frozen_launch_candidates(
            rows, direction, at, valid_minutes=valid_minutes)
        anchors = [row for row in matching
                   if str(row["pattern_type"]).split(":")[-1] in anchor_stages]
        confirmations = [row for row in matching
                         if str(row["pattern_type"]).split(":")[-1] in stage_two]
        if not anchors or not confirmations:
            continue
        anchor = anchors[-1]
        confirmation = next((row for row in confirmations
                             if pd.to_datetime(row["confirmed_bar_time"], utc=True)
                             >= pd.to_datetime(anchor["confirmed_bar_time"], utc=True)), None)
        if confirmation is None:
            continue
        latest_open, latest_close = float(latest["open"]), float(latest["close"])
        resumed = (direction * (latest_close - latest_open) > 0
                   and direction * (latest_close - ma5) > 0)
        if resumed and at > pd.to_datetime(confirmation["confirmed_bar_time"], utc=True):
            candidates.append((pd.to_datetime(confirmation["confirmed_bar_time"], utc=True), {
                "stage": "latched_stage2_resume", "direction": direction,
                "time": at, "price": latest_close,
                "stop": float(anchor["stop_reference"]),
                "anchor_price": float(anchor["entry_reference"]),
            }))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def frozen_launch_direction_allowed(direction: int, one_minute_direction: int,
                                    opposing_structure_active: bool) -> tuple[bool, str]:
    """Never let a delayed frozen cross overrule the latest 1m reversal structure."""
    if direction not in {-1, 1}:
        return False, "冻结大交叉方向无效"
    if opposing_structure_active:
        return False, "最新一分钟反向局部结构已经成立，旧方向冻结和滞后大交叉作废"
    # The latest confirmed opposite local structure above may invalidate this
    # freeze; the displayed 1m arrow itself may not.
    return True, "方向箭头不参与冻结阶段否决；仅由更新的反向局部结构作废"

    if one_minute_direction == -direction:
        return False, "一分钟当前方向已经反向，禁止旧方向冻结大交叉补漏下单"
    return True, "冻结方向与最新一分钟结构一致"


def local_extreme_cross_location_allows(one_minute: pd.DataFrame, direction: int,
                                        *, range_bars: int = 16,
                                        edge_fraction: float = .38) -> tuple[bool, str]:
    """Report local position without vetoing an otherwise valid 1m trigger."""
    frame = one_minute.sort_values("date").tail(range_bars)
    if direction not in {-1, 1} or len(frame) < range_bars:
        return False, "一分钟局部位置数据不足"
    low = float(frame["low"].astype(float).min())
    high = float(frame["high"].astype(float).max())
    if high <= low:
        return False, "一分钟局部区间无效"
    price = float(frame.iloc[-1]["close"])
    position = (price - low) / (high - low)
    return True, (f"一分钟局部位置{position * 100:.0f}%仅作记录；"
                  "有效扫损、MA5穿越或小交叉使用自身局部结构止损，不再受38%位置门否决")


def one_minute_launch_quality_gate(
    one_minute: pd.DataFrame, direction: int, frozen_price: float, *,
    max_launch_atr: float = 1.75, max_ma5_atr: float = 1.0,
    minimum_runway_points: float = 3.5,
) -> tuple[bool, str]:
    """Reject a late chase without restoring the retired 38% range veto."""
    frame = one_minute.sort_values("date").reset_index(drop=True)
    if direction not in {-1, 1} or len(frame) < 21 or frozen_price <= 0:
        return False, "一分钟启动质量门数据不足"
    close = frame["close"].astype(float)
    price = float(close.iloc[-1])
    ma5 = float(close.rolling(5).mean().iloc[-1])
    atr1 = latest_atr(frame)
    if atr1 <= 0:
        return False, "一分钟ATR不可用，禁止前置抢单"
    launch_distance = direction * (price - frozen_price)
    if launch_distance < 0:
        return False, "价格已跌回冻结点反向一侧，前置启动失效"
    if launch_distance > atr1 * max_launch_atr:
        return False, (f"[LATE_LAUNCH]价格距冻结点已运行{launch_distance / atr1:.2f}倍1分钟ATR，"
                       f"超过{max_launch_atr:.2f}倍，属于上涨末端追多/下跌末端追空")
    ma5_distance = direction * (price - ma5)
    if ma5_distance < 0 or ma5_distance > atr1 * max_ma5_atr:
        return False, (f"[MA5_CHASE]价格距MA5有效侧{max(ma5_distance, 0):.2f}点，"
                       f"超过{max_ma5_atr:.2f}倍1分钟ATR，禁止远离MA5追单")
    prior = frame.iloc[-18:-1]
    if direction > 0:
        levels = prior[["open", "close"]].astype(float).max(axis=1)
        levels = levels[levels > price]
        boundary = float(levels.min()) if not levels.empty else float("inf")
        runway = boundary - price
        label = "前方局部实体压力"
    else:
        levels = prior[["open", "close"]].astype(float).min(axis=1)
        levels = levels[levels < price]
        boundary = float(levels.max()) if not levels.empty else float("-inf")
        runway = price - boundary
        label = "下方局部实体支撑"
    # A nearby 5m body is reference context only.  It must not veto a fresh
    # 1m stage-2/stage-3 reversal and turn the entry into a late chase.
    boundary_text = "近期无相邻压力/支撑" if runway == float("inf") else f"结构空间{runway:.2f}点"
    runway_text = (f"5分钟参照空间{runway:.2f}点（不否决）"
                   if runway < minimum_runway_points else boundary_text)
    return True, (f"一分钟反转链第一次有效转向通过：距冻结点{launch_distance / atr1:.2f} ATR，"
                  f"距MA5 {ma5_distance / atr1:.2f} ATR，{runway_text}")


def locked_stage_three_bypasses_ma5_chase(
        stage: str, direction: int, locked_direction: int,
        quality_reason: str) -> bool:
    """Allow the mandatory stage-three fallback for the newest 1m+5m lock."""
    return bool(
        stage in {"small_golden_cross", "small_death_cross"}
        and direction in {-1, 1}
        and direction == locked_direction
        and "[MA5_CHASE]" in str(quality_reason)
    )


def staged_launch_is_fresh(stage_time, latest_time, *, maximum_age_seconds: float = 90.0) -> tuple[bool, str]:
    """A staged trigger is executable only while it still belongs to the current move."""
    try:
        age = (pd.to_datetime(latest_time, utc=True)
               - pd.to_datetime(stage_time, utc=True)).total_seconds()
    except (TypeError, ValueError):
        return False, "一分钟阶段时间无效"
    if age < -1.0:
        return False, "一分钟阶段时间晚于当前K线"
    if age > maximum_age_seconds:
        return False, (f"一分钟阶段触发已过去{age:.0f}秒，超过{maximum_age_seconds:.0f}秒；"
                       "价格与局部结构已换段，禁止在三阶段末端补追")
    return True, f"一分钟阶段触发距当前{age:.0f}秒，仍属于本轮局部结构"


def staged_launch_risk_allows(one_minute: pd.DataFrame, entry: float, stop: float,
                              direction: int, *, maximum_atr: float = 2.0,
                              minimum_contracts: int = 1,
                              calculated_contracts: int = 1) -> tuple[bool, str]:
    """Reject a remote structure stop when minimum contract size cannot reduce risk."""
    if direction not in {-1, 1} or entry <= 0 or stop <= 0:
        return False, "一分钟阶段风险输入无效"
    risk = direction * (entry - stop)
    atr1 = latest_atr(one_minute)
    if risk <= 0 or atr1 <= 0:
        return False, "一分钟阶段止损方向或ATR无效"
    risk_atr = risk / atr1
    if calculated_contracts <= minimum_contracts and risk_atr > maximum_atr:
        return False, (f"局部结构止损{risk:.2f}点（{risk_atr:.2f} ATR），且已是最小"
                       f"{minimum_contracts}张无法继续缩仓；超过{maximum_atr:.2f} ATR，放弃入场")
    return True, f"局部结构止损{risk:.2f}点（{risk_atr:.2f} ATR）与仓位风险匹配"


def _wilder_rsi(close: pd.Series, period: int) -> float:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False).mean()
    average_gain = float(gain.iloc[-1])
    average_loss = float(loss.iloc[-1])
    if average_loss <= 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def continuation_indicator_votes_allowed(votes: dict[str, int], *,
                                         confirmed_uptrend_pullback: bool = False) -> bool:
    """Allow a confirmed 1m/5m rising pullback while 15m is still catching up."""
    standard = all(value >= 1 for value in votes.values()) and sum(votes.values()) >= 6
    early_rising = (confirmed_uptrend_pullback and votes["1m"] >= 2
                    and votes["5m"] >= 2 and votes["15m"] >= 1)
    return standard or early_rising


def indicator_confirmation_gate(
    one_minute: pd.DataFrame, five_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame, direction: int, *,
    entry_class: str = "standard", minimum_volume_ratio: float = .60,
    price_structure_override: bool = False,
    confirmed_uptrend_pullback: bool = False,
) -> tuple[bool, str, dict]:
    """Use exchange candles, never browser DOM, as an auditable final entry gate.

    Indicators do not create orders.  They confirm or reject an already-valid
    strategy-01 candidate immediately before the order intent is persisted.
    """
    if direction not in {-1, 1} or entry_class not in {"reversal", "continuation", "standard"}:
        return False, "技术指标确认门输入无效", {}
    frames = {"1m": one_minute, "5m": five_minute, "15m": fifteen_minute}
    metrics: dict[str, dict[str, float | int | str]] = {}
    for timeframe, market in frames.items():
        frame = market.sort_values("date").reset_index(drop=True)
        if len(frame) < 35 or "volume" not in frame:
            return False, f"{timeframe}技术指标数据不足，禁止自动下单", metrics
        close = frame["close"].astype(float)
        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma10 = float(close.rolling(10).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        latest_dif = float(dif.iloc[-1])
        latest_dea = float(dea.iloc[-1])
        histogram = float((dif - dea).iloc[-1])
        rsi6, rsi12, rsi24 = (_wilder_rsi(close, period) for period in (6, 12, 24))
        volume = frame["volume"].astype(float)
        baseline = float(volume.iloc[-21:-1].median())
        volume_ratio = float(volume.iloc[-1] / baseline) if baseline > 0 else 0.0
        ma_vote = int(ma5 > ma10 > ma20) if direction > 0 else int(ma5 < ma10 < ma20)
        macd_vote = int(histogram > 0) if direction > 0 else int(histogram < 0)
        rsi_vote = int(rsi12 >= 50 and rsi6 >= rsi12) if direction > 0 else int(rsi12 <= 50 and rsi6 <= rsi12)
        macd_zero_axis_region = (
            "above" if latest_dif > 0 and latest_dea > 0 else
            "below" if latest_dif < 0 and latest_dea < 0 else "mixed"
        )
        metrics[timeframe] = {
            "ma5": round(ma5, 6), "ma10": round(ma10, 6), "ma20": round(ma20, 6),
            "macd_dif": round(latest_dif, 6), "macd_dea": round(latest_dea, 6),
            "macd_histogram": round(histogram, 6),
            "macd_zero_axis_region": macd_zero_axis_region,
            "last_input_candle_time": pd.Timestamp(frame.iloc[-1]["date"]).isoformat(),
            "rsi6": round(rsi6, 4),
            "rsi12": round(rsi12, 4), "rsi24": round(rsi24, 4),
            "volume_ratio": round(volume_ratio, 4),
            "ma_vote": ma_vote, "macd_vote": macd_vote, "rsi_vote": rsi_vote,
            "votes": ma_vote + macd_vote + rsi_vote,
            "market_context": exchange_market_context(frame, direction),
        }
    rsi6 = float(metrics["1m"]["rsi6"])
    if (direction > 0 and rsi6 >= 78.0) or (direction < 0 and rsi6 <= 22.0):
        return False, f"一分钟RSI6={rsi6:.2f}已进入追价极端区，等待回踩/反抽", metrics
    volume_ratio = float(metrics["1m"]["volume_ratio"])
    if volume_ratio < minimum_volume_ratio:
        return False, (f"一分钟已收盘成交量仅为前20根中位数的{volume_ratio:.2f}倍，"
                       f"低于{minimum_volume_ratio:.2f}倍，缺少参与度确认"), metrics
    one_context = metrics["1m"]["market_context"]
    five_context = metrics["5m"]["market_context"]
    confirmed_price_reversal = bool(
        price_structure_override and entry_class == "reversal" and (
            direction < 0
            and one_context["liquidity_sweep"] == "high_reject"
            and float(one_context["range_position"]) >= .75
            and int(five_context["choch_direction"]) == -1
            or direction > 0
            and one_context["liquidity_sweep"] == "low_reclaim"
            and float(one_context["range_position"]) <= .25
            and int(five_context["choch_direction"]) == 1
        )
    )
    # Context is auxiliary: it cannot create a candidate and vetoes only when
    # both short timeframes independently show the same three-way conflict.
    strong_context_conflicts = []
    for timeframe in ("1m", "5m"):
        item = metrics[timeframe]
        context = item["market_context"]
        zero_axis_opposes = (
            direction > 0 and item["macd_zero_axis_region"] == "below" or
            direction < 0 and item["macd_zero_axis_region"] == "above"
        )
        strong_context_conflicts.append(bool(
            context["supertrend_direction"] == -direction and
            context["structure_bias"] == -direction and zero_axis_opposes
        ))
    if all(strong_context_conflicts) and not confirmed_price_reversal:
        return False, ("1m与5m超级趋势、已确认价格结构及MACD零轴区域同时反向；"
                       "v0.7.83多维强冲突辅助门拒绝候选"), metrics
    votes = {timeframe: int(item["votes"]) for timeframe, item in metrics.items()}
    opposing_fifteen = votes["15m"] == 0
    if entry_class == "reversal":
        allowed = confirmed_price_reversal or (votes["1m"] >= 2 and not opposing_fifteen)
        rule = "反转要求1m至少2/3同向且15m不得三项全反向"
    elif entry_class == "continuation":
        allowed = continuation_indicator_votes_allowed(
            votes, confirmed_uptrend_pullback=confirmed_uptrend_pullback and direction > 0)
        rule = ("已确认上涨回踩允许1m/5m各至少2票、15m至少1票；其余延续单仍须合计6/9"
                if confirmed_uptrend_pullback and direction > 0 else
                "延续要求三周期各至少1票且合计至少6/9")
    else:
        allowed = votes["1m"] >= 2 and votes["5m"] >= 1 and sum(votes.values()) >= 5
        rule = "普通候选要求1m至少2票、5m至少1票且合计至少5/9"
    summary = f"1m/5m/15m指标票数={votes['1m']}/{votes['5m']}/{votes['15m']}"
    if confirmed_price_reversal:
        rule = "price-structure reversal override: 1m extreme rejection/reclaim and aligned 5m CHoCH"
    if not allowed:
        return False, f"{rule}；{summary}，候选仅记录不下单", metrics
    return True, f"{rule}；{summary}，成交量比={volume_ratio:.2f}，技术指标确认通过", metrics


def three_point_launch_stop(entry: float, direction: int, structural_stop: float,
                            *, maximum_points: float = 3.0) -> tuple[float, bool, str]:
    """Cap an already-qualified reversal/launch entry at three ETH points."""
    if direction not in {-1, 1} or entry <= 0 or maximum_points <= 0:
        raise ValueError("three-point launch stop inputs are invalid")
    structural_risk = ((entry - structural_stop) if direction > 0 else
                       (structural_stop - entry))
    if 0 < structural_risk <= maximum_points:
        return round(structural_stop, 2), False, f"原结构止损{structural_risk:.2f}点，不超过3点，继续使用"
    fixed = entry - maximum_points if direction > 0 else entry + maximum_points
    original = f"{structural_risk:.2f}点" if structural_risk > 0 else "方向无效"
    return round(fixed, 2), True, (f"原结构止损{original}，反转区/趋势启动固定按下单位置"
                                  f"{'下方' if direction > 0 else '上方'}3点保护")


def strategy01_contracts_for_stop(
    configured_contracts: int, stop_distance_pct: float, baseline_stop_pct: float,
) -> int:
    """Keep the configured risk budget constant as the structural stop widens."""
    if configured_contracts <= 0 or stop_distance_pct <= 0 or baseline_stop_pct <= 0:
        raise ValueError("position sizing inputs must be positive")
    calculated = int(configured_contracts * baseline_stop_pct / stop_distance_pct)
    # OKX SWAP does not accept a fractional contract.  A one-contract test
    # account therefore stays at one; wider configured sizes scale down.
    return 1 if configured_contracts == 1 else max(0, calculated)


def safe_strategy01_contracts_for_stop(
    configured_contracts: int, stop_distance_pct: float, baseline_stop_pct: float,
) -> int | None:
    """Reject an invalid candidate without fault-stopping the automatic loop."""
    if configured_contracts <= 0 or stop_distance_pct <= 0 or baseline_stop_pct <= 0:
        return None
    return strategy01_contracts_for_stop(
        configured_contracts, stop_distance_pct, baseline_stop_pct,
    )


@dataclass(frozen=True)
class ValidationExecutionResult:
    action: str
    reason: str
    order_id: str = ""
    direction: int = 0
    entry_reference: float = 0.0
    take_profit: float = 0.0
    stop_loss: float = 0.0


def _recent_candles_frame(rows: list[list[str]], instrument: str) -> pd.DataFrame:
    values = []
    for candle in rows:
        if len(candle) < 9:
            continue
        timestamp, open_, high, low, close, volume = candle[:6]
        values.append((pd.to_datetime(int(timestamp), unit="ms", utc=True).tz_localize(None),
                       instrument, open_, high, low, close, volume))
    if not values:
        return pd.DataFrame(columns=("date", "symbol", "open", "high", "low", "close", "volume"))
    return pd.DataFrame(values, columns=(
        "date", "symbol", "open", "high", "low", "close", "volume"))


def execute_five_second_top_short_tick(
        cfg: AppConfig, database: str | Path, *, client: OkxDemoClient,
        public_client: OkxDemoClient | None = None,
) -> ValidationExecutionResult:
    """Run the isolated top-short path without entering the full strategy audit.

    This function deliberately performs one public candle read, two concurrent
    private safety reads and one order POST.  Hourly replay, PineTS research,
    full protection reconciliation and higher-timeframe scans remain on the
    normal worker.
    """
    started = time.perf_counter()
    instrument = cfg.okx.instruments[0]
    public = public_client or OkxDemoClient(timeout=2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        one_future = executor.submit(
            public.current_candles, instrument, bar="1m", limit=30)
        five_future = executor.submit(
            public.current_candles, instrument, bar="5m", limit=30)
        one_rows, five_rows = one_future.result(), five_future.result()
    signal = five_second_top_short_signal(
        _recent_candles_frame(one_rows, instrument),
        _recent_candles_frame(five_rows, instrument))
    if signal is None:
        with _FAST_SHORT_CONFIRMATION_LOCK:
            prefix = f"{Path(database)}:{instrument}:"
            for key in tuple(_FAST_SHORT_CONFIRMATIONS):
                if key.startswith(prefix):
                    _FAST_SHORT_CONFIRMATIONS.pop(key, None)
        return ValidationExecutionResult(
            "observe", "5秒快空：尚无顶部确认或次高点反抽转弱")
    if signal.setup_type == "lower_high_throwback_weakening":
        confirmation_key = (f"{Path(database)}:{instrument}:"
                            f"{signal.anchor_time}:{signal.signal_time}")
        now = time.monotonic()
        with _FAST_SHORT_CONFIRMATION_LOCK:
            seen_at, count = _FAST_SHORT_CONFIRMATIONS.get(
                confirmation_key, (now, 0))
            count = count + 1 if now - seen_at <= 3.0 else 1
            _FAST_SHORT_CONFIRMATIONS[confirmation_key] = (now, count)
        if count < 2:
            return ValidationExecutionResult(
                "observe", "次高点盘中首次转弱待下一秒复核，防止未收盘K线瞬时翻阴后收阳")
    recognized = time.perf_counter()
    event_id = f"fast-top:{signal.anchor_time}"
    client_order_id = stable_client_order_id("QBFST", event_id, "S", reserve=1)
    store = StateStore(database)
    try:
        if store.has_open_same_side_trade("strategy_01", instrument, -1):
            return ValidationExecutionResult("manage", "5秒顶部快线：已有空头生命周期")
        snapshot = client.fast_entry_snapshot(instrument)
        same_side_position = any(
            str(item.get("posSide") or "").lower() == "short"
            and abs(float(item.get("pos") or 0)) > 0
            for item in snapshot.get("positions", []))
        same_side_order = any(
            str(item.get("posSide") or "").lower() == "short"
            and str(item.get("side") or "").lower() == "sell"
            for item in snapshot.get("orders", []))
        if same_side_position or same_side_order:
            return ValidationExecutionResult("manage", "5秒顶部快线：已有空头仓位或开仓挂单")
        if not store.claim_reversal_trial(instrument, -1, event_id, client_order_id):
            return ValidationExecutionResult("duplicate", "5秒顶部快线：该局部顶部已经处理")
        stop_distance_pct = (signal.stop - signal.entry) / signal.entry
        contracts = safe_strategy01_contracts_for_stop(
            cfg.okx.validation_contracts, stop_distance_pct,
            cfg.okx.validation_stop_loss_pct)
        if contracts != 1:
            store.record_event("five_second_fast_short_rejected", {
                "strategy_version": VALIDATION_VERSION, "event_id": event_id,
                "reason": "局部三K高点止损超出一张合约风险预算",
                "entry": signal.entry, "stop": signal.stop,
            })
            return ValidationExecutionResult("observe", "5秒顶部快线：局部止损风险预算不允许下单")
        intent = SignalIntent(
            instrument, signal.signal_time, -1, VALIDATION_VERSION,
            stop_distance_pct, "pending")
        if not store.record_intent(intent):
            return ValidationExecutionResult("duplicate", "5秒顶部快线：该转弱K线已经处理")
        try:
            response = client.place_demo_market_order(
                "sell", 1, f"{signal.stop:.2f}", enabled=True,
                max_contracts=1, confirmation="DEMO-ORDER", inst_id=instrument,
                client_order_id=client_order_id, position_side="short",
                stop_loss_trigger_type="mark")
        except Exception:
            store.update_intent_status(intent, "rejected")
            raise
        order = (response.get("data") or [{}])[0]
        order_id = str(order.get("ordId") or "")
        if str(response.get("code")) != "0" or not order_id:
            store.update_intent_status(intent, "rejected")
            raise OkxError("5秒顶部快线开仓回执缺少ordId；按clOrdId对账，禁止自动重发")
        store.update_intent_status(intent, "submitted")
        completed = time.perf_counter()
        recognition_to_ack_ms = round((completed - recognized) * 1000, 2)
        scan_to_ack_ms = round((completed - started) * 1000, 2)
        target = round(signal.entry - max(signal.stop - signal.entry, 2.0), 2)
        context = {
            "execution_observed_at": datetime.now(timezone.utc).isoformat(),
            "review_anchor_time": signal.anchor_time,
            "primary_trigger_timeframe": "1m",
            "signal_owner": "five_second_top_weakening_short",
            "entry_classification_code": "local_endpoint_reversal_short",
            "entry_classification_category": "local_endpoint_reversal",
            "fast_short_setup_type": signal.setup_type,
            "consecutive_bearish_candles": signal.bearish_count,
            "ma5_exit_timeframe": "1m", "five_minute_ma5_core_hold": False,
            "protection_mode": "ma5_turn", "take_profit_rule": "1分钟MA5由下降转为上拐止盈",
            "cover_ratio": signal.cover_ratio, "ma5": signal.ma5,
            "one_minute_ma20": signal.ma20,
            "five_minute_ma5": signal.five_minute_ma5,
            "five_minute_ma10": signal.five_minute_ma10,
            "five_minute_ma20": signal.five_minute_ma20,
            "primary_high": signal.primary_high,
            "secondary_high": signal.secondary_high,
            "three_candle_stop": signal.stop, "recognition_to_ack_ms": recognition_to_ack_ms,
            "five_second_budget_met": recognition_to_ack_ms <= 5000,
        }
        if signal.setup_type == "lower_high_throwback_weakening":
            reason = (f"高位次高点反抽后连续{signal.bearish_count}根阴线转弱，"
                      "无需覆盖前阳线或下穿1分钟MA5；本轮三根K线高点外止损")
        else:
            reason = (f"新局部最高点阴线覆盖前阳线{signal.cover_ratio:.0%}，"
                      "回到1分钟MA5内侧；最近三根1分钟K线高点外止损")
        store.open_trade_lifecycle(
            trade_uid=client_order_id, strategy_id="strategy_01",
            strategy_version=VALIDATION_VERSION, instrument=instrument, direction=-1,
            signal_time=signal.signal_time, signal_reason=reason, signal_context=context,
            order_id=order_id, algo_id="", entry_reference=signal.entry,
            stop_price=signal.stop, trailing_activation=target, trailing_callback=0.0,
            branch="five_second_top_weakening_short")
        store.record_event("five_second_fast_short_submitted", {
            "strategy_version": VALIDATION_VERSION, "event_id": event_id,
            "clOrdId": client_order_id, "ordId": order_id,
            "anchor_time": signal.anchor_time, "signal_time": signal.signal_time,
            "entry": signal.entry, "stop": signal.stop,
            "cover_ratio": signal.cover_ratio, "setup_type": signal.setup_type,
            "bearish_count": signal.bearish_count,
            "recognition_to_ack_ms": recognition_to_ack_ms,
            "scan_to_ack_ms": scan_to_ack_ms,
            "within_five_seconds": recognition_to_ack_ms <= 5000,
        })
        return ValidationExecutionResult(
            "submitted", f"5秒顶部快线做空已提交，识别至回执{recognition_to_ack_ms:.0f}毫秒",
            order_id, -1, signal.entry, target, signal.stop)
    finally:
        store.close()


def submit_one_ma5_retest_limit(
        *, store: StateStore, client: OkxDemoClient, snapshot: dict,
        instrument: str, direction: int, stage_time,
        plan: MissedMa5PullbackPlan, atr: float, stage: str,
        event_type: str, context_reason: str = "",
) -> ValidationExecutionResult:
    """Deduplicate and submit a single bounded retest using the frozen stop."""
    if same_side_entry_conflicts(snapshot, direction):
        return ValidationExecutionResult(
            "manage", "MA5反抽限价暂停：已有同向仓位或挂单")
    if store.has_open_same_side_trade(
            "strategy_01", instrument, direction, strategy_version=VALIDATION_VERSION):
        return ValidationExecutionResult(
            "manage", "MA5反抽限价暂停：已有同向实盘生命周期")
    stage_time = pd.Timestamp(stage_time)
    suffix = "L" if direction > 0 else "S"
    client_order_id = MISSED_MA5_PULLBACK_PREFIX + stage_time.strftime("%Y%m%d%H%M%S") + suffix
    intent = SignalIntent(
        instrument, stage_time.isoformat(), direction, VALIDATION_VERSION,
        abs(plan.entry - plan.stop) / plan.entry, "pending")
    if not store.record_intent(intent):
        return ValidationExecutionResult("duplicate", "本轮MA5反抽机会已处理，禁止重复挂单")
    try:
        response = client.place_demo_sniper_limit_order(
            "buy" if direction > 0 else "sell", 1,
            f"{plan.entry:.2f}", f"{plan.stop:.2f}", f"{plan.target:.2f}",
            enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
            inst_id=instrument, position_side="long" if direction > 0 else "short",
            client_order_id=client_order_id)
    except Exception:
        store.update_intent_status(intent, "rejected")
        raise
    order = (response.get("data") or [{}])[0]
    if (str(response.get("code")) != "0" or str(order.get("sCode")) != "0"
            or not order.get("ordId")):
        raise ValueError("pullback limit acknowledgement uncertain; reconcile before retry")
    store.update_intent_status(intent, "submitted")
    register_pullback(
        store, client_id=client_order_id, order_id=str(order["ordId"]),
        instrument=instrument, direction=direction, entry=plan.entry,
        stop=plan.stop, target=plan.target, atr=atr,
        size=getattr(client, "opening_unit_contracts", "1"),
        version=VALIDATION_VERSION, signal_time=stage_time.isoformat())
    store.record_event(event_type, {
        "strategy_version": VALIDATION_VERSION, "ordId": order["ordId"],
        "clOrdId": client_order_id, "direction": direction, "stage": stage,
        "stage_time": stage_time.isoformat(), "entry": plan.entry,
        "stop": plan.stop, "target": plan.target,
        "ttl_seconds": MISSED_MA5_PULLBACK_TTL_SECONDS,
        "reason": f"{plan.reason}; {context_reason}",
    })
    return ValidationExecutionResult(
        "pullback_limit_submitted",
        f"错过市价窗口，原MA5附近单次限价等待{MISSED_MA5_PULLBACK_TTL_SECONDS}秒，挂价{plan.entry:.2f}",
        str(order["ordId"]))


def validation_exit_prices(mark_price: float, direction: int, take_profit_pct: float, stop_loss_pct: float) -> tuple[float, float]:
    if direction not in {-1, 1}:
        raise ValueError("validation direction must be long or short")
    mark = Decimal(str(mark_price))
    tick = Decimal("0.01")
    tp_pct = Decimal(str(take_profit_pct))
    sl_pct = Decimal(str(stop_loss_pct))
    if direction > 0:
        take_profit = (mark * (1 + tp_pct)).quantize(tick, rounding=ROUND_UP)
        stop_loss = (mark * (1 - sl_pct)).quantize(tick, rounding=ROUND_DOWN)
    else:
        take_profit = (mark * (1 - tp_pct)).quantize(tick, rounding=ROUND_DOWN)
        stop_loss = (mark * (1 + sl_pct)).quantize(tick, rounding=ROUND_UP)
    return float(take_profit), float(stop_loss)


def breakout_long_protection(entry_price: float, retest_stop: float, one_minute_market) -> tuple[float, float, float]:
    """Protection tailored to a confirmed breakout-retest long.

    The stop belongs to the 1m retest structure rather than the older/wider
    eight-bar 5m range.  With a single contract there is no partial exit, so
    trailing protection starts at 1R (but never below the existing 0.20%
    minimum) and uses half a 1m ATR to avoid a fixed ultra-tight callback.
    """
    if not 0 < retest_stop < entry_price:
        raise ValueError("breakout retest stop must be below entry")
    one = one_minute_market.sort_values("date").tail(15)
    if len(one) < 3:
        raise ValueError("not enough 1m candles for breakout protection")
    high, low, close = one["high"].astype(float), one["low"].astype(float), one["close"].astype(float)
    previous = close.shift(1)
    one_atr = float(pd.concat(((high - low), (high - previous).abs(), (low - previous).abs()), axis=1)
                    .max(axis=1).tail(14).mean())
    risk = entry_price - retest_stop
    activation = entry_price + max(risk, entry_price * .0020)
    callback = max(entry_price * .0005, one_atr * .50)
    return round(retest_stop, 2), round(activation, 2), round(callback, 2)


def hierarchical_entry_direction(signals, five_minute=None) -> tuple[int, str]:
    """Describe structure hierarchy; the final order still needs the 5m gate."""
    structure = five_minute_price_structure_regime(five_minute) if five_minute is not None else None
    anchor = structure.direction if structure is not None else signals["5m"].direction
    confirmation = signals["5m"].direction
    trigger = signals["1m"]
    arrow = {1: "↑", -1: "↓", 0: "→"}
    fifteen_direction = int(signals.get("15m").direction) if signals.get("15m") else 0
    hour_direction = int(signals.get("1H").direction) if signals.get("1H") else 0
    summary = (f"1H{arrow[hour_direction]}｜15m{arrow[fifteen_direction]}｜"
               f"5m{arrow[confirmation]}｜1m辅助{arrow[trigger.direction]}")
    if anchor == 0:
        detail = structure.reason if structure is not None else "5分钟趋势不明确"
        return 0, f"{summary}；{detail}"
    trend_name = "上涨" if anchor > 0 else "下跌"
    if not trigger.entry_confirmed:
        return 0, (f"{summary}；5分钟结构保持{trend_name}；当前尚无已启用的五分钟收盘或盘中独立触发，"
                   "一分钟仅作通用分支辅助")
    source = structure.reason if structure is not None else f"5分钟{trend_name}"
    return anchor, f"{summary}；{source}；一分钟仅提供辅助证据，仍须通过五分钟主触发"


def validation_market_context(cfg: AppConfig):
    signals = {}
    markets = {}
    bars = ("1m", "5m", "15m", "1H", "4H")
    instrument = cfg.okx.instruments[0]
    # All nine public reads are independent. Dispatch them in one wave; the
    # previous two-wave/three-worker pool regularly spent 9-24 seconds before
    # the strategy could even inspect the newest one-minute candle.
    live_bars = ("1m", "5m", "15m", "1H")
    with ThreadPoolExecutor(max_workers=9, thread_name_prefix="market-fast") as pool:
        closed_futures = {
            bar: pool.submit(okx_history_market, (instrument,), bar, 100)
            for bar in bars
        }
        live_futures = {
            bar: pool.submit(okx_current_unconfirmed_market, instrument, bar)
            for bar in live_bars
        }
        closed = [closed_futures[bar].result() for bar in bars]
        live = [live_futures[bar].result() for bar in live_bars]
    for bar, market in zip(bars, closed):
        markets[bar] = market
        signals[bar] = latest_timeframe_signal(market, bar, fast_window=5, slow_window=10)
    markets.update(zip(("1m_live", "5m_live", "15m_live", "1H_live"), live))
    if markets["1H_live"] is not None and not markets["1H_live"].empty:
        hour_with_live = pd.concat((markets["1H"], markets["1H_live"]), ignore_index=True)
        hour_with_live = hour_with_live.sort_values("date").drop_duplicates("date", keep="last")
        signals["1H"] = latest_timeframe_signal(hour_with_live, "1H", fast_window=5, slow_window=10)
    summary = "｜".join(
        timeframe_direction_label(bar, signals[bar], markets[bar], markets.get(f"{bar}_live"))
        for bar in ("4H", "1H", "15m", "5m", "1m")
    )
    return signals, markets, summary


def waterfall_micro_pullback_stop(one_minute: pd.DataFrame, five_minute: pd.DataFrame,
                                  fifteen_minute: pd.DataFrame, entry_price: float,
                                  direction: int) -> tuple[bool, str, float]:
    """Use the latest 1m pullback extreme once a three-period cascade is aligned."""
    if direction not in {-1, 1} or min(len(one_minute), len(five_minute), len(fifteen_minute)) < 21:
        return False, "瀑布微型止损：周期数据不足", 0.0
    frames = []
    for source in (one_minute, five_minute, fifteen_minute):
        frame = source.sort_values("date").copy()
        for window in (5, 10, 20):
            frame[f"ma{window}"] = frame["close"].astype(float).rolling(window).mean()
        frames.append(frame)
    one, five, fifteen = frames
    if direction < 0:
        aligned = all(float(frame.iloc[-1]["ma5"]) < float(frame.iloc[-1]["ma10"]) <
                      float(frame.iloc[-1]["ma20"]) for frame in (one, five, fifteen))
        resumed = float(one.iloc[-1]["close"]) < float(one.iloc[-1]["ma5"])
        extreme = float(one.tail(5)["high"].astype(float).max())
    else:
        aligned = all(float(frame.iloc[-1]["ma5"]) > float(frame.iloc[-1]["ma10"]) >
                      float(frame.iloc[-1]["ma20"]) for frame in (one, five, fifteen))
        resumed = float(one.iloc[-1]["close"]) > float(one.iloc[-1]["ma5"])
        extreme = float(one.tail(5)["low"].astype(float).min())
    if not aligned or not resumed:
        return False, "瀑布微型止损：三周期排列或一分钟恢复条件未成立", 0.0
    atr1 = latest_atr(one)
    buffer = max(atr1 * .08, entry_price * .0001)
    stop = extreme + buffer if direction < 0 else extreme - buffer
    valid = stop > entry_price if direction < 0 else stop < entry_price
    if not valid:
        return False, "瀑布微型止损：最新微型高低点不在保护方向", 0.0
    return True, (f"瀑布顺势续单：1/5/15分钟均线同向，使用最近5根一分钟微型"
                  f"{'高点' if direction < 0 else '低点'}保护"), round(stop, 2)


def reversal_range_location_gate(one_minute: pd.DataFrame, five_minute: pd.DataFrame,
                                 price: float, direction: int) -> tuple[bool, str]:
    """Retained as an audit description; v102 removes the 38% execution veto."""
    if direction not in {-1, 1} or len(one_minute) < 12:
        return False, "反转位置门：局部区间数据不足"
    recent = one_minute.sort_values("date").tail(12)
    low = float(recent["low"].astype(float).min())
    high = float(recent["high"].astype(float).max())
    width = high - low
    if width <= 0:
        return False, "反转位置门：1分钟局部区间无效"
    position = (price - low) / width
    return True, (f"一分钟局部位置{position * 100:.0f}%"
                  f"（低{low:.2f}/高{high:.2f}）仅作审计；38%门槛已取消，"
                  "一分钟有效结构自行决定入场，5分钟和15分钟只负责验证与止盈升级")


def ma5_local_extreme_anchor(one_minute: pd.DataFrame, direction: int) -> float:
    """Capture the MA5 plateau that must be revisited before an early exit."""
    one = one_minute.sort_values("date")
    if direction not in {-1, 1} or len(one) < 8:
        return 0.0
    ma5 = one["close"].astype(float).rolling(5).mean().tail(12).dropna().reset_index(drop=True)
    if ma5.empty:
        return 0.0
    turning = []
    for index in range(1, len(ma5) - 1):
        if direction < 0 and ma5.iloc[index] >= ma5.iloc[index - 1] and ma5.iloc[index] > ma5.iloc[index + 1]:
            turning.append(float(ma5.iloc[index]))
        if direction > 0 and ma5.iloc[index] <= ma5.iloc[index - 1] and ma5.iloc[index] < ma5.iloc[index + 1]:
            turning.append(float(ma5.iloc[index]))
    if turning:
        return round(turning[-1], 2)
    fallback = ma5.tail(6)
    return round(float(fallback.max() if direction < 0 else fallback.min()), 2)


def ma5_anchor_allows_exit(one_minute: pd.DataFrame, direction: int, anchor: float) -> bool:
    """Retired v0.7.62 anchor lock: valid profit exits are never delayed."""
    return True


def dual_timeframe_ma20_cross_continuation_setup(
    one_closed: pd.DataFrame, one_live: pd.DataFrame,
    five_closed: pd.DataFrame, five_live: pd.DataFrame,
) -> tuple[int, str, float]:
    """Catch the launch where 1m and 5m MA5/MA10 jointly cross MA20."""
    def compose(closed, live):
        frame = closed.sort_values("date").copy()
        if live is not None and not live.empty:
            frame = pd.concat((frame, live), ignore_index=True)
            frame = frame.sort_values("date").drop_duplicates("date", keep="last")
        return frame.reset_index(drop=True)

    one, five = compose(one_closed, one_live), compose(five_closed, five_live)
    if len(one) < 24 or len(five) < 24:
        return 0, "双周期金叉/死叉启动等待足够K线", 0.0

    def state(frame, direction):
        close = frame["close"].astype(float)
        ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
        ordered = (
            direction * (ma5 - ma20) > 0
        ) & (
            direction * (ma10 - ma20) > 0
        ) & (
            direction * (ma5 - ma10) > 0
        )
        now = bool(ordered.iloc[-1])
        opening = abs(float(ma5.iloc[-1] - ma20.iloc[-1])) > abs(float(ma5.iloc[-2] - ma20.iloc[-2]))
        transitions = frame.loc[ordered & ~ordered.shift(1, fill_value=False), "date"]
        cross_time = pd.to_datetime(transitions.iloc[-1]) if not transitions.empty else None
        latest = frame.iloc[-1]
        directional_candle = direction * (float(latest["close"]) - float(latest["open"])) > 0
        price_through_ma5 = direction * (float(latest["close"]) - float(ma5.iloc[-1])) >= 0
        return (now and opening and directional_candle and price_through_ma5,
                cross_time, ma5, ma10, ma20)

    for direction in (-1, 1):
        one_ok, one_cross, one_ma5, one_ma10, one_ma20 = state(one, direction)
        five_ok, five_cross, five_ma5, five_ma10, five_ma20 = state(five, direction)
        if not one_ok or one_cross is None:
            continue
        one_now = pd.to_datetime(one.iloc[-1]["date"])
        one_cross_age = one_now - one_cross
        prior_structure = one.iloc[-9:-3]
        latest_structure = one.iloc[-3:]
        if direction < 0:
            structure_ready = (
                float(latest_structure["high"].astype(float).max())
                < float(prior_structure["high"].astype(float).max())
                and float(one.iloc[-1]["close"]) < float(one.iloc[-4]["close"])
            )
            structure_text = "一分钟局部高点下移且价格继续走低"
        else:
            structure_ready = (
                float(latest_structure["low"].astype(float).min())
                > float(prior_structure["low"].astype(float).min())
                and float(one.iloc[-1]["close"]) > float(one.iloc[-4]["close"])
            )
            structure_text = "一分钟局部低点抬高且价格继续走高"
        if not structure_ready:
            continue
        confirmed = False
        cross_gap = None
        if five_ok and five_cross is not None:
            cross_gap = abs(one_cross - five_cross)
            later_cross = max(one_cross, five_cross)
            current_time = max(one_now, pd.to_datetime(five.iloc[-1]["date"]))
            confirmed = (cross_gap <= pd.Timedelta(minutes=15)
                         and current_time - later_cross <= pd.Timedelta(minutes=5))
        early_probe = pd.Timedelta(0) <= one_cross_age <= pd.Timedelta(minutes=2)
        if not (confirmed or early_probe):
            continue
        atr1 = latest_atr(one)
        price = float(one.iloc[-1]["close"])
        distance = abs(price - float(five_ma20.iloc[-1]))
        atr5 = latest_atr(five)
        if atr5 <= 0 or distance > atr5 * .75:
            return 0, f"双周期交叉已成立，但距五分钟MA20为{distance / max(atr5, 1e-9):.2f} ATR，启动点已过期", 0.0
        buffer = max(atr1 * .12, price * .0003)
        stop = (float(one.tail(6)["high"].astype(float).max()) + buffer if direction < 0
                else float(one.tail(6)["low"].astype(float).min()) - buffer)
        name = "死叉追空" if direction < 0 else "金叉追多"
        market_action = "局部小止损市价做空" if direction < 0 else "局部小止损市价做多"
        timing = (f"一分钟交叉={one_cross:%H:%M}先行{market_action}，不等待五分钟交叉；"
                  if not confirmed else
                  f"一分钟交叉={one_cross:%H:%M}，五分钟交叉={five_cross:%H:%M}，"
                  f"相差{cross_gap.total_seconds() / 60:.0f}分钟≤15分钟，升级趋势持有；")
        reason = (
            f"{'一分钟先行' if not confirmed else '双周期确认'}{name}独立启动：{timing}"
            f"{structure_text}；MA5、MA10已{'下穿' if direction < 0 else '上穿'}MA20并发散，当前"
            f"{'阴线下穿' if direction < 0 else '阳线上穿'}MA5确认；"
            f"距五分钟MA20={distance / atr5:.2f} ATR，止损放最近一分钟局部"
            f"{'高点' if direction < 0 else '低点'}外")
        return direction, reason, round(stop, 2)
    return 0, "等待一分钟MA5/MA10交叉MA20后先行局部小止损市价做空/做多；五分钟在15分钟内同向交叉后升级持有", 0.0


def multi_timeframe_weakness_continuation_short_setup(
    one_closed: pd.DataFrame, five_closed: pd.DataFrame,
) -> tuple[int, str, float]:
    """Confirm a continuation short after a 5m bearish-cover sequence."""
    one = one_closed.sort_values("date").reset_index(drop=True)
    five = five_closed.sort_values("date").reset_index(drop=True)
    if len(one) < 24 or len(five) < 24:
        return 0, "多周期走弱中续：K线数据不足", 0.0
    f = five.tail(4).reset_index(drop=True)
    bullish = float(f.iloc[0]["close"]) > float(f.iloc[0]["open"])
    cover = (float(f.iloc[1]["close"]) < float(f.iloc[1]["open"])
             and float(f.iloc[1]["open"]) >= float(f.iloc[0]["close"])
             and float(f.iloc[1]["close"]) <= float(f.iloc[0]["open"]))
    follow_through = all(float(f.iloc[index]["close"]) < float(f.iloc[index]["open"])
                         for index in (2, 3))
    f_close = five["close"].astype(float)
    f_ma5, f_ma10, f_ma20 = (f_close.rolling(window).mean() for window in (5, 10, 20))
    five_small_death = (f_ma5.iloc[-2] >= f_ma10.iloc[-2]
                        and f_ma5.iloc[-1] < f_ma10.iloc[-1])
    if not (bullish and cover and follow_through and five_small_death):
        return 0, "等待5分钟阴线覆盖阳线、后续两根阴线及MA5/MA10小死叉", 0.0
    o_close = one["close"].astype(float)
    o_ma5, o_ma10, o_ma20 = (o_close.rolling(window).mean() for window in (5, 10, 20))
    small_crosses = (o_ma5 < o_ma10) & (o_ma5.shift(1) >= o_ma10.shift(1))
    recent_small = bool(small_crosses.tail(8).any())
    big_death = (o_ma5.iloc[-1] < o_ma10.iloc[-1] < o_ma20.iloc[-1]
                 and abs(float(o_ma5.iloc[-1] - o_ma20.iloc[-1]))
                 > abs(float(o_ma5.iloc[-2] - o_ma20.iloc[-2]))
                 and float(o_close.iloc[-1]) < float(o_ma5.iloc[-1]))
    if not (recent_small and big_death):
        return 0, "5分钟已走弱，等待1分钟小死叉后形成三均线大死叉", 0.0
    atr5 = latest_atr(five)
    distance = abs(float(o_close.iloc[-1]) - float(f_ma20.iloc[-1]))
    if atr5 <= 0 or distance > atr5 * .90:
        return 0, f"多周期走弱成立，但距5分钟MA20为{distance / max(atr5, 1e-9):.2f} ATR，禁止低位追空", 0.0
    atr1 = latest_atr(one)
    stop = float(one.tail(6)["high"].astype(float).max()) + max(atr1 * .15, float(o_close.iloc[-1]) * .0002)
    return -1, ("5分钟阴线覆盖阳线后连续两根阴线并形成小死叉；"
                "1分钟先小死叉、后形成MA5<MA10<MA20大死叉，确认上涨无力和回踩中续，"
                "允许局部小止损市价做空；若结构止损超过3点则使用下单位置上方3点"), round(stop, 2)


def five_minute_bearish_rollover_continuation_short_setup(
        one_closed: pd.DataFrame, five_closed: pd.DataFrame,
        five_live: pd.DataFrame | None, now) -> tuple[int, str, float]:
    """Continue a frozen decline on a new bearish 5m bar without engulfment."""
    one = one_closed.sort_values("date").reset_index(drop=True)
    five = five_closed.sort_values("date").reset_index(drop=True)
    if five_live is None or five_live.empty or len(one) < 20 or len(five) < 20:
        return 0, "下降连续阴线追空：周期数据不足", 0.0
    live = five_live.sort_values("date").iloc[-1]
    live_time = pd.to_datetime(live["date"], utc=True)
    current_time = pd.to_datetime(now, utc=True)
    age = (current_time - live_time).total_seconds()
    if age < 0 or age > 120:
        return 0, "下降连续阴线追空：仅在五分钟换线后前2分钟确认", 0.0
    previous = five.iloc[-1]
    prior = five.iloc[-2]
    consecutive_five_bears = bool(
        float(prior["close"]) < float(prior["open"])
        and float(previous["close"]) < float(previous["open"])
        and float(previous["close"]) < float(prior["close"])
        and float(live["close"]) < float(live["open"])
        and float(live["close"]) < float(previous["close"]))
    f_close = five["close"].astype(float)
    f_ma5, f_ma10 = f_close.rolling(5).mean(), f_close.rolling(10).mean()
    five_decline = bool(f_ma5.iloc[-1] < f_ma10.iloc[-1]
                        and f_ma5.iloc[-1] < f_ma5.iloc[-2]
                        and f_ma10.iloc[-1] <= f_ma10.iloc[-2])
    recent_one = one.tail(4)
    bearish_count = int((recent_one["close"].astype(float)
                         < recent_one["open"].astype(float)).sum())
    one_close = one["close"].astype(float)
    o_ma5, o_ma10 = one_close.rolling(5).mean(), one_close.rolling(10).mean()
    one_decline = bool(bearish_count >= 3
                       and float(one_close.iloc[-1]) < float(one_close.iloc[-3])
                       and o_ma5.iloc[-1] < o_ma10.iloc[-1]
                       and o_ma5.iloc[-1] < o_ma5.iloc[-2])
    if not (consecutive_five_bears and five_decline and one_decline):
        return 0, "下降连续阴线追空：等待5分钟连续阴线、换线续阴及1分钟同步走弱", 0.0
    entry = float(live["close"])
    atr1 = latest_atr(one)
    body_high = float(one.tail(5)[["open", "close"]].astype(float).max(axis=1).max())
    stop = body_high + max(atr1 * .15, entry * .0002)
    if stop <= entry:
        return 0, "下降连续阴线追空：一分钟实体高点不能形成有效止损", 0.0
    return -1, (
        "下降趋势连续阴线续单：此前反抽追空结果继续冻结；连续两根已收盘5分钟阴线降低，"
        "新五分钟换线后前2分钟继续走阴，且最近4根一分钟至少3根阴线并同步走弱；"
        "不要求当前5分钟阴线覆盖前一根阳线，仍执行风险、ATR、手续费和利润空间检查"
    ), round(stop, 2)


def timeframe_direction_label(bar: str, signal, market, live=None) -> str:
    """Show MA20 turning state instead of mislabelling it as a settled trend.

    The execution signal still uses the established MA5/MA10 ordering.  This
    label additionally exposes the closed-candle MA20 slope, so a residual
    MA5>MA10 during a rollover is displayed as a bearish waiting zone rather
    than an ordinary uptrend.
    """
    close = market.sort_values("date")["close"].astype(float)
    ma20 = close.rolling(20).mean()
    if len(close) < 22 or pd.isna(ma20.iloc[-1]) or pd.isna(ma20.iloc[-3]):
        return f"{bar}{ {1: '↑', -1: '↓', 0: '→'}[signal.direction] }"
    slope = float(ma20.iloc[-1] - ma20.iloc[-3])
    if bar == "1H" and signal.direction > 0 and live is not None and not live.empty:
        live_bar = live.sort_values("date").iloc[-1]
        recent = market.sort_values("date").iloc[-8:]
        prior_peak = float(recent["high"].astype(float).max())
        weakening_live = (float(live_bar["close"]) < float(live_bar["open"])
                          and float(live_bar["high"]) < prior_peak
                          and float(live_bar["close"]) < float(recent.iloc[-1]["close"]))
        if weakening_live:
            return "1H顶部弱化↘(完整上涨结构未翻转，仅作背景提示)"
    if slope < 0 and signal.direction > 0:
        return f"{bar}反转观察区↘(MA20向下，短线信号仍向上)"
    if slope > 0 and signal.direction < 0:
        return f"{bar}反转观察区↗(MA20向上，短线信号仍向下)"
    return f"{bar}{ {1: '↑', -1: '↓', 0: '→'}[signal.direction] }(MA20{'↑' if slope > 0 else '↓' if slope < 0 else '→'})"


def one_minute_reversal_trend_lock_state(
    one_minute: pd.DataFrame, direction: int, anchor_time, anchor_extreme: float,
    *, already_confirmed: bool = False,
) -> tuple[str, str, pd.Timestamp | None]:
    """Promote a fresh 1m endpoint after MA20 turn/retest; revoke on failure."""
    if direction not in {-1, 1} or len(one_minute) < 24:
        return "waiting", "一分钟反转趋势锁定等待足够K线", None
    one = one_minute.sort_values("date").reset_index(drop=True).copy()
    one["date"] = pd.to_datetime(one["date"], utc=True)
    anchor = pd.to_datetime(anchor_time, utc=True)
    recent = one[one["date"] >= anchor].copy()
    if recent.empty or (one.iloc[-1]["date"] - anchor).total_seconds() > 90 * 60:
        return "waiting", "一分钟反转末端已超过90分钟，不再建立新趋势锁定", None
    close = one["close"].astype(float)
    ma20 = close.rolling(20).mean()
    atr = latest_atr(one)
    if atr <= 0 or pd.isna(ma20.iloc[-3]):
        return "waiting", "一分钟反转趋势锁定等待有效MA20与ATR", None
    indexed = one.assign(ma20=ma20)
    after = indexed[indexed["date"] >= anchor].dropna(subset=["ma20"])
    if len(after) < 2:
        return "waiting", "一分钟底部/顶部试单后等待有效上穿/下穿MA20", None
    crossed = direction * (after["close"].astype(float) - after["ma20"].astype(float)) > 0
    cross_positions = [index for index in range(1, len(after))
                       if bool(crossed.iloc[index]) and not bool(crossed.iloc[index - 1])]
    latest_time = pd.Timestamp(one.iloc[-1]["date"])
    ma20_slope = direction * float(ma20.iloc[-1] - ma20.iloc[-3])
    latest_side = direction * (float(close.iloc[-1]) - float(ma20.iloc[-1]))
    prior_side = direction * (float(close.iloc[-2]) - float(ma20.iloc[-2]))

    if already_confirmed:
        broke_anchor = direction * (float(close.iloc[-1]) - float(anchor_extreme)) < 0
        lost_ma20 = latest_side < -atr * .08 and prior_side < 0 and ma20_slope <= 0
        if broke_anchor or lost_ma20:
            return "failed", (
                "一分钟新鲜反转趋势锁定失败：价格重新破坏反转极值"
                if broke_anchor else
                "一分钟连续跌回/涨回MA20旧趋势侧且MA20重新反向，恢复沿用上级旧趋势"
            ), latest_time
        return "confirmed", "一分钟新鲜反转趋势锁定继续有效", latest_time

    if not cross_positions:
        return "waiting", "一分钟底部/顶部试单后尚未有效穿过MA20", None
    cross_at = cross_positions[0]
    post_cross = after.iloc[cross_at + 1:]
    if direction > 0:
        retested = bool(((post_cross["low"].astype(float) <= post_cross["ma20"] + atr * .10)
                         & (post_cross["close"].astype(float) >= post_cross["ma20"])).any())
    else:
        retested = bool(((post_cross["high"].astype(float) >= post_cross["ma20"] - atr * .10)
                         & (post_cross["close"].astype(float) <= post_cross["ma20"])).any())
    direct_turn = ma20_slope > 0 and latest_side > 0 and prior_side > 0
    if retested or direct_turn:
        return "confirmed", (
            "一分钟反转穿过MA20后首次回踩/反抽守住，建立新鲜趋势锁定"
            if retested else
            "一分钟反转穿过MA20后未回踩而继续运行，MA20已经同向拐弯，建立新鲜趋势锁定"
        ), latest_time
    return "waiting", "一分钟已经穿过MA20，等待首次回踩守住或MA20同向拐弯", None


def five_minute_ma5_trend_holds(five_minute, direction: int) -> bool:
    """Let raw 5m close and MA5 direction filter ordinary 1m exit noise."""
    if five_minute is None or direction not in {-1, 1} or len(five_minute) < 8:
        return False
    five = five_minute.sort_values("date").reset_index(drop=True)
    close = five["close"].astype(float)
    ma5 = close.rolling(5).mean()
    if not pd.notna(ma5.iloc[-2]):
        return False
    ma5_aligned = direction * float(ma5.iloc[-1] - ma5.iloc[-2]) > 0
    price_aligned = direction * (float(close.iloc[-1]) - float(ma5.iloc[-1])) >= 0
    return bool(ma5_aligned and price_aligned)


def five_minute_ma20_takeover_confirmed(
        five_minute, direction: int, *, required_closed_bars: int = 2) -> bool:
    """Confirm that closed 5m bars have earned control of MA5 exits."""
    if (five_minute is None or direction not in {-1, 1}
            or required_closed_bars < 1 or len(five_minute) < 20 + required_closed_bars):
        return False
    five = five_minute.sort_values("date").reset_index(drop=True)
    close = five["close"].astype(float)
    ma20 = close.rolling(20).mean()
    margin = direction * (close - ma20)
    confirmed = margin.tail(required_closed_bars)
    return bool(confirmed.notna().all() and (confirmed > 0).all())


def five_minute_average_trend_holds(five_minute, direction: int) -> bool:
    """Legacy API alias; the decision now uses raw price and 5m MA5 only."""
    return five_minute_ma5_trend_holds(five_minute, direction)


def fee_aware_active_exit_allows(average_entry: float, current_price: float,
                                 direction: int, *, minimum_net_usdt: float = .005) -> tuple[bool, str]:
    """Require a 0.05-contract active exit to cover both taker fees plus net buffer."""
    if average_entry <= 0 or current_price <= 0 or direction not in {-1, 1}:
        return False, "主动止盈费用门参数无效"
    live_size, contract_value, taker_rate = .05, .1, .0005
    gross = direction * (current_price - average_entry) * live_size * contract_value
    estimated_fees = (average_entry + current_price) * live_size * contract_value * taker_rate
    required = estimated_fees + minimum_net_usdt
    return gross >= required, (
        f"预计毛收益{gross:.5f}USDT，预计往返手续费{estimated_fees:.5f}USDT，"
        f"最低净收益{minimum_net_usdt:.5f}USDT")


def confirmed_ma5_turn_exit(exit_reason: str, direction: int) -> bool:
    """Identify a completed MA5 trend failure that must exit without a fee veto."""
    reason = str(exit_reason or "")
    if direction > 0:
        return "上升转为走平或向下" in reason or "连续上升转为走平或向下" in reason
    if direction < 0:
        return "下降转为走平或向上" in reason or "连续下滑转为走平或向上" in reason
    return False


def active_exit_requires_fee_gate(
        exit_reason: str, direction: int, *, reversal_takeover: bool = False) -> bool:
    """Apply the profit gate only to discretionary early profit taking."""
    return bool(exit_reason and not reversal_takeover
                and not confirmed_ma5_turn_exit(exit_reason, direction))


def fast_extreme_ma5_profit_exit_reason(one_minute, average_entry: float,
                                        direction: int, entry_time=None) -> str:
    """Lock profit after a fast favorable run when 1m price and MA5 turn back."""
    if direction not in {-1, 1} or average_entry <= 0 or one_minute is None:
        return ""
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(one) < 20:
        return ""
    if entry_time is not None:
        entry_stamp = pd.to_datetime(entry_time, utc=True)
        dates = pd.to_datetime(one["date"], utc=True)
        post_entry = one.loc[dates >= entry_stamp].copy()
        # Keep enough history to calculate MA5/ATR, but measure the excursion
        # only on candles that belong to this position.
        if post_entry.empty:
            return ""
    else:
        post_entry = one.tail(12).copy()
    close = one["close"].astype(float)
    ma5 = close.rolling(5).mean()
    slopes = ma5.diff()
    previous_close = close.shift(1)
    true_range = pd.concat([
        one["high"].astype(float) - one["low"].astype(float),
        (one["high"].astype(float) - previous_close).abs(),
        (one["low"].astype(float) - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr1 = float(true_range.tail(14).mean())
    if not pd.notna(atr1) or atr1 <= 0 or not pd.notna(ma5.iloc[-1]):
        return ""
    latest_close = float(close.iloc[-1])
    retained_profit = direction * (latest_close - average_entry)
    if retained_profit <= 0:
        return ""
    recent_position = post_entry.tail(12)
    favorable_excursion = (
        float(recent_position["high"].astype(float).max()) - average_entry
        if direction > 0 else
        average_entry - float(recent_position["low"].astype(float).min())
    )
    prior_slopes = [float(value) for value in slopes.iloc[-4:-1] if pd.notna(value)]
    had_favorable_ma5_run = any(direction * value > 0 for value in prior_slopes)
    ma5_flat_or_reversed = pd.notna(slopes.iloc[-1]) and direction * float(slopes.iloc[-1]) <= 0
    price_returned_to_ma5 = direction * (latest_close - float(ma5.iloc[-1])) <= 0
    if (favorable_excursion >= atr1 and had_favorable_ma5_run
            and ma5_flat_or_reversed and price_returned_to_ma5):
        side = "多单" if direction > 0 else "空单"
        turn = "上升转为走平或向下拐弯" if direction > 0 else "下降转为走平或向上拐弯"
        return (f"{side}主动止盈（快速行情MA5止盈）：持仓后已有至少1.0倍一分钟ATR的有利运行，"
                f"价格已回到MA5且MA5已由{turn}，立即市价只减仓锁定利润")
    return ""


def aggressive_short_ma5_exit_reason(one_minute, average_entry: float,
                                     five_minute=None, fifteen_minute=None,
                                     one_hour=None, entry_time=None,
                                     prefer_five_minute_hold: bool = False) -> str:
    """Quantify the aggressive short's MA5 bend and climax-candle exits."""
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(one) < 24 or average_entry <= 0:
        return ""
    close = one["close"].astype(float)
    ma5 = close.rolling(5).mean()
    previous_close = close.shift(1)
    tr = pd.concat([
        one["high"].astype(float) - one["low"].astype(float),
        (one["high"].astype(float) - previous_close).abs(),
        (one["low"].astype(float) - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr1 = float(tr.tail(14).mean())
    latest = one.iloc[-1]
    if not pd.notna(atr1) or atr1 <= 0 or float(latest["close"]) >= average_entry:
        return ""
    if not prefer_five_minute_hold:
        fast_exit = fast_extreme_ma5_profit_exit_reason(
            one, average_entry, -1, entry_time=entry_time)
        if fast_exit:
            return fast_exit
    # The 1m chart finds the entry and gives the first exit warning.  A still
    # aligned 5m MA5 plus an ordinary close on the trend side owns the hold decision,
    # so an ordinary 1m MA5 bend cannot cut the larger move short.
    if five_minute_ma5_trend_holds(five_minute, -1):
        return ""
    # A confirmed waterfall is managed directly by the 5m MA5.  While that
    # line is still falling and price remains below it, ignore 1m bends,
    # two-candle profit taking and climax exits; those are normal noise inside
    # a cascade and previously cut the position before the main expansion.
    if five_minute is not None and len(five_minute) >= 22:
        cascade_five = five_minute.sort_values("date").reset_index(drop=True)
        cascade_close = cascade_five["close"].astype(float)
        cascade_ma5 = cascade_close.rolling(5).mean()
        cascade_ma10 = cascade_close.rolling(10).mean()
        cascade_ma20 = cascade_close.rolling(20).mean()
        if (float(cascade_ma5.iloc[-1]) < float(cascade_ma10.iloc[-1]) < float(cascade_ma20.iloc[-1])
                and float(cascade_ma5.iloc[-1] - cascade_ma5.iloc[-2]) < 0
                and float(cascade_close.iloc[-1]) < float(cascade_ma5.iloc[-1])):
            return ""
    latest_two = one.iloc[-2:]
    two_bear_bodies = latest_two["open"].astype(float) - latest_two["close"].astype(float)
    if (len(latest_two) == 2 and bool((two_bear_bodies >= atr1 * .65).all())
            and float(latest_two.iloc[-1]["close"]) < float(latest_two.iloc[-2]["close"])):
        return "激进型空单连续两根长阴快速止盈：两根长阴已连续向下扩张，在第二根长阴下方立即平仓锁利"
    slopes = ma5.diff()
    five_still_falling = False
    five_exit_confirmed = False
    if five_minute is not None and len(five_minute) >= 8:
        five = five_minute.sort_values("date").reset_index(drop=True)
        five_close = five["close"].astype(float)
        five_ma5 = five_close.rolling(5).mean()
        five_slopes = five_ma5.diff()
        five_still_falling = (
            pd.notna(five_slopes.iloc[-1])
            and float(five_slopes.iloc[-1]) < 0
            and any(float(value) < 0 for value in five_slopes.iloc[-3:-1] if pd.notna(value))
        )
        latest_five_close = float(five_close.iloc[-1])
        latest_two_slopes = [float(value) for value in five_slopes.iloc[-2:] if pd.notna(value)]
        five_had_falling_run = any(
            float(value) < 0 for value in five_slopes.iloc[-6:-2] if pd.notna(value))
        five_exit_confirmed = (
            len(latest_two_slopes) == 2
            and five_had_falling_run
            and all(value >= 0 for value in latest_two_slopes)
            and latest_five_close >= float(five_ma5.iloc[-1])
        )
    def recent_big_death_cross(frame) -> bool:
        """A recent MA5<MA10<MA20 expansion upgrades, but never opens, a trade."""
        if frame is None or len(frame) < 22:
            return False
        ordered = frame.sort_values("date").reset_index(drop=True)
        ordered_close = ordered["close"].astype(float)
        ordered_ma5 = ordered_close.rolling(5).mean()
        ordered_ma10 = ordered_close.rolling(10).mean()
        ordered_ma20 = ordered_close.rolling(20).mean()
        spread = ordered_ma20 - ordered_ma5
        for index in range(max(20, len(ordered) - 8), len(ordered)):
            if (pd.notna(ordered_ma20.iloc[index])
                    and float(ordered_ma5.iloc[index]) < float(ordered_ma10.iloc[index]) < float(ordered_ma20.iloc[index])
                    and pd.notna(spread.iloc[index - 1])
                    and float(spread.iloc[index]) > float(spread.iloc[index - 1])):
                return True
        return False

    fifteen_takeover = False
    fifteen_bent = False
    if fifteen_minute is not None and len(fifteen_minute) >= 9:
        fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
        fifteen_ma5 = fifteen["close"].astype(float).rolling(5).mean()
        fifteen_slopes = fifteen_ma5.diff()
        prior_slopes = [float(value) for value in fifteen_slopes.iloc[-4:-1] if pd.notna(value)]
        fifteen_takeover = len(prior_slopes) >= 2 and sum(value < 0 for value in prior_slopes) >= 2
        post_entry_fifteen = (entry_time is None or
                               pd.to_datetime(fifteen.iloc[-1]["date"], utc=True)
                               > pd.to_datetime(entry_time, utc=True))
        fifteen_bent = (fifteen_takeover and post_entry_fifteen
                        and pd.notna(fifteen_slopes.iloc[-1])
                        and float(fifteen_slopes.iloc[-1]) >= 0)
    one_hour_takeover = recent_big_death_cross(fifteen_minute)
    one_hour_bent = False
    if one_hour_takeover and one_hour is not None and len(one_hour) >= 8:
        hourly = one_hour.sort_values("date").reset_index(drop=True)
        hourly_ma5 = hourly["close"].astype(float).rolling(5).mean()
        hourly_slopes = hourly_ma5.diff()
        prior_hourly_slopes = [float(value) for value in hourly_slopes.iloc[-4:-1] if pd.notna(value)]
        post_entry_hour = (entry_time is None or
                           pd.to_datetime(hourly.iloc[-1]["date"], utc=True)
                           > pd.to_datetime(entry_time, utc=True))
        one_hour_bent = (
            len(prior_hourly_slopes) >= 2
            and sum(value < 0 for value in prior_hourly_slopes) >= 2
            and post_entry_hour
            and pd.notna(hourly_slopes.iloc[-1])
            and float(hourly_slopes.iloc[-1]) >= 0
        )
    ma5_bent = float(slopes.iloc[-1]) >= 0 and any(
        float(value) < 0 for value in slopes.iloc[-4:-1] if pd.notna(value)
    )
    if one_hour_bent:
        return "激进型空单一小时趋势止盈：十五分钟大死叉已升级接管周期，一小时MA5由连续下滑转为走平或向上拐弯"
    if fifteen_bent and not one_hour_takeover:
        return "激进型空单十五分钟趋势止盈：十五分钟MA5已由连续下滑转为走平或向上拐弯"
    if ma5_bent:
        if fifteen_takeover or one_hour_takeover:
            return ""
        if five_minute is not None and len(five_minute) >= 8:
            if five_still_falling:
                return ""
            if not five_exit_confirmed:
                return ""
            return "激进型空单双周期确认止盈：一分钟MA5已向上，五分钟MA5连续两根走平/上拐且价格收回MA5"
        return "激进型空单主动止盈：一分钟MA5已由下降转为走平或向上拐弯"
    volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    baseline = float(volume.iloc[-21:-1].mean())
    body = float(latest["open"] - latest["close"])
    climax = (
        body >= atr1 * 1.50
        and float(latest["close"]) <= float(ma5.iloc[-1]) - atr1 * .75
        and (baseline <= 0 or float(volume.iloc[-1]) >= baseline * 1.80)
    )
    if climax:
        return "激进型空单紧急止盈：一分钟异常放量长阴且价格过度远离MA5，按衰竭线立即平仓"
    for index in range(max(1, len(one) - 2), len(one)):
        candle = one.iloc[index]
        candle_ma5 = float(ma5.iloc[index])
        candle_atr = float(tr.iloc[max(0, index - 13):index + 1].mean())
        body_low = min(float(candle["open"]), float(candle["close"]))
        lower_wick = body_low - float(candle["low"])
        deep_low = float(candle["low"]) <= candle_ma5 - candle_atr * .75
        wick_exhaustion = lower_wick >= max(
            abs(float(candle["close"] - candle["open"])) * .80, candle_atr * .35)
        favorable_excursion = average_entry - float(candle["low"])
        retained_profit = average_entry - float(candle["close"])
        one_ma5_still_falling = pd.notna(slopes.iloc[index]) and float(slopes.iloc[index]) < 0
        if (deep_low and wick_exhaustion
                and favorable_excursion >= candle_atr
                and retained_profit >= candle_atr * .25
                and not one_ma5_still_falling
                and not five_still_falling):
            return ("激进型空单紧急止盈：已有至少1.0倍一分钟ATR浮盈，深度下探出现长下影衰竭，"
                    "收盘仍保留至少0.25倍ATR利润且均线不再顺畅下滑，立即平仓锁利")
    return ""


def aggressive_long_ma5_exit_reason(one_minute, average_entry: float,
                                    five_minute=None, fifteen_minute=None,
                                    one_hour=None, entry_time=None,
                                    prefer_five_minute_hold: bool = False) -> str:
    """Use 1m MA5 for warning and rising 5m MA5 to keep a winning long."""
    if not prefer_five_minute_hold:
        fast_exit = fast_extreme_ma5_profit_exit_reason(
            one_minute, average_entry, 1, entry_time=entry_time)
        if fast_exit:
            return fast_exit
    if five_minute_ma5_trend_holds(five_minute, 1):
        return ""
    mirrored = one_minute.copy()
    old_open = mirrored["open"].astype(float).copy()
    old_high = mirrored["high"].astype(float).copy()
    old_low = mirrored["low"].astype(float).copy()
    old_close = mirrored["close"].astype(float).copy()
    # Reflect around a positive price anchor so the shared short-side helper's
    # input validation remains meaningful after mirroring.
    anchor = max(float(old_high.max()), average_entry) * 2.0
    mirrored["open"] = anchor - old_open
    mirrored["high"] = anchor - old_low
    mirrored["low"] = anchor - old_high
    mirrored["close"] = anchor - old_close
    mirrored_fifteen = None
    if fifteen_minute is not None:
        mirrored_fifteen = fifteen_minute.copy()
        for column, old_column in (("open", "open"), ("close", "close")):
            mirrored_fifteen[column] = anchor - fifteen_minute[old_column].astype(float)
        mirrored_fifteen["high"] = anchor - fifteen_minute["low"].astype(float)
        mirrored_fifteen["low"] = anchor - fifteen_minute["high"].astype(float)
    mirrored_one_hour = None
    if one_hour is not None:
        mirrored_one_hour = one_hour.copy()
        for column, old_column in (("open", "open"), ("close", "close")):
            mirrored_one_hour[column] = anchor - one_hour[old_column].astype(float)
        mirrored_one_hour["high"] = anchor - one_hour["low"].astype(float)
        mirrored_one_hour["low"] = anchor - one_hour["high"].astype(float)
    reason = aggressive_short_ma5_exit_reason(
        mirrored, anchor - average_entry, fifteen_minute=mirrored_fifteen,
        one_hour=mirrored_one_hour, entry_time=entry_time)
    translated = (reason.replace("空单", "多单")
                  .replace("下降转为走平或向上", "上升转为走平或向下")
                  .replace("连续下滑转为走平或向上", "连续上升转为走平或向下")
                  .replace("长阴", "长阳")
                  .replace("下探", "上冲")
                  .replace("长下影", "长上影"))
    if "主动止盈" not in translated or five_minute is None or len(five_minute) < 8:
        return translated
    five = five_minute.sort_values("date").reset_index(drop=True)
    five_ma5 = five["close"].astype(float).rolling(5).mean()
    five_slopes = five_ma5.diff()
    five_still_rising = (
        pd.notna(five_slopes.iloc[-1])
        and float(five_slopes.iloc[-1]) > 0
        and any(float(value) > 0 for value in five_slopes.iloc[-3:-1] if pd.notna(value))
    )
    if five_still_rising:
        return ""
    latest_two_slopes = [float(value) for value in five_slopes.iloc[-2:] if pd.notna(value)]
    five_had_rising_run = any(
        float(value) > 0 for value in five_slopes.iloc[-6:-2] if pd.notna(value))
    five_exit_confirmed = (
        len(latest_two_slopes) == 2
        and five_had_rising_run
        and all(value <= 0 for value in latest_two_slopes)
        and float(five["close"].iloc[-1]) <= float(five_ma5.iloc[-1])
    )
    if not five_exit_confirmed:
        return ""
    return "激进型多单双周期确认止盈：一分钟MA5已向下，五分钟MA5连续两根走平/下拐且价格跌回MA5"


def ma20_pullback_short_setup(signals, markets, lookback: int = 6) -> tuple[int, str, float]:
    """Confirm a 1m rejection of 5m MA20 inside an established downtrend."""
    five = markets["5m"].sort_values("date").reset_index(drop=True)
    one = markets["1m"].sort_values("date").reset_index(drop=True)
    if len(five) < 24 or len(one) < max(21, lookback):
        return 0, "not enough closed candles for MA20 pullback", 0.0
    five_close = five["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    one_close = one["close"].astype(float)
    one_ma20 = one_close.rolling(20).mean()
    five_downtrend = (
        signals["5m"].direction == -1
        and signals["15m"].direction != 1
        and float(five_close.iloc[-1]) < float(five_ma20.iloc[-1])
        and float(five_ma20.iloc[-1]) < float(five_ma20.iloc[-4])
    )
    if not five_downtrend:
        return 0, "5m downtrend or falling MA20 is not confirmed", 0.0
    recent = one.tail(lookback).copy()
    recent["ma20"] = one_ma20.loc[recent.index]
    touching = recent[recent["high"].astype(float) >= recent["ma20"] * 0.999]
    latest = one.iloc[-1]
    bearish_rejection = (
        not touching.empty
        and float(latest["close"]) < float(one_ma20.iloc[-1])
        and (float(latest["close"]) < float(latest["open"])
             or float(latest["close"]) < float(one.iloc[-2]["close"]))
        and signals["1m"].direction == -1
    )
    if not bearish_rejection:
        return 0, "downtrend confirmed; waiting for 1m MA20 pullback and bearish rejection", 0.0
    previous_close = five_close.shift(1)
    true_range = max(
        float((five["high"].astype(float) - five["low"].astype(float)).tail(14).mean()),
        float((five["high"].astype(float) - previous_close).abs().tail(14).mean()),
        float((five["low"].astype(float) - previous_close).abs().tail(14).mean()),
    )
    rejection_high = float(touching["high"].astype(float).max())
    stop = max(float(five_ma20.iloc[-1]), rejection_high) + true_range * 0.10
    return -1, "5m downtrend confirmed; 1m pullback rejected at falling MA20", stop


def ma20_pullback_long_setup(signals, markets, lookback: int = 6) -> tuple[int, str, float]:
    """Mirror: confirm a 1m rebound from 5m MA20 inside an established uptrend."""
    five = markets["5m"].sort_values("date").reset_index(drop=True)
    one = markets["1m"].sort_values("date").reset_index(drop=True)
    if len(five) < 24 or len(one) < max(21, lookback):
        return 0, "not enough closed candles for MA20 pullback", 0.0
    five_close = five["close"].astype(float)
    five_ma20 = five_close.rolling(20).mean()
    one_close = one["close"].astype(float)
    one_ma20 = one_close.rolling(20).mean()
    five_uptrend = (
        signals["5m"].direction == 1 and signals["15m"].direction == 1
        and float(five_close.iloc[-1]) > float(five_ma20.iloc[-1])
        and float(five_ma20.iloc[-1]) > float(five_ma20.iloc[-4])
    )
    if not five_uptrend:
        return 0, "5m uptrend or rising MA20 is not confirmed", 0.0
    recent = one.tail(lookback).copy()
    recent["ma20"] = one_ma20.loc[recent.index]
    touching = recent.iloc[:-1][recent.iloc[:-1]["low"].astype(float)
                                <= recent.iloc[:-1]["ma20"] * 1.001]
    latest = one.iloc[-1]
    previous = one.iloc[-2]
    latest_range = max(float(latest["high"] - latest["low"]), 1e-9)
    one_previous = one_close.shift(1)
    one_tr = pd.concat(((one["high"].astype(float) - one["low"].astype(float)),
                        (one["high"].astype(float) - one_previous).abs(),
                        (one["low"].astype(float) - one_previous).abs()), axis=1).max(axis=1)
    one_atr = float(one_tr.tail(14).mean())
    bullish_rebound = (
        not touching.empty and float(latest["close"]) > float(one_ma20.iloc[-1])
        and float(latest["close"]) > float(latest["open"])
        and float(latest["close"] - latest["open"]) >= one_atr * .30
        and float(latest["close"]) > float(previous["high"])
        and float(latest["close"] - latest["low"]) / latest_range >= .65
        and signals["1m"].direction == 1
    )
    if not bullish_rebound:
        return 0, "uptrend confirmed; waiting for 1m MA20 pullback and bullish rebound", 0.0
    rejection_low = float(touching["low"].astype(float).min())
    stop = rejection_low - max(one_atr * .20, float(latest["close"]) * .0003)
    return 1, "5m上升趋势；1m先回踩MA20，再由强阳线突破前高确认", stop


def breakout_pullback_long_setup(signals, markets, *, breakout_lookback: int = 8,
                                 max_wait_minutes: int = 6) -> tuple[int, str, float]:
    """15m trend + confirmed 5m breakout + 1m pullback/reclaim long.

    Only closed candles are supplied by ``okx_history_market``.  The breakout
    may be one of the latest two closed 5m bars; the 1m confirmation must occur
    within six minutes of it.  This captures the first retest without chasing
    an already extended impulse.
    """
    five = markets["5m"].sort_values("date").reset_index(drop=True)
    one = markets["1m"].sort_values("date").reset_index(drop=True)
    fifteen = markets["15m"].sort_values("date").reset_index(drop=True)
    if len(five) < max(24, breakout_lookback + 3) or len(one) < 3 or len(fifteen) < 24:
        return 0, "突破回踩数据不足", 0.0
    f_close = five["close"].astype(float)
    f_open = five["open"].astype(float)
    f_high = five["high"].astype(float)
    f_low = five["low"].astype(float)
    f_ma20 = f_close.rolling(20).mean()
    t_close = fifteen["close"].astype(float)
    t_ma5, t_ma10, t_ma20 = t_close.rolling(5).mean(), t_close.rolling(10).mean(), t_close.rolling(20).mean()
    trend_ok = (
        signals["15m"].direction == 1
        and t_close.iloc[-1] > t_ma20.iloc[-1]
        and t_ma5.iloc[-1] > t_ma10.iloc[-1] > t_ma20.iloc[-1]
        and t_ma20.iloc[-1] > t_ma20.iloc[-3]
    )
    if not trend_ok:
        return 0, "15分钟多头排列或MA20上升尚未确认", 0.0
    latest_fifteen, prior_fifteen = fifteen.iloc[-1], fifteen.iloc[-2]
    bearish_cover = (
        float(prior_fifteen["close"]) > float(prior_fifteen["open"])
        and float(latest_fifteen["close"]) < float(latest_fifteen["open"])
        and float(latest_fifteen["open"]) >= float(prior_fifteen["close"])
        and float(latest_fifteen["close"]) <= float(prior_fifteen["open"])
    )
    if bearish_cover:
        return 0, "15分钟阴线实体覆盖前一根阳线：首次回踩只观察，等待下一根15分钟收盘重新确认", 0.0
    previous = f_close.shift(1)
    tr = pd.concat(((f_high - f_low), (f_high - previous).abs(), (f_low - previous).abs()), axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    volume = five["volume"].astype(float) if "volume" in five else pd.Series(1.0, index=five.index)
    volume_mean = volume.shift(1).rolling(20).mean()
    breakout_index = None
    breakout_level = 0.0
    for index in range(max(breakout_lookback, len(five) - 2), len(five)):
        prior = five.iloc[index - breakout_lookback:index]
        # The breakout reference is the highest candle-body top in the prior
        # eight confirmed 5m bars, not their highest wick.  The newly closed
        # candle's high (its upper-wick extreme) may pierce that body level;
        # close-above is deliberately not required, while bullish body, volume,
        # retest and reclaim filters still guard against a bare wick signal.
        prior_body_high = prior[["open", "close"]].astype(float).max(axis=1)
        prior_high = float(prior_body_high.max())
        body = float(f_close.iloc[index] - f_open.iloc[index])
        impulse_ok = body >= float(atr14.iloc[index]) * .60
        volume_ok = float(volume.iloc[index]) >= float(volume_mean.iloc[index]) * 1.50
        if f_high.iloc[index] > prior_high and body > 0 and impulse_ok and volume_ok:
            breakout_index, breakout_level = index, prior_high
    if breakout_index is None:
        return 0, "等待已收盘5分钟K线上影线突破前8根K线最高实体顶部", 0.0
    breakout_time = pd.Timestamp(five.iloc[breakout_index]["date"])
    confirmed_at = breakout_time + pd.Timedelta(minutes=5)
    recent = one[one["date"] >= confirmed_at].tail(max_wait_minutes)
    if recent.empty or (pd.Timestamp(one.iloc[-1]["date"]) - confirmed_at).total_seconds() > max_wait_minutes * 60:
        return 0, "5分钟突破后的6分钟回踩确认窗口已过期", 0.0
    # A valid 1m entry is deliberately split into two stages.  First there
    # must be a real pullback after the 5m impulse (at least one down-closing
    # bar touching the breakout area).  Only a later, closed 1m bar may be the
    # confirmation.  This prevents an ordinary green bar near the impulse top
    # from being mistaken for a retest/reclaim entry.
    recent = recent.copy()
    recent["open"] = recent["open"].astype(float)
    recent["high"] = recent["high"].astype(float)
    recent["low"] = recent["low"].astype(float)
    recent["close"] = recent["close"].astype(float)
    recent["previous_close"] = recent["close"].shift(1)
    retest_limit = breakout_level + float(atr14.iloc[breakout_index]) * .25
    pullbacks = recent.iloc[:-1]
    pullbacks = pullbacks[
        (pullbacks["low"] <= retest_limit)
        & ((pullbacks["close"] < pullbacks["open"])
           | (pullbacks["close"] < pullbacks["previous_close"]))
    ]
    if pullbacks.empty:
        latest_distance = float(recent.iloc[-1]["close"] - f_ma20.iloc[-1])
        if latest_distance > float(atr14.iloc[-1]) * 2.0:
            return 0, "突破回踩候选价格距离5分钟MA20超过2 ATR，禁止追涨", 0.0
        return 0, "5分钟突破成立，等待1分钟先完成回踩，不能用冲高后的普通阳线直接追多", 0.0
    pullback_index = pullbacks.index[-1]
    confirmation = recent.loc[recent.index > pullback_index]
    latest, previous_one = recent.iloc[-1], recent.iloc[-2]
    one_close = one["close"].astype(float)
    one_ma5 = float(one_close.rolling(5, min_periods=3).mean().iloc[-1])
    one_ma10 = float(one_close.rolling(10, min_periods=3).mean().iloc[-1])
    one_previous = one_close.shift(1)
    one_tr = pd.concat(((one["high"].astype(float) - one["low"].astype(float)),
                        (one["high"].astype(float) - one_previous).abs(),
                        (one["low"].astype(float) - one_previous).abs()), axis=1).max(axis=1)
    one_atr = float(one_tr.tail(14).mean())
    candle_range = max(float(latest["high"] - latest["low"]), 1e-9)
    body = float(latest["close"] - latest["open"])
    closes_near_high = float(latest["close"] - latest["low"]) / candle_range >= .65
    held_breakout = float(recent["close"].min()) >= breakout_level - float(atr14.iloc[breakout_index]) * .10
    reclaimed = (
        not confirmation.empty
        and float(latest["close"]) > breakout_level
        and body >= one_atr * .30
        and float(latest["close"]) > float(previous_one["high"])
        and float(latest["close"]) > max(one_ma5, one_ma10)
        and closes_near_high
    )
    latest_distance = float(latest["close"] - f_ma20.iloc[-1])
    not_extended = latest_distance <= float(atr14.iloc[-1]) * 2.0
    if not (held_breakout and reclaimed):
        return 0, "1分钟已回踩，等待后续阳线突破前高、收复MA5/MA10且收在上方35%内", 0.0
    if not not_extended:
        return 0, "突破回踩已转强，但价格距离5分钟MA20超过2 ATR，禁止追涨", 0.0
    buffer = max(float(atr14.iloc[-1]) * .20, float(latest["close"]) * .0003)
    stop = min(float(recent["low"].min()), breakout_level) - buffer
    return 1, "15分钟多头；5分钟放量突破；1分钟回踩突破位后重新转强做多", stop


def _reconcile_closed_validation_trades(store: StateStore, client: OkxDemoClient, inst_id: str) -> tuple[str, ...]:
    """Complete durable trade records after OKX reports that the position is flat."""
    rows = store.strategy_trade_lifecycles(inst_id)
    if not any(row["status"] == "open" for row in rows):
        return ()
    historical = any(row["status"] == "open" and row["strategy_version"] != VALIDATION_VERSION
                     for row in rows)
    last_history = store.connection.execute(
        "SELECT created_at_utc FROM events WHERE event_type='historical_reconciliation_started' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if last_history and (datetime.now(timezone.utc) - datetime.fromisoformat(last_history[0])).total_seconds() < 300:
        historical = False
    if historical:
        store.record_event("historical_reconciliation_started", {"instrument": inst_id})
    fills = (client.reconciliation_fills(inst_id) if historical
             and callable(getattr(client, "reconciliation_fills", None)) else client.recent_fills(inst_id))
    # A reduce-only MA5 exit closes one same-side layer. Process explicitly
    # requested lifecycles first and never reuse that fill for another layer.
    # Replaying closed records reserves their fills across scans and upgrades.
    # Entry-fill time, rather than signal-bar time, owns FIFO allocation.
    entry_times = {str(f.get("clOrdId")): int(f.get("ts") or 0) for f in fills}
    rows = sorted(rows, key=lambda row: (entry_times.get(str(row["trade_uid"]), 0), row["trade_uid"]))
    consumed_exit_trades: dict[str, float] = {}
    closed_trade_uids: list[str] = []
    for row in rows:
        available_fills = []
        for original in fills:
            size = float(original.get("fillSz") or 0)
            remaining = size - consumed_exit_trades.get(str(original.get("tradeId") or ""), 0)
            if remaining <= 0 or size <= 0:
                continue
            item = dict(original)
            item.update(fillSz=remaining, fee=float(original.get("fee") or 0) * remaining / size,
                        fillPnl=float(original.get("fillPnl") or 0) * remaining / size)
            available_fills.append(item)
        entry, exits = matched_closing_fills(row, available_fills)
        if not entry or not exits:
            if row["status"] == "open":
                store.record_event("lifecycle_reconciliation_unresolved", {
                    "trade_uid": row["trade_uid"], "reason": "未取得完整入场和平仓成交，保留待核查"})
            continue
        for item in exits:
            key = str(item.get("tradeId") or "")
            consumed_exit_trades[key] = consumed_exit_trades.get(key, 0) + float(item.get("fillSz") or 0)
        if row["status"] != "open":
            continue
        gross = sum(float(item.get("fillPnl") or 0) for item in exits)
        fees = sum(float(item.get("fee") or 0) for item in entry + exits)
        size = sum(float(item.get("fillSz") or 0) for item in exits)
        close_price = None if not size else sum(float(item.get("fillPx") or 0) * float(item.get("fillSz") or 0) for item in exits) / size
        exit_reason = str(row["exit_reason"] or (
            "stop_loss" if gross <= 0 else "trailing_take_profit"))
        store.close_trade_lifecycle(str(row["trade_uid"]), close_price=close_price, gross_pnl=gross,
                                    total_fees=fees, exit_reason=exit_reason,
                                    close_time=datetime.fromtimestamp(
                                        max(int(x.get("ts") or 0) for x in exits) / 1000,
                                        timezone.utc).isoformat())
        store.record_event("trade_lifecycle_closed", {
            "trade_uid": row["trade_uid"], "exit_reason": exit_reason, "close_price": close_price,
            "gross_pnl": gross, "fees": fees, "net_pnl": gross + fees,
        })
        closed_trade_uids.append(str(row["trade_uid"]))
    return tuple(closed_trade_uids)


def stale_layer_protective_stops(
    snapshot: dict[str, list[dict]], open_rows, closed_rows, inst_id: str,
) -> list[dict]:
    """Select only strategy-owned fixed stops whose layer has already closed.

    The exchange aggregates same-side positions while attached stops retain
    per-entry identities.  New orders use attachAlgoClOrdId; legacy orders are
    reconciled conservatively by their stored stop price multiplicity.
    """
    rows = list(open_rows) + list(closed_rows)
    if not rows:
        return []
    sides = {1: "long", -1: "short"}
    open_ids = {str(row["trade_uid"])[:31] + "P" for row in open_rows}
    known_prices = {
        (sides[int(row["direction"])], round(float(row["stop_price"]), 2))
        for row in rows if float(row["stop_price"] or 0) > 0
    }
    allowed_legacy: dict[tuple[str, float], int] = {}
    open_count_by_side: dict[str, int] = {}
    for row in open_rows:
        side = sides[int(row["direction"])]
        key = (side, round(float(row["stop_price"]), 2))
        allowed_legacy[key] = allowed_legacy.get(key, 0) + 1
        open_count_by_side[side] = open_count_by_side.get(side, 0) + 1

    remaining_by_side = {"long": 0.0, "short": 0.0}
    for position in snapshot.get("positions", []):
        if str(position.get("instId") or "") == inst_id:
            side = str(position.get("posSide") or "")
            if side in remaining_by_side:
                remaining_by_side[side] += abs(float(position.get("pos") or 0))
    inferred_unit_by_side = {
        side: total / max(open_count_by_side.get(side, 1), 1)
        for side, total in remaining_by_side.items()
    }

    candidates: list[dict] = []
    seen: set[str] = set()
    containers = list(snapshot.get("algo_orders", []))
    for position in snapshot.get("positions", []):
        for nested in position.get("closeOrderAlgo") or []:
            enriched = dict(nested)
            enriched.setdefault("instId", position.get("instId"))
            enriched.setdefault("posSide", position.get("posSide"))
            containers.append(enriched)
    for order in containers:
        algo_id = str(order.get("algoId") or "")
        if not algo_id or algo_id in seen:
            continue
        seen.add(algo_id)
        if str(order.get("instId") or inst_id) != inst_id:
            continue
        if str(order.get("ordType") or "conditional") == "move_order_stop":
            continue
        side = str(order.get("posSide") or "")
        price = round(float(order.get("slTriggerPx") or 0), 2)
        client_id = str(order.get("algoClOrdId") or order.get("attachAlgoClOrdId") or "")
        target = remaining_by_side.get(side, 0.0)
        quantity = float(order.get("sz") or 0)
        if str(order.get("closeFraction") or "") == "1":
            quantity = target
        if quantity <= 0 and target > 0:
            quantity = inferred_unit_by_side.get(side, target)
        if client_id.startswith("QBVAL"):
            if client_id not in open_ids:
                candidates.append({"instId": inst_id, "algoId": algo_id})
            elif target <= 0:
                candidates.append({"instId": inst_id, "algoId": algo_id})
            else:
                remaining_by_side[side] = max(0.0, target - quantity)
            continue
        key = (side, price)
        if key not in known_prices:
            continue
        if allowed_legacy.get(key, 0) > 0 and target > 0:
            allowed_legacy[key] -= 1
            remaining_by_side[side] = max(0.0, target - quantity)
        else:
            candidates.append({"instId": inst_id, "algoId": algo_id})
    return candidates


def confirm_unprotected_positions(
    client: OkxDemoClient,
    snapshot: dict[str, list[dict]],
    *,
    confirmations: int = 2,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Confirm a protection gap with fresh OKX snapshots before alarming.

    Position and attached-algo views can briefly advance at different times
    during fills or violent moves.  A single stale combination must never be
    treated as proof that server protection disappeared.  Conversely, a gap
    that survives independent refreshes remains blocking and visible.
    """
    current = snapshot
    missing = client.unprotected_positions(current)
    for _ in range(max(0, int(confirmations))):
        if not missing:
            break
        current = client.safety_snapshot()
        missing = client.unprotected_positions(current)
    return current, missing


def emergency_close_unprotected_quantity(
    client: OkxDemoClient,
    database: str | Path,
    unprotected: list[dict],
) -> tuple[str, ...]:
    """Immediately reduce only the quantity not covered by an OKX stop."""
    store = StateStore(database)
    closed: list[str] = []
    try:
        for position in unprotected:
            instrument = str(position.get("instId", ""))
            side = str(position.get("posSide", ""))
            deficit = int(round(float(position.get("protection_deficit") or 0)))
            total = int(round(abs(float(position.get("pos") or 0))))
            if side not in {"long", "short"} or deficit <= 0 or deficit > total:
                continue
            response = client.close_demo_position_market(
                deficit, position_side=side, enabled=True, max_contracts=10,
                confirmation="DEMO-ORDER", inst_id=instrument,
            )
            order = (response.get("data") or [{}])[0]
            order_id = str(order.get("ordId") or "")
            if not order_id:
                raise OkxError(
                    "OKX accepted emergency reduce-only close without an ordId; stop and reconcile"
                )
            store.record_event("unprotected_quantity_emergency_exit_submitted", {
                "instrument": instrument,
                "position_side": side,
                "position_contracts": total,
                "protected_contracts": float(position.get("protection_covered") or 0),
                "unprotected_contracts_closed": deficit,
                "order_id": order_id,
            })
            closed.append(f"{instrument}:{side}:{deficit}:{order_id}")
    finally:
        store.close()
    return tuple(closed)


def execute_validation_tick(cfg: AppConfig, credentials: OkxCredentials, database: str | Path,
                            *, client_override: OkxDemoClient | None = None,
                            pinets_consensus_override: object | None = None) -> ValidationExecutionResult:
    """Measure the live decision path and dispatch replay work after it finishes."""
    timing: dict[str, float | bool] = {}
    started = time.perf_counter()
    action = "error"
    try:
        result = _execute_validation_tick_impl(
            cfg, credentials, database, client_override=client_override,
            pinets_consensus_override=pinets_consensus_override, _timing=timing)
        action = result.action
        return result
    finally:
        total_ms = round((time.perf_counter() - started) * 1000, 2)
        diagnostic_keys = {
            "account_mode_check_ms", "initial_account_snapshot_ms",
            "fast_core_bootstrap_ms", "fast_core_budget_met",
        }
        measured_ms = sum(float(value) for key, value in timing.items()
                          if key not in diagnostic_keys)
        timing["decision_and_execution_ms"] = round(max(0.0, total_ms - measured_ms), 2)
        timing["total_ms"] = total_ms
        timing["action"] = action
        timing["strategy_version"] = VALIDATION_VERSION
        timing_store = None
        try:
            timing_store = StateStore(database)
            timing_store.record_event("validation_tick_timing", timing)
        except Exception:
            pass
        finally:
            if timing_store is not None:
                timing_store.close()
def _execute_validation_tick_impl(cfg: AppConfig, credentials: OkxCredentials, database: str | Path,
                                  *, client_override: OkxDemoClient | None = None,
                                  pinets_consensus_override: object | None = None,
                                  _timing: dict | None = None) -> ValidationExecutionResult:
    phase_started = time.perf_counter()
    def phase(name: str) -> None:
        nonlocal phase_started
        if _timing is not None:
            current = time.perf_counter()
            _timing[name] = round((current - phase_started) * 1000, 2)
            phase_started = current

    # The fast core acquires public market data and the private account view
    # concurrently.  Neither result depends on the other, and serial loading
    # was the largest source of the observed 90-138 second decision delay.
    client = client_override or OkxDemoClient(credentials, timeout=20)

    def account_fast_snapshot():
        mode_started = time.perf_counter()
        client.require_swap_trading_mode()
        mode_ms = round((time.perf_counter() - mode_started) * 1000, 2)
        snapshot_started = time.perf_counter()
        account_snapshot = client.safety_snapshot()
        snapshot_ms = round((time.perf_counter() - snapshot_started) * 1000, 2)
        return account_snapshot, mode_ms, snapshot_ms

    bootstrap_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="validation-fast-core") as pool:
        market_future = pool.submit(validation_market_context, cfg)
        account_future = pool.submit(account_fast_snapshot)
        signals, markets, market_summary = market_future.result()
        snapshot, mode_ms, snapshot_ms = account_future.result()
    if _timing is not None:
        _timing["market_data_and_signals_ms"] = round(
            (time.perf_counter() - bootstrap_started) * 1000, 2)
        _timing["account_mode_check_ms"] = mode_ms
        _timing["initial_account_snapshot_ms"] = snapshot_ms
        _timing["fast_core_bootstrap_ms"] = round(
            (time.perf_counter() - bootstrap_started) * 1000, 2)
        _timing["fast_core_budget_met"] = bool(
            _timing["fast_core_bootstrap_ms"] <= 5000)
    phase_started = time.perf_counter()
    sample_hour, sample_direction, sample_classification, sample_evidence = (
        hourly_multiframe_boundary_sample(markets, signals, datetime.now(timezone.utc)))
    sample_store = StateStore(database)
    try:
        sample_store.record_hourly_boundary_sample(
            instrument=cfg.okx.instruments[0], beijing_hour=sample_hour,
            endpoint_direction=sample_direction,
            five_direction=int(signals["5m"].direction),
            fifteen_direction=int(signals["15m"].direction),
            hour_direction=int(signals["1H"].direction),
            classification=sample_classification, evidence=sample_evidence)
    finally:
        sample_store.close()
    phase("hourly_boundary_sample_ms")
    # The original 1m price channel is display-only legacy configuration from
    # the first validation prototype.  It must not decide, reject or describe
    # current strategy-01 entries.
    persistent_market_text = market_summary
    expired_pullbacks = expired_missed_ma5_pullback_orders(
        snapshot, now_ms=int(time.time() * 1000))
    if expired_pullbacks:
        client.cancel_orders(expired_pullbacks)
        expiry_store = StateStore(database)
        try:
            expiry_store.record_event("missed_ma5_pullback_limit_expired", {
                "strategy_version": VALIDATION_VERSION,
                "order_ids": [str(item.get("ordId") or "") for item in expired_pullbacks],
                "ttl_seconds": MISSED_MA5_PULLBACK_TTL_SECONDS,
                "reason": "short pullback window elapsed; do not fill in a later market leg",
            })
        finally:
            expiry_store.close()
        snapshot = client.safety_snapshot()
    orphaned = client.orphaned_owned_trailing_orders(snapshot, "QBVAL")
    if orphaned:
        client.cancel_algo_orders(orphaned)
        snapshot = client.safety_snapshot()
    if cfg.strategy.risk_profile == "aggressive":
        sniper = execute_structure_sniper_tick(
            client, snapshot, cfg.okx.instruments[0], "QBVAL",
            markets["5m"], markets["15m"], markets["1m"],
            database=database, strategy_id="strategy_01", strategy_version=VALIDATION_VERSION,
            preferred_timeframes=("5m",), pivot_selection="latest",
            target_ma_timeframe="5m", max_levels_per_direction=2,
            allowed_directions=(-1, 1),
        )
        if sniper.action in {"placed", "reanchored", "invalidated", "sibling_cancelled"}:
            snapshot = client.safety_snapshot()
        elif sniper.action in {"early_exit", "suspended", "post_fill_exit", "layer_consolidated", "stop_tightened",
                               "protection_state_recovered", "protection_recovery_warning"}:
            return ValidationExecutionResult(
                sniper.action, f"{persistent_market_text}｜{sniper.reason}",
                ",".join(sniper.order_ids),
            )
        phase("order_housekeeping_and_sniper_ms")
        active_positions = [
            item for item in snapshot.get("positions", [])
            if str(item.get("posSide", "")) in {"long", "short"}
            and abs(float(item.get("pos") or 0)) > 0
        ]
        if active_positions:
            # Mark-triggered protection uses a fresh mark quote, not candle highs/MFE.
            protection_store = StateStore(database)
            try:
                quotes = client._request("GET", "/api/v5/public/mark-price", {
                    "instType": "SWAP", "instId": cfg.okx.instruments[0]}).get("data", [])
                if quotes and 0 <= time.time() * 1000 - int(quotes[0].get("ts") or 0) < 15000:
                    protect_owned_profit(client, protection_store, snapshot,
                                         cfg.okx.instruments[0], float(quotes[0]["markPx"]))
                else:
                    protection_store.record_event("profit_protection_stale_quote", {})
            except Exception as exc:
                protection_store.record_event("profit_protection_quote_failed", {
                    "error_type": type(exc).__name__})
            finally:
                protection_store.close()
        latest_audit_price = float(markets["1m"].sort_values("date").iloc[-1]["close"])
        followup_store = StateStore(database)
        try:
            followup_store.update_stop_followups(latest_audit_price)
        finally:
            followup_store.close()
        # Long and short are independent books in OKX hedge mode.  Evaluate
        # both on every scan: holding one side must never hide the opposite
        # side's exit management or its later entry signal.
        for active_position in active_positions:
            position_side = str(active_position.get("posSide", ""))
            exit_direction = 1 if position_side == "long" else -1
            average_entry = float(active_position.get("avgPx") or 0)
            exit_store = StateStore(database)
            try:
                owned_short = exit_store.has_open_same_side_trade(
                    "strategy_01", cfg.okx.instruments[0], exit_direction)
                open_rows = [row for row in exit_store.strategy_trade_lifecycles(
                    cfg.okx.instruments[0]) if row["status"] == "open"]
                owned_rows = [row for row in open_rows
                              if int(row["direction"]) == exit_direction
                              and str(row["instrument"]) == cfg.okx.instruments[0]]
                owned_row = owned_rows[0] if owned_rows else None
                entry_time = owned_row["signal_time"] if owned_row is not None else None
                owned_context = (json.loads(owned_row["signal_context_json"])
                                 if owned_row is not None else {})
                five_minute_core_hold = bool(
                    owned_context.get("five_minute_ma5_core_hold"))
                promoted_uids: list[str] = []
                strict_pair = exit_store.latest_confirmed_one_five_pair(
                    cfg.okx.instruments[0], exit_direction, require_ma5_cross=True)
                parent_only_pair = exit_store.latest_confirmed_one_five_pair(
                    cfg.okx.instruments[0], exit_direction, require_ma5_cross=False)
                ma20_takeover = five_minute_ma20_takeover_confirmed(
                    markets["5m"], exit_direction)
                # Repair any pre-v236 three-stage lifecycle that was born with
                # a 5m core flag before the two closed 5m MA20 holds existed.
                for lifecycle in owned_rows:
                    context = json.loads(str(lifecycle["signal_context_json"] or "{}"))
                    local_three_stage = (
                        str(context.get("entry_classification_code") or "")
                        in {"local_endpoint_reversal_long", "local_endpoint_reversal_short"}
                        or str(lifecycle["branch"] or "") in {
                            "five_minute_bottom_local_reversal_half_cover_long",
                            "five_minute_top_local_reversal_half_cover_short",
                        })
                    if (local_three_stage and not ma20_takeover
                            and bool(context.get("five_minute_ma5_core_hold"))):
                        context.update({
                            "position_class": "local_swing",
                            "ma5_exit_timeframe": "1m",
                            "five_minute_ma5_core_hold": False,
                            "ma5_exit_ownership_repaired": "v236_ma20_takeover_gate",
                        })
                        exit_store.update_trade_lifecycle_context(
                            str(lifecycle["trade_uid"]), context)
                        exit_store.record_event("ma5_exit_ownership_repaired", {
                            "trade_uid": str(lifecycle["trade_uid"]),
                            "direction": exit_direction,
                            "reason": "三阶段反转尚无连续两根已收盘5分钟K线站稳MA20",
                        })
                if strict_pair is not None or parent_only_pair is not None or ma20_takeover:
                    # A completed 1m+5m endpoint pair owns the whole exchange-side
                    # position. Upgrade every matching lifecycle, not only whichever
                    # row happened to be selected first.
                    for lifecycle in owned_rows:
                        context = json.loads(str(lifecycle["signal_context_json"] or "{}"))
                        if bool(context.get("sweep_trial_promoted")):
                            continue
                        higher_timeframe_pullback = (
                            str(context.get("entry_classification_category") or "")
                            == "higher_timeframe_trend_continuation")
                        local_three_stage = (
                            str(context.get("entry_classification_code") or "")
                            in {"local_endpoint_reversal_long", "local_endpoint_reversal_short"}
                            or str(lifecycle["branch"] or "") in {
                                "five_minute_bottom_local_reversal_half_cover_long",
                                "five_minute_top_local_reversal_half_cover_short",
                            })
                        qualifying_pair = parent_only_pair if higher_timeframe_pullback else strict_pair
                        if local_three_stage:
                            if not ma20_takeover:
                                continue
                            promotion_reason = (
                                "底部/顶部反转三阶段入场后，连续两根已收盘5分钟K线"
                                "站稳MA20新趋势侧，5分钟MA5接管止盈")
                        else:
                            if (qualifying_pair is None
                                    or not lifecycle_pair_is_fresh_for_promotion(
                                        lifecycle, qualifying_pair)):
                                continue
                            promotion_reason = (
                                "1分钟+5分钟末端已配对；5分钟反向K线已覆盖前一根实体一半；"
                                + ("15分钟上级回踩/反抽身份免等一分钟MA5穿越"
                                   if higher_timeframe_pullback else
                                   "1分钟收盘价已成功穿过MA5"))
                        trailing_algo_id = str(lifecycle["algo_id"] or "").strip()
                        if trailing_algo_id:
                            # The original server trailing order would otherwise
                            # close a newly promoted core before the 5m MA5 turns.
                            client.cancel_algo_orders([{
                                "instId": cfg.okx.instruments[0],
                                "algoId": trailing_algo_id,
                            }])
                        context.update({
                            "position_class": "ma_spread_endpoint_reversal",
                            "ma5_exit_timeframe": "5m",
                            "five_minute_ma5_core_hold": True,
                            "sweep_trial_promoted": True,
                            "sweep_trial_promotion_source": "paired_1m_5m_ma_endpoint_lineage",
                            "sweep_trial_promotion_reason": promotion_reason,
                        })
                        upgraded_identity = classify_entry_category(
                            exit_direction, true_endpoint_reversal=True)
                        context.update({
                            "entry_classification_code": upgraded_identity["code"],
                            "entry_classification_category": upgraded_identity["category"],
                            "entry_classification_label": upgraded_identity["label"],
                            "entry_classification_rule": upgraded_identity["rule"],
                            "entry_trend_source_timeframe": upgraded_identity["trend_source_timeframe"],
                        })
                        trade_uid = str(lifecycle["trade_uid"])
                        exit_store.update_trade_lifecycle_context(trade_uid, context)
                        if trailing_algo_id:
                            exit_store.clear_trade_trailing_algo(trade_uid)
                        promoted_uids.append(trade_uid)
                        exit_store.record_event("sweep_trial_endpoint_promoted", {
                            "strategy_version": VALIDATION_VERSION,
                            "trade_uid": trade_uid, "direction": exit_direction,
                            "exit_timeframe": "5m", "reason": promotion_reason,
                        })
                    paired_keys = [str(row["event_key"])
                                   for row in exit_store.recent_ma_endpoint_lineage(
                                       cfg.okx.instruments[0], 300)
                                   if int(row["direction"]) == exit_direction
                                   and str(row["pair_status"]) == "paired"
                                   and str(row["timeframe"]) == "1m"]
                    exit_store.mark_ma_endpoint_trade_upgrades(
                        paired_keys[:1], promoted_uids)
                    five_minute_core_hold = bool(promoted_uids)
                # The exchange aggregates same-side contracts, while the
                # lifecycle table keeps their roles. A 1m-MA5 continuation
                # layer is reduced first; only the remaining core waits for
                # the 5m MA5 turn.
                refreshed_rows = [row for row in exit_store.strategy_trade_lifecycles(
                                  cfg.okx.instruments[0]) if row["status"] == "open"
                                  if int(row["direction"]) == exit_direction
                                  and str(row["instrument"]) == cfg.okx.instruments[0]]
                managed_row, five_minute_core_hold = select_ma5_exit_lifecycle(
                    refreshed_rows)
                if managed_row is not None:
                    owned_row = managed_row
                    entry_time = owned_row["signal_time"]
                    average_entry = float(owned_row["entry_reference"] or average_entry)
                    dates = pd.to_datetime(markets["1m"]["date"], utc=True)
                    observed_context = json.loads(str(owned_row["signal_context_json"] or "{}"))
                    submitted_at = observed_context.get("execution_observed_at")
                    # Signal bars can precede submission by minutes. Only complete
                    # post-submission bars may contribute highs/lows to MFE/MAE.
                    start = (pd.to_datetime(submitted_at, utc=True).ceil("min")
                             if submitted_at else pd.Timestamp.now(tz="UTC"))
                    held = markets["1m"].loc[dates >= start]
                    if not held.empty:
                        exit_store.update_trade_excursion(
                            str(owned_row["trade_uid"]),
                            observed_high=float(held["high"].astype(float).max()),
                            observed_low=float(held["low"].astype(float).min()),
                            observed_price=float(held.iloc[-1]["close"]),
                        )
                managed_trade_uid = (str(owned_row["trade_uid"])
                                     if owned_row is not None else "")
                # v0.7.80 retires the MA5 anchor hold for both newly opened and
                # already-existing positions.  Once an exit rule is true, do
                # not erase it merely because MA5 has not revisited an old
                # entry plateau; that lock converted open profit into losses.
            finally:
                exit_store.close()
            exit_reason = (aggressive_long_ma5_exit_reason(
                               markets["1m"], average_entry, markets["5m"], markets["15m"],
                               markets["1H"], entry_time=entry_time,
                               prefer_five_minute_hold=five_minute_core_hold)
                           if exit_direction > 0 else
                           aggressive_short_ma5_exit_reason(
                                markets["1m"], average_entry, markets["5m"], markets["15m"],
                                markets["1H"], entry_time=entry_time,
                                 prefer_five_minute_hold=five_minute_core_hold))
            takeover_store = StateStore(database)
            try:
                reversal_takeover_reason = opposite_reversal_exit_confirmation(
                    exit_direction,
                    list(takeover_store.open_trade_lifecycles(VALIDATION_VERSION)),
                    takeover_store.active_one_minute_reversal_trend_lock(
                        cfg.okx.instruments[0]),
                    entry_time,
                    str(owned_row["branch"] or "") if owned_row is not None else "",
                )
            finally:
                takeover_store.close()
            reversal_takeover = bool(reversal_takeover_reason)
            if reversal_takeover:
                exit_reason = (
                    "opposite_bottom_reversal_takeover_close_all_shorts"
                    if exit_direction < 0 else
                    "opposite_top_reversal_takeover_close_all_longs"
                )
            current_exit_price = float(markets["1m"].sort_values("date").iloc[-1]["close"])
            formal_ma5_turn = confirmed_ma5_turn_exit(exit_reason, exit_direction)
            if active_exit_requires_fee_gate(
                    exit_reason, exit_direction, reversal_takeover=reversal_takeover):
                fee_ok, fee_reason = fee_aware_active_exit_allows(
                    average_entry, current_exit_price, exit_direction)
                if not fee_ok:
                    audit_store = StateStore(database)
                    try:
                        audit_store.record_event("active_exit_deferred_by_fee_gate", {
                            "trade_uid": managed_trade_uid, "reason": exit_reason,
                            "fee_gate": fee_reason, "current_price": current_exit_price,
                            "strategy_version": VALIDATION_VERSION,
                        })
                    finally:
                        audit_store.close()
                    exit_reason = ""
            elif exit_reason and formal_ma5_turn:
                audit_store = StateStore(database)
                try:
                    audit_store.record_event("confirmed_ma5_turn_exit_bypassed_fee_gate", {
                        "trade_uid": managed_trade_uid, "reason": exit_reason,
                        "current_price": current_exit_price,
                        "strategy_version": VALIDATION_VERSION,
                    })
                finally:
                    audit_store.close()
            if exit_reason and owned_short:
                close_units = (client.position_internal_units(active_position)
                               if reversal_takeover else 1)
                response = client.close_demo_position_market(
                    close_units, position_side=position_side, enabled=True,
                    max_contracts=3 if reversal_takeover else 1,
                    confirmation="DEMO-ORDER", inst_id=cfg.okx.instruments[0],
                )
                order = (response.get("data") or [{}])[0]
                exit_store = StateStore(database)
                try:
                    if reversal_takeover:
                        exit_store.mark_trade_exit_requested(
                            "strategy_01", cfg.okx.instruments[0], exit_direction, exit_reason)
                    else:
                        exit_store.mark_trade_exit_requested_by_uid(
                            managed_trade_uid, exit_reason)
                    exit_store.record_event("aggressive_active_exit_submitted", {
                        "reason": exit_reason, "ordId": order.get("ordId"),
                        "trade_uid": managed_trade_uid,
                        "exit_timeframe": "5m" if five_minute_core_hold else "1m",
                        "average_entry": average_entry,
                        "confirmed_bar_time": signals["1m"].candle_time.isoformat(),
                        "reversal_takeover": reversal_takeover,
                        "closed_layers": close_units,
                        "opposite_entry_trigger": reversal_takeover_reason,
                    })
                finally:
                    exit_store.close()
                return ValidationExecutionResult("exit_submitted", exit_reason, str(order.get("ordId", "")))
    if client.unprotected_positions(snapshot):
        snapshot, unprotected = confirm_unprotected_positions(client, snapshot)
        if unprotected:
            emergency_exits = emergency_close_unprotected_quantity(client, database, unprotected)
            if emergency_exits:
                return ValidationExecutionResult(
                    "unprotected_emergency_exit",
                    f"{persistent_market_text}｜连续3份OKX安全快照确认保护数量不足；"
                    "已立即市价只减仓平掉未被服务器止损覆盖的持仓量，"
                    "保留已有止损覆盖的层；本轮禁止新开仓",
                    ",".join(emergency_exits),
                )
            sides = ", ".join(
                f"{item.get('instId', '')}:{item.get('posSide', '')}:"
                f"持仓{item.get('pos', '0')}/已保护{item.get('protection_covered', '0')}/"
                f"缺口{item.get('protection_deficit', '0')}"
                for item in unprotected
            )
            return ValidationExecutionResult(
                "protection_alert",
                f"{persistent_market_text}｜严重警报：连续3份OKX安全快照均确认持仓没有有效止盈止损"
                f"（{sides}），禁止新开仓",
            )
        return ValidationExecutionResult("manage", f"{persistent_market_text}｜现有持仓/委托由OKX服务器端止盈止损管理")
    # The MA5 anchor locks only the exit of its own side.  In OKX long/short
    # mode it must not suppress evaluation of an independent opposite signal;
    # otherwise a protected long can silently hide a valid short (and vice
    # versa).  Same-side duplicate gates remain below.
    store = StateStore(database)
    try:
        rows_before_reconcile = list(store.open_trade_lifecycles(VALIDATION_VERSION))
        closed_trade_uids = _reconcile_closed_validation_trades(
            store, client, cfg.okx.instruments[0])
        if closed_trade_uids:
            # The reduce-only fill and its attached stop can advance in
            # different OKX snapshots.  Refresh after the fill, then remove
            # only the protection belonging to lifecycle layers now closed.
            snapshot = client.safety_snapshot()
            open_rows_after = list(store.open_trade_lifecycles(VALIDATION_VERSION))
            closed_uid_set = set(closed_trade_uids)
            closed_rows = [row for row in rows_before_reconcile
                           if str(row["trade_uid"]) in closed_uid_set]
            stale_stops = stale_layer_protective_stops(
                snapshot, open_rows_after, closed_rows, cfg.okx.instruments[0])
            if stale_stops:
                client.cancel_algo_orders(stale_stops)
                store.record_event("closed_layer_protective_stops_cancelled", {
                    "strategy_version": VALIDATION_VERSION,
                    "closed_trade_uids": list(closed_trade_uids),
                    "cancelled_algo_ids": [item["algoId"] for item in stale_stops],
                    "reason": "remaining real position owns only remaining lifecycle protection",
                })
                snapshot = client.safety_snapshot()
        active_price_zone_keys: list[str] = []
        # Persist the complete operator-facing endpoint lineage.  1H is an
        # explanation/classification parent; it still cannot independently
        # veto a lower-timeframe execution.
        for timeframe in ("1m", "5m", "15m", "1H"):
            reversal = latest_price_reversal(markets[timeframe])
            if reversal is None:
                continue
            five_minute_shape_confirmed = five_minute_price_reversal_confirms(
                markets["5m"], int(reversal["direction"])
            )
            market_shape = classify_reversal_context(
                markets["1m"], markets["5m"], int(reversal["direction"])
            )
            zone_time = pd.Timestamp(reversal["time"])
            zone_key = (f"{cfg.okx.instruments[0]}|price_reversal_zone|{timeframe}|"
                        f"{int(reversal['direction'])}|{zone_time.isoformat()}")
            active_price_zone_keys.append(zone_key)
            for old in store.pending_market_patterns(cfg.okx.instruments[0]):
                if (str(old["pattern_type"]) == f"price_reversal_zone:{timeframe}"
                        and str(old["event_key"]) != zone_key):
                    store.resolve_market_pattern(
                        str(old["event_key"]), outcome_status="missed_before_next_reversal",
                        outcome={"summary": "下一普通K线反转区已经形成但前区未成交",
                                 "reason_source": "validation_observation审计事件",
                                 "strategy_version": VALIDATION_VERSION},
                    )
            endpoint_frame = markets[timeframe].sort_values("date")
            endpoint_close = endpoint_frame["close"].astype(float)
            endpoint_ma5 = endpoint_close.rolling(5).mean()
            endpoint_ma20 = endpoint_close.rolling(20).mean()
            fan_endpoint = recent_ma_fan_endpoint(
                endpoint_frame, int(reversal["direction"]),
                lookback=8 if timeframe == "1m" else 6,
            )
            five_ma20_reversal_confirmed = bool(
                timeframe == "5m" and pd.notna(endpoint_ma20.iloc[-1])
                and int(reversal["direction"]) * (
                    float(endpoint_close.iloc[-1]) - float(endpoint_ma20.iloc[-1])) > 0)
            store.record_market_pattern(
                event_key=zone_key, instrument=cfg.okx.instruments[0],
                pattern_type=f"price_reversal_zone:{timeframe}",
                direction=int(reversal["direction"]),
                confirmed_bar_time=zone_time.isoformat(), status="watching_three_stage",
                entry_reference=float(reversal["price"]),
                stop_reference=float(reversal["extreme"]),
                features={"candle_mode": "raw", "timeframe": timeframe,
                          "raw_execution_prices": True, "run_bars": reversal["run_bars"],
                          "price_action_trigger": reversal["trigger"],
                          "five_minute_shape_confirmed": five_minute_shape_confirmed,
                          "ma_fan_endpoint_confirmed": bool(fan_endpoint),
                          **market_shape,
                          "five_minute_ma20_reversal_confirmed": five_ma20_reversal_confirmed,
                          "strategy_version": VALIDATION_VERSION},
            )
            previous_cross_side = bool(
                len(endpoint_close) >= 2 and pd.notna(endpoint_ma5.iloc[-2])
                and int(reversal["direction"]) * (
                    float(endpoint_close.iloc[-2]) - float(endpoint_ma5.iloc[-2])) <= 0)
            current_cross_side = bool(
                pd.notna(endpoint_ma5.iloc[-1]) and int(reversal["direction"]) * (
                    float(endpoint_close.iloc[-1]) - float(endpoint_ma5.iloc[-1])) > 0)
            parent_half_cover = bool(
                market_shape.get("five_minute_half_cover")) if timeframe == "5m" else (
                    "过半覆盖" in str(reversal.get("trigger") or ""))
            if fan_endpoint is not None:
                store.record_ma_endpoint(
                    event_key=zone_key, instrument=cfg.okx.instruments[0],
                    timeframe=timeframe, direction=int(reversal["direction"]),
                    confirmed_bar_time=zone_time.isoformat(),
                    endpoint_price=float(reversal["price"]),
                    extreme_price=float(fan_endpoint["extreme"]),
                    ma5=float(fan_endpoint["ma5"]),
                    ma10=float(fan_endpoint["ma10"]),
                    ma20=float(fan_endpoint["ma20"]),
                    half_cover_confirmed=parent_half_cover,
                    slow_ma_cross_confirmed=five_ma20_reversal_confirmed,
                    ma5_cross_confirmed=bool(previous_cross_side and current_cross_side),
                    confirmation_reason=(
                        f"MA5/MA20发散{fan_endpoint['spread_atr']:.2f}ATR且收盘位于两线外侧；MA10仅记录；"
                        + ("前一根K线实体过半覆盖" if parent_half_cover
                           else "未覆盖前一根K线实体一半")),
                )
        one_frame = markets["1m"].sort_values("date")
        if len(one_frame) >= 21:
            one_close = one_frame["close"].astype(float)
            one_ma5 = one_close.rolling(5).mean()
            for cross_direction in (-1, 1):
                if (cross_direction * (float(one_close.iloc[-2]) - float(one_ma5.iloc[-2])) <= 0
                        and cross_direction * (float(one_close.iloc[-1]) - float(one_ma5.iloc[-1])) > 0):
                    store.confirm_recent_endpoint_ma5_cross(
                        cfg.okx.instruments[0], cross_direction,
                        pd.Timestamp(one_frame.iloc[-1]["date"]).isoformat())
        held_directions = {
            1 if str(item.get("posSide", "")) == "long" else -1
            for item in snapshot.get("positions", [])
            if str(item.get("posSide", "")) in {"long", "short"}
            and abs(float(item.get("pos") or 0)) > 0
        }
        for held_direction in held_directions:
            promote_newly_paired_local_trials(
                store, client, cfg.okx.instruments[0], held_direction)
        active_price_zone_keys = [
            str(row["event_key"]) for row in store.pending_market_patterns(cfg.okx.instruments[0])
            if str(row["pattern_type"]).startswith("price_reversal_zone:")
        ]
        now = datetime.now(timezone.utc)
        submitted_today = store.submitted_intent_count(now.date(), VALIDATION_VERSION)
        if submitted_today >= cfg.okx.validation_max_trades_per_day:
            return ValidationExecutionResult("blocked", "daily validation trade limit reached")
        latest = store.latest_submitted_at(VALIDATION_VERSION)
        if latest and (now - latest).total_seconds() < cfg.risk.cooldown_seconds:
            return ValidationExecutionResult("blocked", "validation cooldown is active")
        def observe(reason: str) -> ValidationExecutionResult:
            store.record_event("validation_observation", {
                "strategy_version": VALIDATION_VERSION, "reason": reason,
                "directions": {bar: signal.direction for bar, signal in signals.items()},
                "price_reversal_zone_keys": active_price_zone_keys,
            })
            return ValidationExecutionResult("observe", reason)
        direction, hierarchy_reason = 0, "等待新鲜1分钟结构/均线触发；15分钟与1小时仅作背景"
        extreme_entry = False
        terminal_reversal_entry = False
        three_bear_entry = False
        five_minute_color_rollover_short_entry = False
        volume_stopping_entry = False
        volume_stopping_target = 0.0
        early_low_sweep_entry = False
        early_high_sweep_entry = False
        ma20_pullback_entry = False
        trend_continuation_entry = False
        double_rejection_entry = False
        small_bottom_rebound_entry = False
        delayed_five_ma5_bottom_entry = False
        two_timeframe_intrabar_ma5_reversal_entry = False
        one_minute_small_cross_entry = False
        five_minute_small_cross_entry = False
        aggressive_recovery_long_entry = False
        fifteen_minute_recovery_long_entry = False
        intrabar_rebound_long_entry = False
        first_ma20_retest_long_entry = False
        intrabar_ma20_short_entry = False
        intrabar_range_ma5_short_entry = False
        intrabar_bear_engulf_short_entry = False
        top_weakening_ma5_short_entry = False
        compact_top_reversal_short_entry = False
        expanded_ma_top_short_entry = False
        five_minute_high_half_cover_short_entry = False
        stage_one_top_short_entry = False
        breakout_pullback_entry = False
        waterfall_micro_entry = False
        dual_ma20_cross_continuation_entry = False
        multi_timeframe_weakness_short_entry = False
        five_minute_bearish_rollover_short_entry = False
        early_dual_pullback_cover_long_entry = False
        three_timeframe_reversal_entry = False
        three_timeframe_reversal_mode = ""
        persistent_cover_recovery_entry = False
        selected_frozen_cover: dict | None = None
        structural_stop = 0.0
        latest_market_time = pd.Timestamp(markets["1m"].sort_values("date").iloc[-1]["date"])
        pending_patterns = store.pending_market_patterns(cfg.okx.instruments[0])
        pattern_history = store.recent_market_patterns(cfg.okx.instruments[0])
        dual_reversal_bias, dual_reversal_bias_reason = durable_dual_reversal_zone_bias(
            pattern_history, latest_market_time)
        direct_one_minute_lock = store.latest_active_endpoint_direction_lock(
            cfg.okx.instruments[0], "1m")
        inherited_five_minute_lock = store.latest_active_endpoint_direction_lock(
            cfg.okx.instruments[0], "5m")
        lock_candidates = [lock for lock in (
            direct_one_minute_lock, inherited_five_minute_lock) if lock is not None]
        active_endpoint_pair = max(
            lock_candidates,
            key=lambda lock: pd.to_datetime(lock["confirmed_bar_time"], utc=True),
        ) if lock_candidates else None
        if active_endpoint_pair is not None:
            dual_reversal_bias = int(active_endpoint_pair["direction"])
            pair_completed_at = max(
                pd.to_datetime(active_endpoint_pair["confirmed_bar_time"], utc=True),
                pd.to_datetime(active_endpoint_pair["parent_bar_time"], utc=True),
            )
            dual_reversal_bias_reason = (
                f"最新已收盘{active_endpoint_pair['timeframe']}单周期末端已于{pair_completed_at.isoformat()}锁定"
                f"{'顶部做空/下跌趋势' if dual_reversal_bias < 0 else '底部做多/上涨趋势'}；"
                "上一组相反方向锁定已释放"
            )
        fresh_one_lock = store.active_one_minute_reversal_trend_lock(
            cfg.okx.instruments[0])
        failed_endpoint_event_key = ""
        if fresh_one_lock is not None:
            lock_state, lock_reason, lock_time = one_minute_reversal_trend_lock_state(
                markets["1m"], int(fresh_one_lock["direction"]),
                fresh_one_lock["anchor_time"], float(fresh_one_lock["anchor_extreme"]),
                already_confirmed=True)
            if lock_state == "failed":
                store.fail_one_minute_reversal_trend_lock(
                    cfg.okx.instruments[0], lock_time.isoformat(), lock_reason)
                store.record_event("one_minute_reversal_trend_lock_failed", {
                    "strategy_version": VALIDATION_VERSION,
                    "direction": int(fresh_one_lock["direction"]),
                    "endpoint_event_key": fresh_one_lock["endpoint_event_key"],
                    "reason": lock_reason,
                })
                failed_endpoint_event_key = str(fresh_one_lock["endpoint_event_key"])
                fresh_one_lock = None
        latest_one_endpoint = store.latest_one_minute_fan_endpoint(
            cfg.okx.instruments[0])
        takeover_direction, takeover_reason, takeover_time = (
            five_minute_ma20_trend_takeover(
                markets["5m"], markets.get("5m_live")))
        if latest_one_endpoint is not None and takeover_direction:
            endpoint_time_for_takeover = pd.to_datetime(
                latest_one_endpoint.get("zone_last_seen_at")
                or latest_one_endpoint["confirmed_bar_time"], utc=True)
            takeover_time_utc = pd.to_datetime(takeover_time, utc=True)
            endpoint_is_matching_and_fresh = bool(
                int(latest_one_endpoint["direction"]) == takeover_direction
                and endpoint_time_for_takeover <= takeover_time_utc
                and takeover_time_utc - endpoint_time_for_takeover <= pd.Timedelta(minutes=90)
            )
            if endpoint_is_matching_and_fresh:
                store.confirm_one_minute_reversal_trend_lock(
                    instrument=cfg.okx.instruments[0],
                    endpoint_event_key=str(latest_one_endpoint["event_key"]),
                    direction=takeover_direction,
                    anchor_time=endpoint_time_for_takeover.isoformat(),
                    anchor_extreme=float(latest_one_endpoint["extreme_price"]),
                    confirmed_at=takeover_time_utc.isoformat(),
                    reason=takeover_reason,
                )
                store.record_event("five_minute_ma20_trend_takeover_confirmed", {
                    "strategy_version": VALIDATION_VERSION,
                    "instrument": cfg.okx.instruments[0],
                    "direction": takeover_direction,
                    "endpoint_event_key": latest_one_endpoint["event_key"],
                    "reason": takeover_reason,
                })
                fresh_one_lock = store.active_one_minute_reversal_trend_lock(
                    cfg.okx.instruments[0])
        if latest_one_endpoint is not None:
            endpoint_time = str(latest_one_endpoint.get("zone_last_seen_at")
                                or latest_one_endpoint["confirmed_bar_time"])
            existing_time = (pd.to_datetime(fresh_one_lock["anchor_time"], utc=True)
                             if fresh_one_lock is not None else None)
            is_new_endpoint = (str(latest_one_endpoint["event_key"])
                               != failed_endpoint_event_key and
                               (existing_time is None
                                or pd.to_datetime(endpoint_time, utc=True) > existing_time))
            direction_value = int(latest_one_endpoint["direction"])
            if is_new_endpoint:
                lock_state, lock_reason, lock_time = one_minute_reversal_trend_lock_state(
                    markets["1m"], direction_value, endpoint_time,
                    float(latest_one_endpoint["extreme_price"]))
                if lock_state == "confirmed":
                    store.confirm_one_minute_reversal_trend_lock(
                        instrument=cfg.okx.instruments[0],
                        endpoint_event_key=str(latest_one_endpoint["event_key"]),
                        direction=direction_value, anchor_time=endpoint_time,
                        anchor_extreme=float(latest_one_endpoint["extreme_price"]),
                        confirmed_at=lock_time.isoformat(), reason=lock_reason)
                    store.record_event("one_minute_reversal_trend_lock_confirmed", {
                        "strategy_version": VALIDATION_VERSION,
                        "direction": direction_value,
                        "endpoint_event_key": latest_one_endpoint["event_key"],
                        "reason": lock_reason,
                    })
                    fresh_one_lock = store.active_one_minute_reversal_trend_lock(
                        cfg.okx.instruments[0])
        pending_takeover_direction, pending_takeover_reason = (
            fresh_endpoint_suspends_opposite_trend(
                endpoint=latest_one_endpoint, old_bias=dual_reversal_bias,
                now=latest_market_time, one_minute=markets["1m"])
        )
        if pending_takeover_direction:
            store.record_event("fresh_endpoint_suspends_old_trend_entries", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "old_direction": dual_reversal_bias,
                "pending_direction": pending_takeover_direction,
                "endpoint_event_key": latest_one_endpoint["event_key"],
                "reason": pending_takeover_reason,
            })
        if fresh_one_lock is not None:
            dual_reversal_bias = int(fresh_one_lock["direction"])
            dual_reversal_bias_reason = (
                f"一分钟新鲜底部/顶部反转已于{fresh_one_lock['confirmed_at']}完成MA20接管，"
                f"优先锁定{'上涨' if dual_reversal_bias > 0 else '下降'}趋势；"
                "无论反转试单成交或漏单都有效；若反转失败则自动撤销并恢复上级旧趋势"
            )
        opposite_top_time = (
            latest_one_endpoint["confirmed_bar_time"]
            if latest_one_endpoint is not None and int(latest_one_endpoint["direction"]) < 0
            else None)
        five_uptrend_pullback_context = confirmed_five_minute_uptrend_pullback_context(
            markets["5m"], opposite_top_time)
        if five_uptrend_pullback_context and dual_reversal_bias < 0:
            dual_reversal_bias = 1
            dual_reversal_bias_reason = (
                "最近两根已收盘5分钟K线站上上行MA20，MA5高于MA10且回踩低点抬高；"
                "上涨趋势接管旧下降锁，后续仍须新鲜1分钟回踩及完整风控")
            if pending_takeover_direction < 0:
                pending_takeover_direction = 0
                pending_takeover_reason = ""
            store.record_event("five_minute_uptrend_releases_old_downtrend", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "reason": dual_reversal_bias_reason,
            })
        three_reversal_bias, three_reversal_bias_reason = (
            durable_three_timeframe_reversal_zone_bias(
                pattern_history, latest_market_time)
        )
        # A newer successful 1m+5m opposite endpoint changes direction at
        # once.  A matching 15m endpoint upgrades confidence, but an older
        # 15m record must never veto that fresh dual-timeframe turn.
        if three_reversal_bias and three_reversal_bias == dual_reversal_bias:
            dual_reversal_bias_reason = (
                f"{dual_reversal_bias_reason} | {three_reversal_bias_reason}"
            )
        early_pullback_long, early_pullback_reason, early_pullback_stop = (
            early_dual_timeframe_pullback_cover_long_setup(markets["1m"], markets["5m"])
            if dual_reversal_bias > 0 else (False, "双周期底部反转背景未确认", 0.0)
        )
        trend_regime = classify_trend_regime(markets["5m"], markets["15m"])
        dual_ma5_direction, dual_ma5_reason, dual_ma5_stop = (
            aggressive_two_timeframe_intrabar_ma5_reversal_setup(
                markets["1m"], markets.get("1m_live"),
                markets["5m"], markets.get("5m_live"),
            ) if cfg.strategy.risk_profile == "aggressive" else (0, "", 0.0)
        )
        small_cross_direction, small_cross_reason, small_cross_stop = (
            one_minute_local_extreme_ma5_ma10_cross_setup(
                markets["1m"], markets.get("1m_live"))
            if cfg.strategy.risk_profile == "aggressive" else (0, "", 0.0)
        )
        five_small_cross_direction, five_small_cross_reason, five_small_cross_stop = (
            one_minute_local_extreme_ma5_ma10_cross_setup(
                markets["5m"], markets.get("5m_live"), timeframe_label="五分钟")
            if cfg.strategy.risk_profile == "aggressive" else (0, "", 0.0)
        )
        cross_observations = [item for item in (
            ma5_ma10_cross_observation(
                markets["1m"], markets.get("1m_live"), timeframe="1m"),
            ma5_ma10_cross_observation(
                markets["5m"], markets.get("5m_live"), timeframe="5m"),
        ) if item is not None]
        freeze_observations = one_minute_launch_freeze_observations(
            markets["1m"], markets.get("1m_live"), candle_mode="raw")
        pending_freezes_before_tick = [
            row for row in store.pending_market_patterns(cfg.okx.instruments[0])
            if str(row["pattern_type"]).startswith("one_minute_launch_freeze:")
        ]
        frozen_stage_directions: set[int] = set()
        accepted_freeze_observations: list[dict] = []
        for frozen in freeze_observations:
            location_ok, location_text = local_extreme_cross_location_allows(
                markets["1m"], int(frozen["direction"]))
            frozen_time = pd.Timestamp(frozen["time"])
            anchored_stage = str(frozen["stage"]) in {
                "confirmed_local_bottom", "confirmed_local_top",
                "bottom_sweep_reclaim", "top_sweep_reject",
                "bottom_half_bearish_cover", "top_half_bullish_cover",
                "relative_local_bottom_sweep", "relative_local_top_sweep",
            }
            existing_same_direction = bool(matching_frozen_launch_candidates(
                pending_freezes_before_tick, int(frozen["direction"]), frozen_time,
            )) or int(frozen["direction"]) in frozen_stage_directions
            if anchored_stage:
                location_ok = True
                location_text = "一分钟局部顶底/扫损结构本身建立冻结锚点，不要求仍位于大区间边缘"
            elif existing_same_direction:
                location_ok = True
                location_text = "已有同方向局部顶底冻结；后续MA5/小交叉允许离开绝对顶底继续刷新"
            if not location_ok:
                continue
            frozen_stage_directions.add(int(frozen["direction"]))
            accepted_freeze_observations.append(frozen)
            store.record_market_pattern(
                event_key=(f"{cfg.okx.instruments[0]}|one_minute_launch_freeze|"
                           f"{frozen['stage']}|{int(frozen['direction'])}|{frozen_time.isoformat()}"),
                instrument=cfg.okx.instruments[0],
                pattern_type=f"one_minute_launch_freeze:{frozen['stage']}",
                direction=int(frozen["direction"]),
                confirmed_bar_time=frozen_time.isoformat(),
                status="frozen_waiting_big_cross",
                entry_reference=float(frozen["price"]),
                stop_reference=float(frozen["stop"]),
                features={"stage": frozen["stage"], "location": location_text,
                          "strategy_version": VALIDATION_VERSION},
            )
        early_launch_direction, early_launch_reason, early_launch_stop = (
            one_minute_ma5_ma20_early_launch(markets["1m"], markets.get("1m_live")))
        big_cross_direction, big_cross_reason, big_cross_stop = (
            one_minute_big_cross_launch(markets["1m"], markets.get("1m_live")))
        frozen_big_cross_direction = early_launch_direction or big_cross_direction
        frozen_big_cross_reason = early_launch_reason if early_launch_direction else big_cross_reason
        frozen_big_cross_stop = early_launch_stop if early_launch_direction else big_cross_stop
        frozen_launch_candidates = []
        if frozen_big_cross_direction:
            cross_frame = (markets["1m_live"] if markets.get("1m_live") is not None
                           and not markets["1m_live"].empty else markets["1m"])
            latest_cross_candle = cross_frame.sort_values("date").iloc[-1]
            big_cross_time = pd.Timestamp(latest_cross_candle["date"])
            big_cross_price = float(latest_cross_candle["close"])
            frozen_launch_candidates = matching_frozen_launch_candidates(
                store.pending_market_patterns(cfg.okx.instruments[0]),
                frozen_big_cross_direction, big_cross_time,
            )
            frozen_launch_candidates = [
                row for row in frozen_launch_candidates
                if one_minute_launch_quality_gate(
                    cross_frame, frozen_big_cross_direction,
                    float(row["entry_reference"] or big_cross_price),
                )[0]
            ]
        for cross_item in cross_observations:
            other_timeframe = "5m" if cross_item["timeframe"] == "1m" else "1m"
            compared = store.latest_ma_cross(
                cfg.okx.instruments[0], other_timeframe, cross_item["direction"])
            compared_time = str(compared["cross_time"]) if compared is not None else None
            gap_seconds = (abs((cross_item["cross_time"] - pd.to_datetime(compared_time)).total_seconds())
                           if compared_time else None)
            payload = {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "timeframe": cross_item["timeframe"],
                "cross_time": cross_item["cross_time"].isoformat(),
                "direction": cross_item["direction"],
                "cross_name": cross_item["cross_name"],
                "price": cross_item["price"],
                "range_position": cross_item["range_position"],
                "valid_local_extreme": cross_item["valid"],
                "decision": "有效候选" if cross_item["valid"] else "仅记录",
                "reason": cross_item["reason"],
                "compared_timeframe": other_timeframe if compared is not None else None,
                "compared_cross_time": compared_time,
                "comparison_gap_seconds": gap_seconds,
            }
            inserted = store.record_ma_cross(
                instrument=payload["instrument"], timeframe=payload["timeframe"],
                cross_time=payload["cross_time"], direction=payload["direction"],
                cross_name=payload["cross_name"], price=payload["price"],
                range_position=payload["range_position"],
                valid_local_extreme=payload["valid_local_extreme"],
                decision=payload["decision"], reason=payload["reason"],
                compared_timeframe=payload["compared_timeframe"],
                compared_cross_time=payload["compared_cross_time"],
                comparison_gap_seconds=payload["comparison_gap_seconds"],
            )
            if inserted:
                store.record_event("ma5_ma10_small_cross_observed", payload)
        if dual_ma5_direction:
            dual_location_ok, dual_location_reason = local_extreme_cross_location_allows(
                markets["1m"], dual_ma5_direction)
            if not dual_location_ok:
                dual_ma5_direction = 0
                dual_ma5_reason = f"{dual_ma5_reason}；{dual_location_reason}，禁止横盘中部绕过小交叉位置门"
        intrabar_long, intrabar_reason, intrabar_stop = (
            aggressive_intrabar_doji_ma5_cross_long_setup(
                markets["1m"], markets.get("1m_live"))
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        ma20_retest_live_long, ma20_retest_live_reason, ma20_retest_live_stop = (
            aggressive_first_ma20_retest_intrabar_long_setup(
                markets["1m"], markets.get("1m_live"), markets["5m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        intrabar_live_short, intrabar_live_short_reason, intrabar_live_short_stop = (
            aggressive_intrabar_ma20_break_short_setup(
                markets["1m"], markets.get("1m_live"), markets["5m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        intrabar_range_short, intrabar_range_short_reason, intrabar_range_short_stop = (
            aggressive_range_top_doji_ma5_intrabar_short_setup(
                markets["1m"], markets.get("1m_live"), markets["5m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        intrabar_engulf_short, intrabar_engulf_short_reason, intrabar_engulf_short_stop = (
            aggressive_local_top_intrabar_bear_engulf_short_setup(
                markets["1m"], markets.get("1m_live"))
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        top_weakening_short, top_weakening_reason, top_weakening_stop = (
            aggressive_top_weakening_ma5_short_setup(markets["1m"], markets.get("1m_live"))
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        complete_one_minute = merge_closed_and_live_market(
            markets["1m"], markets.get("1m_live"))
        complete_five_minute = merge_closed_and_live_market(
            markets["5m"], markets.get("5m_live"))
        complete_fifteen_minute = merge_closed_and_live_market(
            markets["15m"], markets.get("15m_live"))
        frozen_cover_anchors: dict[int, dict] = {}
        for cover_direction in (-1, 1):
            frozen = freeze_live_five_minute_cover_anchor(
                store, cfg.okx.instruments[0], complete_one_minute,
                complete_five_minute, cover_direction)
            if frozen is not None:
                frozen_cover_anchors[cover_direction] = frozen
        persistent_cover_recoveries: dict[int, tuple[str, dict, float]] = {}
        for cover_direction in (-1, 1):
            (recovery_ok, recovery_reason, recovery_anchor, recovery_lock_state,
             recovery_lock_time, recovery_extreme) = persisted_directional_cover_recovery(
                store, cfg.okx.instruments[0], complete_one_minute,
                complete_fifteen_minute, cover_direction)
            current_cover_ok, _ = fresh_single_candle_half_cover(
                complete_five_minute, None, cover_direction)
            if recovery_anchor is not None and recovery_lock_state == "confirmed":
                active_lock = store.active_one_minute_reversal_trend_lock(
                    cfg.okx.instruments[0])
                anchor_is_newer = bool(
                    active_lock is None
                    or pd.to_datetime(recovery_anchor["one_anchor_time"], utc=True)
                    > pd.to_datetime(active_lock["anchor_time"], utc=True)
                )
                if anchor_is_newer:
                    lock_key = (
                        f"directional-cover|{cfg.okx.instruments[0]}|"
                        f"{cover_direction}|{recovery_anchor['five_bar_time']}"
                    )
                    store.confirm_one_minute_reversal_trend_lock(
                        instrument=cfg.okx.instruments[0],
                        endpoint_event_key=lock_key, direction=cover_direction,
                        anchor_time=str(recovery_anchor["one_anchor_time"]),
                        anchor_extreme=recovery_extreme,
                        confirmed_at=pd.to_datetime(
                            recovery_lock_time, utc=True).isoformat(),
                        reason=(
                            "persisted first-45% cover confirmed the 1m MA20 trend takeover; "
                            "the trend lock remains valid even when the trial order was missed"
                        ),
                    )
                    store.record_event("directional_cover_trend_lock_confirmed", {
                        "strategy_version": VALIDATION_VERSION,
                        "instrument": cfg.okx.instruments[0],
                        "direction": cover_direction,
                        "five_bar_time": recovery_anchor["five_bar_time"],
                        "reason": recovery_reason,
                    })
                    dual_reversal_bias = cover_direction
                    dual_reversal_bias_reason = (
                        "newer persisted first-45% reversal completed the 1m MA20 "
                        "takeover and supersedes the older opposite trend"
                    )
            if recovery_ok and recovery_anchor is not None and not current_cover_ok:
                persistent_cover_recoveries[cover_direction] = (
                    recovery_reason, recovery_anchor, recovery_extreme)
        (three_tf_direction, three_tf_reason, three_tf_stop,
         three_tf_mode) = three_timeframe_reversal_confirmation(
            markets["1m"], markets.get("1m_live"),
            markets["5m"], markets.get("5m_live"),
            markets["15m"], markets.get("15m_live"),
        ) if cfg.strategy.risk_profile == "aggressive" else (0, "", 0.0, "")
        five_high_cover_short, five_high_cover_reason, five_high_cover_stop = (
            aggressive_five_minute_high_half_cover_short_setup(
                complete_five_minute, complete_one_minute,
                complete_fifteen_minute, markets["1H"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        five_low_cover_long, five_low_cover_reason, five_low_cover_stop = (
            aggressive_five_minute_low_half_cover_long_setup(
                complete_five_minute, complete_one_minute)
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        if five_high_cover_short:
            store.record_event("aggressive_five_minute_high_half_cover_short_candidate", {
                "strategy_version": VALIDATION_VERSION,
                "reason": five_high_cover_reason,
                "confirmed_bar_time": pd.Timestamp(
                    complete_five_minute.iloc[-1]["date"]).isoformat(),
                "stop": five_high_cover_stop,
            })
        stage_one_top_short, stage_one_top_reason, stage_one_top_stop = (
            first_stage_top_short_candidate(
                accepted_freeze_observations, complete_one_minute,
                fifteen_direction=int(signals["15m"].direction),
                hour_direction=int(signals["1H"].direction),
                five_direction=int(signals["5m"].direction),
                opposite_bottom_active=five_low_cover_long)
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0))
        top_anchor_times = [pd.to_datetime(item["time"], utc=True) for item in accepted_freeze_observations
                            if int(item["direction"]) < 0 and item["stage"] in {
                                "confirmed_local_top", "top_sweep_reject",
                                "relative_local_top_sweep", "top_half_bullish_cover"}]
        top_anchor_times.extend(pd.to_datetime(row["confirmed_bar_time"], utc=True)
                                for row in pending_freezes_before_tick
                                if int(row["direction"]) < 0
                                and str(row["pattern_type"]).split(":")[-1] in {
                                    "confirmed_local_top", "top_sweep_reject",
                                    "relative_local_top_sweep", "top_half_bullish_cover"}
                                and staged_launch_is_fresh(
                                    row["confirmed_bar_time"], signals["1m"].candle_time)[0])
        top_anchor_time = latest_top_anchor_time(top_anchor_times)
        independent_top_cover_anchor = None
        independent_top_cover_reason = ""
        if not five_high_cover_short and top_anchor_time is not None:
            independent_top_cover_anchor, independent_top_cover_reason = (
                freeze_fresh_top_cluster_cover_anchor(
                    store, cfg.okx.instruments[0], complete_one_minute,
                    complete_five_minute, top_anchor_time)
            )
            if independent_top_cover_anchor is not None:
                five_high_cover_short = True
                five_high_cover_stop = float(
                    independent_top_cover_anchor["stop_reference"])
                five_high_cover_reason = (
                    "新顶部事件独立完成冻结：一分钟顶部仍新鲜，"
                    f"{independent_top_cover_reason}；止损固定在新顶部外"
                )
                frozen_cover_anchors[-1] = independent_top_cover_anchor
        old_bottom_lock_time = (fresh_one_lock["confirmed_at"] if fresh_one_lock is not None
                                and int(fresh_one_lock["direction"]) > 0 else None)
        recent_top_anchor = store.latest_directional_cover_anchor(
            instrument=cfg.okx.instruments[0], direction=-1)
        recent_top_cover = recent_frozen_cover_supports_fresh_turn(
            anchor=recent_top_anchor,
            one_minute=complete_one_minute, direction=-1,
            latest_turn_time=top_anchor_time, old_lock_time=old_bottom_lock_time)
        fresh_top_overrides_bottom = fresh_confirmed_top_releases_old_bottom(
            five_cover=(five_high_cover_short or recent_top_cover), old_bias=dual_reversal_bias,
            fifteen_direction=int(signals["15m"].direction),
            hour_direction=int(signals["1H"].direction),
            latest_top_time=top_anchor_time, old_lock_time=old_bottom_lock_time)
        bottom_anchor_times = [pd.to_datetime(item["time"], utc=True)
                               for item in accepted_freeze_observations
                               if int(item["direction"]) > 0 and item["stage"] in {
                                   "confirmed_local_bottom", "bottom_sweep_reclaim",
                                   "relative_local_bottom_sweep", "bottom_half_bearish_cover"}]
        bottom_anchor_times.extend(pd.to_datetime(row["confirmed_bar_time"], utc=True)
                                   for row in pending_freezes_before_tick
                                   if int(row["direction"]) > 0
                                   and str(row["pattern_type"]).split(":")[-1] in {
                                       "confirmed_local_bottom", "bottom_sweep_reclaim",
                                       "relative_local_bottom_sweep", "bottom_half_bearish_cover"}
                                   and staged_launch_is_fresh(
                                       row["confirmed_bar_time"], signals["1m"].candle_time)[0])
        bottom_anchor_time = latest_top_anchor_time(bottom_anchor_times)
        old_top_lock_time = (fresh_one_lock["confirmed_at"] if fresh_one_lock is not None
                             and int(fresh_one_lock["direction"]) < 0 else None)
        recent_bottom_anchor = store.latest_directional_cover_anchor(
            instrument=cfg.okx.instruments[0], direction=1)
        recent_bottom_cover = recent_frozen_cover_supports_fresh_turn(
            anchor=recent_bottom_anchor,
            one_minute=complete_one_minute, direction=1,
            latest_turn_time=bottom_anchor_time, old_lock_time=old_top_lock_time)
        fresh_bottom_overrides_top = fresh_confirmed_bottom_releases_old_top(
            five_cover=(five_low_cover_long or recent_bottom_cover),
            old_bias=dual_reversal_bias, latest_bottom_time=bottom_anchor_time,
            old_lock_time=old_top_lock_time)
        if fresh_top_overrides_bottom:
            store.record_event("fresh_top_45pct_releases_old_bottom_for_execution", {
                "strategy_version": VALIDATION_VERSION,
                "reason": "新鲜1m局部顶晚于旧底部锁；实时5m阴线已覆盖前阳线至少45%，"
                          "15m与1H仅作背景；本信号独立核对，旧底锁不能否决"})
        if fresh_bottom_overrides_top:
            store.record_event("fresh_bottom_45pct_releases_old_top_for_execution", {
                "strategy_version": VALIDATION_VERSION,
                "reason": "新鲜1m局部底晚于旧顶部锁；实时5m阳线已覆盖前阴线至少45%；"
                          "15m与1H仅作背景；本信号独立核对，旧顶锁不能否决"})
        weak_top_ma20_retest_short, weak_top_ma20_retest_reason, weak_top_ma20_retest_stop = (
            aggressive_weak_top_ma20_retest_short_setup(markets["1m"], markets.get("1m_live"))
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        if top_weakening_short:
            store.record_event("aggressive_top_weakening_ma5_short_candidate", {
                "strategy_version": VALIDATION_VERSION,
                "reason": top_weakening_reason,
                "confirmed_bar_time": signals["1m"].candle_time.isoformat(),
                "stop": top_weakening_stop,
            })
        expanded_ma_top_short, expanded_ma_top_reason, expanded_ma_top_stop = (
            aggressive_expanded_ma_top_ma5_short_setup(markets["1m"], markets.get("1m_live"))
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        if expanded_ma_top_short:
            store.record_event("aggressive_expanded_ma_top_ma5_short_candidate", {
                "strategy_version": VALIDATION_VERSION,
                "reason": expanded_ma_top_reason,
                "confirmed_bar_time": signals["1m"].candle_time.isoformat(),
                "stop": expanded_ma_top_stop,
            })
        recovery_long, recovery_reason, recovery_stop = (
            aggressive_fifteen_minute_recovery_long_setup(
                markets["1m"], markets["5m"], markets["15m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        small_bottom_long, small_bottom_reason, small_bottom_stop = (
            aggressive_small_bottom_ma5_rebound_long_setup(
                markets["5m"], markets["1m"], markets["15m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        delayed_bottom_long, delayed_bottom_reason, delayed_bottom_stop = (
            aggressive_delayed_five_ma5_bottom_recovery_long_setup(markets["5m"], markets["1m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        color_short, color_short_reason, color_short_stop = top_color_reversal_short_setup(
            markets["5m"], markets["1m"], markets["15m"], None, markets["1H"], markets["4H"])
        color_long, color_long_reason, color_long_stop = bottom_color_reversal_long_setup(
            markets["5m"], markets["1m"], markets["15m"], None, markets["1H"], markets["4H"])
        one_cross_time = next((item["cross_time"] for item in cross_observations
                               if item["timeframe"] == "1m"), None)
        five_cross_time = next((item["cross_time"] for item in cross_observations
                                if item["timeframe"] == "5m"), None)
        use_five_small_cross = bool(
            five_small_cross_direction and (
                not small_cross_direction or one_cross_time is None
                or (five_cross_time is not None and five_cross_time <= one_cross_time)
            )
        )
        frozen_big_cross_entry = False
        early_freeze_stage_entry = False
        aligned_trend_pullback_entry = False
        confirmed_strong_reversal_entry = False
        five_minute_local_reversal_entry = False
        endpoint_half_cover_primary_entry = False
        earliest = None
        frozen_launch_pattern_keys: list[str] = []
        # Stage 2 (price crossing/turning through MA5) is the preferred early
        # entry.  Stage 3 (fresh MA5/MA10 cross) is a mandatory fallback
        # candidate when the stage-2 order was missed; normal same-side
        # position/intent deduplication prevents a duplicate add-on.
        actionable_freeze_stages = {
            "price_above_flat_rising_ma5", "price_below_flat_falling_ma5",
            "price_reclaim_ma5", "price_break_ma5",
            "small_golden_cross", "small_death_cross",
            "latched_stage2_resume",
        }
        latest_candle = complete_one_minute.iloc[-1]
        actionable_freezes = []
        persisted_freezes = store.pending_market_patterns(cfg.okx.instruments[0])
        latched_resume = latched_stage_two_resume_candidate(
            persisted_freezes, complete_one_minute,
        )
        candidate_observations = list(accepted_freeze_observations)
        missed_ma5_pullback_candidates = []
        opposing_anchor_observations = list(accepted_freeze_observations)
        opposing_anchor_observations.extend({
            "stage": str(row["pattern_type"]).split(":")[-1],
            "direction": int(row["direction"]),
            "time": row["confirmed_bar_time"],
        } for row in persisted_freezes)
        if latched_resume is not None:
            candidate_observations.append(latched_resume)
        for item in candidate_observations:
            if item["stage"] not in actionable_freeze_stages:
                continue
            fresh_ok, fresh_reason = staged_launch_is_fresh(
                item["time"], latest_candle["date"])
            if not fresh_ok:
                store.record_event("stale_staged_launch_rejected", {
                    "strategy_version": VALIDATION_VERSION, "stage": item["stage"],
                    "direction": int(item["direction"]), "reason": fresh_reason,
                })
                continue
            parent_trend_stage_takeover = parent_trend_takeover_preserves_fresh_stage(
                direction=int(item["direction"]),
                fifteen_direction=int(signals["15m"].direction),
                local_stage=True,
                pending_takeover_direction=pending_takeover_direction,
            )
            if (dual_reversal_bias and int(item["direction"]) != dual_reversal_bias):
                store.record_event("opposite_direction_lock_deferred_until_shape_review", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": int(item["direction"]),
                    "dual_reversal_bias": dual_reversal_bias,
                    "reason": f"{dual_reversal_bias_reason}；新鲜小止损形态优先完成质量、"
                              "位置、止损和风险审查，旧方向锁不再于候选阶段删除信号",
                })
            # A mature two-timeframe extreme is stronger than a transient
            # opposite local label produced while the V/peak is forming.
            mature_location_override = False
            mature_location_reason = ""
            if int(item["direction"]) > 0:
                mature_location_override, mature_location_reason = (
                    low_reversal_two_timeframe_ma_exhaustion(
                        complete_one_minute, complete_five_minute, item["time"])
                )
            elif int(item["direction"]) < 0:
                mature_location_override, mature_location_reason = (
                    high_reversal_three_timeframe_ma_exhaustion(
                        complete_one_minute, complete_five_minute,
                        complete_fifteen_minute, item["time"])
                )
            opposing_anchor, opposing_reason = same_minute_opposing_anchor_blocks_stage(
                opposing_anchor_observations, int(item["direction"]), item["time"])
            prior_confirmed_cover = store.latest_directional_cover_anchor(
                instrument=cfg.okx.instruments[0], direction=-1)
            cover_age = (pd.to_datetime(item["time"], utc=True)
                         - pd.to_datetime(prior_confirmed_cover["one_anchor_time"], utc=True)
                         if prior_confirmed_cover is not None else pd.Timedelta(days=1))
            protected_stage_three = bool(
                item["stage"] == "small_death_cross"
                and dual_reversal_bias < 0
                and int(signals["15m"].direction) < 0
                and int(signals["1H"].direction) < 0
                and pd.Timedelta(0) <= cover_age <= pd.Timedelta(minutes=15)
                and any(int(row["direction"]) < 0
                        and str(row["pattern_type"]).split(":")[-1] in {
                            "confirmed_local_top", "top_sweep_reject",
                            "relative_local_top_sweep", "top_half_bullish_cover"}
                        and pd.Timedelta(0) <= (pd.to_datetime(item["time"], utc=True)
                            - pd.to_datetime(row["confirmed_bar_time"], utc=True))
                            <= pd.Timedelta(minutes=30)
                        for row in persisted_freezes))
            if opposing_anchor and not mature_location_override and not protected_stage_three:
                store.record_event("same_minute_opposing_anchor_rejected", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": int(item["direction"]),
                    "reason": opposing_reason,
                })
                continue
            if parent_trend_stage_takeover and int(item["direction"]) != dual_reversal_bias:
                store.record_event("parent_trend_pullback_preserves_fresh_stage", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": int(item["direction"]),
                    "stale_direction": dual_reversal_bias,
                    "reason": "新鲜1分钟局部转折与15分钟趋势同向，优先按上涨回踩追多/"
                              "下跌反抽追空保留；旧5分钟反向锁不再提前删除候选",
                })
            if opposing_anchor and protected_stage_three:
                store.record_event("confirmed_top_stage_three_overrides_transient_bottom", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": -1,
                    "reason": "已确认1m顶部和5m 45%覆盖，15m/1H下降；"
                              "同分钟临时小底不取消新鲜小死叉补漏"})
            # A new local extreme starts a new execution chain. Bind this
            # stage to the newest preceding anchor instead of allowing an old
            # missed/rejected bottom or top to consume the later opportunity.
            latest_anchor = latest_reversal_anchor_for_stage(
                persisted_freezes, accepted_freeze_observations,
                int(item["direction"]), item["time"])
            if latest_anchor is None:
                store.record_event("early_launch_quality_rejected", {
                    "strategy_version": VALIDATION_VERSION, "stage": item["stage"],
                    "direction": int(item["direction"]),
                    "reason": "MA5/小交叉没有同方向局部顶底或扫损冻结，只记录不下单",
                })
                continue
            latest_anchor_stage = str(
                latest_anchor.get("stage") if isinstance(latest_anchor, dict)
                else str(latest_anchor["pattern_type"]).split(":")[-1])
            latest_anchor_time = (
                latest_anchor.get("time") if isinstance(latest_anchor, dict)
                else latest_anchor["confirmed_bar_time"])
            confirmed_reversal_anchor = latest_anchor_stage in {
                "confirmed_local_bottom", "confirmed_local_top",
                "bottom_sweep_reclaim", "top_sweep_reject",
                "relative_local_bottom_sweep", "relative_local_top_sweep",
            }
            stage_time_utc = pd.to_datetime(item["time"], utc=True)
            same_time_stage2_count = len({
                str(observation.get("stage"))
                for observation in opposing_anchor_observations
                if int(observation.get("direction") or 0) == int(item["direction"])
                and pd.to_datetime(observation.get("time"), utc=True) == stage_time_utc
                and observation.get("stage") in {
                    "price_above_flat_rising_ma5", "price_below_flat_falling_ma5",
                    "price_reclaim_ma5", "price_break_ma5", "small_golden_cross",
                    "small_death_cross",
                }
            })
            anchor_price = float(item.get("anchor_price") or (
                latest_anchor.get("price")
                if isinstance(latest_anchor, dict)
                else latest_anchor["entry_reference"]))
            item = {**item}
            item["reversal_event_anchor_time"] = pd.to_datetime(
                latest_anchor_time, utc=True).isoformat()
            item.update(classify_reversal_context(
                markets["1m"], markets["5m"], int(item["direction"])))
            live_half_cover_override = False
            live_half_cover_zone_reason = ""
            three_timeframe_exhaustion = False
            three_timeframe_exhaustion_reason = ""
            # Reversal entries use the live 5m half-cover so the 1m structural
            # stop remains compact.  The closed 5m fan-endpoint lock separately
            # owns trend direction and prevents an old opposite lock from
            # turning this early-entry rule into a wrong-way trade.
            if int(item["direction"]) < 0 and five_high_cover_short:
                anchor_time = (latest_anchor.get("time") if isinstance(latest_anchor, dict)
                               else latest_anchor["confirmed_bar_time"])
                live_half_cover_override, live_half_cover_zone_reason = (
                    top_anchor_above_bullish_ma_stack(
                        complete_one_minute, anchor_time)
                )
                if live_half_cover_override:
                    live_half_cover_zone_reason = (
                        f"{five_high_cover_reason}；{live_half_cover_zone_reason}；"
                        "顶部反转允许五分钟盘中约半覆盖，以一分钟局部高点作小止损"
                    )
                if live_half_cover_override:
                    (three_timeframe_exhaustion,
                     three_timeframe_exhaustion_reason) = (
                        high_reversal_three_timeframe_ma_exhaustion(
                            complete_one_minute, complete_five_minute,
                            complete_fifteen_minute, anchor_time)
                    )
            elif int(item["direction"]) > 0 and five_low_cover_long:
                anchor_time = (latest_anchor.get("time") if isinstance(latest_anchor, dict)
                               else latest_anchor["confirmed_bar_time"])
                # Five-minute bullish half-cover plus the current six-bar
                # one-minute bottom cluster is enough for a local-bottom
                # launch. MA exhaustion upgrades the classification only.
                live_half_cover_override = bool(
                    five_low_cover_long)
                live_half_cover_zone_reason = (
                    f"{five_low_cover_reason}；底部反转允许五分钟盘中约半覆盖，以一分钟局部低点作小止损")
            if live_half_cover_override:
                item["five_minute_half_cover"] = True
                item["full_endpoint_confirmation"] = _endpoint_full_reversal_confirmation(
                    markets["5m"], markets.get("5m_live"),
                    markets["1m"], markets.get("1m_live"), int(item["direction"]))
                item["market_shape_code"] = (
                    "true_top_reversal" if int(item["direction"]) < 0
                    else ("true_bottom_reversal" if mature_location_override
                          else "local_bottom_reversal"))
                item["market_shape_label"] = (
                    "intrabar five-minute confirmed top reversal"
                    if int(item["direction"]) < 0
                    else "intrabar five-minute confirmed bottom reversal")
                item["market_shape_evidence"] = (
                    f"{five_high_cover_reason if int(item['direction']) < 0 else five_low_cover_reason}; "
                    f"{live_half_cover_zone_reason}")
                store.record_event("classified_five_minute_half_cover_stage_override", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": int(item["direction"]),
                    "stage_time": pd.Timestamp(item["time"]).isoformat(),
                    "five_minute_confirmation": "intrabar_reversal_compact_stop",
                    "reason": item["market_shape_evidence"],
                })
            trend_ok, trend_reason = trend_alignment_allows_reversal_stage(
                int(item["direction"]), int(signals["1m"].direction),
                int(signals["5m"].direction), str(item["stage"]),
                bool(item.get("five_minute_half_cover")),
                str(item.get("market_shape_code", "")),
                same_time_stage2_count,
                confirmed_reversal_anchor,
            )
            if not trend_ok:
                store.record_event("countertrend_stage_rejected", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": int(item["direction"]),
                    "one_minute_direction": int(signals["1m"].direction),
                    "five_minute_direction": int(signals["5m"].direction),
                    "reason": trend_reason,
                })
                continue
            quality_ok, quality_reason = one_minute_launch_quality_gate(
                complete_one_minute,
                int(item["direction"]), anchor_price,
            )
            mature_top_override = high_half_cover_bypasses_ma5_chase(
                live_half_cover_override, three_timeframe_exhaustion,
                quality_reason)
            sideways_top_override = sideways_top_short_bypasses_ma5_chase(
                int(item["direction"]), str(item.get("market_shape_code", "")),
                bool(five_high_cover_short), str(item["stage"]), quality_reason)
            locked_stage_three_override = locked_stage_three_bypasses_ma5_chase(
                str(item["stage"]), int(item["direction"]),
                dual_reversal_bias, quality_reason)
            if not quality_ok and (mature_top_override or sideways_top_override
                                   or locked_stage_three_override):
                store.record_event("two_timeframe_high_half_cover_market_override", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": int(item["direction"]),
                    "replaced_quality_rejection": quality_reason,
                    "reason": (three_timeframe_exhaustion_reason
                               if mature_top_override else
                               ("latest 1m+5m endpoint lock authorizes the mandatory "
                                "stage-three fallback"
                                if locked_stage_three_override else
                                "sideways range-top 5m half-cover plus 1m MA5 breakdown/death-cross")),
                })
                quality_ok = True
                quality_reason = (
                    "latest dual-period directional lock plus fresh MA5/MA10 cross confirmed; "
                    "execute the mandatory stage-three fallback after the earlier launch was missed"
                    if locked_stage_three_override else
                    "sideways range-top bearish half-cover and 1m MA5 breakdown/death-cross "
                    "confirmed; execute once instead of resting an unfilled MA5 pullback"
                    if sideways_top_override else
                    "1m/5m highs are in mature expanded bullish MA stacks and the live 5m "
                    "bearish candle covered half of the prior bullish body; enter at "
                    "confirmation instead of waiting for an MA5 pullback; 15m is optional"
                )
            if not quality_ok:
                plan = missed_ma5_pullback_limit_plan(
                    complete_one_minute, int(item["direction"]),
                    float(item["stop"]), quality_reason,
                    one_minute_live=markets.get("1m_live"),
                    five_minute_live=markets.get("5m_live"))
                if plan.allowed:
                    missed_ma5_pullback_candidates.append((item, plan, trend_reason))
                store.record_event("early_launch_quality_rejected", {
                    "strategy_version": VALIDATION_VERSION, "stage": item["stage"],
                    "direction": int(item["direction"]), "reason": quality_reason,
                })
                continue
            item = {**item, "quality_reason": f"{fresh_reason}；{quality_reason}"}
            item["quality_reason"] = f"{item['quality_reason']}；{trend_reason}"
            effective_stage_shape = str(item.get("market_shape_code", ""))
            if int(item["direction"]) > 0 and five_uptrend_pullback_context:
                # The later branch will classify this as an uptrend pullback.
                # Apply that location gate now, before the classification can
                # bypass the local-top protection as a generic reversal.
                effective_stage_shape = "uptrend_continuation_long"
            location_ok, location_reason = continuation_stage_location_allowed(
                complete_one_minute, int(item["direction"]), effective_stage_shape)
            if not location_ok:
                store.record_event("late_trend_continuation_rejected", {
                    "strategy_version": VALIDATION_VERSION,
                    "stage": item["stage"], "direction": int(item["direction"]),
                    "market_shape_code": item.get("market_shape_code"),
                    "reason": location_reason,
                })
                continue
            item["quality_reason"] = f"{quality_reason}；{location_reason}"
            actionable_freezes.append(item)
        if not actionable_freezes and missed_ma5_pullback_candidates:
            item, plan, trend_reason = max(
                missed_ma5_pullback_candidates,
                key=lambda candidate: pd.to_datetime(candidate[0]["time"], utc=True))
            direction = int(plan.direction)
            return submit_one_ma5_retest_limit(
                store=store, client=client, snapshot=snapshot,
                instrument=cfg.okx.instruments[0], direction=direction,
                stage_time=item["time"], plan=plan,
                atr=latest_atr(complete_one_minute), stage=item["stage"],
                event_type="missed_ma5_pullback_limit_submitted",
                context_reason=trend_reason)
        short_reversal_active = bool(
            stage_one_top_short or five_high_cover_short or expanded_ma_top_short or top_weakening_short
            or weak_top_ma20_retest_short or intrabar_live_short
            or intrabar_range_short or intrabar_engulf_short or color_short
        )
        long_reversal_active = bool(
            recovery_long or small_bottom_long or delayed_bottom_long
            or intrabar_long or ma20_retest_live_long or color_long
        )
        opposing_structure_active = (
            short_reversal_active if frozen_big_cross_direction > 0
            else long_reversal_active
        )
        frozen_direction_allowed, frozen_direction_reason = frozen_launch_direction_allowed(
            frozen_big_cross_direction, int(signals["1m"].direction),
            opposing_structure_active,
        ) if frozen_big_cross_direction else (False, "没有冻结大交叉")
        if frozen_big_cross_direction and not frozen_direction_allowed:
            store.record_event("frozen_big_cross_invalidated_by_latest_structure", {
                "strategy_version": VALIDATION_VERSION,
                "direction": frozen_big_cross_direction,
                "one_minute_direction": int(signals["1m"].direction),
                "reason": frozen_direction_reason,
            })
        if three_tf_direction:
            direction = three_tf_direction
            structural_stop = three_tf_stop
            hierarchy_reason = three_tf_reason
            terminal_reversal_entry = True
            three_timeframe_reversal_entry = True
            three_timeframe_reversal_mode = three_tf_mode
            aggressive_recovery_long_entry = direction > 0
            compact_top_reversal_short_entry = direction < 0
            store.record_event("three_timeframe_reversal_entry_candidate", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "direction": direction,
                "mode": three_tf_mode,
                "reason": three_tf_reason,
                "stop": three_tf_stop,
            })
        elif independent_top_cover_anchor is not None:
            direction = -1
            structural_stop = float(
                independent_top_cover_anchor["stop_reference"])
            hierarchy_reason = (
                f"新顶部事件独立执行链：{five_high_cover_reason}；"
                "旧上涨趋势只作背景，不得用同轮回踩做多候选覆盖该顶部试空"
            )
            selected_frozen_cover = independent_top_cover_anchor
            terminal_reversal_entry = True
            five_minute_high_half_cover_short_entry = True
            compact_top_reversal_short_entry = True
            store.record_event("fresh_top_independent_execution_candidate", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "direction": -1,
                "top_anchor_time": independent_top_cover_anchor["one_anchor_time"],
                "five_bar_time": independent_top_cover_anchor["five_bar_time"],
                "stop": structural_stop,
                "reason": hierarchy_reason,
            })
        elif persistent_cover_recoveries:
            direction, recovery_item = max(
                persistent_cover_recoveries.items(),
                key=lambda item: pd.to_datetime(item[1][1]["five_bar_time"], utc=True),
            )
            hierarchy_reason, selected_frozen_cover, _ = recovery_item
            structural_stop = float(selected_frozen_cover["stop_reference"])
            terminal_reversal_entry = True
            persistent_cover_recovery_entry = True
            aggressive_recovery_long_entry = direction > 0
            compact_top_reversal_short_entry = direction < 0
            store.record_event("persisted_directional_cover_recovery_candidate", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "direction": direction,
                "five_bar_time": selected_frozen_cover["five_bar_time"],
                "one_anchor_time": selected_frozen_cover["one_anchor_time"],
                "stop": structural_stop,
                "reason": hierarchy_reason,
            })
        elif early_pullback_long:
            direction = 1
            structural_stop = early_pullback_stop
            hierarchy_reason = (
                f"双周期底部反转后的上升趋势提前回踩追多：{early_pullback_reason}；"
                f"{dual_reversal_bias_reason}")
            terminal_reversal_entry = True
            trend_continuation_entry = True
            early_dual_pullback_cover_long_entry = True
            aggressive_recovery_long_entry = True
        elif actionable_freezes:
            # The newest completed stage owns the direction after a later
            # top/bottom lock replaces the prior opposite trend.
            earliest = max(
                actionable_freezes,
                key=lambda candidate: pd.to_datetime(candidate["time"], utc=True))
            direction = int(earliest["direction"])
            structural_stop = float(earliest["stop"])
            bottom_reversal_location_ok = True
            bottom_reversal_location_reason = ""
            # A confirmed uptrend pullback is a continuation, not the first
            # bottom-reversal probe.  It keeps its own location checks.
            if (bottom_reversal_ma20_gate_required(earliest)
                    and not (five_uptrend_pullback_context and
                             int(earliest["direction"]) > 0)):
                bottom_reversal_location_ok, bottom_reversal_location_reason = (
                    bottom_reversal_below_both_ma20(
                        complete_one_minute, complete_five_minute, earliest["time"])
                )
                if not bottom_reversal_location_ok:
                    store.record_event("bottom_reversal_dual_ma20_rejected", {
                        "strategy_version": VALIDATION_VERSION,
                        "instrument": cfg.okx.instruments[0],
                        "stage": earliest["stage"],
                        "reason": bottom_reversal_location_reason,
                    })
                    return observe(bottom_reversal_location_reason)
            hierarchy_reason = (
                f"一分钟{earliest.get('market_shape_label', '形态候选')}阶段触发"
                f"{earliest['stage']}已经成立："
                "扫损/局部极值冻结后，价格穿MA5优先提前下单；若该单漏掉，MA5穿MA10再次强制产生补漏候选；"
                "五分钟均线排列和半实体反包只用于区分真正反转与趋势中继，不等待五分钟变色；"
                f"{earliest.get('market_shape_evidence', '')}；{earliest.get('quality_reason', '')}；"
                "立即使用最近局部小止损入场"
                + (f"；{bottom_reversal_location_reason}" if direction > 0 else "")
            )
            terminal_reversal_entry = True
            early_freeze_stage_entry = True
            if five_minute_top_cover_owns_direct_entry(direction, earliest):
                # Preserve the stronger branch identity. This also bypasses
                # the generic rolling-range review below because the top was
                # already validated above the 1m MA5/MA10/MA20 stack.
                five_minute_high_half_cover_short_entry = True
            aligned_trend_pullback_entry = bool(
                direction == int(signals["5m"].direction)
                and (earliest.get("market_shape_code") in {
                    "uptrend_continuation_long", "downtrend_continuation_short",
                } or (direction > 0 and five_uptrend_pullback_context))
            )
            if direction > 0 and five_uptrend_pullback_context and aligned_trend_pullback_entry:
                trend_continuation_entry = True
                terminal_reversal_entry = False
                store.record_event("confirmed_uptrend_pullback_reclassified", {
                    "strategy_version": VALIDATION_VERSION,
                    "instrument": cfg.okx.instruments[0], "direction": 1,
                    "stage": earliest["stage"],
                    "reason": "已收盘5分钟确认上涨，当前新鲜1分钟底部阶段按回踩续单核验；"
                              "底部首单MA20下方门不适用，仍须验证MA边沿、结构止损和利润空间",
                })
            confirmed_strong_reversal_entry = bool(
                not (direction > 0 and five_uptrend_pullback_context)
                and earliest.get("five_minute_half_cover")
                and earliest.get("full_endpoint_confirmation")
                and earliest.get("market_shape_code") in {
                    "true_top_reversal", "true_bottom_reversal",
                }
            )
            aggressive_recovery_long_entry = direction > 0
            compact_top_reversal_short_entry = direction < 0
        elif (frozen_big_cross_direction and frozen_launch_candidates
              and frozen_direction_allowed):
            latest_frozen = frozen_launch_candidates[-1]
            direction = frozen_big_cross_direction
            structural_stop = float(latest_frozen["stop_reference"] or frozen_big_cross_stop)
            hierarchy_reason = (
                f"{frozen_big_cross_reason}：读取最近冻结阶段{str(latest_frozen['pattern_type']).split(':')[-1]}；"
                f"候选冻结价{float(latest_frozen['entry_reference']):.2f}，本次大交叉启动正式下单"
            )
            terminal_reversal_entry = True
            frozen_big_cross_entry = True
            aggressive_recovery_long_entry = direction > 0
            compact_top_reversal_short_entry = direction < 0
            frozen_launch_pattern_keys = [str(row["event_key"]) for row in frozen_launch_candidates]
        # A small cross is persisted above as freeze evidence, but must never
        # consume this decision slot: the older 1m MA5 weakening/reclaim and
        # 5m half-cover-with-1m-confirmation triggers continue in parallel.
        elif five_high_cover_short:
            # A qualified 5m top must be executable, not merely an audit row.
            direction, hierarchy_reason, structural_stop = (
                -1, five_high_cover_reason, five_high_cover_stop)
            terminal_reversal_entry = True
            five_minute_high_half_cover_short_entry = True
            compact_top_reversal_short_entry = True
        elif stage_one_top_short:
            direction, hierarchy_reason, structural_stop = (
                -1, stage_one_top_reason, stage_one_top_stop)
            terminal_reversal_entry = True
            stage_one_top_short_entry = True
            compact_top_reversal_short_entry = True
        elif dual_ma5_direction:
            direction, hierarchy_reason, structural_stop = (
                dual_ma5_direction, dual_ma5_reason, dual_ma5_stop)
            terminal_reversal_entry = True
            two_timeframe_intrabar_ma5_reversal_entry = True
            aggressive_recovery_long_entry = direction > 0
            compact_top_reversal_short_entry = direction < 0
        # The 5m half-cover confirms/persists the top endpoint above; it is not
        # a second direct entry branch.  Once that 5m top succeeds, continuation
        # execution steps down to the 1m local-high MA5-edge setup below.  This
        # prevents a 5m confirmation from being reused as a late bottom short.
        elif expanded_ma_top_short:
            direction, hierarchy_reason, structural_stop = -1, expanded_ma_top_reason, expanded_ma_top_stop
            terminal_reversal_entry = True
            expanded_ma_top_short_entry = True
        elif top_weakening_short:
            direction, hierarchy_reason, structural_stop = -1, top_weakening_reason, top_weakening_stop
            terminal_reversal_entry = True
            top_weakening_ma5_short_entry = True
        elif weak_top_ma20_retest_short:
            direction, hierarchy_reason, structural_stop = (
                -1, weak_top_ma20_retest_reason, weak_top_ma20_retest_stop)
            terminal_reversal_entry = True
            top_weakening_ma5_short_entry = True
        elif intrabar_engulf_short:
            direction, hierarchy_reason, structural_stop = -1, intrabar_engulf_short_reason, intrabar_engulf_short_stop
            terminal_reversal_entry = True
            intrabar_bear_engulf_short_entry = True
        elif intrabar_range_short:
            direction, hierarchy_reason, structural_stop = -1, intrabar_range_short_reason, intrabar_range_short_stop
            terminal_reversal_entry = True
            intrabar_range_ma5_short_entry = True
        elif intrabar_live_short:
            direction, hierarchy_reason, structural_stop = -1, intrabar_live_short_reason, intrabar_live_short_stop
            terminal_reversal_entry = True
            intrabar_ma20_short_entry = True
        elif intrabar_long and 1 not in frozen_stage_directions:
            direction, hierarchy_reason, structural_stop = 1, intrabar_reason, intrabar_stop
            terminal_reversal_entry = True
            aggressive_recovery_long_entry = True
            intrabar_rebound_long_entry = True
        elif ma20_retest_live_long:
            direction, hierarchy_reason, structural_stop = 1, ma20_retest_live_reason, ma20_retest_live_stop
            terminal_reversal_entry = True
            aggressive_recovery_long_entry = True
            first_ma20_retest_long_entry = True
        elif recovery_long:
            direction, hierarchy_reason, structural_stop = 1, recovery_reason, recovery_stop
            terminal_reversal_entry = True
            aggressive_recovery_long_entry = True
            fifteen_minute_recovery_long_entry = True
        elif delayed_bottom_long:
            direction, hierarchy_reason, structural_stop = 1, delayed_bottom_reason, delayed_bottom_stop
            terminal_reversal_entry = True
            delayed_five_ma5_bottom_entry = True
            aggressive_recovery_long_entry = True
        elif small_bottom_long:
            direction, hierarchy_reason, structural_stop = 1, small_bottom_reason, small_bottom_stop
            terminal_reversal_entry = True
            small_bottom_rebound_entry = True
        elif color_short or color_long:
            direction = -1 if color_short else 1
            hierarchy_reason = color_short_reason if color_short else color_long_reason
            structural_stop = color_short_stop if color_short else color_long_stop
            terminal_reversal_entry = True
            compact_top_reversal_short_entry = bool(color_short)
        terminal_short, terminal_reason, terminal_stop = terminal_acceleration_short_setup(
            markets["5m"], markets["1m"], markets["15m"])
        terminal_long, terminal_long_reason, terminal_long_stop = terminal_acceleration_long_setup(
            markets["5m"], markets["1m"], markets["15m"])
        if not terminal_reversal_entry and (terminal_short or terminal_long):
            direction = -1 if terminal_short else 1
            hierarchy_reason = terminal_reason if terminal_short else terminal_long_reason
            structural_stop = terminal_stop if terminal_short else terminal_long_stop
            terminal_reversal_entry = True
            extreme_entry = False
        cross_direction, cross_reason, cross_stop = dual_timeframe_ma20_cross_continuation_setup(
            markets["1m"], markets.get("1m_live"), markets["5m"], markets.get("5m_live"))
        # This is deliberately a local launch rule.  A weakening/rising local
        # structure can begin inside either an established trend or a range;
        # 15m/1H remain descriptive context and must not veto the small-stop
        # 1m probe.  The later 5m cross decides whether to upgrade the hold.
        higher_cross_ok = bool(cross_direction)
        cross_upgrade_only = bool(cross_direction and "升级趋势持有" in cross_reason)
        if cross_upgrade_only:
            if (same_side_entry_conflicts(snapshot, cross_direction, sniper_prefix="QBVALSNP")
                    or store.has_open_same_side_trade("strategy_01", cfg.okx.instruments[0], cross_direction)):
                return ValidationExecutionResult(
                    "manage", "五分钟大金叉/大死叉只升级已有一分钟先行仓，由五分钟MA5接管止盈；不重复下单")
            cross_direction = 0
            cross_reason = "五分钟大金叉/大死叉已出现，但没有一分钟三阶段先行仓；仅记录升级信号，禁止追单"
        elif cross_direction and not frozen_big_cross_entry:
            cross_direction = 0
            cross_reason = "一分钟大交叉没有匹配到30分钟内冻结候选；仅记录，禁止脱离冻结链独立追单"
        if (not terminal_reversal_entry and cross_direction and higher_cross_ok):
            direction, hierarchy_reason, structural_stop = cross_direction, cross_reason, cross_stop
            trend_continuation_entry = True
            dual_ma20_cross_continuation_entry = True
            extreme_entry = False
        weakness_short, weakness_reason, weakness_stop = multi_timeframe_weakness_continuation_short_setup(
            markets["1m"], markets["5m"])
        if (not terminal_reversal_entry and not trend_continuation_entry and weakness_short):
            direction, hierarchy_reason, structural_stop = -1, weakness_reason, weakness_stop
            trend_continuation_entry = True
            multi_timeframe_weakness_short_entry = True
        rollover_short, rollover_reason, rollover_stop = (
            five_minute_bearish_rollover_continuation_short_setup(
                markets["1m"], markets["5m"], markets.get("5m_live"), latest_market_time)
            if cfg.strategy.risk_profile == "aggressive" else (0, "", 0.0)
        )
        if (not terminal_reversal_entry and not trend_continuation_entry
                and rollover_short and dual_reversal_bias == -1):
            direction, hierarchy_reason, structural_stop = -1, rollover_reason, rollover_stop
            trend_continuation_entry = True
            five_minute_bearish_rollover_short_entry = True
        direct_ma5_short, direct_ma5_short_reason, direct_ma5_short_stop = direct_rollover_first_ma5_short_setup(
            markets["5m"], markets["1m"])
        direct_ma5_long, direct_ma5_long_reason, direct_ma5_long_stop = direct_rollover_first_ma5_long_setup(
            markets["5m"], markets["1m"])
        direct_short, direct_reason, direct_stop = direct_rollover_first_ma20_short_setup(
            markets["5m"], markets["1m"])
        direct_long, direct_long_reason, direct_long_stop = direct_rollover_first_ma20_long_setup(
            markets["5m"], markets["1m"])
        aggressive_first_short, aggressive_first_reason, aggressive_first_stop = (
            aggressive_first_bear_ma20_short_setup(markets["5m"], markets["1m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        three_bear_short, three_bear_reason, three_bear_stop = three_bear_ma20_rollover_short_setup(
            markets["5m"], markets["1m"])
        if not terminal_reversal_entry and (aggressive_first_short or three_bear_short or direct_ma5_short or direct_ma5_long or direct_short or direct_long):
            if aggressive_first_short:
                direction, hierarchy_reason, structural_stop = -1, aggressive_first_reason, aggressive_first_stop
                five_minute_color_rollover_short_entry = True
            elif three_bear_short:
                direction, hierarchy_reason, structural_stop = -1, three_bear_reason, three_bear_stop
                three_bear_entry = True
                five_minute_color_rollover_short_entry = True
            elif direct_ma5_short:
                direction, hierarchy_reason, structural_stop = -1, direct_ma5_short_reason, direct_ma5_short_stop
                five_minute_color_rollover_short_entry = True
            elif direct_ma5_long:
                direction, hierarchy_reason, structural_stop = 1, direct_ma5_long_reason, direct_ma5_long_stop
                aggressive_recovery_long_entry = cfg.strategy.risk_profile == "aggressive"
            elif direct_short:
                direction, hierarchy_reason, structural_stop = -1, direct_reason, direct_stop
                five_minute_color_rollover_short_entry = True
            else:
                direction, hierarchy_reason, structural_stop = 1, direct_long_reason, direct_long_stop
            terminal_reversal_entry = True
            if five_minute_color_rollover_short_entry:
                trend_continuation_entry = True
            extreme_entry = False
        volume_long, volume_reason, volume_stop, volume_target = volume_stopping_pullback_long_setup(
            markets["5m"], markets["1m"])
        if not terminal_reversal_entry and volume_long:
            direction, hierarchy_reason, structural_stop = 1, volume_reason, volume_stop
            volume_stopping_target = volume_target
            volume_stopping_entry = True
            terminal_reversal_entry = True
            extreme_entry = False
        early_low_long, early_low_reason, early_low_stop = shared_low_sweep_reclaim_long_setup(
            markets["5m"], markets["1m"])
        if not terminal_reversal_entry and early_low_long:
            direction, hierarchy_reason, structural_stop = 1, early_low_reason, early_low_stop
            terminal_reversal_entry = True
            early_low_sweep_entry = True
            extreme_entry = False
        early_high_short, early_high_reason, early_high_stop = shared_high_sweep_reject_short_setup(
            markets["5m"], markets["1m"])
        if not terminal_reversal_entry and early_high_short:
            direction, hierarchy_reason, structural_stop = -1, early_high_reason, early_high_stop
            terminal_reversal_entry = True
            early_high_sweep_entry = True
            extreme_entry = False
        if early_low_sweep_entry or early_high_sweep_entry:
            publish_extreme_signal(
                store, instrument=cfg.okx.instruments[0], strategy_id="strategy_01",
                direction=direction,
                confirmed_bar_time=pd.Timestamp(markets["1m"].sort_values("date").iloc[-1]["date"]).isoformat(),
                reason=hierarchy_reason, stop_price=structural_stop,
            )
        elif not terminal_reversal_entry:
            shared = recover_extreme_signal(
                store, instrument=cfg.okx.instruments[0], strategy_id="strategy_01")
            if shared is not None:
                direction = int(shared["direction"])
                hierarchy_reason = f"共享信号中心补读｜{shared['reason']}"
                structural_stop = float(shared["stop_price"])
                terminal_reversal_entry = True
                early_high_sweep_entry = direction < 0
                early_low_sweep_entry = direction > 0
                extreme_entry = False
        candidate_direction, candidate_reason, candidate_stop = candidate_reversal_setup(
            markets["5m"], markets["1m"], markets["15m"])
        if not terminal_reversal_entry and candidate_direction:
            direction, hierarchy_reason, structural_stop = candidate_direction, candidate_reason, candidate_stop
            terminal_reversal_entry = True
            compact_top_reversal_short_entry = candidate_direction < 0
            extreme_entry = False
        ma5_continuation_short, ma5_continuation_reason, ma5_continuation_stop = downtrend_ma5_pullback_short_setup(
            markets["5m"], markets["1m"], markets["15m"])
        double_rejection_short, double_rejection_reason, double_rejection_stop = (
            aggressive_double_rejection_ma5_short_setup(
                markets["5m"], markets["1m"], markets["15m"])
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        ma5_continuation_long, ma5_continuation_long_reason, ma5_continuation_long_stop = uptrend_ma5_pullback_long_setup(
            markets["5m"], markets["1m"], markets["15m"])
        continuation_short, continuation_reason, continuation_stop = downtrend_pullback_short_setup(
            markets["5m"], markets["1m"], markets["15m"])
        continuation_long, continuation_long_reason, continuation_long_stop = uptrend_pullback_long_setup(
            markets["5m"], markets["1m"], markets["15m"])
        local_high_short, local_high_reason, local_high_stop = (
            aggressive_downtrend_local_high_ma5_short_setup(
                markets["5m"], complete_one_minute, markets["15m"],
                markets["1H"],
                durable_downtrend=dual_reversal_bias == -1)
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        local_low_long, local_low_reason, local_low_stop = (
            aggressive_uptrend_local_low_ma5_long_setup(
                markets["5m"], complete_one_minute, markets["15m"],
                markets["1H"], durable_uptrend=dual_reversal_bias == 1)
            if cfg.strategy.risk_profile == "aggressive" else (False, "", 0.0)
        )
        # A new unbroken opposite endpoint immediately removes the old
        # continuation's permission.  It does not auto-authorize an order;
        # the new-side candidate must still pass every normal entry gate.
        if pending_takeover_direction == 1:
            local_high_short = double_rejection_short = False
            ma5_continuation_short = continuation_short = False
        elif pending_takeover_direction == -1:
            local_low_long = ma5_continuation_long = continuation_long = False
        priority_trend_turn = (
            (-1, local_high_reason, local_high_stop) if local_high_short else
            (1, local_low_reason, local_low_stop) if local_low_long else None)
        if (priority_trend_turn is not None
                and (dual_reversal_bias == priority_trend_turn[0]
                     or int(signals["15m"].direction) == priority_trend_turn[0])
                and not confirmed_strong_reversal_entry
                and not persistent_cover_recovery_entry
                and not three_timeframe_reversal_entry):
            store.record_event("downtrend_lower_high_short_priority", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "replaced_direction": direction,
                "replaced_reason": hierarchy_reason,
                "reason": priority_trend_turn[1],
            })
            direction, hierarchy_reason, structural_stop = priority_trend_turn
            terminal_reversal_entry = False
            trend_continuation_entry = True
            extreme_entry = False
            two_timeframe_intrabar_ma5_reversal_entry = False
            five_minute_high_half_cover_short_entry = False
            expanded_ma_top_short_entry = False
            top_weakening_ma5_short_entry = False
            intrabar_bear_engulf_short_entry = False
            intrabar_range_ma5_short_entry = False
            intrabar_ma20_short_entry = False
            early_high_sweep_entry = False
        if (not terminal_reversal_entry and not trend_continuation_entry
                and (local_high_short or local_low_long or double_rejection_short or ma5_continuation_short
                     or ma5_continuation_long or continuation_short or continuation_long)):
            if local_high_short:
                direction, hierarchy_reason, structural_stop = -1, local_high_reason, local_high_stop
            elif local_low_long:
                direction, hierarchy_reason, structural_stop = 1, local_low_reason, local_low_stop
            elif double_rejection_short:
                direction, hierarchy_reason, structural_stop = -1, double_rejection_reason, double_rejection_stop
                double_rejection_entry = True
            elif ma5_continuation_short:
                direction, hierarchy_reason, structural_stop = -1, ma5_continuation_reason, ma5_continuation_stop
            elif ma5_continuation_long:
                direction, hierarchy_reason, structural_stop = 1, ma5_continuation_long_reason, ma5_continuation_long_stop
            elif continuation_short:
                direction, hierarchy_reason, structural_stop = -1, continuation_reason, continuation_stop
            else:
                direction, hierarchy_reason, structural_stop = 1, continuation_long_reason, continuation_long_stop
            trend_continuation_entry = True
            extreme_entry = False
        if not extreme_entry and not terminal_reversal_entry and not trend_continuation_entry:
            direction, pullback_reason, structural_stop = breakout_pullback_long_setup(signals, markets)
            if direction:
                hierarchy_reason = pullback_reason
                breakout_pullback_entry = True
        if not extreme_entry and not terminal_reversal_entry and not trend_continuation_entry and not breakout_pullback_entry:
            direction, pullback_reason, structural_stop = ma20_pullback_short_setup(signals, markets)
            if not direction:
                direction, pullback_reason, structural_stop = ma20_pullback_long_setup(signals, markets)
            if direction:
                hierarchy_reason = pullback_reason
                ma20_pullback_entry = True
        if (("candidate" in trend_regime.state or "waiting" in trend_regime.state)
                and ma20_pullback_entry and not trend_continuation_entry):
            hierarchy_reason = f"{hierarchy_reason}；{trend_regime.reason}（仅作观察背景，不暂停本独立回踩候选）"
        external_signal = (latest_external_signal(database, now=now)
                           or pinets_market_signal(markets["1m"], markets["5m"], now=now))
        # Browser/PineTS/Webhook/Edge inputs are research evidence only.  They
        # must never replace the locally validated strategy direction or open
        # a Live order by themselves.
        external_execution_entry = False
        if external_signal is not None and not store.external_execution_claimed(external_signal.event_id):
            store.set_external_execution_status(
                external_signal.event_id, external_signal.source, "research_only",
                "外部技术指标仅作只读研究参考；未改变本地策略方向，也未取得下单权限",
            )
            store.record_event("external_signal_research_only", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "direction": external_signal.direction,
                "source": external_signal.source,
                "event_id": external_signal.event_id,
                "reason": "外部技术指标仅作只读研究参考；本地策略独立决定方向、位置、风控和下单",
            })
        if external_execution_entry:
            direction = external_signal.direction
            structural_stop = external_signal.stop_loss
            hierarchy_reason = f"{external_signal.source.upper()}实时技术指标直接触发；结构={external_signal.structure}；event={external_signal.event_id}"
            terminal_reversal_entry = True
        early_throwback_short_entry = early_downtrend_throwback_short_allowed(
            local_high_short=(local_high_short or stage_one_top_short_entry),
            selected_trend_entry=(trend_continuation_entry or stage_one_top_short_entry),
            direction=direction, durable_bias=dual_reversal_bias,
            five_direction=int(signals["5m"].direction),
            fifteen_direction=int(signals["15m"].direction),
            hour_direction=int(signals["1H"].direction))
        early_parent_trend_entry = early_parent_trend_turn_allowed(
            local_turn=(local_high_short if direction < 0 else local_low_long),
            selected_trend_entry=(trend_continuation_entry
                                  or (stage_one_top_short_entry and direction < 0)),
            direction=direction, durable_bias=dual_reversal_bias,
            five_direction=int(signals["5m"].direction),
            fifteen_direction=int(signals["15m"].direction),
            hour_direction=int(signals["1H"].direction))
        early_throwback_short_entry = bool(early_parent_trend_entry and direction < 0)
        early_pullback_long_entry = bool(early_parent_trend_entry and direction > 0)
        lower_timeframe_pullback_entry = lower_timeframe_ma5_pullback_allowed(
            ma5_candidate=two_timeframe_intrabar_ma5_reversal_entry,
            direction=direction, one_direction=int(signals["1m"].direction),
            five_direction=int(signals["5m"].direction),
            fresh_lock_direction=(int(fresh_one_lock["direction"])
                                  if fresh_one_lock is not None else 0),
        )
        if lower_timeframe_pullback_entry:
            trend_continuation_entry = True
            terminal_reversal_entry = False
            store.record_event("dual_timeframe_ma5_pullback_qualified", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0], "direction": direction,
                "stop": structural_stop,
                "reason": "一分钟底部/顶部反转已形成新鲜趋势锁；一分钟与五分钟方向同向，"
                          "本次局部双周期MA5信号按回踩追多/反抽追空执行，不要求一分钟重新穿MA5，"
                          "也不要求五分钟相邻实体覆盖45%；仍核对结构止损、利润空间和账户安全",
            })
        if early_parent_trend_entry:
            boundary_evidence = higher_timeframe_boundary_evidence(now)
            five_live_colour, five_live_reason = five_minute_live_turn_evidence(
                markets.get("5m_live"), int(signals["5m"].direction))
            store.record_event(
                "downtrend_throwback_early_entry_qualified" if direction < 0
                else "uptrend_pullback_early_entry_qualified", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0], "direction": direction,
                "stop": structural_stop,
                "five_minute_live_candle_direction": five_live_colour,
                "five_minute_trend_direction": int(signals["5m"].direction),
                "reason": ("1分钟形成新局部高点并出现首个转弱迹象；"
                           if direction < 0 else
                           "1分钟形成新局部低点并出现首个转强迹象；")
                          + ("上一级5分钟/15分钟下降背景授权反抽追空，不要求1分钟下穿MA5或"
                             if direction < 0 else
                             "上一级5分钟/15分钟上涨背景授权回踩追多，不要求1分钟上穿MA5或")
                          + "5分钟覆盖45%；" + boundary_evidence +
                          "；" + five_live_reason +
                "；进入独立执行通道；最终仍核对仓位、风险、成本和利润空间",
            })
        parent_trend_reclassifies_local_entry = local_candidate_prefers_trend_continuation(
            direction=direction, five_direction=int(signals["5m"].direction),
            fifteen_direction=int(signals["15m"].direction),
            local_candidate=bool(
                stage_one_top_short_entry or local_high_short or local_low_long
                or aligned_trend_pullback_entry or lower_timeframe_pullback_entry
                or two_timeframe_intrabar_ma5_reversal_entry
                or trend_continuation_entry or three_timeframe_reversal_entry
                or confirmed_strong_reversal_entry or five_minute_local_reversal_entry),
            confirmed_reversal=bool(
                three_timeframe_reversal_entry or confirmed_strong_reversal_entry
                or five_minute_local_reversal_entry or endpoint_half_cover_primary_entry),
            durable_parent_direction=dual_reversal_bias,
        )
        if parent_trend_reclassifies_local_entry:
            trend_continuation_entry = True
            terminal_reversal_entry = False
            three_timeframe_reversal_entry = False
            persistent_cover_recovery_entry = False
            confirmed_strong_reversal_entry = False
            five_minute_local_reversal_entry = False
            endpoint_half_cover_primary_entry = False
            store.record_event("local_entry_reclassified_as_trend_continuation", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0], "direction": direction,
                "reason": "局部转折方向与最新有效上级趋势锁同向，趋势追单优先于严格局部反转；"
                          "反转证据只作辅助，不要求一分钟穿越MA5，也不要求五分钟相邻实体覆盖45%",
            })
        fresh_rising_pullback_candidate = bool(
            direction > 0 and five_uptrend_pullback_context
            and trend_continuation_entry
            and (early_freeze_stage_entry or local_low_long
                 or two_timeframe_intrabar_ma5_reversal_entry
                 or lower_timeframe_pullback_entry))
        trend_pullback_without_cover = bool(
            trend_continuation_entry
            or early_parent_trend_entry or aligned_trend_pullback_entry
            or breakout_pullback_entry or ma20_pullback_entry
            or lower_timeframe_pullback_entry
            or five_minute_color_rollover_short_entry)
        directional_cover_ok = False
        directional_cover_reason = ""
        if direction in {-1, 1}:
            matching_persistent_recovery = persistent_cover_recoveries.get(direction)
            directional_cover_ok, directional_cover_reason = directional_entry_half_cover_gate(
                markets["1m"], markets.get("1m_live"),
                markets["5m"], markets.get("5m_live"), direction,
                trend_continuation=trend_pullback_without_cover,
                frozen_cluster_cover=(
                    independent_top_cover_anchor if direction < 0 else None),
            )
            if not directional_cover_ok and persistent_cover_recovery_entry:
                directional_cover_ok = True
                directional_cover_reason = (
                    "mandatory live 5m adjacent cover was satisfied and persisted at "
                    f"{selected_frozen_cover['five_bar_time']}; this bounded missed-order/"
                    "reconnect recovery consumes that same event and is not a 1m bypass"
                )
            if (not directional_cover_ok and three_timeframe_reversal_entry
                    and matching_persistent_recovery is not None):
                _, selected_frozen_cover, _ = matching_persistent_recovery
                directional_cover_ok = True
                directional_cover_reason = (
                    "本次真正反转已使用同方向首次达到45%的5分钟相邻实体覆盖冻结证据；"
                    f"冻结周期={selected_frozen_cover['five_bar_time']}，当前K线不必重复制造45%覆盖")
            if (not directional_cover_ok and not trend_pullback_without_cover
                    and ((direction < 0 and fresh_top_overrides_bottom and recent_top_cover)
                         or (direction > 0 and fresh_bottom_overrides_top
                             and recent_bottom_cover))):
                selected_frozen_cover = dict(
                    recent_top_anchor if direction < 0 else recent_bottom_anchor)
                directional_cover_ok = True
                directional_cover_reason = (
                    "新鲜1分钟局部反转使用近10分钟已冻结的同向5分钟首次45%覆盖；"
                    f"冻结周期={selected_frozen_cover['five_bar_time']}，止损未被触及；"
                    "15分钟与1小时仅作背景，不要求当前5分钟K线重复覆盖")
            if not directional_cover_ok and not external_execution_entry:
                store.record_event("directional_fresh_half_cover_rejected", {
                    "strategy_version": VALIDATION_VERSION,
                    "instrument": cfg.okx.instruments[0],
                    "direction": direction,
                    "trend_continuation": bool(trend_continuation_entry),
                    "reason": directional_cover_reason,
                })
                return observe(f"{hierarchy_reason}；{directional_cover_reason}")
            hierarchy_reason = f"{hierarchy_reason}；{directional_cover_reason}"
            frozen_cover = selected_frozen_cover or frozen_cover_anchors.get(direction)
            if frozen_cover is None:
                current_five_bar = pd.Timestamp(
                    complete_five_minute.iloc[-1]["date"]).isoformat()
                frozen_row = store.directional_cover_anchor(
                    instrument=cfg.okx.instruments[0], direction=direction,
                    five_bar_time=current_five_bar)
                frozen_cover = dict(frozen_row) if frozen_row is not None else None
            if (frozen_cover is None and not external_execution_entry
                    and not trend_pullback_without_cover):
                return observe(f"{hierarchy_reason}; mandatory 5m cover has no frozen 1m stop anchor")
            if frozen_cover is not None and not trend_pullback_without_cover:
                structural_stop = float(frozen_cover["stop_reference"])
                selected_frozen_cover = frozen_cover
            if early_parent_trend_entry:
                hierarchy_reason = (
                    f"{hierarchy_reason}；新局部{'反抽高点' if direction < 0 else '回踩低点'}冻结小止损{structural_stop:.2f}；"
                    "五分钟覆盖尚待确认，不扩大本次止损")
            elif trend_pullback_without_cover:
                hierarchy_reason = (
                    f"{hierarchy_reason}；趋势回踩/反抽沿用自身结构止损{structural_stop:.2f}；"
                    "不读取反转专用的5分钟45%冻结锚点")
            else:
                hierarchy_reason = (
                    f"{hierarchy_reason}; first 45% live-5m cover froze the compact 1m "
                    f"{'high' if direction < 0 else 'low'} stop at {structural_stop:.2f}; "
                    "later movement cannot widen it")
        fresh_confirmed_reversal_entry = fresh_confirmed_reversal_overrides_old_bias(
            direction=direction, directional_cover_ok=directional_cover_ok,
            three_timeframe_reversal=three_timeframe_reversal_entry,
            confirmed_endpoint_reversal=confirmed_strong_reversal_entry,
            endpoint_pair_reversal=endpoint_half_cover_primary_entry,
            five_minute_local_reversal=five_minute_local_reversal_entry,
        )
        pullback_position_ok = False
        if (trend_continuation_entry or aligned_trend_pullback_entry) and direction:
            if early_parent_trend_entry:
                pullback_position_ok = True
                pullback_position_reason = ("新鲜1m反抽局部高点已在MA5外沿由专属分支核对"
                                            if direction < 0 else
                                            "新鲜1m回踩局部低点已在MA5外沿由专属分支核对")
            elif fresh_rising_pullback_candidate:
                pullback_position_ok, pullback_position_reason = fresh_uptrend_ma_edge_reclaim(
                    complete_one_minute)
            elif trend_pullback_without_cover:
                pullback_position_ok = True
                pullback_position_reason = (
                    "上级趋势与本级MA边沿回踩/反抽结构已由专属分支确认；"
                    "不套用反转专用的5分钟45%覆盖门")
            else:
                pullback_position_ok, pullback_position_reason = (
                    continuation_pullback_reached_ma5_ma10(markets["1m"], direction)
                )
            if not pullback_position_ok:
                store.record_event("trend_continuation_fast_ma_location_rejected", {
                    "strategy_version": VALIDATION_VERSION,
                    "instrument": cfg.okx.instruments[0],
                    "direction": direction,
                    "reason": hierarchy_reason,
                    "location_reason": pullback_position_reason,
                })
                return observe(f"{hierarchy_reason}；{pullback_position_reason}")
            hierarchy_reason = f"{hierarchy_reason}；{pullback_position_reason}"
        locked_trend_pullback_entry = bool(
            trend_continuation_entry
            and direction in {-1, 1}
            and dual_reversal_bias == direction
            and pullback_position_ok
        )
        primary_gate = None
        one_minute_best_position_entry = False
        pinets_independent_entry = bool(external_execution_entry and external_signal.source == "pinets")
        webhook_independent_entry = bool(external_execution_entry and external_signal.source == "webhook")
        edge_independent_entry = bool(external_execution_entry and external_signal.source == "edge")
        original_strategy_direction = direction
        if external_execution_entry and direction in {-1, 1}:
            trigger_frame = markets["1m_live"] if markets.get("1m_live") is not None and not markets["1m_live"].empty else markets["1m"]
            trigger_candle = trigger_frame.sort_values("date").iloc[-1]
            primary_gate = PrimaryEntryGate(True, f"{external_signal.source.upper()}实时事件已提供明确方向与保护价", structural_stop, pd.Timestamp(trigger_candle["date"]), float(trigger_candle["close"]), "external_execution_entry")
        if early_parent_trend_entry and primary_gate is None:
            primary_gate = PrimaryEntryGate(
                True,
                ("上一级下跌趋势反抽追空：新鲜1m高点首根阴线在MA5外沿转弱即执行，"
                 if direction < 0 else
                 "上一级上涨趋势回踩追多：新鲜1m低点首根阳线在MA5外沿转强即执行，")
                + "不等待1m穿越MA5或5m覆盖45%；同五分钟K线去重",
                structural_stop, signals["5m"].candle_time,
                float(complete_one_minute.iloc[-1]["close"]),
                ("downtrend_throwback_early_short" if direction < 0
                 else "uptrend_pullback_early_long"),
            )
        # Strategy-01 automatic execution is intentionally narrowed to one
        # method: fresh 1m reversal zone -> stage 2, with stage 3 as fallback.
        # Legacy continuation/MA20/multi-timeframe candidates stay in research
        # only, so they cannot short a local bottom or buy a local top.
        specialised_top_short = specialised_top_short_bypasses_observe_only_gate(
            direction,
            five_high_cover=five_minute_high_half_cover_short_entry,
            expanded_ma_top=expanded_ma_top_short_entry,
            top_weakening=top_weakening_ma5_short_entry,
            compact_top=compact_top_reversal_short_entry,
            intrabar_engulf=intrabar_bear_engulf_short_entry,
            intrabar_range=intrabar_range_ma5_short_entry,
            early_high_sweep=early_high_sweep_entry,
        )
        specialised_top_short = bool(
            specialised_top_short or five_minute_color_rollover_short_entry)
        if (direction and not external_execution_entry
                and not three_timeframe_reversal_entry
                and not persistent_cover_recovery_entry
                and not early_dual_pullback_cover_long_entry and not early_low_sweep_entry
                and not local_high_short
                and not locked_trend_pullback_entry
                and not specialised_top_short
                and not original_strategy_reversal_entry_allowed(
                    direction, early_freeze_stage_entry=early_freeze_stage_entry,
                    frozen_big_cross_entry=frozen_big_cross_entry,
                    five_minute_heikin_confirmed=True)):
            store.record_event("legacy_strategy_candidate_observe_only", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "direction": direction,
                "reason": hierarchy_reason,
                "policy": "1m_reversal_stage2_primary_stage3_fallback",
            })
            direction = 0
            hierarchy_reason = (
                "旧策略候选仅记录：实盘自动入口已收口为一分钟反转区；"
                "第二阶段价格穿过MA5立即执行，第三阶段MA5/MA10交叉只负责补漏；"
                "5分钟仅作形态参照，不否决一分钟"
            )
        if False:
            reversal_direction = pine_consensus.direction
            matching_trigger = (pine_trigger if pine_trigger is not None
                                and int(pine_trigger["direction"]) == reversal_direction else None)
            if matching_trigger is None:
                hierarchy_reason = (
                    f"{pine_consensus.reason}；5分钟/15分钟只识别翻转背景；"
                    "等待一分钟触发：扫损/局部极值冻结后价格穿MA5优先开仓；"
                    "如第二阶段漏单，则由MA5穿MA10新鲜交叉补漏"
                )
            else:
                direction = reversal_direction
                structural_stop = float(matching_trigger["stop"])
                hierarchy_reason = (
                    f"PineTS高周期翻转背景 + 一分钟三阶段完成 | {pine_consensus.reason} | "
                    f"trigger={matching_trigger['stage']}"
                )
                terminal_reversal_entry = True
                pinets_independent_entry = True
        if direction == 0:
            return observe(f"{hierarchy_reason}；当前尚无新的一分钟反转区第二/第三阶段触发")
        opposite_local_turn = local_low_long if direction < 0 else local_high_short
        if fresh_opposite_local_turn_blocks_continuation(
                direction=direction,
                trend_continuation_entry=bool(
                    trend_continuation_entry or aligned_trend_pullback_entry
                    or early_parent_trend_entry or ma20_pullback_entry),
                opposite_local_turn=opposite_local_turn):
            store.record_event("late_continuation_blocked_by_fresh_opposite_turn", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "blocked_direction": direction,
                "pending_direction": -direction,
                "reason": "新鲜一分钟反向局部转折已形成；旧趋势追单不得重新释放，等待反转接管或失效",
            })
            return observe(
                f"{hierarchy_reason}；新鲜一分钟反向局部转折已形成；"
                "旧趋势追单不得重新释放，等待反转接管或失效")
        if (pending_takeover_direction
                and direction == -pending_takeover_direction
                and not external_execution_entry
                and (trend_continuation_entry or aligned_trend_pullback_entry
                     or early_parent_trend_entry or ma20_pullback_entry)):
            store.record_event("old_trend_entry_blocked_by_fresh_endpoint", {
                "strategy_version": VALIDATION_VERSION,
                "instrument": cfg.okx.instruments[0],
                "blocked_direction": direction,
                "pending_direction": pending_takeover_direction,
                "reason": pending_takeover_reason,
            })
            return observe(f"{hierarchy_reason}；{pending_takeover_reason}")
        aggressive_compact_top_short = bool(
            cfg.strategy.risk_profile == "aggressive" and direction < 0
            and (five_minute_high_half_cover_short_entry or expanded_ma_top_short_entry
                 or top_weakening_ma5_short_entry
                 or compact_top_reversal_short_entry or intrabar_bear_engulf_short_entry
                 or intrabar_range_ma5_short_entry or intrabar_ma20_short_entry
                 or early_high_sweep_entry or five_minute_color_rollover_short_entry)
        )
        aggressive_compact_bottom_long = bool(
            cfg.strategy.risk_profile == "aggressive" and direction > 0
            and (delayed_five_ma5_bottom_entry or two_timeframe_intrabar_ma5_reversal_entry)
        )
        countertrend_price = (
            float(markets["1m_live"].sort_values("date").iloc[-1]["close"])
            if markets.get("1m_live") is not None and not markets["1m_live"].empty
            else float(signals["1m"].close)
        )
        countertrend_close = markets["1m"].sort_values("date")["close"].astype(float)
        countertrend_ma5 = float(
            (countertrend_close.tail(4).sum() + countertrend_price) / 5.0
        )
        if (not external_execution_entry and not stage_one_top_short_entry
                and not early_throwback_short_entry
                and not fresh_confirmed_reversal_entry
                and not (fresh_top_overrides_bottom if direction < 0
                         else fresh_bottom_overrides_top)
                and not countertrend_short_before_ma5_allowed(
                durable_bias=dual_reversal_bias, direction=direction,
                price=countertrend_price, ma5=countertrend_ma5)):
            return observe(
                f"{hierarchy_reason}；最新1分钟+5分钟扫底做多仍为主方向，局部顶部空单可放弃；"
                "若仍试空，必须在1分钟局部高点刚转弱且尚未下穿MA5时进入。"
                "当前价格已在MA5下方，属于迟到追空，禁止下单"
            )
        qualified_fresh_small_stop_shape = bool(
            early_freeze_stage_entry or frozen_big_cross_entry
            or fresh_confirmed_reversal_entry or endpoint_half_cover_primary_entry
            or five_minute_local_reversal_entry or early_parent_trend_entry
            or five_minute_color_rollover_short_entry
        )
        qualified_countertrend_extreme = bool(
            (dual_reversal_bias > 0 and direction < 0
             and (early_freeze_stage_entry or expanded_ma_top_short_entry
                  or confirmed_strong_reversal_entry))
            or (dual_reversal_bias < 0 and aggressive_compact_bottom_long)
        )
        if (dual_reversal_bias and direction != dual_reversal_bias
                and not external_execution_entry
                and not stage_one_top_short_entry
                and not early_throwback_short_entry
                and not fresh_confirmed_reversal_entry
                and not (fresh_top_overrides_bottom if direction < 0
                         else fresh_bottom_overrides_top)
                and not qualified_countertrend_extreme
                and not qualified_fresh_small_stop_shape):
            return observe(
                f"{hierarchy_reason}；{dual_reversal_bias_reason}；"
                "持久趋势方向优先；普通局部顶/底反向单停用，只有新的一分钟末端反转试仓可挑战旧趋势，"
                "并等待五分钟同向末端确认后正式替换")
        live_one_price = (float(markets["1m_live"].sort_values("date").iloc[-1]["close"])
                          if markets.get("1m_live") is not None and not markets["1m_live"].empty
                          else float(signals["1m"].close))
        one_close_for_ma20 = markets["1m"].sort_values("date")["close"].astype(float)
        dynamic_one_ma20 = float((one_close_for_ma20.tail(19).sum() + live_one_price) / 20.0)
        ma5_progression_entry = bool(
            two_timeframe_intrabar_ma5_reversal_entry
            and direction * (live_one_price - dynamic_one_ma20) > 0
        )
        if ma5_progression_entry:
            hierarchy_reason = (
                f"{hierarchy_reason}；已进入第三阶段：价格位于一分钟MA20"
                f"{'上方' if direction > 0 else '下方'}，使用最近局部回踩/反抽极值止损，"
                "不再套用初始底部/顶部38%位置门"
            )
        if (terminal_reversal_entry and cfg.strategy.risk_profile == "aggressive"
                and not external_execution_entry
                and not frozen_big_cross_entry and not early_freeze_stage_entry
                and not ma5_progression_entry and not pinets_independent_entry
                and not three_timeframe_reversal_entry
                and not stage_one_top_short_entry
                and not five_minute_high_half_cover_short_entry
                and not five_minute_color_rollover_short_entry):
            location_price = (float(markets["1m_live"].sort_values("date").iloc[-1]["close"])
                              if markets.get("1m_live") is not None and not markets["1m_live"].empty
                              else float(signals["1m"].close))
            location_ok, location_reason = reversal_range_location_gate(
                markets["1m"], markets["5m"], location_price, direction)
            if not location_ok:
                return observe(f"{hierarchy_reason}；{location_reason}；当前位置与反转方向相反，禁止下单")
            hierarchy_reason = f"{hierarchy_reason}；{location_reason}"
            one_minute_best_position_entry = True
        # Every specialised aggressive top branch already owns a compact local
        # stop and its own timing confirmation.  Once one is confirmed, do not
        # send it through the generic 5m trend gate for a second decision.
        if aggressive_compact_top_short and primary_gate is None:
            trigger_time = (pd.Timestamp(markets["1m_live"].sort_values("date").iloc[-1]["date"])
                            if two_timeframe_intrabar_ma5_reversal_entry else signals["1m"].candle_time)
            trigger_price = (float(markets["1m_live"].sort_values("date").iloc[-1]["close"])
                             if two_timeframe_intrabar_ma5_reversal_entry else float(signals["1m"].close))
            primary_gate = PrimaryEntryGate(
                True,
                "激进型顶部小止损独立通过：盘中/收盘阴线破MA5或短结构已经由专属分支确认；慢速均线和高周期方向只作背景，不重复拒绝",
                structural_stop,
                trigger_time,
                trigger_price,
            )
        if five_minute_bearish_rollover_short_entry and primary_gate is None:
            trigger_candle = markets["5m_live"].sort_values("date").iloc[-1]
            primary_gate = PrimaryEntryGate(
                True,
                "下降趋势连续阴线续单独立通过：五分钟换线续阴与一分钟同步走弱已确认；不要求覆盖前一根阳线",
                structural_stop,
                pd.Timestamp(trigger_candle["date"]),
                float(trigger_candle["close"]),
                "five_minute_bearish_rollover_continuation_short",
            )
        durable_continuation_primary = durable_regime_continuation_primary_gate_allowed(
            trend_continuation_entry=trend_continuation_entry,
            local_high_short=local_high_short, direction=direction,
            durable_bias=dual_reversal_bias, location_ok=pullback_position_ok,
        )
        if durable_continuation_primary and primary_gate is None:
            trigger_frame = (markets["1m_live"] if markets.get("1m_live") is not None
                             and not markets["1m_live"].empty else markets["1m"])
            trigger_candle = trigger_frame.sort_values("date").iloc[-1]
            primary_gate = PrimaryEntryGate(
                True,
                ("持久双周期底部反转锁定后的上涨趋势回踩追多独立通过："
                 "新的1分钟回踩到MA5/MA10外沿并转强即可执行；底部首单是否成交不影响趋势授权"
                 if direction > 0 else
                 "持久双周期顶部反转锁定后的下降趋势反抽追空独立通过："
                 "新的1分钟反抽到MA5/MA10外沿并转弱即可执行；顶部首单是否成交不影响趋势授权"),
                structural_stop, pd.Timestamp(trigger_candle["date"]),
                float(trigger_candle["close"]),
                ("locked_dual_bottom_uptrend_pullback_long"
                 if direction > 0 else
                 "locked_dual_top_downtrend_pullback_short"),
            )
        if aggressive_compact_bottom_long and primary_gate is None:
            trigger_time = (pd.Timestamp(markets["1m_live"].sort_values("date").iloc[-1]["date"])
                            if two_timeframe_intrabar_ma5_reversal_entry else signals["5m"].candle_time)
            trigger_price = (float(markets["1m_live"].sort_values("date").iloc[-1]["close"])
                             if two_timeframe_intrabar_ma5_reversal_entry else float(signals["5m"].close))
            primary_gate = PrimaryEntryGate(
                True,
                ("激进型双周期MA5小波段独立通过：一分钟、五分钟已在30分钟内汇合"
                 if two_timeframe_intrabar_ma5_reversal_entry else
                 "激进型错位底部接力独立通过：一分钟底部证据与延迟五分钟MA5上穿已由专属分支确认"),
                structural_stop,
                trigger_time,
                trigger_price,
            )
        if three_timeframe_reversal_entry and primary_gate is None:
            trigger_frame = (markets["1m_live"] if markets.get("1m_live") is not None
                             and not markets["1m_live"].empty else markets["1m"])
            trigger_candle = trigger_frame.sort_values("date").iloc[-1]
            primary_gate = PrimaryEntryGate(
                True,
                ("三周期底部/顶部反转独立通道：快速模式不等待6根/3根；"
                 "恢复模式仅在漏单或重连后使用最多6根一分钟、最多3根五分钟补单；"
                 "15分钟只要求由弱转强/由强转弱，不要求半覆盖"),
                structural_stop,
                pd.Timestamp(trigger_candle["date"]),
                float(trigger_candle["close"]),
                ("three_timeframe_fast_reversal" if three_timeframe_reversal_mode == "fast"
                 else "three_timeframe_reversal_recovery"),
            )
        if persistent_cover_recovery_entry and primary_gate is None:
            trigger_frame = (markets["1m_live"] if markets.get("1m_live") is not None
                             and not markets["1m_live"].empty else markets["1m"])
            trigger_candle = trigger_frame.sort_values("date").iloc[-1]
            primary_gate = PrimaryEntryGate(
                True,
                ("首次达到45%的5分钟反向覆盖已经持久化；本次属于断线或漏单后的限时恢复，"
                 "一分钟已重新转强/转弱、十五分钟已转向且首次冻结的小止损尚未失效；"
                 "不重新要求当前5分钟K线再次制造45%覆盖。"),
                structural_stop,
                pd.Timestamp(trigger_candle["date"]),
                float(trigger_candle["close"]),
                ("persisted_45pct_bottom_reversal_recovery_long"
                 if direction > 0 else
                 "persisted_45pct_top_reversal_recovery_short"),
            )
        if one_minute_best_position_entry and primary_gate is None:
            trigger_time = (pd.Timestamp(markets["1m_live"].sort_values("date").iloc[-1]["date"])
                            if markets.get("1m_live") is not None and not markets["1m_live"].empty
                            else signals["1m"].candle_time)
            trigger_price = (float(markets["1m_live"].sort_values("date").iloc[-1]["close"])
                             if markets.get("1m_live") is not None and not markets["1m_live"].empty
                             else float(signals["1m"].close))
            primary_gate = PrimaryEntryGate(
                True,
                ("一分钟三阶段最佳位置独立通过：局部下部/上部38%内的启动或反转证据已经成立；"
                 "五分钟只负责确认加分和持仓止盈升级，不再对该先行单进行二次否决"),
                structural_stop,
                trigger_time,
                trigger_price,
            )
        if frozen_big_cross_entry and primary_gate is None:
            trigger_time = signals["1m"].candle_time
            trigger_price = float(signals["1m"].close)
            primary_gate = PrimaryEntryGate(
                True,
                "冻结候选已由一分钟新鲜大交叉启动：禁止五分钟再次否决，禁止等待高周期补追",
                structural_stop,
                trigger_time,
                trigger_price,
            )
        if early_freeze_stage_entry and primary_gate is None:
            trigger_time = pd.Timestamp(latest_candle["date"])
            trigger_price = float(latest_candle["close"])
            freeze_five_local_reversal = bool(
                earliest is not None and earliest.get("five_minute_half_cover")
                and not earliest.get("full_endpoint_confirmation"))
            primary_gate = PrimaryEntryGate(
                True,
                ("上一级5分钟底部局部反转做多：五分钟阳线覆盖前一根阴线实体一半，"
                 "不要求一分钟先上穿MA5，也不冒充真正双周期反转"
                 if freeze_five_local_reversal and direction > 0 else
                 "上一级5分钟顶部局部反转做空：五分钟阴线覆盖前一根阳线实体一半，"
                 "不要求一分钟先下穿MA5，也不冒充真正双周期反转"
                 if freeze_five_local_reversal else
                 "一分钟局部极值后一、二阶段独立通过；五分钟只区分真正反转/趋势中继并作成交后MA5止盈管理，不等待五分钟颜色"),
                structural_stop,
                trigger_time,
                trigger_price,
                ("five_minute_bottom_local_reversal_half_cover_long"
                 if freeze_five_local_reversal and direction > 0 else
                 "five_minute_top_local_reversal_half_cover_short"
                 if freeze_five_local_reversal else ""),
            )
        if pinets_independent_entry and primary_gate is None:
            primary_gate = PrimaryEntryGate(
                True,
                f"PineTS翻转背景(多{pine_consensus.bullish_votes}/空{pine_consensus.bearish_votes})；"
                "一分钟扫损/局部极值后价格穿MA5优先开仓，MA5/MA10交叉负责补漏；"
                "同侧去重、持仓、收益空间及服务器止损检查仍为强制项",
                structural_stop,
                pd.Timestamp(latest_candle["date"]),
                float(latest_candle["close"]),
            )
        if early_dual_pullback_cover_long_entry and primary_gate is None:
            trigger_frame = (markets["1m_live"] if markets.get("1m_live") is not None
                             and not markets["1m_live"].empty else markets["1m"])
            trigger_candle = trigger_frame.sort_values("date").iloc[-1]
            primary_gate = PrimaryEntryGate(
                True,
                "双周期底部反转后的提前回踩追多独立通过：三线组合阳线已覆盖前段回落的一半，允许在MA5上穿前成交",
                structural_stop,
                pd.Timestamp(trigger_candle["date"]),
                float(trigger_candle["close"]),
            )
        if external_execution_entry and primary_gate is None:
            primary_gate = PrimaryEntryGate(True, f"{external_signal.source.upper()}实时信号独立通过",
                                            float(external_signal.stop_loss),
                                            pd.Timestamp(external_signal.occurred_at),
                                            float(external_signal.price))
        if primary_gate is None:
            primary_gate = five_minute_primary_entry_gate(
                markets["5m"], markets["15m"], markets["1H"], markets["1m"], direction,
                markets.get("5m_live"), markets.get("1m_live"), markets.get("15m_live"),
            )
        if not primary_gate.allowed:
            return observe(f"{hierarchy_reason}；{primary_gate.reason}")
        endpoint_half_cover_primary_entry = (
            primary_gate.trigger_code in {
                "one_minute_ma_fan_endpoint_five_minute_half_cover_confirmed",
                "five_minute_bottom_local_reversal_half_cover_long",
                "five_minute_top_local_reversal_half_cover_short",
            }
        )
        confirmed_endpoint_pair_primary_entry = (
            primary_gate.trigger_code
            == "one_minute_ma_fan_endpoint_five_minute_half_cover_confirmed")
        five_minute_local_reversal_entry = primary_gate.trigger_code in {
            "five_minute_bottom_local_reversal_half_cover_long",
            "five_minute_top_local_reversal_half_cover_short",
        }
        hierarchy_reason = f"{primary_gate.reason}；一分钟原信号仅作辅助｜{hierarchy_reason}"
        structural_stop = primary_gate.stop
        direction_map = {bar: item.direction for bar, item in signals.items()}
        if (cfg.strategy.risk_profile != "aggressive"
                and terminal_reversal_entry and not small_bottom_rebound_entry
                and not aggressive_recovery_long_entry
                and not aggressive_compact_top_short
                and not (cfg.strategy.risk_profile == "aggressive" and early_low_sweep_entry)
                and blocks_early_countertrend_reversal(direction_map, direction)):
            return observe("保守/稳妥型主周期方向反向：普通提前反转只记录；激进型不使用15分钟、1小时、4小时否决快速单")
        same_side_conflicts = same_side_entry_conflicts(
            snapshot, direction, sniper_prefix="QBVALSNP")
        core_continuation_addon = False
        local_reversal_addon = False
        position_layers = 0.0
        pending_open_order = False
        if trend_continuation_entry or five_minute_local_reversal_entry:
            open_same_side = [
                row for row in store.open_trade_lifecycles(VALIDATION_VERSION)
                if int(row["direction"]) == direction
                and str(row["instrument"]) == cfg.okx.instruments[0]
            ]
            core_reversal_open = any(
                bool(json.loads(row["signal_context_json"]).get(
                    "five_minute_ma5_core_hold"))
                for row in open_same_side
            )
            position_side = "long" if direction > 0 else "short"
            position_layers = sum(
                abs(float(item.get("pos") or 0))
                for item in snapshot.get("positions", [])
                if str(item.get("posSide", "")).lower() == position_side
            )
            pending_open_order = any(
                str(item.get("posSide", "")).lower() == position_side
                and str(item.get("reduceOnly", "false")).lower() != "true"
                for item in snapshot.get("orders", [])
            )
            if trend_continuation_entry:
                core_continuation_addon = durable_regime_continuation_addon_allowed(
                    core_reversal_open=core_reversal_open,
                    early_dual_entry=early_dual_pullback_cover_long_entry,
                    durable_bias=dual_reversal_bias,
                    direction=direction,
                    position_layers=position_layers,
                    pending_open_order=pending_open_order,
                )
            local_reversal_addon = repeated_local_reversal_addon_allowed(
                local_reversal_entry=five_minute_local_reversal_entry,
                durable_bias=dual_reversal_bias,
                direction=direction,
                position_layers=position_layers,
                pending_open_order=pending_open_order,
            )
            if core_continuation_addon:
                hierarchy_reason = (
                    f"核心反转仓由5分钟MA5管理；一分钟趋势出现新的同向回踩结构继续追单"
                    f"（当前{position_layers:g}层，最多3层）；{hierarchy_reason}")
            elif local_reversal_addon:
                hierarchy_reason = (
                    f"新的独立5分钟局部反转形态允许继续做同向第{position_layers + 1:g}层；"
                    f"当前{position_layers:g}层，最多3层；{hierarchy_reason}"
                )
        same_side_structural_addon = bool(
            core_continuation_addon or local_reversal_addon)
        if same_side_conflicts and not same_side_structural_addon:
            if dual_ma20_cross_continuation_entry and "升级趋势持有" in hierarchy_reason:
                return ValidationExecutionResult(
                    "manage", "五分钟已在15分钟内完成同向交叉：已有一分钟先行仓，不重复加仓，升级为五分钟MA5趋势接管")
            return ValidationExecutionResult("manage", f"{persistent_market_text} | same-side position/order already exists")
        if ((terminal_reversal_entry or endpoint_half_cover_primary_entry)
                and not local_reversal_addon
                and store.has_open_same_side_trade(
                    "strategy_01", cfg.okx.instruments[0], direction,
                    strategy_version=VALIDATION_VERSION)):
            return ValidationExecutionResult(
                "manage",
                f"{persistent_market_text} | same reversal seed already has an open lifecycle; duplicate top/bottom entry blocked",
            )
        if (not same_side_structural_addon and store.has_open_same_side_trade(
                "strategy_01", cfg.okx.instruments[0], direction,
                strategy_version=VALIDATION_VERSION)):
            if dual_ma20_cross_continuation_entry and "升级趋势持有" in hierarchy_reason:
                return ValidationExecutionResult(
                    "manage", "五分钟已在15分钟内完成同向交叉：已有一分钟先行生命周期，不重复下单，升级为五分钟MA5趋势接管")
            return ValidationExecutionResult("manage", f"{persistent_market_text} | durable lifecycle already has same-side exposure")
        entry_kind = (("three_timeframe_reversal_recovery_long" if direction > 0 else
                       "three_timeframe_reversal_recovery_short") if persistent_cover_recovery_entry else
                      "webhook_external_signal" if webhook_independent_entry else
                      ("three_timeframe_fast_reversal_long" if direction > 0 else
                       "three_timeframe_fast_reversal_short") if
                      three_timeframe_reversal_entry and three_timeframe_reversal_mode == "fast" else
                      ("three_timeframe_reversal_recovery_long" if direction > 0 else
                       "three_timeframe_reversal_recovery_short") if
                      three_timeframe_reversal_entry else
                      "pinets_luxalgo_independent" if pinets_independent_entry else
                      "five_minute_top_local_reversal_half_cover_short" if five_minute_local_reversal_entry and direction < 0 else
                      "five_minute_bottom_local_reversal_half_cover_long" if five_minute_local_reversal_entry else
                      "one_minute_ma_fan_endpoint_five_minute_half_cover_short" if confirmed_endpoint_pair_primary_entry and direction < 0 else
                      "one_minute_ma_fan_endpoint_five_minute_half_cover_long" if confirmed_endpoint_pair_primary_entry else
                      "early_dual_timeframe_pullback_cover_long" if early_dual_pullback_cover_long_entry else
                      "early_one_minute_frozen_stage_launch" if early_freeze_stage_entry else
                      "frozen_one_minute_big_cross_launch" if frozen_big_cross_entry else
                      "aggressive_five_minute_local_extreme_small_ma_cross" if five_minute_small_cross_entry else
                      "aggressive_one_minute_local_extreme_small_ma_cross" if one_minute_small_cross_entry else
                      "aggressive_two_timeframe_intrabar_ma5_reversal" if two_timeframe_intrabar_ma5_reversal_entry else
                      "aggressive_delayed_five_ma5_bottom_recovery_long" if delayed_five_ma5_bottom_entry else
                      "aggressive_five_minute_high_half_cover_short" if five_minute_high_half_cover_short_entry else
                      "aggressive_stage_one_local_top_short" if stage_one_top_short_entry else
                      "aggressive_expanded_ma_top_ma5_short" if expanded_ma_top_short_entry else
                      "aggressive_top_weakening_ma5_short" if top_weakening_ma5_short_entry else
                      "aggressive_local_top_intrabar_bear_engulf_short" if intrabar_bear_engulf_short_entry else
                      "aggressive_range_top_doji_ma5_intrabar_short" if intrabar_range_ma5_short_entry else
                      "aggressive_intrabar_ma20_break_short" if intrabar_ma20_short_entry else
                      "aggressive_intrabar_doji_ma5_cross_long" if intrabar_rebound_long_entry else
                      "aggressive_first_ma20_retest_intrabar_long" if first_ma20_retest_long_entry else
                      "aggressive_fifteen_minute_recovery_long" if aggressive_recovery_long_entry else
                      "aggressive_small_bottom_rebound_long" if small_bottom_rebound_entry else
                      "early_three_bear_ma20_short" if three_bear_entry else
                      "five_minute_color_rollover_short" if five_minute_color_rollover_short_entry else
                      "volume_stopping_pullback_long" if volume_stopping_entry else
                      "early_high_sweep_reject" if early_high_sweep_entry else
                      "early_low_sweep_reclaim" if early_low_sweep_entry else
                      "dual_timeframe_ma20_death_cross_short" if dual_ma20_cross_continuation_entry and direction < 0 else
                      "dual_timeframe_ma20_golden_cross_long" if dual_ma20_cross_continuation_entry else
                      "multi_timeframe_weakness_continuation_short" if multi_timeframe_weakness_short_entry else
                      "five_minute_bearish_rollover_continuation_short" if five_minute_bearish_rollover_short_entry else
                      ("downtrend_continuation_short" if direction < 0 else "uptrend_continuation_long") if trend_continuation_entry else
                      "breakout_pullback" if breakout_pullback_entry else
                      "ma20_pullback" if ma20_pullback_entry else "standard")
        if entry_kind in OBSERVE_ONLY_BRANCHES:
            store.record_event("review_branch_observe_only", {
                "branch": entry_kind, "direction": direction,
                "reason": "复盘快速/恢复做空分支转观察，等待独立验证"})
            return observe(f"{entry_kind}复盘后转为观察模式，不提交订单")
        review_anchor = ""
        if not (external_execution_entry or trend_continuation_entry or aligned_trend_pullback_entry
                or three_timeframe_reversal_entry or persistent_cover_recovery_entry
                or stage_one_top_short_entry or five_minute_high_half_cover_short_entry
                or webhook_independent_entry or pinets_independent_entry):
            location_ok, location_reason, review_anchor = reversal_location(
                markets["1m"], markets["5m"], direction)
            if not location_ok:
                store.record_event("review_location_rejected", {
                    "branch": entry_kind, "direction": direction, "reason": location_reason})
                return observe(location_reason)
            hierarchy_reason = f"{hierarchy_reason}；{location_reason}"
        if (early_freeze_stage_entry and earliest is not None
                and earliest.get("reversal_event_anchor_time")):
            # Trial ownership follows the concrete local-extreme event. A later
            # bottom/top therefore remains eligible even when the rolling
            # location window still reports the earlier extreme.
            review_anchor = (
                f"three-stage:{int(direction)}:"
                f"{earliest['reversal_event_anchor_time']}")
        ma20_takeover_at_entry = five_minute_ma20_takeover_confirmed(
            markets["5m"], direction)
        medium_trend_position = medium_trend_five_ma5_hold_eligible(
            durable_bias=dual_reversal_bias, direction=direction,
            confirmed_endpoint_reversal_entry=confirmed_strong_reversal_entry,
        ) or confirmed_endpoint_pair_primary_entry or (
            five_minute_local_reversal_entry and ma20_takeover_at_entry)
        stage3_recovery_entry = bool(
            frozen_big_cross_entry
            or (early_freeze_stage_entry and earliest is not None
                and earliest.get("stage") in {"small_golden_cross", "small_death_cross"})
        )
        entry_classification = classify_entry_category(
            direction,
            stage3_recovery=stage3_recovery_entry,
            true_endpoint_reversal=bool(
                (confirmed_strong_reversal_entry or confirmed_endpoint_pair_primary_entry)
                and not five_minute_local_reversal_entry),
            trend_continuation=bool(
                trend_continuation_entry or aligned_trend_pullback_entry),
            higher_timeframe_trend=bool(
                direction and three_reversal_bias == direction),
            higher_timeframe_source="15m",
            five_minute_local_reversal=five_minute_local_reversal_entry,
        )
        winning_template = winning_template_for(entry_classification)
        three_stage_audit = reversal_three_stage_condition_audit(
            markets["1m"], markets["5m"], direction)
        hierarchy_reason = (
            f"{hierarchy_reason}；盈利样本模板={winning_template.code}"
            f"（冻结样本{winning_template.evidence_count}笔）：{winning_template.rule}"
        )
        position_class = "ma_spread_endpoint_reversal" if medium_trend_position else "local_swing"
        ma5_anchor_hold_entry = False
        ma5_exit_anchor = 0.0
        if (not pinets_independent_entry and not webhook_independent_entry
                and not profile_allows_entry(cfg.strategy.risk_profile, entry_kind)):
            return observe(f"{profile_display(cfg.strategy.risk_profile)}已关闭{entry_kind}触发，等待更高确定性信号")
        # Every order is owned and deduplicated by the confirmed 5m trigger.
        # Intrabar/1m evidence can refine quality but cannot create another
        # order inside the same five-minute candle.
        execution_candle_time = getattr(primary_gate, "candle_time", None) or signals["5m"].candle_time
        age = (now - execution_candle_time.to_pydatetime().replace(tzinfo=timezone.utc)).total_seconds()
        if age < 0 or age > 720:
            return ValidationExecutionResult("blocked", "5分钟主触发行情已经过期")
        mark_data = client._request("GET", "/api/v5/public/mark-price", {"instType": "SWAP", "instId": cfg.okx.instruments[0]}).get("data", [])
        mark_item = mark_data[0] if mark_data else {}
        if int(time.time() * 1000) - int(mark_item.get("ts", 0)) >= 60_000:
            return ValidationExecutionResult("blocked", "mark price is stale")
        mark = Decimal(str(mark_item["markPx"]))
        ticker_data = client._request("GET", "/api/v5/market/ticker", {"instId": cfg.okx.instruments[0]}).get("data", [])
        if not ticker_data:
            return ValidationExecutionResult("blocked", "latest price is unavailable")
        latest_price = Decimal(str(ticker_data[0]["last"]))
        fixed_three_point_stop_entry = False
        qualified_cover_probe_entry = bool(
            early_freeze_stage_entry and earliest is not None and earliest["stage"] in {
                "price_above_flat_rising_ma5", "price_below_flat_falling_ma5",
            }
        )
        trend_following_entry = ma20_pullback_entry or trend_continuation_entry
        if trend_continuation_entry:
            micro_ok, micro_reason, micro_stop = waterfall_micro_pullback_stop(
                markets["1m"], markets["5m"], markets["15m"], float(latest_price), direction)
            if micro_ok:
                structural_stop = micro_stop
                hierarchy_reason = f"{hierarchy_reason}；{micro_reason}"
                waterfall_micro_entry = True
        if trend_following_entry:
            five_atr = latest_atr(markets["5m"])
            local_uptrend_runway = confirmed_uptrend_pullback_uses_local_runway(
                direction=direction,
                locked_trend_pullback=locked_trend_pullback_entry,
                fresh_rising_pullback=(fresh_rising_pullback_candidate
                                       and pullback_position_ok),
            )
            if local_uptrend_runway:
                hierarchy_reason += (
                    "；新鲜上涨回踩已核对MA5/MA10位置；不按离5分钟MA20的固定1 ATR距离否决，"
                    "仍执行局部追价、止损和最近结构边界2点空间检查")
            else:
                runway_ok, runway_reason, five_atr = trend_entry_runway(
                    markets["5m"], float(latest_price), direction,
                    max_ma20_atr=1.50 if double_rejection_entry else 1.25 if waterfall_micro_entry else 1.0)
                if not runway_ok:
                    return observe(f"{hierarchy_reason}；{runway_reason}")
            signal_price = getattr(primary_gate, "reference_price", 0.0) or float(signals["5m"].close)
            execution_location_ok, execution_location_reason = (
                trend_continuation_execution_price_ok(
                    markets["1m"], direction, signal_price, float(latest_price))
            )
            if not execution_location_ok:
                store.record_event("trend_continuation_execution_price_rejected", {
                    "strategy_version": VALIDATION_VERSION,
                    "instrument": cfg.okx.instruments[0],
                    "direction": direction,
                    "signal_price": signal_price,
                    "execution_price": float(latest_price),
                    "reason": execution_location_reason,
                })
                return observe(f"{hierarchy_reason}；{execution_location_reason}")
            hierarchy_reason = f"{hierarchy_reason}；{execution_location_reason}"
            adverse_move = ((signal_price - float(latest_price)) if direction < 0
                            else (float(latest_price) - signal_price))
            if adverse_move > five_atr * .35:
                return observe(
                    f"{hierarchy_reason}；确认后又运行{adverse_move / five_atr:.2f} ATR，剩余利润空间不足"
                )
        if three_timeframe_reversal_entry:
            signal_price = float(getattr(primary_gate, "reference_price", 0.0)
                                 or signals["1m"].close)
            location_ok, location_detail = fresh_reversal_execution_location(
                direction=direction, signal_price=signal_price,
                execution_price=float(latest_price), atr1=latest_atr(markets["1m"]),
                signal_time=execution_candle_time, execution_time=now)
            closed_cover = ((three_stage_audit.get("conditions") or [{}])[-1]
                            .get("evidence", "未记录"))
            hierarchy_reason = (
                f"{hierarchy_reason}；盘中5分钟覆盖：{directional_cover_reason}；"
                f"已收盘三阶段覆盖：{closed_cover}；{location_detail}")
            if not location_ok:
                store.record_event("late_reversal_execution_rejected", {
                    "strategy_version": VALIDATION_VERSION,
                    "direction": direction, "signal_time": execution_candle_time.isoformat(),
                    "signal_price": signal_price, "execution_price": float(latest_price),
                    "reason": location_detail,
                })
                if direction < 0 and "超过0.35 ATR" in location_detail:
                    retest_plan = late_top_reversal_pullback_limit_plan(
                        complete_one_minute, markets["5m"],
                        execution_candle_time, now, float(structural_stop))
                    store.record_event("late_top_reversal_ma5_retest_assessed", {
                        "strategy_version": VALIDATION_VERSION,
                        "signal_time": execution_candle_time.isoformat(),
                        "allowed": retest_plan.allowed,
                        "entry": retest_plan.entry, "stop": retest_plan.stop,
                        "target": retest_plan.target,
                        "reason": retest_plan.reason,
                    })
                    if retest_plan.allowed:
                        return submit_one_ma5_retest_limit(
                            store=store, client=client, snapshot=snapshot,
                            instrument=cfg.okx.instruments[0], direction=-1,
                            stage_time=execution_candle_time,
                            plan=retest_plan, atr=latest_atr(complete_one_minute),
                            stage="late_top_reversal_ma5_retest",
                            event_type="late_top_reversal_ma5_retest_submitted",
                            context_reason=location_detail)
                return observe(hierarchy_reason)
        tp_pct = Decimal(str(cfg.okx.validation_take_profit_pct))
        sl_pct = Decimal(str(cfg.okx.validation_stop_loss_pct))
        take_profit_value, _ = validation_exit_prices(float(latest_price), direction, float(tp_pct), float(sl_pct))
        stop_loss_value, stop_distance_pct, stop_atr_multiple = strategy01_dynamic_stop(
            markets["5m"], float(latest_price), direction,
        )
        if entry_kind == "standard":
            stop_loss_value, stop_distance_pct, stop_atr_multiple = strategy01_local_stop(
                markets["1m"], float(latest_price), direction,
            )
            local_plan = structure_protection_plan(
                float(latest_price), direction, stop_loss_value,
                latest_atr(markets["1m"]), latest_atr(markets["5m"]),
            )
            if not local_plan.allowed:
                return observe(f"{hierarchy_reason}；普通同向单{local_plan.reason}")
        original_dynamic_risk = abs(stop_loss_value - float(latest_price))
        if breakout_pullback_entry:
            stop_loss_value = structural_stop
            risk = float(latest_price) - stop_loss_value
            stop_distance_pct = risk / float(latest_price)
            if original_dynamic_risk > 0:
                stop_atr_multiple *= risk / original_dynamic_risk
        elif (ma20_pullback_entry or terminal_reversal_entry or trend_continuation_entry
              or endpoint_half_cover_primary_entry):
            # A recognised local pattern owns its own stop.  Never widen that
            # stop back to a remote fixed-window 5m extreme.
            stop_loss_value = structural_stop
            risk = (stop_loss_value - float(latest_price) if direction < 0
                    else float(latest_price) - stop_loss_value)
            stop_distance_pct = risk / float(latest_price)
            if original_dynamic_risk > 0:
                stop_atr_multiple *= risk / original_dynamic_risk
        else:
            risk = abs(stop_loss_value - float(latest_price))
        small_probe_entry = bool(
            early_freeze_stage_entry or qualified_cover_probe_entry
            or early_high_sweep_entry or early_low_sweep_entry
        )
        frozen_cover = selected_frozen_cover or frozen_cover_anchors.get(direction)
        if small_probe_entry:
            stage_three_recovery = bool(
                early_freeze_stage_entry and earliest is not None
                and earliest["stage"] in {"small_golden_cross", "small_death_cross"}
            )
            if stage_three_recovery:
                micro_ok, micro_stop, micro_reason = stage_three_micro_structure_stop(
                    complete_one_minute, float(latest_price), direction)
                if micro_ok:
                    structural_stop = micro_stop
                hierarchy_reason = f"{hierarchy_reason}；{micro_reason}"
            recent_structure_only = bool(
                confirmed_strong_reversal_entry or aligned_trend_pullback_entry
                or five_minute_local_reversal_entry or stage_three_recovery)
            cover_cluster_entry = bool(
                earliest is not None and earliest.get("five_minute_half_cover"))
            if frozen_cover is None:
                stop_allowed, protected_stop, stop_reason = reversal_stop_outside_recent_structure(
                    complete_one_minute, float(latest_price), direction,
                    (float(latest_price) if recent_structure_only else structural_stop),
                    lookback=(4 if (early_low_sweep_entry or five_minute_local_reversal_entry)
                              else 6 if cover_cluster_entry else 12),
                    maximum_points=(3.0 if five_minute_local_reversal_entry
                                    else float("inf") if recent_structure_only else 3.0),
                )
                if not stop_allowed:
                    return observe(f"{hierarchy_reason}；{stop_reason}")
                stop_loss_value = protected_stop
                structural_stop = protected_stop
                risk = abs(protected_stop - float(latest_price))
                stop_distance_pct = risk / float(latest_price)
                stop_atr_multiple = risk / max(latest_atr(complete_one_minute), 1e-9)
                hierarchy_reason = f"{hierarchy_reason}；{stop_reason}"
            else:
                hierarchy_reason = (
                    f"{hierarchy_reason}；首次五分钟覆盖已冻结局部保护价，"
                    "不再用后续4/6/12根K线扩大止损")
        # The first live-5m 45% cover owns the stop for every reversal branch.
        # Later stages may improve classification but must never replace this
        # compact 1m extreme with the remote high/low of the whole formation.
        if frozen_cover is not None and not early_throwback_short_entry:
            frozen_stop = float(frozen_cover["stop_reference"])
            correct_side = (frozen_stop > float(latest_price) if direction < 0
                            else frozen_stop < float(latest_price))
            if not correct_side:
                return observe(
                    f"{hierarchy_reason}; frozen first-cover stop is no longer on the protective side")
            stop_loss_value = frozen_stop
            structural_stop = frozen_stop
            risk = abs(frozen_stop - float(latest_price))
            stop_distance_pct = risk / float(latest_price)
            stop_atr_multiple = risk / max(latest_atr(complete_one_minute), 1e-9)
        if not external_execution_entry and frozen_cover is None:
            body_ok, body_stop, body_reason = latest_three_one_minute_body_stop(
                complete_one_minute, float(latest_price), direction)
            if not body_ok:
                return observe(f"{hierarchy_reason}；{body_reason}")
            stop_loss_value = structural_stop = body_stop
            risk = abs(body_stop - float(latest_price))
            stop_distance_pct = risk / float(latest_price)
            stop_atr_multiple = risk / max(latest_atr(complete_one_minute), 1e-9)
            hierarchy_reason = f"{hierarchy_reason}；{body_reason}"
        # Never compress a valid structural stop back inside the swing merely
        # to force a minimum-size order.  The sizing/risk gates below must
        # reject an unaffordable structure instead.
        if (ma20_pullback_entry and direction > 0 and stop_atr_multiple > 1.5
                and not fixed_three_point_stop_entry):
            return observe(f"{hierarchy_reason} | 1m回踩结构止损距离{stop_atr_multiple:.2f} ATR，超过1.5 ATR，放弃入场")
        # Early MA5 reversals and the first three-bear rollover must not chase
        # directly into the nearest five-minute body support/resistance. These
        # branches launch before a fully confirmed 5m reversal, so their normal
        # exemptions are unsafe when the remaining room is smaller than risk.
        # A farther historical extreme would overstate tradeable runway.
        if ((two_timeframe_intrabar_ma5_reversal_entry or three_bear_entry)
                and reversal_runway_required(
                    early_freeze_stage_entry=early_freeze_stage_entry)):
            runway_ok, runway_reason, _ = structure_profit_runway(
                markets["5m"], float(latest_price), direction, risk,
                minimum_r=1.2, minimum_atr=.8, nearest_boundary=True,
            )
            if not runway_ok:
                return observe(
                    f"{hierarchy_reason} | early reversal runway rejected: {runway_reason}"
                )
        if ((expanded_ma_top_short_entry or top_weakening_ma5_short_entry
                or fifteen_minute_recovery_long_entry)
                and reversal_runway_required(
                    early_freeze_stage_entry=early_freeze_stage_entry)):
            runway_ok, runway_reason, _ = structure_profit_runway(
                markets["5m"], float(latest_price), direction, risk,
                minimum_r=1.2, minimum_atr=.8, nearest_boundary=True,
            )
            if not runway_ok:
                return observe(
                    f"{hierarchy_reason} | confirmed reversal runway rejected: {runway_reason}"
                )
        if ((ma20_pullback_entry or breakout_pullback_entry or terminal_reversal_entry
             or trend_continuation_entry or endpoint_half_cover_primary_entry)
                and not aggressive_compact_top_short and not one_minute_best_position_entry
                and not frozen_big_cross_entry and not early_freeze_stage_entry
                and not early_low_sweep_entry):
            runway_ok, runway_reason, _ = structure_profit_runway(
                markets["5m"], float(latest_price), direction, risk, minimum_r=1.5,
                minimum_atr=0.0 if fixed_three_point_stop_entry else .8)
            if not runway_ok:
                return observe(f"{hierarchy_reason}；{runway_reason}")
        elif one_minute_best_position_entry or frozen_big_cross_entry or early_freeze_stage_entry:
            hierarchy_reason = (
                f"{hierarchy_reason}；启动区旧五分钟实体压力仅作预警，不再套用普通1.5R硬拒绝；"
                "继续使用本信号结构止损，结构过大时按下单位置固定3点保护"
            )
        # A structure farther than 2.5 ATR invalidates the entry.  Between 1.5
        # and 2.5 ATR the same formula naturally reduces contracts.
        if (stop_atr_multiple > 2.5 and not aggressive_recovery_long_entry
                and not early_freeze_stage_entry
                and not fixed_three_point_stop_entry):
            return observe(f"{hierarchy_reason} | 5m structure stop is {stop_atr_multiple:.2f} ATR away; entry abandoned")
        contracts = safe_strategy01_contracts_for_stop(
            int(cfg.okx.validation_contracts), stop_distance_pct,
            float(cfg.okx.validation_stop_loss_pct),
        )
        if contracts is None:
            return observe(
                f"{hierarchy_reason} | invalid zero stop distance; candidate rejected and automatic loop continues"
            )
        if contracts < 1:
            return observe(
                f"{hierarchy_reason} | dynamic stop requires less than OKX minimum 1 contract; entry abandoned"
            )
        if early_freeze_stage_entry:
            staged_risk_ok, staged_risk_reason = staged_launch_risk_allows(
                complete_one_minute, float(latest_price), float(stop_loss_value), direction,
                maximum_atr=(float("inf") if five_minute_local_reversal_entry else 2.0),
                minimum_contracts=1, calculated_contracts=contracts,
            )
            if not staged_risk_ok:
                return observe(f"{hierarchy_reason} | {staged_risk_reason}")
            hierarchy_reason = f"{hierarchy_reason}；{staged_risk_reason}"
        if terminal_reversal_entry or endpoint_half_cover_primary_entry:
            protection = structure_protection_plan(
                float(latest_price), direction, structural_stop,
                latest_atr(markets["1m"]), latest_atr(markets["5m"]),
                max_atr_1m=999.0 if (external_execution_entry or fixed_three_point_stop_entry or early_freeze_stage_entry) else 3.0 if aggressive_recovery_long_entry else 1.5,
                max_atr_5m=999.0 if (external_execution_entry or fixed_three_point_stop_entry or early_freeze_stage_entry) else 1.2 if aggressive_recovery_long_entry else .8,
            )
            if not protection.allowed:
                return observe(f"{hierarchy_reason}；{protection.reason}")
            stop_loss_value, risk = protection.stop, protection.risk
            take_profit_value, callback_value = protection.activation, protection.callback
            if volume_stopping_entry:
                reward = volume_stopping_target - float(latest_price)
                if reward < risk * 1.5:
                    return observe(f"{hierarchy_reason} | 五分钟MA20上方目标不足1.5R，放弃入场")
                take_profit_value = volume_stopping_target
        elif trend_continuation_entry:
            protection = structure_protection_plan(
                float(latest_price), direction, structural_stop,
                latest_atr(markets["1m"]), latest_atr(markets["5m"]),
                max_atr_1m=999.0 if fixed_three_point_stop_entry else 1.5,
                max_atr_5m=999.0 if fixed_three_point_stop_entry else .8,
            )
            if not protection.allowed:
                return observe(f"{hierarchy_reason}；{protection.reason}")
            stop_loss_value, risk = protection.stop, protection.risk
            take_profit_value, callback_value = protection.activation, protection.callback
        elif breakout_pullback_entry:
            _, take_profit_value, callback_value = breakout_long_protection(
                float(latest_price), stop_loss_value, markets["1m"]
            )
            risk = float(latest_price) - stop_loss_value
        elif direction < 0:
            take_profit_value = min(take_profit_value, float(latest_price) - risk * 1.5)
        else:
            take_profit_value = max(take_profit_value, float(latest_price) + risk * 1.5)
        if small_probe_entry:
            target_distance = (max(4.0, risk * 1.2)
                               if (aligned_trend_pullback_entry
                                   or confirmed_strong_reversal_entry
                                   or five_minute_local_reversal_entry) else 4.0)
            take_profit_value = (float(latest_price) - target_distance if direction < 0
                                 else float(latest_price) + target_distance)
            hierarchy_reason = (
                f"{hierarchy_reason}；顺势回踩/反抽目标至少1.2R且不少于4点"
                if (aligned_trend_pullback_entry or confirmed_strong_reversal_entry
                    or five_minute_local_reversal_entry) else
                f"{hierarchy_reason}；最早反转小止损单首个毛盈利目标固定4点"
            )
        if external_execution_entry:
            stop_loss_value = float(external_signal.stop_loss)
            take_profit_value = float(external_signal.take_profit)
            structural_stop = stop_loss_value
            risk = abs(float(latest_price) - stop_loss_value)
            reward = direction * (take_profit_value - float(latest_price))
            if direction * (float(latest_price) - stop_loss_value) <= 0 or reward <= 0:
                store.set_external_execution_status(external_signal.event_id, external_signal.source,
                                                    "rejected", "live price invalidated protection")
                return observe("外部实时信号的止损或止盈已被当前价格破坏")
        small_stop_fast_entry = bool(
            not external_execution_entry
            and (terminal_reversal_entry or endpoint_half_cover_primary_entry
                 or five_minute_local_reversal_entry or trend_continuation_entry
                 or aligned_trend_pullback_entry or early_parent_trend_entry)
        )
        estimated_cost = estimated_roundtrip_points(float(latest_price))
        # User policy: once a complete entry shape survives stop/risk review,
        # two gross points of executable room are sufficient.  R/ATR/cost are
        # still logged and used by sizing/protection, but do not raise this
        # final profit-space threshold above two points.
        minimum_r, minimum_atr, minimum_cost_points = 0.0, 0.0, 2.0
        space_ok, space_reason, _ = tradeable_profit_space(
            float(latest_price), direction, float(stop_loss_value), float(take_profit_value),
            latest_atr(markets["1m"]),
            minimum_r=minimum_r,
            minimum_atr=minimum_atr,
            minimum_price_pct=0.0 if (small_stop_fast_entry or fixed_three_point_stop_entry or early_freeze_stage_entry) else .0012,
            minimum_gross_points=minimum_cost_points,
            estimated_cost_points=0.0,
            minimum_net_points=0.0,
            quote_tolerance_points=0.0,
        )
        if not space_ok:
            return observe(f"{hierarchy_reason}｜{space_reason}")
        room_ok, room_reason, boundary = structure_profit_runway(
            markets["5m"], float(latest_price), direction,
            abs(float(latest_price) - float(stop_loss_value)),
            minimum_r=minimum_r, minimum_atr=minimum_atr,
            nearest_boundary=True, estimated_cost_points=estimated_cost,
            minimum_cost_multiple=0.0)
        required_room = 2.0
        if not room_ok or abs(boundary - float(latest_price)) < required_room:
            return observe(
                f"{room_reason}；最近结构边界内须留足"
                "2.00点毛利润空间")
        indicator_entry_class = (
            "reversal" if (terminal_reversal_entry or endpoint_half_cover_primary_entry) else
            "continuation" if trend_continuation_entry else "standard"
        )
        if external_execution_entry:
            indicator_ok = True
            indicator_reason = f"{external_signal.source.upper()}实时指标独立触发"
            indicator_metrics = external_signal.audit_payload()
        elif early_throwback_short_entry:
            indicator_ok = True
            indicator_reason = (
                "1m新反抽高点转弱和5m/15m下降已由专属分支核对；"
                "慢速指标只记录，不再次否决先行单")
            indicator_metrics = {"parent_downtrend": True,
                                 "five_cover_required_for_early_entry": False}
        elif aggressive_compact_top_short:
            indicator_ok = True
            indicator_reason = (
                "顶部专属小止损形态自行确认；1m、5m、15m、1H、4H方向箭头"
                "均已退出下单投票与否决")
            indicator_metrics = {"timeframe_arrows": "display_and_audit_only"}
        elif early_freeze_stage_entry:
            indicator_ok = True
            indicator_reason = (
                "一分钟普通K线反转区价格行为直接确认：扫损/局部极值后价格穿MA5提前入场，"
                "或MA5/MA10交叉补漏；滞后指标和15分钟/1小时不得否决"
            )
            indicator_metrics = {"candle_mode": "raw", "raw_execution_prices": True,
                                 "average_candles_decisional": False}
        else:
            indicator_ok, indicator_reason, indicator_metrics = indicator_confirmation_gate(
                markets["1m"], markets["5m"], markets["15m"], direction,
                entry_class=indicator_entry_class,
                price_structure_override=bool(
                    expanded_ma_top_short_entry or top_weakening_ma5_short_entry
                ),
                confirmed_uptrend_pullback=bool(
                    direction > 0 and trend_continuation_entry and
                    five_uptrend_pullback_context and pullback_position_ok),
            )
        store.record_event("strategy01_indicator_confirmation", {
            "strategy_version": VALIDATION_VERSION,
            "direction": direction,
            "entry_class": indicator_entry_class,
            "allowed": indicator_ok,
            "reason": indicator_reason,
            "metrics": indicator_metrics,
            "source": "okx_market_candles_not_browser_dom",
            "pinets_independent": pinets_independent_entry,
            "webhook_independent": webhook_independent_entry,
        })
        if not indicator_ok:
            return observe(f"{hierarchy_reason}｜{indicator_reason}")
        hierarchy_reason = f"{hierarchy_reason}｜{indicator_reason}"
        take_profit = Decimal(str(take_profit_value))
        stop_loss = Decimal(str(stop_loss_value))
        trailing_spread = Decimal(str(callback_value)) if (breakout_pullback_entry or terminal_reversal_entry or trend_continuation_entry or endpoint_half_cover_primary_entry) else (
            latest_price * Decimal(str(cfg.okx.validation_trailing_callback_pct))
        ).quantize(Decimal("0.01"), rounding=ROUND_UP)
        if direction > 0:
            side, position_side, suffix = "buy", "long", "L"
        else:
            side, position_side, suffix = "sell", "short", "S"
        entry_rule_audit = build_entry_rule_audit(
            direction=direction, classification=entry_classification,
            entry_kind=entry_kind, three_stage_audit=three_stage_audit,
            cover_ok=(None if early_throwback_short_entry else
                      True if (directional_cover_ok or external_execution_entry
                               or five_minute_high_half_cover_short_entry) else None),
            position_ok=(True if (pullback_position_ok or review_anchor
                                  or trend_continuation_entry or aligned_trend_pullback_entry
                                  or external_execution_entry) else None),
            profit_space_ok=bool(space_ok and room_ok), indicator_ok=indicator_ok,
            stop_side_ok=direction * (float(latest_price) - float(stop_loss)) > 0)
        store.record_event("entry_rule_contract_audit", {
            "strategy_version": VALIDATION_VERSION, "entry_rule_audit": entry_rule_audit})
        bar_time = execution_candle_time.isoformat()
        client_order_id = (stable_client_order_id(
                               "QBEX", external_signal.event_id, suffix, reserve=1)
                           if external_execution_entry else
                           "QBVAL" + execution_candle_time.strftime("%Y%m%d%H%M") + suffix)
        intent = SignalIntent(cfg.okx.instruments[0], bar_time, direction, VALIDATION_VERSION, float(sl_pct), "pending")
        if not store.record_intent(intent):
            return ValidationExecutionResult("duplicate", "this 1m validation candle was already processed")
        order = {}
        try:
            waterfall = waterfall_hold_signal(
                markets["1m"], markets["5m"], markets["15m"], direction,
            ) if trend_continuation_entry else None
            if waterfall and waterfall.active:
                activation_value, callback_value = waterfall_trailing_prices(
                    float(latest_price), direction, float(stop_loss), float(take_profit),
                    float(trailing_spread), waterfall.atr_5m,
                )
                take_profit = Decimal(str(activation_value))
                trailing_spread = Decimal(str(callback_value))
            aligned_primary_hold = trend_continuation_entry and primary_timeframes_aligned(direction_map, direction)
            exit_policy = shared_exit_policy(
                entry_kind,
                higher_timeframe_trend_confirmed=trend_continuation_entry,
                waterfall_confirmed=bool((waterfall and waterfall.active) or aligned_primary_hold),
            )
            if exit_policy.mode == "fixed" and not volume_stopping_entry:
                if ordinary_stop_is_too_close(
                    float(latest_price), direction, float(stop_loss), latest_atr(markets["1m"])
                ):
                    return observe("普通结构止损过近，等待第二次确认；不机械扩大风险")
                fixed = normalize_fixed_protection(
                    float(latest_price), direction, float(stop_loss), float(take_profit),
                    latest_atr(markets["1m"]),
                )
                tick = Decimal("0.01")
                stop_loss = Decimal(str(fixed.stop)).quantize(
                    tick, rounding=ROUND_DOWN if direction > 0 else ROUND_UP)
                take_profit = Decimal(str(fixed.take_profit)).quantize(
                    tick, rounding=ROUND_UP if direction > 0 else ROUND_DOWN)
            if review_anchor and not store.claim_reversal_trial(
                    cfg.okx.instruments[0], direction, review_anchor, client_order_id):
                store.update_intent_status(intent, "duplicate")
                return observe("同一反转极值已试单；平仓、止损、重启和换K线均不能重复试单")
            store.record_event("entry_all_gates_passed", {
                "strategy_version": VALIDATION_VERSION, "instrument": cfg.okx.instruments[0],
                "clOrdId": client_order_id, "direction": direction, "bar_time": bar_time,
                "entry_kind": entry_kind, "classification": entry_classification["label"],
                "reason": "全部实盘入场条件通过；已冻结客户端订单号，等待OKX开仓回执",
                "entry_rule_audit": entry_rule_audit,
            })
            response = client.place_demo_market_order(
                side, contracts, str(stop_loss),
                enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
                inst_id=cfg.okx.instruments[0], client_order_id=client_order_id,
                position_side=position_side,
                stop_loss_trigger_type="mark",
                take_profit_price=str(take_profit) if webhook_independent_entry else None,
                take_profit_trigger_type="last" if webhook_independent_entry else exit_policy.take_profit_trigger_type,
            )
            order = (response.get("data") or [{}])[0]
            if not order.get("ordId"):
                raise OkxError("OKX开仓响应缺少ordId；保留clOrdId等待对账，不自动重发")
            trailing_order = {}
            if exit_policy.uses_trailing:
                trailing_side = "sell" if direction > 0 else "buy"
                trailing_response = client.place_demo_trailing_order(
                    trailing_side, contracts,
                    None, str(take_profit),
                    enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
                    inst_id=cfg.okx.instruments[0], position_side=position_side,
                    client_algo_order_id=related_client_order_id(client_order_id, "T"),
                    callback_spread=str(trailing_spread),
                )
                trailing_order = (trailing_response.get("data") or [{}])[0]
            store.update_intent_status(intent, "submitted")
            if external_execution_entry:
                store.set_external_execution_status(external_signal.event_id, external_signal.source,
                                                    "submitted", f"ordId={order.get('ordId', '')}")
            store.record_event("validation_order_submitted", {
                "ordId": order.get("ordId"), "algoId": trailing_order.get("algoId"),
                "clOrdId": client_order_id, "direction": direction,
                "trigger_reason": hierarchy_reason,
                "directions": {bar: item.direction for bar, item in signals.items()},
                "confirmed_bar_time": bar_time, "confirmed_close": signals["5m"].close,
                "trailing_activation": str(take_profit), "trailing_callback_spread": str(trailing_spread),
                "protection_mode": exit_policy.mode,
                "waterfall_hold": bool(waterfall and waterfall.active),
                "waterfall_reason": waterfall.reason if waterfall else "非趋势延续单",
                "take_profit_trigger_type": exit_policy.take_profit_trigger_type,
                "sl": str(stop_loss), "entry_latest_price": str(latest_price),
                "contracts": contracts, "stop_distance_pct": stop_distance_pct,
                "stop_atr_multiple": stop_atr_multiple,
                "entry_type": entry_kind,
                "entry_classification_code": entry_classification["code"],
                "entry_classification_label": entry_classification["label"],
                "entry_classification_category": entry_classification["category"],
                "entry_trend_source_timeframe": entry_classification["trend_source_timeframe"],
                "entry_classification_rule": entry_classification["rule"],
                "local_reversal_addon": local_reversal_addon,
                "position_layer_before_entry": position_layers,
                "winning_trigger_template": winning_template.code,
                "winning_template_evidence_count": winning_template.evidence_count,
            })
            store.open_trade_lifecycle(
                trade_uid=client_order_id, strategy_id="strategy_01", strategy_version=VALIDATION_VERSION,
                instrument=cfg.okx.instruments[0], direction=direction, signal_time=bar_time,
                signal_reason=hierarchy_reason,
                signal_context={"directions": {bar: item.direction for bar, item in signals.items()},
                                "execution_observed_at": datetime.now(timezone.utc).isoformat(),
                                "review_anchor_time": review_anchor,
                                "estimated_cost_points": estimated_roundtrip_points(float(latest_price)),
                                "confirmed_close": signals["5m"].close,
                                "primary_trigger_timeframe": "1m",
                                "indicator_confirmation": indicator_metrics,
                                "indicator_entry_class": indicator_entry_class,
                                "signal_owner": (external_signal.source if external_execution_entry else "original_strategy"),
                                "external_event_id": external_signal.event_id if external_execution_entry else None,
                                "original_strategy_direction": original_strategy_direction,
                                "external_research_consensus": None,
                                "external_signal_snapshot": (external_signal.audit_payload() if external_execution_entry else None),
                                "submitted_before_entry": submitted_today,
                                "reversal_three_stage_audit": three_stage_audit,
                                "entry_rule_audit": entry_rule_audit,
                                "protection_mode": exit_policy.mode,
                                "take_profit_rule": (
                                    f"{('5m' if medium_trend_position else '1m')} MA5走平/拐弯接管"
                                    if exit_policy.uses_trailing else
                                    f"服务器固定止盈 {float(take_profit):.2f}"),
                                "ma5_anchor_hold": ma5_anchor_hold_entry,
                                "ma5_exit_anchor": ma5_exit_anchor,
                                "position_class": position_class,
                                "entry_classification_code": entry_classification["code"],
                                "entry_classification_label": entry_classification["label"],
                                "entry_classification_category": entry_classification["category"],
                                "entry_trend_source_timeframe": entry_classification["trend_source_timeframe"],
                                "entry_classification_rule": entry_classification["rule"],
                                "ma5_exit_timeframe": "5m" if medium_trend_position else "1m",
                                "five_minute_ma5_core_hold": medium_trend_position,
                                "local_reversal_addon": local_reversal_addon,
                                "position_layer_before_entry": position_layers,
                                "winning_trigger_template": winning_template.code,
                                "winning_template_evidence_count": winning_template.evidence_count,
                                "core_reversal_continuation_addon": bool(
                                    core_continuation_addon)},
                order_id=str(order.get("ordId", "")), algo_id=str(trailing_order.get("algoId", "")),
                entry_reference=float(latest_price), stop_price=float(stop_loss),
                trailing_activation=float(take_profit), trailing_callback=float(trailing_spread),
                branch=entry_kind,
            )
            for pattern_key in frozen_launch_pattern_keys:
                store.resolve_market_pattern_for_order(
                    pattern_key, direction=direction,
                    outcome={"order_id": str(order.get("ordId", "")),
                             "strategy_version": VALIDATION_VERSION},
                )
            for zone_key in active_price_zone_keys:
                store.resolve_market_pattern_for_order(
                    zone_key, direction=direction, submitted_bar_time=bar_time,
                    outcome={"order_id": str(order.get("ordId", "")),
                             "summary": "普通K线反转区已进入实盘订单",
                             "strategy_version": VALIDATION_VERSION},
                )
            return ValidationExecutionResult(
                "submitted",
                f"方向：{'做多' if direction > 0 else '做空'}｜分类：{entry_classification['label']}｜"
                f"分类规则：{entry_classification['rule']}｜技术分支：{entry_kind}｜"
                f"一分钟/五分钟位置与触发：{hierarchy_reason}｜"
                f"成交参考：{float(latest_price):.2f}｜局部止损：{float(stop_loss):.2f}｜"
                f"止盈启动：{float(take_profit):.2f}｜允许回撤：{float(trailing_spread):.2f}｜"
                f"持仓规则：服务器保留局部止损；"
                f"止盈逐级接管：一分钟大交叉后看五分钟MA5，五分钟大交叉后看十五分钟MA5，"
                f"十五分钟大交叉后看一小时MA5；高周期交叉只升级止盈，不触发追单。"
                f"{'空单在接管周期MA5由连续下滑转为走平或向上拐弯' if direction < 0 else '多单在接管周期MA5由连续上升转为走平或向下拐弯'}时正常止盈；"
                f"仅在已有充分浮盈且均线不再顺畅延伸时，异常衰竭形态才允许提前锁利",
                str(order.get("ordId", "")), direction, float(latest_price), float(take_profit), float(stop_loss),
            )
        except Exception as exc:
            store.update_intent_status(intent, "failed")
            store.record_event("validation_order_failed", {
                "clOrdId": client_order_id,
                "direction": direction,
                "bar_time": bar_time,
                "ordId": order.get("ordId"),
                "error": str(exc),
            })
            raise
    finally:
        store.close()

