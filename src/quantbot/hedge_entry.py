from __future__ import annotations


def same_side_entry_conflicts(snapshot: dict, direction: int, *, sniper_prefix: str = "") -> list[dict]:
    """Return same-side exposure that blocks adding another opening position.

    The account runs OKX long/short mode, so protected exposure on the opposite
    side is deliberately not a conflict.  Protective algo orders are not
    opening exposure and are handled by the existing protection audit.
    """
    target = "long" if direction > 0 else "short"
    conflicts = [item for item in snapshot.get("positions", [])
                 if str(item.get("posSide", "")).lower() == target
                 and abs(float(item.get("pos") or 0)) > 0]
    for item in snapshot.get("orders", []):
        client_id = str(item.get("clOrdId", ""))
        if sniper_prefix and client_id.startswith(sniper_prefix):
            continue
        if str(item.get("posSide", "")).lower() != target:
            continue
        if str(item.get("reduceOnly", "false")).lower() == "true":
            continue
        conflicts.append(item)
    return conflicts
