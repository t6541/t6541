"""One reconciled execution tick for the account-05 hedge/grid strategy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from itertools import combinations
import time
from datetime import datetime, timezone
import pandas as pd

from .account05_live import Account05LiveClient
from .manual_order_review import load_manual_order_history, manual_order_inventory
from .account05_entry_review import entry_context
from .base_pullback import base_pullback_confirmation
from .account05_signals import Account05Signals, ExtremeRotationTrigger, evaluate_account05_signals
from .extreme_top_candidate import TopParameters, top_entries
from .account05_state import Account05StateStore
from .account05_strategy import (
    Account05Config, ContractSpec, PositionSide, Trend15m,
    addon_entry_plan, recovery_slot_entry_plan, addon_permission, contracts_for_notional,
    extreme_rotation_entry_plan, take_profit_price, take_profit_price_by_points, addon_profit_points,
    addon_take_profit_threshold, addon_take_profit_unlocked,
)
from .live_account_settings import LiveAccountSettings
from .okx import OkxError


def manual_exit_owners(ledger: Account05StateStore) -> dict[str, str]:
    """Use durable per-lot exits before any inferred FIFO history pairing."""
    links = {}
    for row in ledger.connection.execute(
            """SELECT l.entry_order_id,l.signal_id,l.take_profit_algo_id,i.exchange_order_id
            FROM account05_lots l LEFT JOIN account05_order_intents i
            ON i.signal_id='exit:' || l.lot_id"""):
        for oid in (row["take_profit_algo_id"], row["exchange_order_id"]):
            if oid:
                links[str(oid)] = (str(row["entry_order_id"]) if str(row["signal_id"]).startswith("manual:")
                                  else str(row["entry_order_id"]).rsplit("-", 1)[0])
    for row in ledger.connection.execute(
            "SELECT f.order_id,l.entry_order_id,l.signal_id FROM account05_extreme_exit_fills f "
            "JOIN account05_lots l ON f.lot_id=l.lot_id"):
        links[str(row["order_id"])] = (str(row["entry_order_id"]) if str(row["signal_id"]).startswith("manual:")
                                      else str(row["entry_order_id"]).rsplit("-", 1)[0])
    return links


def import_manual_orders(client: Account05LiveClient, ledger: Account05StateStore,
                         *, inst_id: str | None = None,
                         profit_points: Decimal = Decimal("10"),
                         require_fresh: bool = False) -> int:
    """Rebuild manual virtual quantities from cached, quantity-aware history.

    Never reapply an old close to a newer entry. Preserve actual managed exit
    intents, and check aggregate position coverage before repairing old rows.
    No exchange write is performed here.
    """
    inst_id = inst_id or ACCOUNT05_INSTRUMENT
    if (not require_fresh and time.monotonic() - getattr(client, "_manual_synced_at", -60) < 30):
        return 0
    cached = list(ledger.connection.execute(
        "SELECT payload_json FROM account05_manual_order_history"))
    if require_fresh or not cached:
        if hasattr(client, "order_history_page") and hasattr(client, "fills_history"):
            orders, fills, warning = load_manual_order_history(client, inst_id, max_pages=100)
            if warning:
                raise OkxError(f"手工订单历史不完整，禁止据此接管或极值反手：{warning}")
        else:
            orders, fills = client.orders_history(inst_id, limit=100), []
    else:
        orders, fills = client.orders_history(inst_id, limit=100), []
    records = {}
    for item in cached:
        payload = json.loads(item["payload_json"])
        records[payload["key"]] = payload
    fields = ("ordId", "clOrdId", "side", "posSide", "state", "ordType", "accFillSz",
              "avgPx", "fillPx", "fillSz", "fillTime", "cTime", "uTime", "reduceOnly",
              "execType", "ts", "billId", "tradeId", "instId")
    for kind, items in (("order", orders), ("fill", fills)):
        for item in items:
            oid = str(item.get("ordId") or "")
            if not oid:
                continue
            filtered = {k: item[k] for k in fields if k in item}
            suffix = str(item.get("tradeId") or item.get("billId") or
                         hashlib.sha256(json.dumps(filtered, sort_keys=True).encode()).hexdigest())
            key = f"{kind}:{oid}" + (f":{suffix}" if kind == "fill" else "")
            if key in records and kind == "order":
                previous = records[key]["row"]
                old_quantity = Decimal(str(previous.get("accFillSz") or "0"))
                new_quantity = Decimal(str(filtered.get("accFillSz") or "0"))
                # Recent/archive sources may overlap or arrive out of order.
                # Never regress cumulative fills to an older snapshot.
                old_time = int(previous.get("uTime") or previous.get("cTime") or "0")
                new_time = int(filtered.get("uTime") or filtered.get("cTime") or "0")
                if new_quantity < old_quantity or (new_quantity == old_quantity and new_time < old_time):
                    filtered = {**filtered, **previous}
                else:
                    filtered = {**previous, **filtered}
            records[key] = dict(key=key, kind=kind, row=filtered)
    all_orders = [r["row"] for r in records.values() if r["kind"] == "order"]
    all_fills = [r["row"] for r in records.values() if r["kind"] == "fill"]
    entries, _ = manual_order_inventory(all_orders, all_fills, exit_owners=manual_exit_owners(ledger))
    plans = {}
    for oid, entry in entries.items():
        if not entry["manual"]:
            continue
        signal = f"manual:{oid}"
        # A submitted/unknown/filled strategy exit owns this lot's quantity.
        # Never reopen it from missing or lagging exchange history.
        if ledger.order_intent(f"exit:{signal}") is not None:
            continue
        plans[signal] = entry
    positions = _position_map(client.raw_snapshot())
    projected = {side: Decimal(0) for side in (PositionSide.LONG, PositionSide.SHORT)}
    for lot in ledger.open_lots():
        if lot.lot_id not in plans:
            projected[lot.side] += lot.remaining_size
    for entry in plans.values():
        side = PositionSide(entry["logical"])
        projected[side] += entry["qty"] - entry["closed"]
    for side, quantity in projected.items():
        actual = abs(Decimal(str(positions.get(side, {}).get("pos") or "0")))
        if actual and quantity > actual:
            raise OkxError(f"手工历史/批次数量{quantity}超过{side.value}实际仓位{actual}；先对账，不反手")
    imported = 0
    for signal, entry in plans.items():
        side = PositionSide(entry["logical"])
        actual = abs(Decimal(str(positions.get(side, {}).get("pos") or "0")))
        remaining = entry["qty"] - entry["closed"] if actual else Decimal(0)
        price = entry["cost"] / entry["qty"]
        created = datetime.fromtimestamp(entry["ts"] / 1000, timezone.utc).isoformat()
        closed_at = (datetime.fromtimestamp(entry["close_ts"] / 1000, timezone.utc).isoformat()
                     if entry["close_ts"] and not remaining else "")
        existing = ledger.connection.execute(
            "SELECT 1 FROM account05_lots WHERE lot_id=?", (signal,)).fetchone()
        if existing is None:
            ledger.record_filled_lot(lot_id=signal, signal_id=signal, side=side, kind="addon",
                entry_order_id=str(entry["row"]["ordId"]), entry_price=price, size=entry["qty"],
                take_profit_price=take_profit_price_by_points(price, side, max(profit_points, Decimal("10")), Decimal("0.01")),
                take_profit_pct=Decimal(0))
            imported += 1
        # Recompute from cumulative quantities, not incremental polling. This
        # also repairs the old importer that marked an entire lot closed.
        ledger.connection.execute(
            """UPDATE account05_lots SET original_size=?,remaining_size=?,entry_price=?,
            status=?,created_at_utc=?,closed_at_utc=?,exit_order_id=?,exit_price=?,
            local_gross_pnl='',local_fee_estimate='',local_net_pnl='',take_profit_algo_id=NULL,
            updated_at_utc=? WHERE lot_id=?""",
            (str(entry["qty"]), str(remaining), str(price),
             "tp_live" if remaining else "closed", created, closed_at,
             "、".join(entry["ids"]), str(entry["close_cost"] / entry["closed"]) if entry["closed"] else "",
             datetime.now(timezone.utc).isoformat(), signal))
    with ledger.connection:
        ledger.connection.executemany(
            "INSERT OR REPLACE INTO account05_manual_order_history VALUES (?,?)",
            [(key, json.dumps(payload, ensure_ascii=False)) for key, payload in records.items()])
    client._manual_synced_at = time.monotonic()
    return imported


ACCOUNT05_INSTRUMENT = "ETH-USDT-SWAP"


@dataclass(frozen=True)
class Account05TickResult:
    action: str
    reason: str
    order_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AddonCloseResult:
    order_id: str
    exit_price: Decimal
    exchange_realized_pnl: Decimal | None = None
    exchange_fee: Decimal | None = None


def _order_does_not_exist(exc: BaseException) -> bool:
    text = str(exc).lower()
    return ("51603" in text and "order does not exist" in text) or "未返回订单" in text


def _reduce_side_already_flat(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "51169" in text and "don't have any positions" in text


def _client_id(prefix: str, identity: str) -> str:
    return prefix + hashlib.sha256(identity.encode()).hexdigest()[:24].upper()


def _position_map(snapshot: dict) -> dict[PositionSide, dict]:
    result = {}
    for row in snapshot.get("positions", []):
        side = str(row.get("posSide") or "")
        if side in {"long", "short"} and abs(Decimal(str(row.get("pos") or "0"))) > 0:
            result[PositionSide(side)] = row
    return result


def _wait_for_fill(client: Account05LiveClient, order_id: str) -> dict:
    latest = {}
    for _ in range(12):
        latest = client.order(ACCOUNT05_INSTRUMENT, order_id=order_id)
        if str(latest.get("state")) == "filled":
            return latest
        if str(latest.get("state")) in {"canceled", "mmp_canceled"}:
            raise OkxError(f"账户05开仓订单未成交：state={latest.get('state')}")
        time.sleep(.25)
    raise OkxError("账户05市价单3秒内未确认完全成交；自动执行停止并等待人工对账")


def _split_size(size: Decimal, step: Decimal) -> tuple[Decimal, Decimal]:
    first = ((size / Decimal("2")) / step).to_integral_value(rounding=ROUND_DOWN) * step
    second = size - first
    if first < step:
        return Decimal("0"), size
    return first, second


def _protect_fill(client: Account05LiveClient, ledger: Account05StateStore, *,
                  signal_id: str, side: PositionSide, kind: str, fill: dict,
                  spec: ContractSpec, config: Account05Config,
                  base_take_profit_pct: Decimal | None = None) -> tuple[str, ...]:
    entry_order_id = str(fill.get("ordId") or "")
    entry_price = Decimal(str(fill.get("avgPx") or fill.get("fillPx") or "0"))
    size = Decimal(str(fill.get("accFillSz") or fill.get("fillSz") or "0"))
    if not entry_order_id or entry_price <= 0 or size <= 0:
        raise OkxError("账户05成交回执缺少ordId、avgPx或accFillSz；停止并对账")
    if kind == "base" and base_take_profit_pct not in config.base_take_profit_pcts:
        raise OkxError("账户05基础仓缺少有效的单一止盈比例")
    targets = ((base_take_profit_pct,) if kind == "base" else (None,))
    sizes = (size,)
    placed: list[str] = []
    for index, lot_size in enumerate(sizes):
        if lot_size <= 0:
            continue
        tp = (take_profit_price(entry_price, side, targets[index], Decimal("0.01"))
              if kind == "base" else entry_price)
        lot_id = f"{signal_id}-T{index + 1}"
        lot = ledger.record_filled_lot(
            lot_id=lot_id, signal_id=lot_id, side=side, kind=kind,
            entry_order_id=f"{entry_order_id}-{index + 1}", entry_price=entry_price,
            size=lot_size, take_profit_price=tp,
            take_profit_pct=(base_take_profit_pct if kind == "base" else Decimal("0")))
        if kind == "addon":
            ledger.mark_addon_ma5_managed(lot_id)
            continue
        if lot.take_profit_algo_id:
            placed.append(lot.take_profit_algo_id)
            continue
        tp_client_id = _client_id("A5TP", lot_id)
        order = client.place_lot_take_profit(
            inst_id=ACCOUNT05_INSTRUMENT, position_side=side.value,
            size=lot_size, price=tp, client_order_id=tp_client_id)
        order_id = str(order["ordId"])
        ledger.attach_take_profit(lot_id, order_id)
        placed.append(order_id)
    return tuple(placed)


def _addon_ma5_exit_reason(lot, one, five,
                           minimum_profit_points: Decimal = Decimal("10")) -> str:
    """Return an MA5 profit exit only after the local lot exceeds ten points."""
    direction = 1 if lot.side is PositionSide.LONG else -1
    required_profit_points = addon_take_profit_threshold(minimum_profit_points)
    one = one.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    five = five.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if len(one) < 9 or len(five) < 9:
        return ""
    latest = Decimal(str(one.iloc[-1]["close"]))
    lot_profit_points = addon_profit_points(lot.entry_price, latest, lot.side)
    if not addon_take_profit_unlocked(
            lot.entry_price, latest, lot.side, required_profit_points):
        return ""

    # Fast terminal: lock an already profitable small lot before MA5 bends
    # when closes have expanded unusually far in the favorable direction.
    close = one["close"].astype(float)
    open_ = one["open"].astype(float)
    ma5 = close.rolling(5).mean()
    previous = close.shift(1)
    body_tr = pd.concat([
        (open_ - close).abs(), (open_ - previous).abs(), (close - previous).abs(),
    ], axis=1).max(axis=1)
    atr_body = float(body_tr.tail(14).mean())
    latest_close = float(close.iloc[-1])
    latest_body = direction * (latest_close - float(open_.iloc[-1]))
    latest_range = float(one.iloc[-1]["high"]) - float(one.iloc[-1]["low"])
    prior_ranges = (one["high"].astype(float) - one["low"].astype(float)).shift(1)
    prior_range_atr = float(prior_ranges.tail(14).mean())
    three_bar_run = direction * (latest_close - float(close.iloc[-4]))
    distance = direction * (latest_close - float(ma5.iloc[-1]))
    # A strong price extension is not by itself an exit.  A fast terminal exit
    # may happen before MA5 turns only when the current candle is itself a
    # meaningful expansion: its full range and favorable body must both clear
    # recent volatility.  This prevents the old short, quiet candles from
    # closing profitable addons while still catching a genuine terminal bar.
    ma5_slopes = ma5.diff()
    ma5_turning_against = (
        pd.notna(ma5_slopes.iloc[-1])
        and direction * float(ma5_slopes.iloc[-1]) <= 0
    )
    expansion_bar = (
        atr_body > 0 and prior_range_atr > 0
        and latest_range >= prior_range_atr
        and (latest_body >= max(atr_body * 1.20, prior_range_atr * 1.15)
             or three_bar_run >= atr_body * 1.80)
    )
    if (atr_body > 0
            and addon_take_profit_unlocked(
                lot.entry_price, Decimal(str(latest_close)), lot.side,
                required_profit_points)
            and direction * (latest_close - float(lot.entry_price)) > atr_body
            and distance >= atr_body * .70
            and (ma5_turning_against or expansion_bar)
            and (latest_body >= atr_body * 1.20 or three_bar_run >= atr_body * 1.80)):
        if expansion_bar and not ma5_turning_against:
            return "1分钟快速单边末端出现合格扩张K线：实体和全幅达到波动门槛，提前止盈（不等MA5拐弯）"
        return "1分钟快速单边末端且MA5已走平/拐头：收盘价过度远离MA5，允许提前止盈（不等MA5再次拐弯）"

    def closed_candles(frame, minutes):
        # Recent OKX snapshots include confirm=0 candles. Their timestamps
        # are candle starts in UTC, so a bar is eligible only after its end.
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=minutes)
        dates = pd.to_datetime(frame["date"], utc=True)
        return frame.loc[dates <= cutoff].reset_index(drop=True)

    def turn(frame, required_bars=1):
        if len(frame) < 9 + required_bars:
            return False, False
        close = frame["close"].astype(float)
        ma5 = close.rolling(5).mean()
        slopes = ma5.diff()
        prior = [float(x) for x in slopes.iloc[-4-required_bars:-required_bars] if x == x]
        had_run = any(direction * value > 0 for value in prior)
        confirmed = all(
            pd.notna(slopes.iloc[index])
            and direction * float(slopes.iloc[index]) <= 0
            and direction * (float(close.iloc[index]) - float(ma5.iloc[index])) <= 0
            for index in range(-required_bars, 0))
        return had_run, confirmed

    five_closed = closed_candles(five, 5)
    one_closed = closed_candles(one, 1)
    five_run, five_turn = turn(five_closed)
    if five_run:
        if five_turn:
            if (lot_profit_points > 0
                    and not addon_take_profit_unlocked(
                        lot.entry_price, latest, lot.side, required_profit_points)):
                return ""
            return ("5分钟MA5顺势运行后拐弯且收盘价回到MA5反向一侧；"
                    f"当前小单盈亏{lot_profit_points:+.2f}点，止盈须严格超过"
                    f"{required_profit_points:.2f}点")
        return ""
    one_run, one_turn = turn(one_closed)
    if one_run and one_turn:
        if (lot_profit_points > 0
                and not addon_take_profit_unlocked(
                    lot.entry_price, latest, lot.side, required_profit_points)):
            return ""
        return ("1分钟MA5顺势运行后拐弯且收盘价回到MA5反向一侧；"
                f"当前小单盈亏{lot_profit_points:+.2f}点，止盈须严格超过"
                f"{required_profit_points:.2f}点")
    return ""


def _close_addon_on_ma5(client, ledger, lot, reason: str) -> AddonCloseResult:
    # OKX merges every order on the same hedge side into one exchange
    # position.  The local lot is nevertheless the ownership boundary for
    # the 0.1% recycling strategy.  Never let this helper reduce a base lot:
    # base exposure may leave only through its own exchange TP order.
    if lot.kind != "addon":
        raise OkxError("账户05拒绝用小单/利润池通道减基础仓；基础仓只能由自身止盈单退出")
    signal_id = f"exit:{lot.lot_id}"
    client_id = _client_id("A5EX", signal_id)
    intent = ledger.order_intent(signal_id)
    if intent is not None and intent["client_order_id"]:
        client_id = str(intent["client_order_id"])
    if (intent is not None and str(intent["status"]) == "unknown"
            and _reduce_side_already_flat(OkxError(str(intent["detail"] or "")))
            and not str(intent["exchange_order_id"] or "")):
        # A previous version already received an explicit 51169 while this
        # virtual lot was being reduced.  Do not let a newly rebuilt base on
        # the same side turn that stale retry into a real reduction.
        ledger.sync_take_profit_fill(lot.lot_id, lot.original_size)
        ledger.update_order_intent(
            signal_id, "filled",
            detail="继承旧版明确51169：该虚拟小单已在交易所不存在，仅关闭陈旧批次")
        return AddonCloseResult("", Decimal("0"))
    recovered_order_id = ""
    # v0.7.263 could stop after durably claiming the exit but before saving an
    # exchange order id when the read-only clock sync timed out.  Reconcile
    # the clOrdId first: an existing order is adopted; confirmed 51603 means
    # no order exists and the identical id can be submitted safely.
    claimed_without_order = (
        intent is not None
        and str(intent["status"]) == "claimed"
        and not str(intent["exchange_order_id"] or ""))
    if claimed_without_order:
        try:
            recovered = client.order(
                ACCOUNT05_INSTRUMENT,
                client_order_id=str(intent["client_order_id"] or client_id))
        except OkxError as exc:
            if not _order_does_not_exist(exc):
                raise
        else:
            recovered_order_id = str(recovered.get("ordId") or "")
            if not recovered_order_id:
                raise OkxError("账户05小单止盈对账缺少交易所订单号")
            ledger.update_order_intent(
                signal_id, "submitted", exchange_order_id=recovered_order_id,
                detail="启动时按客户端订单号找回已有止盈平仓单")
            intent = ledger.order_intent(signal_id)
    retryable_rejection = (
        intent is not None
        and ((str(intent["status"]) == "rejected"
              and "pre-order server-time synchronization GET failed" in
              str(intent["detail"] or ""))
             or claimed_without_order and not recovered_order_id))
    if intent is None or retryable_rejection:
        if intent is None:
            ledger.claim_order_intent(
                signal_id=signal_id, client_order_id=client_id, side=lot.side,
                kind="addon_ma5_exit", requested_size=lot.remaining_size)
        try:
            order = client.place_market_reduce(
                inst_id=ACCOUNT05_INSTRUMENT, position_side=lot.side.value,
                size=lot.remaining_size, client_order_id=client_id)
        except OkxError as exc:
            if _reduce_side_already_flat(exc):
                # 51169 is an explicit exchange statement that this hedge side
                # has no position to reduce.  Confirm with a fresh snapshot
                # before repairing every stale virtual lot on that side.
                current_positions = _position_map(client.raw_snapshot())
                if lot.side not in current_positions:
                    closed = ledger.reconcile_side_flat(lot.side)
                    ledger.update_order_intent(
                        signal_id, "filled",
                        detail=(f"OKX 51169且刷新持仓确认{lot.side.value}方向为空；"
                                f"已关闭{closed}个本地陈旧批次"))
                    return AddonCloseResult("", Decimal("0"))
            status = ("rejected" if
                      "pre-order server-time synchronization GET failed" in str(exc)
                      else "unknown")
            ledger.update_order_intent(signal_id, status, detail=str(exc))
            raise
        order_id = str(order["ordId"])
        ledger.update_order_intent(signal_id, "submitted", exchange_order_id=order_id,
                                   detail=reason)
    else:
        order_id = recovered_order_id or str(intent["exchange_order_id"] or "")
        if str(intent["status"]) == "filled":
            ledger.sync_take_profit_fill(lot.lot_id, lot.original_size)
            return AddonCloseResult(order_id, Decimal("0"))
        if not order_id:
            raise OkxError("账户05小单MA5止盈意图缺少交易所订单号，停止对账")
    fill = _wait_for_fill(client, order_id)
    cumulative = Decimal(str(fill.get("accFillSz") or fill.get("fillSz") or "0"))
    if cumulative < lot.remaining_size:
        raise OkxError("账户05小单MA5止盈未完全成交，停止对账")
    ledger.update_order_intent(signal_id, "filled", exchange_order_id=order_id, detail=reason)
    exit_price = Decimal(str(fill.get("avgPx") or fill.get("fillPx") or "0"))
    def optional_decimal(*keys: str) -> Decimal | None:
        for key in keys:
            value = str(fill.get(key) or "").strip()
            if value:
                try:
                    return Decimal(value)
                except Exception:
                    pass
        return None
    result = AddonCloseResult(
        order_id, exit_price,
        optional_decimal("pnl", "fillPnl", "realizedPnl"),
        optional_decimal("fee", "fillFee"))
    ledger.sync_take_profit_fill(lot.lot_id, lot.original_size)
    return result


def _local_addon_net(lot, exit_price: Decimal, spec: ContractSpec) -> Decimal:
    """Return the virtual lot's local net PnL, independent of OKX position PnL.

    In hedge mode OKX reports a partial close against the side's aggregate
    average price.  Account 05 intentionally keeps a separate local FIFO-like
    lot book: pairing and the recovery pool are therefore based on this
    add-on's own recorded entry and exit prices.  This value is an internal
    strategy attribution and must never authorize reducing a base lot.
    """
    return _local_addon_pnl_breakdown(lot, exit_price, spec)[2]


def _submit_addon_profit_limit(client, ledger, lot, mark: Decimal,
                               spec: ContractSpec, required_points: Decimal,
                               reason: str) -> str:
    """Queue a reduce-only limit that cannot fill inside the ten-point gate."""
    if lot.kind != "addon":
        raise OkxError("账户05小单止盈通道不得减基础仓")
    if (not addon_take_profit_unlocked(lot.entry_price, mark, lot.side, required_points)
            or _local_addon_net(lot, mark, spec) <= 0):
        return ""
    direction = Decimal("1") if lot.side is PositionSide.LONG else Decimal("-1")
    limit_price = lot.entry_price + direction * (
        addon_take_profit_threshold(required_points) + Decimal("0.01"))
    if limit_price <= 0 or _local_addon_net(lot, limit_price, spec) <= 0:
        return ""
    signal_id = f"exit:{lot.lot_id}"
    if ledger.order_intent(signal_id) is not None:
        return ""  # An existing or uncertain intent must be reconciled first.
    client_id = _client_id("A5EX", signal_id)
    ledger.claim_order_intent(
        signal_id=signal_id, client_order_id=client_id, side=lot.side,
        kind="addon_ma5_exit", requested_size=lot.remaining_size)
    try:
        order = client.place_lot_take_profit(
            inst_id=ACCOUNT05_INSTRUMENT, position_side=lot.side.value,
            size=lot.remaining_size, price=limit_price,
            client_order_id=client_id)
    except OkxError as exc:
        # 51169 means the aggregate exchange position for this direction was
        # already manually reduced to zero.  Confirm with a fresh read-only
        # snapshot, then retire only this local virtual lot.  It is not an
        # account-wide fault and must never be retried as another POST.
        if "51169" in str(exc):
            try:
                positions = _position_map(client.raw_snapshot())
            except Exception:
                positions = {lot.side: {"pos": "1"}}
            if abs(Decimal(str(positions.get(lot.side, {}).get("pos") or "0"))) <= 0:
                ledger.update_order_intent(
                    signal_id, "rejected", detail="交易所该方向已无持仓，自动清理本地利润池记录")
                ledger.delete_lot(lot.lot_id)
                return ""
        ledger.update_order_intent(signal_id, "unknown", detail=str(exc))
        raise
    order_id = str(order.get("ordId") or "")
    if not order_id:
        ledger.update_order_intent(signal_id, "unknown", detail="止盈限价单缺少ordId")
        raise OkxError("账户05小单止盈限价单缺少ordId，停止自动执行并对账")
    ledger.update_order_intent(
        signal_id, "submitted", exchange_order_id=order_id,
        detail=f"{reason}；本地止盈保护限价{limit_price}")
    return order_id


def _local_addon_pnl_breakdown(
        lot, exit_price: Decimal, spec: ContractSpec
) -> tuple[Decimal, Decimal, Decimal]:
    direction = Decimal("1") if lot.side is PositionSide.LONG else Decimal("-1")
    gross = direction * (exit_price - lot.entry_price) * lot.remaining_size * spec.ct_val
    # Conservative regular-user taker estimate for both entry and exit.
    fees = ((lot.entry_price + exit_price) * lot.remaining_size * spec.ct_val
            * Decimal("0.0005"))
    return gross, fees, gross - fees


def _book_closed_addon(ledger: Account05StateStore, lot,
                       result: AddonCloseResult, spec: ContractSpec) -> Decimal:
    if result.exit_price <= 0 or not result.order_id:
        return Decimal("0")
    gross, fee_estimate, net = _local_addon_pnl_breakdown(
        lot, result.exit_price, spec)
    ledger.record_addon_exit_accounting(
        lot.lot_id, exit_order_id=result.order_id,
        exit_price=result.exit_price, local_gross_pnl=gross,
        local_fee_estimate=fee_estimate, local_net_pnl=net,
        exchange_realized_pnl=result.exchange_realized_pnl,
        exchange_fee=result.exchange_fee)
    return net


def _reconcile_addon_profit_limits(client, ledger, spec: ContractSpec) -> set[PositionSide]:
    """Book completed limit exits; leave live orders pending without resubmission."""
    closed_sides: set[PositionSide] = set()
    for lot in ledger.open_lots():
        if lot.kind != "addon":
            continue
        if ledger.connection.execute(
                "SELECT 1 FROM account05_extreme_targets t JOIN account05_extreme_rotation_events e "
                "ON t.event_key=e.event_key WHERE t.lot_id=? AND e.status='claimed'",
                (lot.lot_id,)).fetchone():
            continue
        signal_id = f"exit:{lot.lot_id}"
        intent = ledger.order_intent(signal_id)
        if (intent is None or str(intent["status"]) not in {"submitted", "reconcile_required"}
                or not intent["exchange_order_id"]):
            continue
        order_id = str(intent["exchange_order_id"])
        fill = client.order(ACCOUNT05_INSTRUMENT, order_id=order_id)
        state = str(fill.get("state") or "")
        cumulative = Decimal(str(fill.get("accFillSz") or fill.get("fillSz") or "0"))
        if (cumulative < lot.original_size - lot.remaining_size
                or cumulative > lot.original_size):
            # Isolate malformed exchange data to this lot.  Do not fault-stop
            # the whole account or submit another uncertain reduce order.
            ledger.update_order_intent(
                signal_id, "reconcile_required", exchange_order_id=order_id,
                detail=f"止盈单返回累计成交量异常：{cumulative}")
            continue
        if state in {"live", "partially_filled"}:
            if cumulative > 0:
                ledger.sync_take_profit_fill(lot.lot_id, cumulative)
            ledger.update_order_intent(
                signal_id, "submitted", exchange_order_id=order_id,
                detail=str(intent["detail"] or "") +
                f"；止盈单仍在交易所{state}，累计成交{cumulative}")
            continue
        terminal = state in {"filled", "canceled", "mmp_canceled"}
        if not terminal or cumulative < lot.original_size:
            if terminal:
                # A canceled limit can have actual partial fills. Rebuild
                # cumulative remaining size once; never treat an addon as a
                # base requiring an independent server-side TP. Preserve the
                # exit ID and isolate its intent so no uncertain POST repeats.
                ledger.sync_take_profit_fill(lot.lot_id, cumulative)
            ledger.mark_addon_ma5_managed(lot.lot_id)
            ledger.update_order_intent(
                signal_id, "reconcile_required", exchange_order_id=order_id,
                detail=f"止盈单状态{state}，累计成交{cumulative}/{lot.original_size}；保留剩余小单和原订单号待对账，不重复提交")
            continue
        exit_price = Decimal(str(fill.get("avgPx") or fill.get("fillPx") or "0"))
        if exit_price <= 0:
            raise OkxError("账户05小单止盈限价单成交价缺失，停止自动执行并对账")
        result = AddonCloseResult(order_id, exit_price)
        net = _book_closed_addon(ledger, lot, result, spec)
        ledger.sync_take_profit_fill(lot.lot_id, lot.original_size)
        ledger.update_order_intent(
            signal_id, "filled", exchange_order_id=order_id,
            detail=str(intent["detail"] or "") + "；限价止盈已成交")
        if net > 0:
            ledger.credit_recovery_profit(net)
        closed_sides.add(lot.side)
    return closed_sides


def _extreme_exit_fill(ledger, lot_id, order_id, quantity, price):
    if quantity <= 0:
        return
    if price <= 0:
        raise OkxError("极值平仓成交价缺失，先对账，不反手")
    with ledger.connection:
        ledger.connection.execute(
            "INSERT OR REPLACE INTO account05_extreme_exit_fills VALUES (?,?,?,?)",
            (lot_id, order_id, str(quantity), str(price)))


def _book_extreme_fills(ledger, lot, spec):
    rows = list(ledger.connection.execute(
        "SELECT * FROM account05_extreme_exit_fills WHERE lot_id=? ORDER BY order_id", (lot.lot_id,)))
    total = sum((Decimal(r["filled_size"]) for r in rows), Decimal(0))
    if not total:
        return
    if total > lot.original_size:
        raise OkxError("极值平仓累计成交超过批次原数量，先对账")
    price = sum((Decimal(r["filled_size"]) * Decimal(r["exit_price"]) for r in rows), Decimal(0)) / total
    result = AddonCloseResult("、".join(str(r["order_id"]) for r in rows), price)
    net = _book_closed_addon(ledger, replace(lot, remaining_size=total), result, spec)
    if net > 0:
        ledger.credit_recovery_profit(net, credit_key=f"extreme:{lot.lot_id}")


def _close_extreme_winner(client, ledger, lot, spec, points, mark, reason):
    """Confirm a whole remaining lot's reduction before permitting reversal."""
    signal = f"exit:{lot.lot_id}"
    intent = ledger.order_intent(signal)
    if intent is not None:
        oid = str(intent["exchange_order_id"] or "")
        if not oid:
            try:
                found = client.order(ACCOUNT05_INSTRUMENT, client_order_id=str(intent["client_order_id"]))
            except OkxError as exc:
                if not _order_does_not_exist(exc):
                    raise
                # A read-only 51603 is positive evidence that the durable ID
                # has no order. Do not resend without this reconciliation.
                ledger.update_order_intent(signal, "claimed", detail=reason)
            else:
                oid = str(found.get("ordId") or "")
                if not oid:
                    raise OkxError("极值平仓意图对账缺少订单号")
                ledger.update_order_intent(signal, "submitted", exchange_order_id=oid, detail=reason)
        if oid:
            order = client.order(ACCOUNT05_INSTRUMENT, order_id=oid)
            if str(order.get("state")) in {"live", "partially_filled"}:
                client.cancel_order(ACCOUNT05_INSTRUMENT, oid)
                order = client.order(ACCOUNT05_INSTRUMENT, order_id=oid)
            if str(order.get("state")) not in {"filled", "canceled", "mmp_canceled"}:
                raise OkxError("极值平仓旧订单未确认结束，先对账，不反手")
            intent = ledger.order_intent(signal)
            requested = Decimal(str(intent["requested_size"]))
            quantity = Decimal(str(order.get("accFillSz") or "0"))
            if quantity < 0 or quantity > requested:
                raise OkxError("极值平仓旧订单成交数量异常，先对账，不反手")
            _extreme_exit_fill(ledger, lot.lot_id, oid, quantity,
                               Decimal(str(order.get("avgPx") or order.get("fillPx") or "0")))
            total_closed = lot.original_size - requested + quantity
            if total_closed > lot.original_size or total_closed < 0:
                raise OkxError("极值平仓累计数量异常")
            # This is cumulative and therefore safe after partial-fill polling.
            lot = ledger.sync_take_profit_fill(lot.lot_id, max(
                lot.original_size - lot.remaining_size, total_closed))
            if lot.remaining_size:
                client_id = _client_id("A5EX", f"{signal}:after:{oid}:{lot.remaining_size}")
                with ledger.connection:
                    ledger.connection.execute(
                        """UPDATE account05_order_intents SET status='claimed',
                        client_order_id=?,exchange_order_id=NULL,requested_size=?,detail=? WHERE signal_id=?""",
                        (client_id, str(lot.remaining_size), reason, signal))
            else:
                ledger.update_order_intent(signal, "filled", exchange_order_id=oid, detail=reason)
    if lot.remaining_size:
        quote = client.exit_quote(lot.side.value) if hasattr(client, "exit_quote") else mark
        if (not addon_take_profit_unlocked(lot.entry_price, quote, lot.side, points)
                or _local_addon_net(lot, quote, spec) <= 0):
            raise OkxError("极值待平订单实时报价已不满足严格止盈点数；未平订单保留，不开反手")
        result = _close_addon_on_ma5(client, ledger, lot, reason)
        if result.order_id:
            _extreme_exit_fill(ledger, lot.lot_id, result.order_id, lot.remaining_size, result.exit_price)
    if ledger.get_lot(lot.lot_id).status != "closed":
        raise OkxError("极值盈利订单未确认全部平仓，不开反手")
    _book_extreme_fills(ledger, lot, spec)
    return str(ledger.get_lot(lot.lot_id).lot_id)


def _reconcile_extreme_exit_results(client, ledger, spec):
    """Recover exit fills with GET before any history/position-coverage check."""
    closed_sides = set()
    rows = list(ledger.connection.execute(
        "SELECT DISTINCT t.lot_id FROM account05_extreme_targets t JOIN account05_extreme_rotation_events e "
        "ON t.event_key=e.event_key WHERE e.status='claimed'"))
    for row in rows:
        lot = ledger.get_lot(str(row[0]))
        intent = ledger.order_intent(f"exit:{lot.lot_id}")
        if intent is None:
            continue
        oid = str(intent["exchange_order_id"] or "")
        try:
            fill = client.order(ACCOUNT05_INSTRUMENT, **(
                {"order_id": oid} if oid else {"client_order_id": str(intent["client_order_id"])}))
        except OkxError as exc:
            if not oid and _order_does_not_exist(exc):
                continue
            raise
        oid = str(fill.get("ordId") or oid)
        requested = Decimal(str(intent["requested_size"]))
        quantity = Decimal(str(fill.get("accFillSz") or "0"))
        if not oid or quantity < 0 or quantity > requested:
            raise OkxError("极值平仓恢复对账数量/订单号异常")
        if quantity:
            _extreme_exit_fill(ledger, lot.lot_id, oid, quantity,
                               Decimal(str(fill.get("avgPx") or fill.get("fillPx") or "0")))
            ledger.sync_take_profit_fill(lot.lot_id, max(
                lot.original_size - lot.remaining_size, lot.original_size - requested + quantity))
        if ledger.get_lot(lot.lot_id).remaining_size == 0:
            ledger.update_order_intent(f"exit:{lot.lot_id}", "filled", exchange_order_id=oid,
                                       detail=str(intent["detail"] or ""))
            _book_extreme_fills(ledger, lot, spec)
            closed_sides.add(lot.side)
        else:
            ledger.update_order_intent(f"exit:{lot.lot_id}", "submitted", exchange_order_id=oid,
                                       detail=str(intent["detail"] or ""))
    return closed_sides


def _submit_entry(client: Account05LiveClient, ledger: Account05StateStore, *,
                  signal_id: str, side: PositionSide, kind: str, size: Decimal,
                  spec: ContractSpec, config: Account05Config,
                  base_take_profit_pct: Decimal | None = None,
                  entry_evidence: dict | None = None,
                  resume_existing: bool = False) -> tuple[str, ...]:
    client_id = _client_id("A5EN", signal_id)
    claimed = ledger.claim_order_intent(
        signal_id=signal_id, client_order_id=client_id, side=side,
        kind=kind, requested_size=size)
    if not claimed:
        intent = ledger.order_intent(signal_id)
        retryable_rejection = (
            intent is not None
            and str(intent["status"]) == "rejected"
            and "pre-order server-time synchronization GET failed" in
            str(intent["detail"] or ""))
        if not retryable_rejection:
            if not resume_existing or intent is None:
                return ()
            client_id = str(intent["client_order_id"])
            size = Decimal(str(intent["requested_size"]))
            existing_lot = ledger.connection.execute(
                "SELECT 1 FROM account05_lots WHERE lot_id=?", (signal_id + "-T1",)).fetchone()
            if str(intent["status"]) == "filled" and existing_lot:
                return (str(intent["exchange_order_id"]),)
            try:
                recovered = client.order(ACCOUNT05_INSTRUMENT, client_order_id=client_id)
            except OkxError as exc:
                if not _order_does_not_exist(exc) or str(intent["status"]) in {"submitted", "filled"}:
                    raise
                # Only a confirmed absent order may reuse the same durable ID.
            else:
                order_id = str(recovered.get("ordId") or "")
                if not order_id:
                    raise OkxError("极值反手对账缺少订单号")
                fill = _wait_for_fill(client, order_id)
                if Decimal(str(fill.get("accFillSz") or "0")) != size:
                    raise OkxError("极值反手对账数量不符")
                ledger.update_order_intent(signal_id, "filled", exchange_order_id=order_id)
                return (order_id,) + _protect_fill(
                    client, ledger, signal_id=signal_id, side=side, kind=kind,
                    fill=fill, spec=spec, config=config, base_take_profit_pct=base_take_profit_pct)
    if entry_evidence is not None:
        ledger.record_entry_review(signal_id, entry_evidence)
    try:
        order = client.place_market_entry(
            inst_id=ACCOUNT05_INSTRUMENT,
            side="buy" if side is PositionSide.LONG else "sell",
            position_side=side.value, size=size, client_order_id=client_id)
        order_id = str(order["ordId"])
        ledger.update_order_intent(signal_id, "submitted", exchange_order_id=order_id)
        fill = _wait_for_fill(client, order_id)
        ledger.update_order_intent(signal_id, "filled", exchange_order_id=order_id)
        return (order_id,) + _protect_fill(
            client, ledger, signal_id=signal_id, side=side, kind=kind,
            fill=fill, spec=spec, config=config,
            base_take_profit_pct=base_take_profit_pct)
    except OkxError as exc:
        status = "unknown" if "对账" in str(exc) or "outcome unknown" in str(exc) else "rejected"
        ledger.update_order_intent(signal_id, status, detail=str(exc))
        raise


def _resume_base_take_profit_replacements(
        client: Account05LiveClient, ledger: Account05StateStore) -> None:
    """Finish durable TP repricing, preferring OKX's gap-free in-place amend."""
    for replacement in ledger.pending_take_profit_replacements():
        lot_id = str(replacement["lot_id"])
        lot = ledger.get_lot(lot_id)
        target = Decimal(str(replacement["new_price"]))
        old_order_id = str(replacement["old_order_id"])
        new_order_id = str(replacement["new_order_id"] or "")

        # Recover swaps created by v0.7.252/v0.7.253.  If their new order is
        # genuinely live, finish the original new-first protocol.  OKX may
        # auto-cancel it when two full-size reduce-only orders overlap; in
        # that case fall through to an atomic amendment of the protected old
        # order instead of treating the expected cancellation as fatal.
        if not new_order_id:
            try:
                order = client.order(
                    ACCOUNT05_INSTRUMENT,
                    client_order_id=str(replacement["new_client_order_id"]))
            except OkxError as exc:
                if not _order_does_not_exist(exc):
                    raise
                order = None
            if order is not None:
                new_order_id = str(order.get("ordId") or "")
                if not new_order_id:
                    raise OkxError("账户05新大仓止盈缺少ordId，停止并对账")
                replacement = ledger.mark_replacement_new_live(lot_id, new_order_id)

        if new_order_id:
            try:
                new_order = client.order(ACCOUNT05_INSTRUMENT, order_id=new_order_id)
            except OkxError as exc:
                if not _order_does_not_exist(exc):
                    raise
                new_order = {"state": "canceled", "accFillSz": "0"}
            if str(new_order.get("state")) in {"live", "partially_filled", "filled"}:
                old_order = client.order(ACCOUNT05_INSTRUMENT, order_id=old_order_id)
                old_state = str(old_order.get("state"))
                old_filled = Decimal(str(old_order.get("accFillSz") or "0"))
                if old_filled > 0:
                    if str(new_order.get("state")) in {"live", "partially_filled"}:
                        client.cancel_order(ACCOUNT05_INSTRUMENT, new_order_id)
                    ledger.abort_take_profit_replacement(lot_id)
                    ledger.sync_take_profit_fill(lot_id, old_filled)
                    if old_state != "filled":
                        raise OkxError("账户05旧大仓止盈在换单窗口部分成交，已撤新单并停止对账")
                    continue
                if old_state not in {"canceled", "mmp_canceled"}:
                    client.cancel_order(ACCOUNT05_INSTRUMENT, old_order_id)
                    try:
                        old_order = client.order(ACCOUNT05_INSTRUMENT, order_id=old_order_id)
                    except OkxError as exc:
                        if not _order_does_not_exist(exc):
                            raise
                    else:
                        if str(old_order.get("state")) not in {"canceled", "mmp_canceled"}:
                            raise OkxError("账户05新大仓止盈已生效，但旧止盈未确认撤销；停止并对账")
                ledger.complete_take_profit_replacement(lot_id)
                continue

        # Normal v0.7.254 path: change the existing live order in place.  The
        # original order remains active if OKX rejects the amend because
        # cxlOnFail=false.  A restart first observes px, so a lost successful
        # response is recovered without submitting another economic action.
        old_order = client.order(ACCOUNT05_INSTRUMENT, order_id=old_order_id)
        old_state = str(old_order.get("state"))
        old_filled = Decimal(str(old_order.get("accFillSz") or "0"))
        if old_filled > 0:
            ledger.abort_take_profit_replacement(lot_id)
            ledger.sync_take_profit_fill(lot_id, old_filled)
            continue
        if old_state not in {"live", "partially_filled"}:
            raise OkxError("账户05原大仓止盈非活动状态，停止并对账")
        current_price = Decimal(str(old_order.get("px") or "0"))
        if current_price != target:
            try:
                client.amend_order_price(
                    ACCOUNT05_INSTRUMENT, old_order_id, target,
                    str(replacement["new_client_order_id"]))
            except OkxError as exc:
                # EdgeOne HTTP 554 is a response timeout after the amend POST
                # reached the gateway.  The old reduce-only TP remains the
                # safety order; defer reconciliation to the next cycle so a
                # successful amend is observed before any retry or fault.
                if "HTTP 554" in str(exc) or "HTTP 554" in repr(exc):
                    return
                raise
            old_order = client.order(ACCOUNT05_INSTRUMENT, order_id=old_order_id)
        if (str(old_order.get("state")) not in {"live", "partially_filled"}
                or Decimal(str(old_order.get("px") or "0")) != target):
            raise OkxError("账户05大仓止盈改价未确认生效，原止盈保留并停止对账")
        ledger.complete_take_profit_amendment(lot_id)


def _reprice_base_take_profits_after_position_change(
        client: Account05LiveClient, ledger: Account05StateStore,
        config: Account05Config, sides: set[PositionSide]) -> None:
    """Place each new average-price TP first, then retire its prior order."""
    if not sides:
        return
    positions = _position_map(client.raw_snapshot())
    for side in sides:
        position = positions.get(side)
        average = Decimal(str((position or {}).get("avgPx") or "0"))
        if average <= 0:
            continue
        for lot in ledger.open_lots(side):
            if lot.kind != "base" or not lot.take_profit_algo_id:
                continue
            profit_pct = lot.take_profit_pct
            if profit_pct <= 0:
                profit_pct = (config.base_take_profit_pcts[0]
                              if lot.lot_id.endswith("-T1")
                              else config.base_take_profit_pcts[1])
            target = take_profit_price(
                average, side, profit_pct, Decimal("0.01"))
            if target == lot.take_profit_price:
                continue
            client_id = _client_id(
                "A5RP", f"{lot.lot_id}:{lot.take_profit_algo_id}:{target}")
            ledger.plan_take_profit_replacement(
                lot.lot_id, old_order_id=lot.take_profit_algo_id,
                new_client_order_id=client_id, new_price=target)
    _resume_base_take_profit_replacements(client, ledger)


def _restore_unprotected_base_take_profits(
        client: Account05LiveClient, ledger: Account05StateStore,
        positions: dict[PositionSide, dict]) -> tuple[str, ...]:
    """Recover a confirmed base fill whose reduce-only TP was lost/canceled.

    Recovery is allowed only when the exchange aggregate can cover every open
    locally owned lot on that side. A deterministic clOrdId makes a lost POST
    response restart-safe; no entry order is submitted here.
    """
    restored: list[str] = []
    for side in (PositionSide.LONG, PositionSide.SHORT):
        unprotected = [lot for lot in ledger.unprotected_lots()
                       if lot.kind == "base" and lot.side is side]
        if not unprotected:
            continue
        exchange_size = abs(Decimal(str(
            positions.get(side, {}).get("pos") or "0")))
        all_base = [lot for lot in ledger.open_lots(side) if lot.kind == "base"]
        current_base = all_base[-1] if all_base else None
        current_size = (current_base.remaining_size
                        if current_base is not None else Decimal("0"))
        if exchange_size < current_size:
            if exchange_size > 0:
                raise OkxError(
                    f"账户05{side.value}真实仓位{exchange_size}不足以覆盖当前基础仓"
                    f"{current_size}；拒绝恢复止盈并停止人工对账")
            # The exchange position is already flat. These virtual fills can
            # no longer be protected; quarantine them and let the normal
            # two-stage base rebuild recreate the missing side.
            for lot in unprotected:
                ledger.mark_lot_reconcile_required(lot.lot_id)
            continue
        candidates = ([lot for lot in unprotected
                       if current_base is not None and lot.lot_id == current_base.lot_id])
        for lot in unprotected:
            if lot not in candidates:
                ledger.mark_lot_reconcile_required(lot.lot_id)
        for lot in candidates:
            client_id = _client_id("A5TP", lot.lot_id)
            try:
                order = client.order(
                    ACCOUNT05_INSTRUMENT, client_order_id=client_id)
            except OkxError as exc:
                if not _order_does_not_exist(exc):
                    raise
                order = client.place_lot_take_profit(
                    inst_id=ACCOUNT05_INSTRUMENT,
                    position_side=side.value,
                    size=lot.remaining_size,
                    price=lot.take_profit_price,
                    client_order_id=client_id)
            order_id = str(order.get("ordId") or "")
            if not order_id:
                raise OkxError("账户05恢复基础仓止盈响应缺少ordId；停止对账")
            confirmed = client.order(ACCOUNT05_INSTRUMENT, order_id=order_id)
            if str(confirmed.get("state")) not in {"live", "partially_filled"}:
                raise OkxError("账户05恢复基础仓止盈未确认活动；停止对账")
            ledger.attach_take_profit(lot.lot_id, order_id)
            restored.append(order_id)
    return tuple(restored)


def execute_account05_tick(*, client: Account05LiveClient,
                           ledger: Account05StateStore,
                           settings: LiveAccountSettings,
                           audit: dict, one, five, fifteen,
                           one_hour=None) -> Account05TickResult:
    """Evaluate one tick. Any uncertain write raises and stops the outer loop."""
    capital = Decimal(settings.operating_capital_usdt())
    points = Decimal(settings.addon_take_profit_points())
    config = Account05Config(
        operating_capital_usdt=capital, addon_take_profit_points=points,
        long_slot_capacity=int(settings.long_slot_capacity()),
        short_slot_capacity=int(settings.short_slot_capacity()),
        extreme_rotation_contracts=Decimal(settings.extreme_rotation_contracts()),
        slot_contracts=Decimal(settings.slot_contracts()))
    # Keep the diagnostic text safe even when a legacy configuration still
    # reports the combined-loss permission code.  Current account05 settings
    # disable this limit by setting the percentage to zero.
    loss_limit = config.operating_capital_usdt * config.max_combined_floating_loss_pct
    base_rebuild_enabled = settings.base_rebuild_enabled()
    contract = audit["instrument"]
    mark = Decimal(str(audit["market"].get("markPx") or audit["market"].get("last") or "0"))
    spec = ContractSpec(
        ct_val=Decimal(str(contract["ctVal"])),
        lot_size=Decimal(str(contract["lotSz"])),
        min_size=Decimal(str(contract["minSz"])),
    )
    signals: Account05Signals = evaluate_account05_signals(
        one, five, fifteen, one_hour)
    context = entry_context(signals, one, five, fifteen, one_hour)

    def evidence(source, reason):
        return {**context, "source": source, "reason": reason}
    recovered_extreme_sides = _reconcile_extreme_exit_results(client, ledger, spec)
    snapshot = client.raw_snapshot()
    positions = _position_map(snapshot)
    # Reconcile manually flattened directions before any capacity or signal
    # logic.  This must run even when base rebuilding is disabled: an
    # exchange side at zero cannot support any remaining virtual addon lots.
    for flat_side in (PositionSide.LONG, PositionSide.SHORT):
        exchange_size = abs(Decimal(str(
            positions.get(flat_side, {}).get("pos") or "0")))
        if exchange_size <= 0:
            for stale in list(ledger.open_lots(flat_side)):
                if stale.kind == "addon":
                    ledger.delete_lot(stale.lot_id)
    combined_upl = sum(
        Decimal(str(row.get("upl") or "0")) for row in positions.values())
    equity_text = str((audit.get("usdt_balance") or {}).get("eq") or "0")
    try:
        account_equity = Decimal(equity_text)
    except Exception:
        account_equity = Decimal("0")
    recovery = ledger.recovery_program()
    if (recovery is None and Decimal("0") < account_equity < capital
            and ledger.open_addon_count(PositionSide.LONG) >= 6
            and any(lot.kind == "base"
                    for lot in ledger.open_lots(PositionSide.LONG))):
        recovery = ledger.activate_recovery_program(
            initial_equity=account_equity, target_equity=capital)
    if recovery is not None and int(recovery["active"]) == 1:
        if (account_equity >= Decimal(str(recovery["target_equity"]))
                or ledger.recovery_trapped_open_count() == 0):
            recovery = ledger.set_recovery_phase("complete")
    _resume_base_take_profit_replacements(client, ledger)
    # One-time upgrade of legacy split 0.5%/0.7% base lots.  Keep both virtual
    # halves for reconciliation, but align them to one price so the complete
    # base position exits together and can be rebuilt immediately.
    legacy_sides: set[PositionSide] = set()
    selected_base_pct = (config.base_take_profit_pcts[0]
                         if signals.trend_5m is Trend15m.UNCLEAR
                         else config.base_take_profit_pcts[1])
    for lot in ledger.open_lots():
        if lot.kind == "base" and lot.take_profit_pct <= 0:
            ledger.set_base_take_profit_pct(lot.lot_id, selected_base_pct)
            legacy_sides.add(lot.side)
    _reprice_base_take_profits_after_position_change(
        client, ledger, config, legacy_sides)
    addon_closed_sides: set[PositionSide] = set(recovered_extreme_sides)
    base_closed_sides: set[PositionSide] = set()
    order_ids: list[str] = []

    # Independent base-rebuild audit.  This runs before addon exits, extreme
    # reversals, and ordinary signal arbitration.  A side that has historical
    # base lots but no current base lot is a rebuild obligation; it must not be
    # hidden by an ``observe``/``unclear`` return from the signal path.
    if not base_rebuild_enabled:
        base_audit_rows = ["base_rebuild_disabled:user_policy"]
    else:
        base_audit_rows = []
    base_audit_submitted: list[str] = []
    audit_bar = str(fifteen.sort_values("date").iloc[-1]["date"])
    for rebuild_side in ((PositionSide.LONG, PositionSide.SHORT)
                         if base_rebuild_enabled else ()):
        rebuild_historical, rebuild_open = ledger.base_audit_state(rebuild_side)
        rebuild_lots = [lot for lot in ledger.open_lots(rebuild_side)
                        if lot.kind == "base"]
        newest_rebuild = rebuild_lots[-1] if rebuild_lots else None
        exchange_rebuild_size = abs(Decimal(str(
            positions.get(rebuild_side, {}).get("pos") or "0")))
        rebuild_covered = bool(
            newest_rebuild is not None
            and newest_rebuild.remaining_size > 0
            and exchange_rebuild_size >= newest_rebuild.remaining_size)
        if (rebuild_open and rebuild_covered) or not rebuild_historical:
            continue
        rebuild_reason = (
            f"base_audit_missing:{rebuild_side.value};"
            f"historical={int(rebuild_historical)};open={int(rebuild_open)};"
            f"covered={int(rebuild_covered)};exchange={exchange_rebuild_size};bar={audit_bar}")
        base_audit_rows.append(rebuild_reason)
        plan = ledger.plan_base_rebuild(
            side=rebuild_side, signal_key=(
                f"base-rebuild-stage1:{rebuild_side.value}:{audit_bar}"),
            target_margin_pct=Decimal("0.012"),
            first_margin_pct=Decimal("0.006"),
            take_profit_pct=selected_base_pct)
        rebuild_size = contracts_for_notional(
            capital * Decimal("0.006") * config.leverage, mark, spec)
        if rebuild_size < spec.min_size:
            raise OkxError(
                f"账户05{rebuild_side.value}基础仓审计重建低于交易所最小张数；{rebuild_reason}")
        base_audit_submitted.extend(_submit_entry(
            client, ledger, signal_id=str(plan["signal_key"]),
            side=rebuild_side, kind="base", size=rebuild_size,
            spec=spec, config=config, base_take_profit_pct=selected_base_pct,
            entry_evidence=evidence(
                "基础仓独立审计重建", rebuild_reason)))
        if base_audit_submitted:
            ledger.set_base_rebuild_status(rebuild_side, "stage1_filled")
    if base_audit_submitted:
        return Account05TickResult(
            "submitted",
            "；".join(base_audit_rows) + ";stage1_submitted",
            tuple(base_audit_submitted))
    for lot in ledger.open_lots():
        if not lot.take_profit_algo_id:
            continue
        order = client.order(ACCOUNT05_INSTRUMENT, order_id=lot.take_profit_algo_id)
        cumulative = Decimal(str(order.get("accFillSz") or "0"))
        updated = ledger.sync_take_profit_fill(lot.lot_id, cumulative)
        if lot.kind == "addon" and updated.status == "closed":
            addon_closed_sides.add(lot.side)
        elif lot.kind == "base" and updated.status == "closed":
            base_closed_sides.add(lot.side)
        if str(order.get("state")) in {"canceled", "mmp_canceled"} and cumulative < lot.original_size:
            ledger.mark_take_profit_missing(lot.lot_id)

    # One-time migration of pre-v0.7.255 fixed-point addon TPs.  Cancel only
    # after checking fills, then keep the virtual lot under local MA5 control.
    for lot in ledger.open_lots():
        if lot.kind != "addon":
            continue
        if lot.take_profit_algo_id:
            try:
                order = client.order(ACCOUNT05_INSTRUMENT, order_id=lot.take_profit_algo_id)
            except OkxError as exc:
                if not _order_does_not_exist(exc):
                    raise
                order = {"state": "canceled", "accFillSz": "0"}
            cumulative = Decimal(str(order.get("accFillSz") or "0"))
            if cumulative:
                lot = ledger.sync_take_profit_fill(lot.lot_id, cumulative)
                if lot.status == "closed":
                    addon_closed_sides.add(lot.side)
                    continue
            if str(order.get("state")) not in {"canceled", "mmp_canceled", "filled"}:
                client.cancel_order(ACCOUNT05_INSTRUMENT, lot.take_profit_algo_id)
                try:
                    canceled = client.order(
                        ACCOUNT05_INSTRUMENT, order_id=lot.take_profit_algo_id)
                except OkxError as exc:
                    if not _order_does_not_exist(exc):
                        raise
                else:
                    post_cancel_fill = Decimal(str(canceled.get("accFillSz") or "0"))
                    if post_cancel_fill > cumulative:
                        lot = ledger.sync_take_profit_fill(lot.lot_id, post_cancel_fill)
                        if lot.status == "closed":
                            addon_closed_sides.add(lot.side)
                            continue
                    if str(canceled.get("state")) not in {"canceled", "mmp_canceled"}:
                        raise OkxError("账户05旧小单固定止盈未确认撤销；保留原状态并停止对账")
            ledger.mark_addon_ma5_managed(lot.lot_id)
        elif lot.status == "filled_unprotected":
            ledger.mark_addon_ma5_managed(lot.lot_id)

    addon_closed_sides.update(_reconcile_addon_profit_limits(client, ledger, spec))

    # Repair a durable pre-v0.7.268 51169 intent before evaluating a new exit.
    for lot in ledger.open_lots():
        intent = ledger.order_intent(f"exit:{lot.lot_id}")
        if (lot.kind == "addon" and intent is not None
                and str(intent["status"]) == "unknown"
                and _reduce_side_already_flat(OkxError(str(intent["detail"] or "")))):
            _close_addon_on_ma5(client, ledger, lot, "启动遗留51169对账")
            addon_closed_sides.add(lot.side)

    exit_candidates = []
    for lot in ledger.open_lots():
        if lot.kind != "addon":
            continue
        if ledger.order_intent(f"exit:{lot.lot_id}") is not None:
            continue
        reason = _addon_ma5_exit_reason(
            lot, one, five, config.addon_take_profit_points)
        if reason:
            current_net = _local_addon_net(lot, mark, spec)
            if (current_net <= 0 or not addon_take_profit_unlocked(
                    lot.entry_price, mark, lot.side,
                    config.addon_take_profit_points)):
                # The bar may have qualified before the live mark retraced.
                # Floating losses, including fee-adjusted losses, remain open.
                continue
            exit_candidates.append((lot, reason, current_net))

    # One fresh market event may close one qualifying profitable addon.
    # Neither the recovery pool nor another winner authorizes a loss exit.
    event_bar = str(one.sort_values("date").drop_duplicates("date", keep="last").iloc[-1]["date"])
    profitable = sorted((item for item in exit_candidates if item[2] > 0),
                        key=lambda item: item[2], reverse=True)
    if profitable and signals.extreme_rotation is None:
        winner = profitable[0]
        if ledger.claim_addon_exit_event(winner[0].side, f"profit:{event_bar}"):
            order_id = _submit_addon_profit_limit(
                client, ledger, winner[0], mark, spec,
                config.addon_take_profit_points, winner[1])
            if order_id:
                order_ids.append(order_id)
    addon_exit_submitted = bool(order_ids)
    _reprice_base_take_profits_after_position_change(
        client, ledger, config, addon_closed_sides)
    order_ids.extend(_restore_unprotected_base_take_profits(
        client, ledger, positions))
    unprotected = ledger.unprotected_lots()
    if unprotected:
        details = "；".join(
            f"批次={lot.lot_id},类型={lot.kind},方向={lot.side.value},剩余={lot.remaining_size}"
            for lot in unprotected[:10])
        raise OkxError("账户05存在已成交但未绑定独立止盈的批次；停止新开仓并先对账；" + details)
    # A restored TP can return immediately unless this same tick confirmed a
    # full base TP fill.  In that case reconciliation must continue: aggregate
    # exchange size may be supplied only by add-ons, so the missing base leg
    # still needs to be rebuilt.
    if order_ids and not addon_exit_submitted and not base_closed_sides:
        return Account05TickResult(
            "submitted", "基础仓独立止盈已恢复",
            tuple(order_ids))
    # Maintain one base hedge leg on each side. A missing side after TP is
    # reopened from the current 15m structure allocation.
    bar_key = str(fifteen.sort_values("date").iloc[-1]["date"])
    for side in ((PositionSide.LONG, PositionSide.SHORT)
                 if base_rebuild_enabled else ()):
        # Aggregate OKX positions can remain because 0.1% add-ons are still
        # open even after the full base TP filled.  Base existence must come
        # from the isolated lot ledger, not merely from a non-zero side pos.
        open_base = any(lot.kind == "base" for lot in ledger.open_lots(side))
        tracked_base = ledger.has_base_history(side)
        exchange_side_size = abs(Decimal(str(
            positions.get(side, {}).get("pos") or "0")))
        # The exchange aggregate position is authoritative after a manual
        # full reduction.  Any virtual addon lots on a now-flat side can no
        # longer be paired or protected, so retire them automatically.  This
        # is local bookkeeping only and never sends a cancel/close request.
        if exchange_side_size <= 0:
            for stale in list(ledger.open_lots(side)):
                if stale.kind == "addon":
                    ledger.delete_lot(stale.lot_id)
        # A zero exchange position is authoritative.  Do not let a stale
        # local open lot suppress the required base rebuild.
        if exchange_side_size <= 0:
            for stale in ledger.open_lots(side):
                if stale.kind == "base":
                    ledger.mark_lot_reconcile_required(stale.lot_id)
        base_lots = [lot for lot in ledger.open_lots(side) if lot.kind == "base"]
        # OKX exposes one aggregate position per side. Historical virtual
        # base rows can outlive their exchange quantity, so summing every old
        # row makes a healthy current base look permanently missing. The
        # newest base row owns existence; add-ons still cannot prove a base.
        newest_base = base_lots[-1] if base_lots else None
        open_base_size = (newest_base.remaining_size if newest_base is not None
                          else Decimal("0"))
        base_covered = open_base_size > 0 and exchange_side_size >= open_base_size
        if open_base and base_covered:
            staged = ledger.base_rebuild_plan(side)
            if staged is not None and str(staged["status"]) == "stage1_planned":
                ledger.set_base_rebuild_status(side, "stage1_filled")
            continue
        if not open_base and not tracked_base and exchange_side_size > 0:
            continue
        # A fresh extreme reversal owns its first entry on a side that has
        # never had a base.  A previously closed base, however, must be
        # rebuilt before another addon event can return from this tick.
        if signals.extreme_rotation is not None and not tracked_base:
            continue
        # Both directions always target 1.2% and build in two independent
        # 0.6% stages. Stage two belongs to a fresh precise structure below.
        target_margin_pct = Decimal("0.012")
        margin_pct = Decimal("0.006")
        signal_key = f"base-rebuild-stage1:{side.value}:{bar_key}"
        plan = ledger.plan_base_rebuild(
            side=side, signal_key=signal_key,
            target_margin_pct=target_margin_pct,
            first_margin_pct=margin_pct,
            take_profit_pct=selected_base_pct)
        signal_id = str(plan["signal_key"])
        notional = capital * margin_pct * config.leverage
        size = contracts_for_notional(notional, mark, spec)
        if size < spec.min_size:
            raise OkxError(f"账户05{side.value}基础仓换算后低于交易所最小张数")
        order_ids.extend(_submit_entry(
            client, ledger, signal_id=signal_id, side=side, kind="base",
            size=size, spec=spec, config=config,
            base_take_profit_pct=selected_base_pct,
            entry_evidence=evidence("基础仓第一阶段", "双向基础仓建立/归零重建；不属于六类精准入场")))
        ledger.set_base_rebuild_status(side, "stage1_filled")

    if order_ids:
        return Account05TickResult(
            "submitted", f"5分钟={signals.trend_5m.value}；基础双向仓/缺失方向已建立并挂独立止盈",
            tuple(order_ids))

    # Stage two owns one price-advantaged pullback per rebuild, independently of addons.
    for side in ((PositionSide.LONG, PositionSide.SHORT)
                 if base_rebuild_enabled else ()):
        rebuild = ledger.base_rebuild_plan(side)
        if rebuild is None or str(rebuild['status']) != 'stage1_filled':
            continue
        base = [lot for lot in ledger.open_lots(side) if lot.kind == 'base']
        if not base:
            continue
        first = base[-1]
        anchor = base_pullback_confirmation(
            one, side.value, first.entry_price, rebuild['updated_at_utc'])
        if not anchor:
            continue
        remaining = Decimal(rebuild['target_margin_pct'])-Decimal(rebuild['first_margin_pct'])
        size = contracts_for_notional(capital*remaining*config.leverage, mark, spec)
        if size < spec.min_size:
            continue
        signal_id = 'base-pullback-stage2:' + str(rebuild['signal_key'])
        submitted = _submit_entry(
            client, ledger, signal_id=signal_id, side=side, kind='base', size=size,
            spec=spec, config=config, base_take_profit_pct=Decimal(rebuild['take_profit_pct']),
            entry_evidence=evidence('基础仓局部回踩补满',
                '独立第二阶段：局部摆动回撤达到ATR门槛，已收盘止跌/止涨且价格仍靠近回撤区；不要求优于首笔，非六类入口'))
        if submitted:
            ledger.set_base_rebuild_status(side, 'complete')
            _reprice_base_take_profits_after_position_change(client, ledger, config, {side})
            return Account05TickResult('submitted', '基础仓第二阶段在有利价格补满', tuple(submitted))

    # Independent 1m+5m terminal rotation. Only qualifying profitable
    # source lots may be released; losing lots remain open as floating loss.
    extreme = signals.extreme_rotation
    if extreme is None and config.experimental_fast_top_enabled:
        closed = one.sort_values('date').drop_duplicates('date').iloc[:-1]
        candidates = top_entries(closed, TopParameters(1.5, .65, 3))
        if candidates and candidates[-1][0] == closed.iloc[-1]['date']:
            when, price, peak, key = candidates[-1]
            extreme = ExtremeRotationTrigger(-1, str(when),
                f'实验冲顶回落：峰值{peak}，确认{price}；未通过盈利验证',
                'fast-top:' + str(key))
    if extreme is not None:
        reverse_side = (PositionSide.LONG if extreme.direction > 0
                        else PositionSide.SHORT)
        source_side = (PositionSide.SHORT if reverse_side is PositionSide.LONG
                       else PositionSide.LONG)
        # This reversal is owned by the extreme event, not by the ordinary
        # 18-slot add-on inventory. It remains executable when there is no
        # profitable source lot to release or the reverse-side slots are full.
        reverse_position = positions.get(reverse_side, {})
        reverse_margin = (abs(Decimal(str(reverse_position.get("pos") or "0")))
                          * spec.ct_val * spec.ct_mult * mark / config.leverage)
        extreme_risk_allowed = addon_permission(
            config, addon_count=ledger.open_addon_count(reverse_side),
            slot_capacity=(config.long_slot_capacity if reverse_side is PositionSide.LONG
                           else config.short_slot_capacity),
            side_margin_used_usdt=reverse_margin,
            combined_unrealized_pnl_usdt=combined_upl).allowed
        reverse_plan = extreme_rotation_entry_plan(
            config, reverse_side, mark, spec)
        event_key = f"extreme:{extreme.direction}:{extreme.structure_key or extreme.anchor_time}"
        if (extreme_risk_allowed and reverse_plan.contracts >= spec.min_size
                and ledger.claim_extreme_rotation_event(
                    event_key, extreme.direction)):
            if hasattr(client, "orders_history"):
                import_manual_orders(client, ledger, profit_points=config.addon_take_profit_points,
                                     require_fresh=True)
            if ledger.connection.execute(
                    "SELECT 1 FROM account05_lots WHERE side=? AND kind='addon' AND status='reconcile_required'",
                    (source_side.value,)).fetchone():
                raise OkxError("极值原方向小单仍在隔离对账，不开反手")
            source_lots = [lot for lot in ledger.open_lots(source_side) if lot.kind == "addon"]
            current_positions = _position_map(client.raw_snapshot())
            covered = sum((lot.remaining_size for lot in ledger.open_lots(source_side)), Decimal(0))
            actual = abs(Decimal(str(current_positions.get(source_side, {}).get("pos") or "0")))
            if covered > actual:
                raise OkxError("极值方向本地批次超过实际仓位；先对账，不平仓或反手")
            quote = client.exit_quote(source_side.value) if hasattr(client, "exit_quote") else mark
            profitable = [lot for lot in source_lots
                          if _local_addon_net(lot, quote, spec) > 0
                          and addon_take_profit_unlocked(lot.entry_price, quote, lot.side,
                                                         config.addon_take_profit_points)]
            signal_id = (f"extreme-rotation:{'bottom-long' if extreme.direction > 0 else 'top-short'}:"
                         f"{reverse_side.value}:{extreme.anchor_time}")
            targets, signal_id = ledger.freeze_extreme_targets(event_key, profitable, signal_id)
            changed_sides: set[PositionSide] = set(addon_closed_sides)
            for lot_id, target_size in targets:
                lot = ledger.get_lot(lot_id)
                if lot.remaining_size > target_size or lot.status == "reconcile_required":
                    raise OkxError("极值待平批次数量/状态变化，先对账，不反手")
                _close_extreme_winner(client, ledger, lot, spec, config.addon_take_profit_points,
                                     mark, f"{extreme.reason}；极值先平全部合格盈利小单")
                changed_sides.add(source_side)
                fills = ledger.connection.execute(
                    "SELECT order_id FROM account05_extreme_exit_fills WHERE lot_id=?", (lot_id,))
                order_ids.extend(str(row[0]) for row in fills)
            # The frozen set survives restart; never open the reverse merely
            # because a pending/unknown exit was filtered out of candidates.
            if any(ledger.get_lot(lot_id).status != "closed" for lot_id, _ in targets):
                raise OkxError("极值盈利单未全部确认平仓，不开反手")

            submitted = _submit_entry(
                client, ledger, signal_id=signal_id, side=reverse_side,
                kind="addon", size=reverse_plan.contracts,
                spec=spec, config=config,
                entry_evidence=evidence("大极值反手", extreme.reason), resume_existing=True)
            order_ids.extend(submitted)
            changed_sides.add(reverse_side)
            _reprice_base_take_profits_after_position_change(
                client, ledger, config, changed_sides)
            ledger.complete_extreme_rotation_event(event_key)
            return Account05TickResult(
                "submitted",
                f"{extreme.reason}；已确认先平全部合格盈利小单{len(targets)}笔（无合格盈利单也可反手）；"
                "亏损小单保持持仓；"
                f"反手{reverse_side.value}固定={reverse_plan.contracts}张；计入同侧槽位",
                tuple(order_ids))

    if ledger.connection.execute(
            "SELECT 1 FROM account05_extreme_rotation_events WHERE status='claimed' AND plan_recorded=1").fetchone():
        return Account05TickResult("observe", "极值平仓/反手组尚未完成，等待原事件恢复并对账")

    # Thirty-six per side is a reusable concurrent-slot ceiling, never a fill target. A shape may
    # submit once on its false->true edge and must disappear before rearming.
    present_shapes = tuple(
        (item.identity, PositionSide.LONG if item.direction > 0 else PositionSide.SHORT,
         item.anchor_time)
        for item in signals.triggers)
    one_sorted = one.sort_values("date").drop_duplicates("date", keep="last")
    # The newest row may still be forming.  Use the preceding 1m candle as the
    # rearm clock so intra-candle five-second condition flicker cannot unlock.
    closed_one_minute_bar = str(one_sorted.iloc[-2]["date"])
    five_sorted = five.sort_values("date").drop_duplicates("date", keep="last")
    current_five_minute_bar = str(five_sorted.iloc[-1]["date"])
    # Claim candidates in priority order.  Claiming all at once consumed the
    # three-stage fallback even when the early Supertrend signal won.
    fresh_shapes = set()
    for selected_shape in present_shapes:
        fresh_shapes = ledger.claim_new_signal_shapes(
            (selected_shape,), observation_bar=closed_one_minute_bar,
            five_minute_bar=current_five_minute_bar)
        if fresh_shapes:
            break
    if not present_shapes:
        ledger.claim_new_signal_shapes((), observation_bar=closed_one_minute_bar,
                                       five_minute_bar=current_five_minute_bar)
    trigger = next((item for item in signals.triggers
                    if (item.identity,
                        PositionSide.LONG if item.direction > 0 else PositionSide.SHORT)
                    in fresh_shapes), None)
    if trigger is not None:
        direction = trigger.direction
        side = PositionSide.LONG if direction > 0 else PositionSide.SHORT
        # Trend chase is reserved for the recovery profit-pool inventory:
        # 36 slots per side, 72 slots total.  These addon lots are managed
        # locally and deliberately receive no exchange stop-loss order.
        trend_chase_trigger = trigger.identity in {
            "上涨趋势回踩追多", "下跌趋势反抽追空", "超级趋势支撑早触发追多",
            "5分钟MA20订单块回踩早触发追多", "5分钟高位拒绝早触发追空",
            "5分钟超级趋势首次翻空追空", "1分钟超级趋势首次翻空补漏做空",
            "1分钟超级趋势首次翻空局部反转做空",
            "1分钟超级趋势压力线反抽追空"}
        recovery = ledger.recovery_program()
        recovery_active = recovery is not None and int(recovery["active"]) == 1
        recovery_phase = str(recovery["phase"]) if recovery_active else ""
        if recovery_active and recovery_phase != "adjust":
            recovery = ledger.set_recovery_phase("adjust")
            recovery_phase = "adjust"
        row = positions.get(side, {})
        side_size = abs(Decimal(str(row.get("pos") or "0")))
        side_margin = side_size * spec.ct_val * mark / config.leverage
        permission_config = config
        signal_prefix = "addon"
        if trend_chase_trigger or recovery_phase == "adjust":
            signal_prefix = "recovery-addon"
        permission = addon_permission(
            permission_config, addon_count=ledger.open_addon_count(side),
            slot_capacity=(config.long_slot_capacity if side is PositionSide.LONG
                           else config.short_slot_capacity),
            side_margin_used_usdt=side_margin,
            combined_unrealized_pnl_usdt=combined_upl)
        if permission.allowed:
            if ledger.claim_side_five_entry(side, current_five_minute_bar, trigger.identity):
                plan = (recovery_slot_entry_plan(config, side, mark, spec)
                        if signal_prefix == "recovery-addon"
                        else addon_entry_plan(config, side, mark, spec))
                signal_id = (
                    f"{signal_prefix}:{trigger.identity}:{side.value}:{trigger.anchor_time}")
                submitted = _submit_entry(
                    client, ledger, signal_id=signal_id, side=side, kind="addon",
                    size=plan.contracts, spec=spec, config=config,
                    entry_evidence=evidence(trigger.identity, trigger.reason))
                order_ids.extend(submitted)
                if submitted:
                    # The exchange aggregate average moves as soon as this 0.1%
                    # lot fills. Re-anchor the same-side base target.
                    _reprice_base_take_profits_after_position_change(
                        client, ledger, config, {side})
        elif not order_ids:
            reason = {
                "side_addon_count_limit_reached":
                    f"{side.value}方向当前板块已达{config.max_addons_per_side}笔上限"
                    f"（{'解套0.02张槽位' if signal_prefix == 'recovery-addon' else '独立0.01张小单'}）",
                "side_margin_limit_reached":
                    f"{side.value}方向循环槽位保证金已达运行资金3.6%上限（另加基础仓1.2%）",
                "combined_floating_loss_limit_reached":
                    f"多空合计未实现盈亏={combined_upl:.4f} USDT，"
                    f"停止阈值=-{loss_limit:.2f} USDT",
            }.get(permission.reason_code, permission.reason_code)
            action = ("loss_limit_blocked"
                      if permission.reason_code == "combined_floating_loss_limit_reached"
                      else "capacity_blocked")
            return Account05TickResult(action, reason)
    isolated = ledger.connection.execute(
        """SELECT COUNT(*) FROM account05_order_intents i JOIN account05_lots l
        ON i.signal_id='exit:' || l.lot_id
        WHERE i.status='reconcile_required' AND l.kind='addon' AND l.status<>'closed'""").fetchone()[0]
    reconciliation_note = f"；小单退出待对账{isolated}笔（保留持仓，不重复下单）" if isolated else ""
    return Account05TickResult(
        "submitted" if order_ids else "observe",
        f"5分钟={signals.trend_5m.value}；触发="
        f"{trigger.identity if trigger else '无'}；{trigger.reason if trigger else signals.trend_reason}" + reconciliation_note,
        tuple(order_ids),
    )
