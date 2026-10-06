from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from .data import okx_history_market
from .intraday import latest_timeframe_signal
from .validation_execution import breakout_pullback_long_setup
from .range_pivot import terminal_acceleration_long_setup, terminal_acceleration_short_setup
from .trend_continuation import (
    _atr,
    downtrend_ma5_pullback_short_setup,
    downtrend_pullback_short_setup,
    trend_entry_runway,
    uptrend_ma5_pullback_long_setup,
    uptrend_pullback_long_setup,
)
from .trend_regime import (bottom_color_reversal_long_setup,
                           candidate_reversal_setup, classify_trend_regime,
                           direct_rollover_first_ma5_long_setup,
                           direct_rollover_first_ma5_short_setup,
                           direct_rollover_first_ma20_long_setup,
                           direct_rollover_first_ma20_short_setup,
                           top_color_reversal_short_setup)
from .ma_expansion_chase import dual_timeframe_ma_expansion_chase
from .extreme_entries import (
    shared_high_sweep_reject_short_setup,
    shared_low_sweep_reclaim_long_setup,
    volume_stopping_pullback_long_setup,
)

STRATEGY_ID = "strategy_03"
STRATEGY_VERSION = "ma20_trend_retest_v60"
INITIAL_RETEST_WINDOW = 20
CONTINUATION_WINDOW = 20
ENTRY_ZONE_FRACTION = 0.35
FLIP_CONFIRM_BARS = 4


@dataclass(frozen=True)
class Ma20RetestSignal:
    action: str
    direction: int
    reason: str
    five_minute_state: str
    cross_time: pd.Timestamp | None
    ma20_5m: float
    ma20_1m: float
    retest_low: float
    retest_high: float
    trigger_price: float
    trend_time: pd.Timestamp | None = None
    atr_5m: float = 0.0


def _ma20(frame: pd.DataFrame) -> pd.Series:
    return frame.sort_values("date")["close"].astype(float).rolling(20).mean()


def continuation_channel(candidates: pd.DataFrame, retest_index: int) -> tuple[bool, float, float]:
    """Keep a confirmed setup alive for 20 closed 5m bars, then fail closed."""
    following = candidates.loc[retest_index:]
    if len(following) > CONTINUATION_WINDOW:
        return False, 0.0, 0.0
    tracking = following.iloc[:CONTINUATION_WINDOW]
    return True, float(tracking["low"].min()), float(tracking["high"].max())


def latest_trend_flip(frame: pd.DataFrame, count_window: int = 8,
                      fifteen_minute: pd.DataFrame | None = None) -> tuple[int, int | None, str]:
    regime = classify_trend_regime(
        frame, fifteen_minute, history_window=count_window, block_consolidation=True)
    if regime.state.endswith("_confirmed"):
        return regime.direction, regime.confirmation_index, regime.reason
    return 0, None, regime.reason


def ongoing_downtrend_pullback(
    f5: pd.DataFrame, f1: pd.DataFrame, ma5: pd.Series, ma1: pd.Series
) -> Ma20RetestSignal | None:
    """Enter on a fresh 1m MA20 rejection while the 5m downtrend remains valid."""
    if len(f5) < 24 or len(f1) < 24:
        return None
    close5 = f5["close"].astype(float)
    recent5 = f5.tail(8)
    below_count = int((recent5["close"].astype(float) < ma5.loc[recent5.index]).sum())
    if not (below_count >= 6 and float(close5.iloc[-1]) < float(ma5.iloc[-1])
            and float(ma5.iloc[-1]) < float(ma5.iloc[-4])):
        return None
    crosses = [
        i for i in range(20, len(f5))
        if float(close5.iloc[i - 1]) >= float(ma5.iloc[i - 1])
        and float(close5.iloc[i]) < float(ma5.iloc[i])
    ]
    if not crosses:
        return None
    recent1 = f1.tail(6).copy()
    recent1["ma20"] = ma1.loc[recent1.index]
    touching = recent1[recent1["high"].astype(float) >= recent1["ma20"] * 0.999]
    latest = f1.iloc[-1]
    bearish = (
        not touching.empty
        and float(latest["close"]) < float(ma1.iloc[-1])
        and (float(latest["close"]) < float(latest["open"])
             or float(latest["close"]) < float(f1.iloc[-2]["close"]))
    )
    if not bearish:
        return None
    low = float(recent1["low"].astype(float).min())
    high = max(float(touching["high"].astype(float).max()), float(ma5.iloc[-1]))
    return Ma20RetestSignal(
        "open_short", -1, "5分钟下跌趋势持续有效；1分钟反抽下降中的MA20后再次转弱",
        "bearish_continuation", pd.Timestamp(latest["date"]),
        float(ma5.iloc[-1]), float(ma1.iloc[-1]), low, high, float(latest["close"]),
        pd.Timestamp(f5.iloc[crosses[-1]]["date"]),
    )


def ongoing_uptrend_pullback(
    f5: pd.DataFrame, f1: pd.DataFrame, ma5: pd.Series, ma1: pd.Series
) -> Ma20RetestSignal | None:
    """Mirror: enter on a fresh 1m MA20 rebound while the 5m uptrend remains valid."""
    if len(f5) < 24 or len(f1) < 24:
        return None
    close5 = f5["close"].astype(float)
    recent5 = f5.tail(8)
    above_count = int((recent5["close"].astype(float) > ma5.loc[recent5.index]).sum())
    if not (above_count >= 6 and float(close5.iloc[-1]) > float(ma5.iloc[-1])
            and float(ma5.iloc[-1]) > float(ma5.iloc[-4])):
        return None
    crosses = [
        i for i in range(20, len(f5))
        if float(close5.iloc[i - 1]) <= float(ma5.iloc[i - 1])
        and float(close5.iloc[i]) > float(ma5.iloc[i])
    ]
    if not crosses:
        return None
    recent1 = f1.tail(6).copy()
    recent1["ma20"] = ma1.loc[recent1.index]
    touching = recent1[recent1["low"].astype(float) <= recent1["ma20"] * 1.001]
    latest = f1.iloc[-1]
    bullish = (
        not touching.empty
        and float(latest["close"]) > float(ma1.iloc[-1])
        and (float(latest["close"]) > float(latest["open"])
             or float(latest["close"]) > float(f1.iloc[-2]["close"]))
    )
    if not bullish:
        return None
    low = min(float(touching["low"].astype(float).min()), float(ma5.iloc[-1]))
    high = float(recent1["high"].astype(float).max())
    return Ma20RetestSignal(
        "open_long", 1, "5分钟上涨趋势持续有效；1分钟回踩上升中的MA20后再次转强",
        "bullish_continuation", pd.Timestamp(latest["date"]),
        float(ma5.iloc[-1]), float(ma1.iloc[-1]), low, high, float(latest["close"]),
        pd.Timestamp(f5.iloc[crosses[-1]]["date"]),
    )


def ongoing_trend_pullback(
    f5: pd.DataFrame, f1: pd.DataFrame, ma5: pd.Series, ma1: pd.Series
) -> Ma20RetestSignal | None:
    return (ongoing_downtrend_pullback(f5, f1, ma5, ma1)
            or ongoing_uptrend_pullback(f5, f1, ma5, ma1))


def evaluate_ma20_retest(five_minute: pd.DataFrame, one_minute: pd.DataFrame,
                         fifteen_minute: pd.DataFrame | None = None) -> Ma20RetestSignal:
    f5 = five_minute.sort_values("date").reset_index(drop=True)
    f1 = one_minute.sort_values("date").reset_index(drop=True)
    regime = classify_trend_regime(f5, fifteen_minute, block_consolidation=True)
    direction = regime.direction if regime.state.endswith("_confirmed") else 0
    index = regime.confirmation_index if direction else None
    reason = regime.reason
    ma5 = _ma20(f5)
    ma1 = _ma20(f1)
    close5 = f5["close"].astype(float)
    fast5 = close5.rolling(5).mean()
    medium5 = close5.rolling(10).mean()

    def opposite_breakdown(expected_direction: int) -> bool:
        """Invalidate a historical flip once closed 5m structure reverses."""
        if len(f5) < 21:
            return False
        latest = f5.iloc[-1]
        latest_close = float(latest["close"])
        latest_open = float(latest["open"])
        if expected_direction > 0:
            return (latest_close < float(ma5.iloc[-1])
                    and latest_close < float(fast5.iloc[-1])
                    and latest_close < float(medium5.iloc[-1])
                    and latest_close < latest_open
                    and float(fast5.iloc[-1]) < float(fast5.iloc[-2]))
        return (latest_close > float(ma5.iloc[-1])
                and latest_close > float(fast5.iloc[-1])
                and latest_close > float(medium5.iloc[-1])
                and latest_close > latest_open
                and float(fast5.iloc[-1]) > float(fast5.iloc[-2]))
    if not direction or index is None:
        # A reversal candidate is deliberately a no-trade waiting state.  Do
        # not let the older continuation branch reopen in the former trend.
        if "candidate" in regime.state or "waiting" in regime.state:
            return Ma20RetestSignal("observe", 0, reason, regime.state, None,
                                    float(ma5.iloc[-1]), float(ma1.iloc[-1]), 0, 0, 0)
        continuation = ongoing_trend_pullback(f5, f1, ma5, ma1)
        if continuation is not None:
            return continuation
        return Ma20RetestSignal("observe", 0, reason, "ranging", None, float(ma5.iloc[-1]),
                                float(ma1.iloc[-1]), 0, 0, 0)
    cross_time = pd.Timestamp(f5.iloc[index]["date"])
    if opposite_breakdown(direction):
        side = "上涨" if direction > 0 else "下跌"
        return Ma20RetestSignal(
            "observe", 0,
            f"旧{side}翻转已失效：最新已收盘5分钟K线反向穿越MA20并位于MA5/MA10反侧",
            "stale_flip_invalidated", cross_time, float(ma5.iloc[-1]),
            float(ma1.iloc[-1]), 0, 0, 0,
        )
    # Entry timing belongs to the 5m setup itself. 1m remains display-only and
    # must never turn a late 1m bounce into a strategy-03 entry.
    candidates = f5.iloc[index + 1:].copy()
    if candidates.empty:
        return Ma20RetestSignal("observe", 0, reason, "bullish_flip" if direction > 0 else "bearish_flip",
                                cross_time, float(ma5.iloc[-1]), float(ma1.iloc[-1]), 0, 0, 0)
    candidates["ma20"] = ma5.loc[candidates.index]
    if direction > 0:
        first_candidate = candidates.iloc[:1]
        touched = first_candidate[
            (first_candidate["low"].astype(float) <= first_candidate["ma20"] * 1.001)
            & (first_candidate["close"].astype(float) >= first_candidate["ma20"])
        ]
        if touched.empty:
            return Ma20RetestSignal("observe", 0, reason + "；确认后的首根5分钟K线未形成有效MA20回踩，旧翻转立即作废",
                                    "first_retest_expired",
                                    cross_time, float(ma5.iloc[-1]), float(ma1.iloc[-1]), 0, 0, 0)
        retest_index = int(touched.index[0])
        if retest_index != int(candidates.index[-1]):
            return Ma20RetestSignal(
                "observe", 0, reason + "；首次有效MA20回踩已错过，旧翻转不再追踪",
                "first_retest_expired", cross_time, float(ma5.iloc[-1]),
                float(ma1.iloc[-1]), 0, 0, 0)
        retest = touched.iloc[0]
        low, high = float(retest["low"]), float(retest["high"])
        width = max(0.01, high - low)
        zone_low, zone_high = low, low + width * ENTRY_ZONE_FRACTION
        one = f1.iloc[-1]
        previous_close = float(f1.iloc[-2]["close"])
        one_close = float(one["close"])
        one_bullish = one_close > float(one["open"]) or one_close > previous_close
        if not (zone_low <= one_close <= zone_high and float(one["low"]) <= zone_high and one_bullish):
            return Ma20RetestSignal(
                "observe", 0,
                f"首次5分钟MA20回踩已确认，仅在本次结构区{zone_low:.2f}-{zone_high:.2f}等待1分钟探底转强",
                "bullish_flip", cross_time, float(ma5.iloc[-1]), float(ma1.iloc[-1]), low, high, 0,
            )
        return Ma20RetestSignal(
            "open_long", 1,
            "5分钟回踩MA20已确认，等待价格回到回踩区间下沿再做多",
            "bullish_flip", pd.Timestamp(one["date"]), float(ma5.iloc[-1]), float(ma1.iloc[-1]), low, high,
            one_close, cross_time,
        )
    first_candidate = candidates.iloc[:1]
    touched = first_candidate[
        (first_candidate["high"].astype(float) >= first_candidate["ma20"] * 0.999)
        & (first_candidate["close"].astype(float) <= first_candidate["ma20"])
    ]
    if touched.empty:
        return Ma20RetestSignal("observe", 0, reason + "；确认后的首根5分钟K线未形成有效MA20反抽，旧翻转立即作废",
                                "first_retest_expired",
                                cross_time, float(ma5.iloc[-1]), float(ma1.iloc[-1]), 0, 0, 0)
    retest_index = int(touched.index[0])
    if retest_index != int(candidates.index[-1]):
        return Ma20RetestSignal(
            "observe", 0, reason + "；首次有效MA20反抽已错过，旧翻转不再追踪",
            "first_retest_expired", cross_time, float(ma5.iloc[-1]),
            float(ma1.iloc[-1]), 0, 0, 0)
    retest = touched.iloc[0]
    low, high = float(retest["low"]), float(retest["high"])
    width = max(0.01, high - low)
    zone_low, zone_high = low + width * (1 - ENTRY_ZONE_FRACTION), high * 1.001
    one = f1.iloc[-1]
    previous_close = float(f1.iloc[-2]["close"])
    one_close = float(one["close"])
    one_bearish = one_close < float(one["open"]) or one_close < previous_close
    if not (zone_low <= one_close <= zone_high and float(one["high"]) >= zone_low and one_bearish):
        return Ma20RetestSignal(
            "observe", 0,
            f"首次5分钟MA20反抽已确认，仅在本次结构区{zone_low:.2f}-{zone_high:.2f}等待1分钟冲高转弱",
            "bearish_flip", cross_time, float(ma5.iloc[-1]), float(ma1.iloc[-1]), low, high, 0,
        )
    return Ma20RetestSignal(
        "open_short", -1,
        "5分钟反抽MA20已确认，等待价格回到反抽区间上沿再做空",
        "bearish_flip", pd.Timestamp(one["date"]), float(ma5.iloc[-1]), float(ma1.iloc[-1]), low, high,
        one_close, cross_time,
    )


def evaluate_ma20_with_breakout(five_minute: pd.DataFrame, one_minute: pd.DataFrame,
                                fifteen_minute: pd.DataFrame,
                                thirty_minute: pd.DataFrame | None = None,
                                one_hour: pd.DataFrame | None = None,
                                four_hour: pd.DataFrame | None = None) -> Ma20RetestSignal:
    color_short, color_short_reason, color_short_stop = top_color_reversal_short_setup(
        five_minute, one_minute, fifteen_minute, thirty_minute, one_hour, four_hour)
    color_long, color_long_reason, color_long_stop = bottom_color_reversal_long_setup(
        five_minute, one_minute, fifteen_minute, thirty_minute, one_hour, four_hour)
    if color_short or color_long:
        latest = one_minute.sort_values("date").iloc[-1]
        direction = -1 if color_short else 1
        trigger = float(latest["close"])
        stop = color_short_stop if color_short else color_long_stop
        return Ma20RetestSignal(
            "open_short" if direction < 0 else "open_long", direction,
            color_short_reason if color_short else color_long_reason,
            "top_color_reversal_short" if color_short else "bottom_color_reversal_long",
            pd.Timestamp(latest["date"]), float(_ma20(five_minute).iloc[-1]),
            float(_ma20(one_minute).iloc[-1]),
            trigger if direction < 0 else stop, stop if direction < 0 else trigger,
            trigger, pd.Timestamp(latest["date"]),
        )
    terminal_short, short_reason, short_stop = terminal_acceleration_short_setup(
        five_minute, one_minute, fifteen_minute)
    terminal_long, long_reason, long_stop = terminal_acceleration_long_setup(
        five_minute, one_minute, fifteen_minute)
    if terminal_short or terminal_long:
        latest = one_minute.sort_values("date").iloc[-1]
        direction = -1 if terminal_short else 1
        stop = short_stop if terminal_short else long_stop
        trigger = float(latest["close"])
        return Ma20RetestSignal(
            "open_short" if direction < 0 else "open_long", direction,
            short_reason if terminal_short else long_reason, "terminal_acceleration_reversal",
            pd.Timestamp(latest["date"]), float(_ma20(five_minute).iloc[-1]),
            float(_ma20(one_minute).iloc[-1]),
            trigger if direction < 0 else stop, stop if direction < 0 else trigger,
            trigger, pd.Timestamp(latest["date"]),
        )
    volume_long, volume_reason, volume_stop, volume_target = volume_stopping_pullback_long_setup(
        five_minute, one_minute)
    if volume_long:
        latest = one_minute.sort_values("date").iloc[-1]
        trigger = float(latest["close"])
        return Ma20RetestSignal(
            "open_long", 1, volume_reason, "volume_stopping_pullback_long",
            pd.Timestamp(latest["date"]), float(_ma20(five_minute).iloc[-1]),
            float(_ma20(one_minute).iloc[-1]), volume_stop, volume_target,
            trigger, pd.Timestamp(latest["date"]),
        )
    early_low_long, early_low_reason, early_low_stop = shared_low_sweep_reclaim_long_setup(
        five_minute, one_minute)
    if early_low_long:
        latest = one_minute.sort_values("date").iloc[-1]
        trigger = float(latest["close"])
        return Ma20RetestSignal(
            "open_long", 1, early_low_reason, "early_low_sweep_reclaim",
            pd.Timestamp(latest["date"]), float(_ma20(five_minute).iloc[-1]),
            float(_ma20(one_minute).iloc[-1]), early_low_stop, trigger,
            trigger, pd.Timestamp(latest["date"]),
        )
    early_high_short, early_high_reason, early_high_stop = shared_high_sweep_reject_short_setup(
        five_minute, one_minute)
    if early_high_short:
        latest = one_minute.sort_values("date").iloc[-1]
        trigger = float(latest["close"])
        return Ma20RetestSignal(
            "open_short", -1, early_high_reason, "early_high_sweep_reject",
            pd.Timestamp(latest["date"]), float(_ma20(five_minute).iloc[-1]),
            float(_ma20(one_minute).iloc[-1]), trigger, early_high_stop,
            trigger, pd.Timestamp(latest["date"]),
        )
    direct_ma5_short, direct_ma5_short_reason, direct_ma5_short_stop = direct_rollover_first_ma5_short_setup(
        five_minute, one_minute)
    direct_ma5_long, direct_ma5_long_reason, direct_ma5_long_stop = direct_rollover_first_ma5_long_setup(
        five_minute, one_minute)
    direct_short, direct_reason, direct_stop = direct_rollover_first_ma20_short_setup(
        five_minute, one_minute)
    direct_long, direct_long_reason, direct_long_stop = direct_rollover_first_ma20_long_setup(
        five_minute, one_minute)
    if direct_ma5_short or direct_ma5_long or direct_short or direct_long:
        latest = one_minute.sort_values("date").iloc[-1]
        trigger = float(latest["close"])
        direction = -1 if (direct_ma5_short or direct_short) else 1
        reason = (direct_ma5_short_reason if direct_ma5_short else direct_ma5_long_reason if direct_ma5_long
                  else direct_reason if direct_short else direct_long_reason)
        stop = (direct_ma5_short_stop if direct_ma5_short else direct_ma5_long_stop if direct_ma5_long
                else direct_stop if direct_short else direct_long_stop)
        return Ma20RetestSignal(
            "open_short" if direction < 0 else "open_long", direction, reason,
            ("direct_rollover_first_ma5_short" if direct_ma5_short else
             "direct_rollover_first_ma5_long" if direct_ma5_long else
             "direct_rollover_first_ma20_short" if direct_short else "direct_rollover_first_ma20_long"),
            pd.Timestamp(latest["date"]), float(_ma20(five_minute).iloc[-1]),
            float(_ma20(one_minute).iloc[-1]), trigger if direction < 0 else stop,
            stop if direction < 0 else trigger,
            trigger, pd.Timestamp(latest["date"]),
        )
    candidate_direction, candidate_reason, candidate_stop = candidate_reversal_setup(
        five_minute, one_minute, fifteen_minute, block_consolidation=True)
    if candidate_direction:
        latest = one_minute.sort_values("date").iloc[-1]
        trigger = float(latest["close"])
        return Ma20RetestSignal(
            "open_short" if candidate_direction < 0 else "open_long", candidate_direction,
            candidate_reason, "reversal_candidate_entry", pd.Timestamp(latest["date"]),
            float(_ma20(five_minute).iloc[-1]), float(_ma20(one_minute).iloc[-1]),
            trigger if candidate_direction < 0 else candidate_stop,
            candidate_stop if candidate_direction < 0 else trigger,
            trigger, pd.Timestamp(latest["date"]),
        )
    chase_direction, chase_reason, chase_stop = dual_timeframe_ma_expansion_chase(
        five_minute, one_minute)
    if chase_direction:
        latest = one_minute.sort_values("date").iloc[-1]
        trigger = float(latest["close"])
        atr_value = float(_atr(five_minute.sort_values("date").reset_index(drop=True)).iloc[-1])
        return Ma20RetestSignal(
            "open_long" if chase_direction > 0 else "open_short", chase_direction,
            chase_reason, "ma_expansion_chase", pd.Timestamp(latest["date"]),
            float(_ma20(five_minute).iloc[-1]), float(_ma20(one_minute).iloc[-1]),
            chase_stop if chase_direction > 0 else trigger,
            trigger if chase_direction > 0 else chase_stop,
            trigger, pd.Timestamp(latest["date"]), atr_value,
        )
    ma5_continuation_short, ma5_continuation_reason, ma5_continuation_stop = downtrend_ma5_pullback_short_setup(
        five_minute, one_minute, fifteen_minute)
    ma5_continuation_long, ma5_continuation_long_reason, ma5_continuation_long_stop = uptrend_ma5_pullback_long_setup(
        five_minute, one_minute, fifteen_minute)
    continuation_short, continuation_reason, continuation_stop = downtrend_pullback_short_setup(
        five_minute, one_minute, fifteen_minute)
    continuation_long, continuation_long_reason, continuation_long_stop = uptrend_pullback_long_setup(
        five_minute, one_minute, fifteen_minute)
    if ma5_continuation_short or ma5_continuation_long or continuation_short or continuation_long:
        latest = one_minute.sort_values("date").iloc[-1]
        trigger = float(latest["close"])
        atr_value = float(_atr(five_minute.sort_values("date").reset_index(drop=True)).iloc[-1])
        direction = -1 if (ma5_continuation_short or continuation_short) else 1
        reason = (ma5_continuation_reason if ma5_continuation_short else
                  ma5_continuation_long_reason if ma5_continuation_long else
                  continuation_reason if continuation_short else continuation_long_reason)
        stop = (ma5_continuation_stop if ma5_continuation_short else
                ma5_continuation_long_stop if ma5_continuation_long else
                continuation_stop if continuation_short else continuation_long_stop)
        return Ma20RetestSignal(
            "open_short" if direction < 0 else "open_long", direction,
            reason, ("downtrend_ma5_pullback_short" if ma5_continuation_short else
                     "uptrend_ma5_pullback_long" if ma5_continuation_long else
                     "downtrend_continuation_short" if direction < 0 else "uptrend_continuation_long"),
            pd.Timestamp(latest["date"]), float(_ma20(five_minute).iloc[-1]),
            float(_ma20(one_minute).iloc[-1]),
            trigger if direction < 0 else stop,
            stop if direction < 0 else trigger,
            trigger, pd.Timestamp(latest["date"]), atr_value,
        )
    regular = evaluate_ma20_retest(five_minute, one_minute, fifteen_minute)
    if regular.action in {"open_long", "open_short"}:
        runway_ok, runway_reason, atr_value = trend_entry_runway(
            five_minute, regular.trigger_price, regular.direction)
        if not runway_ok:
            return Ma20RetestSignal(
                "observe", 0, f"{regular.reason}；{runway_reason}", regular.five_minute_state,
                regular.cross_time, regular.ma20_5m, regular.ma20_1m,
                regular.retest_low, regular.retest_high, regular.trigger_price,
                regular.trend_time, atr_value,
            )
        return Ma20RetestSignal(**{**regular.__dict__, "atr_5m": atr_value})
    markets = {"1m": one_minute, "5m": five_minute, "15m": fifteen_minute}
    signals = {bar: latest_timeframe_signal(frame, bar, fast_window=5, slow_window=10)
               for bar, frame in markets.items()}
    direction, reason, _ = breakout_pullback_long_setup(signals, markets)
    if direction != 1:
        return regular
    recent = one_minute.sort_values("date").tail(6)
    f5 = five_minute.sort_values("date").reset_index(drop=True)
    ma5 = _ma20(f5)
    ma1 = _ma20(one_minute.sort_values("date").reset_index(drop=True))
    latest = recent.iloc[-1]
    return Ma20RetestSignal(
        "open_long", 1, reason, "bullish_breakout_pullback", pd.Timestamp(latest["date"]),
        float(ma5.iloc[-1]), float(ma1.iloc[-1]), float(recent["low"].min()),
        float(recent["high"].max()), float(latest["close"]), pd.Timestamp(f5.iloc[-2]["date"]),
    )


def observe_ma20_retest_once(instrument: str = "ETH-USDT-SWAP") -> Ma20RetestSignal:
    return evaluate_ma20_with_breakout(
        okx_history_market((instrument,), "5m", 120),
        okx_history_market((instrument,), "1m", 120),
        okx_history_market((instrument,), "15m", 120),
        None,
        okx_history_market((instrument,), "1H", 120),
        okx_history_market((instrument,), "4H", 120),
    )
