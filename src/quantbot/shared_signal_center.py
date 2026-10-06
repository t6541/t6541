from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .state import StateStore


EXTREME_SIGNAL_TYPES = ("early_high_sweep_reject", "early_low_sweep_reclaim")
PRESSURE_SHORT_SIGNAL = "pressure_zone_early_short"
ONE_MINUTE_MA20_CROSS_DOWN = "one_minute_bearish_ma20_cross"
TOP_BEARISH_ACCUMULATION = "top_bearish_accumulation"
DOWNTREND_PULLBACK_REJECT = "downtrend_pullback_reject"
CONSUMABLE_SIGNAL_TYPES = EXTREME_SIGNAL_TYPES + (
    PRESSURE_SHORT_SIGNAL, TOP_BEARISH_ACCUMULATION, DOWNTREND_PULLBACK_REJECT,
)


@dataclass(frozen=True)
class SharedAdvanceScanResult:
    recorded_candidates: int
    actionable_signals: int
    reason: str


def _market(frame: object) -> pd.DataFrame | None:
    required = {"date", "open", "high", "low", "close", "volume"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
        return None
    out = frame.copy().sort_values("date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    return out if not out.empty else None


def _atr(frame: pd.DataFrame, period: int = 14) -> float:
    previous = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    value = true_range.rolling(period, min_periods=max(3, period // 2)).mean().iloc[-1]
    return float(value) if pd.notna(value) else 0.0


def _top_bearish_accumulation(one: pd.DataFrame) -> tuple[bool, str, float, dict]:
    """Accumulate 2-3 bearish closes after a stretched local 1m high."""
    if len(one) < 24:
        return False, "", 0.0, {}
    frame = one.copy()
    close = frame["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(period).mean() for period in (5, 10, 20))
    atr = _atr(frame)
    if atr <= 0:
        return False, "", 0.0, {}
    search_start = max(20, len(frame) - 7)
    for peak_index in range(len(frame) - 2, search_start - 1, -1):
        window = frame.iloc[peak_index + 1:min(len(frame), peak_index + 5)]
        if len(window) < 2:
            continue
        stretched = (
            ma5.iloc[peak_index] > ma10.iloc[peak_index] > ma20.iloc[peak_index]
            and (ma5.iloc[peak_index] - ma20.iloc[peak_index]) >= atr * .35
        )
        prior = frame.iloc[max(0, peak_index - 20):peak_index + 1]
        high_zone = float(frame.iloc[peak_index]["high"]) >= float(prior["high"].quantile(.85))
        bearish = window[window["close"] < window["open"]]
        bearish_body = float((bearish["open"] - bearish["close"]).sum())
        lower_close = len(bearish) >= 2 and float(bearish["close"].iloc[-1]) < float(bearish["close"].iloc[0])
        ma5_rolled = ma5.iloc[-1] <= ma5.iloc[-2] and float(frame.iloc[-1]["close"]) < float(ma5.iloc[-1])
        if stretched and high_zone and len(bearish) >= 2 and bearish_body >= atr * .35 and lower_close and ma5_rolled:
            stop = float(frame.iloc[peak_index:min(len(frame), peak_index + 5)]["high"].max()) + atr * .15
            features = {
                "bearish_count": int(len(bearish)), "bearish_body_atr": bearish_body / atr,
                "ma5_ma20_gap_atr": float((ma5.iloc[peak_index] - ma20.iloc[peak_index]) / atr),
                "peak_price": float(frame.iloc[peak_index]["high"]),
                "window_bars": int(len(window)),
            }
            reason = (
                f"一分钟高位累计转弱：均线向上发散{features['ma5_ma20_gap_atr']:.2f} ATR，"
                f"随后{len(window)}根内出现{len(bearish)}根阴线，累计实体{features['bearish_body_atr']:.2f} ATR，"
                "阴线收盘降低且MA5转弱，提前做空"
            )
            return True, reason, stop, features
    return False, "", 0.0, {}


def _downtrend_pullback_reject(one: pd.DataFrame) -> tuple[bool, str, float, dict]:
    """Find the first local bullish pullback followed by bearish rejection in an expanded downtrend."""
    if len(one) < 24:
        return False, "", 0.0, {}
    frame = one.copy()
    close = frame["close"].astype(float)
    ma5, ma10, ma20 = (close.rolling(period).mean() for period in (5, 10, 20))
    atr = _atr(frame)
    if atr <= 0:
        return False, "", 0.0, {}
    bullish, bearish = frame.iloc[-2], frame.iloc[-1]
    expanded = ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] and (ma20.iloc[-1] - ma5.iloc[-1]) >= atr * .35
    falling = ma20.iloc[-1] < ma20.iloc[-3]
    bull_body = float(bullish["close"] - bullish["open"])
    bear_body = float(bearish["open"] - bearish["close"])
    local_high = float(bullish["high"]) > float(frame.iloc[-3]["high"])
    rejected = float(bearish["close"]) < (float(bullish["open"]) + float(bullish["close"])) / 2
    if not (expanded and falling and bull_body > 0 and bear_body >= atr * .20 and local_high and rejected):
        return False, "", 0.0, {}
    stop = float(frame.iloc[-6:]["high"].max()) + atr * .15
    features = {
        "bearish_body_atr": bear_body / atr,
        "ma20_ma5_gap_atr": float((ma20.iloc[-1] - ma5.iloc[-1]) / atr),
        "ma20_slope_atr": float((ma20.iloc[-1] - ma20.iloc[-3]) / atr),
        "pullback_high": float(bullish["high"]),
    }
    reason = (
        f"一分钟下跌续势反抽失败：MA5<MA10<MA20且发散{features['ma20_ma5_gap_atr']:.2f} ATR，"
        f"局部阳线反抽高点后紧跟{features['bearish_body_atr']:.2f} ATR阴线转弱，按本轮局部高点保护追空"
    )
    return True, reason, stop, features


def _resolve_pattern_outcomes(store: StateStore, instrument: str,
                              one: pd.DataFrame, horizon: int = 15) -> None:
    """Attach fixed-horizon market outcomes without changing live thresholds."""
    dates = pd.to_datetime(one["date"], utc=True)
    for row in store.pending_market_patterns(instrument):
        confirmed = pd.to_datetime(str(row["confirmed_bar_time"]), utc=True)
        future = one.loc[dates > confirmed].head(horizon)
        if len(future) < horizon:
            continue
        entry, stop, direction = float(row["entry_reference"]), float(row["stop_reference"]), int(row["direction"])
        risk = abs(stop - entry)
        if direction < 0:
            favorable = entry - float(future["low"].min())
            adverse = float(future["high"].max()) - entry
        else:
            favorable = float(future["high"].max()) - entry
            adverse = entry - float(future["low"].min())
        reached_1r = risk > 0 and favorable >= risk
        store.resolve_market_pattern(
            str(row["event_key"]),
            outcome_status="favorable_1r" if reached_1r else "not_favorable_1r",
            outcome={
                "horizon_bars": horizon,
                "max_favorable": round(favorable, 8),
                "max_adverse": round(adverse, 8),
                "risk": round(risk, 8),
                "max_favorable_r": round(favorable / risk, 6) if risk > 0 else 0.0,
                "max_adverse_r": round(adverse / risk, 6) if risk > 0 else 0.0,
            },
        )


def scan_shared_advance_signals(
    store: StateStore,
    *,
    instrument: str,
    one_minute: object,
    five_minute: object,
    fifteen_minute: object,
) -> SharedAdvanceScanResult:
    """Record every confirmed 1m MA20 downside cross and publish early shorts.

    Candidate crosses are kept as audit evidence with an immediately expired TTL;
    only a 15m-pressure + 5m-rejection combination becomes a consumable signal.
    This scanner is public-market-data only and never submits an order.
    """
    one, five, fifteen = (_market(one_minute), _market(five_minute), _market(fifteen_minute))
    if one is None or five is None or fifteen is None:
        return SharedAdvanceScanResult(0, 0, "共享提前信号扫描跳过：K线数据无效")
    if len(one) < 22 or len(five) < 12 or len(fifteen) < 8:
        return SharedAdvanceScanResult(0, 0, "共享提前信号扫描跳过：历史K线不足")

    _resolve_pattern_outcomes(store, instrument, one)

    one = one.copy()
    one["ma20"] = one["close"].rolling(20).mean()
    one["previous_ma20"] = one["ma20"].shift(1)
    one["previous_close"] = one["close"].shift(1)
    crosses = one[
        (one["close"] < one["open"])
        & (one["close"] < one["ma20"])
        & ((one["previous_close"] >= one["previous_ma20"]) | (one["open"] >= one["ma20"]))
    ].tail(40)
    recorded = 0
    atr_1m = max(_atr(one), float(one["close"].iloc[-1]) * .0002)
    for _, row in crosses.iterrows():
        reason = (
            f"1分钟阴线下穿MA20候选：开{float(row['open']):.2f}，"
            f"收{float(row['close']):.2f}，MA20 {float(row['ma20']):.2f}；"
            "先记录，不因出现较早而丢弃，等待15分钟压力与5分钟转弱共同确认"
        )
        recorded += int(store.publish_shared_signal(
            instrument=instrument,
            signal_type=ONE_MINUTE_MA20_CROSS_DOWN,
            direction=-1,
            confirmed_bar_time=pd.Timestamp(row["date"]).isoformat(),
            reason=reason,
            stop_price=float(row["high"]) + atr_1m * .35,
            source_strategy="shared_signal_center_scan",
            ttl_seconds=0,
        ))

    actionable = 0
    for signal_type, detector in (
        (TOP_BEARISH_ACCUMULATION, _top_bearish_accumulation),
        (DOWNTREND_PULLBACK_REJECT, _downtrend_pullback_reject),
    ):
        active, reason, stop, features = detector(one)
        if not active:
            continue
        confirmed = pd.Timestamp(one.iloc[-1]["date"]).isoformat()
        event_key = f"{instrument}|{signal_type}|-1|{confirmed}"
        features.update({
            "entry_reference": float(one.iloc[-1]["close"]),
            "stop_distance_atr_1m": (stop - float(one.iloc[-1]["close"])) / atr_1m,
        })
        store.record_market_pattern(
            event_key=event_key, instrument=instrument, pattern_type=signal_type,
            direction=-1, confirmed_bar_time=confirmed, status="actionable",
            entry_reference=float(one.iloc[-1]["close"]), stop_reference=stop,
            features=features,
        )
        actionable += int(store.publish_shared_signal(
            instrument=instrument, signal_type=signal_type, direction=-1,
            confirmed_bar_time=confirmed, reason=reason, stop_price=stop,
            source_strategy="shared_signal_center_scan", ttl_seconds=300,
        ))

    recent_15 = fifteen.tail(12).copy()
    prior_15 = recent_15.iloc[:-1] if len(recent_15) > 1 else recent_15
    pressure_wick = float(prior_15["high"].max())
    pressure_body = float(prior_15[["open", "close"]].max(axis=1).quantile(.80))
    atr_5m = max(_atr(five), float(five["close"].iloc[-1]) * .0005)
    # Prefer the repeatable real-body area; never chase the isolated wick top.
    pressure_entry = min(pressure_wick, pressure_body + atr_5m * .30)

    latest_5 = five.iloc[-1]
    recent_5 = five.tail(5)
    approached_pressure = float(recent_5["high"].max()) >= pressure_entry - atr_5m * .55
    five_ma5 = float(five["close"].rolling(5).mean().iloc[-1])
    five_ma10 = float(five["close"].rolling(10).mean().iloc[-1])
    five_rejection = (
        float(latest_5["close"]) < float(latest_5["open"])
        or float(latest_5["close"]) < min(five_ma5, five_ma10)
        or float(recent_5["high"].iloc[-1]) < float(recent_5["high"].iloc[-2])
    )
    recent_cross = not crosses.empty and pd.Timestamp(crosses.iloc[-1]["date"]) >= pd.Timestamp(one.iloc[-6]["date"])
    current = float(one["close"].iloc[-1])
    room = pressure_entry - current
    if approached_pressure and five_rejection and recent_cross and room >= atr_1m * .40:
        # Protect this setup with its current local top.  A much older isolated
        # 15m wick is useful as pressure evidence but must not inflate the
        # short-term stop and required profit by tens of points.
        local_top = max(float(recent_5["high"].max()), float(one.tail(12)["high"].max()))
        stop = local_top + max(atr_5m * .15, pressure_entry * .00030)
        trigger_time = pd.Timestamp(crosses.iloc[-1]["date"]).isoformat()
        actionable += int(store.publish_shared_signal(
            instrument=instrument,
            signal_type=PRESSURE_SHORT_SIGNAL,
            direction=-1,
            confirmed_bar_time=trigger_time,
            reason=(
                f"共享提前做空：15分钟压力实体区{pressure_entry:.2f}，"
                f"5分钟接近压力后转弱，1分钟阴线下穿MA20；"
                "不强制等待三个高点逐级降低，优先实体压力限价，错过后保留市价确认"
            ),
            stop_price=stop,
            source_strategy="shared_signal_center_scan",
            ttl_seconds=900,
        ))
    return SharedAdvanceScanResult(
        recorded,
        actionable,
        f"共享提前信号扫描完成：新增1分钟下穿候选{recorded}，可执行信号{actionable}",
    )


def signal_type_for(direction: int) -> str:
    return "early_high_sweep_reject" if direction < 0 else "early_low_sweep_reclaim"


def publish_extreme_signal(store: StateStore, *, instrument: str, strategy_id: str,
                           direction: int, confirmed_bar_time: str,
                           reason: str, stop_price: float) -> bool:
    return store.publish_shared_signal(
        instrument=instrument, signal_type=signal_type_for(direction), direction=direction,
        confirmed_bar_time=confirmed_bar_time, reason=reason, stop_price=stop_price,
        # A bottom/top sweep is stage one of a multi-stage reversal.  Keep the
        # evidence alive long enough for MA5/MA10 reclaim and MA20 follow-up.
        source_strategy=strategy_id, ttl_seconds=1800,
    )


def recover_extreme_signal(store: StateStore, *, instrument: str, strategy_id: str):
    row = store.latest_shared_signal(instrument, CONSUMABLE_SIGNAL_TYPES)
    if row is not None and str(row["source_strategy"]) != strategy_id:
        store.record_shared_signal_consumed(str(row["event_key"]), strategy_id)
        return row
    return None
