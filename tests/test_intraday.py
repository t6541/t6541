import pandas as pd

from quantbot.intraday import (
    DailyRiskState,
    TimeframeSignal,
    contracts_for_risk,
    decide_three_timeframe_entry,
    latest_timeframe_signal,
)


def _signal(bar, direction=1, confirmed=True, atr_pct=.001):
    return TimeframeSignal(bar, pd.Timestamp("2026-08-09"), direction, 1900, 1901, 1890, atr_pct, confirmed)


def test_fifteen_minute_conflict_does_not_veto_five_minute_trend():
    signals = {"1m": _signal("1m"), "5m": _signal("5m"), "15m": _signal("15m", -1)}
    assert decide_three_timeframe_entry(signals, DailyRiskState()).direction == 1


def test_three_timeframe_entry_uses_bounded_atr_stop():
    signals = {bar: _signal(bar) for bar in ("1m", "5m", "15m")}
    decision = decide_three_timeframe_entry(signals, DailyRiskState())
    assert decision.direction == 1
    assert decision.stop_distance_pct == .002


def test_daily_loss_and_trade_limits_block_entries():
    signals = {bar: _signal(bar) for bar in ("1m", "5m", "15m")}
    assert decide_three_timeframe_entry(signals, DailyRiskState(realized_pnl_pct=-.008)).direction == 0
    assert decide_three_timeframe_entry(signals, DailyRiskState(trades=3), max_trades=3).direction == 0


def test_contract_sizing_respects_risk_and_cap():
    assert contracts_for_risk(equity_usdt=1000, risk_fraction=.002, price=2000, contract_value_coin=.1, stop_distance_pct=.005, max_contracts=10) == 2
    assert contracts_for_risk(equity_usdt=10000, risk_fraction=.002, price=2000, contract_value_coin=.1, stop_distance_pct=.005, max_contracts=1) == 1


def test_cooldown_and_cost_edge_block_overtrading():
    signals = {bar: _signal(bar, atr_pct=.001) for bar in ("1m", "5m", "15m")}
    assert decide_three_timeframe_entry(signals, DailyRiskState(), seconds_since_last_trade=120).reason == "trade cooldown is active"
    decision = decide_three_timeframe_entry(signals, DailyRiskState(), round_trip_cost_pct=.0006, min_cost_edge_multiple=2)
    assert decision.reason == "expected movement does not cover trading costs"


def test_latest_signal_uses_confirmed_history_shape():
    close = list(range(100, 162))
    frame = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=len(close), freq="min"), "symbol": "ETH-USDT-SWAP", "open": close, "high": close, "low": close, "close": close, "volume": 1})
    assert latest_timeframe_signal(frame, "1m").direction == 1
