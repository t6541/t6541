from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from collections.abc import Iterator

from .config import AppConfig
from .data import okx_history_market
from .intraday import decide_three_timeframe_entry, latest_timeframe_signal
from .state import SignalIntent, StateStore

STRATEGY_VERSION = "three-timeframe-dual-ma-v1"
MAX_AGE_SECONDS = {"1m": 180, "5m": 600, "15m": 1_800}


def retry_delay_seconds(consecutive_failures: int) -> int:
    if consecutive_failures < 1:
        return 0
    return min(60, 2 ** min(consecutive_failures, 6))


@dataclass(frozen=True)
class ObservationResult:
    direction: int
    reason: str
    intent_created: bool
    orders_enabled: bool
    signals: dict


def observe_once(cfg: AppConfig, database: str | Path, *, now: datetime | None = None) -> ObservationResult:
    current = now or datetime.now(timezone.utc)
    store = StateStore(database)
    try:
        signals = {}
        for bar in cfg.strategy.signal_bars:
            market = okx_history_market((cfg.okx.instruments[0],), bar, 100)
            signal = latest_timeframe_signal(market, bar, fast_window=cfg.strategy.fast_window, slow_window=cfg.strategy.slow_window)
            candle_time = signal.candle_time.to_pydatetime().replace(tzinfo=timezone.utc)
            age = (current - candle_time).total_seconds()
            if age < 0 or age > MAX_AGE_SECONDS[bar]:
                store.record_event("stale_market", {"bar": bar, "age_seconds": age})
                return ObservationResult(0, f"{bar} market data is stale", False, False, {})
            signals[bar] = signal
        risk = store.load_daily_risk(current.date())
        decision = decide_three_timeframe_entry(
            signals, risk,
            daily_loss_limit=cfg.risk.daily_loss_limit,
            daily_profit_stop=cfg.risk.daily_profit_stop,
            max_consecutive_losses=cfg.risk.max_consecutive_losses,
            max_trades=cfg.risk.max_trades_per_day,
            cooldown_seconds=cfg.risk.cooldown_seconds,
            round_trip_cost_pct=2 * ((cfg.backtest.fee_bps * (1 - cfg.backtest.fee_rebate_rate) + cfg.backtest.slippage_bps) / 10_000),
            min_cost_edge_multiple=cfg.risk.min_cost_edge_multiple,
        )
        created = False
        if decision.direction:
            intent = SignalIntent(
                cfg.okx.instruments[0], signals["1m"].candle_time.isoformat(), decision.direction,
                STRATEGY_VERSION, decision.stop_distance_pct,
            )
            created = store.record_intent(intent)
        payload = {
            "direction": decision.direction,
            "reason": decision.reason,
            "intent_created": created,
            "orders_enabled": False,
            "signals": {bar: asdict(signal) | {"candle_time": signal.candle_time.isoformat()} for bar, signal in signals.items()},
        }
        store.record_event("observation", payload)
        return ObservationResult(decision.direction, decision.reason, created, False, payload["signals"])
    finally:
        store.close()


def observation_loop(
    cfg: AppConfig, database: str | Path, *, interval_seconds: int = 60, iterations: int = 0
) -> Iterator[ObservationResult]:
    """Yield locked observation ticks; iterations=0 runs until interrupted."""
    if interval_seconds < 30:
        raise ValueError("observation interval must be at least 30 seconds")
    if iterations < 0:
        raise ValueError("iterations cannot be negative")
    count = 0
    while iterations == 0 or count < iterations:
        yield observe_once(cfg, database)
        count += 1
        if iterations == 0 or count < iterations:
            time.sleep(interval_seconds)
