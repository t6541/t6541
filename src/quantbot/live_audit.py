"""Strictly read-only OKX Live account audit.

This module is intentionally separate from the Demo execution adapter.  It has
no order, cancel, leverage-change, transfer, or withdrawal methods.  Every
request is a signed GET to a small, fixed allow-list.
"""

from __future__ import annotations


import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request
from .http_transport import urlopen

from .okx import BASE_URL, OkxError, OkxDemoClient, locked_base_url
from .live_sizing import minimum_contract_order


LIVE_CONFIRMATION = "LIVE-READ-ONLY-AUDIT"
LIVE_INSTRUMENT = "ETH-USDT-SWAP"
LIVE_AUDIT_NETWORK_ATTEMPTS = 5
LIVE_AUDIT_RETRY_DELAYS = (0.5, 1.5, 3.0, 5.0)


@dataclass(frozen=True)
class LiveAuditCredentials:
    api_key: str
    secret_key: str
    passphrase: str

    @classmethod
    def from_environment(cls) -> "LiveAuditCredentials":
        names = ("OKX_LIVE_API_KEY", "OKX_LIVE_SECRET_KEY", "OKX_LIVE_PASSPHRASE")
        values = [os.environ.get(name, "") for name in names]
        if not all(values):
            missing = ", ".join(name for name, value in zip(names, values) if not value)
            raise OkxError(f"Missing live audit credentials: {missing}")
        return cls(*values)


class OkxLiveReadOnlyClient:
    """A signed Live client whose transport permits only approved GET paths."""

    _PRIVATE_PATHS = frozenset({
        "/api/v5/account/config",
        "/api/v5/account/balance",
        "/api/v5/account/positions",
        "/api/v5/account/leverage-info",
        "/api/v5/trade/orders-pending",
        "/api/v5/trade/orders-algo-pending",
    })
    _PUBLIC_PATHS = frozenset({
        "/api/v5/public/instruments",
        "/api/v5/public/mark-price",
        "/api/v5/market/ticker",
    })

    def __init__(self, credentials: LiveAuditCredentials | None = None, *, timeout: int = 8,
                 base_url: str = BASE_URL):
        self.credentials = credentials
        self.timeout = timeout
        self.base_url = locked_base_url(base_url)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _request(self, method: str, path: str, payload: dict | None = None, *, private: bool) -> dict:
        if method != "GET":
            raise OkxError("Live audit transport rejects every non-GET request")
        allowed = self._PRIVATE_PATHS if private else self._PUBLIC_PATHS
        if path not in allowed:
            raise OkxError(f"Live audit path is not allow-listed: {path}")
        request_path = path + ("?" + urlencode(payload) if payload else "")
        signer = OkxDemoClient(self.credentials, timeout=self.timeout, base_url=self.base_url)
        def build_request():
            headers = {"Content-Type": "application/json", "User-Agent": "CodexQuantBot/live-read-only"}
            if private:
                credentials = self.credentials or LiveAuditCredentials.from_environment()
                timestamp = signer._timestamp()
                signature = base64.b64encode(hmac.new(
                    credentials.secret_key.encode(), f"{timestamp}GET{request_path}".encode(), hashlib.sha256
                ).digest()).decode()
                headers.update({
                    "OK-ACCESS-KEY": credentials.api_key,
                    "OK-ACCESS-SIGN": signature,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": credentials.passphrase,
                })
            # Deliberately no x-simulated-trading header: this is a Live read-only audit.
            return Request(self.base_url + request_path, headers=headers, method="GET")

        result = None
        timestamp_retry_used = False
        for attempt in range(LIVE_AUDIT_NETWORK_ATTEMPTS):
            request = build_request()
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode())
                if private and not timestamp_retry_used and signer._is_timestamp_error(result):
                    signer._sync_server_time()
                    timestamp_retry_used = True
                    continue
                break
            except HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                if private and not timestamp_retry_used and signer._is_timestamp_error(detail):
                    signer._sync_server_time()
                    timestamp_retry_used = True
                    continue
                if exc.code in {429, 500, 502, 503, 504} and attempt < LIVE_AUDIT_NETWORK_ATTEMPTS - 1:
                    time.sleep(LIVE_AUDIT_RETRY_DELAYS[attempt])
                    continue
                # Authentication failures are actionable only when OKX's code
                # and message survive the urllib HTTPError envelope.
                error_code = ""
                error_message = detail.strip()
                try:
                    error_payload = json.loads(detail)
                    error_code = str(error_payload.get("code") or "")
                    error_message = str(error_payload.get("msg") or error_message)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                suffix = ""
                if error_code or error_message:
                    suffix = f"｜OKX code={error_code or '?'} msg={error_message or '?'}"
                raise OkxError(
                    f"OKX live read-only GET {path} HTTP {exc.code}{suffix}"
                ) from exc
            except (OSError, ValueError) as exc:
                if attempt < LIVE_AUDIT_NETWORK_ATTEMPTS - 1:
                    # DNS/TLS/connect errors happen before this read-only GET
                    # can mutate exchange state. Give Windows DNS time to
                    # recover instead of exhausting retries in 0.75 seconds.
                    time.sleep(LIVE_AUDIT_RETRY_DELAYS[attempt])
                    continue
                raise OkxError(
                    f"OKX live read-only audit GET {path} failed after {LIVE_AUDIT_NETWORK_ATTEMPTS} attempts: {exc}"
                ) from exc
        if result is None:
            raise OkxError("OKX live read-only audit failed after retries")
        if str(result.get("code", "0")) != "0":
            raise OkxError(
                f"OKX live read-only audit GET error {result.get('code')}: {result.get('msg')}"
            )
        return result

    def audit(self, inst_id: str = LIVE_INSTRUMENT) -> dict:
        if inst_id != LIVE_INSTRUMENT:
            raise ValueError("Live audit is locked to ETH-USDT-SWAP")
        # Read sequentially.  Nine concurrent/new urllib connections create a TLS
        # handshake burst on some Windows/proxy routes and caused intermittent
        # failures on whichever endpoint happened to lose the race.
        swap = {"instType": "SWAP", "instId": inst_id}
        jobs = [
            ("/api/v5/account/config", None, True),
            ("/api/v5/account/balance", {"ccy": "USDT"}, True),
            ("/api/v5/account/positions", swap, True),
            ("/api/v5/account/leverage-info", {"instId": inst_id, "mgnMode": "cross"}, True),
            ("/api/v5/trade/orders-pending", swap, True),
            ("/api/v5/trade/orders-algo-pending", {**swap, "ordType": "conditional"}, True),
            ("/api/v5/public/instruments", swap, False),
            ("/api/v5/public/mark-price", swap, False),
            ("/api/v5/market/ticker", {"instId": inst_id}, False),
        ]
        def read(job):
            path, payload, private = job
            return self._request("GET", path, payload, private=private)
        config, balance, positions, leverage, orders, algo_orders, instrument, mark, ticker = (
            read(job) for job in jobs)
        return summarize_audit(config, balance, positions, leverage, instrument, mark, ticker, orders, algo_orders)


def summarize_audit(config: dict, balance: dict, positions: dict, leverage: dict, instrument: dict,
                    mark: dict, ticker: dict, orders: dict | None = None,
                    algo_orders: dict | None = None) -> dict:
    """Return an intentionally minimal report that never serializes credentials or account IDs."""
    account = (config.get("data") or [{}])[0]
    details = ((balance.get("data") or [{}])[0].get("details") or [{}])[0]
    contract = (instrument.get("data") or [{}])[0]
    quote = (ticker.get("data") or [{}])[0]
    mark_quote = (mark.get("data") or [{}])[0]
    open_positions = [item for item in positions.get("data", []) if abs(float(item.get("pos") or 0)) > 0]
    return {
        "mode": "live-read-only",
        "execution_permitted": False,
        "instrument": {
            key: contract.get(key, "") for key in ("instId", "state", "ctVal", "ctValCcy", "ctType", "settleCcy", "lotSz", "minSz", "maxLmtSz", "maxMktSz")
        },
        "account": {"acctLv": account.get("acctLv", ""), "posMode": account.get("posMode", "")},
        "usdt_balance": {key: details.get(key, "") for key in ("ccy", "eq", "cashBal", "availBal")},
        "leverage": [{key: item.get(key, "") for key in ("instId", "mgnMode", "posSide", "lever")} for item in leverage.get("data", [])],
        "market": {"last": quote.get("last", ""), "markPx": mark_quote.get("markPx", "")},
        "minimum_order": minimum_contract_order(contract, mark_quote.get("markPx", "")),
        "open_positions": [{key: item.get(key, "") for key in ("posSide", "pos", "avgPx", "mgnMode")} for item in open_positions],
        "open_orders": [{key: item.get(key, "") for key in ("ordId", "clOrdId", "side", "posSide", "sz")}
                        for item in (orders or {}).get("data", [])],
        "open_algo_orders": [{key: item.get(key, "") for key in ("algoId", "algoClOrdId", "side", "posSide", "sz")}
                             for item in (algo_orders or {}).get("data", [])],
        "manual_checks_required": [
            "Verify API-key permissions are Read and Trade only; Withdraw must be disabled.",
            "Verify the API-key IP allow-list and sub-account identity in the OKX web console.",
            "No order, transfer, withdrawal, cancellation, or leverage-setting was attempted by this audit.",
        ],
    }
