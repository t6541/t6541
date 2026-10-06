from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    daily: pd.DataFrame
    metrics: dict[str, float]


def run_backtest(market: pd.DataFrame, targets: pd.DataFrame, initial_cash: float, fee_bps: float, slippage_bps: float, annualization: int, max_drawdown: float, fee_rebate_rate: float = 0.0) -> BacktestResult:
    close = market.pivot(index="date", columns="symbol", values="close").sort_index()
    targets = targets.reindex(index=close.index, columns=close.columns, fill_value=0.0).fillna(0.0)
    asset_returns = close.pct_change(fill_method=None).fillna(0.0)
    holdings = targets.shift(1).fillna(0.0)  # Close-t targets act on t -> t+1 only.
    planned_turnover = holdings.diff().abs().sum(axis=1)
    if len(planned_turnover):
        planned_turnover.iloc[0] = holdings.iloc[0].abs().sum()
    gross_fee_rate = fee_bps / 10_000.0
    rebate_rate = gross_fee_rate * fee_rebate_rate
    net_fee_rate = gross_fee_rate - rebate_rate
    slippage_rate = slippage_bps / 10_000.0
    equity, peak, active = initial_cash, initial_cash, True
    rows = []
    previous = pd.Series(0.0, index=close.columns)
    for date in close.index:
        desired = holdings.loc[date] if active else holdings.loc[date] * 0.0
        turnover = float((desired - previous).abs().sum())
        gross = float((desired * asset_returns.loc[date]).sum())
        gross_fee = turnover * gross_fee_rate
        fee_rebate = turnover * rebate_rate
        net_fee = turnover * net_fee_rate
        slippage = turnover * slippage_rate
        cost = net_fee + slippage
        net = gross - cost
        equity *= 1.0 + net
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0
        if drawdown <= -max_drawdown:
            active = False
        rows.append((gross, turnover, gross_fee, fee_rebate, net_fee, slippage, cost, net, equity, drawdown, not active))
        previous = desired
    daily = pd.DataFrame(rows, index=close.index, columns=["gross_return", "turnover", "gross_fee", "fee_rebate", "net_fee", "slippage", "cost", "net_return", "equity", "drawdown", "circuit_breaker"])
    series = daily["net_return"]
    total = daily["equity"].iloc[-1] / initial_cash - 1.0 if len(daily) else 0.0
    years = len(daily) / annualization
    annual_return = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1.0
    std = series.std(ddof=1)
    annual_vol = float(std * np.sqrt(annualization)) if len(series) > 1 else 0.0
    sharpe = float(series.mean() / std * np.sqrt(annualization)) if std > 0 else 0.0
    metrics = {"total_return": float(total), "annual_return": float(annual_return), "annual_volatility": annual_vol, "sharpe": sharpe, "max_drawdown": float(daily["drawdown"].min()), "gross_fee": float(daily["gross_fee"].sum()), "fee_rebate": float(daily["fee_rebate"].sum()), "net_fee": float(daily["net_fee"].sum()), "total_slippage": float(daily["slippage"].sum()), "total_cost": float(daily["cost"].sum())}
    return BacktestResult(daily, metrics)
