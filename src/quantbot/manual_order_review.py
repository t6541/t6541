"""Read-only manual-order review; quantity-aware FIFO is an audit inference."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

AUTO_PREFIXES = ("qbot", "a5en", "a5tp", "a5ex")


def _number(value) -> Decimal:
    try:
        result = Decimal(str(value or "0"))
        return result if result.is_finite() and result > 0 else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _timestamp(row) -> int:
    value = row.get("ts") or row.get("fillTime") or row.get("uTime") or row.get("cTime") or 0
    return int(value) if str(value).isdigit() else 0


def _when(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, timezone(timedelta(hours=8))).strftime(
        "%Y-%m-%d %H:%M:%S") if ts else "—"


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def load_manual_order_history(client, inst_id: str, *, max_pages: int = 10):
    """Fetch bounded recent/archive history and fills; return coverage warning."""
    orders, fills, warnings = [], [], []
    successful_reads = 0
    for archive in (False, True):
        after = ""
        for _ in range(max_pages):
            try:
                page = client.order_history_page(inst_id, archive=archive, after=after)
            except Exception as exc:
                warnings.append(f"{'归档' if archive else '最近'}订单历史读取失败：{exc}")
                break
            successful_reads += 1
            orders.extend(page)
            if len(page) < 100:
                break
            cursor = str(page[-1].get("ordId") or "")
            if not cursor or cursor == after:
                warnings.append("订单历史游标未推进")
                break
            after = cursor
        else:
            warnings.append("订单历史达到查询上限")
    after = ""
    for _ in range(max_pages):
        try:
            page = client.fills_history(inst_id, after=after, strict=True)
        except Exception as exc:
            warnings.append(f"成交历史读取失败：{exc}")
            break
        successful_reads += 1
        fills.extend(page)
        if len(page) < 100:
            break
        # OKX fills-history paginates by billId, not execution timestamp.
        cursor = str(page[-1].get("billId") or "")
        if not cursor or cursor == after:
            warnings.append("成交历史游标未推进")
            break
        after = cursor
    else:
        warnings.append("成交历史达到查询上限")
    if not successful_reads:
        raise RuntimeError("；".join(dict.fromkeys(warnings)))
    return orders, fills, "；".join(dict.fromkeys(warnings))


def manual_order_rows(orders: list[dict], fills: list[dict], *, limit: int = 20):
    """Return latest executed manual entries/reductions, capped after matching.

    All opening orders (including automatic ones) consume closing quantities.
    No close quantity is reused. Multiple close IDs and weighted prices are
    shown for partial closes. Missing history stays explicitly unmatched.
    """
    by_order = {}
    for row in orders:
        oid = str(row.get("ordId") or "")
        if oid:
            by_order.setdefault(oid, dict(row))
    grouped = defaultdict(list)
    seen = set()
    for fill in fills:
        oid = str(fill.get("ordId") or "")
        key = (oid, str(fill.get("tradeId") or fill.get("billId") or ""),
               str(fill.get("ts") or ""), str(fill.get("fillPx") or ""),
               str(fill.get("fillSz") or ""))
        if not oid or key in seen:
            continue
        seen.add(key)
        grouped[oid].append(fill)
        row = by_order.setdefault(oid, {"ordId": oid, "state": "filled"})
        for field in ("clOrdId", "side", "posSide", "reduceOnly", "instId"):
            if row.get(field) in (None, "") and fill.get(field) not in (None, ""):
                row[field] = fill[field]
        row.setdefault("ordType", fill.get("execType") or "market")

    events = []
    for oid, row in by_order.items():
        executions = grouped[oid]
        total = sum((_number(x.get("fillSz")) for x in executions), Decimal(0))
        size = _number(row.get("accFillSz"))
        price = _number(row.get("avgPx")) or _number(row.get("fillPx"))
        if size and total < size:
            # Incomplete fills page: prefer authoritative cumulative order data.
            executions = [dict(row, fillSz=size, fillPx=price)] if price else []
        elif not executions and size and price:
            executions = [dict(row, fillSz=size, fillPx=price)]
        for fill in executions:
            quantity, px, ts = _number(fill.get("fillSz")), _number(fill.get("fillPx")), _timestamp(fill)
            side = str(row.get("side") or "").lower()
            pos = str(row.get("posSide") or "").lower()
            if not quantity or not px or not ts or side not in {"buy", "sell"}:
                continue
            reducing = (str(row.get("reduceOnly")).lower() == "true"
                        or (pos == "long" and side == "sell")
                        or (pos == "short" and side == "buy"))
            logical = pos if pos in {"long", "short"} else (
                ("long" if side == "sell" else "short") if reducing
                else ("long" if side == "buy" else "short"))
            events.append(dict(row=row, oid=oid, qty=quantity, px=px, ts=ts,
                               reducing=reducing, logical=logical))
    events.sort(key=lambda x: (x["ts"], x["oid"]))
    queues = defaultdict(deque)
    entries, unmatched = {}, []
    for event in events:
        row, oid = event["row"], event["oid"]
        manual = not str(row.get("clOrdId") or "").lower().startswith(AUTO_PREFIXES)
        if not event["reducing"]:
            entry = entries.setdefault(oid, dict(row=row, manual=manual,
                logical=event["logical"], ts=event["ts"], latest=event["ts"],
                qty=Decimal(0), cost=Decimal(0), closed=Decimal(0),
                close_cost=Decimal(0), close_ts=0, ids=[]))
            entry["qty"] += event["qty"]
            entry["cost"] += event["qty"] * event["px"]
            entry["latest"] = max(entry["latest"], event["ts"])
            queues[event["logical"]].append([entry, event["qty"]])
            continue
        left = event["qty"]
        queue = queues[event["logical"]]
        while left and queue:
            entry, available = queue[0]
            taken = min(left, available)
            entry["closed"] += taken
            entry["close_cost"] += taken * event["px"]
            entry["close_ts"] = max(entry["close_ts"], event["ts"])
            entry["latest"] = max(entry["latest"], event["ts"])
            if oid not in entry["ids"]:
                entry["ids"].append(oid)
            left -= taken
            queue[0][1] -= taken
            if not queue[0][1]:
                queue.popleft()
        if left and manual:
            unmatched.append((event, left))
    result = []
    for oid, entry in entries.items():
        if not entry["manual"]:
            continue
        closed = entry["closed"]
        status = ("已平仓（FIFO）" if closed == entry["qty"] else
                  f"部分平仓 {_text(closed)}/{_text(entry['qty'])}（FIFO）" if closed else
                  "未匹配平仓（历史范围内）")
        result.append((entry["latest"], oid, (
            _when(entry["ts"]), _when(entry["close_ts"]), "手工开仓",
            "多" if entry["logical"] == "long" else "空",
            str(entry["row"].get("ordType") or "—"), _text(entry["qty"]),
            _text(entry["cost"] / entry["qty"]),
            _text(entry["close_cost"] / closed) if closed else "—", oid,
            "、".join(entry["ids"]) or "—", status)))
    # Group unmatched split fills into one reduction order, not duplicate rows.
    reductions = {}
    for event, qty in unmatched:
        item = reductions.setdefault(event["oid"], [event, Decimal(0), Decimal(0)])
        item[0] = event
        item[1] += qty
        item[2] += qty * event["px"]
    for oid, (event, qty, cost) in reductions.items():
        result.append((event["ts"], oid, (
            "—", _when(event["ts"]), "手工减仓",
            "多" if event["logical"] == "long" else "空",
            str(event["row"].get("ordType") or "—"), _text(qty), "—",
            _text(cost / qty), "—", oid, "未匹配开仓（历史范围内）")))
    result.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in result[:max(0, limit)]]
