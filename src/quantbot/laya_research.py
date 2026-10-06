"""Read-only Laya research adapter.

The adapter deliberately returns research data only.  It never produces an
execution signal and persisted rows are recorded through StateStore's
decision_eligible=0 path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LayaResearchResult:
    event_id: str
    instrument: str
    event_time: str
    event_type: str
    response: dict

    @property
    def decision_eligible(self) -> bool:
        return False


class LayaResearchClient:
    """Call a local Laya service without coupling it to execution."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", *, timeout: float = 8.0,
                 opener=urlopen):
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._opener = opener

    def analyze(self, *, instrument: str, state, questions: dict,
                event_id: str | None = None, event_time: str | None = None) -> LayaResearchResult:
        if not instrument.strip():
            raise ValueError("instrument is required")
        event_id = event_id or f"laya-{instrument}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        event_time = event_time or datetime.now(timezone.utc).isoformat()
        payload = {"state": state, "questions": questions}
        request = Request(
            f"{self.base_url}/v1/systemone",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with self._opener(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("Laya response must be a JSON object")
        return LayaResearchResult(event_id, instrument, event_time, "laya_analysis", body)

    @staticmethod
    def persist(store, result: LayaResearchResult) -> bool:
        """Persist as external research; StateStore enforces non-decisional storage."""
        return store.record_external_research_signal(
            event_id=result.event_id, source="laya", instrument=result.instrument,
            event_time=result.event_time, event_type=result.event_type,
            payload={"decision_eligible": False, "response": result.response},
        )
