import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from quantbot.manual_order_review import load_manual_order_history, manual_order_rows
from quantbot.account05_live import Account05LiveClient


def order(oid, ts, *, qty="1", px="100", pos="short", side="sell", cid="", **kw):
    return dict(ordId=oid, fillTime=str(ts), accFillSz=qty, avgPx=px,
                posSide=pos, side=side, clOrdId=cid, state="filled", **kw)


def test_manual_short_close_without_reduce_only_is_matched_by_side():
    rows = manual_order_rows([order("open", 1000),
                              order("close", 2000, side="buy", px="90")], [])
    assert len(rows) == 1
    assert rows[0][7:10] == ("90", "open", "close")
    assert rows[0][1] != "—"
    assert rows[0][-1] == "已平仓（FIFO）"


def test_one_close_can_cover_multiple_entries_without_reusing_quantity():
    rows = manual_order_rows([order("a", 1000), order("b", 2000),
        order("c", 3000, qty="1.5", side="buy", px="95")], [])
    by_id = {r[8]: r for r in rows}
    assert by_id["a"][-1] == "已平仓（FIFO）"
    assert by_id["b"][-1] == "部分平仓 0.5/1（FIFO）"
    assert by_id["b"][9] == "c"


def test_multiple_closes_have_weighted_price_ids_and_latest_time():
    rows = manual_order_rows([order("a", 1000, qty="2"),
        order("b", 2000, px="90", side="buy"),
        order("c", 3000, px="80", side="buy")], [])
    assert rows[0][7:10] == ("85", "a", "b、c")
    assert rows[0][1].endswith("08:00:03")


def test_automatic_entries_consume_closes_before_manual_entries():
    rows = manual_order_rows([order("auto", 1000, cid="A5EN1"),
        order("manual", 2000), order("close", 3000, side="buy")], [])
    assert len(rows) == 1
    assert rows[0][8] == "manual"
    assert rows[0][9] == "—"


def test_automatic_close_can_close_manual_entry():
    rows = manual_order_rows([order("manual", 1000),
        order("close", 2000, side="buy", cid="A5EX1")], [])
    assert rows[0][9] == "close"


def test_long_and_short_directions_and_chronology_remain_separate():
    rows = manual_order_rows([order("earlyclose", 500, side="buy"),
        order("short", 1000), order("longclose", 2000, pos="long", side="sell")], [])
    by_id = {r[8]: r for r in rows if r[8] != "—"}
    assert by_id["short"][9] == "—"
    assert {r[9] for r in rows if r[8] == "—"} == {"earlyclose", "longclose"}
    assert all(r[0] == "—" for r in rows if r[8] == "—")


def test_split_fills_are_aggregated_and_deduplicated():
    fills = [dict(ordId="a", tradeId="1", ts="1000", fillSz="1", fillPx="100",
                  posSide="short", side="sell"),
             dict(ordId="a", tradeId="2", ts="2000", fillSz="1", fillPx="110",
                  posSide="short", side="sell")]
    rows = manual_order_rows([], fills + fills)
    assert len(rows) == 1
    assert rows[0][5:7] == ("2", "105")


def test_missing_fill_metadata_is_enriched_without_mutating_inputs():
    opening = order("a", 1000)
    fills = [dict(ordId="b", tradeId="1", ts="2000", fillSz="1", fillPx="90",
                  posSide="short", side="buy")]
    rows = manual_order_rows([opening], fills)
    assert rows[0][9] == "b"
    assert opening == order("a", 1000)


def test_partial_fill_page_uses_authoritative_order_totals():
    rows = manual_order_rows([order("a", 1000, qty="2", px="105")], [
        dict(ordId="a", tradeId="1", ts="1000", fillSz="1", fillPx="100")])
    assert rows[0][5:7] == ("2", "105")


def test_unfilled_cancelled_and_invalid_prices_are_not_displayed():
    rows = manual_order_rows([
        dict(ordId="live", sz="5", px="100", side="sell", posSide="short", cTime="1000"),
        order("zero", 2000, qty="0"), order("invalid", 3000, px="NaN")], [])
    assert rows == []


def test_latest_20_are_selected_after_full_history_matching():
    orders = [order(str(i), (i + 1) * 1000) for i in range(25)]
    orders.append(order("close-oldest", 30000, side="buy"))
    rows = manual_order_rows(orders, [])
    assert len(rows) == 20
    assert rows[0][8:10] == ("0", "close-oldest")
    assert rows[1][8] == "24"
    assert "1" not in {r[8] for r in rows}


def test_net_reduce_only_derives_correct_position_direction():
    rows = manual_order_rows([order("a", 1000, pos="net", side="buy"),
        order("b", 2000, pos="net", side="sell", reduceOnly="true")], [])
    assert rows[0][3] == "多"
    assert rows[0][9] == "b"


def test_history_pagination_uses_order_id_and_bill_id_not_timestamp():
    calls = []
    class Client:
        def order_history_page(self, inst_id, **kwargs):
            calls.append(("orders", kwargs))
            if not kwargs["after"]:
                return [dict(ordId=str(i)) for i in range(100)]
            return []
        def fills_history(self, inst_id, **kwargs):
            calls.append(("fills", kwargs))
            if not kwargs["after"]:
                return [dict(billId=str(i), ts="9999") for i in range(100)]
            return []
    _, _, warning = load_manual_order_history(Client(), "ETH-USDT-SWAP")
    assert warning == ""
    assert calls[1][1]["after"] == "99"
    assert calls[-1][1]["after"] == "99"


def test_history_failure_is_not_silently_rendered_as_no_closes():
    class Client:
        def order_history_page(self, *args, **kwargs):
            raise RuntimeError("offline API failure")
    with pytest.raises(RuntimeError, match="offline API failure"):
        load_manual_order_history(Client(), "ETH-USDT-SWAP")


def test_history_limit_is_reported():
    class Client:
        def order_history_page(self, *args, **kwargs):
            return [dict(ordId=str(i)) for i in range(100)]
        def fills_history(self, *args, **kwargs):
            return [dict(billId=str(i)) for i in range(100)]
    _, _, warning = load_manual_order_history(Client(), "ETH-USDT-SWAP", max_pages=1)
    assert "达到查询上限" in warning


def test_new_history_adapter_only_makes_private_get_requests():
    calls = []
    class Client:
        def _request(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"data": [{"ordId": "a"}]}
    client = Client()
    assert Account05LiveClient.order_history_page(client, archive=True, after="42") == [{"ordId": "a"}]
    assert calls == [(("GET", "/api/v5/trade/orders-history-archive",
                       {"instType": "SWAP", "instId": "ETH-USDT-SWAP", "limit": "100", "after": "42"}),
                      {"private": True})]


def test_strict_fills_errors_propagate_but_legacy_behavior_is_preserved():
    class Client:
        def _request(self, *args, **kwargs):
            raise RuntimeError("history unavailable")
    client = Client()
    assert Account05LiveClient.fills_history(client) == []
    with pytest.raises(RuntimeError, match="history unavailable"):
        Account05LiveClient.fills_history(client, strict=True)


def test_history_sources_degrade_without_hiding_available_orders():
    class Client:
        def order_history_page(self, *args, **kwargs):
            if kwargs["archive"]:
                raise RuntimeError("archive temporarily unavailable")
            return [order("opening", 1000)]
        def fills_history(self, *args, **kwargs):
            raise RuntimeError("fills temporarily unavailable")
    orders, fills, warning = load_manual_order_history(Client(), "ETH-USDT-SWAP")
    assert "archive temporarily unavailable" in warning
    assert "fills temporarily unavailable" in warning
    assert manual_order_rows(orders, fills)[0][6:9] == ("100", "—", "opening")


def test_fills_still_render_when_both_order_history_sources_fail():
    class Client:
        def order_history_page(self, *args, **kwargs):
            raise RuntimeError("orders unavailable")
        def fills_history(self, *args, **kwargs):
            return [dict(ordId="a", ts="1000", fillSz="1", fillPx="100",
                         side="sell", posSide="short")]
    orders, fills, warning = load_manual_order_history(Client(), "ETH-USDT-SWAP")
    assert warning
    assert manual_order_rows(orders, fills)[0][8] == "a"


@pytest.mark.parametrize("archive", [False, True])
def test_account05_history_passes_real_transport_allowlist_offline(monkeypatch, archive):
    import io
    import json
    from urllib.parse import urlsplit, parse_qs
    from quantbot.live_audit import LiveAuditCredentials
    from quantbot.live_aggressive_adapter import OkxLiveAggressiveAdapter
    calls = []
    def response(request, **kwargs):
        calls.append(request)
        return io.BytesIO(json.dumps({"code": "0", "data": [order("a", 1000)]}).encode())
    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", response)
    client = Account05LiveClient(LiveAuditCredentials("test-key", "test-secret", "test-passphrase"))
    rows = client.order_history_page(archive=archive, after="42")
    assert rows[0]["ordId"] == "a"
    assert len(calls) == 1
    parsed = urlsplit(calls[0].full_url)
    expected = "/api/v5/trade/orders-history" + ("-archive" if archive else "")
    assert parsed.path == expected
    assert calls[0].get_method() == "GET"
    assert parse_qs(parsed.query)["after"] == ["42"]
    assert expected not in Account05LiveClient._POST_PATHS
    assert expected not in OkxLiveAggressiveAdapter._GET_PATHS


def _desktop_function(name, namespace):
    # Importing the Windows UI is impossible on Linux. Execute its real
    # Python handler with mocked native window APIs instead.
    source = Path("src/quantbot/win32desktop.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "win32desktop.py", "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("column", [0, 1])
def test_time_header_click_sorts_and_requests_same_refresh_as_button(column):
    calls = []
    notification = SimpleNamespace(hdr=SimpleNamespace(hwndFrom=2, code=3), iSubItem=column)
    namespace = dict(WM_TIMER=10, WM_ORDER_NOTICE=11, WM_NOTIFY=12, WM_COMMAND=13,
        HANDLES={"recovery_pool_window": 1, "recovery_pool_table": 2},
        LVN_COLUMNCLICK=3, NMITEMACTIVATEW=object,
        ctypes=SimpleNamespace(POINTER=lambda x: x, cast=lambda *args: SimpleNamespace(contents=notification)),
        _apply_recovery_pool_time_sort=lambda: calls.append("sort"),
        _request_recovery_pool_refresh=lambda: calls.append("refresh"))
    handler = _desktop_function("window_proc", namespace)
    assert handler(1, 12, 0, 1) == 0
    assert calls == ["sort", "refresh"]
    assert namespace["HANDLES"]["recovery_pool_sort_column"] == column


def test_refresh_lock_prevents_overlap_and_releases_after_failure():
    import threading
    pending = []
    class Thread:
        def __init__(self, *, target, daemon):
            pending.append(target)
        def start(self):
            pass
    def fail():
        raise RuntimeError("test failure")
    namespace = dict(RECOVERY_POOL_REFRESH_LOCK=threading.Lock(),
        threading=SimpleNamespace(Thread=Thread), _recovery_pool_worker=fail)
    refresh = _desktop_function("_request_recovery_pool_refresh", namespace)
    refresh(); refresh()
    assert len(pending) == 1
    with pytest.raises(RuntimeError):
        pending.pop()()
    refresh()
    assert len(pending) == 1


def test_manual_window_worker_passes_latest_20_to_current_sort():
    calls = []
    data = [order(str(i), (i + 1) * 1000) for i in range(25)]
    namespace = dict(LIVE_SESSION_CREDENTIALS={"clone_research": object()},
        Account05LiveClient=lambda *args, **kwargs: object(),
        ACCOUNT05_INSTRUMENT="ETH-USDT-SWAP",
        load_manual_order_history=lambda *args: (data, [], ""),
        manual_order_rows=manual_order_rows, HANDLES={"manual_orders_sort_column": 1},
        _apply_manual_orders_sort=lambda: calls.append("sort"),
        _text=lambda *args: calls.append(args[1]))
    _desktop_function("_manual_orders_worker", namespace)()
    rows = namespace["HANDLES"]["manual_orders_raw_rows"]
    assert len(rows) == 20
    assert rows[0][8] == "24"
    assert namespace["HANDLES"]["manual_orders_sort_column"] == 1
    assert calls[0] == "sort"
    assert "最多20条" in calls[1]
