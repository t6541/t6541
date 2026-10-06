"""The six user-approved account-05 entry identities (entry rules only)."""
from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from .account05_strategy import Trend15m


@dataclass(frozen=True)
class Account05Trigger:
    identity: str
    direction: int
    anchor_time: str
    reason: str


@dataclass(frozen=True)
class ExtremeRotationTrigger:
    direction: int
    anchor_time: str
    reason: str
    structure_key: str = ""


@dataclass(frozen=True)
class Account05Signals:
    trend_5m: Trend15m
    trend_reason: str
    triggers: tuple[Account05Trigger, ...]
    extreme_rotation: ExtremeRotationTrigger | None = None

    @property
    def trend_15m(self) -> Trend15m:
        """Compatibility alias; base sizing/TP now deliberately use 5m."""
        return self.trend_5m

    @property
    def reversal_direction(self) -> int:
        item = next((x for x in self.triggers if "底部" in x.identity or "顶部" in x.identity), None)
        return item.direction if item else 0

    @property
    def chase_direction(self) -> int:
        item = next((x for x in self.triggers if "追多" in x.identity or "追空" in x.identity), None)
        return item.direction if item else 0


def _frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _late_directional_entry_allowed(frame: pd.DataFrame, direction: int) -> bool:
    """Reject a late one-minute chase without rejecting a fresh reversal.

    A reversal may occur before the averages cross.  The veto is only for the
    opposite case: price is already outside MA5, MA5 has crossed MA10 against
    the entry, the last bars continue in one direction, and no new pullback
    into the MA band has formed.
    """
    data = _frame(frame)
    if len(data) < 12:
        return True
    close = data["close"].astype(float)
    open_ = data["open"].astype(float)
    high = data["high"].astype(float)
    low = data["low"].astype(float)
    ma5, ma10 = close.rolling(5).mean(), close.rolling(10).mean()
    price = float(close.iloc[-1])
    fast, slow = float(ma5.iloc[-1]), float(ma10.iloc[-1])
    if direction < 0:
        adverse_stack = fast < slow
        outside = price < fast
        run = bool((close.tail(3).diff().dropna() < 0).all())
        near_extreme = price <= float(low.tail(6).min()) + max(abs(price) * .0008, 1.20)
        band = max(fast, slow)
        fresh_rebound = bool((close.tail(4).diff().dropna() > 0).any()
                             and float(high.iloc[-3:-1].max()) >= band * .999)
    else:
        adverse_stack = fast > slow
        outside = price > fast
        run = bool((close.tail(3).diff().dropna() > 0).all())
        near_extreme = price >= float(high.tail(6).max()) - max(abs(price) * .0008, 1.20)
        band = min(fast, slow)
        fresh_rebound = bool((close.tail(4).diff().dropna() < 0).any()
                             and float(low.iloc[-3:-1].min()) <= band * 1.001)
    return not (adverse_stack and outside and run and near_extreme and not fresh_rebound)


def classify_fifteen_minute_trend(frame: pd.DataFrame) -> tuple[Trend15m, str]:
    data = _frame(frame)
    if len(data) < 30:
        return Trend15m.UNCLEAR, "K线不足30根"
    close = data["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
    atr = pd.concat([
        data["high"].astype(float) - data["low"].astype(float),
        (data["high"].astype(float) - close.shift(1)).abs(),
        (data["low"].astype(float) - close.shift(1)).abs(),
    ], axis=1).max(axis=1).rolling(14).mean().iloc[-1]
    if not pd.notna(atr) or float(atr) <= 0:
        return Trend15m.UNCLEAR, "ATR无效"
    slope = (float(ma20.iloc[-1]) - float(ma20.iloc[-4])) / float(atr)
    recent = data.tail(8)
    higher = (float(recent["high"].iloc[-4:].max()) > float(recent["high"].iloc[:4].max())
              and float(recent["low"].iloc[-4:].min()) > float(recent["low"].iloc[:4].min()))
    lower = (float(recent["high"].iloc[-4:].max()) < float(recent["high"].iloc[:4].max())
             and float(recent["low"].iloc[-4:].min()) < float(recent["low"].iloc[:4].min()))
    if ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] and slope >= .08 and higher:
        return Trend15m.UP, f"MA5>MA10>MA20、MA20上升、高低点抬高（{slope:.2f}ATR）"
    if ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] and slope <= -.08 and lower:
        return Trend15m.DOWN, f"MA5<MA10<MA20、MA20下降、高低点降低（{slope:.2f}ATR）"
    return Trend15m.UNCLEAR, f"均线、斜率、价格结构未同时确认（{slope:.2f}ATR）"


def _cover_45(five: pd.DataFrame, direction: int, *,
              include_running: bool = False) -> tuple[bool, float]:
    data = _frame(five)
    if len(data) < 3:
        return False, 0.0
    # Existing reversal confirmation uses closed candles. The independent
    # first-Supertrend-flip entry may use the running 5m body instead.
    # Skip intervening dojis to find the nearest meaningful opposite body.
    if not include_running:
        data = data.iloc[:-1]
    current = data.iloc[-1]
    body_sizes = (data["close"].astype(float) - data["open"].astype(float)).abs()
    typical_body = float(body_sizes.tail(14).median()) if len(body_sizes) else 0.0
    doji_limit = max(typical_body * 0.25, abs(float(current["close"])) * 0.00005)
    prior_index = len(data) - 2
    while prior_index >= 0 and float(body_sizes.iloc[prior_index]) <= doji_limit:
        prior_index -= 1
    if prior_index < 0:
        return False, 0.0
    prior = data.iloc[prior_index]
    if direction > 0:
        body = float(prior["open"] - prior["close"])
        overlap = max(0.0, min(float(prior["open"]), float(current["close"]))
                      - max(float(prior["close"]), float(current["open"])))
        colors = float(prior["close"]) < float(prior["open"]) and float(current["close"]) > float(current["open"])
    else:
        body = float(prior["close"] - prior["open"])
        overlap = max(0.0, min(float(prior["close"]), float(current["open"]))
                      - max(float(prior["open"]), float(current["close"])))
        colors = float(prior["close"]) > float(prior["open"]) and float(current["close"]) < float(current["open"])
    ratio = overlap / max(body, 1e-9)
    return bool(colors and ratio >= .45), ratio


def _supertrend_state(frame: pd.DataFrame, period: int = 14,
                      multiplier: float = 3.0,
                      include_running: bool = False) -> tuple[int, float, int, float]:
    """Return current/prior Supertrend state, normally from closed bars."""
    data = _frame(frame)
    if not include_running:
        data = data.iloc[:-1]
    data = data.reset_index(drop=True)
    if len(data) < period + 5:
        return 0, float("nan"), 0, float("nan")
    high, low = data["high"].astype(float), data["low"].astype(float)
    close = data["close"].astype(float)
    prev = close.shift(1)
    tr = pd.concat((high - low, (high - prev).abs(), (low - prev).abs()), axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    mid = (high + low) / 2.0
    upper, lower = mid + multiplier * atr, mid - multiplier * atr
    fu, fl = upper.copy(), lower.copy()
    trend = pd.Series(1, index=data.index, dtype="int64")
    for i in range(1, len(data)):
        fu.iloc[i] = upper.iloc[i] if upper.iloc[i] < fu.iloc[i-1] or close.iloc[i-1] > fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i] = lower.iloc[i] if lower.iloc[i] > fl.iloc[i-1] or close.iloc[i-1] < fl.iloc[i-1] else fl.iloc[i-1]
        trend.iloc[i] = (-1 if close.iloc[i] < fl.iloc[i-1] else 1) if trend.iloc[i-1] > 0 else (1 if close.iloc[i] > fu.iloc[i-1] else -1)
    line = fl.iloc[-1] if trend.iloc[-1] > 0 else fu.iloc[-1]
    prior_line = fl.iloc[-2] if trend.iloc[-2] > 0 else fu.iloc[-2]
    return int(trend.iloc[-1]), float(line), int(trend.iloc[-2]), float(prior_line)


def _bottom_supertrend_support_context(
        one: pd.DataFrame, five: pd.DataFrame) -> str | None:
    """Return a protected bottom context, not a generic bullish-trend state.

    The 1m trailing line follows an established rally upward.  It cannot by
    itself qualify a new long or suppress shorts.  Protection applies only at
    the first 1m bullish flip near a recent low, or when current price is near
    the confirmed 5m Supertrend support.
    """
    one, five = _frame(one), _frame(five)
    one_direction, one_support, prior_direction, _ = _supertrend_state(one)
    if one_direction <= 0 or not pd.notna(one_support) or len(one) < 20:
        return None
    close = float(one.iloc[-1]["close"])
    one_atr = float((one["high"].astype(float) - one["low"].astype(float))
                    .iloc[-15:-1].mean())
    if not pd.notna(one_atr) or one_atr <= 0:
        return None
    recent_bottom = float(one["low"].astype(float).tail(8).min())
    if (prior_direction < 0
            and close - recent_bottom <= max(1.5 * one_atr, .0016 * close)):
        return "fresh_bottom_flip"
    if len(five) < 25:
        return None
    five_direction, five_support, _, _ = _supertrend_state(five)
    five_atr = float((five["high"].astype(float) - five["low"].astype(float))
                     .iloc[-15:-1].mean())
    if five_direction <= 0 or not pd.notna(five_support) or not pd.notna(five_atr) or five_atr <= 0:
        return None
    five_band = max(.50 * five_atr, .0010 * close)
    return "five_minute_support" if abs(close - five_support) <= five_band else None


def _one_minute_supertrend_support_bias(one: pd.DataFrame,
                                        five: pd.DataFrame) -> bool:
    """Protect the actual bottom area without locking out mature-top shorts."""
    return _bottom_supertrend_support_context(one, five) is not None


def _supertrend_chop(frame: pd.DataFrame, lookback: int = 8) -> bool:
    """Detect a Supertrend whipsaw waiting zone from repeated flips."""
    data = _frame(frame).iloc[:-1].reset_index(drop=True)
    if len(data) < 25:
        return False
    high, low = data.high.astype(float), data.low.astype(float)
    close, prev = data.close.astype(float), data.close.astype(float).shift(1)
    tr = pd.concat((high - low, (high - prev).abs(), (low - prev).abs()), axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    mid = (high + low) / 2
    upper, lower = mid + 3 * atr, mid - 3 * atr
    trend = [1]
    fu, fl = float(upper.iloc[0]), float(lower.iloc[0])
    for i in range(1, len(data)):
        fu = float(upper.iloc[i]) if upper.iloc[i] < fu or close.iloc[i-1] > fu else fu
        fl = float(lower.iloc[i]) if lower.iloc[i] > fl or close.iloc[i-1] < fl else fl
        trend.append(-1 if trend[-1] > 0 and close.iloc[i] < fl else
                     1 if trend[-1] < 0 and close.iloc[i] > fu else trend[-1])
    flips = sum(a != b for a, b in zip(trend[-lookback-1:], trend[-lookback:]))
    return flips >= 3


def _order_block_zone(frame: pd.DataFrame, direction: int) -> tuple[float, float] | None:
    """Find the latest opposite body that launched a directional impulse."""
    data = _frame(frame).iloc[:-1].reset_index(drop=True)
    if len(data) < 12:
        return None
    for i in range(len(data) - 2, max(5, len(data) - 14), -1):
        row, nxt = data.iloc[i], data.iloc[i + 1]
        bullish = float(row.close) > float(row.open)
        impulse = float(nxt.close) - float(nxt.open)
        if direction > 0 and not bullish and impulse > 0:
            return float(row.close), float(row.open)
        if direction < 0 and bullish and impulse < 0:
            return float(row.open), float(row.close)
    return None


def _supertrend_support_early_long(one: pd.DataFrame, five: pd.DataFrame) -> tuple[bool, str]:
    """Independent 1m Supertrend support reclaim long.

    The base entry intentionally ignores the 5m candle.  A running 1m candle may
    dip below the newly formed support and trigger as soon as it recovers,
    including the first green turn.  The ``five`` argument remains for API
    compatibility with the signal evaluator and is not read here.
    """
    one = _frame(one)
    if len(one) < 25:
        return False, "超级趋势线K线不足"
    one_direction, support, _, _ = _supertrend_state(one)
    live_direction, live_support, live_previous, _ = _supertrend_state(
        one, include_running=True)
    live_flip = live_direction > 0 and live_previous < 0
    if live_flip and pd.notna(live_support):
        one_direction, support = live_direction, live_support
    if one_direction != 1 or not pd.notna(support):
        return False, "1分钟超级趋势底部支撑尚未形成"
    running = one.iloc[-1]
    close = float(running["close"])
    low = float(running["low"])
    high = float(running["high"])
    atr = float((one["high"].astype(float) - one["low"].astype(float)).iloc[-15:-1].mean())
    if not pd.notna(atr) or atr <= 0:
        return False, "一分钟波动数据不足"
    # A broad 0.25% band accepted the 04:26:56 fill at 2680.94 even though
    # the stepped support was near 2678.  Only the wick and the first small
    # recovery beside that line qualify for this independent entry.
    touch_distance = max(.50 * atr, .00045 * close)
    max_recovery = max(.60 * atr, .00035 * close)
    near_support = support - max(.20 * atr, .0001 * close) <= low <= support + touch_distance
    rebound = close - low >= max(.08 * atr, .00005 * close)
    just_recovering = close - low <= max_recovery
    # A recovery from below the line is the primary event.  A first live flip
    # may trigger while the wick is still just above the new support.
    reclaim = low <= support and close >= support
    if ((near_support and rebound and just_recovering and (reclaim or live_flip)
            and support < close <= support + touch_distance + max_recovery
            and high >= low)):
        location = "1分钟运行中首次翻多" if live_flip else "1分钟支撑下方回收"
        five_direction = _supertrend_state(five)[0] if len(_frame(five)) >= 25 else 0
        validation = "；5分钟超级趋势同步上涨，双周期验证" if five_direction > 0 else "；5分钟尚未翻多，先按1分钟支撑回踩试多"
        return True, (f"超级趋势支撑早触发追多：{location}；1分钟运行K线下端靠近{support:.2f}并开始回收；"
                      f"低点距支撑{low-support:.2f}，回收{close-low:.2f}；"
                      f"{validation}；不要求等待5分钟覆盖或MA5确认")
    return False, "尚未靠近超级趋势支撑并出现轻微回收"


def _ma20_order_block_pullback_long(one: pd.DataFrame, five: pd.DataFrame) -> tuple[bool, str]:
    """Live 1m rebound at a confirmed 5m MA20 and bullish demand overlap."""
    one, five = _frame(one), _frame(five)
    if len(one) < 25 or len(five) < 25:
        return False, "回踩K线不足"
    closed_five = five.iloc[:-1]
    ma20 = closed_five["close"].astype(float).rolling(20).mean()
    if not (float(closed_five.iloc[-1]["close"]) > float(ma20.iloc[-1])
            and float(closed_five.iloc[-2]["close"]) > float(ma20.iloc[-2])):
        return False, "5分钟尚未连续两根收盘站上MA20"
    if _supertrend_state(five)[0] != 1:
        return False, "5分钟超级趋势尚未处于上涨侧"
    block = _order_block_zone(five, 1)
    if block is None:
        return False, "5分钟上涨订单块未确认"
    zone_low, zone_high = sorted(block)
    five_atr = float((closed_five["high"].astype(float)
                      - closed_five["low"].astype(float)).tail(14).mean())
    one_atr = float((one["high"].astype(float)
                     - one["low"].astype(float)).iloc[-15:-1].mean())
    if five_atr <= 0 or one_atr <= 0:
        return False, "回踩波动数据不足"
    ma = float(ma20.iloc[-1])
    overlap_tolerance = max(.35 * five_atr, .0004 * ma)
    if ma < zone_low - overlap_tolerance or ma > zone_high + overlap_tolerance:
        return False, "5分钟MA20与上涨订单块未重合"
    five_low = float(five.iloc[-1]["low"])
    one_live = one.iloc[-1]
    low, close = float(one_live["low"]), float(one_live["close"])
    near = max(.35 * five_atr, .0004 * ma)
    five_retest = (five_low <= ma + near and five_low >= min(ma, zone_low) - near
                   and five_low <= zone_high + near)
    one_retest = (low <= ma + near and low >= min(ma, zone_low) - near
                  and low <= zone_high + near)
    recovering = (close > float(one_live["open"])
                  and close - low >= max(.08 * one_atr, .00005 * close)
                  and close <= max(ma, zone_high) + max(.75 * one_atr, .0006 * close))
    if five_retest and one_retest and recovering:
        return True, (f"5分钟MA20订单块回踩早触发追多：MA20={ma:.2f}，"
                      f"上涨订单块={zone_low:.2f}-{zone_high:.2f}；"
                      "1分钟运行K线近区止跌回收，不等待MA5上穿或5分钟45%覆盖")
    return False, "尚未在5分钟MA20与上涨订单块附近出现1分钟回收"


def _five_minute_top_rejection_early_short(
        one: pd.DataFrame, five: pd.DataFrame) -> tuple[bool, str]:
    """Short a fresh 5m top rejection while the 1m candle turns bearish."""
    one, five = _frame(one), _frame(five)
    if len(one) < 25 or len(five) < 25:
        return False, "高位拒绝K线不足"
    closed_five = five.iloc[:-1]
    recent = closed_five.tail(5)
    body = (recent["close"].astype(float) - recent["open"].astype(float)).abs()
    doji_limit = max(float(body.median()) * .25,
                     float(five.iloc[-1]["close"]) * .00005)
    meaningful_bulls = recent[(recent["close"].astype(float)
                               > recent["open"].astype(float)) & (body > doji_limit)]
    if meaningful_bulls.empty:
        return False, "近5根5分钟K线没有有效上涨实体"
    five_atr = float((closed_five["high"].astype(float)
                      - closed_five["low"].astype(float)).tail(14).mean())
    one_atr = float((one["high"].astype(float)
                     - one["low"].astype(float)).iloc[-15:-1].mean())
    if five_atr <= 0 or one_atr <= 0:
        return False, "高位拒绝波动数据不足"
    live_five = five.iloc[-1]
    prior_high = float(recent["high"].astype(float).max())
    high, close = float(live_five["high"]), float(live_five["close"])
    near_top = high >= prior_high - max(.35 * five_atr, .0004 * close)
    five_rejects = (close < float(live_five["open"])
                    and high - close >= max(.12 * five_atr, .00015 * close))
    live_one = one.iloc[-1]
    one_high, one_close = float(live_one["high"]), float(live_one["close"])
    one_peak = float(one["high"].astype(float).tail(8).max())
    one_turns = (one_close < float(live_one["open"])
                 and one_peak - one_close >= max(.20 * one_atr, .0001 * one_close)
                 and one_peak - one_close <= max(1.8 * one_atr, .001 * one_close)
                 and one_high >= one_peak - max(.50 * one_atr, .0003 * one_close))
    # Fast top reversal: while the 1m candle is still green, a high near the
    # live 5m red resistance followed by a small retreat is already enough.
    five_live_direction, five_resistance, _, _ = _supertrend_state(
        five, include_running=True)
    green_rejection = False
    if five_live_direction < 0 and pd.notna(five_resistance):
        one_atr = max(one_atr, 1e-9)
        one_gap = five_resistance - one_high
        green_rejection = (
            float(live_five["close"]) >= float(live_five["open"])
            and one_close >= float(live_one["open"])
            and -.25 * one_atr <= one_gap <= max(.85 * one_atr, .00045 * one_close)
            and one_high - one_close >= max(.06 * one_atr, .00004 * one_close)
            and one_high - one_close <= max(.85 * one_atr, .0005 * one_close))
    if near_top and five_rejects and one_turns:
        return True, ("5分钟局部高位运行中出现首段回落，1分钟高位即时转弱；"
                      "跳过夹在有效阳线之间的十字线，不等待5分钟收盘覆盖45%或MA5下穿")
    if green_rejection:
        return True, ("1分钟上涨K线运行至5分钟超级趋势红色压力线附近后轻微回收；"
                      "即时反抽追空，不等待阴线收盘或5分钟覆盖45%")
    return False, "5分钟高位拒绝与1分钟近顶转弱尚未同步"


def _five_minute_first_supertrend_short(
        one: pd.DataFrame, five: pd.DataFrame) -> tuple[bool, str]:
    """Independent first bearish 5m Supertrend switch, without body coverage."""
    one, five = _frame(one), _frame(five)
    if len(one) < 25 or len(five) < 25:
        return False, "超级趋势首次翻空K线不足"
    live_direction, line, previous_direction, prior_support = _supertrend_state(
        five, include_running=True)
    closed_direction, _, closed_prior, closed_prior_support = _supertrend_state(five)
    live_flip = live_direction < 0 and previous_direction > 0
    just_closed_flip = (live_direction < 0 and closed_direction < 0
                        and closed_prior > 0)
    if not (live_flip or just_closed_flip):
        return False, "5分钟超级趋势尚未首次从上涨侧翻为空头侧"
    if _supertrend_chop(five):
        return False, "超级趋势反复翻转，处于震荡等待区"
    live_one = one.iloc[-1]
    one_bearish = float(live_one["close"]) < float(live_one["open"])
    if not one_bearish:
        return False, "1分钟运行K线尚未转弱"
    close = float(live_one["close"])
    five_atr = float((five["high"].astype(float)
                      - five["low"].astype(float)).iloc[-15:-1].mean())
    boundary = prior_support if live_flip else closed_prior_support
    if not pd.notna(line) or not pd.notna(boundary) or not pd.notna(five_atr) or five_atr <= 0:
        return False, "5分钟超级趋势线或波动数据无效"
    if close >= boundary or boundary - close > max(.75 * five_atr, .001 * close):
        return False, "已远离5分钟翻空前支撑线，不追空"
    return True, (f"5分钟超级趋势首次翻空，压力线={line:.2f}；"
                  f"原支撑线={boundary:.2f}附近1分钟运行K线转弱，独立追空；"
                  "不等待5分钟阴线覆盖前阳线45%")


def _one_minute_first_supertrend_short(
        one: pd.DataFrame, five: pd.DataFrame) -> tuple[bool, str]:
    """Independent first bearish 1m Supertrend flip at its new resistance."""
    one, five = _frame(one), _frame(five)
    if len(one) < 25:
        return False, "1分钟超级趋势首次翻空K线不足"
    live_direction, live_line, previous_direction, _ = _supertrend_state(
        one, include_running=True)
    closed_direction, closed_line, closed_prior, _ = _supertrend_state(one)
    live_flip = live_direction < 0 and previous_direction > 0
    just_closed_flip = (live_direction < 0 and closed_direction < 0
                        and closed_prior > 0)
    if not (live_flip or just_closed_flip):
        return False, "1分钟超级趋势尚未首次翻空"
    if _supertrend_chop(one):
        return False, "1分钟超级趋势反复翻转，处于震荡等待区"
    live_one = one.iloc[-1]
    close = float(live_one["close"])
    resistance = live_line if live_flip else closed_line
    one_atr = float((one["high"].astype(float)
                     - one["low"].astype(float)).iloc[-15:-1].mean())
    if not pd.notna(resistance) or not pd.notna(one_atr) or one_atr <= 0:
        return False, "1分钟翻空压力线或波动数据无效"
    high = float(live_one["high"])
    line_gap = resistance - high
    retreat = high - close
    near_line = (-.25 * one_atr <= line_gap <= max(.85 * one_atr, .00045 * close))
    first_retreat = (.08 * one_atr <= retreat <= max(.85 * one_atr, .0005 * close))
    if close >= resistance or not near_line or not first_retreat:
        return False, "翻空首根尚未在超级趋势压力线附近出现初始回落"
    return True, (f"1分钟超级趋势首次翻空，运行K线高点距新压力线{line_gap:.2f}，"
                  f"已回落{retreat:.2f}；独立局部反转做空，不等5分钟45%覆盖或MA5确认")


def _one_minute_supertrend_resistance_retest_short(
        one: pd.DataFrame) -> tuple[bool, str]:
    """Short a fresh live 1m rebound rejection at bearish Supertrend resistance."""
    one = _frame(one)
    if len(one) < 25:
        return False, "1分钟超级趋势压力线K线不足"
    direction, resistance, _, _ = _supertrend_state(one, include_running=True)
    if direction >= 0 or not pd.notna(resistance):
        return False, "1分钟超级趋势仍在上涨侧或压力线无效"
    if _supertrend_chop(one):
        return False, "1分钟超级趋势反复翻转，处于震荡等待区"
    live = one.iloc[-1]
    close, high = (float(live["close"]),
                          float(live["high"]))
    atr = float((one["high"].astype(float) - one["low"].astype(float)).iloc[-15:-1].mean())
    if not pd.notna(atr) or atr <= 0:
        return False, "1分钟反抽波动数据无效"
    line_gap = resistance - high
    retreat = high - close
    near_pressure = (-.25 * atr <= line_gap <= max(.85 * atr, .00045 * close))
    early_rejection = (.08 * atr <= retreat <= max(.85 * atr, .0005 * close))
    if not near_pressure or not early_rejection or close >= resistance:
        return False, "1分钟反抽高点未靠近超级趋势红色压力线并开始回落"
    return True, (f"1分钟下跌趋势反抽追空：运行K线高点距超级趋势红色压力线{line_gap:.2f}，"
                  f"初始回落{retreat:.2f}；独立触发，不看5分钟覆盖或MA5")


def _one_minute_reversal(one: pd.DataFrame, direction: int) -> tuple[bool, bool, str]:
    data = _frame(one)
    if len(data) < 24:
        return False, False, "一分钟数据不足"
    close = data["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
    latest = data.iloc[-1]
    bullish = float(latest["close"]) > float(latest["open"])
    color_ok = bullish if direction > 0 else not bullish
    recent, older = data.tail(3), data.iloc[-15:-3]
    # The approved 3-vs-12 structure is body-only.  Wicks are deliberately
    # excluded so a single sweep cannot suppress the next valid body turn.
    recent_high = recent[["open", "close"]].astype(float).max(axis=1).max()
    recent_low = recent[["open", "close"]].astype(float).min(axis=1).min()
    older_high = older[["open", "close"]].astype(float).max(axis=1).max()
    older_low = older[["open", "close"]].astype(float).min(axis=1).min()
    body_range = pd.concat([
        data[["open", "close"]].astype(float).max(axis=1),
        data[["open", "close"]].astype(float).min(axis=1),
    ], axis=1)
    body_atr = float((body_range.max(axis=1) - body_range.min(axis=1)).tail(14).mean())
    # A near, lower second high (or higher second low) is a fresh local turn,
    # not a requirement to break the preceding body extreme again.
    tolerance = max(body_atr * .50, float(close.iloc[-1]) * .00035)
    extreme = ((recent_low <= older_low or (recent_low > older_low and recent_low - older_low <= tolerance))
               if direction > 0 else
               (recent_high >= older_high or (recent_high < older_high and older_high - recent_high <= tolerance)))
    inside = (float(latest["close"]) >= float(ma5.iloc[-1]) if direction > 0
              else float(latest["close"]) <= float(ma5.iloc[-1]))
    turning = (float(ma5.iloc[-1]) >= float(ma5.iloc[-2]) if direction > 0
               else float(ma5.iloc[-1]) <= float(ma5.iloc[-2]))
    cross = (float(ma5.iloc[-2]) <= float(ma10.iloc[-2]) and float(ma5.iloc[-1]) > float(ma10.iloc[-1])
             if direction > 0 else
             float(ma5.iloc[-2]) >= float(ma10.iloc[-2]) and float(ma5.iloc[-1]) < float(ma10.iloc[-1]))
    trigger = bool(extreme and color_ok and ((inside and turning) or cross))
    terminal = bool(trigger and ((ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1]) if direction > 0
                                 else (ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1])))
    stage = "进入MA5内侧且MA5转向" if inside and turning else "一分钟MA5/MA10小死叉补漏"
    return trigger, terminal, f"近3根实体极值相对前12根成立（不计上下影线，含邻近次高/次低）；首根反向K线；{stage}"


def _trend_chase(one: pd.DataFrame, five: pd.DataFrame, direction: int,
                 background: bool) -> tuple[bool, str]:
    if not background:
        return False, "上级趋势背景未确认"
    one, five = _frame(one), _frame(five)
    if len(one) < 24 or len(five) < 24:
        return False, "趋势追单K线不足"
    # Standard chase uses closed 1m bars. The live 5m wick exception below
    # still uses the latest closed 1m bar while the current 5m candle forms.
    one_live = one
    one = one.iloc[:-1].reset_index(drop=True)
    oc, fc = one["close"].astype(float), five["close"].astype(float)
    oma5 = oc.rolling(5).mean()
    fma5, fma10 = fc.rolling(5).mean(), fc.rolling(10).mean()
    latest, prior, recent = one.iloc[-1], one.iloc[-2], one.tail(3)
    five_latest, five_prior = five.iloc[-1], five.iloc[-2]
    recent_body_high = recent[["open", "close"]].astype(float).max(axis=1).max()
    recent_body_low = recent[["open", "close"]].astype(float).min(axis=1).min()
    twelve = one.tail(12)
    twelve_body_high = twelve[["open", "close"]].astype(float).max(axis=1)
    twelve_body_low = twelve[["open", "close"]].astype(float).min(axis=1)
    five_close = float(five_latest["close"])
    five_open = float(five_latest["open"])
    five_prior_close = float(five_prior["close"])
    band_low = min(float(fma5.iloc[-1]), float(fma10.iloc[-1])) * .9985
    band_high = max(float(fma5.iloc[-1]), float(fma10.iloc[-1])) * 1.0015
    if direction > 0:
        pulled = band_low <= five_close <= band_high
        five_stabilized = five_close > five_open and five_close >= five_prior_close
        local = recent_body_low <= float(twelve_body_low.quantile(.30))
        first_turn = (float(prior["close"]) <= float(prior["open"])
                      and float(latest["close"]) > float(latest["open"])
                      and float(latest["close"]) > float(prior["close"])
                      and float(latest["close"]) >=
                      (float(prior["open"]) + float(prior["close"])) / 2)
        turn = first_turn and float(latest["close"]) <= float(oma5.iloc[-1]) * 1.002
    else:
        pulled = band_low <= five_close <= band_high
        five_stabilized = five_close < five_open and five_close <= five_prior_close
        local = recent_body_high >= float(twelve_body_high.quantile(.70))
        first_turn = (float(prior["close"]) >= float(prior["open"])
                      and float(latest["close"]) < float(latest["open"])
                      and float(latest["close"]) < float(prior["close"])
                      and float(latest["close"]) <=
                      (float(prior["open"]) + float(prior["close"])) / 2)
        turn = first_turn and float(latest["close"]) >= float(oma5.iloc[-1]) * .998
    standard = bool(pulled and five_stabilized and local and turn)
    # Supertrend exchange-point trigger. The line is treated as a dynamic
    # support/resistance area: touch and reclaim supports a pullback long;
    # touch and reject permits an early throwback short without waiting for a
    # full 1m body cover or an MA5-side cross.
    st1, line1, prior_st1, prior_line1 = _supertrend_state(one_live)
    st5, line5, prior_st5, prior_line5 = _supertrend_state(five)
    # The caller decides which side is preferred inside a chop zone. In an
    # established uptrend, chop near the upper supply area favors shorts; in
    # a downtrend near the lower demand area it favors longs.
    closed_latest = one.iloc[-1]
    one_close = float(closed_latest["close"])
    one_low = float(closed_latest["low"])
    one_high = float(closed_latest["high"])
    one_ma20 = oc.rolling(20).mean()
    one_atr = float((one["high"].astype(float) - one["low"].astype(float)).tail(14).mean())
    tolerance = max(one_atr * .30, abs(one_close) * .00035)
    order_block = _order_block_zone(one_live, direction)
    order_block_touch = True
    if order_block is not None:
        zone_low, zone_high = sorted(order_block)
        order_block_touch = (one_low <= zone_high + tolerance and
                             one_high >= zone_low - tolerance)
    if direction > 0:
        touched_support = min(one_low, one_close) <= line1 + tolerance and one_close >= line1 - tolerance
        supertrend_long = bool(st1 > 0 and st5 > 0 and (prior_st1 > 0 or prior_st5 > 0)
                                and touched_support and order_block_touch and one_close > float(oma5.iloc[-1]))
        if supertrend_long:
            return True, "超级趋势线支撑换线点回踩追多：1分钟/5分钟处于上升侧，价格回踩支撑线后收回；MA20斜率不作硬过滤"
    else:
        touched_resistance = max(one_high, one_close) >= line1 - tolerance and one_close <= line1 + tolerance
        supertrend_short = bool(st1 < 0 and st5 < 0 and (prior_st1 < 0 or prior_st5 < 0)
                                 and touched_resistance and order_block_touch and one_close < float(oma5.iloc[-1]))
        if supertrend_short:
            return True, "超级趋势线压力换线点反抽追空：1分钟/5分钟处于下降侧，价格触及压力线后回落；无需等待1分钟覆盖或下穿MA5；MA20斜率不作硬过滤"
    if direction > 0:
        # Deep-end recovery: after a bearish waterfall, the first bullish
        # cover can be the only usable pullback entry.  Require both clocks to
        # reclaim their MA20 area and a real body reversal, so this does not
        # become a blind buy at every low tick.
        one_close = float(latest["close"])
        one_open = float(latest["open"])
        one_ma20 = float(oc.rolling(20).mean().iloc[-1])
        one_body = one_close - one_open
        one_range = float(latest["high"]) - float(latest["low"])
        one_atr = float((one["high"].astype(float) - one["low"].astype(float)).tail(14).mean())
        five_closed = five.iloc[:-1]
        if len(five_closed) >= 2:
            fbar = five_closed.iloc[-1]
            fma20 = float(five_closed["close"].astype(float).rolling(20).mean().iloc[-1])
            five_bullish = float(fbar["close"]) > float(fbar["open"])
            five_reclaims = float(fbar["close"]) > fma20
            cover_ok, cover_ratio = _cover_45(five, direction=1)
            deep_recovery = (
                five_bullish and five_reclaims and cover_ok
                and one_close > one_ma20 and one_body > 0
                and one_range >= max(one_atr * .65, abs(one_close) * .00025)
                # The first recovery candle often prints a slightly higher
                # low than the waterfall's absolute low. Treat the lower
                # part of the recent eight lows as the deep-end zone instead
                # of requiring an exact retest of the lowest tick.
                and float(latest["low"]) <= float(one["low"].tail(8).quantile(.75))
            )
            if deep_recovery:
                return True, (
                    "下跌末端深位反转追多：1分钟/5分钟收复MA20，"
                    f"5分钟阳线覆盖最近有效阴线{cover_ratio:.0%}（门槛45%）；"
                    "1分钟首根有效阳线确认，不等待更深回踩")
    if direction < 0:
        # A 5m candle may still be green before the interval rolls, although
        # its upper wick already shows rejection from the local high. Waiting
        # for the new candle exposes the entry to the next one or two 1m bars
        # and can make the normal distance gate reject it. Treat this as an
        # early throwback only when the wick is substantial and 1m has already
        # turned bearish.
        live_five = five.iloc[-1]
        five_range = float(live_five["high"]) - float(live_five["low"])
        upper_wick = float(live_five["high"]) - max(
            float(live_five["open"]), float(live_five["close"]))
        wick_rejection = (five_range > 0 and upper_wick / five_range >= .45
                          and float(live_five["close"]) < float(live_five["high"]))
        live_one = one_live.iloc[-2] if len(one_live) >= 2 else latest
        live_one_bearish = float(live_one["close"]) < float(live_one["open"])
        live_one_ma5 = one_live["close"].astype(float).rolling(5).mean()
        live_one_ma10 = one_live["close"].astype(float).rolling(10).mean()
        live_one_cross = (len(live_one_ma10) >= 2 and
                          live_one_ma5.iloc[-2] >= live_one_ma10.iloc[-2] and
                          live_one_ma5.iloc[-1] < live_one_ma10.iloc[-1])
        if wick_rejection and (live_one_bearish or live_one_cross):
            return True, (
                f"5分钟运行K线提前反抽追空：上影线占全幅{upper_wick / five_range:.0%}，"
                "高点回落；1分钟阴线/MA5小死叉已确认，不等待5分钟换线")
        # Account-01 compatible throwback entry: once the 5m bearish body
        # covers at least 45% of the nearest meaningful prior bullish body,
        # the first closed 1m bearish candle is enough. The 1m close does not
        # need to cross inside MA5 first; that requirement delays fast drops.
        cover_ok, cover_ratio = _cover_45(five, direction=-1)
        one_bearish = float(latest["close"]) < float(latest["open"])
        if cover_ok and one_bearish:
            return True, (
                f"账户01兼容反抽追空：1分钟已收盘阴线；"
                f"5分钟反向实体覆盖最近有效阳线{cover_ratio:.0%}（门槛45%）；"
                "不要求1分钟先进入MA5内侧")
    return standard, (
        "5分钟进入MA5/MA10带且已止跌/止涨；"
        "1分钟按实体局部极值并等待已收盘首根反向K线确认")


def _large_extreme(frame: pd.DataFrame, direction: int, *,
                   distance_atr: float, recent_bars: int,
                   include_running: bool = False) -> tuple[bool, str]:
    """Confirm an MA-stack terminal far from MA20."""
    data = _frame(frame)
    if len(data) < 35:
        return False, "K线不足35根"
    if not include_running:
        data = data.iloc[:-1].reset_index(drop=True)
    close = data["close"].astype(float)
    high, low = data["high"].astype(float), data["low"].astype(float)
    ma5, ma10, ma20 = (close.rolling(window).mean() for window in (5, 10, 20))
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    start = max(20, len(data) - recent_bars)
    candidates = (range(len(data) - 1, len(data)) if include_running
                  else range(start, len(data)))
    if direction < 0:
        index = max(candidates, key=lambda i: high.iloc[i])
        stack = ma5.iloc[index] > ma10.iloc[index] > ma20.iloc[index]
        reference_atr = atr.iloc[index - 1] if include_running else atr.iloc[index]
        distance = (high.iloc[index] - ma20.iloc[index]) / max(reference_atr, 1e-9)
        impulse = (high.iloc[index] - low.iloc[max(0, index - 4):index + 1].min()) / max(reference_atr, 1e-9)
    else:
        index = min(candidates, key=lambda i: low.iloc[i])
        stack = ma5.iloc[index] < ma10.iloc[index] < ma20.iloc[index]
        reference_atr = atr.iloc[index - 1] if include_running else atr.iloc[index]
        distance = (ma20.iloc[index] - low.iloc[index]) / max(reference_atr, 1e-9)
        impulse = (high.iloc[max(0, index - 4):index + 1].max() - low.iloc[index]) / max(reference_atr, 1e-9)
    ok = bool(stack and pd.notna(distance) and distance >= distance_atr
              and impulse >= 1.50)
    return ok, f"三均线发散末端；距MA20={distance:.2f}ATR；快速段={impulse:.2f}ATR"


def _extreme_rotation(one: pd.DataFrame, five: pd.DataFrame,
                      fifteen: pd.DataFrame) -> ExtremeRotationTrigger | None:
    one_data = _frame(one)
    if len(one_data) < 25:
        return None
    closed = one_data.iloc[:-1].reset_index(drop=True)
    close = closed["close"].astype(float)
    ma5 = close.rolling(5).mean()
    # The latest 1m row is deliberately kept as a running candle here.  An
    # extreme rotation is a fast terminal event, so waiting for its bearish
    # (or bullish) candle to close loses the price area being released.
    latest = one_data.iloc[-1]
    prior = one_data.iloc[-2]
    anchor = str(latest["date"])
    recent_high = float(one_data["high"].astype(float).tail(4).max())
    recent_low = float(one_data["low"].astype(float).tail(4).min())
    for direction in (-1, 1):
        five_ok, five_reason = _large_extreme(
            five, direction, distance_atr=1.00, recent_bars=6,
            include_running=True)
        if direction < 0:
            high = float(latest["high"])
            close = float(latest["close"])
            # Two fast paths are intentional: a tiny rejection from the
            # extreme, or an unbroken terminal surge.  Both are earlier than
            # waiting for a completed red 1m candle.
            rejection = high >= recent_high and close <= high * (1 - 0.00012)
            terminal_surge = (high >= recent_high and
                              high >= float(prior["high"]) * (1 + 0.00025))
            one_turn = bool(rejection or terminal_surge)
        else:
            low = float(latest["low"])
            close = float(latest["close"])
            rejection = low <= recent_low and close >= low * (1 + 0.00012)
            terminal_surge = (low <= recent_low and
                              low <= float(prior["low"]) * (1 - 0.00025))
            one_turn = bool(rejection or terminal_surge)
        if five_ok and one_turn:
            five_bar = _frame(five).iloc[-1]
            label = "大极值顶部反手空" if direction < 0 else "大极值底部反手多"
            return ExtremeRotationTrigger(
                direction, anchor,
                f"5分钟运行中{five_reason}；"
                f"1分钟极值运行中快速触发（轻微回落或末端继续冲高，不等待收盘）；{label}",
                str(five_bar["date"]))
    return None


def evaluate_account05_signals(one: pd.DataFrame, five: pd.DataFrame,
                               fifteen: pd.DataFrame, one_hour: pd.DataFrame | None = None) -> Account05Signals:
    trend15, _reason15 = classify_fifteen_minute_trend(fifteen)
    trend1, _reason1 = classify_fifteen_minute_trend(one)
    trend5, reason5 = classify_fifteen_minute_trend(five)
    trend1h = classify_fifteen_minute_trend(one_hour)[0] if one_hour is not None else Trend15m.UNCLEAR
    anchor = str(_frame(one).iloc[-1]["date"])
    triggers: list[Account05Trigger] = []
    bullish_support_bias = _one_minute_supertrend_support_bias(one, five)
    one_st, one_line, one_prior_st, _ = _supertrend_state(one)
    one_live_st, _, _, _ = _supertrend_state(one, include_running=True)
    # The 1m Supertrend owns this lock. The 5m line turns later and must not
    # delay a valid 1m top reversal. Keep evaluating pullback longs while the
    # 1m line remains bullish; reopen shorts as soon as the running 1m line
    # flips bearish.
    bullish_supertrend_lock = (one_st > 0 and one_live_st > 0)
    bearish_supertrend_lock = (one_st < 0 and one_live_st < 0)
    if bullish_support_bias:
        reason5 = (f"{reason5}；价格仍在1分钟超级趋势底部支撑保护带内，"
                   "暂缓空头触发，支撑回踩多头规则继续独立评估")
    elif bullish_supertrend_lock:
        reason5 = (f"{reason5}；1分钟超级趋势仍在上涨侧，"
                   "暂缓逆势摸顶空单，回踩追多继续评估")
    elif bearish_supertrend_lock:
        reason5 = (f"{reason5}；1分钟超级趋势仍在下跌侧，"
                   "暂缓逆势摸底多单，反抽追空继续评估")
    early_long, early_reason = _supertrend_support_early_long(one, five)
    if early_long:
        triggers.append(Account05Trigger("超级趋势支撑早触发追多", 1, anchor, early_reason))
    block_long, block_reason = _ma20_order_block_pullback_long(one, five)
    if block_long:
        triggers.append(Account05Trigger("5分钟MA20订单块回踩早触发追多", 1, anchor, block_reason))
    one_flip_short, one_flip_reason = _one_minute_first_supertrend_short(one, five)
    # Once the 1m Supertrend has flipped bullish and is holding its support,
    # defer countertrend shorts; the next valid pullback is the preferred long.
    if one_flip_short and not (bullish_support_bias or bullish_supertrend_lock):
        triggers.append(Account05Trigger("1分钟超级趋势首次翻空局部反转做空", -1, anchor, one_flip_reason))
    retest_short, retest_reason = _one_minute_supertrend_resistance_retest_short(one)
    if retest_short and not (bullish_support_bias or bullish_supertrend_lock):
        triggers.append(Account05Trigger("1分钟超级趋势压力线反抽追空", -1, anchor, retest_reason))
    for direction, local_name, true_name in (
        (1, "局部底部做多", "真正底部做多"), (-1, "局部顶部做空", "真正顶部做空")):
        # The 1m turn may complete one or two minutes before the running 5m
        # candle reaches 45% coverage.  Preserve that fresh body structure for
        # three 1m bars instead of requiring both clocks to flip together.
        one_data = _frame(one)
        one_ok, terminal, one_reason = False, False, ""
        candidate_anchor = anchor
        for lag in range(0, min(6, len(one_data) - 23)):
            candidate = one_data.iloc[:len(one_data) - lag]
            ok, candidate_terminal, candidate_reason = _one_minute_reversal(candidate, direction)
            if ok:
                one_ok, terminal, one_reason = ok, candidate_terminal, candidate_reason
                candidate_anchor = str(candidate.iloc[-1]["date"])
                break
        cover_ok, ratio = _cover_45(five, direction)
        candidate_frame = one_data[one_data["date"] <= pd.Timestamp(candidate_anchor)]
        if one_ok and cover_ok and _late_directional_entry_allowed(candidate_frame, direction):
            lagged = candidate_anchor != anchor
            if lagged:
                identity = "反转三阶段补漏做多" if direction > 0 else "反转三阶段补漏做空"
                timing = f"实际开仓锚点{anchor}；形态候选锚点{candidate_anchor}；"
            else:
                identity = true_name if terminal else local_name
                timing = f"实际开仓锚点{anchor}；"
            triggers.append(Account05Trigger(
                identity, direction, candidate_anchor,
                f"{timing}{one_reason}；反转三阶段一分钟候选保留最多6根；"
                f"5分钟相邻反向实体覆盖{ratio:.0%}（门槛45%）"))
    # Trend chase is a 5m execution setup with the 15m chart as the
    # environment filter.  The optional 1h feed is research context only;
    # it must not veto a valid 5m+15m setup.
    # The chase entry is intentionally owned by the execution timeframe pair:
    # 1m supplies the trigger and 5m supplies the direction.  15m arrows lag
    # fast turns and therefore remain review context only.
    chop = _supertrend_chop(one) or _supertrend_chop(five)
    up, up_reason = _trend_chase(
        one, five, 1, (trend1 is Trend15m.UP and trend5 is Trend15m.UP)
        or (chop and trend1 is Trend15m.DOWN and trend5 is Trend15m.DOWN))
    if up:
        triggers.append(Account05Trigger("上涨趋势回踩追多", 1, anchor, up_reason))
    if not (bullish_support_bias or bullish_supertrend_lock):
        down, down_reason = _trend_chase(
            one, five, -1, (trend1 is Trend15m.DOWN and trend5 is Trend15m.DOWN)
            or (chop and trend1 is Trend15m.UP and trend5 is Trend15m.UP))
        if down:
            triggers.append(Account05Trigger("下跌趋势反抽追空", -1, anchor, down_reason))
    extreme_rotation = _extreme_rotation(one, five, fifteen)
    return Account05Signals(
        trend5, reason5, tuple(triggers), extreme_rotation)
