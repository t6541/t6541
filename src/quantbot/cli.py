from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

from .config import load_config
from .data import okx_history_market
from .intraday import DailyRiskState, decide_three_timeframe_entry, latest_timeframe_signal
from .live_audit import LIVE_CONFIRMATION, OkxLiveReadOnlyClient
from .okx import OkxDemoClient
from .pipeline import run_pipeline
from .runner import observation_loop, observe_once
from .public_paper import run_public_paper_cycle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantbot", description="Paper-trading quantitative workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", default="configs/default.toml")
    sub.add_parser("test")
    okx = sub.add_parser("okx", help="OKX demo-trading operations")
    okx.add_argument("action", choices=["instrument", "check", "leverage", "signals", "observe", "watch"])
    okx.add_argument("--config", default="configs/default.toml")
    okx.add_argument("--interval-seconds", type=int, default=60)
    okx.add_argument("--iterations", type=int, default=0)
    live = sub.add_parser("live", help="strictly read-only OKX Live audit")
    live.add_argument("action", choices=["audit"])
    live.add_argument("--confirm", default="")
    paper = sub.add_parser("paper-cycle", help="public-data-only paper ledger cycle")
    paper.add_argument("--ledger", default="laya-data/paper-eth.sqlite")
    paper.add_argument("--instrument", default="ETH-USDT-SWAP")
    args = parser.parse_args(argv)
    if args.command == "run":
        output = run_pipeline(args.config)
        print(f"Completed paper-research run: {output.resolve()}")
        return 0
    if args.command == "test":
        return subprocess.call([sys.executable, "-m", "pytest"])
    if args.command == "paper-cycle":
        metrics = run_public_paper_cycle(ledger_path=args.ledger, instrument=args.instrument)
        print(json.dumps({
            "mode": "public-paper-only", "instrument": args.instrument,
            "timestamp": metrics.timestamp, "equity": metrics.equity,
            "realized_pnl": metrics.realized_pnl, "unrealized_pnl": metrics.unrealized_pnl,
            "fees": metrics.fees, "slippage": metrics.slippage,
            "drawdown": metrics.drawdown, "positions": metrics.positions,
            "execution_permitted": False,
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "live":
        if args.confirm != LIVE_CONFIRMATION:
            parser.error(f"live audit requires --confirm {LIVE_CONFIRMATION}")
        print(json.dumps(OkxLiveReadOnlyClient().audit(), ensure_ascii=False, indent=2))
        return 0
    cfg = load_config(args.config)
    client = OkxDemoClient()
    if args.action == "observe":
        database = Path(cfg.output.root) / "live" / "state.sqlite3"
        result = observe_once(cfg, database)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    if args.action == "watch":
        database = Path(cfg.output.root) / "live" / "state.sqlite3"
        try:
            for result in observation_loop(cfg, database, interval_seconds=args.interval_seconds, iterations=args.iterations):
                print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
        except KeyboardInterrupt:
            print("Observation loop stopped safely; order execution remained locked.")
        return 0
    if args.action == "signals":
        signals = {}
        for bar in cfg.strategy.signal_bars:
            market = okx_history_market((cfg.okx.instruments[0],), bar, 100)
            signals[bar] = latest_timeframe_signal(
                market, bar,
                fast_window=cfg.strategy.fast_window,
                slow_window=cfg.strategy.slow_window,
            )
        decision = decide_three_timeframe_entry(
            signals,
            DailyRiskState(),
            daily_loss_limit=cfg.risk.daily_loss_limit,
            daily_profit_stop=cfg.risk.daily_profit_stop,
            max_consecutive_losses=cfg.risk.max_consecutive_losses,
            max_trades=cfg.risk.max_trades_per_day,
            cooldown_seconds=cfg.risk.cooldown_seconds,
            round_trip_cost_pct=2 * ((cfg.backtest.fee_bps * (1 - cfg.backtest.fee_rebate_rate) + cfg.backtest.slippage_bps) / 10_000),
            min_cost_edge_multiple=cfg.risk.min_cost_edge_multiple,
        )
        output = {
            "signals": [
                {
                    "bar": signal.bar,
                    "candle_time": str(signal.candle_time),
                    "close": signal.close,
                    "fast_ma": signal.fast_ma,
                    "slow_ma": signal.slow_ma,
                    "atr_pct": signal.atr_pct,
                    "direction": "long" if signal.direction > 0 else "short" if signal.direction < 0 else "flat",
                    "entry_confirmed": signal.entry_confirmed,
                }
                for signal in signals.values()
            ],
            "decision": {
                "direction": "long" if decision.direction > 0 else "short" if decision.direction < 0 else "flat",
                "reason": decision.reason,
                "stop_distance_pct": decision.stop_distance_pct,
            },
            "orders_enabled": False,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    if args.action == "instrument":
        result = {item: client.public_instrument(item) for item in cfg.okx.instruments}
    elif args.action == "check":
        result = client.check_demo_account()
    else:
        result = {item: client.set_leverage(cfg.okx.leverage, item) for item in cfg.okx.instruments}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
