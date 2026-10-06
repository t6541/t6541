"""Auditable market context calculated only from confirmed exchange candles.

SMC names describe compatible price-action primitives. This module does not
claim parity with the proprietary LuxAlgo browser indicator.
"""
from __future__ import annotations

import math
import pandas as pd


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    close = frame["close"].astype(float)
    previous = close.shift(1)
    ranges = pd.concat((
        frame["high"].astype(float) - frame["low"].astype(float),
        (frame["high"].astype(float) - previous).abs(),
        (frame["low"].astype(float) - previous).abs(),
    ), axis=1).max(axis=1)
    return ranges.ewm(alpha=1.0 / period, adjust=False).mean()


def _supertrend(frame: pd.DataFrame, period: int = 14, multiplier: float = 3.0) -> tuple[int, float]:
    market = frame.reset_index(drop=True)
    high, low = market["high"].astype(float), market["low"].astype(float)
    close, atr = market["close"].astype(float), _atr(market, period)
    midpoint = (high + low) / 2.0
    upper, lower = midpoint + multiplier * atr, midpoint - multiplier * atr
    final_upper, final_lower = upper.copy(), lower.copy()
    trend = pd.Series(1, index=close.index, dtype="int64")
    for index in range(1, len(close)):
        final_upper.iloc[index] = (upper.iloc[index] if
            upper.iloc[index] < final_upper.iloc[index - 1] or close.iloc[index - 1] > final_upper.iloc[index - 1]
            else final_upper.iloc[index - 1])
        final_lower.iloc[index] = (lower.iloc[index] if
            lower.iloc[index] > final_lower.iloc[index - 1] or close.iloc[index - 1] < final_lower.iloc[index - 1]
            else final_lower.iloc[index - 1])
        if trend.iloc[index - 1] > 0:
            trend.iloc[index] = -1 if close.iloc[index] < final_lower.iloc[index] else 1
        else:
            trend.iloc[index] = 1 if close.iloc[index] > final_upper.iloc[index] else -1
    line = final_lower.iloc[-1] if trend.iloc[-1] > 0 else final_upper.iloc[-1]
    return int(trend.iloc[-1]), float(line)


def exchange_market_context(frame: pd.DataFrame, direction: int) -> dict[str, object]:
    """Return trend, structure, support/resistance and SMC-compatible features."""
    market = frame.sort_values("date").reset_index(drop=True)
    if direction not in {-1, 1} or len(market) < 35:
        raise ValueError("market context needs a direction and at least 35 confirmed candles")
    close = market["close"].astype(float)
    high, low = market["high"].astype(float), market["low"].astype(float)
    latest, atr_value = float(close.iloc[-1]), float(_atr(market).iloc[-1])
    supertrend_direction, supertrend_line = _supertrend(market)
    prior = market.iloc[-21:-1]
    above = prior["high"].astype(float)
    above = above[above > latest]
    below = prior["low"].astype(float)
    below = below[below < latest]
    resistance = float(above.min()) if not above.empty else math.inf
    support = float(below.max()) if not below.empty else -math.inf
    runway = resistance - latest if direction > 0 else latest - support
    swing = market.iloc[-12:-2]
    prior_high, prior_low = float(swing["high"].max()), float(swing["low"].min())
    bos_direction = 1 if latest > prior_high else -1 if latest < prior_low else 0
    sweep = ("low_reclaim" if float(low.iloc[-1]) < prior_low and latest > prior_low else
             "high_reject" if float(high.iloc[-1]) > prior_high and latest < prior_high else "none")
    fvg = ("bullish" if float(low.iloc[-1]) > float(high.iloc[-3]) else
           "bearish" if float(high.iloc[-1]) < float(low.iloc[-3]) else "none")
    structure_bias = bos_direction or (1 if latest > float(close.tail(20).mean()) else -1)
    volume = market["volume"].astype(float)
    money_flow_multiplier = ((close - low) - (high - close)) / (high - low).replace(0, float("nan"))
    money_flow_volume = money_flow_multiplier.fillna(0.0) * volume
    volume_sum = float(volume.tail(20).sum())
    chaikin_money_flow = float(money_flow_volume.tail(20).sum() / volume_sum) if volume_sum > 0 else 0.0
    range_position = (latest - prior_low) / (prior_high - prior_low) if prior_high > prior_low else .5
    zone = "discount" if range_position < .4 else "premium" if range_position > .6 else "equilibrium"
    tolerance = max(atr_value * .10, latest * .00005)
    recent_highs, recent_lows = high.iloc[-10:-1], low.iloc[-10:-1]
    equal_high = bool((recent_highs - float(recent_highs.max())).abs().le(tolerance).sum() >= 2)
    equal_low = bool((recent_lows - float(recent_lows.min())).abs().le(tolerance).sum() >= 2)
    older, newer = market.iloc[-20:-10], market.iloc[-10:]
    older_mid = float(older["close"].mean())
    newer_mid = float(newer["close"].mean())
    prior_structure_bias = 1 if older_mid > float(market.iloc[-30:-20]["close"].mean()) else -1
    current_structure_bias = 1 if newer_mid > older_mid else -1
    choch_direction = current_structure_bias if current_structure_bias != prior_structure_bias else 0
    returns = close.pct_change().tail(20)
    return_std = float(returns.std())
    normalized_momentum = 0.0 if return_std <= 0 else float(returns.mean() / return_std)
    return {
        "source": "okx_confirmed_candles",
        "supertrend_direction": supertrend_direction,
        "supertrend_line": round(supertrend_line, 6),
        "support": None if not math.isfinite(support) else round(support, 6),
        "resistance": None if not math.isfinite(resistance) else round(resistance, 6),
        "runway_points": None if not math.isfinite(runway) else round(runway, 6),
        "runway_atr": None if not math.isfinite(runway) or atr_value <= 0 else round(runway / atr_value, 4),
        "structure_bias": structure_bias, "bos_direction": bos_direction,
        "liquidity_sweep": sweep, "fair_value_gap": fvg,
        "choch_direction": choch_direction,
        "equal_high": equal_high, "equal_low": equal_low,
        "premium_discount_zone": zone,
        "range_position": round(range_position, 4),
        "chaikin_money_flow_20": round(chaikin_money_flow, 6),
        "normalized_momentum_20": round(normalized_momentum, 6),
        "smc_compatible_only": True, "luxalgo_parity_claimed": False,
    }
