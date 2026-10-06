"""Live contract sizing helpers; no order transport lives in this module."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def minimum_contract_order(instrument: dict, mark_price: str) -> dict[str, str]:
    """Describe the exchange minimum without confusing contracts with USDT.

    For a USDT-settled linear swap, notional is contracts * ctVal * mark.
    The returned API size is always the exchange minSz, never the USDT value.
    """
    if instrument.get("ctType") not in {"", "linear"}:
        raise ValueError("Only linear contracts are supported")
    if instrument.get("settleCcy") not in {"", "USDT"}:
        raise ValueError("Only USDT-settled contracts are supported")
    try:
        contracts = Decimal(str(instrument["minSz"]))
        lot_size = Decimal(str(instrument["lotSz"]))
        contract_value = Decimal(str(instrument["ctVal"]))
        mark = Decimal(str(mark_price))
    except (KeyError, InvalidOperation) as exc:
        raise ValueError("Instrument sizing fields are incomplete") from exc
    if min(contracts, lot_size, contract_value, mark) <= 0:
        raise ValueError("Instrument sizing fields must be positive")
    if contracts % lot_size != 0:
        raise ValueError("Exchange minSz is not aligned to lotSz")
    notional = contracts * contract_value * mark
    return {
        "api_size_contracts": format(contracts, "f"),
        "lot_size_contracts": format(lot_size, "f"),
        "contract_value_base": format(contract_value, "f"),
        "estimated_notional_usdt": format(notional.quantize(Decimal("0.01")), "f"),
        "sizing_rule": "exchange-minimum-contracts",
    }
