import pytest

from quantbot.live_ui_state import (aggressive_button_text, aggressive_click_transition,
                                    aggressive_live_audit_error,
                                    aggressive_network_backoff_seconds,
                                    is_confirmed_aggressive_post_rejection,
                                    is_retryable_aggressive_get_error)


def test_aggressive_button_has_unambiguous_start_stop_cycle():
    assert aggressive_button_text("stopped").startswith("▶ 启动")
    assert aggressive_click_transition("stopped") == "starting"
    assert "正在启动" in aggressive_button_text("starting")
    assert aggressive_button_text("running").startswith("■ 停止")
    assert aggressive_click_transition("running") == "stopping"
    assert "正在停止" in aggressive_button_text("stopping")
    assert aggressive_button_text("faulted").startswith("▶ 重新启动")


def test_aggressive_button_rejects_unknown_state():
    with pytest.raises(ValueError):
        aggressive_button_text("closed")


def test_aggressive_get_ssl_timeout_is_safe_for_unattended_reconnect():
    assert is_retryable_aggressive_get_error(
        "OKX network error after 4 GET attempts: <urlopen error _ssl.c:993: SSL安全连接握手超时>")
    assert is_retryable_aggressive_get_error("OKX Live GET failed: timed out")
    assert is_retryable_aggressive_get_error(
        "OKX live read-only audit failed after 3 attempts: TLS handshake timed out")
    assert is_retryable_aggressive_get_error(
        "OKX Live pre-order server-time synchronization GET failed after explicit "
        "rejection: OKX server-time synchronization failed: SSL handshake timed out")
    assert is_retryable_aggressive_get_error(
        "OKX server-time synchronization failed: <urlopen error [SSL: "
        "UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol>")
    assert is_retryable_aggressive_get_error(
        "OKX live read-only audit failed after 5 attempts: <urlopen error [Errno 11004] getaddrinfo failed>")
    assert is_retryable_aggressive_get_error(
        "OKX Live GET error 51054: Request timed out. Please try again.")
    assert is_retryable_aggressive_get_error(
        "OKX live read-only audit GET error 51054: Request timed out. Please try again.")
    assert is_retryable_aggressive_get_error(
        "OKX Live GET error 51290: Trading bot engine currently upgrading. Try again later.")
    assert is_retryable_aggressive_get_error(
        "OKX live read-only audit GET error 51290: Trading bot engine currently upgrading. Try again later.")


def test_aggressive_unknown_post_result_never_auto_retries():
    assert not is_retryable_aggressive_get_error(
        "OKX network error after single-shot POST: timed out")
    assert not is_retryable_aggressive_get_error(
        "OKX Live POST error 51054: Request timed out. Please try again.")
    assert not is_retryable_aggressive_get_error(
        "OKX Live POST error 51290: Trading bot engine currently upgrading. Try again later.")


@pytest.mark.parametrize("reason", [
    "<urlopen error [WinError 10054] 远程主机强迫关闭了一个现有的连接。>",
    "[WinError 10053] connection aborted", "Remote end closed connection without response",
    "TLS handshake timed out", "[Errno 11004] getaddrinfo failed",
])
def test_endpoint_bearing_audit_failure_remains_recoverable(reason):
    assert is_retryable_aggressive_get_error(
        "OKX live read-only audit GET /api/v5/account/config failed after 5 attempts: " + reason)


@pytest.mark.parametrize("reason", [
    "OKX Live POST outcome unknown: getaddrinfo failed",
    "OKX Live POST outcome unknown: OKX Live GET failed during reconciliation",
    "OKX outcome is unknown: [Errno 11004] getaddrinfo failed",
    "OKX live read-only GET /api/v5/account/config HTTP 401",
    "OKX live read-only audit GET error 50113: Invalid signature",
    "[WinError 10054] 远程主机强迫关闭了一个现有的连接。",
])
def test_unknown_writes_authentication_and_unscoped_reset_are_not_retried(reason):
    assert not is_retryable_aggressive_get_error(reason)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_audit_temporary_http_errors_reenter_recovery(status):
    assert is_retryable_aggressive_get_error(
        f"OKX live read-only GET /api/v5/account/config HTTP {status}")


def test_confirmed_post_rejection_continues_loop_without_repeating_request():
    assert is_confirmed_aggressive_post_rejection(
        "OKX Live POST error 1: All operations failed")
    assert is_confirmed_aggressive_post_rejection(
        "OKX Live request rejected: 51008 insufficient balance")
    assert not is_confirmed_aggressive_post_rejection(
        "OKX Live POST outcome unknown; automatic engine stopped")


def test_aggressive_network_recovery_probes_quickly_and_caps_at_thirty_seconds():
    assert [aggressive_network_backoff_seconds(value) for value in (1, 2, 3, 4, 5, 20)] == [10, 15, 20, 30, 30, 30]


def test_unattended_recovery_audit_revalidates_execution_invariants():
    valid = {
        "account": {"acctLv": "2", "posMode": "long_short_mode"},
        "leverage": [{"posSide": "long", "lever": "100"},
                     {"posSide": "short", "lever": "100"}],
        "minimum_order": {"api_size_contracts": "0.01"},
    }
    assert aggressive_live_audit_error(valid) == ""
    invalid = {**valid, "account": {"acctLv": "1", "posMode": "net_mode"}}
    assert "account mode" in aggressive_live_audit_error(invalid)
