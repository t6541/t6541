"""Five-minute primary trigger and higher-timeframe structure gate.

The 5m candle owns the entry authorization.  The 1m chart may describe
confirmation quality, but it can never turn a failed 5m gate into an order.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .close_structure import closing_price_structure
from .price_reversal import recent_ma_fan_endpoint
from .reversal_classification import five_minute_weakening_cover


@dataclass(frozen=True)
class PriceStructure:
    direction: int
    reason: str


@dataclass(frozen=True)
class PrimaryEntryGate:
    allowed: bool
    reason: str
    stop: float = 0.0
    candle_time: pd.Timestamp | None = None
    reference_price: float = 0.0
    trigger_code: str = ""


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous = close.shift(1)
    return pd.concat(((high - low), (high - previous).abs(), (low - previous).abs()), axis=1).max(axis=1).rolling(window).mean()


def _latest_local_extreme(data: pd.DataFrame, direction: int) -> float:
    """Return the latest shape-confirmed swing without a fixed-bar window."""
    values = data["high" if direction < 0 else "low"].astype(float).tolist()
    if direction < 0:
        pivots = [i for i in range(1, len(values) - 1)
                  if values[i] > values[i - 1] and values[i] >= values[i + 1]]
        return values[pivots[-1]] if pivots else max(values[-2:])
    pivots = [i for i in range(1, len(values) - 1)
              if values[i] < values[i - 1] and values[i] <= values[i + 1]]
    return values[pivots[-1]] if pivots else min(values[-2:])


def _recent_double_bottom(data: pd.DataFrame, atr: float) -> bool:
    """Recognize the latest two shape lows as a double bottom.

    The second low may be slightly higher or nearly equal.  It may undercut
    the first only within a small ATR tolerance, and the two lows must be
    separated by a visible rebound.
    """
    lows = data["low"].astype(float).tolist()
    highs = data["high"].astype(float).tolist()
    pivots = [i for i in range(1, len(lows) - 1)
              if lows[i] <= lows[i - 1] and lows[i] < lows[i + 1]]
    if len(pivots) < 2 or not pd.notna(atr) or atr <= 0:
        return False
    first, second = pivots[-2:]
    if second <= first + 1:
        return False
    first_low, second_low = lows[first], lows[second]
    comparable = abs(second_low - first_low) <= atr * .25 and second_low >= first_low - atr * .12
    rebound = max(highs[first + 1:second]) >= max(first_low, second_low) + atr * .30
    return comparable and rebound


def confirmed_price_structure(frame: pd.DataFrame, label: str) -> PriceStructure:
    """Use confirmed closing-price highs and lows as the trend definition."""
    structure = closing_price_structure(frame, label)
    return PriceStructure(structure.direction, structure.reason)


def _one_minute_ma_fan_endpoint(
    closed: pd.DataFrame, live: pd.DataFrame | None, direction: int,
) -> tuple[bool, str, float]:
    """Find a recent 1m MA5/10/20 fan end at a closing-price edge."""
    if live is None or live.empty or direction not in {-1, 1}:
        return False, "一分钟三均线末端等待实时K线", 0.0
    history = closed.sort_values("date").reset_index(drop=True)
    if len(history) < 24:
        return False, "一分钟三均线末端数据不足", 0.0
    current = live.sort_values("date").iloc[-1]
    combined = pd.concat([history, current.to_frame().T], ignore_index=True)
    atr = float(_atr(history).iloc[-1])
    if not pd.notna(atr) or atr <= 0:
        return False, "一分钟三均线末端ATR不足", 0.0
    current_confirms = (float(current["close"]) < float(current["open"]) if direction < 0
                        else float(current["close"]) > float(current["open"]))
    if not current_confirms:
        return False, f"一分钟尚未出现收盘价{'转弱' if direction < 0 else '转强'}苗头", 0.0
    endpoint = recent_ma_fan_endpoint(history, direction, lookback=8)
    if endpoint is None:
        return False, (
            "一分钟最近八根内没有合格均线发散末端：底部收盘须位于"
            "MA5和MA20下方，顶部收盘须位于MA5和MA20上方；MA10只记录不设硬门槛"
        ), 0.0
    opposite_endpoint = recent_ma_fan_endpoint(history, -direction, lookback=8)
    if (opposite_endpoint is not None
            and int(opposite_endpoint["index"]) > int(endpoint["index"])):
        return False, (
            "一分钟最近的三均线末端方向已经反转，旧的相反方向末端已释放"
        ), 0.0
    return True, (
        f"一分钟收盘位于MA5和MA20{'上方' if direction < 0 else '下方'}的发散末端"
        f"（MA5/MA20间距{endpoint['spread_atr']:.2f} ATR，MA10不作硬门槛）"
    ), float(endpoint["extreme"])


def _five_minute_endpoint_half_cover_trigger(
    closed: pd.DataFrame, live_five: pd.DataFrame | None,
    one_minute: pd.DataFrame, one_minute_live: pd.DataFrame | None, direction: int,
) -> tuple[bool, str, float, pd.Timestamp | None, float]:
    """Authorize a local reversal as soon as live 5m covers half the prior body."""
    if live_five is None or live_five.empty:
        return False, "三均线末端快触发等待实时五分钟K线", 0.0, None, 0.0
    five = closed.sort_values("date").reset_index(drop=True)
    if len(five) < 24:
        return False, "三均线末端快触发五分钟数据不足", 0.0, None, 0.0
    endpoint, endpoint_reason, endpoint_extreme = _one_minute_ma_fan_endpoint(
        one_minute, one_minute_live, direction,
    )
    if not endpoint:
        return False, endpoint_reason, 0.0, None, 0.0
    one_history = one_minute.sort_values("date").reset_index(drop=True)
    one_endpoint = recent_ma_fan_endpoint(one_history, direction, lookback=8)
    if one_endpoint is None:
        return False, endpoint_reason, 0.0, None, 0.0
    one_endpoint_index = int(one_endpoint["index"])
    one_close = one_history["close"].astype(float)
    one_ma5 = float(one_close.rolling(5).mean().iloc[one_endpoint_index])
    one_ma20 = float(one_close.rolling(20).mean().iloc[one_endpoint_index])
    one_stack_ok = (one_ma5 > one_ma20 if direction < 0 else one_ma5 < one_ma20)
    if not one_stack_ok:
        return False, (
            f"1m {'top' if direction < 0 else 'bottom'} identity rejected: "
            f"MA5 {one_ma5:.2f} must be "
            f"{'above' if direction < 0 else 'below'} MA20 {one_ma20:.2f}"
        ), 0.0, None, 0.0
    live = live_five.sort_values("date").iloc[-1]
    combined_five = pd.concat([five, live.to_frame().T], ignore_index=True)
    cover, anchor_index, cover_ratio = five_minute_weakening_cover(combined_five, direction)
    if not cover:
        return False, (
            f"{endpoint_reason}已记录；等待五分钟盘中一至三根反向K线实体累计形成约半覆盖"
        ), 0.0, None, 0.0
    five_close = combined_five["close"].astype(float)
    five_ma5 = five_close.rolling(5).mean()
    five_ma10 = five_close.rolling(10).mean()
    five_ma20 = five_close.rolling(20).mean()
    anchor = combined_five.iloc[int(anchor_index)]
    anchor_close = float(anchor["close"])
    anchor_ma5 = float(five_ma5.iloc[int(anchor_index)])
    anchor_ma10 = float(five_ma10.iloc[int(anchor_index)])
    anchor_ma20 = float(five_ma20.iloc[int(anchor_index)])
    if direction > 0:
        five_location_ok = anchor_close < min(anchor_ma5, anchor_ma10)
    else:
        five_location_ok = anchor_close > max(anchor_ma5, anchor_ma10)
    if not five_location_ok:
        return False, (
            f"5m {'bottom' if direction > 0 else 'top'} local reversal identity rejected: "
            f"anchor close {anchor_close:.2f}; MA5/MA10/MA20 "
            f"{anchor_ma5:.2f}/{anchor_ma10:.2f}/{anchor_ma20:.2f}; "
            + ("bottom requires anchor close below both MA5 and MA10; MA20 may run through the reversal"
               if direction > 0 else
               "top requires anchor close above both MA5 and MA10; MA20 may run through the reversal")
        ), 0.0, None, 0.0
    live_price = float(live["close"])
    atr = float(_atr(five).iloc[-1])
    buffer = max(atr * .15, live_price * .0003)
    stop = (max(endpoint_extreme, float(live["high"])) + buffer if direction < 0
            else min(endpoint_extreme, float(live["low"])) - buffer)
    side = "做空" if direction < 0 else "做多"
    return True, (
        f"三均线末端五分钟转弱/转强快{side}：{endpoint_reason}；随后一至三根反向K线"
        f"累计覆盖约{cover_ratio:.0%}；五分钟运行中达到约半覆盖立即算数，不等待收盘，"
        "也不要求五分钟自身末端锁定、周期配对、MA5或慢均线穿越；一分钟用局部小止损执行反转试单"
    ), stop, pd.Timestamp(live["date"]), live_price


def _endpoint_full_reversal_confirmation(
    five_minute: pd.DataFrame, five_minute_live: pd.DataFrame | None,
    one_minute: pd.DataFrame, one_minute_live: pd.DataFrame | None,
    direction: int,
) -> bool:
    """Separate a true 1m+5m reversal from a 5m half-cover local reversal."""
    if (direction not in {-1, 1} or five_minute_live is None or five_minute_live.empty
            or one_minute_live is None or one_minute_live.empty):
        return False
    five = five_minute.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(five) < 20 or len(one) < 5:
        return False
    live_five = five_minute_live.sort_values("date").iloc[-1]
    live_one = one_minute_live.sort_values("date").iloc[-1]
    five_close = pd.concat([
        five["close"].astype(float),
        pd.Series([float(live_five["close"])], dtype=float),
    ], ignore_index=True)
    one_close = one["close"].astype(float)
    previous_one_ma5 = float(one_close.iloc[-5:].mean())
    live_one_ma5 = float((one_close.iloc[-4:].sum() + float(live_one["close"])) / 5.0)
    one_crossed_ma5 = (
        direction * (float(one_close.iloc[-1]) - previous_one_ma5) <= 0
        and direction * (float(live_one["close"]) - live_one_ma5) > 0
    )
    five_ma20 = float(five_close.iloc[-20:].mean())
    five_crossed_ma20 = direction * (float(live_five["close"]) - five_ma20) > 0
    return bool(one_crossed_ma5 and five_crossed_ma20)


def _five_minute_trigger(frame: pd.DataFrame, direction: int) -> tuple[bool, str, float]:
    data = frame.sort_values("date").reset_index(drop=True)
    if len(data) < 24 or direction not in {-1, 1}:
        return False, "五分钟主触发数据不足", 0.0
    close = data["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    atr = float(_atr(data).iloc[-1])
    if not pd.notna(atr) or atr <= 0:
        return False, "五分钟主触发ATR不足", 0.0
    latest, prior = data.iloc[-1], data.iloc[-2]
    body = float(latest["close"] - latest["open"])
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr
    local_extreme = _latest_local_extreme(data.iloc[:-1], direction)
    buffer = max(atr * .15, float(latest["close"]) * .0003)
    if direction < 0:
        at_structure = float(latest["high"]) >= local_extreme - atr * .20
        lower_high = float(latest["high"]) <= float(prior["high"]) + atr * .10
        broke_prior = float(latest["close"]) < float(prior["low"])
        crossed_ma5 = float(prior["close"]) >= float(ma5.iloc[-2]) and float(latest["close"]) < float(ma5.iloc[-1])
        failed_ma20 = float(latest["high"]) >= float(ma20.iloc[-1]) - atr * .15 and float(latest["close"]) < float(ma20.iloc[-1])
        if ma20_slope > .12:
            return False, f"五分钟MA20快速上升 {ma20_slope:.3f} ATR，拒绝做空", 0.0
        if not (body <= -atr * .15 and lower_high and (at_structure or failed_ma20)
                and (broke_prior or crossed_ma5 or failed_ma20)):
            return False, "等待已收盘五分钟局部顶部/反抽高点转弱，不接受一分钟独立做空", 0.0
        stop = max(local_extreme, float(latest["high"])) + buffer
        return True, f"五分钟主触发做空成立｜局部顶部/反抽高点转弱｜MA20斜率{ma20_slope:.3f} ATR", stop
    at_structure = float(latest["low"]) <= local_extreme + atr * .20
    higher_low = float(latest["low"]) >= float(prior["low"]) - atr * .10
    broke_prior = float(latest["close"]) > float(prior["high"])
    crossed_ma5 = float(prior["close"]) <= float(ma5.iloc[-2]) and float(latest["close"]) > float(ma5.iloc[-1])
    held_ma20 = float(latest["low"]) <= float(ma20.iloc[-1]) + atr * .15 and float(latest["close"]) > float(ma20.iloc[-1])
    if ma20_slope < -.12:
        return False, f"五分钟MA20快速下降 {ma20_slope:.3f} ATR，拒绝做多", 0.0
    if not (body >= atr * .15 and higher_low and (at_structure or held_ma20)
            and (broke_prior or crossed_ma5 or held_ma20)):
        return False, "等待已收盘五分钟局部底部/回踩低点转强，不接受一分钟独立做多", 0.0
    stop = min(local_extreme, float(latest["low"])) - buffer
    return True, f"五分钟主触发做多成立｜局部底部/回踩低点转强｜MA20斜率{ma20_slope:.3f} ATR", stop


def _five_minute_intrabar_trigger(
    closed: pd.DataFrame, live_five: pd.DataFrame | None,
    one_minute_live: pd.DataFrame | None, direction: int,
) -> tuple[bool, str, float, pd.Timestamp | None, float]:
    """Allow an established 5m top/bottom to trigger during the live candle."""
    if live_five is None or live_five.empty or one_minute_live is None or one_minute_live.empty:
        return False, "五分钟盘中快触发等待实时五分钟和一分钟K线", 0.0, None, 0.0
    data = closed.sort_values("date").reset_index(drop=True)
    if len(data) < 24 or direction not in {-1, 1}:
        return False, "五分钟盘中快触发数据不足", 0.0, None, 0.0
    live = live_five.sort_values("date").iloc[-1]
    one = one_minute_live.sort_values("date").iloc[-1]
    close = data["close"].astype(float)
    atr = float(_atr(data).iloc[-1])
    ma20 = close.rolling(20).mean()
    if not pd.notna(atr) or atr <= 0:
        return False, "五分钟盘中快触发ATR不足", 0.0, None, 0.0
    live_price = float(live["close"])
    live_ma5 = float((close.iloc[-4:].sum() + live_price) / 5.0)
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr
    local_extreme = _latest_local_extreme(data, direction)
    setup = data.iloc[-4:]
    buffer = max(atr * .15, live_price * .0003)
    if direction < 0:
        bulls = setup[setup["close"].astype(float) > setup["open"].astype(float)]
        if bulls.empty:
            return False, "五分钟盘中快空等待顶部前置阳线", 0.0, None, 0.0
        bull = bulls.iloc[-1]
        midpoint = (float(bull["open"]) + float(bull["close"])) / 2.0
        at_top = float(setup["high"].max()) >= local_extreme - atr * .15
        weakening = sum(
            1 for _, row in setup.iterrows()
            if float(row["close"]) <= float(row["open"])
            or abs(float(row["close"] - row["open"])) <= atr * .10
        ) >= 2
        crossed_ma5 = float(live["high"]) >= live_ma5 and live_price <= live_ma5 - atr * .05
        covered_bull = live_price <= midpoint
        one_confirms = float(one["close"]) < float(one["open"]) and float(one["close"]) <= live_ma5
        if ma20_slope > .18:
            return False, f"五分钟盘中快空拒绝：MA20仍快速上升 {ma20_slope:.3f} ATR", 0.0, None, 0.0
        if not (at_top and weakening and crossed_ma5 and covered_bull and one_confirms):
            return False, "等待五分钟顶部走弱后，当前阴线盘中覆盖前阳线并下穿MA5，由一分钟同步确认", 0.0, None, 0.0
        stop = max(local_extreme, float(live["high"])) + buffer
        return True, (
            f"五分钟盘中快空：顶部走弱证据已建立，当前未收盘阴线已覆盖前阳线一半并下穿MA5 "
            f"{live_ma5:.2f}，一分钟同步转弱；不等待五分钟收盘"
        ), stop, pd.Timestamp(live["date"]), live_price
    bears = setup[setup["close"].astype(float) < setup["open"].astype(float)]
    if bears.empty:
        return False, "五分钟盘中快多等待底部前置阴线", 0.0, None, 0.0
    bear = bears.iloc[-1]
    midpoint = (float(bear["open"]) + float(bear["close"])) / 2.0
    at_bottom = float(setup["low"].min()) <= local_extreme + atr * .15
    weakening = sum(
        1 for _, row in setup.iterrows()
        if float(row["close"]) >= float(row["open"])
        or abs(float(row["close"] - row["open"])) <= atr * .10
    ) >= 2
    crossed_ma5 = float(live["low"]) <= live_ma5 and live_price >= live_ma5 + atr * .05
    covered_bear = live_price >= midpoint
    one_confirms = float(one["close"]) > float(one["open"]) and float(one["close"]) >= live_ma5
    if ma20_slope < -.18:
        return False, f"五分钟盘中快多拒绝：MA20仍快速下降 {ma20_slope:.3f} ATR", 0.0, None, 0.0
    if not (at_bottom and weakening and crossed_ma5 and covered_bear and one_confirms):
        return False, "等待五分钟底部走强后，当前阳线盘中覆盖前阴线并上穿MA5，由一分钟同步确认", 0.0, None, 0.0
    stop = min(local_extreme, float(live["low"])) - buffer
    return True, (
        f"五分钟盘中快多：底部走强证据已建立，当前未收盘阳线已覆盖前阴线一半并上穿MA5 "
        f"{live_ma5:.2f}，一分钟同步转强；不等待五分钟收盘"
    ), stop, pd.Timestamp(live["date"]), live_price


def _five_minute_higher_low_ma5_long_trigger(
    closed: pd.DataFrame, live_five: pd.DataFrame | None,
    one_minute: pd.DataFrame, one_minute_live: pd.DataFrame | None, direction: int,
) -> tuple[bool, str, float, pd.Timestamp | None, float]:
    """Early long: rising 5m lows plus sustained 1m strength above MA20 and live MA5 reclaim."""
    if direction != 1 or live_five is None or live_five.empty or one_minute_live is None or one_minute_live.empty:
        return False, "五分钟抬高低点快多等待实时五分钟和一分钟K线", 0.0, None, 0.0
    five = closed.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(five) < 24 or len(one) < 24:
        return False, "五分钟抬高低点快多数据不足", 0.0, None, 0.0
    live = live_five.sort_values("date").iloc[-1]
    live_one = one_minute_live.sort_values("date").iloc[-1]
    atr5 = float(_atr(five).iloc[-1])
    atr1 = float(_atr(one).iloc[-1])
    if not pd.notna(atr5) or atr5 <= 0 or not pd.notna(atr1) or atr1 <= 0:
        return False, "五分钟抬高低点快多ATR不足", 0.0, None, 0.0
    five_lows = five["low"].astype(float).tolist()
    pivot_lows = [five_lows[i] for i in range(max(1, len(five_lows) - 14), len(five_lows) - 1)
                  if five_lows[i] <= five_lows[i - 1] and five_lows[i] < five_lows[i + 1]]
    higher_five_lows = len(pivot_lows) >= 2 and pivot_lows[-1] >= pivot_lows[-2] + atr5 * .05
    five_double_bottom = _recent_double_bottom(five, atr5)
    constructive_five_bottom = higher_five_lows or five_double_bottom
    live_price = float(live["close"])
    live_ma5 = float((five["close"].astype(float).iloc[-4:].sum() + live_price) / 5.0)
    live_crossed_ma5 = (float(live["close"]) > float(live["open"])
                        and float(live["low"]) <= live_ma5
                        and live_price >= live_ma5 + atr5 * .05)

    one_close = one["close"].astype(float)
    one_ma20 = one_close.rolling(20).mean()
    one_ma20_slope = float(one_ma20.iloc[-1] - one_ma20.iloc[-4]) / atr1
    one_lows = one["low"].astype(float).tolist()
    one_pivots = [(i, one_lows[i]) for i in range(max(20, len(one) - 16), len(one) - 1)
                  if one_lows[i] <= one_lows[i - 1] and one_lows[i] < one_lows[i + 1]
                  and float(one.iloc[i]["close"]) >= float(one_ma20.iloc[i]) - atr1 * .12]
    one_double_bottom = _recent_double_bottom(one, atr1)
    one_retest = len(one_pivots) >= 1 or one_double_bottom
    sustained_above_ma20 = (
        bool((one_close.iloc[-2:] >= one_ma20.iloc[-2:]).all())
        and float(live_one["close"]) >= float(one_ma20.iloc[-1])
        and one_ma20_slope >= -.08
    )
    one_turns_up = (float(live_one["close"]) > float(live_one["open"])
                    and float(live_one["close"]) > float(one.iloc[-1]["close"]))
    if not (constructive_five_bottom and live_crossed_ma5 and sustained_above_ma20 and one_turns_up):
        return False, "等待五分钟低点抬高或形成双底并盘中上穿MA5，同时一分钟突破MA20后持续在其上方运行并转强", 0.0, None, 0.0
    buffer = max(atr5 * .15, live_price * .0003)
    one_structure_low = (one_pivots[-1][1] if one_pivots
                         else _latest_local_extreme(one, 1) if one_double_bottom
                         else float(one.iloc[-5:]["low"].min()))
    stop = min(pivot_lows[-1], one_structure_low, float(live["low"]), float(live_one["low"])) - buffer
    return True, (
        f"五分钟{'双底' if five_double_bottom else '抬高低点'}MA5快多独立主触发：当前未收盘阳线有效上穿动态MA5 {live_ma5:.2f}；"
        f"一分钟突破MA20后连续运行在其上方并重新转强{'，且已有双底/回踩守住加分' if one_retest else '，无需等待回踩MA20'}；"
        "不等待五分钟收盘"
    ), stop, pd.Timestamp(live["date"]), live_price


def _five_minute_closed_ma5_one_minute_ma20_break_long_trigger(
    closed: pd.DataFrame, one_minute: pd.DataFrame,
    one_minute_live: pd.DataFrame | None, direction: int,
) -> tuple[bool, str, float, pd.Timestamp | None, float]:
    """Buy the first MA20 break after a closed 5m MA5 bottom reversal.

    This is earlier than the expansion chase: no MA5/MA10/MA20 fan and no
    MA20 retest is required.  The closed five-minute candle owns the trigger;
    the live one-minute MA20 break confirms timing.
    """
    if direction != 1 or one_minute_live is None or one_minute_live.empty:
        return False, "底部MA20突破快多等待五分钟收盘和实时一分钟K线", 0.0, None, 0.0
    five = closed.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(five) < 24 or len(one) < 24:
        return False, "底部MA20突破快多数据不足", 0.0, None, 0.0
    live_one = one_minute_live.sort_values("date").iloc[-1]
    five_close = five["close"].astype(float)
    five_ma5 = five_close.rolling(5).mean()
    atr5 = float(_atr(five).iloc[-1])
    atr1 = float(_atr(one).iloc[-1])
    if not all(pd.notna(value) and value > 0 for value in (atr5, atr1)):
        return False, "底部MA20突破快多ATR不足", 0.0, None, 0.0
    latest = five.iloc[-1]
    five_ma5_reclaim = (
        float(latest["close"]) > float(latest["open"])
        and min(float(latest["open"]), float(latest["low"])) <= float(five_ma5.iloc[-1]) + atr5 * .05
        and float(latest["close"]) >= float(five_ma5.iloc[-1]) + atr5 * .03
    )
    one_close = one["close"].astype(float)
    one_ma20 = one_close.rolling(20).mean()
    live_price = float(live_one["close"])
    live_ma20 = float((one_close.iloc[-19:].sum() + live_price) / 20.0)
    just_broke_ma20 = (
        float(one_close.iloc[-1]) <= float(one_ma20.iloc[-1]) + atr1 * .05
        and float(live_one["open"]) <= live_ma20 + atr1 * .05
        and live_price >= live_ma20 + atr1 * .05
        and live_price > float(live_one["open"])
    )
    not_extended = live_price - live_ma20 <= atr1 * .45
    if not (five_ma5_reclaim and just_broke_ma20 and not_extended):
        return False, (
            "等待已收盘五分钟底部阳线站上MA5，并由当前一分钟首次有效突破MA20；"
            "不等待回踩或均线发散"
        ), 0.0, None, 0.0
    buffer = max(atr5 * .15, float(latest["close"]) * .0003)
    stop = min(float(latest["low"]), float(one.iloc[-6:]["low"].min()),
               float(live_one["low"])) - buffer
    return True, (
        "五分钟底部阳线已收盘上穿MA5；当前一分钟首次有效突破MA20，"
        "立即小风险做多，不等待MA20回踩，也不等待MA5/MA10/MA20发散"
    ), stop, pd.Timestamp(latest["date"]), float(latest["close"])


def _five_minute_uptrend_pullback_resume_long_trigger(
    closed: pd.DataFrame, live_five: pd.DataFrame | None,
    one_minute: pd.DataFrame, one_minute_live: pd.DataFrame | None,
    fifteen_minute: pd.DataFrame, direction: int,
) -> tuple[bool, str, float, pd.Timestamp | None, float]:
    """Mature uptrend: buy the MA5/MA10 pullback as it resumes, not after expansion."""
    if direction != 1 or live_five is None or live_five.empty or one_minute_live is None or one_minute_live.empty:
        return False, "五分钟上涨回踩续涨等待实时五分钟和一分钟K线", 0.0, None, 0.0
    five = closed.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
    if min(len(five), len(one), len(fifteen)) < 24:
        return False, "五分钟上涨回踩续涨数据不足", 0.0, None, 0.0
    live = live_five.sort_values("date").iloc[-1]
    live_one = one_minute_live.sort_values("date").iloc[-1]
    five_close = five["close"].astype(float)
    fifteen_close = fifteen["close"].astype(float)
    ma5 = five_close.rolling(5).mean()
    ma10 = five_close.rolling(10).mean()
    ma20 = five_close.rolling(20).mean()
    fifteen_ma5 = fifteen_close.rolling(5).mean()
    fifteen_ma10 = fifteen_close.rolling(10).mean()
    fifteen_ma20 = fifteen_close.rolling(20).mean()
    atr5 = float(_atr(five).iloc[-1])
    atr1 = float(_atr(one).iloc[-1])
    if not all(pd.notna(value) and value > 0 for value in (atr5, atr1)):
        return False, "五分钟上涨回踩续涨ATR不足", 0.0, None, 0.0
    trend_aligned = (
        float(ma5.iloc[-1]) > float(ma10.iloc[-1]) > float(ma20.iloc[-1])
        and float(fifteen_ma5.iloc[-1]) > float(fifteen_ma10.iloc[-1]) > float(fifteen_ma20.iloc[-1])
        and float(ma20.iloc[-1] - ma20.iloc[-4]) >= atr5 * .03
    )
    pullback = five.iloc[-3:]
    touched_fast_band = bool((pullback["low"].astype(float) <= ma5.iloc[-3:].to_numpy() + atr5 * .18).any())
    held_ma10 = bool((pullback["close"].astype(float) >= ma10.iloc[-3:].to_numpy() - atr5 * .15).all())
    impulse_before_pullback = float(five.iloc[-8:-3]["high"].max()) >= float(ma20.iloc[-1]) + atr5 * .70
    live_price = float(live["close"])
    live_ma5 = float((five_close.iloc[-4:].sum() + live_price) / 5.0)
    resumed_five = (
        float(live["close"]) > float(live["open"])
        and float(live["low"]) <= live_ma5 + atr5 * .12
        and live_price >= live_ma5 + atr5 * .04
    )
    one_ma5 = one["close"].astype(float).rolling(5).mean()
    resumed_one = (
        float(live_one["close"]) > float(live_one["open"])
        and float(live_one["close"]) > float(one.iloc[-1]["close"])
        and float(live_one["close"]) >= float(one_ma5.iloc[-1])
    )
    # This entry belongs at the pullback turn.  Once price expands away from
    # MA5 it is a late chase and must stay blocked even if the trend is strong.
    not_extended = live_price - live_ma5 <= atr5 * .45
    if not (trend_aligned and impulse_before_pullback and touched_fast_band and held_ma10
            and resumed_five and resumed_one and not_extended):
        return False, (
            "等待五分钟与十五分钟多头排列，五分钟回踩MA5/MA10带守住后盘中重新转强，"
            "并由一分钟同步上穿短结构；离MA5超过0.45 ATR禁止迟到追涨"
        ), 0.0, None, 0.0
    recent_low = min(float(pullback["low"].min()), float(live["low"]), float(live_one["low"]))
    buffer = max(atr5 * .15, live_price * .0003)
    return True, (
        f"五分钟上涨趋势回踩续涨独立主触发：5分钟/15分钟MA5>MA10>MA20，"
        f"回踩MA5/MA10带守住后当前未收盘五分钟重新站上动态MA5 {live_ma5:.2f}，"
        "一分钟同步转强；不要求再次回踩一分钟MA20，不等待五分钟收盘"
    ), recent_low - buffer, pd.Timestamp(live["date"]), live_price


def _five_minute_ma20_pullback_intrabar_trigger(
    closed: pd.DataFrame, live_five: pd.DataFrame | None,
    one_minute: pd.DataFrame, one_minute_live: pd.DataFrame | None, direction: int,
    fifteen_minute: pd.DataFrame | None = None, fifteen_minute_live: pd.DataFrame | None = None,
) -> tuple[bool, str, float, pd.Timestamp | None, float]:
    """Trigger a continuation entry when a 5m pullback fails at falling/rising MA20."""
    if live_five is None or live_five.empty or one_minute_live is None or one_minute_live.empty:
        return False, "五分钟MA20反抽等待实时五分钟和一分钟K线", 0.0, None, 0.0
    data = closed.sort_values("date").reset_index(drop=True)
    one = one_minute.sort_values("date").reset_index(drop=True)
    if len(data) < 24 or len(one) < 8 or direction not in {-1, 1}:
        return False, "五分钟MA20反抽数据不足", 0.0, None, 0.0
    live = live_five.sort_values("date").iloc[-1]
    live_one = one_minute_live.sort_values("date").iloc[-1]
    close = data["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    atr = float(_atr(data).iloc[-1])
    if not pd.notna(atr) or atr <= 0:
        return False, "五分钟MA20反抽ATR不足", 0.0, None, 0.0
    live_price = float(live["close"])
    live_ma20 = float((close.iloc[-19:].sum() + live_price) / 20.0)
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-4]) / atr
    recent = data.iloc[-8:]
    one_recent = one.iloc[-6:]
    buffer = max(atr * .15, live_price * .0003)
    if direction < 0:
        older_peak = float(data.iloc[-20:-5]["high"].max())
        pullback_peak = max(float(data.iloc[-5:]["high"].max()), float(live["high"]))
        lower_pullback_high = pullback_peak <= older_peak - atr * .10
        fifteen_confirms = False
        if (fifteen_minute is not None and len(fifteen_minute) >= 20
                and fifteen_minute_live is not None and not fifteen_minute_live.empty):
            fifteen = fifteen_minute.sort_values("date").reset_index(drop=True)
            fifteen_live = fifteen_minute_live.sort_values("date").iloc[-1]
            prior_fifteen = fifteen.iloc[-1]
            fifteen_close = fifteen["close"].astype(float)
            fifteen_live_ma20 = float((fifteen_close.iloc[-19:].sum() + float(fifteen_live["close"])) / 20.0)
            prior_midpoint = (float(prior_fifteen["open"]) + float(prior_fifteen["close"])) / 2.0
            fifteen_confirms = (
                float(prior_fifteen["close"]) > float(prior_fifteen["open"])
                and float(fifteen_live["close"]) < float(fifteen_live["open"])
                and float(fifteen_live["high"]) >= fifteen_live_ma20
                and float(fifteen_live["close"]) < fifteen_live_ma20
                and float(fifteen_live["close"]) <= prior_midpoint
            )
        established_downtrend = (
            lower_pullback_high
            and ma20_slope <= .08
            and (float(ma5.iloc[-1]) < float(ma20.iloc[-1]) or fifteen_confirms)
        )
        tested_ma20 = float(live["high"]) >= live_ma20 - atr * .15
        rejected_ma20 = (
            float(live["close"]) < float(live["open"])
            and live_price <= live_ma20 - atr * .03
        )
        one_local_top = float(live_one["high"]) >= float(one_recent["high"].max()) - atr * .12
        one_confirms = (
            one_local_top
            and float(live_one["close"]) < float(live_one["open"])
            and float(live_one["close"]) < float(one.iloc[-1]["close"])
        )
        if not (established_downtrend and tested_ma20 and rejected_ma20 and one_confirms):
            return False, "等待五分钟下跌趋势反抽MA20失败，并由一分钟局部高点转弱确认", 0.0, None, 0.0
        stop = max(float(recent["high"].max()), float(live["high"]), float(live_one["high"])) + buffer
        return True, (
            f"五分钟下跌趋势较低高点反抽MA20快空：原下降结构未破坏，当前五分钟反抽MA20 {live_ma20:.2f} 后盘中重新跌破，"
            f"一分钟局部高点同步转弱{'，十五分钟长阴覆盖前阳线一半并下穿MA20' if fifteen_confirms else ''}；不等待五分钟收盘"
        ), stop, pd.Timestamp(live["date"]), live_price
    established_uptrend = float(ma5.iloc[-1]) > float(ma20.iloc[-1]) and ma20_slope >= .03
    tested_ma20 = float(live["low"]) <= live_ma20 + atr * .15
    rebounded_ma20 = float(live["close"]) > float(live["open"]) and live_price >= live_ma20 + atr * .03
    one_local_bottom = float(live_one["low"]) <= float(one_recent["low"].min()) + atr * .12
    one_confirms = (one_local_bottom and float(live_one["close"]) > float(live_one["open"])
                    and float(live_one["close"]) > float(one.iloc[-1]["close"]))
    if not (established_uptrend and tested_ma20 and rebounded_ma20 and one_confirms):
        return False, "等待五分钟上涨趋势回踩MA20守住，并由一分钟局部低点转强确认", 0.0, None, 0.0
    stop = min(float(recent["low"].min()), float(live["low"]), float(live_one["low"])) - buffer
    return True, (
        f"五分钟上涨趋势回踩MA20快多：MA5位于MA20上方、MA20上升{ma20_slope:.3f} ATR，"
        f"当前五分钟回踩MA20 {live_ma20:.2f} 后盘中重新站回，一分钟局部低点同步转强；不等待五分钟收盘"
    ), stop, pd.Timestamp(live["date"]), live_price


def five_minute_primary_entry_gate(
    five_minute: pd.DataFrame,
    fifteen_minute: pd.DataFrame,
    one_hour: pd.DataFrame,
    one_minute: pd.DataFrame,
    direction: int,
    five_minute_live: pd.DataFrame | None = None,
    one_minute_live: pd.DataFrame | None = None,
    fifteen_minute_live: pd.DataFrame | None = None,
) -> PrimaryEntryGate:
    """Apply option C: 5m owns the entry; 15m/1H are descriptive context."""
    triggered, trigger_reason, stop, candle_time, reference_price = _five_minute_endpoint_half_cover_trigger(
        five_minute, five_minute_live, one_minute, one_minute_live, direction,
    )
    endpoint_half_cover_triggered = triggered
    endpoint_full_reversal = bool(
        triggered and _endpoint_full_reversal_confirmation(
            five_minute, five_minute_live, one_minute, one_minute_live, direction))
    if endpoint_half_cover_triggered:
        trigger_reason = (
            f"{trigger_reason}；"
            + ("一分钟已穿MA5且五分钟已站到MA20反转侧，完整双周期反转确认"
               if endpoint_full_reversal else
               "身份仅为上一级5分钟顶部局部反转做空，不冒充真正双周期反转"
               if direction < 0 else
               "身份仅为上一级5分钟底部局部反转做多，不冒充真正双周期反转"))
    endpoint_reason = trigger_reason
    if not triggered:
        triggered, trigger_reason, stop = _five_minute_trigger(five_minute, direction)
        candle_time = pd.Timestamp(five_minute.sort_values("date").iloc[-1]["date"]) if triggered else None
        reference_price = float(five_minute.sort_values("date").iloc[-1]["close"]) if triggered else 0.0
    closed_reason = trigger_reason
    if not triggered:
        triggered, trigger_reason, stop, candle_time, reference_price = _five_minute_closed_ma5_one_minute_ma20_break_long_trigger(
            five_minute, one_minute, one_minute_live, direction,
        )
    if not triggered:
        triggered, trigger_reason, stop, candle_time, reference_price = _five_minute_higher_low_ma5_long_trigger(
            five_minute, five_minute_live, one_minute, one_minute_live, direction,
        )
    if not triggered:
        triggered, trigger_reason, stop, candle_time, reference_price = _five_minute_uptrend_pullback_resume_long_trigger(
            five_minute, five_minute_live, one_minute, one_minute_live, fifteen_minute, direction,
        )
    if not triggered:
        triggered, trigger_reason, stop, candle_time, reference_price = _five_minute_ma20_pullback_intrabar_trigger(
            five_minute, five_minute_live, one_minute, one_minute_live, direction,
            fifteen_minute, fifteen_minute_live,
        )
    if not triggered:
        triggered, trigger_reason, stop, candle_time, reference_price = _five_minute_intrabar_trigger(
            five_minute, five_minute_live, one_minute_live, direction,
        )
    if not triggered:
        return PrimaryEntryGate(False, f"{endpoint_reason}；{closed_reason}；{trigger_reason}")
    fifteen = confirmed_price_structure(fifteen_minute, "15分钟")
    hour = confirmed_price_structure(one_hour, "1小时")
    one = one_minute.sort_values("date").reset_index(drop=True)
    auxiliary = "1分钟辅助数据不足"
    if len(one) >= 3:
        latest = one.iloc[-1]
        auxiliary_direction = 1 if float(latest["close"]) > float(latest["open"]) else (-1 if float(latest["close"]) < float(latest["open"]) else 0)
        auxiliary = "1分钟辅助同向" if auxiliary_direction == direction else "1分钟仅作辅助，未独立授权"
    return PrimaryEntryGate(
        True,
        f"{trigger_reason}；方案C通过：15分钟/1小时只作背景提示，不机械拦截｜{fifteen.reason}｜{hour.reason}；{auxiliary}",
        stop, candle_time, reference_price,
        ("one_minute_ma_fan_endpoint_five_minute_half_cover_confirmed"
         if endpoint_full_reversal else
         "five_minute_bottom_local_reversal_half_cover_long"
         if endpoint_half_cover_triggered and direction > 0 else
         "five_minute_top_local_reversal_half_cover_short"
         if endpoint_half_cover_triggered else ""),
    )
