from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from .okx import OkxDemoClient

REQUIRED = ["date", "symbol", "open", "high", "low", "close", "volume"]


def validate_market_data(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(REQUIRED) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing market columns: {sorted(missing)}")
    out = frame[REQUIRED].copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise")
    out["symbol"] = out["symbol"].astype(str)
    for col in REQUIRED[2:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"])
    if out[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("OHLC data contains missing/non-numeric values")
    if (out[["open", "high", "low", "close"]] <= 0).any().any() or (out["volume"] < 0).any():
        raise ValueError("Prices must be positive and volume non-negative")
    if ((out["high"] < out[["open", "close", "low"]].max(axis=1)) | (out["low"] > out[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC relationship")
    return out.reset_index(drop=True)


def load_csv(path: str | Path) -> pd.DataFrame:
    return validate_market_data(pd.read_csv(path))


def synthetic_market(start: str, end: str, symbols: tuple[str, ...], seed: int) -> pd.DataFrame:
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    rows = []
    for idx, symbol in enumerate(symbols):
        returns = rng.normal(0.00015 + idx * 0.00003, 0.012 + idx * 0.0003, len(dates))
        close = 100 * np.exp(np.cumsum(returns))
        open_ = close * np.exp(rng.normal(0, 0.002, len(dates)))
        spread = np.abs(rng.normal(0.006, 0.002, len(dates)))
        high = np.maximum(open_, close) * (1 + spread)
        low = np.minimum(open_, close) * (1 - spread)
        volume = rng.integers(100_000, 2_000_000, len(dates))
        rows.extend(zip(dates, [symbol] * len(dates), open_, high, low, close, volume))
    return validate_market_data(pd.DataFrame(rows, columns=REQUIRED))


def okx_history_market(symbols: tuple[str, ...], bar: str, history_limit: int) -> pd.DataFrame:
    """Load confirmed public OKX candles. This function never uses private credentials."""
    client = OkxDemoClient()
    rows: list[tuple[object, ...]] = []
    for symbol in symbols:
        for candle in client.history_candles(symbol, bar=bar, total=history_limit):
            timestamp, open_, high, low, close, volume = candle[:6]
            date = pd.to_datetime(int(timestamp), unit="ms", utc=True).tz_localize(None)
            rows.append((date, symbol, open_, high, low, close, volume))
    if not rows:
        raise ValueError("OKX returned no confirmed historical candles")
    return validate_market_data(pd.DataFrame(rows, columns=REQUIRED))


def okx_current_unconfirmed_market(symbol: str, bar: str = "1m") -> pd.DataFrame:
    """Return only the current OKX confirm=0 candle for an intrabar trigger."""
    rows = []
    for candle in OkxDemoClient().current_candles(symbol, bar=bar, limit=2):
        if candle[8] != "0":
            continue
        timestamp, open_, high, low, close, volume = candle[:6]
        date = pd.to_datetime(int(timestamp), unit="ms", utc=True).tz_localize(None)
        rows.append((date, symbol, open_, high, low, close, volume))
    if not rows:
        return pd.DataFrame(columns=REQUIRED)
    return validate_market_data(pd.DataFrame(rows, columns=REQUIRED))


def okx_recent_market(symbol: str, bar: str, limit: int = 100) -> pd.DataFrame:
    """Load one fast public snapshot containing confirmed and running candles."""
    rows = []
    for candle in OkxDemoClient().current_candles(symbol, bar=bar, limit=limit):
        timestamp, open_, high, low, close, volume = candle[:6]
        date = pd.to_datetime(int(timestamp), unit="ms", utc=True).tz_localize(None)
        rows.append((date, symbol, open_, high, low, close, volume))
    if not rows:
        raise ValueError(f"OKX returned no recent {bar} candles")
    return validate_market_data(pd.DataFrame(rows, columns=REQUIRED))
