from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .config import AppConfig
from .data import okx_history_market
from .range_pivot import STRATEGY_ID, STRATEGY_VERSION, RangePivotSignal, evaluate_range_pivot
from .state import RangePivotIntent, StateStore


def round_trip_cost_pct(cfg: AppConfig) -> float:
    fee = cfg.backtest.fee_bps / 10_000
    slippage = cfg.backtest.slippage_bps / 10_000
    return 2 * (fee * (1 - cfg.backtest.fee_rebate_rate) + slippage)


def observe_range_pivot_once(cfg: AppConfig, database: str | Path | None = None) -> RangePivotSignal:
    instrument = cfg.okx.instruments[0]
    # Strategy 02 always discovers reversal zones on confirmed 5m candles.
    # The 1m market is only the precise entry trigger.
    pivot_bar = "5m"
    pivot_market = okx_history_market((instrument,), pivot_bar, 100)
    entry_market = pivot_market if pivot_bar == "1m" else okx_history_market((instrument,), "1m", 100)
    trend_market = okx_history_market((instrument,), "15m", 100)
    one_hour_market = okx_history_market((instrument,), "1H", 100)
    four_hour_market = okx_history_market((instrument,), "4H", 100)
    signal = evaluate_range_pivot(
        pivot_market, entry_market, trend_market,
        lookback=cfg.strategy.pivot_lookback,
        touch_atr_multiple=cfg.strategy.touch_atr_multiple,
        stop_atr_multiple=cfg.strategy.stop_atr_multiple,
        minimum_reward_risk=cfg.strategy.minimum_reward_risk,
        trend_fast_window=cfg.strategy.trend_fast_window,
        trend_slow_window=cfg.strategy.trend_slow_window,
        entry_fast_window=cfg.strategy.entry_fast_window,
        entry_slow_window=cfg.strategy.entry_slow_window,
        adx_window=cfg.strategy.adx_window,
        max_adx_for_reversal=cfg.strategy.max_adx_for_reversal,
        round_trip_cost_pct=round_trip_cost_pct(cfg),
        min_cost_edge_multiple=cfg.risk.min_cost_edge_multiple,
        frequency_test_mode=cfg.strategy.frequency_test_mode,
        thirty_minute_market=None,
        one_hour_market=one_hour_market,
        four_hour_market=four_hour_market,
    )
    if database is not None and signal.action != "observe":
        store = StateStore(database)
        try:
            store.record_range_pivot_intent(RangePivotIntent(
                instrument, STRATEGY_ID, STRATEGY_VERSION, pivot_bar, signal.confirmed_bar_time.isoformat(),
                signal.action, signal.direction, signal.pivot_price, signal.atr, signal.stop_price,
                signal.take_profit_price, signal.config_fingerprint, branch=signal.branch,
            ))
        finally:
            store.close()
    return signal


def write_observation_report(signal: RangePivotSignal, output: str | Path, *, timeframe: str, instrument: str) -> Path:
    target = Path(output)
    target.mkdir(parents=True, exist_ok=True)
    rows = {
        "客户端策略编号": "策略02",
        "策略ID": STRATEGY_ID,
        "策略版本": STRATEGY_VERSION,
        "规则分支": signal.branch,
        "交易品种": instrument,
        "区间周期": timeframe,
        "确认K线": signal.confirmed_bar_time,
        "区间高点": f"{signal.pivot_high:.4f}",
        "区间低点": f"{signal.pivot_low:.4f}",
        "ATR": f"{signal.atr:.4f}",
        "ADX": f"{signal.adx:.2f}",
        "市场环境": signal.market_state,
        "1分钟确认": "已确认" if signal.entry_confirmed else "未确认",
        "当前动作": signal.action,
        "第一止盈（1R/计划减仓50%）": f"{signal.first_take_profit_price:.4f}" if signal.direction else "未触发",
        "第二止盈（区间中线或至少1.5R）": f"{signal.take_profit_price:.4f}" if signal.direction else "未触发",
        "1R后保本参考价": f"{signal.breakeven_stop_price:.4f}" if signal.direction else "未触发",
        "原因": signal.reason,
        "配置指纹": signal.config_fingerprint,
        "自动下单": "关闭（仅公开历史K线观察）",
    }
    body = "".join(f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>" for k, v in rows.items())
    html = ("<!doctype html><meta charset='utf-8'><title>策略02观察报告</title>"
            "<style>body{font-family:Segoe UI,Microsoft YaHei,sans-serif;margin:32px;max-width:900px}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:10px;text-align:left}"
            "th{width:190px;background:#f4f4f4}.safe{color:#087f23;font-weight:700}</style>"
            f"<h1>策略02｜{STRATEGY_VERSION}</h1><p class='safe'>本报告未连接实盘，也没有提交模拟订单。</p>"
            f"<p>生成时间：{datetime.now(timezone.utc).isoformat()}</p><table>{body}</table>")
    path = target / "range-pivot-observation.html"
    path.write_text(html, encoding="utf-8")
    return path
