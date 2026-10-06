from __future__ import annotations

import pandas as pd


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    previous_close = frame["close"].astype(float).shift(1)
    return pd.concat((
        frame["high"].astype(float) - frame["low"].astype(float),
        (frame["high"].astype(float) - previous_close).abs(),
        (frame["low"].astype(float) - previous_close).abs(),
    ), axis=1).max(axis=1).rolling(window).mean()


def recent_ma_fan_endpoint(
        frame: pd.DataFrame, direction: int, *, lookback: int = 8,
) -> dict | None:
    """Return a genuine local MA5/MA10/MA20 fan endpoint.

    A local high/low is not enough.  Its close must remain beyond both MA5 and
    MA20 while those outer averages are materially expanded and MA5 is still
    travelling in the exhausted direction.  MA10 is recorded but deliberately
    not gated because it may cross inside a valid endpoint zone.
    """
    if frame is None or direction not in {-1, 1} or len(frame) < 24:
        return None
    data = frame.sort_values("date").drop_duplicates(
        "date", keep="last").reset_index(drop=True).copy()
    close = data["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(window).mean()
                       for window in (5, 10, 20))
    atr = _atr(data)
    first = max(20, len(data) - max(2, int(lookback)))
    for index in range(len(data) - 1, first - 1, -1):
        values = (ma5.iloc[index], ma10.iloc[index], ma20.iloc[index],
                  atr.iloc[index])
        if not all(pd.notna(value) for value in values):
            continue
        m5, m10, m20, current_atr = map(float, values)
        if current_atr <= 0:
            continue
        row = data.iloc[index]
        endpoint_close = float(row["close"])
        recent = data.iloc[max(0, index - 7):index + 1]
        if direction > 0:
            outer_order = m5 < m20
            outer_close = endpoint_close <= min(m5, m20) + current_atr * .03
            at_edge = float(row["low"]) <= float(recent["low"].min()) + current_atr * .12
            extended = m20 - float(row["low"]) >= current_atr * .60
            travelling = index >= 3 and m5 < float(ma5.iloc[index - 3])
            extreme = float(recent["low"].min())
        else:
            outer_order = m5 > m20
            outer_close = endpoint_close >= max(m5, m20) - current_atr * .03
            at_edge = float(row["high"]) >= float(recent["high"].max()) - current_atr * .12
            extended = float(row["high"]) - m20 >= current_atr * .60
            travelling = index >= 3 and m5 > float(ma5.iloc[index - 3])
            extreme = float(recent["high"].max())
        total_spread = abs(m5 - m20)
        if (outer_order and total_spread >= current_atr * .25
                and outer_close and at_edge and extended and travelling):
            return {
                "time": pd.Timestamp(row["date"]), "index": index,
                "extreme": extreme, "ma5": m5, "ma10": m10, "ma20": m20,
                "atr": current_atr, "spread_atr": total_spread / current_atr,
                "close_outside_ma5_ma20": True,
            }
    return None


def candle_direction(row: pd.Series) -> int:
    """Return the direction of one exchange/raw candle."""
    body = float(row["close"]) - float(row["open"])
    return 1 if body > 0 else -1 if body < 0 else 0


def latest_price_reversal(
        frame: pd.DataFrame, *, lookback: int = 6,
        minimum_opposite_bars: int = 1) -> dict | None:
    """Identify an early reversal directly from ordinary exchange candles.

    The trigger is intentionally price-action based: a fresh opposite-colour
    candle at a recent edge, a half-body cover, or a sweep-and-reclaim/reject.
    MA-stack maturity is classified separately, so this detector does not turn
    every local colour change into a "true" endpoint.
    """
    if frame is None or len(frame) < max(4, minimum_opposite_bars + 2):
        return None
    raw = frame.sort_values("date").reset_index(drop=True)
    latest = raw.iloc[-1]
    previous = raw.iloc[-2]
    direction = candle_direction(latest)
    if direction == 0:
        return None

    prior = raw.iloc[-lookback - 1:-1]
    prior_directions = [candle_direction(row) for _, row in prior.iterrows()]
    opposite_count = sum(value == -direction for value in prior_directions)
    fresh_turn = candle_direction(previous) in {-direction, 0}

    previous_open = float(previous["open"])
    previous_close = float(previous["close"])
    previous_midpoint = (previous_open + previous_close) / 2.0
    close = float(latest["close"])
    half_cover = bool(
        candle_direction(previous) == -direction
        and direction * (close - previous_midpoint) >= 0
    )

    earlier = raw.iloc[-lookback - 1:-1]
    high = float(latest["high"])
    low = float(latest["low"])
    span = max(high - low, 1e-9)
    swept_low = low < float(earlier["low"].astype(float).min())
    swept_high = high > float(earlier["high"].astype(float).max())
    sweep_reversal = bool(
        direction > 0 and swept_low and (close - low) / span >= .45
        or direction < 0 and swept_high and (high - close) / span >= .45
    )

    edge_high = high >= float(raw.tail(lookback + 1)["high"].astype(float).max())
    edge_low = low <= float(raw.tail(lookback + 1)["low"].astype(float).min())
    edge_turn = fresh_turn and (edge_low if direction > 0 else edge_high)
    if opposite_count < minimum_opposite_bars or not (
            half_cover or sweep_reversal or edge_turn):
        return None

    window = raw.tail(lookback + 1)
    extreme = (float(window["low"].astype(float).min()) if direction > 0
               else float(window["high"].astype(float).max()))
    trigger = ("普通K线扫低收回" if direction > 0 else "普通K线扫高回落") if sweep_reversal else (
        "普通K线过半覆盖转强" if direction > 0 else "普通K线过半覆盖转弱") if half_cover else (
        "普通K线局部低点转强" if direction > 0 else "普通K线局部高点转弱")
    return {
        "direction": direction,
        "time": pd.Timestamp(latest["date"]),
        "price": close,
        "extreme": extreme,
        "run_bars": opposite_count,
        "trigger": trigger,
    }


def recent_price_reversal_confirms(
        frame: pd.DataFrame, direction: int, *, lookback: int = 3) -> bool:
    """Confirm a matching ordinary-candle turn in the latest few bars."""
    if frame is None or direction not in {-1, 1} or len(frame) < 5:
        return False
    raw = frame.sort_values("date").reset_index(drop=True)
    close = raw["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ranges = (raw["high"].astype(float) - raw["low"].astype(float)).tail(5)
    tolerance = max(1e-9, float(ranges.median()) * .10)
    if (not pd.notna(ma5.iloc[-2])
            or direction * float(ma5.iloc[-1] - ma5.iloc[-2]) < -tolerance):
        return False
    directions = [candle_direction(row) for _, row in raw.iterrows()]
    start = max(1, len(directions) - max(1, lookback))
    return any(directions[index] == direction
               and directions[index - 1] in {-direction, 0}
               for index in range(start, len(directions)))
