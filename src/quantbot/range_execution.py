from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path

import pandas as pd

from .config import AppConfig
from .okx import OkxCredentials, OkxDemoClient
from .range_observer import observe_range_pivot_once
from .range_pivot import (MA_EXPANSION_CHASE_BRANCH, RELATIVE_EXTREME_BRANCH, STRATEGY_ID, STRATEGY_VERSION,
                          TREND_CONTINUATION_BRANCH, confirmed_wick_pivots,
                          five_minute_ma_deviation,
                          five_minute_reversal_zone_allows)
from .data import okx_history_market
from .spike_guard import spike_circuit_breaker
from .entry_risk import latest_atr, structure_profit_runway
from .exit_policy import normalize_fixed_protection, shared_exit_policy
from .entry_risk import tradeable_profit_space
from .waterfall_hold import waterfall_hold_signal, waterfall_trailing_prices
from .structure_sniper import execute_structure_sniper_tick
from .risk_profiles import profile_allows_entry, profile_display
from .state import RangePivotIntent, StateStore
from .shared_signal_center import publish_extreme_signal, recover_extreme_signal
from .hedge_entry import same_side_entry_conflicts
from .execution_guards import ordinary_stop_is_too_close


@dataclass(frozen=True)
class RangeExecutionResult:
    action: str
    reason: str
    order_id: str = ""
    algo_id: str = ""


def _owned_sniper_orders(snapshot: dict) -> list[dict]:
    return [item for item in snapshot.get("orders", [])
            if str(item.get("clOrdId", "")).startswith("QBRSNP")]


def _without_owned_sniper_orders(snapshot: dict) -> dict:
    """Resting structure lines must not hide an independent entry signal."""
    return {**snapshot, "orders": [
        item for item in snapshot.get("orders", [])
        if not str(item.get("clOrdId", "")).startswith("QBRSNP")
    ]}


def rounded_exit_prices(mark_price: float, direction: int, stop_price: float, activation_price: float,
                        callback_spread: float | None = None) -> tuple[str, str, str]:
    tick = Decimal("0.01")
    mark, stop, activation = map(lambda value: Decimal(str(value)), (mark_price, stop_price, activation_price))
    if direction > 0:
        if not stop < mark < activation:
            raise ValueError("long range protection prices are no longer valid")
        stop = stop.quantize(tick, rounding=ROUND_DOWN)
        activation = activation.quantize(tick, rounding=ROUND_UP)
    elif direction < 0:
        if not activation < mark < stop:
            raise ValueError("short range protection prices are no longer valid")
        stop = stop.quantize(tick, rounding=ROUND_UP)
        activation = activation.quantize(tick, rounding=ROUND_DOWN)
    else:
        raise ValueError("range direction must be long or short")
    callback = Decimal(str(callback_spread)) if callback_spread is not None else mark * Decimal("0.0005")
    callback = callback.quantize(tick, rounding=ROUND_UP)
    return str(stop), str(activation), str(callback)


def range_protection_from_actual_entry(mark_price: float, direction: int, structure_stop: float,
                                       minimum_reward_risk: float = 1.5,
                                       *, allow_one_r: bool = False) -> tuple[str, str, str]:
    """Build strategy-02 protection from the actual entry and its structure risk."""
    risk = abs(mark_price - structure_stop)
    if risk <= 0:
        raise ValueError("range structure stop must differ from actual entry")
    multiple = .5 if allow_one_r else max(1.5, minimum_reward_risk)
    activation = mark_price + direction * risk * multiple
    return rounded_exit_prices(mark_price, direction, structure_stop, activation)


def execute_range_pivot_tick(cfg: AppConfig, credentials: OkxCredentials, database: str | Path) -> RangeExecutionResult:
    signal = observe_range_pivot_once(cfg)
    prefix = (f"5分钟反转区间 {signal.pivot_low:.2f}–{signal.pivot_high:.2f}｜"
              f"ATR {signal.atr:.2f}｜ADX {signal.adx:.1f}｜{signal.market_state}")
    client = OkxDemoClient(credentials, timeout=20)
    client.require_swap_trading_mode()
    snapshot = client.safety_snapshot()
    orphaned = client.orphaned_owned_trailing_orders(snapshot, "QBR")
    if orphaned:
        client.cancel_algo_orders(orphaned)
        snapshot = client.safety_snapshot()
    one_minute_market = okx_history_market((cfg.okx.instruments[0],), "1m", 100)
    five_minute_market = okx_history_market((cfg.okx.instruments[0],), "5m", 100)
    zone_ok = False
    allowed_directions: tuple[int, ...] = ()
    sniper = None
    if cfg.strategy.risk_profile == "aggressive":
        fifteen_minute_market = okx_history_market((cfg.okx.instruments[0],), "15m", 100)
        resistance, support = confirmed_wick_pivots(
            five_minute_market, cfg.strategy.pivot_lookback)
        atr_5m = latest_atr(five_minute_market)
        zone_ok, _ = five_minute_reversal_zone_allows(resistance, support, atr_5m)
        far_high, far_low, _, _ = five_minute_ma_deviation(
            five_minute_market, resistance, support, atr_5m)
        allowed_directions = (-1, 1)
        owned_sniper_orders = _owned_sniper_orders(snapshot)
        if allowed_directions:
            sniper = execute_structure_sniper_tick(
                client, snapshot, cfg.okx.instruments[0], "QBR",
                five_minute_market, fifteen_minute_market, one_minute_market,
                database=database, strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION,
                preferred_timeframes=("5m",), allowed_directions=allowed_directions,
                pivot_selection="latest", target_ma_timeframe="5m",
                max_levels_per_direction=2,
            )
            if (sniper.action in {"placed", "reanchored", "armed", "sibling_cancelled"}
                    and signal.direction != 0 and signal.action in {"open_long", "open_short"}):
                snapshot = client.safety_snapshot()
            elif sniper.action in {"placed", "reanchored", "armed", "sibling_cancelled", "early_exit", "suspended"}:
                return RangeExecutionResult(
                    sniper.action, f"{prefix}｜{sniper.reason}",
                    ",".join(sniper.order_ids),
                )
            if sniper.action == "invalidated":
                # The resting limit was missed or became stale.  Refresh the
                # exchange state and allow the still-valid 1m reversal signal
                # below to use the original market-order path.
                snapshot = client.safety_snapshot()
        elif owned_sniper_orders:
            # A previously valid resting order no longer passes the 5m range
            # or MA-deviation gate.  Remove it, then preserve the market-order
            # fallback if the independently evaluated 1m signal is valid.
            client.cancel_orders(owned_sniper_orders)
            snapshot = client.safety_snapshot()
    signal_store = StateStore(database)
    try:
        local_extreme = signal.direction and (
            "扫高新高" in signal.reason or "扫损新低" in signal.reason
        )
        if local_extreme:
            publish_extreme_signal(
                signal_store, instrument=cfg.okx.instruments[0], strategy_id=STRATEGY_ID,
                direction=signal.direction,
                confirmed_bar_time=signal.confirmed_bar_time.isoformat(),
                reason=signal.reason, stop_price=signal.stop_price,
            )
        elif signal.direction == 0 and zone_ok:
            shared = recover_extreme_signal(
                signal_store, instrument=cfg.okx.instruments[0], strategy_id=STRATEGY_ID)
            if shared is not None and int(shared["direction"]) in allowed_directions:
                direction = int(shared["direction"])
                signal = replace(
                    signal, direction=direction,
                    action="open_short" if direction < 0 else "open_long",
                    reason=f"共享信号中心补读｜{shared['reason']}；策略02区间与均线乖离门槛通过",
                    confirmed_bar_time=pd.Timestamp(str(shared["confirmed_bar_time"])),
                    pivot_price=signal.pivot_high if direction < 0 else signal.pivot_low,
                    stop_price=float(shared["stop_price"]),
                    extreme_exhaustion=True,
                )
    finally:
        signal_store.close()
    market_signal_ready = signal.direction != 0 and signal.action in {"open_long", "open_short"}
    blocking_snapshot = _without_owned_sniper_orders(snapshot)
    if client.unprotected_positions(snapshot):
        return RangeExecutionResult("protection_alert", f"{prefix} | unprotected position blocks every new entry")
    if market_signal_ready and same_side_entry_conflicts(
        snapshot, signal.direction, sniper_prefix="QBRSNP"
    ):
        return RangeExecutionResult("manage", f"{prefix} | same-side position/order already exists")
    if any(blocking_snapshot.values()) and not market_signal_ready:
        unprotected = client.unprotected_positions(blocking_snapshot)
        if unprotected:
            return RangeExecutionResult("protection_alert", f"{prefix}｜持仓没有有效保护单，禁止新开仓")
        return RangeExecutionResult("manage", f"{prefix}｜现有持仓或委托由OKX服务器管理")
    if signal.direction == 0 or signal.action not in {"open_long", "open_short"}:
        return RangeExecutionResult("observe", f"{prefix}｜{signal.reason}")
    if not signal.entry_confirmed:
        return RangeExecutionResult("observe", f"{prefix} | 市场单未完成1分钟确认，仅保留结构预埋单")
    volume_stopping_entry = (
        "双周期放量止跌" in signal.reason
        or "5分钟放量止跌" in signal.reason
        or "五分钟放量瀑布" in signal.reason
    )
    entry_kind = "trend_continuation" if signal.branch == TREND_CONTINUATION_BRANCH else (
        "volume_stopping_pullback_long" if volume_stopping_entry else
        "early_high_sweep_reject" if "扫高" in signal.reason else
        "early_low_sweep_reclaim" if "扫损" in signal.reason else "extreme_reversal")
    if not profile_allows_entry(cfg.strategy.risk_profile, entry_kind):
        return RangeExecutionResult("observe", f"{prefix}｜{profile_display(cfg.strategy.risk_profile)}已关闭{entry_kind}，等待更高确定性信号")
    store = StateStore(database)
    try:
        if store.has_open_same_side_trade(STRATEGY_ID, cfg.okx.instruments[0], signal.direction):
            return RangeExecutionResult("manage", f"{prefix} | 生命周期仍有同向普通仓，禁止重复市价叠加")
        now = datetime.now(timezone.utc)
        if store.submitted_range_intent_count(now.date(), STRATEGY_VERSION) >= cfg.risk.max_trades_per_day:
            return RangeExecutionResult("blocked", f"{prefix}｜策略02已达到每日交易上限")
        latest = store.latest_submitted_range_at(STRATEGY_VERSION)
        if latest and (now - latest).total_seconds() < cfg.risk.cooldown_seconds:
            return RangeExecutionResult("blocked", f"{prefix}｜策略02五分钟冷却中")
        ticker_rows = client._request("GET", "/api/v5/market/ticker", {"instId": cfg.okx.instruments[0]}).get("data", [])
        if not ticker_rows:
            return RangeExecutionResult("blocked", f"{prefix}｜无法取得最新价")
        entry_price = float(ticker_rows[0]["last"])
        exhaustion_price_ok = False
        if signal.extreme_exhaustion and signal.direction < 0:
            original_risk = signal.stop_price - signal.breakeven_stop_price
            moved = signal.breakeven_stop_price - entry_price
            exhaustion_price_ok = 0 <= moved <= max(original_risk * .50, 0)
        if signal.trend_continuation and signal.branch != MA_EXPANSION_CHASE_BRANCH:
            exhaustion_price_ok = (
                entry_price >= signal.breakeven_stop_price - signal.atr * .35
                if signal.direction < 0 else
                entry_price <= signal.breakeven_stop_price + signal.atr * .35
            )
            if not exhaustion_price_ok:
                return RangeExecutionResult(
                    "wait_entry_zone",
                    f"{prefix}｜续势确认后价格已离开回抽区超过0.35 ATR，剩余利润空间不足",
                )
        stop, activation, callback = range_protection_from_actual_entry(
            entry_price, signal.direction, signal.stop_price,
            1.0 if signal.extreme_exhaustion else cfg.strategy.minimum_reward_risk,
            allow_one_r=signal.extreme_exhaustion,
        )
        if volume_stopping_entry:
            risk = entry_price - signal.stop_price
            reward = signal.take_profit_price - entry_price
            if risk <= 0 or reward < risk * 1.5:
                return RangeExecutionResult(
                    "wait_entry_zone", f"{prefix}｜五分钟MA20上方目标不足1.5R，放弃入场")
            stop, activation, callback = rounded_exit_prices(
                entry_price, signal.direction, signal.stop_price, signal.take_profit_price,
                max(entry_price * .0005, signal.atr * .25),
            )
        if signal.extreme_exhaustion and not volume_stopping_entry:
            # Every submitted order must have positive reward greater than its
            # initial structural risk.  The former 0.50R activation could put
            # the trailing take-profit closer than the stop-loss (especially
            # on the narrow extreme-reversal entries).
            activation_value = entry_price + signal.direction * abs(entry_price - signal.stop_price) * 1.10
            stop, activation, callback = rounded_exit_prices(
                entry_price, signal.direction, signal.stop_price, activation_value,
                max(entry_price * .0005, signal.atr * .25),
            )
        elif signal.trend_continuation:
            runway_ok, runway_reason, _ = structure_profit_runway(
                five_minute_market, entry_price, signal.direction,
                abs(entry_price - signal.stop_price),
            )
            if not runway_ok:
                return RangeExecutionResult("wait_entry_zone", f"{prefix}｜{runway_reason}")
            activation_value = entry_price + signal.direction * abs(entry_price - signal.stop_price)
            stop, activation, callback = rounded_exit_prices(
                entry_price, signal.direction, signal.stop_price, activation_value,
                max(entry_price * .0005, signal.atr * .25),
            )
        waterfall = waterfall_hold_signal(
            one_minute_market, five_minute_market, fifteen_minute_market, signal.direction,
        ) if signal.trend_continuation else None
        if waterfall and waterfall.active:
            activation_value, callback_value = waterfall_trailing_prices(
                entry_price, signal.direction, float(stop), float(activation), float(callback), waterfall.atr_5m,
            )
            stop, activation, callback = rounded_exit_prices(
                entry_price, signal.direction, float(stop), activation_value, callback_value,
            )
        exit_policy = shared_exit_policy(
            "trend_continuation" if signal.trend_continuation else entry_kind,
            higher_timeframe_trend_confirmed=signal.trend_continuation,
            waterfall_confirmed=bool(waterfall and waterfall.active),
        )
        if exit_policy.mode == "fixed" and not volume_stopping_entry:
            if ordinary_stop_is_too_close(
                entry_price, signal.direction, float(stop), latest_atr(one_minute_market)
            ):
                return RangeExecutionResult("wait_entry_zone", f"{prefix} | 普通结构止损过近，等待二次确认")
            fixed = normalize_fixed_protection(
                entry_price, signal.direction, float(stop), float(activation),
                latest_atr(one_minute_market),
            )
            stop, activation, callback = rounded_exit_prices(
                entry_price, signal.direction, fixed.stop, fixed.take_profit, float(callback),
            )
        space_ok, space_reason, _ = tradeable_profit_space(
            entry_price, signal.direction, float(stop), float(activation),
            latest_atr(one_minute_market),
            minimum_r=1.0 if exit_policy.uses_trailing else 1.5,
        )
        if not space_ok:
            return RangeExecutionResult("wait_entry_zone", f"{prefix}｜{space_reason}")
        side, pos_side, suffix = ("buy", "long", "L") if signal.direction > 0 else ("sell", "short", "S")
        order_prefix = "QBRX" if signal.branch == RELATIVE_EXTREME_BRANCH else "QBRC"
        branch_label = "相对极值反转" if signal.branch == RELATIVE_EXTREME_BRANCH else "趋势延续追单"
        client_id = order_prefix + signal.confirmed_bar_time.strftime("%Y%m%d%H%M") + suffix
        intent = RangePivotIntent(
            cfg.okx.instruments[0], STRATEGY_ID, STRATEGY_VERSION, cfg.strategy.pivot_timeframe,
            signal.confirmed_bar_time.isoformat(), signal.action, signal.direction, signal.pivot_price,
            signal.atr, float(stop), float(activation), signal.config_fingerprint, "pending", signal.branch,
        )
        # Resting support/pressure orders and MA5/MA20 confirmations are
        # parallel sources.  Cancel opening lines only after the independent
        # market signal has passed every safety, price and profit-space gate.
        owned_sniper_orders = _owned_sniper_orders(snapshot)
        if owned_sniper_orders:
            client.cancel_orders(owned_sniper_orders)
            snapshot = client.safety_snapshot()
            unprotected = client.unprotected_positions(snapshot)
            if unprotected:
                return RangeExecutionResult("protection_alert", f"{prefix} | resting lines cancelled but an unprotected position exists")
            if same_side_entry_conflicts(snapshot, signal.direction, sniper_prefix="QBRSNP"):
                return RangeExecutionResult("manage", f"{prefix} | resting lines cancelled but same-side exposure now exists")
        if not store.record_range_pivot_intent(intent):
            return RangeExecutionResult("duplicate", f"{prefix}｜同一确认K线已经处理")
        try:
            # Strategy-02 range/extreme reversals are short-horizon 1m/5m
            # trades.  Use a fixed last-price take-profit so a wick that
            # reaches the target realizes the planned reward instead of only
            # activating a trailing order and then giving most of it back.
            # Keep trailing protection only for the trend-continuation branch.
            use_trailing = False  # v0.6.87: profit exits use the closed-5m MA5 turn rule.
            response = client.place_demo_market_order(
                side, 1, stop, enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
                inst_id=cfg.okx.instruments[0], client_order_id=client_id,
                position_side=pos_side, stop_loss_trigger_type="mark",
                take_profit_price=None,
                take_profit_trigger_type="last",
            )
            order = (response.get("data") or [{}])[0]
            algo = {}
            if use_trailing:
                trailing = client.place_demo_trailing_order(
                    "sell" if signal.direction > 0 else "buy", 1, None, activation,
                    enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
                    inst_id=cfg.okx.instruments[0], position_side=pos_side,
                    client_algo_order_id=client_id + "T", callback_spread=callback,
                )
                algo = (trailing.get("data") or [{}])[0]
            store.update_range_pivot_intent_status(intent, "submitted")
            store.record_event("range_pivot_order_submitted", {
                "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION,
                "branch": signal.branch, "branch_label": branch_label,
                "ordId": order.get("ordId"), "algoId": algo.get("algoId"), "clOrdId": client_id,
                "signal": {
                    **signal.__dict__,
                    "confirmed_bar_time": signal.confirmed_bar_time.isoformat(),
                }, "entry_latest_price": entry_price, "stop_trigger_type": "last", "stop": stop,
                "take_profit_trigger_type": exit_policy.take_profit_trigger_type, "protection_mode": exit_policy.mode,
                "waterfall_hold": bool(waterfall and waterfall.active),
                "waterfall_reason": waterfall.reason if waterfall else "非趋势延续单",
                "trailing_activation": activation, "trailing_callback_spread": callback,
            })
            store.open_trade_lifecycle(
                trade_uid=client_id, strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION,
                instrument=cfg.okx.instruments[0], direction=signal.direction,
                signal_time=signal.confirmed_bar_time.isoformat(), signal_reason=signal.reason,
                signal_context={**signal.__dict__,
                                "confirmed_bar_time": signal.confirmed_bar_time.isoformat(),
                                "branch_label": branch_label},
                order_id=str(order.get("ordId", "")), algo_id=str(algo.get("algoId", "")),
                entry_reference=entry_price, stop_price=float(stop),
                trailing_activation=float(activation), trailing_callback=float(callback), branch=signal.branch,
            )
            return RangeExecutionResult("submitted", f"{prefix}｜策略02【{branch_label}】已提交1张Demo订单：服务器结构止损＋本地5分钟MA5转向止盈", str(order.get("ordId", "")), str(algo.get("algoId", "")))
        except Exception as exc:
            store.update_range_pivot_intent_status(intent, "failed")
            store.record_event("range_pivot_order_failed", {"clOrdId": client_id, "error": str(exc)})
            raise
    finally:
        store.close()
