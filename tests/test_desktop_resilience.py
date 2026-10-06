from quantbot import win32desktop
from quantbot.okx import OkxError


def test_order_notice_helper_queues_every_new_order(monkeypatch):
    queued = []
    monkeypatch.setattr(
        win32desktop, "_queue_order_notice",
        lambda strategy, order_id: queued.append((strategy, order_id)),
    )
    win32desktop._queue_order_notices("策略01", "A,B")
    assert queued == [("策略01", "A"), ("策略01", "B")]


def test_order_event_handler_refreshes_embedded_panel_instead_of_popup():
    from pathlib import Path
    source = Path(win32desktop.__file__).read_text(encoding="utf-8")
    branch = source.split("if message == WM_ORDER_NOTICE:", 1)[1].split("if message == WM_COMMAND:", 1)[0]
    assert "_main_trade_panel_worker" in branch
    assert "_show_order_notice" not in branch


def test_trading_cycle_window_is_fullscreen_and_separates_long_short_conditions():
    from pathlib import Path
    from quantbot.trade_cycle_rules import LONG_CYCLE_ROWS, SHORT_CYCLE_ROWS
    source = Path(win32desktop.__file__).read_text(encoding="utf-8")
    branch = source.split("def _show_trading_cycles()", 1)[1].split("def _ma_endpoints_worker", 1)[0]
    assert any("进入MA5内侧" in row[1] for row in LONG_CYCLE_ROWS)
    assert any("进入MA5内侧" in row[1] for row in SHORT_CYCLE_ROWS)
    assert any("45%" in row[1] for row in SHORT_CYCLE_ROWS)
    assert "SW_MAXIMIZE" in branch
    assert "trading_cycles_long_table" in branch
    assert "trading_cycles_short_table" in branch


def test_restore_timeout_is_isolated_from_strategy_cycle(monkeypatch, tmp_path):
    def fail(*_):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(win32desktop, "_restore_saved_demo_state", fail)
    results, error = win32desktop._attempt_restore_saved_demo_state(
        object(), tmp_path / "state.sqlite3")
    assert results == ()
    assert "read timed out" in error


def test_account05_read_only_audit_retries_without_faulting(monkeypatch):
    attempts = []
    waits = []
    statuses = []
    expected = {"account": {"acctLv": "2", "posMode": "long_short_mode"}}

    class AuditClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def audit(self):
            attempts.append(True)
            if len(attempts) < 3:
                raise OkxError(
                    "OKX live read-only audit GET /api/v5/account/balance "
                    "failed after 5 attempts: The handshake operation timed out"
                )
            return expected

    class StopEvent:
        def is_set(self):
            return False

        def wait(self, seconds):
            waits.append(seconds)

    monkeypatch.setattr(win32desktop, "OkxLiveReadOnlyClient", AuditClient)
    monkeypatch.setattr(
        win32desktop, "_live_status_text",
        lambda slot, message: statuses.append((slot, message)),
    )

    result = win32desktop._account05_audit_with_recovery(
        object(), StopEvent(), phase="启动")

    assert result is expected
    assert len(attempts) == 3
    assert waits == [10, 15]
    assert all(slot == "clone_research" for slot, _ in statuses)
    assert all("不下单" in message for _, message in statuses)


def test_account05_market_get_retries_without_faulting(monkeypatch):
    attempts, waits, statuses = [], [], []

    class StopEvent:
        def is_set(self):
            return False

        def wait(self, seconds):
            waits.append(seconds)

    def operation():
        attempts.append(True)
        if len(attempts) < 3:
            raise OkxError(
                "OKX network error after 4 GET attempts: "
                "<urlopen error _ssl.c:993: The handshake operation timed out>"
            )
        return {"recovered": True}

    monkeypatch.setattr(
        win32desktop, "_live_status_text",
        lambda slot, message: statuses.append((slot, message)),
    )
    result = win32desktop._account05_get_with_recovery(
        operation, StopEvent(), phase="行情/持仓/止盈读取")

    assert result == {"recovered": True}
    assert waits == [10, 15]
    assert all("不下单" in message for _, message in statuses)


def test_account05_unknown_post_outcome_still_stops_immediately():
    class StopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            raise AssertionError("unknown POST outcome must not retry")

    def operation():
        raise OkxError("OKX Live POST outcome unknown after TLS timeout")

    try:
        win32desktop._account05_get_with_recovery(
            operation, StopEvent(), phase="订单处理")
    except OkxError as exc:
        assert "POST outcome unknown" in str(exc)
    else:
        raise AssertionError("unknown POST outcome must escape to fault handling")


def test_account05_twenty_percent_gate_keeps_worker_alive_for_recovery():
    from pathlib import Path
    source = Path(win32desktop.__file__).read_text(encoding="utf-8")
    worker = source.split("def _account05_automatic_worker()", 1)[1].split(
        "def _account05_audit_with_recovery", 1)[0]
    assert 'if result.action == "loss_limit_blocked"' not in worker
    assert "stop_event.wait(5)" in worker
