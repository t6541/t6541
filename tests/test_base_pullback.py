import pandas as pd
from quantbot.base_pullback import base_pullback_confirmation


def market():
    dates = pd.date_range('2026-01-01', periods=30, freq='min')
    close = [100.]*26+[99.7,99.5,99.6,105.]
    return pd.DataFrame({'date':dates,'open':[x+.02 for x in close],
                         'close':close,'high':[x+.15 for x in close],
                         'low':[x-.15 for x in close]})


def test_low_confirmation_ignores_running_bar_and_uses_local_pullback_not_first_fill():
    f=market()
    f.loc[28,'open']=99.55
    assert base_pullback_confirmation(f,'long',100.,'2026-01-01T00:00:00Z')
    assert base_pullback_confirmation(f,'long',99.6,'2026-01-01T00:00:00Z')
    assert base_pullback_confirmation(f,'long',100.,'2026-01-01T00:28:00Z') is None


def test_falling_low_is_not_a_stabilized_entry():
    f=market()
    f.loc[28,'open']=99.55
    f.loc[28,'low']=99.2
    assert base_pullback_confirmation(f,'long',100.,'2026-01-01T00:00:00Z') is None


def test_rising_market_pullback_can_fill_above_first_stage_price():
    f=market()
    f.loc[28,'open']=99.55
    # First stage at 98: the local pullback is valid even though price is higher.
    assert base_pullback_confirmation(f,'long',98.,'2026-01-01T00:00:00Z')


def test_long_rebound_too_far_from_local_low_is_not_chased():
    f=market()
    f.loc[28,'open']=99.55
    f.loc[28,'close']=100.1
    f.loc[28,'high']=100.2
    assert base_pullback_confirmation(f,'long',98.,'2026-01-01T00:00:00Z') is None


def test_short_waterfall_accepts_first_closed_reacceleration_after_shallow_rebound():
    dates = pd.date_range('2026-01-01', periods=30, freq='min')
    close = [100.0] * 26 + [99.8, 100.2, 99.9, 99.4]
    f = pd.DataFrame({'date': dates, 'open': [x + .05 for x in close],
                      'close': close, 'high': [x + .12 for x in close],
                      'low': [x - .12 for x in close]})
    # The last bar is the running bar and is ignored; bar 28 is the first
    # closed bearish re-acceleration after the small rebound at bar 27.
    f.loc[28, 'open'] = 100.1
    f.loc[28, 'high'] = 100.25
    f.loc[28, 'low'] = 99.7
    f.loc[29, 'open'] = 99.6
    f.loc[29, 'high'] = 99.7
    f.loc[29, 'low'] = 99.2
    assert base_pullback_confirmation(
        f, 'short', 100.0, '2026-01-01T00:00:00Z')


def test_short_waterfall_fallback_does_not_chase_beyond_two_atr():
    dates = pd.date_range('2026-01-01', periods=30, freq='min')
    close = [100.0] * 26 + [99.8, 100.2, 96.0, 95.5]
    f = pd.DataFrame({'date': dates, 'open': [x + .05 for x in close],
                      'close': close, 'high': [x + .12 for x in close],
                      'low': [x - .12 for x in close]})
    f.loc[28, 'open'] = 100.1
    f.loc[29, 'open'] = 96.1
    assert base_pullback_confirmation(
        f, 'short', 100.0, '2026-01-01T00:00:00Z') is None
