"""Locally confirmed second-stage base entries from closed candles."""
def base_pullback_confirmation(one, side, first_price, since):
    import pandas as pd
    data = one.sort_values('date').drop_duplicates('date').iloc[:-1].copy()
    if len(data) < 25:
        return None
    close = data.close.astype(float)
    tr = pd.concat([data.high-data.low, (data.high-close.shift()).abs(),
                    (data.low-close.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    recent = data[pd.to_datetime(data.date, utc=True) >= pd.Timestamp(since)].tail(6)
    if len(recent) < 3 or atr <= 0:
        return None
    latest, prior = recent.iloc[-1], recent.iloc[-2]
    price = float(latest.close)
    if side == 'long':
        low = float(recent.low.min())
        high = float(recent.high.max())
        # A real pullback from this local swing is the price-location test.
        # Do not anchor it to stage one's fill: in a rising market a shallow
        # pullback can be valid even when it remains above that first fill.
        ok = (high-low >= atr*.35 and high-price >= atr*.25
              and latest.close > latest.open
              and latest.close >= prior.close and latest.low >= prior.low
              and price-low <= atr*.8)
    else:
        high = float(recent.high.max())
        low = float(recent.low.min())
        ok = (high-low >= atr*.35 and price-low >= atr*.25
              and latest.close < latest.open
              and latest.close <= prior.close and latest.high <= prior.high
              and high-price <= atr*.8)
        # In a waterfall the first stage can be followed by a shallow or
        # unfinished rebound.  The strict local-pullback rule above then
        # rejects the second stage after price has already moved away from
        # the swing high.  Accept the first closed bearish re-acceleration
        # when the move is still bounded relative to the first fill.  This
        # is deliberately capped so it cannot turn into an unlimited chase.
        if not ok and len(recent) >= 4:
            tail = recent.tail(4)
            bearish = latest.close < latest.open
            lower_close = latest.close <= prior.close
            rebound = tail.iloc[-2]
            before_rebound = tail.iloc[-3]
            lower_high = latest.high <= float(rebound.high)
            had_rebound = float(rebound.close) > float(before_rebound.close)
            resumed = price < float(rebound.low)
            bounded = float(first_price) - price <= atr * 2.0
            ok = (bearish and lower_close and lower_high and had_rebound
                  and resumed and bounded)
    if side == 'long' and not ok and len(recent) >= 4:
        tail = recent.tail(4)
        bullish = latest.close > latest.open
        higher_close = latest.close >= prior.close
        higher_low = latest.low >= float(tail.iloc[-3].low)
        had_pullback = float(tail.iloc[-3].close) < float(tail.iloc[-4].close)
        resumed = price > float(tail.iloc[-3].high)
        bounded = price - float(first_price) <= atr * 2.0
        ok = (bullish and higher_close and higher_low and had_pullback
              and resumed and bounded)
    return str(latest.date) if ok else None
