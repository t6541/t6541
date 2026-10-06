"""Local diagnostics for a stopped automatic execution worker."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import traceback


def write_live_fault(workspace: Path, slot: str, exc: BaseException) -> Path:
    """Record the traceback without turning a diagnostic failure into a retry."""
    path = workspace / "profiles" / slot / "automatic-fault.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        path.replace(path.with_suffix(".previous.log"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {slot}\n")
        stream.writelines(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return path
