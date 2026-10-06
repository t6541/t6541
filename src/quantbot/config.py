from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
import tomllib


RETIRED_OKX_CONFIG_KEYS = frozenset({
    "validation_channel_bar",
    "validation_channel_entry_fraction",
    "validation_channel_window",
    "validation_extreme_entry_fraction",
})


@dataclass(frozen=True)
class DataConfig:
    source: str = "synthetic"
    csv_path: str = "data/raw/market.csv"
    start: str = "2020-01-01"
    end: str = "2024-12-31"
    symbols: tuple[str, ...] = ("AAA", "BBB", "CCC")
    seed: int = 42
    bar: str = "1D"
    history_limit: int = 500


@dataclass(frozen=True)
class FactorConfig:
    momentum_window: int = 20
    volatility_window: int = 20
    trend_window: int = 60


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "multifactor_rank"
    top_n: int = 3
    rebalance_every: int = 5
    fast_window: int = 20
    slow_window: int = 60
    volatility_window: int = 20
    max_annualized_volatility: float = 1.5
    stop_loss_pct: float = 0.05
    long_weight: float = 0.30
    short_weight: float = 0.30
    signal_bars: tuple[str, ...] = ("1m", "5m", "15m")
    pivot_lookback: int = 8
    pivot_lookback_min: int = 5
    pivot_lookback_max: int = 10
    pivot_timeframe: str = "1m"
    touch_atr_multiple: float = 0.25
    stop_atr_multiple: float = 0.75
    minimum_reward_risk: float = 1.5
    trend_fast_window: int = 20
    trend_slow_window: int = 60
    entry_fast_window: int = 5
    entry_slow_window: int = 10
    adx_window: int = 14
    max_adx_for_reversal: float = 22.0
    frequency_test_mode: bool = False
    risk_profile: str = "aggressive"


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    fee_bps: float = 3.0
    slippage_bps: float = 5.0
    annualization: int = 252
    fee_rebate_rate: float = 0.20


@dataclass(frozen=True)
class RiskConfig:
    max_position: float = 0.35
    max_gross_exposure: float = 1.0
    max_industry_exposure: float = 0.6
    max_drawdown: float = 0.2
    risk_per_trade: float = 0.002
    daily_loss_limit: float = 0.0075
    daily_profit_stop: float = 0.02
    max_consecutive_losses: int = 2
    max_trades_per_day: int = 24
    cooldown_seconds: int = 300
    min_cost_edge_multiple: float = 2.0


@dataclass(frozen=True)
class OkxConfig:
    environment: str = "demo"
    instruments: tuple[str, ...] = ("ETH-USDT-SWAP",)
    margin_mode: str = "cross"
    leverage: int = 100
    max_contracts_per_order: int = 1
    enable_demo_orders: bool = False
    validation_take_profit_pct: float = 0.0020
    validation_stop_loss_pct: float = 0.0015
    validation_trailing_callback_pct: float = 0.0005
    validation_max_trades_per_day: int = 200
    validation_max_leverage: int = 10
    validation_contracts: int = 1


@dataclass(frozen=True)
class OutputConfig:
    root: str = "artifacts"


@dataclass(frozen=True)
class AppConfig:
    data: DataConfig
    factors: FactorConfig
    strategy: StrategyConfig
    backtest: BacktestConfig
    risk: RiskConfig
    okx: OkxConfig
    output: OutputConfig


def _build(cls, values: dict):
    if cls is OkxConfig:
        # v0.7.12 retired channel-position rules.  Existing user workspaces may
        # still contain these app-generated keys, so ignore only this explicit
        # compatibility allow-list while keeping all other unknown keys strict.
        values = {key: value for key, value in values.items()
                  if key not in RETIRED_OKX_CONFIG_KEYS}
    allowed = {f.name for f in fields(cls)}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {sorted(unknown)}")
    if cls is DataConfig and "symbols" in values:
        values = {**values, "symbols": tuple(values["symbols"])}
    if cls is StrategyConfig and "signal_bars" in values:
        values = {**values, "signal_bars": tuple(values["signal_bars"])}
    if cls is OkxConfig:
        if "instrument" in values and "instruments" not in values:
            values = {**values, "instruments": (values.pop("instrument"),)}
        elif "instruments" in values:
            values = {**values, "instruments": tuple(values["instruments"])}
    return cls(**values)


def load_config(path: str | Path) -> AppConfig:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    required = {"data", "factors", "strategy", "backtest", "risk", "okx", "output"}
    if set(raw) != required:
        raise ValueError(f"Config sections must be exactly {sorted(required)}")
    cfg = AppConfig(
        _build(DataConfig, raw["data"]), _build(FactorConfig, raw["factors"]),
        _build(StrategyConfig, raw["strategy"]), _build(BacktestConfig, raw["backtest"]),
        _build(RiskConfig, raw["risk"]), _build(OkxConfig, raw["okx"]), _build(OutputConfig, raw["output"]),
    )
    validate_config(cfg)
    return cfg


def validate_config(cfg: AppConfig) -> None:
    if cfg.data.source not in {"synthetic", "csv", "okx"}:
        raise ValueError("data.source must be synthetic, csv, or okx")
    if not 100 <= cfg.data.history_limit <= 5_000:
        raise ValueError("data.history_limit must be between 100 and 5000")
    if min(cfg.factors.momentum_window, cfg.factors.volatility_window, cfg.factors.trend_window) < 2:
        raise ValueError("factor windows must be >= 2")
    if cfg.strategy.name not in {"multifactor_rank", "dual_ma_trend", "range_pivot_reversal"}:
        raise ValueError("Unknown strategy name")
    from .risk_profiles import normalize_profile
    normalize_profile(cfg.strategy.risk_profile)
    if cfg.strategy.top_n < 1 or cfg.strategy.rebalance_every < 1:
        raise ValueError("strategy values must be positive")
    if not 2 <= cfg.strategy.fast_window < cfg.strategy.slow_window:
        raise ValueError("trend windows must satisfy 2 <= fast < slow")
    if cfg.strategy.volatility_window < 2 or cfg.strategy.max_annualized_volatility <= 0:
        raise ValueError("invalid volatility filter")
    if not 0 < cfg.strategy.stop_loss_pct < 1:
        raise ValueError("stop_loss_pct must be in (0, 1)")
    if not 0 < cfg.strategy.long_weight <= 1 or not 0 < cfg.strategy.short_weight <= 1:
        raise ValueError("trend weights must be in (0, 1]")
    if not cfg.strategy.signal_bars or any(bar not in {"1m", "5m", "15m"} for bar in cfg.strategy.signal_bars):
        raise ValueError("strategy.signal_bars may only contain 1m, 5m, and 15m")
    if not 5 <= cfg.strategy.pivot_lookback_min <= cfg.strategy.pivot_lookback <= cfg.strategy.pivot_lookback_max <= 10:
        raise ValueError("pivot lookback must stay inside the configured 5-10 range")
    if cfg.strategy.pivot_timeframe not in {"1m", "5m", "15m", "30m", "1H"}:
        raise ValueError("pivot_timeframe must be 1m, 5m, 15m, 30m, or 1H")
    if not 0 < cfg.strategy.touch_atr_multiple <= 1:
        raise ValueError("touch_atr_multiple must be in (0, 1]")
    if not 0.5 <= cfg.strategy.stop_atr_multiple <= 2:
        raise ValueError("stop_atr_multiple must be between 0.5 and 2")
    if not 1 <= cfg.strategy.minimum_reward_risk <= 5:
        raise ValueError("minimum_reward_risk must be between 1 and 5")
    if not 2 <= cfg.strategy.trend_fast_window < cfg.strategy.trend_slow_window:
        raise ValueError("range trend windows must satisfy 2 <= fast < slow")
    if not 2 <= cfg.strategy.entry_fast_window < cfg.strategy.entry_slow_window:
        raise ValueError("range entry windows must satisfy 2 <= fast < slow")
    if not 5 <= cfg.strategy.adx_window <= 50 or not 5 <= cfg.strategy.max_adx_for_reversal <= 100:
        raise ValueError("range ADX parameters are outside their safe range")
    for value in (cfg.risk.max_position, cfg.risk.max_gross_exposure, cfg.risk.max_industry_exposure, cfg.risk.max_drawdown):
        if not 0 < value <= 1:
            raise ValueError("risk limits must be in (0, 1]")
    if not 0 < cfg.risk.risk_per_trade <= cfg.risk.daily_loss_limit < 1:
        raise ValueError("risk_per_trade must not exceed daily_loss_limit")
    if not 0 < cfg.risk.daily_profit_stop < 1:
        raise ValueError("daily_profit_stop must be in (0, 1)")
    if cfg.risk.max_consecutive_losses < 1 or cfg.risk.max_trades_per_day < 1:
        raise ValueError("intraday risk counters must be positive")
    if cfg.backtest.fee_bps < 0 or cfg.backtest.slippage_bps < 0:
        raise ValueError("costs cannot be negative")
    if not 0 <= cfg.backtest.fee_rebate_rate < 1:
        raise ValueError("fee_rebate_rate must be in [0, 1)")
    if cfg.risk.cooldown_seconds < 60 or cfg.risk.min_cost_edge_multiple < 1:
        raise ValueError("cooldown and cost edge controls are too small")
    if cfg.okx.environment != "demo":
        raise ValueError("This build only permits OKX demo trading")
    if not 1 <= len(cfg.okx.instruments) <= 10 or len(set(cfg.okx.instruments)) != len(cfg.okx.instruments):
        raise ValueError("OKX instruments must contain 1 to 10 unique contracts")
    if any(not item.endswith("-USDT-SWAP") or item != item.upper() for item in cfg.okx.instruments):
        raise ValueError("OKX instruments must use uppercase *-USDT-SWAP contract IDs")
    if cfg.okx.margin_mode != "cross":
        raise ValueError("OKX adapter is restricted to cross margin")
    if not 1 <= cfg.okx.leverage <= 100 or cfg.okx.max_contracts_per_order < 1:
        raise ValueError("Invalid OKX leverage or order cap")
    if not 0 < cfg.okx.validation_stop_loss_pct < cfg.okx.validation_take_profit_pct < 0.01:
        raise ValueError("validation TP/SL percentages are invalid")
    if not 0 < cfg.okx.validation_trailing_callback_pct < cfg.okx.validation_take_profit_pct:
        raise ValueError("validation trailing callback must be positive and below its activation profit")
    if not 1 <= cfg.okx.validation_max_trades_per_day <= 200:
        raise ValueError("Demo validation daily trade limit must be between 1 and 200")
    if not 1 <= cfg.okx.validation_max_leverage <= 10 or cfg.okx.validation_contracts != 1:
        raise ValueError("validation mode is restricted to one contract and at most 10x")
