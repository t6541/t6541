"""Per-subaccount Live candidate ledger and manual-approval gate.

This module never talks to OKX.  Its job is to make candidate limits,
cooldowns, and one-time human approval durable and atomic before an execution
adapter is allowed to exist.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .live_strategy import LIVE_PROFILES, LiveCandidate


LIVE_APPROVAL_PREFIX = "LIVE-MANUAL"


def _stored_utc(value: str) -> datetime:
    """Read both current aware timestamps and legacy naive UTC timestamps."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def candidate_identifier(slot: str, candidate: LiveCandidate) -> str:
    if slot not in LIVE_PROFILES or candidate.profile != slot:
        raise ValueError("Live candidate does not belong to this profile")
    material = json.dumps({
        "slot": slot,
        "bar": candidate.confirmed_bar_time,
        "direction": candidate.direction,
        "entry": candidate.entry_reference,
        "stop": candidate.stop_loss,
        "target": candidate.take_profit,
        "contracts": candidate.contracts,
        "policy": candidate.policy_version,
    }, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(material).hexdigest()[:20].upper()


def approval_phrase(candidate_id: str) -> str:
    return f"{LIVE_APPROVAL_PREFIX}:{candidate_id}"


class LiveStateStore:
    """SQLite ledger intended to live in one profile-specific directory."""

    def __init__(self, path: str | Path, slot: str):
        if slot not in LIVE_PROFILES:
            raise ValueError("Unknown Live risk profile")
        self.path = Path(path)
        self.slot = slot
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    slot TEXT NOT NULL,
                    trading_day TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','expired')),
                    approved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS candidates_slot_day
                    ON candidates(slot, trading_day, created_at);
                CREATE TABLE IF NOT EXISTS engine_cycles (
                    slot TEXT NOT NULL,
                    trading_day TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(slot, created_at)
                );
            """)

    def register_candidate(self, candidate: LiveCandidate, *, now: datetime | None = None) -> str:
        if not candidate.eligible:
            raise ValueError("Ineligible Live candidate cannot be persisted")
        if candidate.profile != self.slot:
            raise ValueError("Live candidate profile mismatch")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Live timestamps must be timezone-aware")
        current = current.astimezone(timezone.utc)
        trading_day = current.date().isoformat()
        created_at = current.isoformat()
        spec = LIVE_PROFILES[self.slot]
        candidate_id = candidate_identifier(self.slot, candidate)
        payload = json.dumps(asdict(candidate), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT candidate_id FROM candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if existing:
                connection.rollback()
                return candidate_id
            latest = connection.execute(
                "SELECT created_at FROM candidates WHERE slot=? ORDER BY created_at DESC LIMIT 1",
                (self.slot,),
            ).fetchone()
            if latest:
                previous = _stored_utc(latest[0])
                elapsed = (current - previous).total_seconds()
                if elapsed < int(spec["cooldown_minutes"]) * 60:
                    connection.rollback()
                    raise ValueError("Live candidate cooldown is active")
            connection.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,'pending',NULL)",
                (candidate_id, self.slot, trading_day, created_at, payload),
            )
            connection.commit()
        return candidate_id

    def approve_once(self, candidate_id: str, phrase: str, *, now: datetime | None = None) -> dict:
        if phrase != approval_phrase(candidate_id):
            raise ValueError("Exact Live manual approval phrase required")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload,status FROM candidates WHERE candidate_id=? AND slot=?",
                (candidate_id, self.slot),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ValueError("Unknown Live candidate")
            if row["status"] != "pending":
                connection.rollback()
                raise ValueError("Live approval was already consumed")
            connection.execute(
                "UPDATE candidates SET status='approved', approved_at=? WHERE candidate_id=?",
                (current, candidate_id),
            )
            connection.commit()
        return json.loads(row["payload"])

    def pending_candidate(self, candidate_id: str) -> dict:
        """Read, but never consume, the candidate needed for fresh preflight."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload,status FROM candidates WHERE candidate_id=? AND slot=?",
                (candidate_id, self.slot),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown Live candidate")
        if row["status"] != "pending":
            raise ValueError("Live approval was already consumed")
        return json.loads(row["payload"])

    def daily_candidate_count(self, trading_day: str) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM candidates WHERE slot=? AND trading_day=?",
                (self.slot, trading_day),
            ).fetchone()[0])

    def claim_automatic_cycle(self, *, now: datetime | None = None) -> int:
        """Atomically enforce the automatic profile's daily/cooldown budget."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Live timestamps must be timezone-aware")
        current = current.astimezone(timezone.utc)
        day = current.date().isoformat()
        created_at = current.isoformat()
        spec = LIVE_PROFILES[self.slot]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = int(connection.execute(
                "SELECT COUNT(*) FROM engine_cycles WHERE slot=? AND trading_day=?",
                (self.slot, day),
            ).fetchone()[0])
            latest = connection.execute(
                "SELECT created_at FROM engine_cycles WHERE slot=? ORDER BY created_at DESC LIMIT 1",
                (self.slot,),
            ).fetchone()
            if latest and (current - _stored_utc(latest[0])).total_seconds() < int(
                    spec["cooldown_minutes"]) * 60:
                connection.rollback()
                raise ValueError("Live candidate cooldown is active")
            connection.execute("INSERT INTO engine_cycles VALUES (?,?,?)", (self.slot, day, created_at))
            connection.commit()
        return count + 1
