from quantbot.live_audit import LiveAuditCredentials
from quantbot.live_credential_store import (
    delete_live_credentials, load_live_credentials, save_live_credentials,
)


def test_windows_live_encrypted_credentials_round_trip(tmp_path):
    path = tmp_path / "live-credentials.bin"
    expected = LiveAuditCredentials("live-key", "live-secret", "live-passphrase")
    save_live_credentials(path, expected)
    assert path.read_bytes()
    assert b"live-secret" not in path.read_bytes()
    assert load_live_credentials(path) == expected
    delete_live_credentials(path)
    assert load_live_credentials(path) is None
