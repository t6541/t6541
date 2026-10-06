from urllib.error import URLError
import pytest
from quantbot import live_audit
from quantbot.live_ui_state import is_retryable_aggressive_get_error
from quantbot.okx import OkxError


def test_real_audit_error_envelope_routes_connection_reset_to_outer_recovery(monkeypatch):
    calls=[]
    def reset(*args,**kwargs):
        calls.append(True)
        raise URLError(ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接。'))
    monkeypatch.setattr(live_audit,'urlopen',reset)
    monkeypatch.setattr(live_audit.time,'sleep',lambda _:None)
    client=live_audit.OkxLiveReadOnlyClient(live_audit.LiveAuditCredentials('test','test','test'))
    with pytest.raises(OkxError) as error:
        client.audit()
    message=str(error.value)
    assert 'GET /api/v5/' in message and 'failed after 5 attempts' in message
    assert is_retryable_aggressive_get_error(message)
    assert calls
