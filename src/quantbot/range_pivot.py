from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

import pandas as pd

from .extreme_entries import (
    high_sweep_reject_short_setup,
    low_sweep_reclaim_long_setup,
    volume_stopping_pullback_long_setup,
)
from .trend_continuation import (downtrend_ma5_pullback_short_setup,
                                 downtrend_pullback_short_setup,
                                 uptrend_ma5_pullback_long_setup,
                                 uptrend_pullback_long_setup)
from .trend_regime import (bottom_color_reversal_long_setup,
                           candidate_reversal_setup,
                           direct_rollover_first_ma5_long_setup,
                           direct_rollover_first_ma5_short_setup,
                           direct_rollover_first_ma20_long_setup,
                           direct_rollover_first_ma20_short_setup,
                           top_color_reversal_short_setup)
from .ma_expansion_chase import dual_timeframe_ma_expansion_chase


STRATEGY_ID = "strategy_02"
STRATEGY_VERSION = "range_pivot_reversal_v59"
RELATIVE_EXTREME_BRANCH = "relative_extreme_reversal"
TREND_CONTINUATION_BRANCH = "trend_continuation"
MA_EXPANSION_CHASE_BRANCH = "ma_expansion_chase"


@dataclass(frozen=True)
class RangePivotSignal:
    direction: int
    action: str
    reason: str
    confirmed_bar_time: pd.Timestamp
    pivot_high: float
    pivot_low: float
    pivot_price: float
    atr: float
    adx: float
    market_state: str
    entry_confirmed: bool
    stop_price: float
    first_take_profit_price: float
    take_profit_price: float
    breakeven_stop_price: float
    reward_risk: float
    config_fingerprint: str
    extreme_exhaustion: bool = False
    trend_continuation: bool = False
    branch: str = RELATIVE_EXTREME_BRANCH


def _series(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values("date").reset_index(drop=True)


def true_range(frame: pd.DataFrame) -> pd.Series:
    f = _series(frame)
    previous = f["close"].shift(1)
    return pd.concat(((f["high"] - f["low"]), (f["high"] - previous).abs(), (f["low"] - previous).abs()), axis=1).max(axis=1)


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(frame).rolling(window).mean()


def adx(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    f = _series(frame)
    up = f["high"].diff()
    down = -f["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr_sum = true_range(f).rolling(window).sum()
    plus_di = 100 * plus_dm.rolling(window).sum() / tr_sum
    minus_di = 100 * minus_dm.rolling(window).sum() / tr_sum
    denominator = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / denominator
    return dx.rolling(window).mean().fillna(0.0)


def shifted_pivots(frame: pd.DataFrame, lookback: int) -> tuple[pd.Series, pd.Series]:
    """Prior confirmed range only; the current candle is deliberately excluded."""
    f = _series(frame)
    return f["high"].shift(1).rolling(lookback).max(), f["low"].shift(1).rolling(lookback).min()


def confirmed_wick_pivots(frame: pd.DataFrame, lookback: int) -> tuple[float, float]:
    """Return 5m wick extremes selected only from prior confirmed candles.

    Resistance is the high of the candle with the longest upper wick; support
    is the low of the candle with the longest lower wick.  Ties prefer the
    most recent candle.  The current row is excluded to prevent look-ahead.
    """
    if not 5 <= lookback <= 10:
        raise ValueError("lookback must be between 5 and 10")
    f = _series(frame)
    if len(f) < lookback + 1:
        raise ValueError("not enough confirmed pivot candles")
    window = f.iloc[-lookback - 1:-1].copy()
    upper = window["high"].astype(float) - window[["open", "close"]].astype(float).max(axis=1)
    lower = window[["open", "close"]].astype(float).min(axis=1) - window["low"].astype(float)
    resistance_index = upper[upper == upper.max()].index[-1]
    support_index = lower[lower == lower.max()].index[-1]
    return float(window.loc[resistance_index, "high"]), float(window.loc[support_index, "low"])


def confirmed_structure_extremes(frame: pd.DataFrame, lookback: int) -> tuple[float, float]:
    """Highest and lowest wick of the prior confirmed 5m structure."""
    f = _series(frame)
    if len(f) < lookback + 1:
        raise ValueError("not enough confirmed structure candles")
    window = f.iloc[-lookback - 1:-1]
    return float(window["high"].max()), float(window["low"].min())


def range_activity_allows(frame: pd.DataFrame, lookback: int, current_atr: float) -> tuple[bool, str]:
    """Reject compressed sideways ranges without a meaningful impulse candle."""
    f = _series(frame)
    if len(f) < lookback + 1 or current_atr <= 0:
        return False, "5分钟波动数据不足"
    window = f.iloc[-lookback - 1:-1]
    candle_ranges = window["high"].astype(float) - window["low"].astype(float)
    bodies = (window["close"].astype(float) - window["open"].astype(float)).abs()
    structure_span = float(window["high"].max() - window["low"].min())
    peak_range_ratio = float(candle_ranges.max() / current_atr)
    peak_body_ratio = float(bodies.max() / current_atr)
    span_ratio = structure_span / current_atr
    allowed = peak_range_ratio >= 1.50 and peak_body_ratio >= .60 and span_ratio >= 2.50
    if not allowed:
        return False, (f"5分钟窄幅横盘：峰值振幅{peak_range_ratio:.2f}ATR、"
                       f"峰值实体{peak_body_ratio:.2f}ATR、区间{span_ratio:.2f}ATR，禁止开仓")
    return True, "5分钟存在显著长实体/长影线波动"


def five_minute_reversal_zone_allows(resistance: float, support: float,
                                     current_atr: float) -> tuple[bool, str]:
    """Require a tradable 5m swing range before 1m may confirm an entry."""
    width = resistance - support
    width_atr = width / current_atr if current_atr > 0 else 0.0
    if width <= 0 or width_atr < 2.50:
        return False, (f"5分钟反转区间不足：高低区宽度{width:.2f}，仅{width_atr:.2f}ATR；"
                       "视为横盘震荡，不允许1分钟触发下单")
    return True, f"5分钟反转高低区有效：宽度{width_atr:.2f}ATR"


def five_minute_ma_deviation(frame: pd.DataFrame, resistance: float, support: float,
                             current_atr: float, minimum_atr: float = 1.50
                             ) -> tuple[bool, bool, float, float]:
    """Find early reversal zones far outside the closed-5m MA5/10/20 band."""
    f = _series(frame)
    confirmed = f.iloc[:-1]
    if len(confirmed) < 20 or current_atr <= 0:
        return False, False, 0.0, 0.0
    close = confirmed["close"].astype(float)
    ma_values = [float(close.rolling(window).mean().iloc[-1]) for window in (5, 10, 20)]
    upper_ma, lower_ma = max(ma_values), min(ma_values)
    high_distance_atr = max(0.0, resistance - upper_ma) / current_atr
    low_distance_atr = max(0.0, lower_ma - support) / current_atr
    return (high_distance_atr >= minimum_atr, low_distance_atr >= minimum_atr,
            high_distance_atr, low_distance_atr)


def one_minute_sweep_wick(candle: pd.Series, resistance: float, support: float,
                          one_minute_atr: float) -> tuple[bool, bool]:
    """Confirm a stop sweep by a meaningful 1m wick that closes back inside."""
    open_price, close = float(candle["open"]), float(candle["close"])
    high, low = float(candle["high"]), float(candle["low"])
    body = abs(close - open_price)
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    minimum_wick = max(body, one_minute_atr * .35)
    high_sweep = high >= resistance and close < resistance and upper_wick >= minimum_wick
    low_sweep = low <= support and close > support and lower_wick >= minimum_wick
    return high_sweep, low_sweep


def ma_cross_direction(frame: pd.DataFrame, fast: int = 5, slow: int = 10) -> int:
    f = _series(frame)
    close = f["close"].astype(float)
    fast_ma, slow_ma = close.rolling(fast).mean(), close.rolling(slow).mean()
    if len(f) < slow + 1:
        return 0
    if fast_ma.iloc[-2] <= slow_ma.iloc[-2] and fast_ma.iloc[-1] > slow_ma.iloc[-1]:
        return 1
    if fast_ma.iloc[-2] >= slow_ma.iloc[-2] and fast_ma.iloc[-1] < slow_ma.iloc[-1]:
        return -1
    return 0


def ma_alignment_direction(frame: pd.DataFrame, fast: int = 5, slow: int = 10) -> int:
    """Current confirmed MA direction; less sparse than an exact one-bar cross."""
    f = _series(frame)
    close = f["close"].astype(float)
    if len(f) < slow:
        return 0
    fast_value = float(close.rolling(fast).mean().iloc[-1])
    slow_value = float(close.rolling(slow).mean().iloc[-1])
    return 1 if fast_value > slow_value else -1 if fast_value < slow_value else 0


def local_post_impulse_range(frame: pd.DataFrame, current_atr: float) -> tuple[bool, float, float, str]:
    """Use the consolidation after a displacement candle, excluding its remote origin."""
    f = _series(frame)
    recent = f.iloc[-10:].reset_index(drop=True)
    bodies = (recent["close"].astype(float) - recent["open"].astype(float)).abs()
    candidates = [i for i, value in enumerate(bodies) if value >= current_atr * 1.20]
    if not candidates:
        return False, 0.0, 0.0, "没有近期位移K线"
    impulse = candidates[-1]
    after = recent.iloc[impulse + 1:]
    if len(after) < 3:
        return False, 0.0, 0.0, "位移后局部区间尚不足3根五分钟K线"
    support = float(recent.iloc[impulse:]["low"].astype(float).min())
    resistance = float(after["high"].astype(float).max())
    width_atr = (resistance - support) / current_atr if current_atr > 0 else 0.0
    if resistance <= support or width_atr < .80:
        return False, 0.0, 0.0, f"位移后局部区间仅{width_atr:.2f}ATR"
    return True, resistance, support, f"位移后局部区间{support:.2f}-{resistance:.2f}"


def staged_range_edge_trigger(
    entry_frame: pd.DataFrame, support: float, resistance: float, one_atr: float,
) -> tuple[int, str]:
    """Independent phase-2/phase-3 entries after a real range-edge touch."""
    one = _series(entry_frame)
    if len(one) < 24 or resistance <= support or one_atr <= 0:
        return 0, "局部边缘分阶段触发数据不足"
    close = one["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    current = one.iloc[-1]
    recent = one.iloc[-12:]
    bottom_touched = float(recent["low"].astype(float).min()) <= support + one_atr * .20
    top_touched = float(recent["high"].astype(float).max()) >= resistance - one_atr * .20
    bullish_ma5 = (
        bottom_touched
        and float(current["close"]) > float(current["open"])
        and float(current["close"]) > float(ma5.iloc[-1])
        and float(current["close"] - current["open"]) >= one_atr * .20
    )
    bearish_ma5 = (
        top_touched
        and float(current["close"]) < float(current["open"])
        and float(close.iloc[-2]) >= float(ma5.iloc[-2])
        and float(current["close"]) < float(ma5.iloc[-1])
        and float(current["open"] - current["close"]) >= one_atr * .20
    )
    reclaimed20 = bool((close.iloc[-7:-1] > ma20.iloc[-7:-1]).any())
    lost20 = bool((close.iloc[-7:-1] < ma20.iloc[-7:-1]).any())
    first_ma20_break_short = (
        top_touched
        and float(current["close"]) < float(current["open"])
        and float(close.iloc[-2]) >= float(ma20.iloc[-2])
        and float(current["close"]) < float(ma20.iloc[-1])
        and float(current["open"] - current["close"]) >= one_atr * .20
    )
    ma20_long = (
        bottom_touched and reclaimed20
        and float(current["low"]) <= float(ma20.iloc[-1]) + one_atr * .05
        and float(current["close"]) >= float(ma20.iloc[-1])
        and float(current["close"]) > float(current["open"])
    )
    ma20_short = (
        top_touched and lost20
        and float(current["high"]) >= float(ma20.iloc[-1]) - one_atr * .05
        and float(current["close"]) <= float(ma20.iloc[-1])
        and float(current["close"]) < float(current["open"])
    )
    # Priority: first closed bearish cross through MA5; MA20 is the fallback
    # only when that earlier opportunity was missed.  Older triggers remain.
    if bearish_ma5:
        return -1, "顶部压力第一优先触发：第一根1分钟阴线收盘向下穿透MA5，立即排队做空"
    if first_ma20_break_short:
        return -1, "顶部压力第二优先触发：错过MA5后，1分钟阴线首次收盘跌破MA20，立即排队做空"
    if ma20_long:
        return 1, "底部支撑未成交后的第三触发：1分钟已站上MA20并回踩交叉点企稳，排队做多"
    if ma20_short:
        return -1, "顶部压力未成交后的第三触发：1分钟已跌破MA20并反抽交叉点受阻，排队做空"
    if bullish_ma5:
        return 1, "底部支撑未成交后的第二触发：已收盘1分钟阳线站上MA5，排队做多"
    if bearish_ma5:
        return -1, "顶部压力未成交后的第二触发：已收盘1分钟阴线跌破MA5，排队做空"
    return 0, "等待支撑/压力后的MA5确认或MA20交叉点回踩"


def range_structure_stop(direction: int, support: float, resistance: float,
                         candle_low: float, candle_high: float, current_atr: float,
                         stop_atr_multiple: float = .75) -> float:
    """Place the stop beyond the interval edge and rejection wick plus ATR buffer."""
    buffer = current_atr * max(.10, stop_atr_multiple / 3)
    if direction > 0:
        return min(support, candle_low) - buffer
    if direction < 0:
        return max(resistance, candle_high) + buffer
    return 0.0


def exhaustion_top_short_setup(pivot_market: pd.DataFrame, entry_market: pd.DataFrame,
                               trend_market: pd.DataFrame) -> tuple[bool, str, float]:
    """Confirm an exceptional high-distance top before allowing a countertrend short.

    The higher timeframe only identifies an extreme candidate.  Entry still
    waits for a later closed 1m bearish candle to break the prior 1m low, so a
    rising market is never shorted merely because it looks expensive.
    """
    five, one, fifteen = _series(pivot_market), _series(entry_market), _series(trend_market)
    if len(five) < 21 or len(one) < 4 or len(fifteen) < 21:
        return False, "极端高位数据不足", 0.0

    candidates: list[tuple[str, pd.Series, float, float]] = []
    for label, frame in (("5分钟", five), ("15分钟", fifteen)):
        close = frame["close"].astype(float)
        current = frame.iloc[-1]
        current_atr = float(atr(frame).iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        if pd.isna(current_atr) or current_atr <= 0:
            continue
        high = float(current["high"])
        open_price, close_price = float(current["open"]), float(current["close"])
        candle_range = max(high - float(current["low"]), 1e-9)
        upper_wick = high - max(open_price, close_price)
        bearish_collapse = (
            close_price < open_price
            and (open_price - close_price) >= current_atr * .80
            and (close_price - float(current["low"])) / candle_range <= .35
        )
        wick_rejection = upper_wick >= current_atr * .60 and close_price < high - current_atr * .50
        distance_atr = (high - ma20) / current_atr
        if distance_atr >= 2.0 and (wick_rejection or bearish_collapse):
            candidates.append((label, current, current_atr, distance_atr))
    if not candidates:
        return False, "等待5分钟/15分钟最高价远离MA20至少2 ATR并形成冲高拒绝", 0.0

    recent = one.tail(4)
    latest, previous = recent.iloc[-1], recent.iloc[-2]
    one_atr = float(atr(one).tail(14).mean())
    latest_range = max(float(latest["high"] - latest["low"]), 1e-9)
    bearish_confirm = (
        float(latest["close"]) < float(latest["open"])
        and float(latest["open"] - latest["close"]) >= one_atr * .50
        and float(latest["close"]) < float(previous["low"])
        and float(latest["close"] - latest["low"]) / latest_range <= .35
    )
    if not bearish_confirm:
        return False, "已到极端高位，等待1分钟大阴线跌破前低并收在下方35%确认", 0.0

    label, _, higher_atr, distance_atr = max(candidates, key=lambda item: item[3])
    peak = max(float(recent["high"].max()), float(five.tail(2)["high"].max()))
    buffer = max(higher_atr * .10, float(latest["close"]) * .0003)
    return True, f"{label}最高价距MA20 {distance_atr:.2f} ATR且冲高拒绝；1分钟跌破前低确认摸顶做空", peak + buffer


def terminal_acceleration_short_setup(pivot_market: pd.DataFrame, entry_market: pd.DataFrame,
                                      trend_market: pd.DataFrame) -> tuple[bool, str, float]:
    """At least two expansions, MA20 reset, then the next stop-run fails on 1m."""
    five, one = _series(pivot_market), _series(entry_market)
    if len(five) < 30 or len(one) < 24:
        return False, "末端加速结构数据不足", 0.0
    close5 = five["close"].astype(float)
    ma20 = close5.rolling(20).mean()
    atr5 = atr(five)
    current_ma, current_atr = float(ma20.iloc[-1]), float(atr5.iloc[-1])
    if pd.isna(current_atr) or current_atr <= 0:
        return False, "末端加速ATR数据不足", 0.0

    # Confirm at least two earlier expansion peaks, excluding the latest three
    # 5m bars that can contain the live third acceleration.
    highs = five["high"].astype(float)
    peak_indexes = [i for i in range(max(20, len(five) - 18), len(five) - 3)
                    if highs.iloc[i] >= highs.iloc[i - 1] and highs.iloc[i] > highs.iloc[i + 1]]
    if len(peak_indexes) < 2:
        return False, "等待前两次上涨扩张波峰完成", 0.0
    first_two = peak_indexes[-2:]
    if first_two[1] - first_two[0] < 2:
        return False, "前两次扩张间隔不足", 0.0

    reset_window = five.iloc[first_two[1] + 1:].tail(6)
    reset_ma = ma20.loc[reset_window.index]
    reset_atr = atr5.loc[reset_window.index]
    reset = bool((reset_window["low"].astype(float) <= reset_ma + reset_atr * .35).any())
    if not reset:
        return False, "前两次扩张后尚未回到5分钟MA20附近复位", 0.0

    recent_one = one.tail(5)
    latest, previous = recent_one.iloc[-1], recent_one.iloc[-2]
    live_peak = float(recent_one["high"].max())
    prior_peak = max(float(highs.iloc[first_two[0]]), float(highs.iloc[first_two[1]]))
    one_atr = float(atr(one).tail(14).mean())
    one_volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    volume_baseline = float(one_volume.iloc[-21:-1].mean()) if len(one_volume) >= 21 else float(one_volume.mean())
    latest_range = max(float(latest["high"] - latest["low"]), 1e-9)
    third_acceleration = live_peak > prior_peak and (live_peak - current_ma) >= current_atr * 2.0
    bearish_failure = (
        float(latest["close"]) < float(latest["open"])
        and float(latest["open"] - latest["close"]) >= one_atr * .50
        and float(latest["close"]) < float(previous["low"])
        and float(latest["close"] - latest["low"]) / latest_range <= .35
        and float(one_volume.iloc[-1]) >= volume_baseline * 1.50
    )
    if not third_acceleration:
        return False, "MA20已复位，等待下一次末端加速扫过最近波峰并达到2 ATR乖离", 0.0
    if not bearish_failure:
        return False, "复位后的末端加速已出现，等待1分钟放量大阴线跌破前低确认失败", 0.0
    buffer = max(current_atr * .10, float(latest["close"]) * .0003)
    return True, "至少两次扩张后回到MA20复位；下一次末端加速扫高失败，1分钟放量跌破前低做空", live_peak + buffer


def terminal_acceleration_long_setup(pivot_market: pd.DataFrame, entry_market: pd.DataFrame,
                                     trend_market: pd.DataFrame) -> tuple[bool, str, float]:
    """Mirror: at least two declines, MA20 reset, then the next stop-run fails."""
    five, one = _series(pivot_market), _series(entry_market)
    if len(five) < 30 or len(one) < 24:
        return False, "末端下跌结构数据不足", 0.0
    close5 = five["close"].astype(float)
    ma20, atr5 = close5.rolling(20).mean(), atr(five)
    current_ma, current_atr = float(ma20.iloc[-1]), float(atr5.iloc[-1])
    if pd.isna(current_atr) or current_atr <= 0:
        return False, "末端下跌ATR数据不足", 0.0
    lows = five["low"].astype(float)
    trough_indexes = [i for i in range(max(20, len(five) - 18), len(five) - 3)
                      if lows.iloc[i] <= lows.iloc[i - 1] and lows.iloc[i] < lows.iloc[i + 1]]
    if len(trough_indexes) < 2:
        return False, "等待前两次下跌扩张低点完成", 0.0
    first_two = trough_indexes[-2:]
    if first_two[1] - first_two[0] < 2:
        return False, "前两次下跌扩张间隔不足", 0.0
    reset_window = five.iloc[first_two[1] + 1:].tail(6)
    reset = bool((reset_window["high"].astype(float)
                  >= ma20.loc[reset_window.index] - atr5.loc[reset_window.index] * .35).any())
    if not reset:
        return False, "前两次下跌扩张后尚未反抽到5分钟MA20附近复位", 0.0
    recent_one = one.tail(5)
    latest, previous = recent_one.iloc[-1], recent_one.iloc[-2]
    live_low = float(recent_one["low"].min())
    prior_low = min(float(lows.iloc[first_two[0]]), float(lows.iloc[first_two[1]]))
    one_atr = float(atr(one).tail(14).mean())
    one_volume = one["volume"].astype(float) if "volume" in one else pd.Series(1.0, index=one.index)
    volume_baseline = float(one_volume.iloc[-21:-1].mean()) if len(one_volume) >= 21 else float(one_volume.mean())
    latest_range = max(float(latest["high"] - latest["low"]), 1e-9)
    third_acceleration = live_low < prior_low and (current_ma - live_low) >= current_atr * 2.0
    bullish_failure = (
        float(latest["close"]) > float(latest["open"])
        and float(latest["close"] - latest["open"]) >= one_atr * .50
        and float(latest["close"]) > float(previous["high"])
        and float(latest["high"] - latest["close"]) / latest_range <= .35
        and float(one_volume.iloc[-1]) >= volume_baseline * 1.50
    )
    if not third_acceleration:
        return False, "MA20已复位，等待下一次末端加速扫过最近低点并达到2 ATR乖离", 0.0
    if not bullish_failure:
        return False, "复位后的末端下跌加速已出现，等待1分钟放量大阳线突破前高确认失败", 0.0
    buffer = max(current_atr * .10, float(latest["close"]) * .0003)
    return True, "至少两次下跌扩张后反抽MA20复位；下一次末端扫低失败，1分钟放量突破前高做多", live_low - buffer


def trend_environment(frame: pd.DataFrame, fast: int = 20, slow: int = 60, adx_window: int = 14) -> tuple[int, float, str]:
    f = _series(frame)
    close = f["close"].astype(float)
    fast_ma, slow_ma = close.rolling(fast).mean(), close.rolling(slow).mean()
    if len(f) < max(slow + 2, adx_window * 2):
        raise ValueError("not enough confirmed trend candles")
    rising = fast_ma.iloc[-1] > fast_ma.iloc[-2] and slow_ma.iloc[-1] > slow_ma.iloc[-2]
    falling = fast_ma.iloc[-1] < fast_ma.iloc[-2] and slow_ma.iloc[-1] < slow_ma.iloc[-2]
    direction = 1 if fast_ma.iloc[-1] > slow_ma.iloc[-1] and rising else -1 if fast_ma.iloc[-1] < slow_ma.iloc[-1] and falling else 0
    strength = float(adx(f, adx_window).iloc[-1])
    return direction, strength, "上涨趋势" if direction > 0 else "下跌趋势" if direction < 0 else "震荡"


def config_fingerprint(values: dict) -> str:
    raw = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def action_for_position(direction: int, long_contracts: float = 0, short_contracts: float = 0) -> tuple[str, str]:
    if direction > 0:
        if short_contracts > 0:
            return "close_short", "先平空；确认空仓和相关委托归零后才能开多"
        if long_contracts > 0:
            return "observe", "已有多仓，不重复开多"
        return "open_long", "低点反转条件成立"
    if direction < 0:
        if long_contracts > 0:
            return "close_long", "先平多；确认多仓和相关委托归零后才能开空"
        if short_contracts > 0:
            return "observe", "已有空仓，不重复开空"
        return "open_short", "高点反转条件成立"
    return "observe", "没有有效反转方向"


def reversal_safety_filter(
    direction: int, *, trend_direction: int, adx_value: float, max_adx: float,
    reward_risk: float, minimum_reward_risk: float, expected_profit_pct: float,
    round_trip_cost_pct: float, cost_multiple: float = 2,
) -> tuple[int, str]:
    if not direction:
        return 0, "没有有效反转方向"
    if adx_value > max_adx:
        return 0, f"ADX {adx_value:.1f} 高于 {max_adx:.1f}，禁止区间反转"
    if direction < 0 and trend_direction > 0:
        return 0, "15分钟处于强上涨趋势，禁止逆势开空"
    if direction > 0 and trend_direction < 0:
        return 0, "15分钟处于强下跌趋势，禁止逆势开多"
    if reward_risk < minimum_reward_risk or expected_profit_pct < round_trip_cost_pct * cost_multiple:
        return 0, "预期利润不足以覆盖最低盈亏比或往返交易成本"
    return direction, "安全过滤通过"


def execution_data_gate(*, market_fresh: bool, network_ok: bool, positions_known: bool, orders_known: bool) -> tuple[bool, str]:
    checks = ((network_ok, "网络查询失败"), (market_fresh, "行情已经过期"),
              (positions_known, "持仓查询失败"), (orders_known, "委托查询失败"))
    for passed, reason in checks:
        if not passed:
            return False, reason + "，安全拒绝开仓"
    return True, "账户与行情检查通过"


def long_short_order_mapping(action: str) -> dict[str, object]:
    mappings = {
        "open_long": {"side": "buy", "posSide": "long", "reduceOnly": False},
        "close_long": {"side": "sell", "posSide": "long", "reduceOnly": True},
        "open_short": {"side": "sell", "posSide": "short", "reduceOnly": False},
        "close_short": {"side": "buy", "posSide": "short", "reduceOnly": True},
    }
    if action not in mappings:
        raise ValueError("unsupported range pivot order action")
    return mappings[action]


def evaluate_range_pivot(
    pivot_market: pd.DataFrame,
    entry_market: pd.DataFrame,
    trend_market: pd.DataFrame,
    *,
    lookback: int = 8,
    touch_atr_multiple: float = .25,
    stop_atr_multiple: float = .75,
    minimum_reward_risk: float = 1.5,
    trend_fast_window: int = 20,
    trend_slow_window: int = 60,
    entry_fast_window: int = 5,
    entry_slow_window: int = 10,
    adx_window: int = 14,
    max_adx_for_reversal: float = 22,
    round_trip_cost_pct: float = 0,
    min_cost_edge_multiple: float = 2,
    long_contracts: float = 0,
    short_contracts: float = 0,
    frequency_test_mode: bool = False,
    thirty_minute_market: pd.DataFrame | None = None,
    one_hour_market: pd.DataFrame | None = None,
    four_hour_market: pd.DataFrame | None = None,
) -> RangePivotSignal:
    if not 5 <= lookback <= 10:
        raise ValueError("lookback must be between 5 and 10")
    p = _series(pivot_market)
    resistance, support = confirmed_wick_pivots(p, lookback)
    structure_high, structure_low = confirmed_structure_extremes(p, lookback)
    # The 5m frame defines the structural high/low zone.  The 1m frame only
    # confirms the precise rejection/entry and never redefines that zone.
    entry_frame = _series(entry_market)
    current = entry_frame.iloc[-1]
    current_atr = float(atr(p, adx_window).iloc[-1])
    if pd.isna(resistance) or pd.isna(support) or pd.isna(current_atr) or current_atr <= 0:
        raise ValueError("not enough confirmed pivot candles")
    local_range, local_resistance, local_support, local_reason = local_post_impulse_range(
        p, current_atr)
    if local_range:
        resistance, support = local_resistance, local_support
        structure_high, structure_low = resistance, support
    activity_ok, activity_reason = range_activity_allows(p, lookback, current_atr)
    trend, strength, market_state = trend_environment(trend_market, trend_fast_window, trend_slow_window, adx_window)
    cross = (ma_alignment_direction if frequency_test_mode else ma_cross_direction)(
        entry_market, entry_fast_window, entry_slow_window)
    body = abs(float(current["close"]) - float(current["open"]))
    upper_wick = float(current["high"]) - max(float(current["open"]), float(current["close"]))
    lower_wick = min(float(current["open"]), float(current["close"])) - float(current["low"])
    previous_close = float(entry_frame["close"].iloc[-2])
    high_touch = float(current["high"]) >= resistance - current_atr * touch_atr_multiple
    low_touch = float(current["low"]) <= support + current_atr * touch_atr_multiple
    high_reject = float(current["close"]) < resistance or upper_wick > body or float(current["close"]) < previous_close
    low_reject = float(current["close"]) > support or lower_wick > body or float(current["close"]) > previous_close
    range_width = resistance - support
    zone_ok, zone_reason = five_minute_reversal_zone_allows(resistance, support, current_atr)
    if local_range:
        zone_ok, zone_reason = True, local_reason
    far_high, far_low, high_ma_distance, low_ma_distance = five_minute_ma_deviation(
        p, resistance, support, current_atr)
    one_atr = float(atr(entry_frame, adx_window).iloc[-1])
    staged_direction, staged_reason = staged_range_edge_trigger(
        entry_frame, support, resistance, one_atr)
    high_sweep_wick, low_sweep_wick = one_minute_sweep_wick(
        current, resistance, support, one_atr)
    direction, reason = 0, "价格未形成合格的区间边缘反转"
    color_short, color_short_reason, color_short_stop = top_color_reversal_short_setup(
        p, entry_frame, trend_market, thirty_minute_market, one_hour_market, four_hour_market)
    color_long, color_long_reason, color_long_stop = bottom_color_reversal_long_setup(
        p, entry_frame, trend_market, thirty_minute_market, one_hour_market, four_hour_market)
    terminal_short, terminal_reason, terminal_stop = terminal_acceleration_short_setup(p, entry_frame, trend_market)
    terminal_long, terminal_long_reason, terminal_long_stop = terminal_acceleration_long_setup(p, entry_frame, trend_market)
    early_low_long, early_low_long_reason, early_low_long_stop = low_sweep_reclaim_long_setup(
        entry_frame, support, current_atr)
    early_high_short, early_high_short_reason, early_high_short_stop = high_sweep_reject_short_setup(
        entry_frame, resistance, current_atr)
    volume_long, volume_reason, volume_stop, volume_target = volume_stopping_pullback_long_setup(
        p, entry_frame)
    direct_ma5_short, direct_ma5_short_reason, direct_ma5_short_stop = direct_rollover_first_ma5_short_setup(
        p, entry_frame)
    direct_ma5_long, direct_ma5_long_reason, direct_ma5_long_stop = direct_rollover_first_ma5_long_setup(
        p, entry_frame)
    direct_short, direct_reason, direct_stop = direct_rollover_first_ma20_short_setup(
        p, entry_frame)
    direct_long, direct_long_reason, direct_long_stop = direct_rollover_first_ma20_long_setup(
        p, entry_frame)
    candidate_direction, candidate_reason, candidate_stop = candidate_reversal_setup(p, entry_frame, trend_market)
    ma5_continuation_short, ma5_continuation_reason, ma5_continuation_stop = downtrend_ma5_pullback_short_setup(
        p, entry_frame, trend_market)
    ma5_continuation_long, ma5_continuation_long_reason, ma5_continuation_long_stop = uptrend_ma5_pullback_long_setup(
        p, entry_frame, trend_market)
    continuation_short, continuation_reason, continuation_stop = downtrend_pullback_short_setup(
        p, entry_frame, trend_market)
    continuation_long, continuation_long_reason, continuation_long_stop = uptrend_pullback_long_setup(
        p, entry_frame, trend_market)
    chase_direction, chase_reason, chase_stop = dual_timeframe_ma_expansion_chase(p, entry_frame)
    exhaustion_short, exhaustion_reason, exhaustion_stop = exhaustion_top_short_setup(p, entry_frame, trend_market)
    if terminal_short:
        exhaustion_short, exhaustion_reason, exhaustion_stop = True, terminal_reason, terminal_stop
    if color_short:
        direction, reason = -1, color_short_reason
    elif color_long:
        direction, reason = 1, color_long_reason
    elif volume_long:
        direction, reason = 1, volume_reason
    elif far_low and low_sweep_wick:
        direction, reason = 1, (f"5分钟低区距MA5/MA10/MA20均线带{low_ma_distance:.2f}ATR，"
                                "1分钟触及后收回，提前做多；无需等待V形或均线突破")
    elif far_high and high_sweep_wick:
        direction, reason = -1, (f"5分钟高区距MA5/MA10/MA20均线带{high_ma_distance:.2f}ATR，"
                                 "1分钟触及后回落，提前做空；无需等待V形或均线突破")
    elif early_low_long:
        direction, reason = 1, early_low_long_reason
    elif early_high_short:
        direction, reason = -1, early_high_short_reason
    elif terminal_long:
        direction, reason = 1, terminal_long_reason
    elif exhaustion_short:
        direction, reason = -1, exhaustion_reason
    elif staged_direction:
        direction, reason = staged_direction, staged_reason
    elif direct_ma5_short:
        direction, reason = -1, direct_ma5_short_reason
    elif direct_ma5_long:
        direction, reason = 1, direct_ma5_long_reason
    elif direct_short:
        direction, reason = -1, direct_reason
    elif direct_long:
        direction, reason = 1, direct_long_reason
    elif candidate_direction < 0:
        direction, reason = candidate_direction, candidate_reason
    elif candidate_direction > 0:
        direction, reason = candidate_direction, candidate_reason
    elif chase_direction:
        direction, reason = chase_direction, chase_reason
    elif ma5_continuation_short:
        direction, reason = -1, ma5_continuation_reason
    elif ma5_continuation_long:
        direction, reason = 1, ma5_continuation_long_reason
    elif continuation_short:
        direction, reason = -1, continuation_reason
    elif continuation_long:
        direction, reason = 1, continuation_long_reason
    elif high_touch and high_reject and cross == -1:
        direction, reason = -1, "高点拒绝且1分钟MA5向下穿越MA10"
    elif low_touch and low_reject and cross == 1:
        direction, reason = 1, "低点拒绝且1分钟MA5向上穿越MA10"
    # Every reversal branch, including early/terminal candidates, must remain
    # inside the corresponding edge of the confirmed 5m range.  The 1m frame
    # only times the entry and can never create or bypass a 5m reversal zone.
    if direction and not (ma5_continuation_short or ma5_continuation_long or continuation_short or continuation_long or staged_direction or chase_direction):
        if not zone_ok:
            direction, reason = 0, zone_reason
    if direction and not activity_ok:
        direction, reason = 0, activity_reason
    pivot = support if direction > 0 else resistance if direction < 0 else 0.0
    # Structure stop: beyond both the interval edge and the rejection wick,
    # with a small ATR buffer. Never place a short stop below the upper wick
    # or a long stop above the lower wick.
    stop = range_structure_stop(
        direction, structure_low, structure_high, float(current["low"]), float(current["high"]),
        current_atr, stop_atr_multiple,
    )
    if color_short:
        stop = color_short_stop
    elif color_long:
        stop = color_long_stop
    elif volume_long:
        stop = volume_stop
    elif early_low_long:
        stop = early_low_long_stop
    elif early_high_short:
        stop = early_high_short_stop
    elif exhaustion_short:
        stop = exhaustion_stop
    elif terminal_long:
        stop = terminal_long_stop
    elif direct_ma5_short:
        stop = direct_ma5_short_stop
    elif direct_ma5_long:
        stop = direct_ma5_long_stop
    elif direct_short:
        stop = direct_stop
    elif direct_long:
        stop = direct_long_stop
    elif candidate_direction:
        stop = candidate_stop
    elif chase_direction:
        stop = chase_stop
    elif ma5_continuation_short:
        stop = ma5_continuation_stop
    elif ma5_continuation_long:
        stop = ma5_continuation_long_stop
    elif continuation_short:
        stop = continuation_stop
    elif continuation_long:
        stop = continuation_long_stop
    midpoint = (resistance + support) / 2
    entry = float(current["close"])
    risk = abs(entry - stop)
    reward = ((volume_target - entry) if volume_long and direction > 0 else
              midpoint - entry if direction > 0 else
              entry - midpoint if direction < 0 else 0.0)
    reward = max(0.0, reward)
    if chase_direction:
        reward = max(reward, risk * 1.5, entry * .003)
    rr = reward / risk if risk > 0 else 0.0
    expected_pct = reward / entry if entry > 0 else 0.0
    filtered_direction, filter_reason = reversal_safety_filter(
        direction, trend_direction=0 if (exhaustion_short or terminal_long or volume_long or early_low_long or early_high_short or continuation_short or continuation_long or chase_direction) else trend,
        adx_value=0.0 if (frequency_test_mode or chase_direction) else strength,
        max_adx=max_adx_for_reversal,
        reward_risk=rr,
        minimum_reward_risk=0.0 if frequency_test_mode else minimum_reward_risk,
        expected_profit_pct=expected_pct,
        round_trip_cost_pct=0.0 if frequency_test_mode else round_trip_cost_pct,
        cost_multiple=min_cost_edge_multiple,
    )
    if direction and not filtered_direction:
        reason = filter_reason
    direction = filtered_direction
    action, action_reason = action_for_position(direction, long_contracts, short_contracts)
    if direction:
        reason = f"{reason}；{action_reason}"
    first_tp = entry + direction * risk * minimum_reward_risk if direction else 0.0
    rr15_tp = entry + direction * risk * 1.5 if direction else 0.0
    if direction > 0:
        second_tp = min(midpoint, rr15_tp) if midpoint > entry else rr15_tp
    elif direction < 0:
        second_tp = max(midpoint, rr15_tp) if midpoint < entry else rr15_tp
    else:
        second_tp = 0.0
    if volume_long and direction > 0:
        first_tp = volume_target
        second_tp = volume_target
    params = {"lookback": lookback, "touch": touch_atr_multiple, "stop": stop_atr_multiple, "rr": minimum_reward_risk,
              "trend_fast": trend_fast_window, "trend_slow": trend_slow_window, "entry_fast": entry_fast_window,
              "entry_slow": entry_slow_window, "adx_window": adx_window, "max_adx": max_adx_for_reversal,
              "frequency_test_mode": frequency_test_mode,
              "activity_filter": "peak_range_1.5atr_peak_body_0.6atr_span_2.5atr",
              "stop_structure_lookback": lookback,
              "exhaustion_top": "ma20_distance_2atr_then_1m_breakdown",
              "early_low_long": "1m_stop_sweep_then_closed_bullish_reclaim_55pct",
              "volume_stopping_pullback_long": "5m_volume_stopping_then_1m_volume_v_reclaim",
              "early_high_short": "1m_high_sweep_then_closed_bearish_reject_55pct"}
    continuation_active = bool(chase_direction) or (
        (continuation_short and direction < 0) or (continuation_long and direction > 0)
    )
    branch = (MA_EXPANSION_CHASE_BRANCH if chase_direction else
              TREND_CONTINUATION_BRANCH if continuation_active else RELATIVE_EXTREME_BRANCH)
    return RangePivotSignal(direction, action, reason, pd.Timestamp(current["date"]), resistance, support, pivot,
                            current_atr, strength, market_state, cross == direction and direction != 0, stop,
                            first_tp, second_tp, entry if direction else 0.0, rr, config_fingerprint(params),
                            exhaustion_short or terminal_long or volume_long or early_low_long or early_high_short,
                            continuation_active, branch)
