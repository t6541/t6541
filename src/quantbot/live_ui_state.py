"""Pure state machine for the aggressive Live start/stop control."""

from __future__ import annotations
import re


AGGRESSIVE_STATES = frozenset({"stopped", "starting", "running", "stopping", "faulted"})


def aggressive_live_audit_error(audit: dict) -> str:
    """Return any invariant that blocks unattended Live execution."""
    account = audit.get("account", {})
    leverage = {str(item.get("posSide")): str(item.get("lever"))
                for item in audit.get("leverage", [])}
    if account.get("acctLv") != "2" or account.get("posMode") != "long_short_mode":
        return "aggressive account mode is not single-currency margin with hedge positions"
    if leverage.get("long") != "100" or leverage.get("short") != "100":
        return "aggressive long and short leverage must both be 100"
    if str(audit.get("minimum_order", {}).get("api_size_contracts")) != "0.01":
        return "exchange minimum order size is no longer 0.01 contracts"
    return ""


def aggressive_button_text(state: str) -> str:
    if state not in AGGRESSIVE_STATES:
        raise ValueError("Unknown aggressive control state")
    return {
        "stopped": "▶ 启动激进型自动实盘",
        "starting": "正在启动激进型自动实盘…",
        "running": "■ 停止激进型自动实盘",
        "stopping": "正在停止并撤销预埋单…",
        "faulted": "▶ 重新启动激进型自动实盘",
    }[state]


def aggressive_click_transition(state: str) -> str:
    if state in {"stopped", "faulted"}:
        return "starting"
    if state in {"starting", "running"}:
        return "stopping"
    return "stopping"


def is_retryable_aggressive_get_error(message: str) -> bool:
    """Only auto-recover failures that are known to precede any order POST."""
    text = str(message).lower()
    if ("post" in text or "outcome unknown" in text or "outcome is unknown" in text
            or "live order error" in text):
        return False
    # v178 added the endpoint between "audit GET" and "failed". Match the
    # known read-only transport envelope, not the localized socket text.
    if re.search(r"okx live read-only audit get /api/v5/[a-z0-9/_-]+ failed after \d+ attempts:", text):
        return True
    if re.search(r"okx live (?:read-only get /api/v5/[a-z0-9/_-]+ |)http (?:429|500|502|503|504)\b", text):
        return True
    return ("okx live get failed" in text
            or ("okx server-time synchronization failed" in text
                and any(token in text for token in (
                    "ssl", "tls", "unexpected_eof", "timed out", "timeout",
                    "urlopen", "connection", "network")))
            or "pre-order server-time synchronization get failed after explicit rejection" in text
            or "okx live read-only audit failed" in text
            or "network error after 4 get attempts" in text
            or "getaddrinfo failed" in text
            or "errno 11004" in text
            or ("get error 51054" in text and "request timed out" in text)
            or ("get error 51290" in text and "trading bot engine" in text)
            or ("get attempts" in text and any(token in text for token in (
                "ssl", "timed out", "timeout", "urlopen", "connection", "network"))))


def is_confirmed_aggressive_post_rejection(message: str) -> bool:
    """Return true only when OKX explicitly rejected and created no order."""
    text = str(message).lower()
    if "outcome unknown" in text or "outcome is unknown" in text:
        return False
    return ("okx live post error" in text
            or "okx live request rejected" in text
            or "okx live order error" in text)


def aggressive_network_backoff_seconds(consecutive_failures: int) -> int:
    """Probe recovery promptly without hammering the API during an outage."""
    failure = max(1, int(consecutive_failures))
    return (10, 15, 20)[failure - 1] if failure <= 3 else 30
