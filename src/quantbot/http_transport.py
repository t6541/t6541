"""Refresh Windows proxy routing per request without replaying HTTP writes."""
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener, getproxies
import http.client
import ipaddress
import json
import socket
import threading
import time


_VERIFIED_PEERS = {}
_PEER_LOCK = threading.Lock()
VERIFIED_PEER_TTL = 300
_API_HOST = "www.tpouxyihas.com"
_DOH_URL = "https://dns.alidns.com/resolve?name={host}&type=A"


def _usable_public_ip(value):
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return False
    return not any((address.is_unspecified, address.is_loopback, address.is_link_local,
                    address.is_multicast, address.is_reserved))


def _resolve_api_peer_with_doh(host, *, timeout=5):
    """Resolve only the locked API host when local DNS is unavailable or poisoned."""
    if host != _API_HOST:
        return []
    from urllib.request import urlopen as platform_urlopen
    with platform_urlopen(_DOH_URL.format(host=host), timeout=timeout) as response:
        payload = json.loads(response.read().decode())
    return [str(row["data"]) for row in (payload.get("Answer") or [])
            if int(row.get("type", 0)) == 1 and _usable_public_ip(row.get("data"))]


class VerifiedPeerHTTPSConnection(http.client.HTTPSConnection):
    """Reuse a recently TLS-verified peer only when local DNS temporarily fails.

    The URL, Host and TLS SNI remain the original hostname. No HTTP request is
    replayed: fallback occurs during TCP setup, before TLS and request bytes.
    Proxied requests use their configured route exclusively.
    """
    def connect(self):
        direct = self.host == _API_HOST and not self._tunnel_host
        original = self._create_connection
        cached_used = False

        def connect_address(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
            nonlocal cached_used
            try:
                return original(address, timeout, source_address)
            except socket.gaierror as error:
                if not direct:
                    raise
                with _PEER_LOCK:
                    cached = _VERIFIED_PEERS.get((self.host, self.port))
                if cached is None or not 0 <= time.monotonic() - cached[1] < VERIFIED_PEER_TTL:
                    try:
                        peers = _resolve_api_peer_with_doh(self.host)
                    except (OSError, ValueError, json.JSONDecodeError):
                        raise error
                    if not peers:
                        raise error
                    cached = (peers[0], time.monotonic())
                    with _PEER_LOCK:
                        _VERIFIED_PEERS[(self.host, self.port)] = cached
                cached_used = True
                # Numeric address is scoped to this connection; never replace
                # global getaddrinfo or mutate system DNS/hosts files.
                return original((cached[0], address[1]), timeout, source_address)

        self._create_connection = connect_address
        try:
            super().connect()
            if direct and not cached_used:
                # Cache only AFTER normal hostname/certificate verification.
                # Fallback use cannot extend the cache's original lifetime.
                peer = self.sock.getpeername()[0]
                with _PEER_LOCK:
                    _VERIFIED_PEERS[(self.host, self.port)] = (peer, time.monotonic())
        except Exception:
            if cached_used:
                with _PEER_LOCK:
                    _VERIFIED_PEERS.pop((self.host, self.port), None)
            raise
        finally:
            self._create_connection = original


class VerifiedPeerHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(VerifiedPeerHTTPSConnection, request, context=self._context)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authentication is valid for this origin only. Do not forward signed
        # headers to another origin, or turn an order POST into a redirected GET.
        raise HTTPError(req.full_url, code, "API redirect rejected", headers, fp)


def urlopen(request, timeout=8):
    """No cached global urllib opener: proxy changes take effect next request.

    TLS certificate/hostname verification stays enabled. This function performs
    one attempt only; callers retain GET retries and POST reconciliation rules.
    """
    url = request if isinstance(request, str) else request.full_url
    if urlsplit(url).scheme != "https":
        raise ValueError("API transport requires HTTPS")
    opener = build_opener(ProxyHandler(getproxies()), NoRedirect(), VerifiedPeerHTTPSHandler())
    return opener.open(request, timeout=timeout)
