import pytest
import io
import time
from urllib.error import HTTPError, URLError

from quantbot.okx import BASE_URL, OkxCredentials, OkxDemoClient, OkxError


class _Response:
    def __init__(self, body=b'{"code":"0","data":[]}'): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.body


def test_deployment_uses_user_selected_okx_access_domain():
    assert BASE_URL == "https://www.tpouxyihas.com"


def test_request_accepts_only_the_user_selected_access_domain(monkeypatch):
    captured = {}
    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _Response()
    monkeypatch.setattr("quantbot.okx.urlopen", fake_urlopen)
    client = OkxDemoClient(timeout=6, base_url="https://www.tpouxyihas.com/")
    assert client._request("GET", "/test") == {"code": "0", "data": []}
    assert captured["url"] == "https://www.tpouxyihas.com/test"


def test_request_rejects_every_other_transport_host():
    with pytest.raises(ValueError, match="transport host is locked"):
        OkxDemoClient(base_url="https://invalid.example")


def test_get_retries_transient_network_errors(monkeypatch):
    attempts = []
    def fake_urlopen(request, timeout):
        attempts.append(timeout)
        if len(attempts) < 3:
            raise URLError("TLS handshake timed out")
        return _Response()
    monkeypatch.setattr("quantbot.okx.urlopen", fake_urlopen)
    monkeypatch.setattr("quantbot.okx.time.sleep", lambda _: None)
    assert OkxDemoClient(timeout=6)._request("GET", "/test") == {"code": "0", "data": []}
    assert attempts == [6, 6, 6]


def test_get_retries_okx_503_50001(monkeypatch):
    attempts = []
    def fake_urlopen(request, timeout):
        attempts.append(timeout)
        if len(attempts) < 3:
            raise HTTPError(request.full_url, 503, "unavailable", {},
                            io.BytesIO(b'{"code":"50001","data":[],"msg":"Service temporarily unavailable. Please try again later."}'))
        return _Response()
    monkeypatch.setattr("quantbot.okx.urlopen", fake_urlopen)
    monkeypatch.setattr("quantbot.okx.time.sleep", lambda _: None)
    assert OkxDemoClient(timeout=6)._request("GET", "/test") == {"code": "0", "data": []}
    assert attempts == [6, 6, 6]


def test_set_leverage_retries_explicit_503_but_order_post_does_not(monkeypatch):
    attempts = []
    def fake_urlopen(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 503, "unavailable", {},
                            io.BytesIO(b'{"code":"50001","data":[],"msg":"Service temporarily unavailable."}'))
        return _Response()
    monkeypatch.setattr("quantbot.okx.urlopen", fake_urlopen)
    monkeypatch.setattr("quantbot.okx.time.sleep", lambda _: None)
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"), timeout=6)
    client.set_leverage(10, position_side="long")
    assert len(attempts) == 2


def test_post_is_never_retried_after_unknown_network_outcome(monkeypatch):
    attempts = []
    def fake_urlopen(request, timeout):
        attempts.append(timeout)
        raise URLError("read timed out")
    monkeypatch.setattr("quantbot.okx.urlopen", fake_urlopen)
    with pytest.raises(OkxError):
        OkxDemoClient(timeout=6)._request("POST", "/test", {"x": 1})
    assert attempts == [6]


def test_private_request_syncs_okx_time_and_resigns_after_explicit_50102(monkeypatch):
    OkxDemoClient._clock_offsets_ms.clear()
    order_timestamps, paths = [], []
    order_attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal order_attempts
        paths.append(request.full_url)
        if request.full_url.endswith("/api/v5/public/time"):
            server_ms = int(time.time() * 1000) + 60_000
            return _Response(f'{{"code":"0","data":[{{"ts":"{server_ms}"}}]}}'.encode())
        order_attempts += 1
        headers = {key.lower(): value for key, value in request.header_items()}
        order_timestamps.append(headers["ok-access-timestamp"])
        if order_attempts == 1:
            return _Response(b'{"code":"50102","msg":"Timestamp request expired","data":[]}')
        return _Response()

    monkeypatch.setattr("quantbot.okx.urlopen", fake_urlopen)
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"), timeout=6)
    assert client._request("GET", "/api/v5/account/config", private=True)["code"] == "0"
    assert order_attempts == 2
    assert any(path.endswith("/api/v5/public/time") for path in paths)
    assert order_timestamps[0] != order_timestamps[1]


def test_post_retries_only_after_explicit_timestamp_rejection(monkeypatch):
    OkxDemoClient._clock_offsets_ms.clear()
    order_attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal order_attempts
        if request.full_url.endswith("/api/v5/public/time"):
            return _Response(f'{{"code":"0","data":[{{"ts":"{int(time.time() * 1000)}"}}]}}'.encode())
        order_attempts += 1
        if order_attempts == 1:
            return _Response(b'{"code":"50102","msg":"Timestamp request expired","data":[]}')
        return _Response()

    monkeypatch.setattr("quantbot.okx.urlopen", fake_urlopen)
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"), timeout=6)
    assert client._request("POST", "/api/v5/test", {"x": 1}, private=True)["code"] == "0"
    assert order_attempts == 2


def test_post_rejects_item_level_okx_failure(monkeypatch):
    body = b'{"code":"0","data":[{"sCode":"51000","sMsg":"invalid protection"}]}'
    monkeypatch.setattr("quantbot.okx.urlopen", lambda request, timeout: _Response(body))
    with pytest.raises(OkxError, match="51000"):
        OkxDemoClient()._request("POST", "/test", {"x": 1})


def test_demo_order_requires_unlock_and_stop_loss():
    client = OkxDemoClient()
    with pytest.raises(OkxError):
        client.place_demo_market_order("buy", 1, "2000", enabled=False, max_contracts=1, confirmation="DEMO-ORDER")
    with pytest.raises(ValueError):
        client.place_demo_market_order("buy", 1, "", enabled=True, max_contracts=1, confirmation="DEMO-ORDER")


def test_demo_order_caps_contracts_before_network():
    client = OkxDemoClient()
    with pytest.raises(ValueError):
        client.place_demo_market_order("buy", 2, "2000", enabled=True, max_contracts=1, confirmation="DEMO-ORDER")


def test_credential_change_is_blocked_by_position(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))

    def fake_request(method, path, payload=None, private=False):
        if path.endswith("/positions"):
            return {"code": "0", "data": [{"instId": "ETH-USDT-SWAP", "pos": "1"}]}
        return {"code": "0", "data": []}

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(OkxError, match="not clear"):
        client.assert_account_clear()


def test_clear_account_allows_credential_change(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"code": "0", "data": []})
    client.assert_account_clear()


def test_history_candles_are_confirmed_deduplicated_and_sorted(monkeypatch):
    client = OkxDemoClient()
    batch = [
        ["3000", "1", "2", "1", "2", "10", "0", "0", "0"],
        ["2000", "1", "2", "1", "2", "10", "0", "0", "1"],
        ["1000", "1", "2", "1", "2", "10", "0", "0", "1"],
    ]
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"code": "0", "data": batch})
    assert [item[0] for item in client.history_candles("ETH-USDT-SWAP", total=3)] == ["1000", "2000"]


def test_current_candles_keep_unconfirmed_bar_and_sort_oldest_first(monkeypatch):
    client = OkxDemoClient()
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: {"data": [
        ["2000", "2", "3", "1", "2.5", "10", "0", "0", "0"],
        ["1000", "1", "2", "1", "2", "10", "0", "0", "1"],
    ]})
    rows = client.current_candles("ETH-USDT-SWAP")
    assert [row[0] for row in rows] == ["1000", "2000"]
    assert rows[-1][8] == "0"


def test_demo_order_uses_instrument_and_client_order_id(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))
    captured = {}
    monkeypatch.setattr(client, "_request", lambda method, path, payload, private: captured.update(payload) or {"code": "0"})
    client.place_demo_market_order(
        "buy", 1, "1800", enabled=True, max_contracts=1,
        confirmation="DEMO-ORDER", inst_id="ETH-USDT-SWAP", client_order_id="QB202608090001",
        position_side="long", take_profit_price="2000",
    )
    assert captured["instId"] == "ETH-USDT-SWAP"
    assert captured["clOrdId"] == "QB202608090001"
    assert captured["posSide"] == "long"
    assert captured["attachAlgoOrds"][0]["slTriggerPx"] == "1800"
    assert captured["attachAlgoOrds"][0]["tpTriggerPx"] == "2000"
    assert captured["attachAlgoOrds"][0]["slTriggerPxType"] == "mark"
    assert captured["attachAlgoOrds"][0]["tpTriggerPxType"] == "last"
    assert captured["attachAlgoOrds"][0]["attachAlgoClOrdId"] == "QB202608090001P"


def test_invalid_client_order_id_fails_before_post(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))
    posted = False

    def unexpected_request(*_args, **_kwargs):
        nonlocal posted
        posted = True

    monkeypatch.setattr(client, "_request", unexpected_request)
    with pytest.raises(ValueError, match="alphanumeric"):
        client.place_demo_market_order(
            "sell", 1, "2600", enabled=True, max_contracts=1,
            confirmation="DEMO-ORDER", inst_id="ETH-USDT-SWAP",
            client_order_id="QBEXpinets-invalidS", position_side="short",
        )
    assert posted is False


def test_demo_order_ignores_local_take_profit_type_when_no_server_target(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))
    captured = {}
    monkeypatch.setattr(client, "_request", lambda method, path, payload, private: captured.update(payload) or {"code": "0"})
    client.place_demo_market_order(
        "sell", 1, "2450", enabled=True, max_contracts=1,
        confirmation="DEMO-ORDER", inst_id="ETH-USDT-SWAP",
        client_order_id="QBLOCALTP001", position_side="short",
        take_profit_price=None,
        take_profit_trigger_type="local_closed_5m_ma5_turn",
        stop_loss_trigger_type="mark",
    )
    attached = captured["attachAlgoOrds"][0]
    assert attached["slTriggerPxType"] == "mark"
    assert "tpTriggerPx" not in attached


def test_unprotected_position_is_detected():
    snapshot = {"positions": [{"instId": "ETH-USDT-SWAP", "pos": "1", "closeOrderAlgo": []}], "orders": [], "algo_orders": []}
    missing = OkxDemoClient.unprotected_positions(snapshot)
    assert len(missing) == 1 and missing[0]["protection_deficit"] == 1


def test_pending_algo_protects_position():
    snapshot = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "1", "closeOrderAlgo": []}],
        "orders": [],
        "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "state": "live",
                         "algoId": "SL1", "slTriggerPx": "99", "sz": "1"}],
    }
    assert OkxDemoClient.unprotected_positions(snapshot) == []


def test_opposite_side_algo_does_not_falsely_protect_other_hedge_side():
    short_position = {"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1", "closeOrderAlgo": []}
    snapshot = {
        "positions": [short_position],
        "orders": [],
        "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "long", "state": "live",
                         "algoId": "SL1", "slTriggerPx": "99", "sz": "1"}],
    }
    missing = OkxDemoClient.unprotected_positions(snapshot)
    assert len(missing) == 1 and missing[0]["posSide"] == "short"


def test_same_side_stop_must_cover_every_filled_layer():
    snapshot = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "2",
                       "closeOrderAlgo": []}],
        "orders": [],
        "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "short",
                         "algoId": "ONLY-FIRST-LAYER", "slTriggerPx": "2500", "sz": "1"}],
    }
    missing = OkxDemoClient.unprotected_positions(snapshot)
    assert len(missing) == 1
    assert missing[0]["protection_covered"] == 1
    assert missing[0]["protection_deficit"] == 1


def test_close_fraction_one_stop_covers_whole_position():
    snapshot = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "2",
                       "closeOrderAlgo": [{"algoId": "FULL", "slTriggerPx": "2500",
                                           "closeFraction": "1"}]}],
        "orders": [], "algo_orders": [],
    }
    assert OkxDemoClient.unprotected_positions(snapshot) == []


def test_swap_trading_mode_rejects_simple_net_account(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))
    monkeypatch.setattr(
        client, "check_demo_account",
        lambda: {"code": "0", "data": [{"acctLv": "1", "posMode": "net_mode"}]},
    )
    with pytest.raises(OkxError, match="acctLv=1, posMode=net_mode"):
        client.require_swap_trading_mode()


def test_demo_order_can_attach_trailing_take_profit_and_mark_stop(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))
    captured = {}
    monkeypatch.setattr(client, "_request", lambda method, path, payload, private: captured.update(payload) or {"code": "0"})
    client.place_demo_market_order(
        "buy", 1, "1997", enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
        position_side="long", trailing_activation_price="2004", trailing_callback_ratio="0.001",
    )
    assert captured["attachAlgoOrds"] == [{
        "slTriggerPx": "1997", "slOrdPx": "-1", "slTriggerPxType": "mark",
        "callbackRatio": "0.001", "activePx": "2004",
    }]


def test_fixed_and_trailing_take_profit_are_mutually_exclusive():
    client = OkxDemoClient()
    with pytest.raises(ValueError, match="cannot be used together"):
        client.place_demo_market_order(
            "buy", 1, "1997", enabled=True, max_contracts=1, confirmation="DEMO-ORDER",
            take_profit_price="2004", trailing_activation_price="2004", trailing_callback_ratio="0.001",
        )


def test_standalone_trailing_close_uses_algo_endpoint(monkeypatch):
    client = OkxDemoClient(OkxCredentials("key", "secret", "pass"))
    captured = {}
    def fake_request(method, path, payload, private):
        captured.update({"method": method, "path": path, "payload": payload, "private": private})
        return {"code": "0", "data": [{"algoId": "123"}]}
    monkeypatch.setattr(client, "_request", fake_request)
    client.place_demo_trailing_order(
        "buy", 1, None, "1915.20", enabled=True, max_contracts=1,
        confirmation="DEMO-ORDER", position_side="short", client_algo_order_id="QBTESTT",
        callback_spread="0.96",
    )
    assert captured["path"] == "/api/v5/trade/order-algo"
    assert captured["payload"]["ordType"] == "move_order_stop"
    assert captured["payload"]["reduceOnly"] == "true"
    assert captured["payload"]["side"] == "buy"
    assert captured["payload"]["posSide"] == "short"
    assert captured["payload"]["callbackSpread"] == "0.96"
    assert "callbackRatio" not in captured["payload"]


def test_demo_sniper_limit_order_can_attach_frozen_target_and_server_stop(monkeypatch):
    captured = {}
    client = OkxDemoClient(OkxCredentials("k", "s", "p"))
    monkeypatch.setattr(client, "_request", lambda method, path, payload, private=False: captured.update(
        {"method": method, "path": path, "payload": payload, "private": private}) or {"data": [{"ordId": "1"}]})
    client.place_demo_sniper_limit_order(
        "sell", 1, "1900", "1901", "1880", enabled=True, max_contracts=1,
        confirmation="DEMO-ORDER", inst_id="ETH-USDT-SWAP", position_side="short",
        client_order_id="QBSNPTESTS",
    )
    assert captured["payload"]["ordType"] == "post_only"
    attached = captured["payload"]["attachAlgoOrds"][0]
    assert attached["tpTriggerPxType"] == "last"
    assert attached["tpTriggerPx"] == "1880"
    assert attached["slTriggerPxType"] == "mark"
    assert attached["attachAlgoClOrdId"] == "QBSNPTESTSP"


def test_active_sniper_stop_can_only_be_tightened_in_place(monkeypatch):
    captured = {}
    client = OkxDemoClient(OkxCredentials("k", "s", "p"))
    monkeypatch.setattr(client, "_request", lambda method, path, payload, private=False: captured.update(
        {"method": method, "path": path, "payload": payload, "private": private}) or {"data": []})
    client.tighten_active_stop_loss(
        {"instId": "ETH-USDT-SWAP", "algoId": "A1"}, "2405.50")
    assert captured["path"] == "/api/v5/trade/amend-algos"
    assert captured["payload"] == {
        "instId": "ETH-USDT-SWAP", "algoId": "A1",
        "newSlTriggerPx": "2405.50", "newSlOrdPx": "-1",
        "newSlTriggerPxType": "mark", "cxlOnFail": False,
    }


def test_demo_active_risk_exit_is_reduce_only_market_close(monkeypatch):
    captured = {}
    client = OkxDemoClient(OkxCredentials("k", "s", "p"))
    monkeypatch.setattr(client, "_request", lambda method, path, payload, private=False: captured.update(
        {"method": method, "path": path, "payload": payload, "private": private}) or {"data": [{"ordId": "2"}]})
    client.close_demo_position_market(
        1, position_side="long", enabled=True, max_contracts=1,
        confirmation="DEMO-ORDER", inst_id="ETH-USDT-SWAP",
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v5/trade/order"
    assert captured["private"] is True
    assert captured["payload"]["ordType"] == "market"
    assert captured["payload"]["side"] == "sell"
    assert captured["payload"]["posSide"] == "long"
    assert captured["payload"]["reduceOnly"] == "true"


def test_orphan_cleanup_only_selects_owned_trailing_without_position():
    snapshot = {
        "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "1"}],
        "orders": [],
        "algo_orders": [
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "ordType": "move_order_stop", "algoClOrdId": "QBVAL1T", "algoId": "1"},
            {"instId": "ETH-USDT-SWAP", "posSide": "short", "ordType": "move_order_stop", "algoClOrdId": "QBVAL2T", "algoId": "2"},
            {"instId": "ETH-USDT-SWAP", "posSide": "long", "ordType": "move_order_stop", "algoClOrdId": "MANUAL", "algoId": "3"},
        ],
    }
    found = OkxDemoClient.orphaned_owned_trailing_orders(snapshot, "QBVAL")
    assert [item["algoId"] for item in found] == ["1"]


def test_legacy_active_stop_is_amended_to_mark_without_cancelling(monkeypatch):
    calls = []
    client = OkxDemoClient(OkxCredentials("k", "s", "p"))
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, payload, private=False: calls.append(
            (method, path, payload, private)) or {"code": "0", "data": [{"sCode": "0"}]},
    )
    snapshot = {"algo_orders": [
        {"instId": "ETH-USDT-SWAP", "algoId": "11", "ordType": "conditional",
         "slTriggerPx": "2400", "slOrdPx": "-1", "slTriggerPxType": "last"},
        {"instId": "ETH-USDT-SWAP", "algoId": "12", "ordType": "move_order_stop",
         "slTriggerPx": "2390", "slTriggerPxType": "last"},
        {"instId": "ETH-USDT-SWAP", "algoId": "13", "ordType": "trigger",
         "slTriggerPx": "2380", "slTriggerPxType": "mark"},
    ]}
    assert client.migrate_active_stop_losses_to_mark(snapshot) == 1
    assert calls == [("POST", "/api/v5/trade/amend-algos", {
        "instId": "ETH-USDT-SWAP", "algoId": "11", "newSlTriggerPx": "2400",
        "newSlOrdPx": "-1", "newSlTriggerPxType": "mark", "cxlOnFail": False,
    }, True)]
    assert snapshot["algo_orders"][0]["slTriggerPxType"] == "mark"
