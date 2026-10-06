import pandas as pd

from quantbot.spike_guard import spike_circuit_breaker


def market(spike_at: int | None = None):
    rows = []
    for index in range(30):
        open_ = 100 + index * .02
        high, low, close, volume = open_ + .4, open_ - .4, open_ + .05, 100.0
        if index == spike_at:
            high, low, close, volume = open_ + .2, open_ - 5.0, open_, 500.0
        rows.append((pd.Timestamp("2026-08-13") + pd.Timedelta(minutes=index), "ETH-USDT-SWAP",
                     open_, high, low, close, volume))
    return pd.DataFrame(rows, columns=("date", "symbol", "open", "high", "low", "close", "volume"))


def test_spike_guard_blocks_recent_extreme_wick_and_volume():
    result = spike_circuit_breaker(market(29), market())
    assert result.blocked
    assert result.timeframe == "1分钟"
    assert "暂停新开仓10分钟" in result.reason


def test_spike_guard_does_not_block_old_or_unconfirmed_conditions():
    assert not spike_circuit_breaker(market(10), market()).blocked
    high_volume_without_spike = market()
    high_volume_without_spike.loc[29, "volume"] = 500
    assert not spike_circuit_breaker(high_volume_without_spike, market()).blocked
