from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import json
import sqlite3
import threading
import time

import pandas as pd

from .range_pivot import adx
from .review_quality import estimated_roundtrip_points
from .trend_regime import classify_trend_regime


_SUSPENSION_LOCK = threading.Lock()
_CONTRACT_SUSPENDED_UNTIL: dict[str, float] = {}


def _suspension_remaining(instrument: str) -> int:
    with _SUSPENSION_LOCK:
        remaining = _CONTRACT_SUSPENDED_UNTIL.get(instrument, 0.0) - time.monotonic()
    return max(0, int(remaining + .999))


def _mark_contract_suspended(instrument: str, seconds: int = 60) -> None:
    with _SUSPENSION_LOCK:
        _CONTRACT_SUSPENDED_UNTIL[instrument] = time.monotonic() + seconds


@dataclass(frozen=True)
class SniperLevel:
    direction: int
    entry: float
    stop: float
    take_profit: float
    reward_risk: float
    structure_time: str
    normal_stop: float | None = None


@dataclass(frozen=True)
class SniperPlan:
    allowed: bool
    reason: str
    timeframe: str
    short: SniperLevel | None = None
    long: SniperLevel | None = None
    short_levels: tuple[SniperLevel, ...] = ()
    long_levels: tuple[SniperLevel, ...] = ()
    ttl_seconds: int = 1800


@dataclass(frozen=True)
class SniperExecutionResult:
    action: str
    reason: str
    order_ids: tuple[str, ...] = ()


def staged_entry_phase(five_minute_frame: pd.DataFrame,
                       fifteen_minute_frame: pd.DataFrame | None = None) -> str:
    """Map the shared reversal/trend state to the staged entry queue."""
    regime = classify_trend_regime(five_minute_frame, fifteen_minute_frame)
    if "candidate" in regime.state or "waiting" in regime.state:
        return "反转等候期"
    if "reversal_confirmed" in regime.state:
        return "反转确认第一单"
    if regime.state in {"bullish_trend", "bearish_trend"}:
        return "趋势回踩第二/第三单"
    return "V型极值捕捉"


def _atr(frame: pd.DataFrame, period: int = 14) -> float:
    previous = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _confirmed_pivots(frame: pd.DataFrame, span: int = 2, lookback: int = 96,
                      selection: str = "strongest") -> tuple[pd.Series, pd.Series]:
    closed = frame.iloc[:-1].tail(lookback).copy()
    if len(closed) < span * 2 + 20:
        raise ValueError("结构K线数量不足")
    high_mask = pd.Series(True, index=closed.index)
    low_mask = pd.Series(True, index=closed.index)
    for offset in range(1, span + 1):
        high_mask &= closed["high"] > closed["high"].shift(offset)
        high_mask &= closed["high"] >= closed["high"].shift(-offset)
        low_mask &= closed["low"] < closed["low"].shift(offset)
        low_mask &= closed["low"] <= closed["low"].shift(-offset)
    highs, lows = closed[high_mask], closed[low_mask]
    if highs.empty or lows.empty:
        raise ValueError("没有找到已确认结构高低点")
    if selection == "latest":
        return highs.iloc[-1], lows.iloc[-1]
    # Use the strongest confirmed boundaries in the active structure window;
    # small later pivots inside the range must not replace the actual ceiling
    # or floor used for resting sniper orders.
    return highs.loc[highs["high"].idxmax()], lows.loc[lows["low"].idxmin()]


def _confirmed_pivot_rows(frame: pd.DataFrame, span: int = 2,
                          lookback: int = 96) -> tuple[pd.DataFrame, pd.DataFrame]:
    closed = frame.iloc[:-1].tail(lookback).copy()
    high_mask = pd.Series(True, index=closed.index)
    low_mask = pd.Series(True, index=closed.index)
    for offset in range(1, span + 1):
        high_mask &= closed["high"] > closed["high"].shift(offset)
        high_mask &= closed["high"] >= closed["high"].shift(-offset)
        low_mask &= closed["low"] < closed["low"].shift(offset)
        low_mask &= closed["low"] <= closed["low"].shift(-offset)
    return closed[high_mask], closed[low_mask]


def build_structure_sniper_plan(
    structure_frame: pd.DataFrame,
    one_minute_frame: pd.DataFrame,
    *,
    timeframe: str,
    adx_15m: float,
    ma20_slope_atr: float,
    minimum_reward_risk: float = 1.5,
    pivot_selection: str = "strongest",
    target_ma_frame: pd.DataFrame | None = None,
    max_levels_per_direction: int = 1,
) -> SniperPlan:
    """Build a Demo-only two-sided mean-reversion ambush plan."""
    if timeframe not in {"5m", "15m"}:
        return SniperPlan(False, "狙击结构周期只能使用5m或15m", timeframe)
    try:
        swing_high, swing_low = _confirmed_pivots(
            structure_frame, selection=pivot_selection)
        atr_1m = _atr(one_minute_frame)
    except (KeyError, ValueError, IndexError) as exc:
        return SniperPlan(False, str(exc), timeframe)
    if atr_1m <= 0:
        return SniperPlan(False, "1分钟ATR无效", timeframe)

    closes = (target_ma_frame if target_ma_frame is not None else one_minute_frame)["close"]
    ma5 = float(closes.rolling(5).mean().iloc[-1])
    ma10 = float(closes.rolling(10).mean().iloc[-1])
    short_entry = float(swing_high["high"])
    long_entry = float(swing_low["low"])
    short_target = max(ma5, ma10)
    long_target = min(ma5, ma10)
    buffer_short = max(atr_1m * 2.0, short_entry * .0020)
    buffer_long = max(atr_1m * 2.0, long_entry * .0020)
    short_risk = buffer_short
    long_risk = buffer_long
    short_reward = short_entry - short_target
    long_reward = long_target - long_entry
    short_minimum_reward = max(atr_1m * 3.0, short_entry * .0030, short_risk * minimum_reward_risk)
    long_minimum_reward = max(atr_1m * 3.0, long_entry * .0030, long_risk * minimum_reward_risk)
    if short_reward < short_minimum_reward:
        return SniperPlan(False, "前高到MA5/MA10的做空利润空间不足", timeframe)
    if long_reward < long_minimum_reward:
        return SniperPlan(False, "前低到MA5/MA10的做多利润空间不足", timeframe)

    short = SniperLevel(
        -1, short_entry, short_entry + buffer_short, short_target,
        short_reward / short_risk, str(swing_high.name),
    )
    long = SniperLevel(
        1, long_entry, long_entry - buffer_long, long_target,
        long_reward / long_risk, str(swing_low.name),
    )
    short_levels, long_levels = (short,), (long,)
    if pivot_selection == "latest" and max_levels_per_direction > 1:
        highs, lows = _confirmed_pivot_rows(structure_frame)
        short_levels = tuple(
            SniperLevel(-1, float(row["high"]), float(row["high"]) + buffer_short,
                        short_target, (float(row["high"]) - short_target) / short_risk, str(index))
            for index, row in highs.tail(max_levels_per_direction).iloc[::-1].iterrows()
            if float(row["high"]) - short_target >= short_minimum_reward
        ) or (short,)
        long_levels = tuple(
            SniperLevel(1, float(row["low"]), float(row["low"]) - buffer_long,
                        long_target, (long_target - float(row["low"])) / long_risk, str(index))
            for index, row in lows.tail(max_levels_per_direction).iloc[::-1].iterrows()
            if long_target - float(row["low"]) >= long_minimum_reward
        ) or (long,)
    return SniperPlan(True, "结构高低点分层狙击条件成立", timeframe,
                      short_levels[0], long_levels[0], short_levels, long_levels)


def _rounded_level(level: SniperLevel) -> tuple[str, str, str]:
    tick = Decimal("0.01")
    entry = Decimal(str(level.entry)).quantize(
        tick, rounding=ROUND_UP if level.direction < 0 else ROUND_DOWN)
    stop = Decimal(str(level.stop)).quantize(
        tick, rounding=ROUND_UP if level.direction < 0 else ROUND_DOWN)
    target = Decimal(str(level.take_profit)).quantize(
        tick, rounding=ROUND_DOWN if level.direction < 0 else ROUND_UP)
    return str(entry), str(stop), str(target)


def _disaster_stop(level: SniperLevel, atr_1m: float) -> float | None:
    """Return a bounded outer stop for a resting wick-capture entry.

    The ordinary structural stop is retained in ``normal_stop`` and is only
    restored after the fill bar plus two complete 1m bars.  A candidate is
    rejected when one contract would require protection beyond both the
    percentage and volatility risk ceilings.
    """
    entry = float(level.entry)
    normal = float(level.normal_stop if level.normal_stop is not None else level.stop)
    normal_risk = abs(entry - normal)
    required = max(normal_risk * 1.75, entry * .0060, atr_1m * 4.0)
    maximum = min(entry * .0120, atr_1m * 8.0)
    if required > maximum:
        return None
    return entry + required if level.direction < 0 else entry - required


def _completed_bars_after_fill(one: pd.DataFrame, filled_at: datetime) -> int:
    """Count complete 1m bars after the bar in which the entry filled."""
    if one.empty:
        return 0
    fill_bar = pd.Timestamp(filled_at).tz_convert("UTC").floor("min")
    dates = pd.to_datetime(one["date"], utc=True).sort_values()
    return int((dates > fill_bar).sum())


def _post_fill_decision(direction: int, entry: float, normal_stop: float,
                        mark: float, one: pd.DataFrame, completed_bars: int) -> str:
    """Return observe, tighten or exit for a disaster-protected sniper fill."""
    if completed_bars < 2 or one.empty:
        return "observe"
    closed = one.sort_values("date")
    latest_close = float(closed["close"].iloc[-1])
    invalid = ((direction > 0 and (mark <= normal_stop or latest_close <= normal_stop))
               or (direction < 0 and (mark >= normal_stop or latest_close >= normal_stop)))
    if invalid:
        return "exit"
    recovered = ((direction > 0 and latest_close > entry)
                 or (direction < 0 and latest_close < entry))
    if recovered:
        return "tighten"
    return "exit" if completed_bars >= 4 else "observe"


def _managed_protection_algos(snapshot: dict[str, list[dict]], client_order_id: str) -> list[dict]:
    protection_id = client_order_id[:31] + "P"
    found = [
        item for item in snapshot.get("algo_orders", [])
        if str(item.get("algoClOrdId", "")) == protection_id
    ]
    for position in snapshot.get("positions", []):
        found.extend(
            item for item in (position.get("closeOrderAlgo") or [])
            if str(item.get("algoClOrdId", "")) == protection_id
        )
    return found


def _open_sniper_lifecycle(store, state: dict, position: dict,
                           instrument: str, strategy_id: str,
                           filled_at: datetime) -> bool:
    """Adopt a confirmed resting sniper fill into the normal lifecycle ledger."""
    client_order_id = str(state["client_order_id"])
    entry_row = store.entry_snapshot(client_order_id)
    context = {}
    if entry_row is not None:
        try:
            context = json.loads(str(entry_row["context_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            context = {}
    direction = int(state["direction"])
    return store.open_trade_lifecycle_if_missing(
        trade_uid=client_order_id, strategy_id=strategy_id,
        strategy_version=str(state["strategy_version"]), instrument=instrument,
        direction=direction, signal_time=filled_at.isoformat(),
        signal_reason=(str(entry_row["trigger_reason"]) if entry_row is not None
                       else "结构狙击限价成交后恢复生命周期"),
        signal_context={**context, "resting_fill": True,
                        "winning_trigger_template": (
                            "bottom_reversal_long" if direction > 0
                            else "top_reversal_short")},
        order_id=str(state["order_id"]), algo_id="",
        entry_reference=float(position.get("avgPx") or state["entry_price"]),
        stop_price=float(state["disaster_stop"]),
        trailing_activation=float(context.get("take_profit") or 0),
        trailing_callback=0.0, branch="structure_sniper",
    )


def _manage_filled_sniper_protections(
    client, snapshot: dict[str, list[dict]], instrument: str,
    one: pd.DataFrame, *, database, strategy_id: str,
) -> tuple[set[str], SniperExecutionResult | None]:
    """Reconcile fills and tighten only after two complete post-fill 1m bars."""
    if database is None or not strategy_id:
        return set(), None
    from .state import StateStore
    store = StateStore(database)
    protected_sides: set[str] = set()
    try:
        active_positions = {
            str(item.get("posSide", "")): item for item in snapshot.get("positions", [])
            if abs(float(item.get("pos") or 0)) > 0
        }
        active_order_ids = {str(item.get("ordId", "")) for item in snapshot.get("orders", [])}
        all_algos = list(snapshot.get("algo_orders", []))
        for position in snapshot.get("positions", []):
            all_algos.extend(position.get("closeOrderAlgo") or [])

        # A copied workspace, restored database, or interrupted write can leave
        # OKX holding a live sniper position/protection while the local state
        # row is absent.  Rebuild it from the durable entry snapshot and the
        # protection client id instead of silently abandoning stop management.
        for side, position in active_positions.items():
            candidates = [
                item for item in all_algos
                if str(item.get("posSide", "")) == side
                and str(item.get("algoClOrdId", "")).endswith("P")
                and "SNP" in str(item.get("algoClOrdId", ""))
            ]
            for algo in candidates:
                protection_id = str(algo.get("algoClOrdId", ""))
                client_order_id = protection_id[:-1]
                if store.sniper_protection(client_order_id) is not None:
                    continue
                entry_row = store.entry_snapshot(client_order_id)
                if entry_row is None:
                    return protected_sides, SniperExecutionResult(
                        "protection_recovery_warning",
                        f"保护状态恢复失败：{side}持仓仍有灾难止损{protection_id}，"
                        "但本机缺少对应预埋单快照；已停止静默跳过，请核对本机数据库和运行版本",
                        (client_order_id,),
                    )
                try:
                    context = json.loads(str(entry_row["context_json"]))
                    normal_stop = float(context["normal_stop"])
                    disaster_stop = float(context.get("disaster_stop") or entry_row["stop_price"])
                    entry_price = float(entry_row["entry_reference"])
                    direction = int(entry_row["direction"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    return protected_sides, SniperExecutionResult(
                        "protection_recovery_warning",
                        f"保护状态恢复失败：预埋单{client_order_id}缺少正常止损或成交参考；"
                        "灾难止损保持不动，请人工核对",
                        (client_order_id,),
                    )
                store.record_sniper_protection(
                    client_order_id=client_order_id,
                    order_id=str(entry_row["order_id"] or "recovered"),
                    strategy_id=strategy_id,
                    strategy_version=str(entry_row["strategy_version"]),
                    instrument=instrument,
                    direction=direction,
                    entry_price=entry_price,
                    normal_stop=normal_stop,
                    disaster_stop=disaster_stop,
                )
                created_ms = int(position.get("cTime") or position.get("uTime") or 0)
                filled_at = (datetime.fromtimestamp(created_ms / 1000, timezone.utc)
                             if created_ms else datetime.now(timezone.utc))
                store.update_sniper_protection(
                    client_order_id, status="observing",
                    filled_at_utc=filled_at.isoformat(),
                    entry_bar_time=filled_at.replace(second=0, microsecond=0).isoformat(),
                )
                recovered_state = dict(store.sniper_protection(client_order_id))
                _open_sniper_lifecycle(
                    store, recovered_state, position, instrument, strategy_id, filled_at)
                return protected_sides, SniperExecutionResult(
                    "protection_state_recovered",
                    f"已从交易所持仓、灾难止损和本机预埋快照恢复{side}保护状态；"
                    f"继续等待完整一分钟K线后收紧到正常止损{normal_stop:.2f}",
                    (client_order_id,),
                )
        for row in store.sniper_protections(strategy_id, instrument):
            state = dict(row)
            side = "long" if int(state["direction"]) > 0 else "short"
            position = active_positions.get(side)
            algos = _managed_protection_algos(snapshot, str(state["client_order_id"]))
            if state["status"] == "pending":
                if str(state["order_id"]) in active_order_ids:
                    continue
                if position is None:
                    continue
                if not algos:
                    # A stale pending row must not claim an unrelated newer
                    # position merely because both use the same hedge side.
                    # Resting sniper lines are refreshed within 30 minutes;
                    # beyond that window, or when another same-side server
                    # protection exists, this local row cannot safely be the
                    # owner of the current position.
                    side_algos = [item for item in all_algos
                                  if str(item.get("posSide", "")) == side]
                    created_ms = int(position.get("cTime") or position.get("uTime") or 0)
                    try:
                        state_time = datetime.fromisoformat(
                            str(state["updated_at_utc"]).replace("Z", "+00:00"))
                        if state_time.tzinfo is None:
                            state_time = state_time.replace(tzinfo=timezone.utc)
                        position_time = (datetime.fromtimestamp(created_ms / 1000, timezone.utc)
                                         if created_ms else state_time)
                        unrelated_newer_position = position_time - state_time > timedelta(minutes=35)
                    except (TypeError, ValueError, OverflowError):
                        unrelated_newer_position = False
                    if side_algos or unrelated_newer_position:
                        store.update_sniper_protection(
                            str(state["client_order_id"]), status="closed")
                        store.record_event("stale_sniper_protection_closed", {
                            "client_order_id": str(state["client_order_id"]),
                            "side": side,
                            "reason": ("current side is protected by another order"
                                       if side_algos else
                                       "current position was created after the sniper ownership window"),
                        })
                        continue
                    return protected_sides, SniperExecutionResult(
                        "protection_recovery_warning",
                        f"保护状态异常：{state['client_order_id']}已经出现{side}持仓，"
                        "但未找到匹配的服务器灾难止损；已停止静默跳过，请立即核对OKX保护单",
                        (str(state["client_order_id"]),),
                    )
                created_ms = int(position.get("cTime") or position.get("uTime") or 0)
                filled_at = (datetime.fromtimestamp(created_ms / 1000, timezone.utc)
                             if created_ms else datetime.now(timezone.utc))
                store.update_sniper_protection(
                    str(state["client_order_id"]), status="observing",
                    filled_at_utc=filled_at.isoformat(),
                    entry_bar_time=filled_at.replace(second=0, microsecond=0).isoformat(),
                )
                state["status"] = "observing"
                state["filled_at_utc"] = filled_at.isoformat()
                _open_sniper_lifecycle(
                    store, state, position, instrument, strategy_id, filled_at)
            if state["status"] != "observing":
                continue
            if position is None:
                store.update_sniper_protection(str(state["client_order_id"]), status="closed")
                continue
            protected_sides.add(side)
            try:
                filled_at = datetime.fromisoformat(str(state["filled_at_utc"]).replace("Z", "+00:00"))
                if filled_at.tzinfo is None:
                    filled_at = filled_at.replace(tzinfo=timezone.utc)
                mark = float(position.get("markPx") or position.get("last") or 0)
                completed = _completed_bars_after_fill(one, filled_at)
                decision = _post_fill_decision(
                    int(state["direction"]), float(state["entry_price"]),
                    float(state["normal_stop"]), mark, one, completed,
                )
            except (TypeError, ValueError, KeyError):
                continue
            position_contracts = int(abs(float(position.get("pos") or 0)))
            if position_contracts > 1 and completed >= 2:
                same_side = [
                    dict(item) for item in store.sniper_protections(strategy_id, instrument)
                    if str(item["status"]) == "observing"
                    and (int(item["direction"]) > 0) == (side == "long")
                ]
                if len(same_side) >= 2:
                    # Keep the structurally better fill: lower entry for a
                    # long, higher entry for a short.  After the two complete
                    # one-minute observation bars, reduce only the worse layer
                    # so normal management converges to one contract per side.
                    worse = (max(same_side, key=lambda item: float(item["entry_price"]))
                             if side == "long" else
                             min(same_side, key=lambda item: float(item["entry_price"])))
                    if str(worse["client_order_id"]) == str(state["client_order_id"]):
                        client.close_demo_position_market(
                            1, position_side=side, enabled=True, max_contracts=10,
                            confirmation="DEMO-ORDER", inst_id=instrument,
                        )
                        store.update_sniper_protection(
                            str(state["client_order_id"]), status="exit_requested")
                        return protected_sides, SniperExecutionResult(
                            "layer_consolidated",
                            f"{side}两层结构预埋均已成交；完成两根1分钟观察K线后已减掉较差成交层，保留1张继续管理",
                            (str(state["client_order_id"]),),
                        )
            if decision == "observe":
                continue
            if decision == "exit":
                client.close_demo_position_market(
                    1, position_side=side, enabled=True, max_contracts=10,
                    confirmation="DEMO-ORDER", inst_id=instrument,
                )
                store.update_sniper_protection(
                    str(state["client_order_id"]), status="exit_requested")
                return protected_sides, SniperExecutionResult(
                    "post_fill_exit",
                    f"{side}结构预埋成交观察结束：未收回或正常结构失效，已只减1张",
                )
            if not algos:
                continue
            normal = Decimal(str(state["normal_stop"])).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN if side == "long" else ROUND_UP)
            client.tighten_active_stop_loss(algos[0], str(normal))
            store.update_sniper_protection(
                str(state["client_order_id"]), status="tightened")
            return protected_sides, SniperExecutionResult(
                "stop_tightened",
                f"{side}结构预埋成交已收回；入场K线及后两根完整1分钟K线确认后，"
                f"灾难止损已原地收紧到{normal}",
            )
    finally:
        store.close()
    return protected_sides, None


def _apply_final_sniper_gates(
    plan: SniperPlan, *, atr_1m: float, adx_15m: float,
    ma20_slope_atr: float, regime_direction: int,
) -> SniperPlan:
    """Reapply shared risk gates after every optional sniper layer.

    Overlay layers may replace a blocked base plan, so the absolute protection,
    profit-space and strong-trend direction checks must run last as well.
    """
    minimums = {}
    for direction, levels in ((-1, plan.short_levels or ((plan.short,) if plan.short else ())),
                              (1, plan.long_levels or ((plan.long,) if plan.long else ()))):
        accepted = []
        for level in levels:
            normal_stop = float(level.normal_stop if level.normal_stop is not None else level.stop)
            disaster_stop = _disaster_stop(replace(level, normal_stop=normal_stop), atr_1m)
            if disaster_stop is None:
                continue
            risk = abs(float(level.entry) - disaster_stop)
            reward = abs(float(level.take_profit) - float(level.entry))
            minimum_reward = max(3.0 * estimated_roundtrip_points(float(level.entry)),
                                 atr_1m * 3.0,
                                 risk * 1.5)
            if reward >= minimum_reward:
                accepted.append(replace(
                    level, stop=disaster_stop, normal_stop=normal_stop,
                    reward_risk=reward / risk,
                ))
        minimums[direction] = tuple(accepted)

    # Persistent structure lines are deliberately two-sided in every regime.
    # Strong trend still affects ordinary entries and risk exits, but it must
    # not make the protected support/pressure resting lines disappear.
    shorts, longs = minimums[-1], minimums[1]
    if not shorts and not longs:
        return SniperPlan(
            False,
            "共享最终门槛未通过：保护/利润空间不足",
            plan.timeframe,
        )
    return replace(
        plan, allowed=True,
        short=shorts[0] if shorts else None, short_levels=shorts,
        long=longs[0] if longs else None, long_levels=longs,
    )


def _candidate_waiting_layers(plan: SniperPlan, five: pd.DataFrame,
                              one: pd.DataFrame, regime) -> SniperPlan:
    """Queue reversal and failed-reversal continuation levels together."""
    if regime.state not in {"bearish_reversal_candidate", "bullish_reversal_candidate"}:
        return plan
    closed = five.iloc[:-1]
    atr5 = _atr(five)
    atr1 = _atr(one)
    ma20 = float(closed["close"].rolling(20).mean().iloc[-1])
    buffer = max(atr5 * .15, ma20 * .0003)
    try:
        high, low = _confirmed_pivots(five, selection="latest")
        highs, lows = _confirmed_pivot_rows(five)
    except ValueError:
        return plan
    if regime.state == "bearish_reversal_candidate":
        entries = tuple(dict.fromkeys((ma20 + buffer, float(high["high"]))))
        stop = max(entries) + max(atr1 * 2, max(entries) * .002)
        risk = stop - min(entries)
        target = min(entries) - risk * 1.5
        levels = tuple(SniperLevel(-1, entry, stop, target,
                                   (entry - target) / (stop - entry), str(high.name))
                       for entry in entries if stop > entry > target)
        long_target = ma20 + buffer
        long_entries = tuple(dict.fromkeys(float(row["low"])
                                            for _, row in lows.tail(2).iloc[::-1].iterrows()
                                            if float(row["low"]) < long_target))
        long_buffer = max(atr1 * 2, (min(long_entries) if long_entries else ma20) * .002)
        long_levels = tuple(
            SniperLevel(1, entry, entry - long_buffer, long_target,
                        (long_target - entry) / long_buffer, str(low.name))
            for entry in long_entries if long_target - entry >= long_buffer * 1.5
        )
        return replace(plan, short=levels[0], short_levels=levels,
                       long=long_levels[0] if long_levels else None, long_levels=long_levels,
                       reason="看跌反转等候区双情景：上方反转空；下方原上涨趋势回踩多，目标五分钟MA20上沿")
    entries = tuple(dict.fromkeys((ma20 - buffer, float(low["low"]))))
    stop = min(entries) - max(atr1 * 2, min(entries) * .002)
    risk = max(entries) - stop
    target = max(entries) + risk * 1.5
    levels = tuple(SniperLevel(1, entry, stop, target,
                               (target - entry) / (entry - stop), str(low.name))
                   for entry in entries if stop < entry < target)
    short_target = ma20 - buffer
    short_entries = tuple(dict.fromkeys(float(row["high"])
                                        for _, row in highs.tail(2).iloc[::-1].iterrows()
                                        if float(row["high"]) > short_target))
    short_buffer = max(atr1 * 2, (max(short_entries) if short_entries else ma20) * .002)
    short_levels = tuple(
        SniperLevel(-1, entry, entry + short_buffer, short_target,
                    (entry - short_target) / short_buffer, str(high.name))
        for entry in short_entries if entry - short_target >= short_buffer * 1.5
    )
    return replace(plan, short=short_levels[0] if short_levels else None,
                   short_levels=short_levels, long=levels[0], long_levels=levels,
                   reason="看涨反转等候区双情景：下方反转多；上方原下跌趋势反抽空，目标五分钟MA20下沿")


def _one_minute_stopping_limit_layer(plan: SniperPlan, five: pd.DataFrame,
                                     one: pd.DataFrame, regime) -> SniperPlan:
    """Prefer a resting buy at a closed 1m volume-stopping wick.

    This is an earlier limit-order stage, not permission to catch every falling
    candle.  A confirmed bearish 5m regime keeps the counter-trend long off.
    The later 1m V-reclaim market entry remains an independent fallback.
    """
    closed = one.iloc[:-1].sort_values("date").reset_index(drop=True)
    if len(closed) < 25 or "volume" not in closed:
        return plan
    atr1 = _atr(closed)
    if atr1 <= 0:
        return plan
    stopping = closed.iloc[-1]
    baseline = float(closed.iloc[-21:-1]["volume"].astype(float).mean())
    candle_range = max(float(stopping["high"] - stopping["low"]), 1e-9)
    lower_wick = min(float(stopping["open"]), float(stopping["close"])) - float(stopping["low"])
    is_stopping = (
        baseline > 0
        and float(stopping["volume"]) >= baseline * 1.5
        and float(stopping["close"]) <= float(stopping["open"])
        and lower_wick >= candle_range * .20
        and float(stopping["close"]) >= float(stopping["low"]) + candle_range * .20
    )
    if not is_stopping:
        return plan
    entry = float(stopping["low"]) + lower_wick * .15
    stop = float(stopping["low"]) - max(atr1 * .15, entry * .0003)
    close5 = five.iloc[:-1]["close"].astype(float)
    ma20_5 = float(close5.rolling(20).mean().iloc[-1])
    atr5 = _atr(five.iloc[:-1])
    bearish_confirmed = regime.direction < 0 and regime.state.endswith("_confirmed")
    if bearish_confirmed and entry > ma20_5 - atr5:
        return plan
    target = min(float(close5.rolling(5).mean().iloc[-1]),
                 float(close5.rolling(10).mean().iloc[-1]))
    risk, reward = entry - stop, target - entry
    # A visually attractive R multiple can still be an uneconomic tiny trade
    # when both stop and target are only a few ticks away.  Require enough
    # gross room to cover an estimated round trip twice (fees + slippage).
    minimum_gross_edge = max(risk * 1.5, atr1 * .80,
                             3.0 * estimated_roundtrip_points(entry))
    if risk <= 0 or reward < minimum_gross_edge:
        return plan
    level = SniperLevel(1, entry, stop, target, reward / risk,
                        pd.Timestamp(stopping["date"]).isoformat())
    existing = tuple(item for item in plan.long_levels if abs(item.entry - entry) >= atr1 * .25)
    levels = (level,) + existing[:1]
    return replace(plan, allowed=True, timeframe="1m", short=None, short_levels=(),
                   long=level, long_levels=levels,
                   reason=("下跌趋势深度乖离后一分钟放量止跌，优先在下影结构小风险预埋限价多单；"
                           if bearish_confirmed else
                           "一分钟放量止跌线成立，优先在下影结构预埋限价多单；")
                          + "V形确认市价单继续兜底")


def _ma20_breakout_first_pullback_limit_layer(
    plan: SniperPlan, five: pd.DataFrame, one: pd.DataFrame,
) -> SniperPlan:
    """Rest a long at the first 5m pullback after a real-body MA20 break.

    The level is created as soon as three closed 5m bodies stand above MA20,
    so it is already waiting before the pullback wick arrives.  The same
    breakout remains eligible for six subsequent closed bars while every body
    still closes above MA20; this gives the order persistence across restarts
    without preserving stale direction after structural invalidation.
    """
    closed = five.iloc[:-1].sort_values("date").reset_index(drop=True).copy()
    if len(closed) < 30:
        return plan
    closes = closed["close"].astype(float)
    ma20 = closes.rolling(20).mean()
    body_low = closed[["open", "close"]].astype(float).min(axis=1)
    atr5, atr1 = _atr(closed), _atr(one)
    if atr5 <= 0 or atr1 <= 0:
        return plan

    confirmation_end = None
    first = max(22, len(closed) - 9)
    for end in range(len(closed) - 1, first - 1, -1):
        start = end - 2
        if start < 20:
            continue
        bodies_above = bool((body_low.iloc[start:end + 1] > ma20.iloc[start:end + 1]).all())
        approached_from_below = bool(
            closes.iloc[start - 1] <= ma20.iloc[start - 1] + atr5 * .15
            or body_low.iloc[start - 1] <= ma20.iloc[start - 1]
        )
        if bodies_above and approached_from_below:
            confirmation_end = end
            break
    if confirmation_end is None:
        return plan

    after = closed.iloc[confirmation_end + 1:]
    after_ma20 = ma20.iloc[confirmation_end + 1:]
    # A close back under MA20 invalidates the breakout.  A wick through MA20
    # alone is the pullback we are trying to catch and does not cancel it.
    if len(after) > 6 or (not after.empty and
                          bool((after["close"].astype(float) <= after_ma20).any())):
        return plan

    confirmation = closed.iloc[confirmation_end - 2:confirmation_end + 1]
    anchor = float(confirmation[["open", "close"]].astype(float).min(axis=1).min())
    breakout_origin = float(closed.iloc[confirmation_end - 3]["low"])
    structure_low = min(breakout_origin, float(confirmation["low"].min()))
    stop = structure_low - max(atr1 * .15, anchor * .0003)
    # The recent swing/body ceiling is the realistic rebound objective.  It is
    # frozen when the order is built, rather than chasing a moving MA target.
    recent = closed.iloc[max(0, confirmation_end - 8):]
    target = max(float(recent[["open", "close"]].astype(float).max(axis=1).max()),
                 float(closes.rolling(5).mean().iloc[-1]),
                 float(closes.rolling(10).mean().iloc[-1]))
    minimum_cost_edge = max(atr1 * .80, anchor * .0012)
    # If the visible anchor is too high, improve the resting price instead of
    # falling back immediately to a worse market entry.
    rr_ceiling = (target + 1.5 * stop) / 2.5
    entry = min(anchor, target - minimum_cost_edge, rr_ceiling)
    minimum_risk = max(atr1 * .15, entry * .0003)
    if entry <= stop + minimum_risk or target <= entry:
        return plan
    risk, reward = entry - stop, target - entry
    if reward < max(risk * 1.5, atr1 * .80,
                    3.0 * estimated_roundtrip_points(entry)):
        return plan
    current = float(one["close"].iloc[-1])
    if entry >= current:
        return plan
    level = SniperLevel(
        1, entry, stop, target, reward / risk,
        pd.Timestamp(closed.iloc[confirmation_end]["date"]).isoformat(),
    )
    return replace(
        plan, allowed=True, timeframe="5m", short=None, short_levels=(),
        long=level, long_levels=(level,), ttl_seconds=1800,
        reason=("五分钟连续3根实体站上MA20，首次回踩优先在前实体支撑位置预埋限价多单；"
                "实体收回MA20下方、超过6根仍未回踩或收益空间失效才撤销；市价确认继续兜底"),
    )


def _pressure_zone_advance_short_layer(
    plan: SniperPlan, fifteen: pd.DataFrame, five: pd.DataFrame, one: pd.DataFrame,
) -> SniperPlan:
    """Rest a short at a 15m body-pressure zone after 5m/1m weakness.

    The entry deliberately uses the upper real-body area instead of an isolated
    wick.  Every 1m MA20 cross is audited by the shared signal centre; this
    layer only turns a recent cross into an order when 15m pressure and 5m
    rejection also agree.  A sequence of three descending highs is therefore
    useful evidence, but is not a mandatory gate for a critical reversal.
    """
    fifteen_closed = fifteen.iloc[:-1].sort_values("date").reset_index(drop=True)
    five_closed = five.iloc[:-1].sort_values("date").reset_index(drop=True)
    one_closed = one.iloc[:-1].sort_values("date").reset_index(drop=True)
    if len(fifteen_closed) < 8 or len(five_closed) < 20 or len(one_closed) < 22:
        return plan
    atr5, atr1 = _atr(five_closed), _atr(one_closed)
    if atr5 <= 0 or atr1 <= 0:
        return plan

    one_close = one_closed["close"].astype(float)
    one_open = one_closed["open"].astype(float)
    one_ma20 = one_close.rolling(20).mean()
    crossed = ((one_close < one_open) & (one_close < one_ma20) &
               ((one_close.shift(1) >= one_ma20.shift(1)) | (one_open >= one_ma20)))
    recent_crosses = crossed.iloc[-6:]
    if not bool(recent_crosses.any()):
        return plan
    cross_index = recent_crosses[recent_crosses].index[-1]

    pressure_window = fifteen_closed.iloc[-12:]
    body_high = pressure_window[["open", "close"]].astype(float).max(axis=1)
    pressure_body = float(body_high.quantile(.80))
    pressure_wick = float(pressure_window["high"].astype(float).max())
    entry = min(pressure_wick, pressure_body + atr5 * .30)
    latest5 = five_closed.iloc[-1]
    five_close = five_closed["close"].astype(float)
    five_ma5 = five_close.rolling(5).mean()
    five_ma10 = five_close.rolling(10).mean()
    rejected = bool(
        float(latest5["close"]) < float(latest5["open"])
        or float(latest5["close"]) < min(float(five_ma5.iloc[-1]), float(five_ma10.iloc[-1]))
        or float(latest5["high"]) < float(five_closed.iloc[-2]["high"])
    )
    approached = float(five_closed.iloc[-6:]["high"].astype(float).max()) >= entry - atr5 * .55
    current = float(one["close"].iloc[-1])
    if not rejected or not approached or current >= entry:
        return plan

    recent5_high = float(five_closed.iloc[-6:]["high"].astype(float).max())
    stop = max(recent5_high, entry + atr5 * .65) + max(atr1 * .15, entry * .0003)
    target = float(one_closed.iloc[-40:]["low"].astype(float).min())
    risk, reward = stop - entry, entry - target
    if risk <= 0 or reward < max(risk * 1.5, atr1 * .80,
                                 3.0 * estimated_roundtrip_points(entry)):
        return plan
    level = SniperLevel(
        -1, entry, stop, target, reward / risk,
        pd.Timestamp(one_closed.loc[cross_index, "date"]).isoformat(),
    )
    return replace(
        plan, allowed=True, timeframe="15m+5m+1m", short=level,
        short_levels=(level,), ttl_seconds=max(plan.ttl_seconds, 900),
        reason=("十五分钟实体压力区、五分钟受阻和一分钟阴线下穿MA20共同成立；"
                "优先在实体压力位预埋限价空单，未成交时保留原市价确认兜底；"
                "关键反转不强制等待连续三个降低高点"),
    )


def _active_risk_exit_reason(
    position_side: str, five: pd.DataFrame, one: pd.DataFrame, age_minutes: float,
    *, position_profitable: bool = True,
) -> str:
    """Return an evidence-based early-exit reason, or an empty string.

    The structural stop remains the server-side safety floor.  Profit is
    realized locally when the closed 5m candle meets a flattening/turning MA5,
    with the existing faster adverse-reversal evidence allowed to exit sooner.
    It applies equally to limit-entry and market-entry positions.
    """
    if not position_profitable:
        return ""
    five_closed = five.iloc[:-1].sort_values("date").reset_index(drop=True)
    one_closed = one.iloc[:-1].sort_values("date").reset_index(drop=True)
    if len(five_closed) < 22 or len(one_closed) < 22:
        return ""

    five_close = five_closed["close"].astype(float)
    one_close = one_closed["close"].astype(float)
    five_ma5 = five_close.rolling(5).mean()
    five_ma10 = five_close.rolling(10).mean()
    five_ma20 = five_close.rolling(20).mean()
    one_ma5 = one_close.rolling(5).mean()
    one_ma20_fast_exit = one_close.rolling(20).mean()
    atr5 = _atr(five_closed)
    atr1 = _atr(one_closed)
    latest1 = one_closed.iloc[-1]
    latest_range = max(float(latest1["high"] - latest1["low"]), 1e-9)
    latest_body = abs(float(latest1["close"] - latest1["open"]))
    recent_run = float(one_closed.iloc[-4:]["high"].max() - one_closed.iloc[-4:]["low"].min())
    ma20_distance = abs(float(latest1["close"]) - float(one_ma20_fast_exit.iloc[-1]))
    volume_mean = (float(one_closed.iloc[-21:-1]["volume"].astype(float).mean())
                   if "volume" in one_closed else 0.0)
    expansion = (recent_run >= atr1 * 1.5 and ma20_distance >= atr1 * .8
                 and (latest_range >= atr1 * 1.2 or
                      (volume_mean > 0 and float(latest1.get("volume", 0)) >= volume_mean * 1.5)))
    if position_side == "long":
        tip_rejection = (
            float(latest1["high"] - max(latest1["open"], latest1["close"])) >= max(latest_body, atr1 * .35)
            and float(latest1["close"]) <= float(latest1["high"]) - latest_range * .45
        )
    else:
        tip_rejection = (
            float(min(latest1["open"], latest1["close"]) - latest1["low"]) >= max(latest_body, atr1 * .35)
            and float(latest1["close"]) >= float(latest1["low"]) + latest_range * .45
        )
    if expansion and tip_rejection:
        tip = "快速拉升/向上扎针高点" if position_side == "long" else "快速下跌/向下扎针低点"
        return f"1分钟{tip}出现已收盘长影反向拒绝；优先强制止盈，早于MA5转向规则"
    one_ma5_now = float(one_ma5.iloc[-1])
    one_ma5_slope = ((one_ma5_now - float(one_ma5.iloc[-3])) / atr1
                     if atr1 > 0 else 999.0)
    one_near_ma5 = atr1 * .10
    if position_side == "long":
        one_minute_ma5_take_profit = (
            float(latest1["close"]) < float(latest1["open"])
            and one_ma5_slope <= .02
            and float(latest1["low"]) <= one_ma5_now + one_near_ma5
            and float(latest1["close"]) <= one_ma5_now + one_near_ma5
        )
    else:
        one_minute_ma5_take_profit = (
            float(latest1["close"]) > float(latest1["open"])
            and one_ma5_slope >= -.02
            and float(latest1["high"]) >= one_ma5_now - one_near_ma5
            and float(latest1["close"]) >= one_ma5_now - one_near_ma5
        )
    if one_minute_ma5_take_profit:
        direction = "走平或向下弯头" if position_side == "long" else "走平或向上抬头"
        return f"1分钟已收盘K线触及MA5，且MA5已经{direction}；按趋势镜像规则强制止盈"
    current5 = five_closed.iloc[-1]
    ma5_now = float(five_ma5.iloc[-1])
    ma5_slope = (ma5_now - float(five_ma5.iloc[-3])) / atr5 if atr5 > 0 else 999.0
    near_ma5 = atr5 * .10
    if position_side == "long":
        mandatory_ma5_take_profit = (
            float(current5["close"]) < float(current5["open"])
            and ma5_slope <= .03
            and float(current5["low"]) <= ma5_now + near_ma5
            and float(current5["close"]) <= ma5_now + near_ma5
        )
    else:
        mandatory_ma5_take_profit = (
            float(current5["close"]) > float(current5["open"])
            and ma5_slope >= -.03
            and float(current5["high"]) >= ma5_now - near_ma5
            and float(current5["close"]) >= ma5_now - near_ma5
        )
    if mandatory_ma5_take_profit:
        return ("5分钟已收盘K线触及MA5，且MA5已经走平或向不利方向弯头；"
                "按趋势MA5交叉规则强制止盈")
    one_ma10 = one_close.rolling(10).mean()
    one_ma20 = one_close.rolling(20).mean()
    volume_baseline = float(one_closed.iloc[-21:-1]["volume"].astype(float).mean()) \
        if "volume" in one_closed else 0.0
    volume_reversal = (
        volume_baseline > 0
        and float(latest1.get("volume", 0)) >= volume_baseline * 1.8
    )

    if position_side == "long":
        five_break = bool(
            five_close.iloc[-1] < five_ma20.iloc[-1]
            and five_ma5.iloc[-1] < five_ma10.iloc[-1]
            and five_close.iloc[-2] < five_ma10.iloc[-2]
        )
        one_break = bool(
            one_close.iloc[-1] < one_ma20.iloc[-1]
            and one_close.iloc[-2] < one_ma20.iloc[-2]
            and one_ma5.iloc[-1] < one_ma10.iloc[-1] < one_ma20.iloc[-1]
        )
        violent_reversal = bool(
            volume_reversal and float(latest1["close"]) < float(latest1["open"])
            and one_close.iloc[-1] < one_ma10.iloc[-1]
        )
        stale_failure = bool(
            age_minutes >= 30 and one_close.iloc[-3:].max() < one_ma10.iloc[-3:].max()
            and five_close.iloc[-1] < five_ma10.iloc[-1]
        )
    else:
        five_break = bool(
            five_close.iloc[-1] > five_ma20.iloc[-1]
            and five_ma5.iloc[-1] > five_ma10.iloc[-1]
            and five_close.iloc[-2] > five_ma10.iloc[-2]
        )
        one_break = bool(
            one_close.iloc[-1] > one_ma20.iloc[-1]
            and one_close.iloc[-2] > one_ma20.iloc[-2]
            and one_ma5.iloc[-1] > one_ma10.iloc[-1] > one_ma20.iloc[-1]
        )
        violent_reversal = bool(
            volume_reversal and float(latest1["close"]) > float(latest1["open"])
            and one_close.iloc[-1] > one_ma10.iloc[-1]
        )
        stale_failure = bool(
            age_minutes >= 30 and one_close.iloc[-3:].min() > one_ma10.iloc[-3:].min()
            and five_close.iloc[-1] > five_ma10.iloc[-1]
        )
    if five_break and one_break:
        return "1分钟与5分钟同向反转并破坏MA20持仓结构"
    if violent_reversal:
        return "1分钟放量反向实体破坏持仓均线带"
    if stale_failure:
        return "持仓超过30分钟未到止盈且横盘后向不利方向失守MA10"
    return ""
    return replace(
        plan, allowed=True, timeframe="5m", short=None, short_levels=(),
        long=level, long_levels=(level,), ttl_seconds=1800,
        reason=("五分钟连续3根实体站上MA20，首次回踩前实体支撑限价多单持续等待；"
                "实体收回MA20下方、超过6根未回踩或收益空间失效才撤销；市价确认继续兜底"),
    )


def execute_structure_sniper_tick(
    client, snapshot: dict[str, list[dict]], instrument: str, strategy_prefix: str,
    five_minute_frame: pd.DataFrame, fifteen_minute_frame: pd.DataFrame,
    one_minute_frame: pd.DataFrame,
    *, database=None, strategy_id: str = "", strategy_version: str = "",
    preferred_timeframes: tuple[str, ...] = ("15m", "5m"),
    allowed_directions: tuple[int, ...] = (-1, 1),
    pivot_selection: str = "strongest",
    target_ma_timeframe: str = "1m",
    max_levels_per_direction: int = 1,
) -> SniperExecutionResult:
    """Maintain Demo-only layered support/pressure entries.

    Up to two distinct structure levels per side may fill in OKX long/short
    mode.  Each fill keeps its audit snapshot; active-risk exits reduce one
    contract at a time.  This is not exchange-atomic OCO behavior.
    """
    owned = [
        item for item in snapshot.get("orders", [])
        if str(item.get("clOrdId", "")).startswith(strategy_prefix + "SNP")
    ]
    # A structural limit is an entry idea for the structure that created it,
    # not a permanent price trap.  The old implementation deliberately kept
    # matching orders indefinitely; an order placed at 10:05 was consequently
    # filled at 13:43 after the market regime had already changed.  Expire the
    # entire paired plan after 30 minutes so an obsolete opposite-side line
    # cannot open a position hours later.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_owned = []
    for item in owned:
        try:
            created_ms = int(item.get("cTime") or item.get("uTime") or now_ms)
        except (TypeError, ValueError):
            created_ms = now_ms
        if now_ms - created_ms >= 30 * 60_000:
            stale_owned.append(item)
    if stale_owned:
        client.cancel_orders(owned)
        return SniperExecutionResult(
            "expired",
            "结构狙击预埋单已超过30分钟有效期，整组旧单已撤销；下一轮按当前结构重新评估",
            tuple(str(item.get("ordId", "")) for item in owned),
        )
    active_positions = [
        item for item in snapshot.get("positions", []) if abs(float(item.get("pos") or 0)) > 0
    ]
    active_sides = {str(item.get("posSide", "")) for item in active_positions}
    total_contracts = sum(abs(float(item.get("pos") or 0)) for item in active_positions)
    observing_sides, protection_result = _manage_filled_sniper_protections(
        client, snapshot, instrument, one_minute_frame,
        database=database, strategy_id=strategy_id,
    )
    if protection_result is not None:
        return protection_result
    # The account position list contains contracts opened by every execution
    # branch.  The generic sniper risk rule used to inspect all of them and
    # could therefore close a normal strategy lifecycle on a 1m MA5 touch
    # before its declared 5m MA5 exit manager ran.  An open lifecycle is an
    # explicit ownership claim: leave that side to the lifecycle manager.
    if database is not None and strategy_id:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            lifecycles = connection.execute(
                "SELECT direction FROM trade_lifecycle "
                "WHERE strategy_id=? AND instrument=? AND status='open'",
                (strategy_id, instrument),
            ).fetchall()
            for lifecycle in lifecycles:
                direction = int(lifecycle["direction"] or 0)
                if direction:
                    observing_sides.add("long" if direction > 0 else "short")
        finally:
            connection.close()
    if False and len(active_sides) > 1:
        if owned:
            client.cancel_orders(owned)
        return SniperExecutionResult("blocked", "当前同时存在多空持仓，狙击加仓已禁止")
    # Long/short mode permits two independent structural layers per side.
    # Audit each side separately and reduce only one filled layer when risk
    # deteriorates; a first fill does not cancel the other structural line.
    active_counts = {side: sum(int(abs(float(item.get("pos") or 0)))
                               for item in active_positions
                               if str(item.get("posSide", "")) == side)
                     for side in ("long", "short")}
    for position in active_positions:
        position_side = str(position.get("posSide", ""))
        if position_side in observing_sides:
            continue
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        created_ms = int(position.get("cTime") or position.get("uTime") or now_ms)
        age_minutes = max(0.0, (now_ms - created_ms) / 60_000)
        try:
            average = float(position.get("avgPx") or 0)
            mark = float(position.get("markPx") or position.get("last") or 0)
            directional_return = ((mark - average) / average if position_side == "long"
                                  else (average - mark) / average) if average and mark else 0.0
            position_profitable = directional_return >= .0012
        except (TypeError, ValueError, ZeroDivisionError):
            position_profitable = False
        exit_reason = _active_risk_exit_reason(
            position_side, five_minute_frame, one_minute_frame, age_minutes,
            position_profitable=position_profitable)
        if exit_reason:
            client.close_demo_position_market(
                1, position_side=position_side, enabled=True,
                max_contracts=10, confirmation="DEMO-ORDER", inst_id=instrument)
            return SniperExecutionResult(
                "early_exit", f"layered {position_side} risk exit: {exit_reason}; reduced one contract only")
    active_side = ""
    sibling_cancelled = False
    if active_side and owned:
        # A filled entry owns the account.  Cancel remaining opening orders,
        # but keep this cycle's risk-exit inspection so a fresh fill cannot
        # hide an immediately invalidated market structure.
        client.cancel_orders(owned)
        sibling_cancelled = True
    if active_side:
        position = next(item for item in active_positions
                        if str(item.get("posSide", "")) == active_side)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        created_ms = int(position.get("cTime") or position.get("uTime") or now_ms)
        age_minutes = max(0.0, (now_ms - created_ms) / 60_000)
        try:
            average = float(position.get("avgPx") or 0)
            mark = float(position.get("markPx") or position.get("last") or 0)
            directional_return = ((mark - average) / average if active_side == "long"
                                  else (average - mark) / average) if average and mark else 0.0
            # Require enough gross profit to plausibly cover round-trip fees
            # and market-close slippage; a merely positive UPL is insufficient.
            position_profitable = directional_return >= .0012
        except (TypeError, ValueError, ZeroDivisionError):
            position_profitable = False
        exit_reason = _active_risk_exit_reason(
            active_side, five_minute_frame, one_minute_frame, age_minutes,
            position_profitable=position_profitable)
        if exit_reason:
            contracts = int(abs(float(position.get("pos") or 0)))
            client.close_demo_position_market(
                contracts, position_side=active_side, enabled=True,
                max_contracts=10, confirmation="DEMO-ORDER", inst_id=instrument)
            return SniperExecutionResult(
                "early_exit", f"主动风险退出：{exit_reason}；固定止盈止损仍保留作为服务器保护")
        if sibling_cancelled:
            return SniperExecutionResult(
                "sibling_cancelled", "已有预埋成交，其他开仓挂单已全部撤销；本轮风险检查已完成")
        return SniperExecutionResult(
            "blocked", "当前交易已取得执行权；其他阶段排队等待平仓后重新验证")
    suspended_for = _suspension_remaining(instrument)
    if suspended_for:
        return SniperExecutionResult(
            "suspended",
            f"OKX模拟盘合约暂停交易，{suspended_for}秒后自动复查；无需重新解锁，恢复后自动补挂双侧结构线",
            tuple(str(item.get("ordId", "")) for item in owned),
        )
    atr_15m = _atr(fifteen_minute_frame)
    ma20 = fifteen_minute_frame["close"].rolling(20).mean()
    slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr_15m if atr_15m > 0 else 999.0
    strength = float(adx(fifteen_minute_frame, 14).iloc[-1])
    regime = classify_trend_regime(five_minute_frame, fifteen_minute_frame)
    phase = staged_entry_phase(five_minute_frame, fifteen_minute_frame)
    frames = {"15m": fifteen_minute_frame, "5m": five_minute_frame}
    plan = SniperPlan(False, "没有可用的结构预埋周期", "")
    for timeframe in preferred_timeframes:
        if timeframe not in frames:
            continue
        plan = build_structure_sniper_plan(
            frames[timeframe], one_minute_frame, timeframe=timeframe,
            adx_15m=strength, ma20_slope_atr=slope,
            pivot_selection=pivot_selection,
            target_ma_frame=frames.get(target_ma_timeframe),
            max_levels_per_direction=max_levels_per_direction,
        )
        if plan.allowed:
            break
    if plan.allowed:
        plan = _candidate_waiting_layers(plan, five_minute_frame, one_minute_frame, regime)
    plan = _one_minute_stopping_limit_layer(
        plan, five_minute_frame, one_minute_frame, regime)
    plan = _ma20_breakout_first_pullback_limit_layer(
        plan, five_minute_frame, one_minute_frame)
    plan = _pressure_zone_advance_short_layer(
        plan, fifteen_minute_frame, five_minute_frame, one_minute_frame)
    plan = _apply_final_sniper_gates(
        plan, atr_1m=_atr(one_minute_frame), adx_15m=strength,
        ma20_slope_atr=slope, regime_direction=regime.direction,
    )
    # Apply strategy-specific direction gates before reconciling existing
    # orders.  Otherwise a restore could keep an order that the current
    # strategy-02 range/deviation gates no longer permit.
    if -1 not in allowed_directions:
        plan = replace(plan, short=None, short_levels=())
    if 1 not in allowed_directions:
        plan = replace(plan, long=None, long_levels=())
    if not plan.allowed or (plan.short is None and plan.long is None):
        if owned:
            client.cancel_orders(owned)
            return SniperExecutionResult(
                "invalidated",
                f"当前结构已不再支持原预埋计划，旧单已撤销：{plan.reason}",
                tuple(str(item.get("ordId", "")) for item in owned),
            )
        return SniperExecutionResult("observe", plan.reason)

    current = float(one_minute_frame["close"].iloc[-1])
    short_valid = plan.short is None or current < plan.short.entry
    long_valid = plan.long is None or plan.long.entry < current
    if not (short_valid and long_valid):
        if owned:
            client.cancel_orders(owned)
            return SniperExecutionResult("invalidated", "最新价已离开有效结构区间，旧预埋单已撤销")
        return SniperExecutionResult("observe", "最新价已离开结构高低点内部，预埋计划作废")

    refresh_reason = ""
    orders_to_replace: list[dict] = []
    if owned:
        short_desired = plan.short_levels or ((plan.short,) if plan.short else ())
        long_desired = plan.long_levels or ((plan.long,) if plan.long else ())
        desired_by_side = {
            "short": [float(_rounded_level(level)[0]) for level in short_desired],
            "long": [float(_rounded_level(level)[0]) for level in long_desired],
        }
        reanchor_threshold = max(_atr(one_minute_frame) * .25, current * .0005)
        moved = []
        for item in owned:
            side = str(item.get("posSide", ""))
            try:
                existing_price = float(item.get("px"))
            except (TypeError, ValueError):
                continue
            desired_prices = desired_by_side.get(side, [])
            if desired_prices and min(abs(existing_price - price) for price in desired_prices) >= reanchor_threshold:
                moved.append(item)
                continue
            # Legacy resting entries may still carry an attached last-price
            # stop.  Replace them using the existing place-new-before-cancel-old
            # path so the visible support/pressure line never has a gap.
            attached = item.get("attachAlgoOrds") or []
            if any(str(algo.get("slTriggerPxType", "last")).lower() != "mark"
                   for algo in attached if algo.get("slTriggerPx")):
                moved.append(item)
        desired_count = sum(max(0, len(prices) - active_counts.get(side, 0))
                            for side, prices in desired_by_side.items())
        if moved or len(owned) != desired_count:
            refresh_reason = "结构价已明显移动，先挂新单确认后再撤旧单并动态重锚"
        if refresh_reason:
            # Keep the old visible lines armed until every replacement has an
            # exchange order id.  If placement fails, the exception cleanup
            # removes only the new partial set and the old lines remain.
            orders_to_replace = list(owned)
        else:
            return SniperExecutionResult(
                "armed", f"{plan.timeframe}结构仍有效，狙击单持续等待并每轮动态复核",
                tuple(str(item.get("ordId", "")) for item in owned),
            )

    unrelated_orders = [
        item for item in snapshot.get("orders", [])
        if item not in owned and not str(item.get("clOrdId", "")).startswith(strategy_prefix + "SNP")
    ]
    if unrelated_orders:
        return SniperExecutionResult("blocked", "存在其他普通委托，不新增结构狙击预埋单")
    if total_contracts >= 4:
        return SniperExecutionResult("blocked", "同方向持仓已达到2张狙击上限")
    stamp = datetime.now(timezone.utc).strftime("%m%d%H%M%S")
    placed: list[dict] = []
    short_levels = plan.short_levels or ((plan.short,) if plan.short else ())
    long_levels = plan.long_levels or ((plan.long,) if plan.long else ())
    short_slots = max(0, 2 - active_counts.get("short", 0))
    long_slots = max(0, 2 - active_counts.get("long", 0))
    candidates = tuple(
        [(f"S{index}", level, "sell", "short")
         for index, level in enumerate(
             sorted(short_levels, key=lambda item: item.entry)[active_counts.get("short", 0):active_counts.get("short", 0) + short_slots], 1)] +
        [(f"L{index}", level, "buy", "long")
         for index, level in enumerate(
             sorted(long_levels, key=lambda item: item.entry, reverse=True)[active_counts.get("long", 0):active_counts.get("long", 0) + long_slots], 1)]
    )
    candidates = tuple(item for item in candidates if item[1].direction in allowed_directions)
    if not candidates:
        return SniperExecutionResult("observe", "当前没有满足五分钟均线乖离条件的预埋方向")
    try:
        for label, level, side, pos_side in candidates:
            entry, stop, target = _rounded_level(level)
            client_id = f"{strategy_prefix}SNP{stamp}{label}"
            response = client.place_demo_sniper_limit_order(
                side, 1, entry, stop, None, enabled=True, max_contracts=1,
                confirmation="DEMO-ORDER", inst_id=instrument, position_side=pos_side,
                client_order_id=client_id,
            )
            order = (response.get("data") or [{}])[0]
            order_id = str(order.get("ordId", "")).strip()
            if not order_id:
                raise RuntimeError("OKX Demo replacement order returned no order id; keep old sniper lines")
            placed.append({"instId": instrument, "ordId": order_id,
                           "client_id": client_id, "level": level,
                           "entry": entry, "stop": stop, "target": target})
    except Exception as exc:
        if placed:
            try:
                client.cancel_orders(placed)
            except Exception:
                pass
        if "51022" in str(exc) or "Contract suspended" in str(exc):
            _mark_contract_suspended(instrument)
            return SniperExecutionResult(
                "suspended",
                "OKX模拟盘返回51022：合约暂时停止交易；60秒后自动探测，恢复后立即补挂双侧结构线",
                tuple(str(item.get("ordId", "")) for item in orders_to_replace),
            )
        raise
    if orders_to_replace:
        client.cancel_orders(orders_to_replace)
    if database is not None and strategy_id and strategy_version:
        from .state import StateStore
        store = StateStore(database)
        try:
            signal_time = datetime.now(timezone.utc).isoformat()
            for item in placed:
                level = item["level"]
                store.record_entry_snapshot(
                    snapshot_uid=str(item["client_id"]), strategy_id=strategy_id,
                    strategy_version=strategy_version, order_id=str(item["ordId"]),
                    signal_time=signal_time, direction=int(level.direction),
                    branch="structure_sniper",
                    trigger_reason=(f"{plan.timeframe}已确认结构极值狙击："
                                    f"{'前结构低点预埋做多' if level.direction > 0 else '前结构高点预埋做空'}；"
                                    f"目标为{target_ma_timeframe} MA5/MA10回归，保护位在结构极值外"),
                    context={"timeframe": plan.timeframe, "staged_phase": phase, "adx_15m": strength,
                             "ma20_slope_atr": slope, "structure_time": level.structure_time,
                             "reward_risk": level.reward_risk,
                             "entry": item["entry"], "normal_stop": level.normal_stop,
                             "disaster_stop": item["stop"], "stop": item["stop"],
                             "take_profit": item["target"]},
                    entry_reference=float(item["entry"]), stop_price=float(item["stop"]),
                    take_profit_price=float(item["target"]), status="pending_limit",
                )
                store.record_sniper_protection(
                    client_order_id=str(item["client_id"]), order_id=str(item["ordId"]),
                    strategy_id=strategy_id, strategy_version=strategy_version,
                    instrument=instrument, direction=int(level.direction),
                    entry_price=float(item["entry"]),
                    normal_stop=float(level.normal_stop), disaster_stop=float(item["stop"]),
                )
        finally:
            store.close()
    return SniperExecutionResult(
        "reanchored" if refresh_reason else "placed",
        ((f"{refresh_reason}；" if refresh_reason else "") + f"{phase}｜{plan.timeframe}结构已分层预埋："
         f"{'预埋空' + format(plan.short.entry, '.2f') if plan.short and active_side != 'long' else ''}"
         f"{'、' if not active_side else ''}"
         f"{'预埋多' + format(plan.long.entry, '.2f') if plan.long and active_side != 'short' else ''}，"
         f"标记价回归{target_ma_timeframe} MA5/MA10止盈；同方向最多2张"),
        tuple(item["ordId"] for item in placed),
    )
