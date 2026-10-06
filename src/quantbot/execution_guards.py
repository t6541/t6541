from __future__ import annotations


CORE_DIRECTION_TIMEFRAMES = ("5m", "15m", "1H")
PRIMARY_ALIGNMENT_TIMEFRAMES = ("5m", "15m", "1H")


def blocks_early_countertrend_reversal(directions: dict[str, int], direction: int) -> bool:
    """Protect ordinary early reversals only when both core trends oppose.

    The final v0.7.35 entry gate is owned by the confirmed 5m trigger and the
    option-C higher-timeframe context rule.  1m/15m/1H/4H never override a
    failed 5m primary trigger here; 30m is removed.
    """
    return direction in {-1, 1} and all(
        int(directions.get(tf, 0)) == -direction for tf in CORE_DIRECTION_TIMEFRAMES
    )


def primary_timeframes_aligned(directions: dict[str, int], direction: int) -> bool:
    return direction in {-1, 1} and all(
        int(directions.get(tf, 0)) == direction for tf in PRIMARY_ALIGNMENT_TIMEFRAMES
    )


def six_timeframes_aligned(directions: dict[str, int], direction: int) -> bool:
    """Backward-compatible name for stored lifecycle/report consumers."""
    return primary_timeframes_aligned(directions, direction)


def ordinary_stop_is_too_close(entry: float, direction: int, stop: float, atr_1m: float) -> bool:
    if direction not in {-1, 1} or min(entry, stop, atr_1m) <= 0:
        return True
    risk = (entry - stop) if direction > 0 else (stop - entry)
    return risk < max(entry * .0020, atr_1m * .80)


def matched_closing_fills(row, fills: list[dict]) -> tuple[list[dict], list[dict]]:
    """Match only the earliest closing quantity belonging to one entry."""
    uid = str(row["trade_uid"])
    entry = sorted(
        (x for x in fills if str(x.get("clOrdId", "")) == uid),
        key=lambda x: (int(x.get("ts") or 0), str(x.get("tradeId") or "")),
    )
    required = sum(float(x.get("fillSz") or 0) for x in entry)
    if required <= 0:
        return entry, []
    entry_time = max(int(x.get("ts") or 0) for x in entry)
    side = "sell" if int(row["direction"]) > 0 else "buy"
    pos_side = "long" if int(row["direction"]) > 0 else "short"
    candidates = sorted((x for x in fills if int(x.get("ts") or 0) >= entry_time
                         and str(x.get("clOrdId", "")) != uid
                         and x.get("side") == side and x.get("posSide") == pos_side),
                        key=lambda x: (int(x.get("ts") or 0), str(x.get("tradeId") or "")))
    result, remaining = [], required
    for original in candidates:
        size = float(original.get("fillSz") or 0)
        if size <= 0:
            continue
        fraction = min(1.0, remaining / size)
        item = dict(original)
        if fraction < 1:
            item["fillSz"] = remaining
            item["fillPnl"] = float(original.get("fillPnl") or 0) * fraction
            item["fee"] = float(original.get("fee") or 0) * fraction
        result.append(item)
        remaining -= min(size, remaining)
        if remaining <= 1e-12:
            return entry, result
    return entry, []
