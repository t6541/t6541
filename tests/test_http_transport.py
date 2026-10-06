import io
import json
import http.client
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from quantbot import http_transport, live_audit, dns_recovery
from quantbot.okx import BASE_URL, OkxDemoClient


class Response:
    def __init__(self, payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return json.dumps(self.payload).encode()


def test_proxy_changes_are_observed_on_next_request(monkeypatch):
    routes=iter([{}, {'https':'http://127.0.0.1:1234'}, {'https':'http://127.0.0.1:5678'}])
    seen=[]
    monkeypatch.setattr(http_transport,'getproxies',lambda:next(routes))
    def build(proxy,redirect,https):
        seen.append(dict(proxy.proxies))
        assert isinstance(redirect,http_transport.NoRedirect)
        class Opener:
            def open(self,request,timeout):return Response({'code':'0'})
        return Opener()
    monkeypatch.setattr(http_transport,'build_opener',build)
    for _ in range(3):http_transport.urlopen(BASE_URL+'/api/v5/public/time')
    assert seen==[{}, {'https':'http://127.0.0.1:1234'}, {'https':'http://127.0.0.1:5678'}]


@pytest.mark.parametrize('method',['GET','POST'])
def test_redirects_never_forward_authentication(method):
    req=Request(BASE_URL+'/api/v5/trade/order',method=method,headers={'OK-ACCESS-KEY':'test-secret'})
    with pytest.raises(HTTPError):
        http_transport.NoRedirect().redirect_request(req,None,302,'redirect',{},'https://other.invalid/')


def test_transport_never_replays_uncertain_post(monkeypatch):
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(request);raise TimeoutError('read timeout')
    monkeypatch.setattr(http_transport,'build_opener',lambda *a:Opener())
    with pytest.raises(TimeoutError):
        http_transport.urlopen(Request(BASE_URL+'/api/v5/trade/order',data=b'{}',method='POST'))
    assert len(calls)==1


def test_http_downgrade_is_rejected():
    with pytest.raises(ValueError):http_transport.urlopen('http://example.invalid')


def test_doh_filters_sink_and_private_addresses(monkeypatch):
    payload = {'Answer': [
        {'type': 1, 'data': '0.0.0.0'}, {'type': 1, 'data': '127.0.0.1'},
        {'type': 1, 'data': '43.168.24.35'}, {'type': 5, 'data': 'alias.invalid.'},
    ]}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: Response(payload))
    assert http_transport._resolve_api_peer_with_doh(http_transport._API_HOST) == ['43.168.24.35']
    assert http_transport._resolve_api_peer_with_doh('other.invalid') == []


def test_api_connection_uses_doh_peer_after_local_route_failure(monkeypatch):
    connection = http_transport.VerifiedPeerHTTPSConnection(http_transport._API_HOST)
    calls = []
    class Sock:
        def getpeername(self): return ('43.168.24.35', 443)
    def connect(address, timeout=None, source_address=None):
        calls.append(address)
        if address[0] == http_transport._API_HOST:
            raise __import__('socket').gaierror(11004, 'local DNS sink address')
        return Sock()
    connection._create_connection = connect
    monkeypatch.setattr(http_transport, '_resolve_api_peer_with_doh',
                        lambda host: ['43.168.24.35'])
    monkeypatch.setattr(http.client.HTTPSConnection, 'connect',
                        lambda self: setattr(self, 'sock', self._create_connection(
                            (self.host, self.port), self.timeout, self.source_address)))
    connection.connect()
    assert calls == [(http_transport._API_HOST, 443), ('43.168.24.35', 443)]


def test_proxy_route_probe_does_not_require_local_dns_or_modify_system(monkeypatch):
    monkeypatch.setattr(http_transport,'urlopen',lambda *a,**kw:Response({'code':'0','data':[{'ts':'1'}]}))
    def forbidden(*a,**kw):raise AssertionError('must not change DNS or require direct DNS resolution')
    monkeypatch.setattr(dns_recovery.socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(dns_recovery,'recover_default_physical_adapter_dns',forbidden)
    r=dns_recovery.probe_configured_api_route()
    assert r.verified and not r.changed


def test_audit_retries_get_with_new_timestamp_and_signature(monkeypatch):
    timestamps=iter(['2026-09-11T00:00:01Z','2026-09-11T00:00:03Z'])
    monkeypatch.setattr(OkxDemoClient,'_timestamp',lambda self:next(timestamps))
    monkeypatch.setattr(live_audit.time,'sleep',lambda _:None)
    requests=[]
    def open_request(req,**kw):
        requests.append(req)
        if len(requests)==1:raise URLError('getaddrinfo failed')
        return Response({'code':'0','data':[]})
    monkeypatch.setattr(live_audit,'urlopen',open_request)
    c=live_audit.OkxLiveReadOnlyClient(live_audit.LiveAuditCredentials('key','secret','phrase'))
    c._request('GET','/api/v5/account/config',private=True)
    assert requests[0].get_header('Ok-access-timestamp')!=requests[1].get_header('Ok-access-timestamp')
    assert requests[0].get_header('Ok-access-sign')!=requests[1].get_header('Ok-access-sign')


@pytest.mark.parametrize('http_error',[False,True])
def test_audit_synchronizes_expired_signature_once(monkeypatch,http_error):
    calls=[];sync=[]
    monkeypatch.setattr(OkxDemoClient,'_sync_server_time',lambda self:sync.append(True))
    def open_request(req,**kw):
        calls.append(req)
        if len(calls)==1:
            if http_error:raise HTTPError(req.full_url,401,'expired',{},io.BytesIO(b'{"code":"50102"}'))
            return Response({'code':'50102'})
        return Response({'code':'0','data':[]})
    monkeypatch.setattr(live_audit,'urlopen',open_request)
    c=live_audit.OkxLiveReadOnlyClient(live_audit.LiveAuditCredentials('key','secret','phrase'))
    assert c._request('GET','/api/v5/account/config',private=True)['code']=='0'
    assert len(sync)==1 and len(calls)==2


def test_status_reports_endpoint_without_echoing_arbitrary_error_payload():
    s=dns_recovery.network_failure_summary('GET /api/v5/account/config getaddrinfo failed secret=EXAMPLE')
    assert '/api/v5/account/config' in s and 'EXAMPLE' not in s
