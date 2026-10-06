from __future__ import annotations

import pandas as pd


def target_weights(factors: pd.DataFrame, top_n: int, rebalance_every: int) -> pd.DataFrame:
    scores = factors["score"].unstack("symbol").sort_index()
    weights = pd.DataFrame(float("nan"), index=scores.index, columns=scores.columns)
    for position in range(0, len(scores), rebalance_every):
        weights.iloc[position] = 0.0
        row = scores.iloc[position].dropna().nlargest(top_n)
        if len(row):
            weights.loc[scores.index[position], row.index] = 1.0 / len(row)
    return weights.ffill(limit=rebalance_every - 1).fillna(0.0)


def dual_ma_trend_weights(
    market: pd.DataFrame,
    *,
    fast_window: int,
    slow_window: int,
    volatility_window: int,
    max_annualized_volatility: float,
    stop_loss_pct: float,
    long_weight: float,
    short_weight: float,
    annualization: int,
) -> pd.DataFrame:
    """Close-only long/short trend signal; execution is delayed by the backtester."""
    close = market.pivot(index="date", columns="symbol", values="close").sort_index()
    fast = close.rolling(fast_window, min_periods=fast_window).mean()
    slow = close.rolling(slow_window, min_periods=slow_window).mean()
    annualized_vol = close.pct_change(fill_method=None).rolling(
        volatility_window, min_periods=volatility_window
    ).std() * annualization**0.5
    raw = pd.DataFrame(0, index=close.index, columns=close.columns, dtype=int)
    eligible = annualized_vol <= max_annualized_volatility
    raw[(fast > slow) & eligible] = 1
    raw[(fast < slow) & eligible] = -1

    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for symbol in close.columns:
        position = 0
        peak_or_trough = 0.0
        blocked_direction = 0
        for date in close.index:
            price = float(close.at[date, symbol])
            signal = int(raw.at[date, symbol])
            if signal != blocked_direction:
                blocked_direction = 0
            if position == 1:
                peak_or_trough = max(peak_or_trough, price)
                if price <= peak_or_trough * (1 - stop_loss_pct) or signal != 1:
                    if price <= peak_or_trough * (1 - stop_loss_pct):
                        blocked_direction = 1
                    position = 0
            elif position == -1:
                peak_or_trough = min(peak_or_trough, price)
                if price >= peak_or_trough * (1 + stop_loss_pct) or signal != -1:
                    if price >= peak_or_trough * (1 + stop_loss_pct):
                        blocked_direction = -1
                    position = 0
            if position == 0 and signal and signal != blocked_direction:
                position = signal
                peak_or_trough = price
            weights.at[date, symbol] = long_weight if position == 1 else -short_weight if position == -1 else 0.0
    return weights
