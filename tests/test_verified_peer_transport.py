import socket
import ssl
import threading
import time
import pytest
from quantbot import http_transport as transport
from quantbot.live_audit import OkxLiveReadOnlyClient


@pytest.fixture(autouse=True)
def clear_cache():
    transport._VERIFIED_PEERS.clear()
    yield
    transport._VERIFIED_PEERS.clear()


def connection(monkeypatch, *, failure=None):
    c=transport.VerifiedPeerHTTPSConnection(transport._API_HOST)
    def tls_connect(self):
        self.sock=self._create_connection((self.host,self.port),self.timeout,None)
        if failure:raise failure
    monkeypatch.setattr(transport.http.client.HTTPSConnection,'connect',tls_connect)
    class Sock:
        def getpeername(self):return ('192.0.2.10',443)
    return c,Sock()


def test_success_is_cached_only_after_verified_tls(monkeypatch):
    c,sock=connection(monkeypatch)
    c._create_connection=lambda *args:sock
    c.connect()
    assert transport._VERIFIED_PEERS[(c.host,c.port)][0]=='192.0.2.10'


def test_handler_passes_verifying_context_on_bundled_python(monkeypatch):
    handler=transport.VerifiedPeerHTTPSHandler()
    def open_connection(cls,request,**kwargs):
        assert cls is transport.VerifiedPeerHTTPSConnection
        assert kwargs['context'].check_hostname
        assert kwargs['context'].verify_mode==ssl.CERT_REQUIRED
        return 'ok'
    monkeypatch.setattr(handler,'do_open',open_connection)
    assert handler.https_open(object())=='ok'


def test_failed_certificate_cannot_seed_cache(monkeypatch):
    c,sock=connection(monkeypatch,failure=ssl.SSLCertVerificationError('invalid'))
    c._create_connection=lambda *args:sock
    with pytest.raises(ssl.SSLCertVerificationError):c.connect()
    assert not transport._VERIFIED_PEERS


def test_dns_fallback_preserves_hostname_and_never_renews_ttl(monkeypatch):
    c,sock=connection(monkeypatch);stamp=time.monotonic()-20
    transport._VERIFIED_PEERS[(c.host,c.port)]=('192.0.2.10',stamp)
    calls=[]
    def create(address,*args):
        calls.append(address)
        if address[0]==c.host:raise socket.gaierror(11004,'DNS')
        return sock
    c._create_connection=create;c.connect()
    assert calls==[(transport._API_HOST,443),('192.0.2.10',443)]
    assert c.host==transport._API_HOST
    assert transport._VERIFIED_PEERS[(c.host,c.port)][1]==stamp


@pytest.mark.parametrize('case',['expired','empty','proxy','other_host','timeout'])
def test_fallback_does_not_bypass_route_or_retry_non_dns_errors(monkeypatch,case):
    c,sock=connection(monkeypatch)
    if case=='other_host':c.host='example.invalid'
    if case=='proxy':c.set_tunnel('example.invalid')
    if case!='empty':transport._VERIFIED_PEERS[(c.host,c.port)]=('192.0.2.10',time.monotonic()-(301 if case=='expired' else 0))
    calls=[]
    def create(*args):
        calls.append(args)
        if case=='timeout':raise TimeoutError('timeout')
        raise socket.gaierror(11004,'DNS')
    c._create_connection=create
    with pytest.raises(OSError):c.connect()
    assert len(calls)==1


def test_fallback_certificate_failure_removes_cached_address(monkeypatch):
    c,sock=connection(monkeypatch,failure=ssl.SSLCertVerificationError('invalid'))
    transport._VERIFIED_PEERS[(c.host,c.port)]=('192.0.2.10',time.monotonic())
    def create(address,*args):
        if address[0]==c.host:raise socket.gaierror(11004,'DNS')
        return sock
    c._create_connection=create
    with pytest.raises(ssl.SSLCertVerificationError):c.connect()
    assert not transport._VERIFIED_PEERS


def test_audit_reads_sequentially_to_avoid_tls_burst_and_never_returns_partial_success(monkeypatch):
    client=OkxLiveReadOnlyClient();lock=threading.Lock();counts=[0,0]
    def read(method,path,payload,private):
        assert method=='GET'
        with lock:
            counts[0]+=1;counts[1]=max(counts)
        with lock:counts[0]-=1
        if path.endswith('/ticker'):raise OSError('no quote')
        return {'code':'0','data':[]}
    monkeypatch.setattr(client,'_request',read)
    with pytest.raises(OSError,match='no quote'):client.audit()
    assert counts[1]==1
