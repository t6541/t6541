"""Live transport dedicated to account 05.

Unlike the aggressive adapter, sizes are already real OKX contract sizes and
no stop-loss parameter is injected.  Every opening order has a stable clOrdId;
every exit is a reduce-only limit order tied to one virtual lot.
"""

from __future__ import annotations

from decimal import Decimal

from .live_aggressive_adapter import OkxLiveAggressiveAdapter
from .okx import OkxError


class Account05LiveClient(OkxLiveAggressiveAdapter):
    _POST_PATHS = OkxLiveAggressiveAdapter._POST_PATHS | frozenset({
        "/api/v5/account/set-leverage",
        "/api/v5/trade/amend-order",
    })

    def __init__(self, credentials, timeout: int = 8, base_url=None):
        kwargs = {"timeout": timeout}
        if base_url is not None:
            kwargs["base_url"] = base_url
        super().__init__(credentials, contracts_per_unit="0.01", **kwargs)

    def _live_size(self, payload):
        """Account-05 payloads contain final exchange contract quantities."""
        return payload

    @staticmethod
    def _size(value: Decimal) -> str:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount % Decimal("0.01"):
            raise ValueError("账户05订单张数必须为正数并按0.01张递增")
        return format(amount, "f")

    def set_hedge_leverage_100(self, inst_id: str = "ETH-USDT-SWAP") -> None:
        for side in ("long", "short"):
            self._request("POST", "/api/v5/account/set-leverage", {
                "instId": inst_id, "lever": "100", "mgnMode": "cross", "posSide": side,
            }, private=True, retry_safe_post=True)

    def place_market_entry(self, *, inst_id: str, side: str, position_side: str,
                           size: Decimal, client_order_id: str) -> dict:
        if side not in {"buy", "sell"} or position_side not in {"long", "short"}:
            raise ValueError("invalid account05 entry side")
        expected = "buy" if position_side == "long" else "sell"
        if side != expected:
            raise ValueError("entry side and position side disagree")
        response = self._request("POST", "/api/v5/trade/order", {
            "instId": inst_id, "tdMode": "cross", "side": side,
            "posSide": position_side, "ordType": "market", "sz": self._size(size),
            "clOrdId": client_order_id,
        }, private=True)
        order = (response.get("data") or [{}])[0]
        if not order.get("ordId"):
            raise OkxError("账户05开仓响应缺少ordId；必须按clOrdId对账，禁止重发")
        return order

    def place_market_reduce(self, *, inst_id: str, position_side: str,
                            size: Decimal, client_order_id: str) -> dict:
        if position_side not in {"long", "short"}:
            raise ValueError("invalid account05 position side")
        side = "sell" if position_side == "long" else "buy"
        response = self._request("POST", "/api/v5/trade/order", {
            "instId": inst_id, "tdMode": "cross", "side": side,
            "posSide": position_side, "ordType": "market", "sz": self._size(size),
            "reduceOnly": "true", "clOrdId": client_order_id,
        }, private=True)
        order = (response.get("data") or [{}])[0]
        if not order.get("ordId"):
            raise OkxError("账户05小单MA5止盈响应缺少ordId；必须按clOrdId对账，禁止重发")
        return order

    def place_lot_take_profit(self, *, inst_id: str, position_side: str,
                              size: Decimal, price: Decimal,
                              client_order_id: str) -> dict:
        if position_side not in {"long", "short"}:
            raise ValueError("invalid account05 position side")
        side = "sell" if position_side == "long" else "buy"
        response = self._request("POST", "/api/v5/trade/order", {
            "instId": inst_id, "tdMode": "cross", "side": side,
            "posSide": position_side, "ordType": "limit", "sz": self._size(size),
            "px": format(Decimal(str(price)), "f"), "reduceOnly": "true",
            "clOrdId": client_order_id,
        }, private=True)
        order = (response.get("data") or [{}])[0]
        if not order.get("ordId"):
            raise OkxError("账户05独立止盈响应缺少ordId；必须按clOrdId对账，禁止重发")
        return order

    def order(self, inst_id: str, *, order_id: str = "", client_order_id: str = "") -> dict:
        if not order_id and not client_order_id:
            raise ValueError("order_id or client_order_id is required")
        payload = {"instId": inst_id}
        payload["ordId" if order_id else "clOrdId"] = order_id or client_order_id
        rows = self._request("GET", "/api/v5/trade/order", payload, private=True).get("data", [])
        if not rows:
            raise OkxError("账户05订单对账未返回订单")
        return rows[0]

    def orders_history(self, inst_id: str = "ETH-USDT-SWAP", *, limit: int = 100) -> list[dict]:
        """Read recent exchange orders for audit/import; never writes orders."""
        payload = {"instType": "SWAP", "instId": inst_id, "limit": str(min(max(limit, 1), 100))}
        rows: list[dict] = []
        seen: set[str] = set()
        # Recent history contains newly filled orders before the archive feed
        # catches up. Keep the archive as a second source for older fills.
        for path in ("/api/v5/trade/orders-history", "/api/v5/trade/orders-history-archive"):
            try:
                data = self._request("GET", path, payload, private=True).get("data", [])
            except Exception:
                continue
            for row in data:
                key = str(row.get("ordId") or row.get("clOrdId") or "")
                if key and key not in seen:
                    seen.add(key)
                    rows.append(row)
        try:
            for fill in self._request("GET", "/api/v5/trade/fills-history",
                                      payload, private=True).get("data", []):
                key = str(fill.get("ordId") or fill.get("tradeId") or "")
                if key and key not in seen:
                    seen.add(key)
                    rows.append({
                        "ordId": fill.get("ordId"), "clOrdId": fill.get("clOrdId"),
                        "state": "filled", "posSide": fill.get("posSide"),
                        "side": fill.get("side"), "ordType": fill.get("execType") or "market",
                        "accFillSz": fill.get("fillSz"), "avgPx": fill.get("fillPx"),
                        "fillTime": fill.get("ts"), "reduceOnly": fill.get("reduceOnly", "false"),
                    })
        except Exception:
            pass
        return rows

    def order_history_page(self, inst_id: str = "ETH-USDT-SWAP", *,
                           archive: bool = False, after: str = "",
                           limit: int = 100) -> list[dict]:
        """Read a single history page without suppressing audit failures."""
        payload = {"instType": "SWAP", "instId": inst_id,
                   "limit": str(min(max(limit, 1), 100))}
        if after:
            payload["after"] = str(after)
        path = "/api/v5/trade/orders-history" + ("-archive" if archive else "")
        return self._request("GET", path, payload, private=True).get("data", [])

    def fills_history(self, inst_id: str = "ETH-USDT-SWAP", *, limit: int = 100,
                      after: str = "", before: str = "", strict: bool = False) -> list[dict]:
        payload = {"instType": "SWAP", "instId": inst_id,
                   "limit": str(min(max(limit, 1), 100))}
        if after:
            payload["after"] = str(after)
        if before:
            payload["before"] = str(before)
        rows: list[dict] = []
        try:
            rows.extend(self._request("GET", "/api/v5/trade/fills-history",
                                      payload, private=True).get("data", []))
        except Exception:
            if strict:
                raise
        return rows

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        response = self.cancel_orders([{"instId": inst_id, "ordId": order_id}])
        rows = response.get("data") or []
        if not rows or str(rows[0].get("ordId") or "") != str(order_id):
            raise OkxError("账户05撤单响应缺少目标ordId；停止并对账")
        return rows[0]

    def amend_order_price(self, inst_id: str, order_id: str, price: Decimal,
                          request_id: str) -> dict:
        """Amend a live TP in place; a failed amend must retain the old order."""
        response = self._request("POST", "/api/v5/trade/amend-order", {
            "instId": inst_id, "ordId": str(order_id),
            "newPx": format(Decimal(str(price)), "f"),
            "reqId": str(request_id), "cxlOnFail": False,
        }, private=True)
        rows = response.get("data") or []
        if not rows or str(rows[0].get("ordId") or "") != str(order_id):
            raise OkxError("账户05改单响应缺少目标ordId；保留原止盈并停止对账")
        return rows[0]

    def raw_snapshot(self, inst_id: str = "ETH-USDT-SWAP") -> dict:
        positions = self._request("GET", "/api/v5/account/positions", {
            "instId": inst_id}, private=True).get("data", [])
        orders = self._request("GET", "/api/v5/trade/orders-pending", {
            "instType": "SWAP", "instId": inst_id}, private=True).get("data", [])
        return {"positions": positions, "orders": orders}
