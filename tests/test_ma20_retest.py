import pandas as pd

import quantbot.ma20_retest as ma20_retest
from quantbot.ma20_retest import _ma20, continuation_channel, evaluate_ma20_retest, latest_trend_flip, ongoing_downtrend_pullback, ongoing_uptrend_pullback


def market(closes):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(closes), freq="5min"),
        "open": closes, "high": [v + .2 for v in closes], "low": [v - .2 for v in closes], "close": closes,
    })


def test_downtrend_to_uptrend_flip_uses_confirmed_close():
    closes = list(range(140, 100, -1)) + [120, 121, 122, 123]
    direction, index, reason = latest_trend_flip(market(closes))
    assert direction == 1
    assert index == len(closes) - 1
    assert "等待后续5分钟回踩" in reason


def test_no_flip_in_sideways_market():
    direction, index, _ = latest_trend_flip(market([100, 101] * 30))
    assert direction == 0
    assert index is None


def one_minute_market(value=100.0):
    closes = [value] * 120
    frame = market(closes)
    frame["date"] = pd.date_range("2026-01-01", periods=len(frame), freq="1min")
    return frame


def one_minute_reversal(price, bullish):
    frame = one_minute_market(price)
    i = frame.index[-1]
    frame.loc[i, "open"] = price - 0.1 if bullish else price + 0.1
    frame.loc[i, "high"] = price + 0.2
    frame.loc[i, "low"] = price - 0.2
    frame.loc[i, "close"] = price
    frame.loc[i - 1, "close"] = price - 0.1 if bullish else price + 0.1
    return frame


def test_strategy03_executes_shared_early_low_before_ma_cross(monkeypatch):
    five = market([100.0] * 40)
    one = one_minute_market(99.0)
    monkeypatch.setattr(ma20_retest, "terminal_acceleration_short_setup",
                        lambda *_: (False, "", 0.0))
    monkeypatch.setattr(ma20_retest, "terminal_acceleration_long_setup",
                        lambda *_: (False, "", 0.0))
    monkeypatch.setattr(ma20_retest, "shared_low_sweep_reclaim_long_setup",
                        lambda *_: (True, "共享低位扫损收回提前做多", 97.5))
    signal = ma20_retest.evaluate_ma20_with_breakout(five, one, five)
    assert signal.action == "open_long"
    assert signal.five_minute_state == "early_low_sweep_reclaim"
    assert signal.retest_low == 97.5


def test_strategy03_executes_shared_early_high_before_ma_cross(monkeypatch):
    five = market([100.0] * 40)
    one = one_minute_market(101.0)
    monkeypatch.setattr(ma20_retest, "terminal_acceleration_short_setup", lambda *_: (False, "", 0.0))
    monkeypatch.setattr(ma20_retest, "terminal_acceleration_long_setup", lambda *_: (False, "", 0.0))
    monkeypatch.setattr(ma20_retest, "shared_low_sweep_reclaim_long_setup", lambda *_: (False, "", 0.0))
    monkeypatch.setattr(ma20_retest, "shared_high_sweep_reject_short_setup",
                        lambda *_: (True, "共享高位扫高回落提前做空", 103.5))
    signal = ma20_retest.evaluate_ma20_with_breakout(five, one, five)
    assert signal.action == "open_short"
    assert signal.five_minute_state == "early_high_sweep_reject"
    assert signal.retest_high == 103.5


def bullish_flip_market(with_retest=False):
    closes = list(range(140, 100, -1)) + [120, 121, 122, 123]
    frame = market(closes + ([115] if with_retest else []))
    if with_retest:
        ma20 = frame["close"].rolling(20).mean().iloc[-1]
        frame.loc[frame.index[-1], "low"] = ma20 - 0.1
        frame.loc[frame.index[-1], "close"] = max(115.0, ma20 + 0.1)
    return frame


def test_flip_alone_does_not_enter_before_later_five_minute_bar():
    signal = evaluate_ma20_retest(bullish_flip_market(), one_minute_market())
    assert signal.action == "observe"
    assert signal.five_minute_state == "bullish_flip"


def test_later_five_minute_ma20_retest_triggers_long():
    five = bullish_flip_market(with_retest=True)
    low, high = float(five.iloc[-1]["low"]), float(five.iloc[-1]["high"])
    signal = evaluate_ma20_retest(five, one_minute_reversal(low + (high - low) * 0.10, True))
    assert signal.action == "open_long"
    assert signal.direction == 1
    assert "5分钟" in signal.reason


def test_confirmed_flip_first_retest_expires_after_the_setup_bar(monkeypatch):
    five = bullish_flip_market(with_retest=True)
    later = five.iloc[-1].copy()
    later["date"] = five.iloc[-1]["date"] + pd.Timedelta(minutes=5)
    later[["open", "high", "low", "close"]] = [116.0, 116.3, 115.7, 116.1]
    five = pd.concat([five, later.to_frame().T], ignore_index=True)
    monkeypatch.setattr(
        ma20_retest, "classify_trend_regime",
        lambda *_, **__: type("Regime", (), {
            "state": "bullish_reversal_confirmed", "direction": 1,
            "confirmation_index": 43, "reason": "旧上涨翻转",
        })(),
    )
    signal = evaluate_ma20_retest(five, one_minute_reversal(116.0, True))
    assert signal.action == "observe"
    assert signal.five_minute_state == "first_retest_expired"
    assert "旧翻转不再追踪" in signal.reason


def test_old_bullish_flip_is_invalidated_by_closed_five_minute_breakdown(monkeypatch):
    five = bullish_flip_market(with_retest=True)
    for value in (114.0, 112.0, 109.0, 105.0, 100.0):
        row = five.iloc[-1].copy()
        row["date"] = five.iloc[-1]["date"] + pd.Timedelta(minutes=5)
        row[["open", "high", "low", "close"]] = [value + 2, value + 2.2, value - .2, value]
        five = pd.concat([five, row.to_frame().T], ignore_index=True)
    monkeypatch.setattr(
        ma20_retest, "classify_trend_regime",
        lambda *_, **__: type("Regime", (), {
            "state": "bullish_reversal_confirmed", "direction": 1,
            "confirmation_index": 43, "reason": "旧上涨翻转",
        })(),
    )
    signal = evaluate_ma20_retest(five, one_minute_reversal(100.0, True))
    assert signal.action == "observe"
    assert signal.five_minute_state == "stale_flip_invalidated"
    assert "旧上涨翻转已失效" in signal.reason


def test_one_minute_must_reach_lower_entry_zone_after_five_minute_retest():
    five = bullish_flip_market(with_retest=True)
    signal = evaluate_ma20_retest(five, one_minute_market(500.0))
    assert signal.action == "observe"
    assert "1分钟" in signal.reason


def test_later_five_minute_ma20_retest_triggers_short():
    closes = list(range(100, 140)) + [120, 119, 118, 117, 125]
    five = market(closes)
    ma20 = five["close"].rolling(20).mean().iloc[-1]
    five.loc[five.index[-1], "high"] = ma20 + 0.1
    five.loc[five.index[-1], "close"] = min(125.0, ma20 - 0.1)
    low, high = float(five.iloc[-1]["low"]), float(five.iloc[-1]["high"])
    signal = evaluate_ma20_retest(five, one_minute_reversal(low + (high - low) * 0.90, False))
    assert signal.action == "open_short"
    assert signal.direction == -1


def test_two_or_three_bars_above_ma20_do_not_cancel_confirmed_downtrend():
    closes = list(range(100, 140)) + [120, 119, 118, 117, 125, 126, 127]
    direction, index, _ = latest_trend_flip(market(closes))
    assert direction == -1
    assert index == 43


def test_strategy_three_can_short_later_ma20_rejection_in_same_downtrend():
    five = market([120 - i * .3 for i in range(40)])
    # Create a stable trend-start crossing instead of relying on an arbitrary
    # historical trend label.
    five.loc[:19, "close"] = 120
    five.loc[:19, "open"] = 120
    one = one_minute_market(100.0)
    for offset, value in enumerate([99.8, 100.0, 100.1, 99.9, 99.7, 99.5], start=len(one) - 6):
        one.loc[offset, ["open", "high", "low", "close"]] = [value + .1, value + .2, value - .2, value]
    result = ongoing_downtrend_pullback(five, one, _ma20(five), _ma20(one))
    assert result is not None
    assert result.action == "open_short"
    assert result.five_minute_state == "bearish_continuation"
    assert result.cross_time == one.iloc[-1]["date"]

    # Once the previous position is closed, a later confirmed rejection in the
    # same downtrend has a new identity and may create the next sequential trade.
    later = one.copy()
    next_bar = later.iloc[-1].copy()
    next_bar["date"] = later.iloc[-1]["date"] + pd.Timedelta(minutes=1)
    next_bar[["open", "high", "low", "close"]] = [99.7, 99.9, 99.3, 99.4]
    later = pd.concat([later, next_bar.to_frame().T], ignore_index=True)
    second = ongoing_downtrend_pullback(five, later, _ma20(five), _ma20(later))
    assert second is not None
    assert second.cross_time != result.cross_time


def test_strategy_three_mirrors_later_ma20_rebound_in_same_uptrend():
    five = market([100 + i * .3 for i in range(40)])
    five.loc[:19, "close"] = 100
    five.loc[:19, "open"] = 100
    one = one_minute_market(120.0)
    for offset, value in enumerate([120.2, 120.0, 119.9, 120.1, 120.3, 120.5], start=len(one) - 6):
        one.loc[offset, ["open", "high", "low", "close"]] = [value - .1, value + .2, value - .2, value]
    result = ongoing_uptrend_pullback(five, one, _ma20(five), _ma20(one))
    assert result is not None
    assert result.action == "open_long"
    assert result.five_minute_state == "bullish_continuation"
    assert result.cross_time == one.iloc[-1]["date"]


def test_continuation_channel_tracks_twenty_closed_bars():
    frame = market(list(range(100, 120)))
    active, low, high = continuation_channel(frame, 0)
    assert active is True
    assert low == 99.8
    assert high == 119.2


def test_continuation_channel_expires_after_twenty_closed_bars():
    frame = market(list(range(100, 121)))
    active, low, high = continuation_channel(frame, 0)
    assert (active, low, high) == (False, 0.0, 0.0)
