"""Conservative Windows DNS self-heal for sustained name-resolution outages."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen


PREFERRED_DNS_SERVERS = ("223.5.5.5", "119.29.29.29")
DNS_CANDIDATE_PAIRS = (
    ("223.5.5.5", "119.29.29.29"),
    ("180.76.76.76", "114.114.114.114"),
    ("1.1.1.1", "8.8.8.8"),
)
OKX_DNS_PROBE_HOST = "www.tpouxyihas.com"
OKX_PUBLIC_TIME_URL = f"https://{OKX_DNS_PROBE_HOST}/api/v5/public/time"


@dataclass(frozen=True)
class DnsRecoveryResult:
    changed: bool
    verified: bool
    detail: str


def probe_configured_api_route() -> DnsRecoveryResult:
    """Test the actual current HTTPS/proxy route, without rewriting system DNS."""
    from .http_transport import urlopen as current_route_open
    try:
        with current_route_open(OKX_PUBLIC_TIME_URL, timeout=8) as response:
            payload = json.loads(response.read().decode())
        verified = str(payload.get("code")) == "0" and bool(payload.get("data"))
        return DnsRecoveryResult(False, verified,
            "current HTTPS route verified; signed account audit still required" if verified
            else "current HTTPS route returned invalid API data")
    except (OSError, ValueError) as exc:
        return DnsRecoveryResult(False, False, "current HTTPS route failed: " + type(exc).__name__)


def is_dns_resolution_failure(message: object) -> bool:
    text = str(message).lower()
    return any(token in text for token in (
        "getaddrinfo failed", "errno 11004", "name or service not known",
        "temporary failure in name resolution", "nodename nor servname provided",
    ))


def network_failure_summary(message: object) -> str:
    text = str(message).lower()
    if is_dns_resolution_failure(text):
        label = "域名解析失败（检查当前代理连接）"
    elif "10054" in text or "connection reset" in text or "远程主机强迫关闭" in text:
        label = "远端重置连接，等待重新建立连接"
    elif "certificate" in text:
        label = "TLS证书验证失败"
    elif "handshake" in text:
        label = "TLS握手失败或超时"
    elif "50102" in text or "timestamp" in text:
        label = "API签名时间过期"
    elif "refused" in text:
        label = "连接被拒绝（检查代理端口）"
    elif "timed out" in text or "timeout" in text:
        label = "连接或响应超时"
    else:
        label = "API请求失败，等待只读核验"
    path = re.search(r"/api/v5/[a-z0-9/_-]+", text)
    return label + ("：" + path.group() if path else "")


def is_sustained_pre_request_network_failure(message: object) -> bool:
    """Classify failures that happen before an order body can be submitted."""
    text = str(message).lower()
    return is_dns_resolution_failure(text) or any(token in text for token in (
        "ssl handshake", "tls handshake", "handshake timed out",
        "connection timed out", "connect timeout", "connection refused",
        "no route to host", "network is unreachable",
    ))


def recover_default_physical_adapter_dns(*, runner=subprocess.run) -> DnsRecoveryResult:
    """Set DNS only on the active physical adapter that owns the IPv4 default route.

    The caller must already have paused new order submission.  This function never
    launches an elevation prompt: an unelevated process receives a clean failure.
    """
    if os.name != "nt":
        return DnsRecoveryResult(False, False, "DNS self-heal is Windows-only")
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    lock_path = os.path.join(tempfile.gettempdir(), "codexquantbot-dns-recovery.lock")
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(lock_path) <= 120:
                return DnsRecoveryResult(False, False, "another QuantBot instance is already optimizing DNS")
            os.unlink(lock_path)
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return DnsRecoveryResult(False, False, "another QuantBot instance is already optimizing DNS")
    os.write(lock_fd, f"{os.getpid()} {time.time()}".encode())
    os.close(lock_fd)
    attempts: list[str] = []
    changed = False
    try:
        for primary, secondary in DNS_CANDIDATE_PAIRS:
            script = (
                "$ErrorActionPreference='Stop';"
                "$route=Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
                "Where-Object {$_.State -eq 'Alive'} | "
                "Sort-Object RouteMetric,InterfaceMetric | Select-Object -First 1;"
                "if(-not $route){throw 'No active IPv4 default route'};"
                "$adapter=Get-NetAdapter -InterfaceIndex $route.InterfaceIndex;"
                "if(-not $adapter.HardwareInterface -or $adapter.Status -ne 'Up'){"
                "throw 'Default route is not an active physical adapter'};"
                f"Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses @('{primary}','{secondary}');"
                "Clear-DnsClientCache;"
                "$adapter.Name"
            )
            try:
                completed = runner(
                    [powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                     "-Command", script],
                    capture_output=True, text=True, timeout=30, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                attempts.append(f"{primary}/{secondary}: adjustment error {exc}")
                continue
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "administrator permission required").strip()
                attempts.append(f"{primary}/{secondary}: {detail}")
                continue
            changed = True
            adapter_name = (completed.stdout or "physical default adapter").strip().splitlines()[-1]
            try:
                socket.getaddrinfo(OKX_DNS_PROBE_HOST, 443)
                with urlopen(OKX_PUBLIC_TIME_URL, timeout=20) as response:
                    payload = json.loads(response.read().decode())
                verified = (int(getattr(response, "status", 200)) == 200
                            and str(payload.get("code")) == "0")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                attempts.append(f"{primary}/{secondary}: verification failed {exc}")
                continue
            if verified:
                return DnsRecoveryResult(
                    True, True,
                    f"DNS optimization verified on {adapter_name}: {primary}, {secondary}",
                )
            attempts.append(f"{primary}/{secondary}: OKX returned invalid data")
        return DnsRecoveryResult(changed, False, "all DNS candidates failed; " + " | ".join(attempts))
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
