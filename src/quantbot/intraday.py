from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class TimeframeSignal:
    bar: str
    candle_time: pd.Timestamp
    direction: int
    close: float
    fast_ma: float
    slow_ma: float
    atr_pct: float
    entry_confirmed: bool


@dataclass(frozen=True)
class DailyRiskState:
    realized_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    trades: int = 0
    circuit_breaker: bool = False


@dataclass(frozen=True)
class IntradayDecision:
    direction: int
    reason: str
    stop_distance_pct: float = 0.0


def latest_timeframe_signal(
    market: pd.DataFrame, bar: str, *, fast_window: int = 20, slow_window: int = 60, atr_window: int = 14
) -> TimeframeSignal:
    frame = market.sort_values("date")
    if len(frame) < slow_window + 1:
        raise ValueError(f"{bar} requires at least {slow_window + 1} confirmed candles")
    close = frame["close"].astype(float)
    fast = close.rolling(fast_window).mean()
    slow = close.rolling(slow_window).mean()
    previous_close = close.shift(1)
    previous_fast = fast.shift(1)
    true_range = pd.concat(
        [(frame["high"] - frame["low"]), (frame["high"] - previous_close).abs(), (frame["low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_pct = float(true_range.rolling(atr_window).mean().iloc[-1] / close.iloc[-1])
    direction = 1 if fast.iloc[-1] > slow.iloc[-1] else -1 if fast.iloc[-1] < slow.iloc[-1] else 0
    crossed_up = previous_close.iloc[-1] <= previous_fast.iloc[-1] and close.iloc[-1] > fast.iloc[-1]
    crossed_down = previous_close.iloc[-1] >= previous_fast.iloc[-1] and close.iloc[-1] < fast.iloc[-1]
    return TimeframeSignal(
        bar=bar,
        candle_time=pd.Timestamp(frame["date"].iloc[-1]),
        direction=direction,
        close=float(close.iloc[-1]),
        fast_ma=float(fast.iloc[-1]),
        slow_ma=float(slow.iloc[-1]),
        atr_pct=atr_pct,
        entry_confirmed=bool(crossed_up if direction == 1 else crossed_down if direction == -1 else False),
    )


def decide_three_timeframe_entry(
    signals: dict[str, TimeframeSignal],
    risk: DailyRiskState,
    *,
    daily_loss_limit: float = 0.0075,
    daily_profit_stop: float = 0.02,
    max_consecutive_losses: int = 2,
    max_trades: int = 24,
    cooldown_seconds: int = 300,
    seconds_since_last_trade: float | None = None,
    round_trip_cost_pct: float = 0.0,
    min_cost_edge_multiple: float = 2.0,
) -> IntradayDecision:
    if risk.circuit_breaker or risk.realized_pnl_pct <= -daily_loss_limit:
        return IntradayDecision(0, "daily loss circuit breaker")
    if risk.realized_pnl_pct >= daily_profit_stop:
        return IntradayDecision(0, "daily profit stop reached")
    if risk.consecutive_losses >= max_consecutive_losses:
        return IntradayDecision(0, "consecutive loss limit reached")
    if risk.trades >= max_trades:
        return IntradayDecision(0, "daily trade limit reached")
    if seconds_since_last_trade is not None and seconds_since_last_trade < cooldown_seconds:
        return IntradayDecision(0, "trade cooldown is active")
    if set(signals) != {"1m", "5m", "15m"}:
        return IntradayDecision(0, "missing timeframe signal")
    direction = signals["5m"].direction
    if direction == 0 or signals["1m"].direction != direction:
        return IntradayDecision(0, "waiting for 1m pullback direction to rejoin 5m structure")
    if not signals["1m"].entry_confirmed:
        return IntradayDecision(0, "waiting for 1m pullback confirmation")
    if signals["5m"].atr_pct < round_trip_cost_pct * min_cost_edge_multiple:
        return IntradayDecision(0, "expected movement does not cover trading costs")
    stop_distance = min(0.006, max(0.002, signals["5m"].atr_pct * 1.75))
    return IntradayDecision(direction, "5m trend with 1m pullback confirmed", stop_distance)


def contracts_for_risk(
    *, equity_usdt: float, risk_fraction: float, price: float, contract_value_coin: float,
    stop_distance_pct: float, max_contracts: int,
) -> int:
    if min(equity_usdt, risk_fraction, price, contract_value_coin, stop_distance_pct, max_contracts) <= 0:
        raise ValueError("position sizing inputs must be positive")
    risk_budget = equity_usdt * risk_fraction
    loss_per_contract = price * contract_value_coin * stop_distance_pct
    return min(max_contracts, max(0, math.floor(risk_budget / loss_per_contract)))
