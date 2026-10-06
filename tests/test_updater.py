import json

import pytest

from quantbot.updater import is_newer, load_manifest, version_tuple


def test_semantic_version_comparison():
    assert version_tuple("v0.5.0") == (0, 5, 0)
    assert is_newer("0.5.1", "0.5.0")
    assert not is_newer("0.5.0", "0.5.0")


def test_local_manifest_without_download_describes_current_release(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": "0.5.0", "changelog": ["安全更新"]}), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.version == "0.5.0"
    assert manifest.changelog == ("安全更新",)


def test_online_download_requires_sha256(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": "0.5.1", "download_url": "https://example.com/app.exe", "sha256": "bad"}), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        load_manifest(path)
