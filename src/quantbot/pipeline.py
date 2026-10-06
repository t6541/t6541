from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .backtest import run_backtest
from .config import load_config
from .data import load_csv, okx_history_market, synthetic_market
from .factors import compute_factors
from .report import write_html_report
from .risk import constrain_weights
from .strategy import dual_ma_trend_weights, target_weights


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        return subprocess.check_output([git, "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.SubprocessError, OSError):
        return None


def run_pipeline(config_path: str | Path) -> Path:
    config_path = Path(config_path).resolve()
    cfg = load_config(config_path)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(cfg.output.root) / run_id
    suffix = 1
    while out.exists():
        out = Path(cfg.output.root) / f"{run_id}-{suffix}"
        suffix += 1
    out.mkdir(parents=True)
    if cfg.data.source == "csv":
        market = load_csv(cfg.data.csv_path)
    elif cfg.data.source == "okx":
        market = okx_history_market(cfg.data.symbols, cfg.data.bar, cfg.data.history_limit)
    else:
        market = synthetic_market(cfg.data.start, cfg.data.end, cfg.data.symbols, cfg.data.seed)
    market_path = out / "market.csv"
    market.to_csv(market_path, index=False)
    data_hash = _sha256(market_path)
    factors = compute_factors(market, cfg.factors.momentum_window, cfg.factors.volatility_window, cfg.factors.trend_window)
    if cfg.strategy.name == "dual_ma_trend":
        targets = dual_ma_trend_weights(
            market,
            fast_window=cfg.strategy.fast_window,
            slow_window=cfg.strategy.slow_window,
            volatility_window=cfg.strategy.volatility_window,
            max_annualized_volatility=cfg.strategy.max_annualized_volatility,
            stop_loss_pct=cfg.strategy.stop_loss_pct,
            long_weight=cfg.strategy.long_weight,
            short_weight=cfg.strategy.short_weight,
            annualization=cfg.backtest.annualization,
        )
    else:
        targets = target_weights(factors, cfg.strategy.top_n, cfg.strategy.rebalance_every)
    targets = constrain_weights(targets, cfg.risk.max_position, cfg.risk.max_gross_exposure, cfg.risk.max_industry_exposure)
    result = run_backtest(market, targets, cfg.backtest.initial_cash, cfg.backtest.fee_bps, cfg.backtest.slippage_bps, cfg.backtest.annualization, cfg.risk.max_drawdown, cfg.backtest.fee_rebate_rate)
    factors.to_csv(out / "factors.csv")
    targets.to_csv(out / "target_weights.csv")
    result.daily.to_csv(out / "backtest_daily.csv")
    (out / "metrics.json").write_text(json.dumps(result.metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(config_path, out / "config.toml")
    manifest = {"run_id": out.name, "created_at_utc": datetime.now(timezone.utc).isoformat(), "mode": "paper-research", "data_sha256": data_hash, "git_revision": _git_revision(), "config": str(config_path)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html_report(out / "report.html", out.name, result.metrics, data_hash)
    return out
