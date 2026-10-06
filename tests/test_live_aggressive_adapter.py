from datetime import datetime
from io import BytesIO
import json
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from quantbot.live_aggressive_adapter import OkxLiveAggressiveAdapter
from quantbot.live_audit import LiveAuditCredentials
from quantbot.okx import OkxError


def _client():
    return OkxLiveAggressiveAdapter(LiveAuditCredentials("key", "secret", "pass"))


def test_live_adapter_translates_only_one_internal_unit_to_point_zero_five():
    assert _client()._live_size({"sz": "1", "ordType": "post_only"})["sz"] == "0.05"
    with pytest.raises(ValueError, match="one opening unit"):
        _client()._live_size({"sz": "2"})


@pytest.mark.parametrize("size", ["0.01", "0.05", "0.15"])
def test_live_adapter_uses_each_accounts_configured_order_size(size):
    client = OkxLiveAggressiveAdapter(
        LiveAuditCredentials("key", "secret", "pass"), contracts_per_unit=size)
    assert client._live_size({"sz": "1"})["sz"] == size
    assert client._live_size({"sz": "3", "reduceOnly": "true"})["sz"] == format(
        3 * __import__("decimal").Decimal(size), "f")


def test_live_adapter_can_close_all_three_layers_but_cannot_open_three():
    assert _client()._live_size({"sz": "3", "reduceOnly": "true"})["sz"] == "0.15"
    with pytest.raises(ValueError, match="one opening unit"):
        _client()._live_size({"sz": "3", "reduceOnly": "false"})
    assert _client().position_internal_units({"pos": "3"}) == 3


def test_live_snapshot_supports_two_filled_point_zero_five_layers(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        "quantbot.okx.OkxDemoClient.safety_snapshot",
        lambda _self: {
            "positions": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "0.10"}],
            "orders": [],
            "algo_orders": [{"instId": "ETH-USDT-SWAP", "posSide": "short",
                             "algoId": "SL1", "slTriggerPx": "2500", "sz": "0.05"}],
        },
    )
    snapshot = client.safety_snapshot()
    assert snapshot["positions"][0]["pos"] == "2"
    assert snapshot["algo_orders"][0]["sz"] == "1"
    missing = client.unprotected_positions(snapshot)
    assert missing[0]["protection_deficit"] == 1


def test_live_snapshot_supports_more_than_ten_aggregate_layers(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        "quantbot.okx.OkxDemoClient.safety_snapshot",
        lambda _self: {"positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long",
                                       "pos": "0.55"}],
                       "orders": [], "algo_orders": []},
    )
    assert client.safety_snapshot()["positions"][0]["pos"] == "11"


def test_live_snapshot_still_rejects_non_point_zero_five_legacy_quantity(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        "quantbot.okx.OkxDemoClient.safety_snapshot",
        lambda _self: {"positions": [{"instId": "ETH-USDT-SWAP", "posSide": "long",
                                       "pos": "0.51"}],
                       "orders": [], "algo_orders": []},
    )
    with pytest.raises(OkxError, match="not aligned"):
        client.safety_snapshot()


def test_live_adapter_rejects_nonallowlisted_endpoints():
    with pytest.raises(OkxError, match="not allow-listed"):
        _client()._request("POST", "/api/v5/asset/withdrawal", {}, private=True)
    with pytest.raises(OkxError, match="not allow-listed"):
        _client()._request("POST", "/api/v5/account/set-leverage", {}, private=True)


def test_daily_net_uses_asia_shanghai_day(monkeypatch):
    client = _client()
    tz = ZoneInfo("Asia/Shanghai")
    today = datetime(2026, 8, 24, 12, tzinfo=tz)
    captured = {}
    def fake_request(method, path, payload, private=False):
        captured.update(payload)
        return {"data": [{"billId": "1", "fillPnl": "-4.8", "fee": "-0.2"}]}
    monkeypatch.setattr(client, "_request", fake_request)
    assert client.daily_net_pnl_usdt(now=today) == -5.0
    assert captured["begin"] == str(int(datetime(2026, 8, 24, 0, tzinfo=tz).timestamp() * 1000))
    assert captured["end"] == str(int(today.timestamp() * 1000))


def test_daily_net_reuses_one_result_during_the_same_ten_second_loop(monkeypatch):
    client = _client()
    calls = []
    monkeypatch.setattr(
        client, "_request",
        lambda *_args, **_kwargs: calls.append(1) or
        {"data": [{"billId": "1", "fillPnl": "1.2", "fee": "-0.2"}]})
    assert client.daily_net_pnl_usdt() == 1.0
    assert client.daily_net_pnl_usdt() == 1.0
    assert len(calls) == 1


def test_live_get_retries_transient_ssl_timeout(monkeypatch):
    client = _client()
    attempts = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps({"code": "0", "data": []}).encode()

    def fake_urlopen(_request, timeout):
        attempts.append(timeout)
        if len(attempts) < 3:
            raise TimeoutError("The handshake operation timed out")
        return Response()

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", fake_urlopen)
    monkeypatch.setattr("quantbot.live_aggressive_adapter.time.sleep", lambda _seconds: None)
    assert client._request("GET", "/api/v5/market/ticker", {"instId": "ETH-USDT-SWAP"})["code"] == "0"
    assert len(attempts) == 3


def test_live_post_read_timeout_is_not_retried_when_outcome_is_unknown(monkeypatch):
    client = _client()
    attempts = []

    def fake_urlopen(_request, timeout):
        attempts.append(timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", fake_urlopen)
    with pytest.raises(OkxError, match="outcome unknown"):
        client._request("POST", "/api/v5/trade/order", {"sz": "1"})
    assert len(attempts) == 1


def test_live_post_retries_tls_handshake_timeout_before_request_is_sent(monkeypatch):
    client = _client()
    attempts = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"code":"0","data":[{"ordId":"123","clOrdId":"QBVAL1","sCode":"0"}]}'

    def fake_urlopen(request, timeout):
        attempts.append(request)
        if len(attempts) < 3:
            raise TimeoutError("The handshake operation timed out")
        return Response()

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", fake_urlopen)
    monkeypatch.setattr("quantbot.live_aggressive_adapter.time.sleep", lambda _seconds: None)
    result = client._request(
        "POST", "/api/v5/trade/order",
        {"instId": "ETH-USDT-SWAP", "clOrdId": "QBVAL1", "sz": "1"}, private=True)
    assert result["data"][0]["ordId"] == "123"
    assert len(attempts) == 3


def test_live_post_lost_response_recovers_existing_order_by_client_id(monkeypatch):
    client = _client()
    requests = []

    class Response:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps(self.payload).encode()

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.get_method() == "POST":
            raise TimeoutError("The read operation timed out")
        return Response({"code": "0", "data": [{
            "ordId": "456", "clOrdId": "QBVAL2", "state": "live"}]})

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", fake_urlopen)
    result = client._request(
        "POST", "/api/v5/trade/order",
        {"instId": "ETH-USDT-SWAP", "clOrdId": "QBVAL2", "sz": "1"}, private=True)
    assert result["data"][0]["ordId"] == "456"
    assert [request.get_method() for request in requests] == ["POST", "GET"]


def test_live_51054_preserves_request_method_for_safe_recovery(monkeypatch):
    client = _client()

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"code":"51054","msg":"Request timed out. Please try again.","data":[]}'

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(OkxError, match="Live GET error 51054"):
        client._request("GET", "/api/v5/account/positions", private=True)
    with pytest.raises(OkxError, match="Live POST error 51054"):
        client._request("POST", "/api/v5/trade/order", {"sz": "1"}, private=True)


def test_live_http_50102_syncs_server_time_and_resigns(monkeypatch):
    client = _client()
    requests = []
    synced = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"code":"0","data":[]}'

    def fake_urlopen(request, timeout):
        requests.append(request)
        if len(requests) == 1:
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(
                b'{"msg":"Timestamp request expired","code":"50102"}'))
        return Response()

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", fake_urlopen)
    monkeypatch.setattr(client, "_sync_server_time", lambda: synced.append(True) or 1234.0)
    assert client._request("GET", "/api/v5/account/positions", private=True)["code"] == "0"
    assert synced == [True]
    assert len(requests) == 2


def test_live_post_retries_only_after_explicit_timestamp_rejection(monkeypatch):
    client = _client()
    attempts = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"code":"0","data":[]}'

    def fake_urlopen(request, timeout):
        attempts.append(request)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(
                b'{"msg":"Timestamp request expired","code":"50102"}'))
        return Response()

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", fake_urlopen)
    monkeypatch.setattr(client, "_sync_server_time", lambda: 1234.0)
    result = client._request("POST", "/api/v5/trade/order", {"sz": "1"}, private=True)
    assert result["code"] == "0"
    assert len(attempts) == 2


def test_explicit_timestamp_rejection_with_clock_get_timeout_is_retryable_pre_order(
        monkeypatch):
    client = _client()

    def reject(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(
            b'{"msg":"Timestamp request expired","code":"50102"}'))

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", reject)
    monkeypatch.setattr(
        client, "_sync_server_time",
        lambda: (_ for _ in ()).throw(
            OkxError("OKX server-time synchronization failed: SSL handshake timed out")))
    with pytest.raises(
            OkxError,
            match="pre-order server-time synchronization GET failed after explicit rejection"):
        client._request("POST", "/api/v5/trade/order", {"sz": "1"}, private=True)


def test_live_post_exp_time_rejection_syncs_okx_clock_and_retries_same_payload(monkeypatch):
    client = _client()
    client._clock_offsets_ms = {client.base_url: 0.0}
    attempts = []
    synced = []

    class Response:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return self.body

    def fake_urlopen(request, timeout):
        attempts.append(request)
        if len(attempts) == 1:
            return Response(b'{"code":"1","data":[{"sCode":"50036",'
                            b'"sMsg":"expTime expired"}]}')
        return Response(b'{"code":"0","data":[{"sCode":"0"}]}')

    def fake_sync():
        synced.append(True)
        client._clock_offsets_ms[client.base_url] = 20000.0
        return 20000.0

    monkeypatch.setattr("quantbot.live_aggressive_adapter.urlopen", fake_urlopen)
    monkeypatch.setattr(client, "_sync_server_time", fake_sync)
    result = client._request("POST", "/api/v5/trade/order",
                             {"sz": "1", "clOrdId": "QBTEST190"}, private=True)
    assert result["code"] == "0"
    assert synced == [True] and len(attempts) == 2
    assert attempts[0].data == attempts[1].data
    assert int(attempts[1].headers["Exptime"]) >= int(attempts[0].headers["Exptime"]) + 19000
