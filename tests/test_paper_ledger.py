import pandas as pd
from quantbot.paper_ledger import PaperLedger


def test_incremental_public_market_updates_are_idempotent(tmp_path):
    market = pd.DataFrame([
        {"date": "2026-01-01T00:00:00Z", "symbol": "ETH-USDT-SWAP", "close": 100},
        {"date": "2026-01-01T01:00:00Z", "symbol": "ETH-USDT-SWAP", "close": 110},
    ])
    ledger = PaperLedger(tmp_path / "paper.sqlite", initial_cash=1000, fee_bps=0, slippage_bps=0)
    targets = pd.DataFrame({"ETH-USDT-SWAP": [0.0, 1.0]}, index=pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"]))
    first = ledger.update(market, targets)
    second = ledger.update(market, targets)
    assert first.equity == second.equity == 1000.0
    assert second.timestamp == "2026-01-01 01:00:00+00:00"


def test_target_position_applies_on_next_price_change(tmp_path):
    market = pd.DataFrame([
        {"date": "2026-01-01T00:00:00Z", "symbol": "ETH-USDT-SWAP", "close": 100},
        {"date": "2026-01-01T01:00:00Z", "symbol": "ETH-USDT-SWAP", "close": 110},
        {"date": "2026-01-01T02:00:00Z", "symbol": "ETH-USDT-SWAP", "close": 121},
    ])
    targets = pd.DataFrame({"ETH-USDT-SWAP": [0.0, 1.0, 1.0]}, index=pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", "2026-01-01T02:00:00Z"]))
    metrics = PaperLedger(tmp_path / "paper.sqlite", initial_cash=1000, fee_bps=0, slippage_bps=0).update(market, targets)
    assert metrics.equity == 1100.0
