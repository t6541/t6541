from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path

import pandas as pd

from .ma20_retest import STRATEGY_ID, STRATEGY_VERSION, observe_ma20_retest_once
from .okx import OkxCredentials, OkxDemoClient
from .state import SignalIntent, StateStore
from .data import okx_history_market
from .spike_guard import spike_circuit_breaker
from .entry_risk import latest_atr, structure_profit_runway, structure_protection_plan, tradeable_profit_space
from .exit_policy import normalize_fixed_protection, shared_exit_policy
from .waterfall_hold import waterfall_hold_signal, waterfall_trailing_prices
from .structure_sniper import execute_structure_sniper_tick
from .hedge_entry import same_side_entry_conflicts
from .extreme_entries import low_structure_context
from .risk_profiles import profile_allows_entry, profile_display
from .shared_signal_center import publish_extreme_signal, recover_extreme_signal


@dataclass(frozen=True)
class Ma20ExecutionResult:
    action: str
    reason: str
    order_id: str = ""
    algo_id: str = ""


@dataclass(frozen=True)
class EarlyLowSweepRiskPlan:
    allowed: bool
    reason: str
    stop: float = 0.0
    risk: float = 0.0
    activation: float = 0.0
    callback: float = 0.0


def early_low_sweep_risk_plan(
    entry_price: float, signal_price: float, signal_stop: float,
    structural_support: float, atr_1m: float, atr_5m: float,
) -> EarlyLowSweepRiskPlan:
    """Validate the early long against the original sweep structure, not a later local low."""
    if min(entry_price, signal_price, signal_stop, structural_support, atr_1m, atr_5m) <= 0:
        return EarlyLowSweepRiskPlan(False, "低位扫损结构或ATR数据不足")
    if entry_price - signal_price > atr_1m * .35:
        return EarlyLowSweepRiskPlan(False, "收回确认后已上涨超过0.35 ATR，不再追高")
    buffer = max(atr_1m * .15, entry_price * .0003)
    stop = min(signal_stop, structural_support - buffer)
    risk = entry_price - stop
    if risk <= 0:
        return EarlyLowSweepRiskPlan(False, "真实结构止损无效")
    if risk > atr_1m * 1.5 or risk > atr_5m * .8:
        return EarlyLowSweepRiskPlan(False, "真实扫损低点过远，风险距离超限，放弃本单")
    activation = entry_price + risk * 1.2
    callback = max(entry_price * .0005, risk * .15)
    return EarlyLowSweepRiskPlan(True, "真实扫损结构、风险距离与1.2R条件通过", stop, risk, activation, callback)


def _latest_atr(frame, period: int = 14) -> float:
    ordered = frame.sort_values("date").reset_index(drop=True)
    high, low, close = (ordered[name].astype(float) for name in ("high", "low", "close"))
    previous = close.shift(1)
    ranges = pd.concat(
        (high - low, (high - previous).abs(), (low - previous).abs()), axis=1
    ).max(axis=1)
    return float(ranges.tail(period).mean())


def _reconcile_closed_trades(store: StateStore, client: OkxDemoClient, instrument: str) -> None:
    rows = store.open_trade_lifecycles(STRATEGY_VERSION)
    if not rows:
        return
    fills = client.recent_fills(instrument)
    for row in rows:
        entry_ms = int(datetime.fromisoformat(str(row["signal_time"])).timestamp() * 1000)
        relevant = [item for item in fills if int(item.get("ts") or 0) >= entry_ms]
        entry = [item for item in relevant if str(item.get("clOrdId", "")) == str(row["trade_uid"])]
        closing_side = "sell" if int(row["direction"]) > 0 else "buy"
        pos_side = "long" if int(row["direction"]) > 0 else "short"
        exits = sorted(
            [item for item in relevant if item.get("side") == closing_side and item.get("posSide") == pos_side],
            key=lambda item: int(item.get("ts") or 0),
        )
        if not entry or not exits:
            continue
        entry_size = sum(float(item.get("fillSz") or 0) for item in entry)
        selected, closed_size = [], 0.0
        for item in exits:
            selected.append(item)
            closed_size += float(item.get("fillSz") or 0)
            if closed_size >= entry_size:
                break
        exits = selected
        gross = sum(float(item.get("fillPnl") or 0) for item in exits)
        fees = sum(float(item.get("fee") or 0) for item in entry + exits)
        size = sum(float(item.get("fillSz") or 0) for item in exits)
        close_price = None if not size else sum(
            float(item.get("fillPx") or 0) * float(item.get("fillSz") or 0) for item in exits
        ) / size
        exit_reason = "stop_loss" if gross <= 0 else "trailing_take_profit"
        store.close_trade_lifecycle(str(row["trade_uid"]), close_price=close_price, gross_pnl=gross,
                                    total_fees=fees, exit_reason=exit_reason)
        store.record_event("ma20_retest_trade_closed", {
            "trade_uid": row["trade_uid"], "exit_reason": exit_reason,
            "close_price": close_price, "gross_pnl": gross, "fees": fees, "net_pnl": gross + fees,
        })


def ma20_entry_zone(retest_low: float, retest_high: float, direction: int) -> tuple[float, float]:
    width = max(0.01, retest_high - retest_low)
    if direction > 0:
        return retest_low * 0.999, retest_low + width * 0.35
    if direction < 0:
        return retest_low + width * 0.65, retest_high * 1.001
    raise ValueError("direction must be long or short")


def ma20_exit_prices(mark_price: float, direction: int, retest_low: float | None = None,
                     retest_high: float | None = None) -> tuple[str, str, str]:
    """Use the 5m retest structure for SL, 1R activation and 0.10% callback."""
    mark = Decimal(str(mark_price))
    tick = Decimal("0.01")
    structured = retest_low is not None and retest_high is not None and retest_high > retest_low
    width = Decimal(str((retest_high or 0) - (retest_low or 0)))
    buffer = max(mark * Decimal("0.001"), width * Decimal("0.25")) if structured else mark * Decimal("0.0015")
    if direction > 0:
        raw_stop = Decimal(str(retest_low)) - buffer if structured else mark - buffer
        stop = raw_stop.quantize(tick, rounding=ROUND_DOWN)
        risk = mark - stop
        activation = (mark + max(risk, mark * Decimal("0.002"))).quantize(tick, rounding=ROUND_UP)
    elif direction < 0:
        raw_stop = Decimal(str(retest_high)) + buffer if structured else mark + buffer
        stop = raw_stop.quantize(tick, rounding=ROUND_UP)
        risk = stop - mark
        activation = (mark - max(risk, mark * Decimal("0.002"))).quantize(tick, rounding=ROUND_DOWN)
    else:
        raise ValueError("direction must be long or short")
    callback = (mark * Decimal("0.0010")).quantize(tick, rounding=ROUND_UP)
    return str(stop), str(activation), str(callback)


def execute_ma20_retest_tick(
    credentials: OkxCredentials,
    database: str | Path,
    instrument: str = "ETH-USDT-SWAP",
    risk_profile: str = "aggressive",
) -> Ma20ExecutionResult:
    signal = observe_ma20_retest_once(instrument)
    prefix = (
        f"5m状态 {signal.five_minute_state}｜5m MA20 {signal.ma20_5m:.2f}｜"
        f"5m回踩区 {signal.retest_low:.2f}-{signal.retest_high:.2f}"
    )
    client = OkxDemoClient(credentials, timeout=20)
    client.require_swap_trading_mode()
    snapshot = client.safety_snapshot()
    orphaned = client.orphaned_owned_trailing_orders(snapshot, "QBM")
    if orphaned:
        client.cancel_algo_orders(orphaned)
        snapshot = client.safety_snapshot()
    one_minute_market = okx_history_market((instrument,), "1m", 100)
    five_minute_market = okx_history_market((instrument,), "5m", 100)
    signal_store = StateStore(database)
    try:
        if signal.five_minute_state in {"early_high_sweep_reject", "early_low_sweep_reclaim"}:
            publish_extreme_signal(
                signal_store, instrument=instrument, strategy_id=STRATEGY_ID,
                direction=signal.direction, confirmed_bar_time=signal.cross_time.isoformat(),
                reason=signal.reason,
                stop_price=signal.retest_high if signal.direction < 0 else signal.retest_low,
            )
        elif signal.action not in {"open_long", "open_short"}:
            shared = recover_extreme_signal(signal_store, instrument=instrument, strategy_id=STRATEGY_ID)
            if shared is not None:
                direction = int(shared["direction"])
                trigger = float(one_minute_market.sort_values("date").iloc[-1]["close"])
                stop = float(shared["stop_price"])
                signal = replace(
                    signal, action="open_short" if direction < 0 else "open_long",
                    direction=direction, reason=f"共享信号中心补读｜{shared['reason']}",
                    five_minute_state=str(shared["signal_type"]),
                    cross_time=pd.Timestamp(str(shared["confirmed_bar_time"])),
                    retest_low=trigger if direction < 0 else stop,
                    retest_high=stop if direction < 0 else trigger,
                    trigger_price=trigger,
                )
    finally:
        signal_store.close()
    prefix = (
        f"5m状态 {signal.five_minute_state}｜5m MA20 {signal.ma20_5m:.2f}｜"
        f"5m回踩区 {signal.retest_low:.2f}-{signal.retest_high:.2f}"
    )
    if risk_profile == "aggressive":
        fifteen_minute_market = okx_history_market((instrument,), "15m", 100)
        sniper = execute_structure_sniper_tick(
            client, snapshot, instrument, "QBM",
            five_minute_market, fifteen_minute_market, one_minute_market,
            database=database, strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION,
            preferred_timeframes=("5m",), pivot_selection="latest",
            target_ma_timeframe="5m", max_levels_per_direction=2,
        )
        if (sniper.action in {"placed", "reanchored", "armed", "invalidated", "sibling_cancelled"}
                and signal.action in {"open_long", "open_short"} and signal.direction):
            snapshot = client.safety_snapshot()
        elif sniper.action in {"placed", "reanchored", "armed", "invalidated", "sibling_cancelled", "early_exit", "suspended"}:
            return Ma20ExecutionResult(
                sniper.action, f"{prefix}｜{sniper.reason}",
                ",".join(sniper.order_ids),
            )
    directional_signal = signal.action in {"open_long", "open_short"} and bool(signal.direction)
    if directional_signal and client.unprotected_positions(snapshot):
        return Ma20ExecutionResult("protection_alert", f"{prefix} | unprotected position blocks every new entry")
    if directional_signal and same_side_entry_conflicts(snapshot, signal.direction, sniper_prefix="QBMSNP"):
        return Ma20ExecutionResult("manage", f"{prefix} | same-side position/order already exists")
    if any(snapshot.values()) and not directional_signal:
        if client.unprotected_positions(snapshot):
            return Ma20ExecutionResult("protection_alert", f"{prefix}｜持仓缺少有效保护单，禁止新开仓")
        return Ma20ExecutionResult("manage", f"{prefix}｜已有持仓或委托，由OKX服务器端保护单管理")
    if signal.action not in {"open_long", "open_short"} or not signal.direction or signal.cross_time is None:
        return Ma20ExecutionResult("observe", f"{prefix}｜{signal.reason}")
    if not profile_allows_entry(risk_profile, signal.five_minute_state):
        return Ma20ExecutionResult("observe", f"{prefix}｜{profile_display(risk_profile)}已关闭{signal.five_minute_state}，等待更高确定性信号")

    store = StateStore(database)
    try:
        _reconcile_closed_trades(store, client, instrument)
        now = datetime.now(timezone.utc)
        if store.submitted_intent_count(now.date(), STRATEGY_VERSION) >= 200:
            return Ma20ExecutionResult("blocked", f"{prefix}｜策略03已达到每日200次模拟测试上限")
        rows = client._request("GET", "/api/v5/market/ticker", {"instId": instrument}).get("data", [])
        if not rows:
            raise RuntimeError("无法取得OKX标记价格")
        latest_price = float(rows[0]["last"])
        early_risk_plan = None
        if signal.five_minute_state == "early_low_sweep_reclaim":
            structural_support, atr_5m = low_structure_context(five_minute_market)
            early_risk_plan = early_low_sweep_risk_plan(
                latest_price, signal.trigger_price, signal.retest_low,
                structural_support, _latest_atr(one_minute_market), atr_5m,
            )
            if not early_risk_plan.allowed:
                return Ma20ExecutionResult("wait_entry_zone", f"{prefix}｜{early_risk_plan.reason}")
            runway_ok, runway_reason, _ = structure_profit_runway(
                five_minute_market, latest_price, 1, early_risk_plan.risk,
                minimum_r=1.5, minimum_atr=.8, nearest_boundary=True,
            )
            if not runway_ok:
                return Ma20ExecutionResult("wait_entry_zone", f"{prefix}｜{runway_reason}")
        if signal.five_minute_state in {"downtrend_continuation_short", "uptrend_continuation_long",
                                         "downtrend_ma5_pullback_short", "uptrend_ma5_pullback_long"} and signal.atr_5m > 0:
            moved = signal.trigger_price - latest_price
            if moved > signal.atr_5m * .35:
                return Ma20ExecutionResult(
                    "wait_entry_zone", f"{prefix}｜追空确认后价格又下跌超过0.35 ATR，剩余利润空间不足"
                )
        zone_low, zone_high = ma20_entry_zone(signal.retest_low, signal.retest_high, signal.direction)
        precise_breakout_entry = signal.five_minute_state in {
            "bullish_breakout_pullback", "terminal_acceleration_reversal", "reversal_candidate_entry",
            "direct_rollover_first_ma5_short", "direct_rollover_first_ma5_long",
            "direct_rollover_first_ma20_short", "direct_rollover_first_ma20_long",
            "early_low_sweep_reclaim", "early_high_sweep_reject",
            "downtrend_continuation_short", "uptrend_continuation_long",
            "downtrend_ma5_pullback_short", "uptrend_ma5_pullback_long",
            "volume_stopping_pullback_long"}
        if not precise_breakout_entry and not zone_low <= latest_price <= zone_high:
            edge = "下沿" if signal.direction > 0 else "上沿"
            return Ma20ExecutionResult(
                "wait_entry_zone", f"{prefix}｜回踩已确认，等待价格进入{edge}优选区 {zone_low:.2f}-{zone_high:.2f}"
            )
        if signal.five_minute_state not in {"terminal_acceleration_reversal", "reversal_candidate_entry",
                                             "direct_rollover_first_ma5_short", "direct_rollover_first_ma5_long",
                                             "direct_rollover_first_ma20_short", "direct_rollover_first_ma20_long",
                                             "early_low_sweep_reclaim", "early_high_sweep_reject"}:
            structure_stop = signal.retest_low if signal.direction > 0 else signal.retest_high
            runway_ok, runway_reason, _ = structure_profit_runway(
                five_minute_market, latest_price, signal.direction,
                abs(latest_price - structure_stop),
            )
            if not runway_ok:
                return Ma20ExecutionResult("wait_entry_zone", f"{prefix}｜{runway_reason}")
        side, pos_side, suffix = ("buy", "long", "L") if signal.direction > 0 else ("sell", "short", "S")
        signal_time = signal.cross_time.isoformat()
        client_id = "QBM3" + signal.cross_time.strftime("%Y%m%d%H%M") + suffix
        intent = SignalIntent(instrument, signal_time, signal.direction, STRATEGY_VERSION, 0.0015, "pending")
        if not store.record_intent(intent):
            return Ma20ExecutionResult("duplicate", f"{prefix}｜这一段5分钟趋势翻转已经处理，不重复追单")
        try:
            if signal.five_minute_state in {"terminal_acceleration_reversal", "reversal_candidate_entry",
                                            "direct_rollover_first_ma5_short", "direct_rollover_first_ma5_long",
                                            "direct_rollover_first_ma20_short", "direct_rollover_first_ma20_long",
                                            "early_low_sweep_reclaim", "early_high_sweep_reject",
                                            "downtrend_continuation_short", "uptrend_continuation_long",
                                            "downtrend_ma5_pullback_short", "uptrend_ma5_pullback_long",
                                            "volume_stopping_pullback_long"}:
                raw_stop = (early_risk_plan.stop if early_risk_plan is not None else
                            (signal.retest_high if signal.direction < 0 else signal.retest_low))
                protection = structure_protection_plan(
                    latest_price, signal.direction, raw_stop,
                    latest_atr(one_minute_market), latest_atr(five_minute_market),
                )
                if not protection.allowed:
                    return Ma20ExecutionResult("wait_entry_zone", f"{prefix}｜{protection.reason}")
                risk = protection.risk
                activation_value = protection.activation
                if signal.five_minute_state == "volume_stopping_pullback_long":
                    activation_value = signal.retest_high
                    if activation_value - latest_price < risk * 1.5:
                        return Ma20ExecutionResult(
                            "wait_entry_zone", f"{prefix}｜五分钟MA20上方目标不足1.5R，放弃入场")
                tick = Decimal("0.01")
                stop = str(Decimal(str(raw_stop)).quantize(
                    tick, rounding=ROUND_UP if signal.direction < 0 else ROUND_DOWN))
                activation = str(Decimal(str(activation_value)).quantize(
                    tick, rounding=ROUND_DOWN if signal.direction < 0 else ROUND_UP))
                callback = str(Decimal(str(protection.callback)).quantize(
                    tick, rounding=ROUND_UP))
            else:
                stop, activation, callback = ma20_exit_prices(
                    latest_price, signal.direction, signal.retest_low, signal.retest_high
                )
            continuation_states = {"downtrend_continuation_short", "uptrend_continuation_long",
                                   "downtrend_ma5_pullback_short", "uptrend_ma5_pullback_long"}
            waterfall = waterfall_hold_signal(
                one_minute_market, five_minute_market, fifteen_minute_market, signal.direction,
            ) if signal.five_minute_state in continuation_states else None
            if waterfall and waterfall.active:
                activation_value, callback_value = waterfall_trailing_prices(
                    latest_price, signal.direction, float(stop), float(activation), float(callback), waterfall.atr_5m,
                )
                tick = Decimal("0.01")
                activation = str(Decimal(str(activation_value)).quantize(
                    tick, rounding=ROUND_UP if signal.direction > 0 else ROUND_DOWN))
                callback = str(Decimal(str(callback_value)).quantize(tick, rounding=ROUND_UP))
            exit_policy = shared_exit_policy(
                signal.five_minute_state,
                higher_timeframe_trend_confirmed=signal.five_minute_state in continuation_states,
                waterfall_confirmed=bool(waterfall and waterfall.active),
            )
            if exit_policy.mode == "fixed" and signal.five_minute_state != "volume_stopping_pullback_long":
                fixed = normalize_fixed_protection(
                    latest_price, signal.direction, float(stop), float(activation),
                    latest_atr(one_minute_market),
                )
                tick = Decimal("0.01")
                stop = str(Decimal(str(fixed.stop)).quantize(
                    tick, rounding=ROUND_DOWN if signal.direction > 0 else ROUND_UP))
                activation = str(Decimal(str(fixed.take_profit)).quantize(
                    tick, rounding=ROUND_UP if signal.direction > 0 else ROUND_DOWN))
            space_ok, space_reason, _ = tradeable_profit_space(
                latest_price, signal.direction, float(stop), float(activation),
                latest_atr(one_minute_market),
                minimum_r=1.0 if exit_policy.uses_trailing else 1.5,
            )
            if not space_ok:
                return Ma20ExecutionResult("wait_entry_zone", f"{prefix}｜{space_reason}")
            response = client.place_demo_market_order(
                side, 1, stop, enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
                inst_id=instrument, client_order_id=client_id, position_side=pos_side,
                stop_loss_trigger_type="mark",
                take_profit_price=None,
                take_profit_trigger_type=exit_policy.take_profit_trigger_type,
            )
            order = (response.get("data") or [{}])[0]
            algo = {}
            if exit_policy.uses_trailing:
                trailing = client.place_demo_trailing_order(
                    "sell" if signal.direction > 0 else "buy", 1, None, activation,
                    enabled=True, max_contracts=1, confirmation="DEMO-ORDER", inst_id=instrument,
                    position_side=pos_side, client_algo_order_id=client_id + "T", callback_spread=callback,
                )
                algo = (trailing.get("data") or [{}])[0]
            store.update_intent_status(intent, "submitted")
            store.record_event("ma20_retest_order_submitted", {
                "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION,
                "ordId": order.get("ordId"), "algoId": algo.get("algoId"), "clOrdId": client_id,
                "direction": signal.direction, "signal_time": signal_time, "reason": signal.reason,
                "entry_latest_price": latest_price, "stop_trigger_type": "last",
                "stop": stop, "trailing_activation": activation,
                "trailing_callback_spread": callback, "retest_low": signal.retest_low,
                "retest_high": signal.retest_high,
                "protection_mode": exit_policy.mode,
                "waterfall_hold": bool(waterfall and waterfall.active),
                "waterfall_reason": waterfall.reason if waterfall else "非趋势延续单",
                "take_profit_trigger_type": exit_policy.take_profit_trigger_type,
            })
            store.open_trade_lifecycle(
                trade_uid=client_id, strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION,
                instrument=instrument, direction=signal.direction, signal_time=signal_time,
                signal_reason=signal.reason,
                signal_context={"five_minute_state": signal.five_minute_state,
                                "retest_low": signal.retest_low, "retest_high": signal.retest_high,
                                "trigger_price": signal.trigger_price},
                order_id=str(order.get("ordId", "")), algo_id=str(algo.get("algoId", "")),
                entry_reference=latest_price, stop_price=float(stop), trailing_activation=float(activation),
                trailing_callback=float(callback),
            )
            return Ma20ExecutionResult(
                "submitted", f"{prefix}｜策略03已提交1张Demo订单：服务器结构止损＋本地5分钟MA5转向止盈",
                str(order.get("ordId", "")), str(algo.get("algoId", "")),
            )
        except Exception as exc:
            store.update_intent_status(intent, "failed")
            store.record_event("ma20_retest_order_failed", {"clOrdId": client_id, "error": str(exc)})
            raise
    finally:
        store.close()
