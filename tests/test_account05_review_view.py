from quantbot.account05_review_view import (
    sort_recovery_pool_rows, sort_recovery_pool_by_structure_profit,
)


def test_recovery_pool_time_columns_sort_newest_first_and_keep_open_lots_last():
    rows = [
        ("12-31 23:00:00", "01-01 01:00:00", "older close"),
        ("01-01 00:00:00", "—", "open lot"),
        ("12-30 23:00:00", "01-02 01:00:00", "newer close"),
    ]
    timestamps = [
        ("2025-12-31T15:00:00Z", "2025-12-31T17:00:00Z"),
        ("2026-01-01T00:00:00Z", ""),
        ("2025-12-30T23:00:00Z", "2026-01-01T17:00:00Z"),
    ]
    assert [row[2] for row in sort_recovery_pool_rows(rows, timestamps, column=0)] == [
        "open lot", "older close", "newer close"]
    assert [row[2] for row in sort_recovery_pool_rows(rows, timestamps, column=1)] == [
        "newer close", "older close", "open lot"]


def test_structure_profit_sort_groups_by_realized_net_and_reverses():
    def row(when, structure, net, status="已释放"):
        return (when, "—", "多", structure, "0.02", "100", "101",
                net, "—", "—", status)

    rows = [row("09-30 12:00", "A", "+0.200000"),
            row("09-30 11:00", "B", "-0.100000"),
            row("09-30 10:00", "A", "-0.050000"),
            row("09-30 09:00", "B", "待平仓", "持仓中"),
            row("09-30 08:00", "C", "+0.050000")]
    timestamps = [(f"2026-09-30T{hour:02}:00:00Z", "")
                  for hour in (12, 11, 10, 9, 8)]
    high = sort_recovery_pool_by_structure_profit(rows, timestamps, highest_first=True)
    low = sort_recovery_pool_by_structure_profit(rows, timestamps, highest_first=False)
    assert [r[3].split("〔")[0] for r in high] == ["A", "A", "C", "B", "B"]
    assert [r[3].split("〔")[0] for r in low] == ["B", "B", "C", "A", "A"]
    assert "累计+0.150000U" in high[0][3]
    assert "累计-0.100000U" in low[0][3]
