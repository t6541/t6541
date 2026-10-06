import pandas as pd

from quantbot import public_paper
from quantbot.intraday import TimeframeSignal


def test_public_cycle_requires_timeframe_agreement(monkeypatch, tmp_path):
    def candles(symbols, bar, history_limit):
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=61, freq="min"),
            "symbol": [symbols[0]] * 61,
            "open": [100.0] * 61, "high": [101.0] * 61,
            "low": [99.0] * 61, "close": [100.0] * 61, "volume": [1.0] * 61,
        })

    directions = iter([1, -1, 1])
    monkeypatch.setattr(public_paper, "okx_history_market", candles)
    monkeypatch.setattr(public_paper, "latest_timeframe_signal", lambda *args: TimeframeSignal(
        args[1], pd.Timestamp("2026-01-01T01:00:00Z"), next(directions), 100, 101, 100, .01, True))
    metrics = public_paper.run_public_paper_cycle(ledger_path=tmp_path / "paper.sqlite")
    assert metrics.positions == {}
