"""Single-shot, manually approved OKX Live minimum-order transport."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .live_audit import LIVE_INSTRUMENT, LiveAuditCredentials
from .live_state import LiveStateStore
from .live_strategy import LIVE_MIN_REWARD_RISK, LIVE_PROFILES
from .okx import BASE_URL, OkxError, locked_base_url


LIVE_ORDER_PATH = "/api/v5/trade/order"
LIVE_ORDER_CONTRACTS = "0.05"
_CLIENT_ID = re.compile(r"^[A-Za-z0-9]{1,32}$")


class OkxLiveManualClient:
    """Live client exposing exactly one POST operation: protected entry."""

    def __init__(self, credentials: LiveAuditCredentials, *, timeout: int = 8,
                 base_url: str = BASE_URL):
        self.credentials = credentials
        self.timeout = timeout
        self.base_url = locked_base_url(base_url)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _post_order(self, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":"))
        timestamp = self._timestamp()
        signature = base64.b64encode(hmac.new(
            self.credentials.secret_key.encode(),
            f"{timestamp}POST{LIVE_ORDER_PATH}{body}".encode(), hashlib.sha256,
        ).digest()).decode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CodexQuantBot/live-manual-minimum",
            "OK-ACCESS-KEY": self.credentials.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
        }
        # Deliberately no x-simulated-trading header: this is the Live endpoint.
        request = Request(self.base_url + LIVE_ORDER_PATH, data=body.encode(), headers=headers, method="POST")
        try:
            # Never retry a Live POST after an unknown network outcome.
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise OkxError(f"OKX Live HTTP {exc.code}: {detail}") from exc
        except (OSError, ValueError) as exc:
            raise OkxError(
                "OKX Live POST outcome is unknown; request was not retried. Audit the order in OKX before any further action: "
                f"{exc}"
            ) from exc
        if str(result.get("code", "")) != "0":
            raise OkxError(f"OKX Live order error {result.get('code')}: {result.get('msg')}")
        rows = result.get("data") or []
        if len(rows) != 1 or str(rows[0].get("sCode", "")) not in {"", "0"}:
            row = rows[0] if rows else {}
            raise OkxError(f"OKX Live order rejected: {row.get('sCode', '')} {row.get('sMsg', '')}")
        if not rows[0].get("ordId"):
            raise OkxError("OKX Live response did not include ordId")
        return {"ordId": str(rows[0]["ordId"]), "clOrdId": payload["clOrdId"]}

    def place_minimum_protected_order(self, candidate: dict, *, candidate_id: str,
                                      exchange_min_size: str) -> dict:
        profile = str(candidate.get("profile", ""))
        if profile not in LIVE_PROFILES:
            raise ValueError("Unknown Live candidate profile")
        if str(candidate.get("contracts")) != LIVE_ORDER_CONTRACTS or str(exchange_min_size) != "0.01":
            raise ValueError("Live execution is locked to 0.05 contracts on the verified 0.01 lot size")
        try:
            direction = int(candidate["direction"])
            entry = Decimal(str(candidate["entry_reference"]))
            stop = Decimal(str(candidate["stop_loss"]))
            target = Decimal(str(candidate["take_profit"]))
            planned_loss = Decimal(str(candidate["planned_loss_usdt"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Live candidate prices or risk are invalid") from exc
        if direction not in {-1, 1} or min(entry, stop, target) <= 0:
            raise ValueError("Live candidate direction or prices are invalid")
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0 or reward / risk < Decimal(str(LIVE_MIN_REWARD_RISK)):
            raise ValueError("Live reward/risk is below the policy minimum")
        if direction == 1 and not stop < entry < target:
            raise ValueError("Long protection prices are not ordered correctly")
        if direction == -1 and not target < entry < stop:
            raise ValueError("Short protection prices are not ordered correctly")
        if planned_loss > Decimal(str(LIVE_PROFILES[profile]["max_loss"])):
            raise ValueError("Live planned loss exceeds the profile limit")
        cl_ord_id = f"QBL{profile[0].upper()}{candidate_id}"[:32]
        algo_id = f"QBP{profile[0].upper()}{candidate_id}"[:32]
        if not _CLIENT_ID.fullmatch(cl_ord_id) or not _CLIENT_ID.fullmatch(algo_id):
            raise ValueError("Live client order identifier is invalid")
        side = "buy" if direction == 1 else "sell"
        pos_side = "long" if direction == 1 else "short"
        payload = {
            "instId": LIVE_INSTRUMENT,
            "tdMode": "cross",
            "side": side,
            "posSide": pos_side,
            "ordType": "market",
            "sz": LIVE_ORDER_CONTRACTS,
            "clOrdId": cl_ord_id,
            "attachAlgoOrds": [{
                "attachAlgoClOrdId": algo_id,
                "tpTriggerPx": format(target, "f"),
                "tpOrdPx": "-1",
                "tpTriggerPxType": "last",
                "slTriggerPx": format(stop, "f"),
                "slOrdPx": "-1",
                "slTriggerPxType": "mark",
            }],
        }
        return self._post_order(payload)


def execute_approved_minimum_order(store: LiveStateStore, client: OkxLiveManualClient,
                                   candidate_id: str, phrase: str, audit_report: dict) -> dict:
    """Consume one approval and submit once, only after a fresh clear audit."""
    if audit_report.get("execution_permitted") is not False:
        raise ValueError("Fresh Live read-only audit is required")
    if audit_report.get("open_orders") or audit_report.get("open_algo_orders"):
        raise ValueError("Live account already has a pending order")
    account = audit_report.get("account", {})
    if account.get("acctLv") != "2" or account.get("posMode") != "long_short_mode":
        raise ValueError("Live account mode is not approved")
    leverage = {str(x.get("posSide")): str(x.get("lever")) for x in audit_report.get("leverage", [])}
    if leverage.get("long") != "100" or leverage.get("short") != "100":
        raise ValueError("Live long and short cross leverage must both be 100x")
    exchange_min = str(audit_report.get("minimum_order", {}).get("api_size_contracts", ""))
    pending = store.pending_candidate(candidate_id)
    candidate_side = "long" if int(pending["direction"]) > 0 else "short"
    existing_sides = {str(item.get("posSide", "")) for item in audit_report.get("open_positions", [])
                      if abs(float(item.get("pos") or 0)) > 0}
    if candidate_side in existing_sides:
        raise ValueError("Live account already has same-side position")
    # Approval is atomically consumed before the single-shot POST. A failed or
    # unknown submission must be reconciled manually and cannot be clicked again.
    candidate = store.approve_once(candidate_id, phrase)
    result = client.place_minimum_protected_order(
        candidate, candidate_id=candidate_id, exchange_min_size=exchange_min,
    )
    return {**result, "candidate": candidate}
