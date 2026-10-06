"""Persistent, public-data-only paper trading ledger."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
import pandas as pd


@dataclass(frozen=True)
class PaperMetrics:
    timestamp: str | None
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    slippage: float
    drawdown: float
    positions: dict[str, float]


class PaperLedger:
    """Incremental mark-to-market ledger; it never sends exchange requests."""

    def __init__(self, path: str | Path, *, initial_cash: float = 10_000.0,
                 fee_bps: float = 5.0, slippage_bps: float = 2.0):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS paper_state (
            id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL NOT NULL,
            equity REAL NOT NULL, peak_equity REAL NOT NULL, realized_pnl REAL NOT NULL,
            fees REAL NOT NULL, slippage REAL NOT NULL, last_timestamp TEXT,
            positions_json TEXT NOT NULL, prices_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_snapshots (
            timestamp TEXT PRIMARY KEY, equity REAL NOT NULL, gross_return REAL NOT NULL,
            turnover REAL NOT NULL, fees REAL NOT NULL, slippage REAL NOT NULL,
            drawdown REAL NOT NULL, positions_json TEXT NOT NULL);
        """)
        row = self.db.execute("SELECT id FROM paper_state WHERE id=1").fetchone()
        if row is None:
            import json
            self.db.execute("INSERT INTO paper_state VALUES(1,?,?,?,?,?,?,?, ?,?)",
                            (float(initial_cash), float(initial_cash), float(initial_cash),
                             0.0, 0.0, 0.0, None, json.dumps({}), json.dumps({})))
            self.db.commit()
        self.fee_rate = float(fee_bps) / 10_000.0
        self.slippage_rate = float(slippage_bps) / 10_000.0

    def update(self, market: pd.DataFrame, targets: pd.DataFrame | None = None) -> PaperMetrics:
        """Apply new confirmed OHLC rows; targets are fractional long/short weights."""
        required = {"date", "symbol", "close"}
        if not required.issubset(market.columns):
            raise ValueError(f"market must contain {sorted(required)}")
        import json
        state = self.db.execute("SELECT * FROM paper_state WHERE id=1").fetchone()
        positions = json.loads(state["positions_json"]); prices = json.loads(state["prices_json"])
        last = state["last_timestamp"]
        frame = market[["date", "symbol", "close"]].copy()
        frame["date"] = pd.to_datetime(frame["date"], utc=True).astype(str)
        frame["close"] = pd.to_numeric(frame["close"], errors="raise")
        frame = frame.sort_values(["date", "symbol"])
        targets = targets if targets is not None else pd.DataFrame()
        target_lookup = {}
        for i, row in targets.iterrows():
            target_time = pd.Timestamp(i)
            target_time = (target_time.tz_localize("UTC") if target_time.tzinfo is None
                           else target_time.tz_convert("UTC"))
            target_lookup[str(target_time)] = row.to_dict()
        for timestamp, group in frame.groupby("date", sort=True):
            if last is not None and timestamp <= last:
                continue
            current = {str(r.symbol): float(r.close) for r in group.itertuples()}
            gross = 0.0
            for symbol, price in current.items():
                if symbol in prices and float(prices[symbol]):
                    gross += positions.get(symbol, 0.0) * (price / float(prices[symbol]) - 1.0)
            desired = {s: float(v) for s, v in target_lookup.get(timestamp, {}).items() if float(v) != 0.0}
            turnover = sum(abs(desired.get(s, 0.0) - positions.get(s, 0.0)) for s in set(desired) | set(positions))
            fees = turnover * self.fee_rate
            slippage = turnover * self.slippage_rate
            equity = float(state["equity"]) * (1.0 + gross - fees - slippage)
            peak = max(float(state["peak_equity"]), equity)
            drawdown = equity / peak - 1.0 if peak else 0.0
            self.db.execute("INSERT OR IGNORE INTO paper_snapshots VALUES(?,?,?,?,?,?,?,?)",
                            (timestamp, equity, gross, turnover, fees, slippage, drawdown, json.dumps(desired, sort_keys=True)))
            positions, prices, last = desired, current, timestamp
            state = dict(state); state.update(equity=equity, peak_equity=peak,
                fees=float(state["fees"]) + fees, slippage=float(state["slippage"]) + slippage,
                last_timestamp=last, positions_json=json.dumps(positions, sort_keys=True),
                prices_json=json.dumps(prices, sort_keys=True))
        self.db.execute("UPDATE paper_state SET equity=?,peak_equity=?,fees=?,slippage=?,last_timestamp=?,positions_json=?,prices_json=? WHERE id=1",
                        (state["equity"], state["peak_equity"], state["fees"], state["slippage"], state["last_timestamp"], state["positions_json"], state["prices_json"]))
        self.db.commit()
        return self.metrics()

    def metrics(self) -> PaperMetrics:
        import json
        state = self.db.execute("SELECT * FROM paper_state WHERE id=1").fetchone()
        prices = json.loads(state["prices_json"]); positions = json.loads(state["positions_json"])
        unrealized = sum(positions.get(s, 0.0) * (float(p) / float(p) - 1.0) for s, p in prices.items())
        drawdown = float(state["equity"]) / float(state["peak_equity"]) - 1.0 if state["peak_equity"] else 0.0
        return PaperMetrics(state["last_timestamp"], float(state["equity"]), float(state["realized_pnl"]), unrealized, float(state["fees"]), float(state["slippage"]), drawdown, positions)
