from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _display_time(value: object) -> str:
    if not value:
        return "-"
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%m-%d %H:%M")


def read_ma_endpoint_lineage(database: Path, limit: int = 300) -> list[dict]:
    if not database.exists():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ma_endpoint_lineage'"
        ).fetchone()
        if not exists:
            return []
        rows = connection.execute(
            """SELECT child.*,parent.confirmed_bar_time AS parent_bar_time,
            parent.endpoint_price AS parent_price
            FROM ma_endpoint_lineage child
            LEFT JOIN ma_endpoint_lineage parent ON parent.event_key=child.parent_event_key
            ORDER BY child.confirmed_bar_time DESC LIMIT ?""", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def ma_endpoint_table_row(item: dict) -> tuple[str, ...]:
    direction = int(item.get("direction") or 0)
    paired = str(item.get("pair_status")) == "paired"
    parent = str(item.get("parent_timeframe") or "-")
    evidence = str(item.get("confirmation_reason") or "")
    if paired:
        interpretation = str(item.get("identity_label") or "真正高位/低位反转")
    elif str(item.get("timeframe")) == "1m":
        interpretation = (
            "1分钟K线上涨趋势中的回踩追多触发点"
            if direction < 0 else
            "1分钟K线下降趋势中的反抽追空触发点")
    else:
        interpretation = str(item.get("identity_label") or "局部末端，待上级配对")
    upgrades = json.loads(str(item.get("upgraded_trade_uids_json") or "[]"))
    return (
        _display_time(item.get("confirmed_bar_time")), str(item.get("timeframe") or "-"),
        _display_time(item.get("zone_last_seen_at") or item.get("confirmed_bar_time")),
        "顶部/做空" if direction < 0 else "底部/做多",
        f"{float(item.get('endpoint_price') or 0):.2f}",
        f"{float(item.get('extreme_price') or 0):.2f}",
        f"{float(item.get('ma5') or 0):.2f}",
        f"{float(item.get('ma10') or 0):.2f}",
        f"{float(item.get('ma20') or 0):.2f}",
        "是" if bool(item.get("half_cover_confirmed")) else "否",
        "是" if bool(item.get("slow_ma_cross_confirmed")) else "否",
        "是" if bool(item.get("ma5_cross_confirmed")) else "否",
        "已配对" if paired else "末端持续中/未配对", parent,
        _display_time(item.get("parent_bar_time")), f"{interpretation}｜{evidence}",
        ",".join(upgrades) if upgrades else "-",
    )
