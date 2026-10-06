"""Windows DPAPI storage dedicated to OKX Live read-only credentials."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys

from .live_audit import LiveAuditCredentials
from .okx import OkxError


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, object]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise OkxError("Live API encrypted storage is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(b"CodexQuantBot-OKX-Live-ReadOnly-v1")
    output = DATA_BLOB()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if protect:
        ok = function(ctypes.byref(source), "Codex QuantBot OKX Live Read-Only",
                      ctypes.byref(entropy), None, None, 0x1, ctypes.byref(output))
    else:
        ok = function(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                      0x1, ctypes.byref(output))
    _ = source_buffer, entropy_buffer
    if not ok:
        raise OkxError(f"Windows Live credential encryption failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def save_live_credentials(path: Path, credentials: LiveAuditCredentials) -> None:
    payload = json.dumps({
        "environment": "live-read-only",
        "api_key": credentials.api_key,
        "secret_key": credentials.secret_key,
        "passphrase": credentials.passphrase,
    }, separators=(",", ":")).encode("utf-8")
    encrypted = _crypt(payload, protect=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load_live_credentials(path: Path) -> LiveAuditCredentials | None:
    if not path.exists():
        return None
    try:
        values = json.loads(_crypt(path.read_bytes(), protect=False).decode("utf-8"))
        if values.get("environment") != "live-read-only":
            raise ValueError("wrong credential environment")
        credentials = LiveAuditCredentials(
            values["api_key"], values["secret_key"], values["passphrase"])
        if not all((credentials.api_key, credentials.secret_key, credentials.passphrase)):
            raise ValueError("empty credential field")
        return credentials
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise OkxError(
            "Saved Live API credentials cannot be decrypted by this Windows user"
        ) from exc


def delete_live_credentials(path: Path) -> None:
    if path.exists():
        path.unlink()
