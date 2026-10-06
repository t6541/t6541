"""Strict Live adapter for the existing aggressive Demo strategy engine."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request
from .http_transport import urlopen
from zoneinfo import ZoneInfo

from .live_audit import LiveAuditCredentials
from .okx import BASE_URL, OkxDemoClient, OkxError


LIVE_AGGRESSIVE_CONTRACTS_PER_UNIT = Decimal("0.05")


class OkxLiveAggressiveAdapter(OkxDemoClient):
    """Expose only the endpoints required by strategy-01 aggressive execution.

    The inherited strategy methods still speak in one Demo test unit. Every
    opening, closing and protection request is translated to exactly 0.05 Live
    contracts at the final transport boundary.
    """

    opening_unit_contracts = str(LIVE_AGGRESSIVE_CONTRACTS_PER_UNIT)

    _GET_PATHS = frozenset({
        "/api/v5/account/config", "/api/v5/account/positions",
        "/api/v5/trade/orders-pending", "/api/v5/trade/orders-algo-pending",
        "/api/v5/trade/fills", "/api/v5/trade/fills-history",
        "/api/v5/public/mark-price", "/api/v5/market/ticker",
        "/api/v5/trade/order",
    })
    _POST_PATHS = frozenset({
        "/api/v5/trade/order", "/api/v5/trade/order-algo",
        "/api/v5/trade/cancel-batch-orders", "/api/v5/trade/cancel-algos",
        "/api/v5/trade/amend-algos",
    })

    def __init__(self, credentials: LiveAuditCredentials, timeout: int = 8,
                 base_url: str = BASE_URL, contracts_per_unit="0.05"):
        super().__init__(credentials, timeout, base_url)
        self._contracts_per_unit_source = contracts_per_unit
        self._contracts_per_unit()  # fail before any request if initial configuration is invalid
        self._daily_pnl_cache: tuple[float, object, float] | None = None

    def _contracts_per_unit(self) -> Decimal:
        raw = (self._contracts_per_unit_source() if callable(self._contracts_per_unit_source)
               else self._contracts_per_unit_source)
        value = Decimal(str(raw))
        if (not value.is_finite() or value < Decimal("0.01") or value > Decimal("1.00")
                or value % Decimal("0.01")):
            raise ValueError("Live contracts per unit must be 0.01-1.00 in 0.01 steps")
        return value

    @property
    def opening_unit_contracts(self) -> str:
        return format(self._contracts_per_unit(), "f")

    def _post_exp_time_ms(self) -> int:
        """Use the same OKX clock offset as the request signature."""
        offset = self._clock_offsets_ms.get(self.base_url, 0.0)
        return int(time.time() * 1000 + offset + 15_000)

    @staticmethod
    def _explicit_exp_time_rejection(result: dict) -> bool:
        data = result.get("data") or []
        return bool(data and all(isinstance(item, dict)
                                 and str(item.get("sCode")) == "50036"
                                 for item in data))

    def _live_size(self, payload):
        if isinstance(payload, list):
            return [self._live_size(item) for item in payload]
        if not isinstance(payload, dict):
            return payload
        converted = dict(payload)
        if "sz" in converted:
            units = Decimal(str(converted["sz"]))
            reduce_only = str(converted.get("reduceOnly", "")).lower() == "true"
            if units != units.to_integral_value() or units < 1 or units > (3 if reduce_only else 1):
                raise ValueError("Aggressive Live adapter accepts one opening unit or up to three reduce-only units")
            converted["sz"] = format(self._contracts_per_unit() * units, "f")
        return converted

    def _request(self, method: str, path: str, payload: dict | list | None = None,
                 private: bool = False, retry_safe_post: bool = False) -> dict:
        method = method.upper()
        allowed = self._GET_PATHS if method == "GET" else self._POST_PATHS if method == "POST" else frozenset()
        if path not in allowed:
            raise OkxError(f"Aggressive Live endpoint is not allow-listed: {method} {path}")
        outgoing = self._live_size(payload) if method == "POST" and path in {
            "/api/v5/trade/order", "/api/v5/trade/order-algo",
        } else payload
        request_path = path
        body = ""
        if method == "GET" and outgoing:
            request_path += "?" + urlencode(outgoing)
        elif method == "POST":
            body = json.dumps(outgoing, separators=(",", ":"))
        def build_request() -> Request:
            headers = {"Content-Type": "application/json", "User-Agent": "CodexQuantBot/live-aggressive"}
            if private:
                timestamp = self._timestamp()
                signature = base64.b64encode(hmac.new(
                    self.credentials.secret_key.encode(),
                    f"{timestamp}{method}{request_path}{body}".encode(), hashlib.sha256,
                ).digest()).decode()
                headers.update({
                    "OK-ACCESS-KEY": self.credentials.api_key,
                    "OK-ACCESS-SIGN": signature,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
                })
            if method == "POST" and path in {"/api/v5/trade/order", "/api/v5/trade/order-algo"}:
                headers["expTime"] = str(self._post_exp_time_ms())
            # Never add x-simulated-trading: this adapter is Live-only.
            return Request(self.base_url + request_path, data=body.encode() if body else None,
                           headers=headers, method=method)

        attempts = 3 if method == "GET" else 1
        result = None
        network_attempt = 0
        timestamp_retry_used = False
        exp_time_retry_used = False
        while True:
            request = build_request()  # retries must receive a fresh timestamp and signature
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode())
            except HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                if private and not timestamp_retry_used and self._is_timestamp_error(detail):
                    try:
                        self._sync_server_time()
                    except OkxError as clock_exc:
                        # OKX explicitly rejected the signed request, so no
                        # order exists.  Clock recovery is a read-only GET and
                        # the durable clOrdId may be retried after connectivity
                        # returns without creating a duplicate order.
                        raise OkxError(
                            "OKX Live pre-order server-time synchronization GET failed "
                            f"after explicit rejection: {clock_exc}") from clock_exc
                    timestamp_retry_used = True
                    continue
                if method == "GET" and exc.code in {429, 500, 502, 503, 504}:
                    network_attempt += 1
                    if network_attempt < attempts:
                        time.sleep(.5 * network_attempt)
                        continue
                raise OkxError(f"OKX Live HTTP {exc.code}: {detail}") from exc
            except (OSError, ValueError) as exc:
                if method == "POST":
                    # TLS handshake/DNS/connect failures happen before an HTTP
                    # request can be transmitted. They are safe to retry with
                    # the same clOrdId and a fresh expTime/signature. A read
                    # timeout after transmission remains outcome-unknown.
                    if self._is_pre_send_transport_error(exc):
                        network_attempt += 1
                        if network_attempt < 3:
                            time.sleep(.35 * network_attempt)
                            continue
                    reconciled = self._reconcile_order_submission(path, outgoing, exc)
                    if reconciled is not None:
                        return reconciled
                    raise OkxError(
                        "OKX Live POST outcome unknown; automatic engine stopped and must be reconciled in OKX: "
                        f"{exc}"
                    ) from exc
                network_attempt += 1
                if network_attempt < attempts:
                    time.sleep(.25 * network_attempt)
                    continue
                raise OkxError(f"OKX Live GET failed after {attempts} attempts ({path}): {exc}") from exc
            if private and not timestamp_retry_used and self._is_timestamp_error(result):
                try:
                    self._sync_server_time()
                except OkxError as clock_exc:
                    raise OkxError(
                        "OKX Live pre-order server-time synchronization GET failed "
                        f"after explicit rejection: {clock_exc}") from clock_exc
                timestamp_retry_used = True
                continue
            if (method == "POST" and path in {"/api/v5/trade/order", "/api/v5/trade/order-algo"}
                    and not exp_time_retry_used and self._explicit_exp_time_rejection(result)):
                # OKX explicitly created no order. The identical clOrdId and
                # payload may be retried once after a read-only clock sync.
                try:
                    self._sync_server_time()
                except OkxError as clock_exc:
                    raise OkxError(
                        "OKX Live pre-order server-time synchronization GET failed "
                        f"after explicit rejection: {clock_exc}") from clock_exc
                exp_time_retry_used = True
                continue
            break
        if result is None:
            raise OkxError("OKX Live GET failed after retries")
        if str(result.get("code", "")) != "0":
            details = "; ".join(
                f"sCode={item.get('sCode', '')} sMsg={item.get('sMsg', '')}"
                for item in (result.get("data") or []) if isinstance(item, dict)
            )
            suffix = f" ({details})" if details else ""
            raise OkxError(
                f"OKX Live {method} error {result.get('code')}: {result.get('msg')}{suffix}"
            )
        failed = [item for item in result.get("data", []) if isinstance(item, dict)
                  and str(item.get("sCode", "0")) not in {"", "0"}]
        if failed:
            raise OkxError("OKX Live request rejected: " + "; ".join(
                f"{x.get('sCode', '')} {x.get('sMsg', '')}" for x in failed))
        return result

    @staticmethod
    def _is_pre_send_transport_error(exc: BaseException) -> bool:
        parts = []
        current: BaseException | None = exc
        for _ in range(3):
            if current is None:
                break
            parts.append(str(current).lower())
            current = getattr(current, "reason", None)
        text = " ".join(parts)
        return any(token in text for token in (
            "handshake operation timed out", "ssl handshake", "tls handshake",
            "name or service not known", "temporary failure in name resolution",
            "getaddrinfo failed", "connection refused", "no route to host",
            "failed to establish a new connection",
        ))

    def _reconcile_order_submission(self, path: str, payload, exc: BaseException) -> dict | None:
        """Recover an accepted ordinary order whose POST response was lost."""
        if path != "/api/v5/trade/order" or not isinstance(payload, dict):
            return None
        client_id = str(payload.get("clOrdId") or "")
        instrument = str(payload.get("instId") or "")
        if not client_id or not instrument:
            return None
        try:
            result = self._request(
                "GET", "/api/v5/trade/order",
                {"instId": instrument, "clOrdId": client_id}, private=True)
        except OkxError:
            return None
        rows = result.get("data") or []
        if not rows:
            return None
        order = dict(rows[0])
        order.setdefault("clOrdId", client_id)
        order.setdefault("sCode", "0")
        order.setdefault("sMsg", "reconciled by clOrdId after lost POST response")
        return {"code": "0", "msg": "reconciled", "data": [order]}

    def safety_snapshot(self) -> dict[str, list[dict]]:
        snapshot = super().safety_snapshot()
        # The tested Demo engine reasons in one-unit layers. Convert only its
        # in-memory view; the exchange and every outbound request remain 0.05.
        normalized = {name: [dict(item) for item in items] for name, items in snapshot.items()}
        for item in normalized["positions"]:
            raw = float(item.get("pos") or 0)
            unit_size = self._contracts_per_unit()
            units = abs(Decimal(str(raw))) / unit_size if raw else Decimal("0")
            # Aggregate exposure can legitimately exceed ten layers when
            # several owned entry branches fill over time. The execution
            # boundary still enforces exactly 0.05 per request; only reject a
            # position that cannot be represented as whole 0.05 layers.
            if raw and abs(units - round(units)) > Decimal("0.000000001"):
                raise OkxError(
                    f"Aggressive Live aggregate position is not aligned to whole {self.opening_unit_contracts}-contract layers"
                )
            normalized_units = int(round(units))
            item["pos"] = str(normalized_units if raw > 0 else -normalized_units)
        for item in normalized["algo_orders"]:
            raw = float(item.get("sz") or 0)
            if raw:
                units = Decimal(str(raw)) / self._contracts_per_unit()
                if abs(units - round(units)) > 1e-9:
                    raise OkxError(
                        f"Aggressive Live protection size is not aligned to {self.opening_unit_contracts} contracts")
                item["sz"] = str(int(round(units)))
        return normalized

    def daily_net_pnl_usdt(self, *, now: datetime | None = None) -> float:
        cacheable = now is None
        monotonic_now = time.monotonic()
        moment = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
        day = moment.date()
        if cacheable and self._daily_pnl_cache is not None:
            cached_at, cached_day, cached_value = self._daily_pnl_cache
            if cached_day == day and monotonic_now - cached_at < 10.0:
                return cached_value
        start = datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai"))
        begin_ms = int(start.timestamp() * 1000)
        end_ms = int(moment.timestamp() * 1000)
        total = 0.0
        after = ""
        seen: set[str] = set()
        for _ in range(10):
            params = {"instType": "SWAP", "instId": "ETH-USDT-SWAP", "begin": str(begin_ms),
                      "end": str(end_ms), "limit": "100"}
            if after:
                params["after"] = after
            rows = self._request("GET", "/api/v5/trade/fills-history", params, private=True).get("data", [])
            if not rows:
                break
            for fill in rows:
                identity = str(fill.get("billId") or fill.get("tradeId") or "")
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                total += float(fill.get("fillPnl") or 0) + float(fill.get("fee") or 0)
            if len(rows) < 100:
                break
            next_after = str(rows[-1].get("billId") or "")
            if not next_after or next_after == after:
                break
            after = next_after
        if cacheable:
            self._daily_pnl_cache = (monotonic_now, day, total)
        return total
