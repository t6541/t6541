from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    download_url: str
    sha256: str
    changelog: tuple[str, ...]


def version_tuple(value: str) -> tuple[int, int, int]:
    parts = value.strip().lstrip("v").split(".")
    if len(parts) != 3 or any(not item.isdigit() for item in parts):
        raise ValueError("version must use major.minor.patch")
    return tuple(int(item) for item in parts)  # type: ignore[return-value]


def load_manifest(location: str | Path) -> UpdateManifest:
    text: str
    location_text = str(location)
    parsed = urlparse(location_text)
    windows_drive = len(parsed.scheme) == 1 and len(location_text) > 1 and location_text[1] == ":"
    if parsed.scheme and not windows_drive:
        if parsed.scheme != "https":
            raise ValueError("online update manifests must use HTTPS")
        request = Request(location_text, headers={"User-Agent": "CodexQuantBot-Updater/0.5"})
        with urlopen(request, timeout=20) as response:
            text = response.read().decode("utf-8")
    else:
        text = Path(location).read_text(encoding="utf-8")
    raw = json.loads(text)
    manifest = UpdateManifest(
        str(raw["version"]), str(raw.get("download_url", "")), str(raw.get("sha256", "")).upper(),
        tuple(str(item) for item in raw.get("changelog", ())),
    )
    version_tuple(manifest.version)
    if manifest.download_url:
        if urlparse(manifest.download_url).scheme != "https":
            raise ValueError("update downloads must use HTTPS")
        if len(manifest.sha256) != 64 or any(ch not in "0123456789ABCDEF" for ch in manifest.sha256):
            raise ValueError("update SHA-256 is invalid")
    return manifest


def is_newer(candidate: str, current: str) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def download_verified_update(manifest: UpdateManifest, destination: str | Path) -> Path:
    if not manifest.download_url:
        raise ValueError("manifest does not contain a download URL")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    request = Request(manifest.download_url, headers={"User-Agent": "CodexQuantBot-Updater/0.5"})
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
        if digest.hexdigest().upper() != manifest.sha256:
            raise ValueError("downloaded update failed SHA-256 verification")
        temporary.replace(destination)
        return destination
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
