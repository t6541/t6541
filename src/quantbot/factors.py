from __future__ import annotations

import numpy as np
import pandas as pd


def _zscore_by_date(values: pd.Series) -> pd.Series:
    mean = values.groupby(level="date").transform("mean")
    std = values.groupby(level="date").transform("std").replace(0, np.nan)
    return ((values - mean) / std).fillna(0.0)


def compute_factors(market: pd.DataFrame, momentum_window: int, volatility_window: int, trend_window: int) -> pd.DataFrame:
    close = market.pivot(index="date", columns="symbol", values="close").sort_index()
    returns = close.pct_change(fill_method=None)
    momentum = close.pct_change(momentum_window, fill_method=None)
    volatility = returns.rolling(volatility_window, min_periods=volatility_window).std()
    trend = close / close.rolling(trend_window, min_periods=trend_window).mean() - 1.0
    factors = pd.concat({"momentum": momentum.stack(future_stack=True), "volatility": volatility.stack(future_stack=True), "trend": trend.stack(future_stack=True)}, axis=1)
    factors.index.names = ["date", "symbol"]
    factors["score"] = _zscore_by_date(factors["momentum"]) + _zscore_by_date(factors["trend"]) - _zscore_by_date(factors["volatility"])
    return factors.sort_index()

