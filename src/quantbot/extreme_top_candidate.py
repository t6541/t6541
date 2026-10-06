"""Offline candidate detector; not connected to live execution."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TopParameters:
    distance_atr: float = 1.0
    body_cover: float = 0.55
    valid_minutes: int = 6


def top_entries(frame, parameters):
    """Use completed minute bars to update a running five-minute candidate."""
    import pandas as pd
    five = frame.set_index('date').resample('5min').agg(
        open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
        close=('close', 'last')).dropna()
    close = five.close
    tr = pd.concat([five.high-five.low, (five.high-close.shift()).abs(),
                    (five.low-close.shift()).abs()], axis=1).max(axis=1)
    # Shift ensures only earlier, completed five-minute candles supply context.
    context = pd.DataFrame({'ma20': close.rolling(20).mean().shift(),
                            'atr': tr.rolling(14).mean().shift(),
                            'old_high': five.high.rolling(6).max().shift()})
    minute = frame.copy().set_index('date')
    minute['ma5'] = minute.close.rolling(5).mean()
    minute['prev_ma5'] = minute.ma5.shift()
    minute['prev_open'] = minute.open.shift()
    minute['prev_close'] = minute.close.shift()
    minute['prev_high'] = minute.high.shift()
    minute['bucket'] = minute.index.floor('5min')
    minute = minute.join(context, on='bucket')
    used = set()
    candidate = None
    bucket = None
    running_high = running_low = 0.0
    entries = []
    for row in minute.itertuples():
        if row.bucket != bucket:
            bucket = row.bucket
            running_high, running_low = row.high, row.low
        else:
            running_high = max(running_high, row.high)
            running_low = min(running_low, row.low)
        if not row.atr > 0:
            continue
        if candidate:
            anchor, peak, atr, key = candidate
            age = (row.Index-anchor).total_seconds()/60
            if age > parameters.valid_minutes or row.high > peak + .25*atr:
                candidate = None
            elif age > 0:
                body = row.prev_close-row.prev_open
                reverse = row.open-row.close
                confirmed = (body > 0 and reverse > 0
                             and reverse >= parameters.body_cover*body
                             and row.close <= row.prev_close-parameters.body_cover*body
                             and row.close < row.ma5 and row.ma5 <= row.prev_ma5)
                if confirmed and key not in used:
                    entries.append((row.Index, row.close, peak, key))
                    used.add(key)
                    candidate = None
        if (candidate is None and bucket not in used and row.close > row.open
                and running_high > row.old_high
                and (running_high-row.ma20)/row.atr >= parameters.distance_atr
                and (running_high-running_low)/row.atr >= 1.5):
            candidate = (row.Index, running_high, row.atr, bucket)
        elif candidate and row.high > candidate[1]:
            candidate = (row.Index, row.high, candidate[2], candidate[3])
    return entries
