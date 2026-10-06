from quantbot.execution_guards import (blocks_early_countertrend_reversal,
    matched_closing_fills, ordinary_stop_is_too_close, primary_timeframes_aligned)


def test_countertrend_first_turn_is_observation_only():
    d = {"1m": 1, "5m": -1, "15m": -1, "30m": -1, "1H": -1, "4H": -1}
    assert blocks_early_countertrend_reversal(d, 1)


def test_30m_1h_4h_never_create_alignment_count_entry_veto():
    d = {"1m": 1, "5m": 1, "15m": 1, "30m": -1, "1H": -1, "4H": -1}
    assert not blocks_early_countertrend_reversal(d, 1)


def test_primary_period_alignment_ignores_1m_4h_and_removed_30m():
    d = {"1m": 1, "5m": -1, "15m": -1, "1H": -1, "4H": 1}
    assert primary_timeframes_aligned(d, -1)
    assert ordinary_stop_is_too_close(100, 1, 99.9, .1)
    assert not ordinary_stop_is_too_close(100, 1, 99, .1)


def test_fill_matching_stops_at_the_entry_quantity():
    row = {"trade_uid": "E1", "direction": 1}
    fills = [
        {"clOrdId": "E1", "ts": "100", "fillSz": "1", "fee": "-.1"},
        {"clOrdId": "X1", "ts": "200", "tradeId": "1", "side": "sell", "posSide": "long", "fillSz": ".6", "fillPnl": "1.2", "fee": "-.06"},
        {"clOrdId": "X1", "ts": "201", "tradeId": "2", "side": "sell", "posSide": "long", "fillSz": ".6", "fillPnl": "1.2", "fee": "-.06"},
        {"clOrdId": "LATER", "ts": "300", "tradeId": "3", "side": "sell", "posSide": "long", "fillSz": "1", "fillPnl": "9", "fee": "-.1"},
    ]
    _, exits = matched_closing_fills(row, fills)
    assert sum(float(x["fillSz"]) for x in exits) == 1
    assert sum(float(x["fillPnl"]) for x in exits) == 2
