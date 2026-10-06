import pytest

from quantbot.live_audit import LiveAuditCredentials, OkxLiveReadOnlyClient, summarize_audit
from quantbot.okx import OkxError


def test_live_audit_gives_windows_dns_time_to_recover(monkeypatch):
    client = OkxLiveReadOnlyClient(LiveAuditCredentials("key", "secret", "pass"))
    attempts = []
    delays = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"code":"0","data":[]}'

    def flaky_urlopen(_request, timeout):
        attempts.append(timeout)
        if len(attempts) < 4:
            raise OSError("[Errno 11004] getaddrinfo failed")
        return Response()

    monkeypatch.setattr("quantbot.live_audit.urlopen", flaky_urlopen)
    monkeypatch.setattr("quantbot.live_audit.time.sleep", delays.append)

    assert client._request("GET", "/api/v5/account/config", private=True)["code"] == "0"
    assert len(attempts) == 4
    assert delays == [0.5, 1.5, 3.0]


def test_live_audit_transport_rejects_post_and_unknown_paths():
    client = OkxLiveReadOnlyClient(LiveAuditCredentials("key", "secret", "pass"))
    with pytest.raises(OkxError, match="non-GET"):
        client._request("POST", "/api/v5/trade/order", {}, private=True)
    with pytest.raises(OkxError, match="allow-listed"):
        client._request("GET", "/api/v5/account/bills", {}, private=True)


def test_live_audit_summary_is_redacted_and_marks_execution_forbidden():
    report = summarize_audit(
        {"data": [{"uid": "secret-account-id", "acctLv": "2", "posMode": "long_short_mode"}]},
        {"data": [{"details": [{"ccy": "USDT", "eq": "100", "cashBal": "100", "availBal": "98"}]}]},
        {"data": [{"posSide": "long", "pos": "1", "avgPx": "2000", "mgnMode": "isolated"}]},
        {"data": [{"instId": "ETH-USDT-SWAP", "mgnMode": "isolated", "posSide": "long", "lever": "100"}]},
        {"data": [{"instId": "ETH-USDT-SWAP", "state": "live", "ctVal": "0.1", "ctValCcy": "ETH", "ctType": "linear", "settleCcy": "USDT", "lotSz": "0.01", "minSz": "0.01"}]},
        {"data": [{"markPx": "2001"}]}, {"data": [{"last": "2002"}]},
    )
    assert report["execution_permitted"] is False
    assert "uid" not in str(report)
    assert report["open_positions"] == [{"posSide": "long", "pos": "1", "avgPx": "2000", "mgnMode": "isolated"}]
    assert report["minimum_order"]["api_size_contracts"] == "0.01"
    assert report["minimum_order"]["estimated_notional_usdt"] == "2.00"


def test_live_audit_queries_cross_leverage_and_only_allowlisted_gets(monkeypatch):
    client = OkxLiveReadOnlyClient(LiveAuditCredentials("key", "secret", "pass"))
    calls = []

    def fake_request(method, path, payload=None, *, private):
        calls.append((method, path, payload, private))
        if path.endswith("/balance"):
            return {"data": [{"details": []}]}
        if path.endswith("/instruments"):
            return {"data": [{"ctType": "linear", "settleCcy": "USDT", "ctVal": "0.1", "minSz": "0.01", "lotSz": "0.01"}]}
        if path.endswith("/mark-price"):
            return {"data": [{"markPx": "2460"}]}
        return {"data": []}

    monkeypatch.setattr(client, "_request", fake_request)
    client.audit()
    assert all(method == "GET" for method, *_ in calls)
    assert ("GET", "/api/v5/account/leverage-info", {
        "instId": "ETH-USDT-SWAP", "mgnMode": "cross"}, True) in calls
