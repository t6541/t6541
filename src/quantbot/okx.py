from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request
from .http_transport import urlopen
from .order_ids import related_client_order_id

# User-selected OKX access domain for this deployment.  The public V5 time and
# ETH-USDT-SWAP instrument endpoints are verified before release.  Keep the
# value explicit: signed requests must never follow an automatic redirect to a
# different host.
BASE_URL = "https://www.tpouxyihas.com"


def locked_base_url(value: str = BASE_URL) -> str:
    """Reject every transport host except the deployment's fixed access domain."""
    normalized = str(value).rstrip("/")
    if normalized != BASE_URL:
        raise ValueError(f"OKX transport host is locked to {BASE_URL}")
    return BASE_URL


class OkxError(RuntimeError):
    pass


@dataclass(frozen=True)
class OkxCredentials:
    api_key: str
    secret_key: str
    passphrase: str

    @classmethod
    def from_environment(cls) -> "OkxCredentials":
        names = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
        values = [os.environ.get(name, "") for name in names]
        if not all(values):
            missing = ", ".join(name for name, value in zip(names, values) if not value)
            raise OkxError(f"Missing demo credentials: {missing}")
        return cls(*values)


class OkxDemoClient:
    """Minimal OKX v5 client that always sends the demo-trading header."""

    _clock_lock = threading.Lock()
    _clock_offsets_ms: dict[str, float] = {}

    def __init__(self, credentials: OkxCredentials | None = None, timeout: int = 8,
                 base_url: str = BASE_URL):
        self.credentials = credentials
        self.timeout = timeout
        self.base_url = locked_base_url(base_url)

    def _timestamp(self) -> str:
        offset_ms = self._clock_offsets_ms.get(self.base_url, 0.0)
        moment = datetime.fromtimestamp((time.time() * 1000 + offset_ms) / 1000, timezone.utc)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _is_timestamp_error(payload: str | dict) -> bool:
        if isinstance(payload, dict):
            return str(payload.get("code", "")) == "50102"
        return "50102" in payload or "Timestamp request expired" in payload

    @staticmethod
    def _is_transient_http_error(status: int, payload: str) -> bool:
        """Recognize explicit gateway/service failures that are safe to retry."""
        return status in {429, 500, 502, 503, 504} or '"code":"50001"' in payload.replace(" ", "")

    def _sync_server_time(self) -> float:
        """Cache OKX-server minus local-clock milliseconds for signed requests."""
        with self._clock_lock:
            started_ms = time.time() * 1000
            request = Request(
                self.base_url + "/api/v5/public/time",
                headers={"User-Agent": "CodexQuantBot/0.1"}, method="GET",
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode())
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                raise OkxError(f"OKX server-time synchronization failed: {exc}") from exc
            finished_ms = time.time() * 1000
            if str(payload.get("code", "0")) != "0" or not payload.get("data"):
                raise OkxError(f"OKX server-time synchronization failed: {payload}")
            try:
                server_ms = float(payload["data"][0]["ts"])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise OkxError(f"OKX server-time response is invalid: {payload}") from exc
            offset_ms = server_ms - ((started_ms + finished_ms) / 2)
            self._clock_offsets_ms[self.base_url] = offset_ms
            return offset_ms

    def _request(self, method: str, path: str, payload: dict | None = None, private: bool = False,
                 retry_safe_post: bool = False) -> dict:
        body = "" if payload is None or method == "GET" else json.dumps(payload, separators=(",", ":"))
        request_path = path
        if method == "GET" and payload:
            request_path += "?" + urlencode(payload)
        credentials = (self.credentials or OkxCredentials.from_environment()) if private else None

        def build_request() -> Request:
            headers = {"Content-Type": "application/json", "User-Agent": "CodexQuantBot/0.1", "x-simulated-trading": "1"}
            if private and credentials is not None:
                timestamp = self._timestamp()
                message = f"{timestamp}{method}{request_path}{body}"
                signature = base64.b64encode(hmac.new(credentials.secret_key.encode(), message.encode(), hashlib.sha256).digest()).decode()
                headers.update({"OK-ACCESS-KEY": credentials.api_key, "OK-ACCESS-SIGN": signature, "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": credentials.passphrase})
            return Request(self.base_url + request_path, data=body.encode() if body else None, headers=headers, method=method)

        # GET is idempotent and may be retried after transient TLS/read errors.
        # POST is single-shot after unknown network outcomes.  A POST may only
        # be repeated when OKX explicitly rejected it with 50102, which proves
        # the expired request was not accepted as an order.
        attempts = 4 if method == "GET" or retry_safe_post else 1
        result = None
        network_failures = 0
        timestamp_retry_used = False
        while True:
            request = build_request()  # every retry receives a fresh signature timestamp
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode())
            except HTTPError as exc:
                error_text = exc.read().decode(errors="replace")
                if private and not timestamp_retry_used and self._is_timestamp_error(error_text):
                    self._sync_server_time()
                    timestamp_retry_used = True
                    continue
                network_failures += 1
                if self._is_transient_http_error(exc.code, error_text) and network_failures < attempts:
                    time.sleep((0.5, 1.5, 3.0)[network_failures - 1])
                    continue
                raise OkxError(f"OKX HTTP {exc.code}: {error_text}") from exc
            except OSError as exc:
                network_failures += 1
                if network_failures >= attempts:
                    detail = f"after {attempts} GET attempts" if method == "GET" else "after single-shot POST"
                    raise OkxError(f"OKX network error {detail}: {exc}") from exc
                time.sleep((0.5, 1.5, 3.0)[network_failures - 1])
                continue
            if private and not timestamp_retry_used and self._is_timestamp_error(result):
                self._sync_server_time()
                timestamp_retry_used = True
                result = None
                continue
            break
        assert result is not None
        if str(result.get("code", "0")) != "0":
            details = result.get("data") or []
            detail_text = "; ".join(
                f"sCode={item.get('sCode', '')} sMsg={item.get('sMsg', '')}" for item in details if isinstance(item, dict)
            )
            suffix = f" ({detail_text})" if detail_text else ""
            raise OkxError(f"OKX error {result.get('code')}: {result.get('msg')}{suffix}")
        if method == "POST":
            failed = [
                item for item in (result.get("data") or [])
                if isinstance(item, dict) and str(item.get("sCode", "0")) not in {"", "0"}
            ]
            if failed:
                details = "; ".join(
                    f"sCode={item.get('sCode', '')} sMsg={item.get('sMsg', '')}" for item in failed
                )
                raise OkxError(f"OKX order rejected: {details}")
        return result

    def public_instrument(self, inst_id: str = "ETH-USDT-SWAP") -> dict:
        return self._request("GET", "/api/v5/public/instruments", {"instType": "SWAP", "instId": inst_id})

    def history_candles(self, inst_id: str, *, bar: str = "1D", total: int = 500) -> list[list[str]]:
        """Fetch confirmed public candles, oldest first, without API credentials."""
        if not 1 <= total <= 5_000:
            raise ValueError("total candles must be between 1 and 5000")
        candles: dict[str, list[str]] = {}
        after: str | None = None
        while len(candles) < total:
            payload = {"instId": inst_id, "bar": bar, "limit": str(min(100, total - len(candles)))}
            if after is not None:
                payload["after"] = after
            batch = self._request("GET", "/api/v5/market/history-candles", payload).get("data", [])
            if not batch:
                break
            for candle in batch:
                if len(candle) >= 9 and candle[8] == "1":
                    candles[candle[0]] = candle
            oldest = min(str(item[0]) for item in batch)
            if oldest == after:
                break
            after = oldest
            if len(batch) < int(payload["limit"]):
                break
            time.sleep(0.11)
        return [candles[key] for key in sorted(candles, key=int)][-total:]

    def current_candles(self, inst_id: str, *, bar: str = "1m", limit: int = 2) -> list[list[str]]:
        """Fetch live candles, including the current confirm=0 candle."""
        if not 1 <= limit <= 100:
            raise ValueError("current candle limit must be between 1 and 100")
        data = self._request("GET", "/api/v5/market/candles", {
            "instId": inst_id, "bar": bar, "limit": str(limit),
        }).get("data", [])
        return sorted((row for row in data if len(row) >= 9), key=lambda row: int(row[0]))

    def check_demo_account(self) -> dict:
        return self._request("GET", "/api/v5/account/config", private=True)

    def require_swap_trading_mode(self) -> dict:
        """Require the account settings used by the isolated long/short strategy runner."""
        result = self.check_demo_account()
        account = (result.get("data") or [{}])[0]
        account_level = str(account.get("acctLv", ""))
        position_mode = str(account.get("posMode", ""))
        if account_level != "2" or position_mode != "long_short_mode":
            raise OkxError(
                "Demo account mode is not ready for swap trading: "
                f"acctLv={account_level or 'unknown'}, posMode={position_mode or 'unknown'}; "
                "select single-currency margin mode and long/short position mode in OKX Demo Trading"
            )
        return account

    def safety_snapshot(self) -> dict[str, list[dict]]:
        """Return all swap exposure that must be clear before local credential changes."""
        # These eight GETs describe one read-only account view and do not
        # depend on each other. Serial polling used to consume 11-34 seconds,
        # already longer than the intended fast execution budget.
        order_types = ("conditional", "oco", "trigger", "move_order_stop", "iceberg", "twap")
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="okx-snapshot") as pool:
            positions_future = pool.submit(
                self._request, "GET", "/api/v5/account/positions",
                {"instType": "SWAP"}, True)
            orders_future = pool.submit(
                self._request, "GET", "/api/v5/trade/orders-pending",
                {"instType": "SWAP"}, True)
            algo_futures = [pool.submit(
                self._request, "GET", "/api/v5/trade/orders-algo-pending",
                {"ordType": order_type, "instType": "SWAP"}, True)
                for order_type in order_types]
            positions = positions_future.result().get("data", [])
            orders = orders_future.result().get("data", [])
            algo_orders = [item for future in algo_futures
                           for item in future.result().get("data", [])]
        nonzero_positions = [item for item in positions if abs(float(item.get("pos") or 0)) > 0]
        return {"positions": nonzero_positions, "orders": orders, "algo_orders": algo_orders}

    def fast_entry_snapshot(self, inst_id: str = "ETH-USDT-SWAP") -> dict[str, list[dict]]:
        """Read only exposure that can conflict with a new market entry.

        Protection reconciliation remains in the full audit loop.  The fast
        entry loop needs positions and ordinary pending orders only, and reads
        them concurrently so unrelated algo-order categories cannot delay a
        time-sensitive entry.
        """
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="okx-fast-entry") as pool:
            positions_future = pool.submit(
                self._request, "GET", "/api/v5/account/positions",
                {"instId": inst_id}, True)
            orders_future = pool.submit(
                self._request, "GET", "/api/v5/trade/orders-pending",
                {"instType": "SWAP", "instId": inst_id}, True)
            positions = positions_future.result().get("data", [])
            orders = orders_future.result().get("data", [])
        return {
            "positions": [item for item in positions
                          if abs(float(item.get("pos") or 0)) > 0],
            "orders": orders,
            "algo_orders": [],
        }

    def assert_account_clear(self) -> None:
        snapshot = self.safety_snapshot()
        if any(snapshot.values()):
            counts = ", ".join(f"{name}={len(items)}" for name, items in snapshot.items())
            raise OkxError(f"Demo account is not clear ({counts}); close positions and orders first")

    def set_leverage(self, leverage: int = 100, inst_id: str = "ETH-USDT-SWAP", position_side: str | None = None) -> dict:
        if not 1 <= leverage <= 100:
            raise ValueError("leverage must be between 1 and 100")
        payload = {"instId": inst_id, "lever": str(leverage), "mgnMode": "cross"}
        if position_side is not None:
            if position_side not in {"long", "short"}:
                raise ValueError("position_side must be long or short")
            payload["posSide"] = position_side
        # Setting leverage is idempotent account configuration, not an order.
        # An explicit 503/50001 rejection is therefore safe to retry.
        return self._request("POST", "/api/v5/account/set-leverage", payload, private=True,
                             retry_safe_post=True)

    def place_demo_market_order(
        self,
        side: str,
        contracts: int,
        stop_loss_price: str,
        *,
        enabled: bool,
        max_contracts: int,
        confirmation: str,
        inst_id: str = "ETH-USDT-SWAP",
        client_order_id: str | None = None,
        position_side: str | None = None,
        take_profit_price: str | None = None,
        take_profit_trigger_type: str = "last",
        stop_loss_trigger_type: str = "mark",
        trailing_callback_ratio: str | None = None,
        trailing_activation_price: str | None = None,
    ) -> dict:
        if not enabled or confirmation != "DEMO-ORDER":
            raise OkxError("Demo order is locked; enable it in config and provide DEMO-ORDER confirmation")
        if side not in {"buy", "sell"} or not 1 <= contracts <= max_contracts:
            raise ValueError("Order side or contract size violates limits")
        if not stop_loss_price or float(stop_loss_price) <= 0:
            raise ValueError("A positive stop-loss trigger is mandatory")
        if not inst_id.endswith("-USDT-SWAP") or inst_id != inst_id.upper():
            raise ValueError("Only uppercase USDT perpetual contracts are allowed")
        valid_trigger_types = {"last", "mark", "index"}
        if (stop_loss_trigger_type not in valid_trigger_types
                or (take_profit_price is not None and take_profit_trigger_type not in valid_trigger_types)):
            raise ValueError("trigger price type must be last, mark, or index")
        stop_loss = {
            "slTriggerPx": str(stop_loss_price),
            "slOrdPx": "-1",
            "slTriggerPxType": stop_loss_trigger_type,
        }
        if client_order_id:
            # Give the attached stop a durable layer identity.  OKX positions
            # are aggregated by side, so a partial manual exit cannot otherwise
            # tell which layer's protection must be removed.
            stop_loss["attachAlgoClOrdId"] = related_client_order_id(client_order_id, "P")
        attached_orders = [stop_loss]
        if trailing_callback_ratio is not None:
            if take_profit_price is not None:
                raise ValueError("fixed take-profit and trailing take-profit cannot be used together")
            callback = float(trailing_callback_ratio)
            if not 0 < callback <= 0.05:
                raise ValueError("trailing callback ratio must be between 0 and 0.05")
            stop_loss["callbackRatio"] = str(trailing_callback_ratio)
            if trailing_activation_price is not None:
                if float(trailing_activation_price) <= 0:
                    raise ValueError("A positive trailing activation price is mandatory")
                stop_loss["activePx"] = str(trailing_activation_price)
        if take_profit_price is not None:
            if float(take_profit_price) <= 0:
                raise ValueError("A positive take-profit trigger is mandatory")
            stop_loss.update({
                "tpTriggerPx": str(take_profit_price),
                "tpOrdPx": "-1",
                "tpTriggerPxType": take_profit_trigger_type,
            })
        payload = {"instId": inst_id, "tdMode": "cross", "side": side, "ordType": "market", "sz": str(contracts), "attachAlgoOrds": attached_orders}
        if position_side is not None:
            if position_side not in {"long", "short"}:
                raise ValueError("position_side must be long or short")
            if (side, position_side) not in {("buy", "long"), ("sell", "short")}:
                raise ValueError("Opening side and position_side do not match")
            payload["posSide"] = position_side
        if client_order_id:
            if len(client_order_id) > 32 or not client_order_id.isalnum():
                raise ValueError("client_order_id must be 1-32 alphanumeric characters")
            payload["clOrdId"] = client_order_id
        return self._request("POST", "/api/v5/trade/order", payload, private=True)

    def place_demo_trailing_order(
        self,
        side: str,
        contracts: int,
        callback_ratio: str | None,
        activation_price: str,
        *,
        enabled: bool,
        max_contracts: int,
        confirmation: str,
        inst_id: str = "ETH-USDT-SWAP",
        position_side: str,
        client_algo_order_id: str | None = None,
        callback_spread: str | None = None,
    ) -> dict:
        """Place a standalone server-side trailing close order for an open Demo position."""
        if not enabled or confirmation != "DEMO-ORDER":
            raise OkxError("Demo order is locked; enable it in config and provide DEMO-ORDER confirmation")
        if side not in {"buy", "sell"} or not 1 <= contracts <= max_contracts:
            raise ValueError("Order side or contract size violates limits")
        if (side, position_side) not in {("sell", "long"), ("buy", "short")}:
            raise ValueError("Trailing close side and position_side do not match")
        if (callback_ratio is None) == (callback_spread is None):
            raise ValueError("provide exactly one trailing callback ratio or spread")
        if callback_ratio is not None and not 0.001 <= float(callback_ratio) <= 1:
            raise ValueError("OKX trailing callback ratio must be between 0.001 and 1")
        if callback_spread is not None and float(callback_spread) <= 0:
            raise ValueError("Trailing callback spread must be positive")
        if float(activation_price) <= 0:
            raise ValueError("Trailing callback ratio or activation price is invalid")
        payload = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": side,
            "posSide": position_side,
            "ordType": "move_order_stop",
            "sz": str(contracts),
            "activePx": str(activation_price),
            "reduceOnly": "true",
        }
        if callback_ratio is not None:
            payload["callbackRatio"] = str(callback_ratio)
        else:
            payload["callbackSpread"] = str(callback_spread)
        if client_algo_order_id:
            if len(client_algo_order_id) > 32 or not client_algo_order_id.isalnum():
                raise ValueError("client_algo_order_id must be 1-32 alphanumeric characters")
            payload["algoClOrdId"] = client_algo_order_id
        return self._request("POST", "/api/v5/trade/order-algo", payload, private=True)

    def place_demo_protective_stop(
        self, contracts: int, stop_loss_price: str, *, position_side: str,
        enabled: bool, max_contracts: int, confirmation: str,
        inst_id: str = "ETH-USDT-SWAP", client_algo_order_id: str | None = None,
    ) -> dict:
        """Attach a standalone reduce-only server stop to an existing position."""
        if not enabled or confirmation != "DEMO-ORDER":
            raise OkxError("Demo protective recovery is locked")
        if position_side not in {"long", "short"}:
            raise ValueError("position_side must be long or short")
        if not 1 <= contracts <= max_contracts or float(stop_loss_price) <= 0:
            raise ValueError("Protective stop size or trigger is invalid")
        payload = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": "sell" if position_side == "long" else "buy",
            "posSide": position_side,
            "ordType": "conditional",
            "sz": str(contracts),
            "slTriggerPx": str(stop_loss_price),
            "slOrdPx": "-1",
            "slTriggerPxType": "mark",
            "reduceOnly": "true",
        }
        if client_algo_order_id:
            if len(client_algo_order_id) > 32 or not client_algo_order_id.isalnum():
                raise ValueError("client_algo_order_id must be 1-32 alphanumeric characters")
            payload["algoClOrdId"] = client_algo_order_id
        return self._request("POST", "/api/v5/trade/order-algo", payload, private=True)

    def place_demo_sniper_limit_order(
        self, side: str, contracts: int, price: str, stop_loss_price: str,
        take_profit_price: str | None, *, enabled: bool, max_contracts: int,
        confirmation: str, inst_id: str, position_side: str,
        client_order_id: str,
    ) -> dict:
        """Place one Demo-only resting structure order with mandatory SL."""
        if not enabled or confirmation != "DEMO-ORDER":
            raise OkxError("Demo sniper order is locked")
        if side not in {"buy", "sell"} or not 1 <= contracts <= max_contracts:
            raise ValueError("Sniper order side or size violates limits")
        if (side, position_side) not in {("buy", "long"), ("sell", "short")}:
            raise ValueError("Sniper side and position side do not match")
        entry, stop = map(float, (price, stop_loss_price))
        target = float(take_profit_price) if take_profit_price is not None else None
        if side == "buy" and not stop < entry:
            raise ValueError("Long sniper protection prices are invalid")
        if side == "sell" and not entry < stop:
            raise ValueError("Short sniper protection prices are invalid")
        if len(client_order_id) > 32 or not client_order_id.isalnum():
            raise ValueError("client_order_id must be 1-32 alphanumeric characters")
        payload = {
            "instId": inst_id, "tdMode": "cross", "side": side,
            "posSide": position_side, "ordType": "post_only", "sz": str(contracts),
            "px": str(price), "clOrdId": client_order_id,
            "attachAlgoOrds": [{
                "slTriggerPx": str(stop_loss_price),
                "slOrdPx": "-1", "slTriggerPxType": "mark",
                "attachAlgoClOrdId": related_client_order_id(client_order_id, "P"),
            }],
        }
        if target is not None:
            if side == "buy" and not entry < target:
                raise ValueError("Long sniper take-profit must be above entry")
            if side == "sell" and not target < entry:
                raise ValueError("Short sniper take-profit must be below entry")
            payload["attachAlgoOrds"][0].update({
                "tpTriggerPx": str(take_profit_price), "tpOrdPx": "-1",
                "tpTriggerPxType": "last",
            })
        return self._request("POST", "/api/v5/trade/order", payload, private=True)

    def cancel_orders(self, orders: list[dict]) -> dict:
        payload = [
            {"instId": str(item["instId"]), "ordId": str(item["ordId"])}
            for item in orders if item.get("instId") and item.get("ordId")
        ]
        if not payload:
            return {"code": "0", "data": []}
        return self._request("POST", "/api/v5/trade/cancel-batch-orders", payload, private=True)

    def cancel_algo_orders(self, orders: list[dict]) -> dict:
        """Cancel explicitly identified algo orders. Callers must scope ownership first."""
        payload = [
            {"instId": str(order["instId"]), "algoId": str(order["algoId"])}
            for order in orders if order.get("instId") and order.get("algoId")
        ]
        if not payload:
            return {"code": "0", "data": []}
        return self._request("POST", "/api/v5/trade/cancel-algos", payload, private=True)

    def migrate_active_stop_losses_to_mark(self, snapshot: dict[str, list[dict]]) -> int:
        """Amend legacy active SL algos in place, without cancelling protection.

        OKX only permits this amendment for Stop/Trigger algos.  Trailing stops
        are deliberately excluded because their trigger model is different.
        """
        migrated = 0
        for order in snapshot.get("algo_orders", []):
            order_type = str(order.get("ordType", "")).lower()
            trigger_type = str(order.get("slTriggerPxType", "last")).lower()
            algo_id = str(order.get("algoId", "")).strip()
            instrument = str(order.get("instId", "")).strip()
            trigger_price = str(order.get("slTriggerPx", "")).strip()
            if (order_type not in {"conditional", "trigger"} or trigger_type == "mark"
                    or not algo_id or not instrument or not trigger_price):
                continue
            payload = {
                "instId": instrument,
                "algoId": algo_id,
                "newSlTriggerPx": trigger_price,
                "newSlOrdPx": str(order.get("slOrdPx") or "-1"),
                "newSlTriggerPxType": "mark",
                # If OKX rejects the amendment, retain the original protective
                # stop rather than leaving the position unprotected.
                "cxlOnFail": False,
            }
            self._request("POST", "/api/v5/trade/amend-algos", payload, private=True)
            order["slTriggerPxType"] = "mark"
            migrated += 1
        return migrated

    def tighten_active_stop_loss(self, order: dict, stop_price: str) -> dict:
        """Tighten one identified Demo SL in place; never cancel on failure."""
        algo_id = str(order.get("algoId", "")).strip()
        instrument = str(order.get("instId", "")).strip()
        if not algo_id or not instrument or float(stop_price) <= 0:
            raise ValueError("Active stop identity and price are required")
        payload = {
            "instId": instrument, "algoId": algo_id,
            "newSlTriggerPx": str(stop_price), "newSlOrdPx": "-1",
            "newSlTriggerPxType": "mark", "cxlOnFail": False,
        }
        return self._request("POST", "/api/v5/trade/amend-algos", payload, private=True)

    def close_demo_position_market(
        self, contracts: int, *, position_side: str, enabled: bool,
        max_contracts: int, confirmation: str, inst_id: str,
    ) -> dict:
        """Actively close one Demo position without ever opening the opposite side."""
        if not enabled or confirmation != "DEMO-ORDER":
            raise OkxError("Demo active exit is locked")
        if position_side not in {"long", "short"}:
            raise ValueError("position_side must be long or short")
        if not 1 <= contracts <= max_contracts:
            raise ValueError("Active-exit size violates limits")
        payload = {
            "instId": inst_id, "tdMode": "cross",
            "side": "sell" if position_side == "long" else "buy",
            "posSide": position_side, "ordType": "market",
            "sz": str(contracts), "reduceOnly": "true",
        }
        return self._request("POST", "/api/v5/trade/order", payload, private=True)

    @staticmethod
    def position_internal_units(position: dict) -> int:
        """Convert exchange position size into strategy test units."""
        units = int(round(abs(float(position.get("pos") or 0))))
        if not 1 <= units <= 3:
            raise ValueError("Position size is outside the one-to-three-layer strategy limit")
        return units

    def recent_fills(self, inst_id: str, limit: int = 100) -> list[dict]:
        return self._request(
            "GET", "/api/v5/trade/fills-history",
            {"instType": "SWAP", "instId": inst_id, "limit": str(limit)}, private=True,
        ).get("data", [])

    def reconciliation_fills(self, inst_id: str, max_pages: int = 10) -> list[dict]:
        """Bounded historical read. A missing old fill remains unresolved."""
        collected, seen = [], set()
        after = ""
        for _ in range(max_pages):
            params = {"instType": "SWAP", "instId": inst_id, "limit": "100"}
            if after:
                params["after"] = after
            page = self._request("GET", "/api/v5/trade/fills-history",
                                 params, private=True).get("data", [])
            if not page:
                break
            for fill in page:
                identity = str(fill.get("tradeId") or "")
                if identity and identity not in seen:
                    collected.append(fill)
                    seen.add(identity)
            cursor = str(page[-1].get("billId") or "")
            if len(page) < 100 or not cursor or cursor == after:
                break
            after = cursor
        return collected

    @staticmethod
    def orphaned_owned_trailing_orders(snapshot: dict[str, list[dict]], prefix: str) -> list[dict]:
        active_sides = {
            (str(item.get("instId", "")), str(item.get("posSide", "")))
            for item in snapshot.get("positions", []) if abs(float(item.get("pos") or 0)) > 0
        }
        return [
            item for item in snapshot.get("algo_orders", [])
            if str(item.get("ordType", "")) == "move_order_stop"
            and str(item.get("algoClOrdId", "")).startswith(prefix)
            and (str(item.get("instId", "")), str(item.get("posSide", ""))) not in active_sides
        ]

    @staticmethod
    def unprotected_positions(snapshot: dict[str, list[dict]]) -> list[dict]:
        """Return positions whose active stop quantity does not cover exposure."""
        unprotected: list[dict] = []
        for position in snapshot.get("positions", []):
            position_algos = position.get("closeOrderAlgo") or []
            side_key = (str(position.get("instId", "")), str(position.get("posSide", "")))
            position_size = abs(float(position.get("pos") or 0))
            full_position_stop = any(
                float(item.get("slTriggerPx") or 0) > 0
                and str(item.get("closeFraction") or "") == "1"
                for item in position_algos
            )
            if full_position_stop:
                continue
            seen: set[str] = set()
            covered = 0.0
            for order in snapshot.get("algo_orders", []):
                if (str(order.get("instId", "")), str(order.get("posSide", ""))) != side_key:
                    continue
                if float(order.get("slTriggerPx") or 0) <= 0:
                    continue
                identity = str(order.get("algoId") or order.get("algoClOrdId") or "")
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                covered += abs(float(order.get("sz") or 0))
            if covered + 1e-9 < position_size:
                item = dict(position)
                item["protection_covered"] = covered
                item["protection_deficit"] = position_size - covered
                unprotected.append(item)
        return unprotected
