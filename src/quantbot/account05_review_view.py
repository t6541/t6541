"""Pure display helpers for the Account-05 recovery-pool review window."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal


def sort_recovery_pool_rows(
        rows: list[tuple[str, ...]], timestamps: list[tuple[str, str]],
        *, column: int = 0,
) -> list[tuple[str, ...]]:
    """Sort newest first by full UTC time; missing close times go last."""
    if column not in (0, 1) or len(rows) != len(timestamps):
        raise ValueError("invalid time column or timestamp count")

    def key(item: tuple[tuple[str, ...], tuple[str, str]]) -> datetime:
        value = item[1][column]
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    return [row for row, _ in sorted(zip(rows, timestamps), key=key, reverse=True)]


def sort_recovery_pool_by_structure_profit(
        rows: list[tuple[str, ...]], timestamps: list[tuple[str, str]],
        *, highest_first: bool,
) -> list[tuple[str, ...]]:
    """Group structures by summed realized local net PnL in visible rows."""
    if len(rows) != len(timestamps):
        raise ValueError("rows and timestamps must have equal lengths")
    totals: dict[str, Decimal] = {}
    for row in rows:
        structure = row[3]
        if row[10] == "已释放" and row[7] != "待平仓":
            totals[structure] = totals.get(structure, Decimal("0")) + Decimal(row[7])
        else:
            totals.setdefault(structure, Decimal("0"))
    indexed = list(zip(rows, timestamps))
    indexed.sort(key=lambda item: item[1][0], reverse=True)
    indexed.sort(key=lambda item: item[0][3])
    indexed.sort(key=lambda item: totals[item[0][3]], reverse=highest_first)
    return [(*row[:3], f"{row[3]}〔累计{totals[row[3]]:+.6f}U〕", *row[4:])
            for row, _ in indexed]
