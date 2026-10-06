from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .data import okx_history_market
from .entry_risk import latest_atr
from .okx import OkxCredentials, OkxDemoClient
from .range_pivot import (confirmed_wick_pivots, five_minute_ma_deviation,
                          five_minute_reversal_zone_allows, local_post_impulse_range)
from .shared_signal_center import scan_shared_advance_signals
from .state import StateStore
from .structure_sniper import execute_structure_sniper_tick


@dataclass(frozen=True)
class RestoreResult:
    strategy: str
    action: str
    reason: str
    order_ids: tuple[str, ...] = ()


def restore_demo_structure_snipers(
    accounts: tuple[tuple[str, OkxCredentials | None, str, str], ...],
    instrument: str,
    database: str | Path,
    *, range_config: AppConfig | None = None,
) -> tuple[RestoreResult, ...]:
    """Reconcile only Demo resting sniper orders after application startup.

    This deliberately does not unlock or execute ordinary market-entry rules.
    """
    markets = {
        bar: okx_history_market((instrument,), bar, 100)
        for bar in ("1m", "5m", "15m")
    }
    store = StateStore(database)
    try:
        scan_shared_advance_signals(
            store, instrument=instrument, one_minute=markets["1m"],
            five_minute=markets["5m"], fifteen_minute=markets["15m"],
        )
    finally:
        store.close()
    results: list[RestoreResult] = []
    for strategy, credentials, prefix, strategy_version in accounts:
        if credentials is None:
            results.append(RestoreResult(strategy, "skipped", "未保存Demo API，跳过启动预埋恢复"))
            continue
        try:
            client = OkxDemoClient(credentials, timeout=20)
            client.require_swap_trading_mode()
            snapshot = client.safety_snapshot()
            migrate_stops = getattr(client, "migrate_active_stop_losses_to_mark", None)
            migrated_stops = migrate_stops(snapshot) if migrate_stops else 0
            allowed_directions = (-1, 1)
            if strategy == "strategy_02":
                if range_config is None:
                    raise ValueError("strategy_02 restore requires its range configuration")
                resistance, support = confirmed_wick_pivots(
                    markets["5m"], range_config.strategy.pivot_lookback)
                atr_5m = latest_atr(markets["5m"])
                zone_ok, _ = five_minute_reversal_zone_allows(
                    resistance, support, atr_5m)
                far_high, far_low, _, _ = five_minute_ma_deviation(
                    markets["5m"], resistance, support, atr_5m)
                local_ok, _, _, _ = local_post_impulse_range(markets["5m"], atr_5m)
                # Keep protected support and pressure lines on both sides.
                # Range/deviation remains evidence for ordinary entries, not
                # a reason to erase one side of the persistent resting pair.
                allowed_directions = (-1, 1)
            result = execute_structure_sniper_tick(
                client, snapshot, instrument, prefix,
                markets["5m"], markets["15m"], markets["1m"],
                database=database, strategy_id=strategy, strategy_version=strategy_version,
                preferred_timeframes=("5m",), pivot_selection="latest",
                target_ma_timeframe="5m", max_levels_per_direction=2,
                allowed_directions=allowed_directions,
            )
            results.append(RestoreResult(
                strategy, result.action,
                ((f"legacy mark-stop migrations={migrated_stops}; " if migrated_stops else "")
                 + result.reason),
                getattr(result, "order_ids", ())))
        except Exception as exc:
            results.append(RestoreResult(strategy, "error", str(exc)))
    return tuple(results)
