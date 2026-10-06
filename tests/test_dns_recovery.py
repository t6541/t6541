from __future__ import annotations

import json
from types import SimpleNamespace

from quantbot import dns_recovery


def test_dns_resolution_failure_is_narrowly_classified():
    assert dns_recovery.is_dns_resolution_failure("<urlopen error [Errno 11004] getaddrinfo failed>")
    assert not dns_recovery.is_dns_resolution_failure("SSL handshake timed out")
    assert not dns_recovery.is_dns_resolution_failure("OKX Live POST outcome unknown")


def test_sustained_pre_request_network_failure_includes_tls_but_not_unknown_post():
    assert dns_recovery.is_sustained_pre_request_network_failure("SSL handshake timed out")
    assert dns_recovery.is_sustained_pre_request_network_failure("connection timed out")
    assert not dns_recovery.is_sustained_pre_request_network_failure(
        "OKX Live POST outcome unknown")


def test_dns_recovery_changes_default_physical_adapter_and_verifies(monkeypatch):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="Ethernet\n", stderr="")

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self): return json.dumps({"code": "0"}).encode()

    monkeypatch.setattr(dns_recovery.os, "name", "nt")
    monkeypatch.setattr(dns_recovery.socket, "getaddrinfo", lambda *_args: [(2, 1, 6, "", ())])
    monkeypatch.setattr(dns_recovery, "urlopen", lambda *_args, **_kwargs: Response())
    result = dns_recovery.recover_default_physical_adapter_dns(runner=runner)

    assert result.changed and result.verified
    script = calls[0][0][-1]
    for expected in ("0.0.0.0/0", "HardwareInterface", "223.5.5.5", "119.29.29.29", "Clear-DnsClientCache"):
        assert expected in script
    assert "runas" not in script.lower()


def test_dns_recovery_reports_command_failure(monkeypatch):
    monkeypatch.setattr(dns_recovery.os, "name", "nt")

    def runner(_command, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Access denied")

    result = dns_recovery.recover_default_physical_adapter_dns(runner=runner)
    assert not result.changed and not result.verified
    assert "Access denied" in result.detail


def test_dns_recovery_tries_next_pair_when_first_pair_cannot_verify(monkeypatch):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="Ethernet\n", stderr="")

    resolutions = iter([OSError("first resolver failed"), [(2, 1, 6, "", ())]])

    def resolve(*_args):
        outcome = next(resolutions)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self): return json.dumps({"code": "0"}).encode()

    monkeypatch.setattr(dns_recovery.os, "name", "nt")
    monkeypatch.setattr(dns_recovery.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(dns_recovery, "urlopen", lambda *_args, **_kwargs: Response())
    result = dns_recovery.recover_default_physical_adapter_dns(runner=runner)

    assert result.changed and result.verified
    assert len(calls) == 2
    assert "223.5.5.5" in calls[0][-1]
    assert "180.76.76.76" in calls[1][-1]
