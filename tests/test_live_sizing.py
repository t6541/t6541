import pytest

from quantbot.live_sizing import minimum_contract_order


def test_eth_minimum_contract_is_about_two_and_a_half_usdt_notional():
    result = minimum_contract_order({
        "ctType": "linear", "settleCcy": "USDT", "ctVal": "0.1",
        "minSz": "0.01", "lotSz": "0.01",
    }, "2460")
    assert result["api_size_contracts"] == "0.01"
    assert result["estimated_notional_usdt"] == "2.46"


def test_sizing_rejects_non_usdt_or_misaligned_exchange_minimum():
    with pytest.raises(ValueError, match="USDT-settled"):
        minimum_contract_order({
            "ctType": "linear", "settleCcy": "BTC", "ctVal": "0.1",
            "minSz": "0.01", "lotSz": "0.01",
        }, "2460")
    with pytest.raises(ValueError, match="aligned"):
        minimum_contract_order({
            "ctType": "linear", "settleCcy": "USDT", "ctVal": "0.1",
            "minSz": "0.015", "lotSz": "0.01",
        }, "2460")
