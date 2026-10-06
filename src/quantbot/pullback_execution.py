"""Short-lived, single-threaded monitoring of explicitly registered MA5 orders.

Cancel acknowledgement is never fill evidence. A replacement is attempted once,
only after an exact terminal order query proves zero cumulative fills.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time

from .review_quality import estimated_roundtrip_points
from .state import StateStore

PULLBACK_TTL_SECONDS = 300

@dataclass(frozen=True)
class PullbackResult:
    action: str = "observe"
    reason: str = "no registered MA5 pullback order"
    order_id: str = ""


def _table(store):
    store.connection.execute("""CREATE TABLE IF NOT EXISTS ma5_pullback_execution(
        client_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL, previous_price REAL, previous_ts INTEGER)""")
    store.connection.commit()


def register_pullback(store, *, client_id, order_id, instrument, direction,
                      entry, stop, target, atr, size, version, signal_time,
                      created_ms=None):
    """Register accepted orders only; old builds' orders cannot be adopted."""
    _table(store)
    payload = dict(instrument=instrument, direction=direction, entry=entry,
                   stop=stop, target=target, atr=atr, size=str(size), version=version,
                   signal_time=signal_time,
                   expires_ms=(created_ms or int(time.time() * 1000)) + PULLBACK_TTL_SECONDS * 1000)
    store.connection.execute(
        "INSERT OR IGNORE INTO ma5_pullback_execution VALUES(?,?,?,'armed',NULL,NULL)",
        (client_id, order_id, json.dumps(payload)))
    store.connection.commit()


def _open_filled_pullback_lifecycle(store, client_id, order_id, order, plan):
    entry = float(order.get("avgPx") or order.get("fillPx") or plan["entry"])
    return store.open_trade_lifecycle_if_missing(
        trade_uid=client_id, strategy_id="strategy_01", strategy_version=plan["version"],
        instrument=plan["instrument"], direction=int(plan["direction"]),
        signal_time=plan["signal_time"],
        signal_reason="原MA5回踩限价已经成交；按冻结结构接管持仓与平仓对账",
        signal_context={"resting_fill": True, "original_order_id": order_id,
                        "original_limit": plan["entry"], "frozen_stop": plan["stop"],
                        "frozen_target": plan["target"],
                        "winning_trigger_template": (
                            "pullback_long" if int(plan["direction"]) > 0
                            else "throwback_short")},
        order_id=order_id, algo_id="", entry_reference=entry,
        stop_price=float(plan["stop"]), trailing_activation=float(plan["target"]),
        trailing_callback=0.0, branch="missed_ma5_pullback_limit_fill",
    )


def _state(store, client_id, state, reason):
    store.connection.execute("UPDATE ma5_pullback_execution SET state=? WHERE client_id=?",
                             (state, client_id))
    store.connection.commit()
    store.record_event("ma5_pullback_" + state, {"clOrdId": client_id, "reason": reason})


def near_pullback_quote(plan, ticker, now_ms, previous_price=None, previous_ts=None,
                        *, require_approach=True):
    """Executable side, fresh approach, unchanged stop and target, symmetric gates."""
    try:
        direction = int(plan["direction"])
        entry, stop, target, atr = (float(plan[k]) for k in ("entry", "stop", "target", "atr"))
        bid, ask = float(ticker["bidPx"]), float(ticker["askPx"])
        ts = int(ticker["ts"])
        if direction not in (-1, 1) or not all(math.isfinite(v) and v > 0
                                               for v in (entry, stop, target, atr, bid, ask)):
            return False, 0., "invalid quote or frozen structure"
        if ticker.get("instId") != plan["instrument"] or not 0 <= now_ms - ts <= 2000:
            return False, 0., "stale or wrong-instrument quote"
        if now_ms >= plan["expires_ms"] or not 0 <= ask - bid <= .10:
            return False, 0., "expired order or wide spread"
        price = ask if direction > 0 else bid
        gap = direction * (price - entry)
        tolerance = min(.20, .15 * atr)
        if not 0 < gap <= tolerance:
            return False, price, "not just outside the resting limit"
        if require_approach and (previous_price is None or previous_ts is None
                or not 0 < ts - previous_ts <= 5000
                or direction * (price - previous_price) >= 0):
            return False, price, "need two fresh quotes moving toward the limit"
        risk, reward = direction * (price - stop), direction * (target - price)
        if not 0 < risk <= 2 * atr:
            return False, price, "frozen structural stop is invalid or too far"
        # This is completion of an already accepted resting plan, whose floor
        # is 1.2R. Re-price that same plan including taker costs, rather than
        # widening its target to manufacture a new market-entry opportunity.
        # Ordinary fresh market signals retain their separate 1.8R/3-cost gate.
        if (reward < 1.2 * risk
                or reward < 3.0 * estimated_roundtrip_points(price)):
            return False, price, "frozen target leaves insufficient reward after costs"
        return True, price, "fresh retracement within MA5 limit tolerance"
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, 0., "incomplete quote or frozen structure"


def _get_order(client, plan, order_id, client_id):
    response = client._request("GET", "/api/v5/trade/order", {
        "instId": plan["instrument"], "ordId": order_id}, private=True)
    rows = response.get("data") or []
    if str(response.get("code")) != "0" or len(rows) != 1:
        raise ValueError("order query did not resolve exactly one order")
    order = rows[0]
    expected_side = "buy" if plan["direction"] > 0 else "sell"
    if (order.get("ordId") != order_id or order.get("clOrdId") != client_id
            or order.get("instId") != plan["instrument"]
            or order.get("side") != expected_side
            or order.get("posSide") != ("long" if plan["direction"] > 0 else "short")
            or float(order["sz"]) != float(plan["size"])
            or float(order["px"]) != float(plan["entry"])):
        raise ValueError("original order identity or size changed")
    # A missing/NaN fill quantity is uncertainty, never a zero fill.
    fills = float(order["accFillSz"])
    if not math.isfinite(fills) or fills < 0:
        raise ValueError("invalid cumulative fills")
    return order


def _quote(client, plan):
    response = client._request("GET", "/api/v5/market/ticker", {"instId": plan["instrument"]})
    rows = response.get("data") or []
    if str(response.get("code")) != "0" or len(rows) != 1:
        raise ValueError("ticker unavailable")
    return rows[0]


def _flat_and_clear(snapshot, original_id):
    # Any active position returns control to the ordinary protection manager.
    return (all(float(p["pos"]) == 0 for p in snapshot["positions"])
            and all(o.get("ordId") == original_id for o in snapshot["orders"]))


def poll_pullback(client, store, row, *, can_enter, stopped, clock_ms):
    """One bounded observation; persistent CAS prevents replacement replays."""
    cid, oid = row["client_id"], row["order_id"]
    plan = json.loads(row["payload"])
    now = clock_ms()
    if stopped():
        return PullbackResult("stopped", "automatic execution stopped")
    order = _get_order(client, plan, oid, cid)
    created = int(order["cTime"])
    if created <= 0:
        raise ValueError("missing exchange order creation time")
    plan["expires_ms"] = min(plan["expires_ms"], created + PULLBACK_TTL_SECONDS * 1000)
    if float(order["accFillSz"]) > 0:
        _open_filled_pullback_lifecycle(store, cid, oid, order, plan)
        _state(store, cid, "filled", "original has fills; never market-replace any remainder")
        return PullbackResult("manage", "original limit has fills", oid)
    if order.get("state") != "live":
        _state(store, cid, "closed", "original is no longer live")
        return PullbackResult("manage", "original no longer live", oid)
    if now >= plan["expires_ms"]:
        _state(store, cid, "expired", "300 second window elapsed; cancel without replacement")
        client.cancel_orders([order])
        return PullbackResult("expired", "pullback waiting window elapsed", oid)
    if not _flat_and_clear(client.safety_snapshot(), oid):
        return PullbackResult("manage", "return to position/protection management", oid)
    ticker = _quote(client, plan)
    now = clock_ms()
    quote_price = float(ticker["askPx"] if plan["direction"] > 0 else ticker["bidPx"])
    if (ticker.get("instId") == plan["instrument"]
            and 0 <= now - int(ticker["ts"]) <= 2000
            and plan["direction"] * (quote_price - plan["stop"]) <= 0):
        _state(store, cid, "invalidated", "price broke original stop; cancel without replacement")
        client.cancel_orders([order])
        return PullbackResult("manage", "original reversal structure invalidated", oid)
    allowed, price, reason = near_pullback_quote(
        plan, ticker, now, row["previous_price"], row["previous_ts"])
    if price and 0 <= now - int(ticker["ts"]) <= 2000:
        store.connection.execute("""UPDATE ma5_pullback_execution
            SET previous_price=?,previous_ts=? WHERE client_id=? AND state='armed'""",
            (price, int(ticker["ts"]), cid))
        store.connection.commit()
    if not allowed:
        return PullbackResult("waiting", reason, oid)
    if stopped() or not can_enter():
        return PullbackResult("manage", "automatic execution or daily risk gate blocked", oid)
    # Durable claim BEFORE the first POST. Restarts never retry uncertain writes.
    claimed = store.connection.execute("""UPDATE ma5_pullback_execution
        SET state='cancel_requested' WHERE client_id=? AND state='armed'""", (cid,))
    store.connection.commit()
    if claimed.rowcount != 1:
        return PullbackResult("manage", "conversion already claimed", oid)
    response = client.cancel_orders([order])
    data = response.get("data") or []
    if (str(response.get("code")) != "0" or len(data) != 1
            or data[0].get("ordId") != oid or str(data[0].get("sCode")) != "0"):
        _state(store, cid, "blocked", "cancellation not accepted; no replacement")
        return PullbackResult("manage", "cancellation not accepted", oid)
    # Acknowledgement alone is insufficient, including when the order vanishes
    # from orders-pending. Only terminal canceled plus explicit zero fills passes.
    order = _get_order(client, plan, oid, cid)
    if order.get("state") != "canceled" or float(order["accFillSz"]) != 0:
        _state(store, cid, "blocked", "cancel not terminal or raced with a fill")
        return PullbackResult("manage", "cancel/fill race; no replacement", oid)
    if not _flat_and_clear(client.safety_snapshot(), ""):
        _state(store, cid, "blocked", "position or another order appeared")
        return PullbackResult("manage", "new account exposure; no replacement", oid)
    if stopped() or not can_enter():
        _state(store, cid, "blocked", "risk or stop switch changed after cancellation")
        return PullbackResult("manage", "risk gate changed; no replacement", oid)
    fresh = _quote(client, plan)
    allowed, price, reason = near_pullback_quote(plan, fresh, clock_ms(), require_approach=False)
    if not allowed or stopped():
        _state(store, cid, "blocked", "post-cancel recheck: " + reason)
        return PullbackResult("manage", "price moved after cancellation; no chase", oid)
    replacement_id = cid + "M"
    _state(store, cid, "submitting", "zero-fill cancel confirmed; single market attempt")
    response = client.place_demo_market_order(
        "buy" if plan["direction"] > 0 else "sell", 1, str(plan["stop"]),
        enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
        inst_id=plan["instrument"], client_order_id=replacement_id,
        position_side="long" if plan["direction"] > 0 else "short",
        take_profit_price=str(plan["target"]), stop_loss_trigger_type="mark")
    data = response.get("data") or []
    if (str(response.get("code")) != "0" or len(data) != 1
            or str(data[0].get("sCode")) != "0" or not data[0].get("ordId")):
        raise ValueError("replacement acknowledgement uncertain; reconcile, never resubmit")
    new_id = str(data[0]["ordId"])
    _state(store, cid, "submitted", "market replacement accepted: " + new_id)
    store.open_trade_lifecycle(
        trade_uid=replacement_id, strategy_id="strategy_01", strategy_version=plan["version"],
        instrument=plan["instrument"], direction=plan["direction"],
        signal_time=plan["signal_time"], signal_reason=reason,
        signal_context={"original_order_id": oid, "original_limit": plan["entry"],
                        "quote_ts": fresh["ts"], "frozen_stop": plan["stop"],
                        "conversion": "confirmed_zero_fill_cancel_then_market"},
        order_id=new_id, algo_id="", entry_reference=price, stop_price=plan["stop"],
        trailing_activation=plan["target"], trailing_callback=0,
        branch="missed_ma5_pullback_near_market")
    return PullbackResult("submitted", "回抽接近限价，已确认原单零成交撤销后转市价；保护价保持冻结", new_id)


def monitor_pullbacks(client, database, *, stop_event, can_enter,
                      clock_ms=lambda: int(time.time() * 1000)):
    """Use the execution thread, not a second writer; stop waits are interruptible.

    Only one registered order and a flat account can enter this short watch.
    Existing positions immediately return to the usual exit/protection loop.
    """
    store = StateStore(database)
    try:
        _table(store)
        for _ in range(PULLBACK_TTL_SECONDS + 5):
            rows = store.connection.execute(
                "SELECT * FROM ma5_pullback_execution WHERE state='armed'").fetchall()
            if len(rows) != 1:
                return PullbackResult()
            result = poll_pullback(client, store, rows[0], can_enter=can_enter,
                                   stopped=stop_event.is_set, clock_ms=clock_ms)
            if result.action != "waiting":
                return result
            if stop_event.wait(1):
                return PullbackResult("stopped", "automatic execution stopped")
        return PullbackResult("manage", "bounded pullback watch completed")
    finally:
        store.close()
