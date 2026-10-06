"""Isolated SQLite lot ledger for account 05.

OKX aggregates same-side positions.  This ledger retains virtual ownership so
each small entry can have its own reduce-only take-profit and reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
import json

from .account05_strategy import PositionSide


@dataclass(frozen=True)
class Account05Lot:
    lot_id: str
    side: PositionSide
    kind: str
    entry_order_id: str
    entry_price: Decimal
    original_size: Decimal
    remaining_size: Decimal
    take_profit_price: Decimal
    take_profit_pct: Decimal
    take_profit_algo_id: str | None
    status: str


class Account05StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS account05_entry_reviews (
                signal_id TEXT PRIMARY KEY,
                evidence_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_virtual_adjustments (
                lot_id TEXT PRIMARY KEY, excluded_size TEXT NOT NULL,
                reason TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
                closed_baseline TEXT NOT NULL DEFAULT '0'
            );
            CREATE TABLE IF NOT EXISTS account05_lots (
                lot_id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL UNIQUE,
                side TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('base','addon')),
                entry_order_id TEXT NOT NULL UNIQUE,
                entry_price TEXT NOT NULL,
                original_size TEXT NOT NULL,
                remaining_size TEXT NOT NULL,
                take_profit_price TEXT NOT NULL,
                take_profit_pct TEXT NOT NULL DEFAULT '0',
                take_profit_algo_id TEXT UNIQUE,
                status TEXT NOT NULL CHECK(status IN
                    ('filled_unprotected','tp_live','partially_closed','closed','reconcile_required')),
                exit_order_id TEXT NOT NULL DEFAULT '',
                exit_price TEXT NOT NULL DEFAULT '',
                local_gross_pnl TEXT NOT NULL DEFAULT '',
                local_fee_estimate TEXT NOT NULL DEFAULT '',
                local_net_pnl TEXT NOT NULL DEFAULT '',
                exchange_realized_pnl TEXT NOT NULL DEFAULT '',
                exchange_fee TEXT NOT NULL DEFAULT '',
                closed_at_utc TEXT NOT NULL DEFAULT '',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_account05_lots_open
            ON account05_lots(side,kind,status);
            CREATE TABLE IF NOT EXISTS account05_order_intents (
                signal_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL UNIQUE,
                side TEXT NOT NULL,
                kind TEXT NOT NULL,
                requested_size TEXT NOT NULL,
                status TEXT NOT NULL,
                exchange_order_id TEXT,
                detail TEXT NOT NULL DEFAULT '',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_signal_latches (
                identity TEXT NOT NULL,
                side TEXT NOT NULL,
                active INTEGER NOT NULL CHECK(active IN (0,1)),
                last_anchor TEXT NOT NULL DEFAULT '',
                last_five_bar TEXT NOT NULL DEFAULT '',
                missing_since_bar TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY(identity,side)
            );
            CREATE TABLE IF NOT EXISTS account05_side_five_locks (
                side TEXT PRIMARY KEY,
                last_five_bar TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_side_five_groups (
                side TEXT NOT NULL,
                five_bar TEXT NOT NULL,
                entry_group TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY(side,five_bar,entry_group)
            );
            CREATE TABLE IF NOT EXISTS account05_tp_replacements (
                lot_id TEXT PRIMARY KEY,
                old_order_id TEXT NOT NULL,
                new_client_order_id TEXT NOT NULL UNIQUE,
                new_order_id TEXT UNIQUE,
                new_price TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('planned','new_live')),
                updated_at_utc TEXT NOT NULL,
                FOREIGN KEY(lot_id) REFERENCES account05_lots(lot_id)
            );
            CREATE TABLE IF NOT EXISTS account05_recovery_program (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                active INTEGER NOT NULL CHECK(active IN (0,1)),
                initial_equity TEXT NOT NULL,
                target_equity TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('balance_short','adjust','complete')),
                started_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_recovery_trapped_lots (
                lot_id TEXT PRIMARY KEY,
                FOREIGN KEY(lot_id) REFERENCES account05_lots(lot_id)
            );
            CREATE TABLE IF NOT EXISTS account05_base_rebuild_plans (
                side TEXT PRIMARY KEY,
                signal_key TEXT NOT NULL UNIQUE,
                target_margin_pct TEXT NOT NULL,
                first_margin_pct TEXT NOT NULL,
                take_profit_pct TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('stage1_planned','stage1_filled','complete')),
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_recovery_pool (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                pool_balance TEXT NOT NULL,
                reserve_balance TEXT NOT NULL,
                total_net_profit TEXT NOT NULL,
                total_loss_paid TEXT NOT NULL,
                total_uncovered_loss TEXT NOT NULL DEFAULT '0',
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_recovery_resets (
                reset_at_utc TEXT PRIMARY KEY,
                clear_balances INTEGER NOT NULL CHECK(clear_balances IN (0,1)),
                prior_values_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_addon_exit_latches (
                side TEXT PRIMARY KEY,
                last_event_key TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_extreme_rotation_events (
                event_key TEXT PRIMARY KEY,
                direction INTEGER NOT NULL CHECK(direction IN (-1,1)),
                status TEXT NOT NULL DEFAULT 'claimed' CHECK(status IN ('claimed','complete')),
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_manual_order_history (
                order_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account05_extreme_targets (
                event_key TEXT NOT NULL,
                lot_id TEXT NOT NULL,
                target_size TEXT NOT NULL,
                PRIMARY KEY(event_key,lot_id)
            );
            CREATE TABLE IF NOT EXISTS account05_extreme_exit_fills (
                lot_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                filled_size TEXT NOT NULL,
                exit_price TEXT NOT NULL,
                PRIMARY KEY(lot_id,order_id)
            );
            CREATE TABLE IF NOT EXISTS account05_profit_credits (
                credit_key TEXT PRIMARY KEY
            );
            """
        )
        adjustment_columns = {str(row["name"]) for row in self.connection.execute(
            "PRAGMA table_info(account05_virtual_adjustments)")}
        if "closed_baseline" not in adjustment_columns:
            self.connection.execute("ALTER TABLE account05_virtual_adjustments ADD COLUMN closed_baseline TEXT NOT NULL DEFAULT '0'")
        columns = {str(row["name"]) for row in self.connection.execute(
            "PRAGMA table_info(account05_signal_latches)").fetchall()}
        if "last_anchor" not in columns:
            self.connection.execute(
                "ALTER TABLE account05_signal_latches ADD COLUMN last_anchor TEXT NOT NULL DEFAULT ''")
        if "missing_since_bar" not in columns:
            self.connection.execute(
                "ALTER TABLE account05_signal_latches ADD COLUMN missing_since_bar TEXT NOT NULL DEFAULT ''")
        if "last_five_bar" not in columns:
            self.connection.execute(
                "ALTER TABLE account05_signal_latches ADD COLUMN last_five_bar TEXT NOT NULL DEFAULT ''")
        lot_columns = {str(row["name"]) for row in self.connection.execute(
            "PRAGMA table_info(account05_lots)").fetchall()}
        if "take_profit_pct" not in lot_columns:
            self.connection.execute(
                "ALTER TABLE account05_lots ADD COLUMN take_profit_pct TEXT NOT NULL DEFAULT '0'")
        for name in (
                "exit_order_id", "exit_price", "local_gross_pnl",
                "local_fee_estimate", "local_net_pnl",
                "exchange_realized_pnl", "exchange_fee", "closed_at_utc"):
            if name not in lot_columns:
                self.connection.execute(
                    f"ALTER TABLE account05_lots ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        pool_columns = {str(row["name"]) for row in self.connection.execute(
            "PRAGMA table_info(account05_recovery_pool)").fetchall()}
        if "total_uncovered_loss" not in pool_columns:
            self.connection.execute(
                "ALTER TABLE account05_recovery_pool "
                "ADD COLUMN total_uncovered_loss TEXT NOT NULL DEFAULT '0'")
        extreme_columns = {str(row["name"]) for row in self.connection.execute(
            "PRAGMA table_info(account05_extreme_rotation_events)").fetchall()}
        if "status" not in extreme_columns:
            self.connection.execute(
                "ALTER TABLE account05_extreme_rotation_events "
                "ADD COLUMN status TEXT NOT NULL DEFAULT 'claimed'")
        if "plan_recorded" not in extreme_columns:
            self.connection.execute(
                "ALTER TABLE account05_extreme_rotation_events ADD COLUMN plan_recorded INTEGER NOT NULL DEFAULT 0")
        if "reverse_signal_id" not in extreme_columns:
            self.connection.execute(
                "ALTER TABLE account05_extreme_rotation_events ADD COLUMN reverse_signal_id TEXT NOT NULL DEFAULT ''")
        self._seed_signal_latches_from_history()
        self.connection.commit()

    def _seed_signal_latches_from_history(self) -> None:
        """Prevent an upgrade from replaying a shape already traded before latches existed."""
        count = int(self.connection.execute(
            "SELECT COUNT(*) FROM account05_signal_latches").fetchone()[0])
        if count:
            return
        now = datetime.now(timezone.utc).isoformat()
        rows = self.connection.execute(
            """SELECT signal_id,side FROM account05_order_intents
            WHERE kind='addon' ORDER BY created_at_utc""").fetchall()
        for row in rows:
            signal_id, side = str(row["signal_id"]), str(row["side"])
            marker = f":{side}:"
            if not signal_id.startswith("addon:") or marker not in signal_id:
                continue
            identity = signal_id[len("addon:"):].split(marker, 1)[0]
            self.connection.execute(
                """INSERT OR REPLACE INTO account05_signal_latches
                (identity,side,active,last_anchor,last_five_bar,missing_since_bar,updated_at_utc)
                VALUES(?,?,1,'','','',?)""", (identity, side, now),
            )

    @staticmethod
    def _lot(row: sqlite3.Row) -> Account05Lot:
        return Account05Lot(
            lot_id=str(row["lot_id"]), side=PositionSide(str(row["side"])),
            kind=str(row["kind"]), entry_order_id=str(row["entry_order_id"]),
            entry_price=Decimal(str(row["entry_price"])),
            original_size=Decimal(str(row["original_size"])),
            remaining_size=Decimal(str(row["remaining_size"])),
            take_profit_price=Decimal(str(row["take_profit_price"])),
            take_profit_pct=Decimal(str(row["take_profit_pct"] or "0")),
            take_profit_algo_id=(str(row["take_profit_algo_id"])
                                 if row["take_profit_algo_id"] else None),
            status=str(row["status"]),
        )

    def record_filled_lot(self, *, lot_id: str, signal_id: str,
                          side: PositionSide, kind: str, entry_order_id: str,
                          entry_price: Decimal, size: Decimal,
                          take_profit_price: Decimal,
                          take_profit_pct: Decimal = Decimal("0")) -> Account05Lot:
        """Idempotently record a confirmed fill before placing its TP."""
        if kind not in {"base", "addon"}:
            raise ValueError("kind must be base or addon")
        if min(entry_price, size, take_profit_price) <= 0:
            raise ValueError("fill price, size and take-profit price must be positive")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT OR IGNORE INTO account05_lots
            (lot_id,signal_id,side,kind,entry_order_id,entry_price,original_size,
             remaining_size,take_profit_price,take_profit_pct,take_profit_algo_id,status,
             created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'filled_unprotected',?,?)""",
            (lot_id, signal_id, PositionSide(side).value, kind, entry_order_id,
             str(entry_price), str(size), str(size), str(take_profit_price),
             str(take_profit_pct), now, now),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM account05_lots WHERE signal_id=?", (signal_id,)).fetchone()
        assert row is not None
        lot = self._lot(row)
        if (lot.lot_id != lot_id or lot.entry_order_id != entry_order_id
                or lot.side is not PositionSide(side)):
            raise ValueError("signal_id already belongs to a different lot")
        return lot

    def claim_order_intent(self, *, signal_id: str, client_order_id: str,
                           side: PositionSide, kind: str,
                           requested_size: Decimal) -> bool:
        """Return True only for the first attempt of a durable signal."""
        if kind not in {"base", "addon", "addon_ma5_exit"} or requested_size <= 0:
            raise ValueError("invalid account05 order intent")
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO account05_order_intents
            (signal_id,client_order_id,side,kind,requested_size,status,
             exchange_order_id,detail,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,'claimed',NULL,'',?,?)""",
            (signal_id, client_order_id, PositionSide(side).value, kind,
             str(requested_size), now, now),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_order_intent(self, signal_id: str, status: str, *,
                            exchange_order_id: str = "", detail: str = "") -> None:
        # reconcile_required is a durable quarantine state: the exchange
        # result needs review, but it must not crash the worker while writing
        # that state.
        allowed = {"claimed", "submitted", "filled", "rejected", "unknown",
                   "reconcile_required"}
        if status not in allowed:
            raise ValueError("invalid account05 order status")
        detail = str(detail or "")
        # Reconciliation details are diagnostic only. Bound them so repeated
        # polling cannot grow one SQLite row without limit.
        if len(detail) > 2000:
            detail = detail[-2000:]
        cursor = self.connection.execute(
            """UPDATE account05_order_intents SET status=?,exchange_order_id=?,detail=?,
            updated_at_utc=? WHERE signal_id=?""",
            (status, exchange_order_id or None, detail,
             datetime.now(timezone.utc).isoformat(), signal_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(signal_id)

    def order_intent(self, signal_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM account05_order_intents WHERE signal_id=?", (signal_id,)
        ).fetchone()

    def attach_take_profit(self, lot_id: str, algo_id: str) -> Account05Lot:
        """Bind exactly one exchange TP to a lot after its API acknowledgement."""
        algo_id = str(algo_id).strip()
        if not algo_id:
            raise ValueError("algo_id is required")
        now = datetime.now(timezone.utc).isoformat()
        row = self.connection.execute(
            "SELECT * FROM account05_lots WHERE lot_id=?", (lot_id,)).fetchone()
        if row is None:
            raise KeyError(lot_id)
        existing = row["take_profit_algo_id"]
        if existing and str(existing) != algo_id:
            raise ValueError("lot already has a different take-profit order")
        self.connection.execute(
            """UPDATE account05_lots SET take_profit_algo_id=?,status='tp_live',updated_at_utc=?
            WHERE lot_id=?""", (algo_id, now, lot_id))
        self.connection.commit()
        return self.get_lot(lot_id)

    def set_base_take_profit_pct(self, lot_id: str, profit_pct: Decimal) -> Account05Lot:
        lot = self.get_lot(lot_id)
        if lot.kind != "base" or lot.status == "closed" or profit_pct <= 0:
            raise ValueError("invalid base take-profit percentage")
        self.connection.execute(
            """UPDATE account05_lots SET take_profit_pct=?,updated_at_utc=?
            WHERE lot_id=?""",
            (str(profit_pct), datetime.now(timezone.utc).isoformat(), lot_id))
        self.connection.commit()
        return self.get_lot(lot_id)

    def plan_take_profit_replacement(self, lot_id: str, *, old_order_id: str,
                                     new_client_order_id: str,
                                     new_price: Decimal) -> sqlite3.Row:
        """Durably record replacement intent before submitting the new TP."""
        lot = self.get_lot(lot_id)
        if (lot.kind != "base" or lot.status == "closed"
                or lot.take_profit_algo_id != old_order_id or new_price <= 0):
            raise ValueError("invalid base take-profit replacement")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT OR IGNORE INTO account05_tp_replacements
            (lot_id,old_order_id,new_client_order_id,new_order_id,new_price,status,updated_at_utc)
            VALUES(?,?,?,NULL,?,'planned',?)""",
            (lot_id, old_order_id, new_client_order_id, str(new_price), now),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM account05_tp_replacements WHERE lot_id=?", (lot_id,)
        ).fetchone()
        assert row is not None
        return row

    def mark_replacement_new_live(self, lot_id: str, new_order_id: str) -> sqlite3.Row:
        new_order_id = str(new_order_id).strip()
        if not new_order_id:
            raise ValueError("new_order_id is required")
        cursor = self.connection.execute(
            """UPDATE account05_tp_replacements SET new_order_id=?,status='new_live',
            updated_at_utc=? WHERE lot_id=?""",
            (new_order_id, datetime.now(timezone.utc).isoformat(), lot_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(lot_id)
        return self.connection.execute(
            "SELECT * FROM account05_tp_replacements WHERE lot_id=?", (lot_id,)
        ).fetchone()

    def pending_take_profit_replacements(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM account05_tp_replacements ORDER BY updated_at_utc"
        ).fetchall()

    def complete_take_profit_replacement(self, lot_id: str) -> Account05Lot:
        row = self.connection.execute(
            "SELECT * FROM account05_tp_replacements WHERE lot_id=?", (lot_id,)
        ).fetchone()
        if row is None or str(row["status"]) != "new_live" or not row["new_order_id"]:
            raise ValueError("replacement is not ready to complete")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """UPDATE account05_lots SET take_profit_algo_id=?,take_profit_price=?,
                status='tp_live',updated_at_utc=? WHERE lot_id=?""",
                (str(row["new_order_id"]), str(row["new_price"]), now, lot_id),
            )
            self.connection.execute(
                "DELETE FROM account05_tp_replacements WHERE lot_id=?", (lot_id,))
        return self.get_lot(lot_id)

    def complete_take_profit_amendment(self, lot_id: str) -> Account05Lot:
        """Commit an in-place price amendment while preserving the order id."""
        row = self.connection.execute(
            "SELECT * FROM account05_tp_replacements WHERE lot_id=?", (lot_id,)
        ).fetchone()
        if row is None:
            raise KeyError(lot_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """UPDATE account05_lots SET take_profit_price=?,status='tp_live',
                updated_at_utc=? WHERE lot_id=? AND take_profit_algo_id=?""",
                (str(row["new_price"]), now, lot_id, str(row["old_order_id"])),
            )
            self.connection.execute(
                "DELETE FROM account05_tp_replacements WHERE lot_id=?", (lot_id,))
        return self.get_lot(lot_id)

    def abort_take_profit_replacement(self, lot_id: str) -> None:
        self.connection.execute(
            "DELETE FROM account05_tp_replacements WHERE lot_id=?", (lot_id,))
        self.connection.commit()

    def apply_take_profit_fill(self, lot_id: str, filled_size: Decimal) -> Account05Lot:
        """Reduce only this virtual lot; reject exchange overfills for reconciliation."""
        if filled_size <= 0:
            raise ValueError("filled_size must be positive")
        lot = self.get_lot(lot_id)
        remaining = lot.remaining_size - filled_size
        now = datetime.now(timezone.utc).isoformat()
        if remaining < 0:
            self.connection.execute(
                "UPDATE account05_lots SET status='reconcile_required',updated_at_utc=? WHERE lot_id=?",
                (now, lot_id))
            self.connection.commit()
            raise ValueError("take-profit fill exceeds the lot remaining size")
        status = "closed" if remaining == 0 else "partially_closed"
        self.connection.execute(
            """UPDATE account05_lots SET remaining_size=?,status=?,updated_at_utc=?
            WHERE lot_id=?""", (str(remaining), status, now, lot_id))
        self.connection.commit()
        return self.get_lot(lot_id)

    def virtual_excluded_size(self, lot_id: str, historical_closed: Decimal | None = None) -> Decimal:
        row = self.connection.execute(
            "SELECT excluded_size,closed_baseline FROM account05_virtual_adjustments WHERE lot_id=?",
            (lot_id,)).fetchone()
        if not row:
            return Decimal(0)
        excluded = Decimal(row[0])
        if historical_closed is not None:
            # Delayed close history confirms some of the earlier virtual correction.
            # Absorb it, rather than counting that reduction twice.
            excluded -= max(Decimal(0), historical_closed - Decimal(row[1]))
        return max(Decimal(0), excluded)

    def trim_virtual_quantity(self, lot_id: str, quantity: Decimal, reason: str) -> None:
        """Retire unmatched virtual exposure, never fabricate a trade or PnL."""
        lot = self.get_lot(lot_id)
        if not quantity.is_finite() or quantity <= 0 or quantity > lot.remaining_size:
            raise ValueError("invalid virtual quantity adjustment")
        remaining = lot.remaining_size - quantity
        original = lot.original_size - quantity
        now = datetime.now(timezone.utc).isoformat()
        excluded = self.virtual_excluded_size(lot_id) + quantity
        previous = self.connection.execute(
            "SELECT closed_baseline FROM account05_virtual_adjustments WHERE lot_id=?", (lot_id,)).fetchone()
        baseline = str(previous[0]) if previous else str(lot.original_size - lot.remaining_size)
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO account05_virtual_adjustments VALUES (?,?,?,?,?)",
                (lot_id, str(excluded), reason, now, baseline))
            self.connection.execute(
                """UPDATE account05_lots SET original_size=?,remaining_size=?,
                status=?,closed_at_utc=?,updated_at_utc=?,take_profit_algo_id=
                CASE WHEN ?=1 THEN NULL ELSE take_profit_algo_id END WHERE lot_id=?""",
                (str(original), str(remaining), "closed" if remaining == 0 else lot.status,
                 now if remaining == 0 else "", now, int(remaining == 0), lot_id))
            if remaining == 0:
                self.connection.execute("DELETE FROM account05_tp_replacements WHERE lot_id=?", (lot_id,))

    def sync_take_profit_fill(self, lot_id: str, cumulative_filled: Decimal) -> Account05Lot:
        """Set remaining size from the exchange cumulative fill quantity."""
        lot = self.get_lot(lot_id)
        if cumulative_filled < 0 or cumulative_filled > lot.original_size:
            self.connection.execute(
                "UPDATE account05_lots SET status='reconcile_required',updated_at_utc=? WHERE lot_id=?",
                (datetime.now(timezone.utc).isoformat(), lot_id))
            self.connection.commit()
            raise ValueError("exchange cumulative TP fill is outside the lot size")
        remaining = lot.original_size - cumulative_filled
        status = ("closed" if remaining == 0 else
                  "partially_closed" if cumulative_filled > 0 else "tp_live")
        self.connection.execute(
            "UPDATE account05_lots SET remaining_size=?,status=?,updated_at_utc=? WHERE lot_id=?",
            (str(remaining), status, datetime.now(timezone.utc).isoformat(), lot_id))
        self.connection.commit()
        return self.get_lot(lot_id)

    def mark_take_profit_missing(self, lot_id: str) -> Account05Lot:
        self.connection.execute(
            """UPDATE account05_lots SET take_profit_algo_id=NULL,
            status='filled_unprotected',updated_at_utc=? WHERE lot_id=?""",
            (datetime.now(timezone.utc).isoformat(), lot_id))
        self.connection.commit()
        return self.get_lot(lot_id)

    def mark_addon_ma5_managed(self, lot_id: str) -> Account05Lot:
        lot = self.get_lot(lot_id)
        if lot.kind != "addon" or lot.status == "closed":
            raise ValueError("only an open addon can use MA5 management")
        self.connection.execute(
            """UPDATE account05_lots SET take_profit_algo_id=NULL,status='tp_live',
            updated_at_utc=? WHERE lot_id=?""",
            (datetime.now(timezone.utc).isoformat(), lot_id))
        self.connection.commit()
        return self.get_lot(lot_id)

    def record_addon_exit_accounting(
            self, lot_id: str, *, exit_order_id: str, exit_price: Decimal,
            local_gross_pnl: Decimal, local_fee_estimate: Decimal,
            local_net_pnl: Decimal, exchange_realized_pnl: Decimal | None = None,
            exchange_fee: Decimal | None = None) -> None:
        """Persist the local virtual-lot result beside OKX partial-close PnL."""
        lot = self.get_lot(lot_id)
        if lot.kind != "addon" or exit_price <= 0 or not str(exit_order_id).strip():
            raise ValueError("invalid addon exit accounting")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """UPDATE account05_lots SET exit_order_id=?,exit_price=?,
            local_gross_pnl=?,local_fee_estimate=?,local_net_pnl=?,
            exchange_realized_pnl=?,exchange_fee=?,closed_at_utc=?,updated_at_utc=?
            WHERE lot_id=?""",
            (str(exit_order_id), str(exit_price), str(local_gross_pnl),
             str(local_fee_estimate), str(local_net_pnl),
             "" if exchange_realized_pnl is None else str(exchange_realized_pnl),
             "" if exchange_fee is None else str(exchange_fee),
             now, now, lot_id))
        self.connection.commit()

    def record_entry_review(self, signal_id: str, evidence: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO account05_entry_reviews VALUES (?,?)",
                (signal_id, json.dumps(evidence, ensure_ascii=False, allow_nan=False)))

    def entry_review(self, signal_id: str) -> str:
        row = self.connection.execute(
            "SELECT evidence_json FROM account05_entry_reviews WHERE signal_id=?",
            (signal_id,)).fetchone()
        return str(row[0]) if row else ""

    def entry_review_lot_rows(self, limit: int = 1000) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT l.*, entry.exchange_order_id AS entry_exchange_order_id,
            exit_intent.exchange_order_id AS exit_exchange_order_id,
            COALESCE(exit_intent.detail,'') || CASE WHEN adjustment.reason IS NULL THEN '' ELSE '；' || adjustment.reason END AS exit_reason, review.evidence_json
            FROM account05_lots l
            LEFT JOIN account05_virtual_adjustments adjustment ON adjustment.lot_id=l.lot_id
            LEFT JOIN account05_order_intents entry ON entry.signal_id=
                CASE WHEN substr(l.signal_id,-3)='-T1'
                     THEN substr(l.signal_id,1,length(l.signal_id)-3) ELSE l.signal_id END
            LEFT JOIN account05_entry_reviews review ON review.signal_id=entry.signal_id
            LEFT JOIN account05_order_intents exit_intent ON exit_intent.signal_id='exit:' || l.lot_id
            ORDER BY l.created_at_utc DESC LIMIT ?""", (int(limit),)).fetchall()

    def recovery_lot_rows(self, limit: int = 500,
                          closed_since_utc: str | None = None) -> list[sqlite3.Row]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        period_clause = " AND (l.status<>'closed' OR l.closed_at_utc>=?)" if closed_since_utc else ""
        params: tuple[object, ...] = ((closed_since_utc, int(limit)) if closed_since_utc
                                      else (int(limit),))
        return self.connection.execute(
            """SELECT l.*,
            entry.exchange_order_id AS entry_exchange_order_id,
            exit_intent.exchange_order_id AS exit_exchange_order_id,
            COALESCE(exit_intent.detail,'') || CASE WHEN adjustment.reason IS NULL THEN '' ELSE '；' || adjustment.reason END AS exit_reason
            FROM account05_lots l
            LEFT JOIN account05_virtual_adjustments adjustment ON adjustment.lot_id=l.lot_id
            LEFT JOIN account05_order_intents entry ON entry.signal_id=
                CASE WHEN substr(l.signal_id,-3)='-T1'
                     THEN substr(l.signal_id,1,length(l.signal_id)-3)
                     ELSE l.signal_id END
            LEFT JOIN account05_order_intents exit_intent
                ON exit_intent.signal_id='exit:' || l.lot_id
            WHERE l.kind IN ('addon','base') AND
              l.signal_id NOT LIKE 'manual:%'""" + period_clause +
            " ORDER BY l.created_at_utc DESC LIMIT ?", params).fetchall()

    def fixed_addon_lot_rows(self, size: Decimal = Decimal("0.01"),
                             limit: int = 1000) -> list[sqlite3.Row]:
        """Return only fixed-size add-on lots for the live evidence screen."""
        if size <= 0:
            raise ValueError("size must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")
        return self.connection.execute(
            """SELECT l.*,
            entry.exchange_order_id AS entry_exchange_order_id,
            exit_intent.exchange_order_id AS exit_exchange_order_id,
            COALESCE(exit_intent.detail,'') || CASE WHEN adjustment.reason IS NULL THEN '' ELSE '；' || adjustment.reason END AS exit_reason
            FROM account05_lots l
            LEFT JOIN account05_virtual_adjustments adjustment ON adjustment.lot_id=l.lot_id
            LEFT JOIN account05_order_intents entry ON entry.signal_id=
                CASE WHEN substr(l.signal_id,-3)='-T1'
                     THEN substr(l.signal_id,1,length(l.signal_id)-3)
                     ELSE l.signal_id END
            LEFT JOIN account05_order_intents exit_intent
                ON exit_intent.signal_id='exit:' || l.lot_id
            WHERE l.kind='addon' AND l.original_size=? AND l.signal_id LIKE 'addon:%'
            ORDER BY l.created_at_utc DESC LIMIT ?""",
            (str(size), int(limit))).fetchall()

    def reconcile_side_flat(self, side: PositionSide) -> int:
        """Retire stale virtual exposure without recording a fictitious fill."""
        lot_ids = [str(row[0]) for row in self.connection.execute(
            "SELECT lot_id FROM account05_lots WHERE side=? AND status<>'closed'",
            (PositionSide(side).value,))]
        for lot_id in lot_ids:
            lot = self.get_lot(lot_id)
            if lot.remaining_size:
                self.trim_virtual_quantity(lot_id, lot.remaining_size,
                    "交易所确认该方向空仓；清理虚拟残留，非成交、不计利润")
        with self.connection:
            self.connection.executemany(
                "DELETE FROM account05_tp_replacements WHERE lot_id=?",
                ((lot_id,) for lot_id in lot_ids))
        return len(lot_ids)

    def get_lot(self, lot_id: str) -> Account05Lot:
        row = self.connection.execute(
            "SELECT * FROM account05_lots WHERE lot_id=?", (lot_id,)).fetchone()
        if row is None:
            raise KeyError(lot_id)
        return self._lot(row)

    def delete_lot(self, lot_id: str) -> None:
        """Forget one locally tracked virtual lot without touching the exchange."""
        row = self.connection.execute(
            "SELECT signal_id FROM account05_lots WHERE lot_id=?", (lot_id,)
        ).fetchone()
        if row is None:
            raise KeyError(lot_id)
        signal_id = str(row[0])
        with self.connection:
            self.connection.execute("DELETE FROM account05_tp_replacements WHERE lot_id=?", (lot_id,))
            self.connection.execute("DELETE FROM account05_lots WHERE lot_id=?", (lot_id,))
            self.connection.execute("DELETE FROM account05_order_intents WHERE signal_id=?", (signal_id,))

    def open_lots(self, side: PositionSide | None = None) -> list[Account05Lot]:
        params: tuple[str, ...] = ()
        side_clause = ""
        if side is not None:
            side_clause = " AND side=?"
            params = (PositionSide(side).value,)
        rows = self.connection.execute(
            """SELECT * FROM account05_lots
            WHERE status NOT IN ('closed','reconcile_required')""" + side_clause
            + " ORDER BY created_at_utc", params).fetchall()
        return [self._lot(row) for row in rows]

    def open_addon_count(self, side: PositionSide) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM account05_lots
            WHERE side=? AND kind='addon' AND status<>'closed'""",
            (PositionSide(side).value,),
        ).fetchone()[0])

    def open_recovery_slot_count(self, side: PositionSide) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM account05_lots WHERE side=? AND kind='addon'
            AND signal_id NOT LIKE 'addon:%'
            AND status NOT IN ('closed','reconcile_required')""",
            (PositionSide(side).value,)).fetchone()[0])

    def open_fixed_addon_count(self, side: PositionSide) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM account05_lots WHERE side=? AND kind='addon'
            AND signal_id LIKE 'addon:%' AND status<>'closed'""",
            (PositionSide(side).value,)).fetchone()[0])

    def has_base_history(self, side: PositionSide) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM account05_lots WHERE side=? AND kind='base' LIMIT 1",
            (PositionSide(side).value,),
        ).fetchone() is not None

    def base_audit_state(self, side: PositionSide) -> tuple[bool, bool]:
        """Return (historical_base, open_base) from the authoritative lots table."""
        row = self.connection.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM account05_lots WHERE side=? AND kind='base'),
                 EXISTS(SELECT 1 FROM account05_lots WHERE side=? AND kind='base'
                        AND status NOT IN ('closed','reconcile_required'))""",
            (PositionSide(side).value, PositionSide(side).value)).fetchone()
        return bool(row[0]), bool(row[1])

    def recovery_program(self) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM account05_recovery_program WHERE singleton=1"
        ).fetchone()

    def activate_recovery_program(self, *, initial_equity: Decimal,
                                  target_equity: Decimal = Decimal("100")) -> sqlite3.Row:
        """Start the one-off September loss-recovery programme idempotently."""
        if initial_equity <= 0 or target_equity <= initial_equity:
            raise ValueError("invalid account05 recovery equity range")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO account05_recovery_program
                (singleton,active,initial_equity,target_equity,phase,started_at_utc,updated_at_utc)
                VALUES(1,1,?,?,'balance_short',?,?)""",
                (str(initial_equity), str(target_equity), now, now))
            self.connection.execute(
                """INSERT OR IGNORE INTO account05_recovery_trapped_lots(lot_id)
                SELECT lot_id FROM account05_lots
                WHERE side='long' AND status<>'closed'""")
        row = self.recovery_program()
        assert row is not None
        return row

    def set_recovery_phase(self, phase: str) -> sqlite3.Row:
        if phase not in {"balance_short", "adjust", "complete"}:
            raise ValueError("invalid account05 recovery phase")
        active = 0 if phase == "complete" else 1
        self.connection.execute(
            """UPDATE account05_recovery_program SET phase=?,active=?,updated_at_utc=?
            WHERE singleton=1""",
            (phase, active, datetime.now(timezone.utc).isoformat()))
        self.connection.commit()
        row = self.recovery_program()
        if row is None:
            raise KeyError("account05 recovery program")
        return row

    def recovery_trapped_open_count(self) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM account05_recovery_trapped_lots r
            JOIN account05_lots l ON l.lot_id=r.lot_id
            WHERE l.status<>'closed'""").fetchone()[0])

    def recovery_extra_used(self, side: PositionSide) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM account05_order_intents
            WHERE side=? AND kind='addon' AND status='filled'
            AND signal_id LIKE 'recovery-addon:%'""",
            (PositionSide(side).value,)).fetchone()[0])

    def plan_base_rebuild(self, *, side: PositionSide, signal_key: str,
                          target_margin_pct: Decimal, first_margin_pct: Decimal,
                          take_profit_pct: Decimal) -> sqlite3.Row:
        """Persist a two-stage post-TP base rebuild before its first order."""
        if not (Decimal("0") < first_margin_pct <= target_margin_pct):
            raise ValueError("invalid staged base rebuild margins")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                """INSERT INTO account05_base_rebuild_plans
                (side,signal_key,target_margin_pct,first_margin_pct,take_profit_pct,
                 status,updated_at_utc)
                VALUES(?,?,?,?,?,'stage1_planned',?)
                ON CONFLICT(side) DO UPDATE SET
                  signal_key=excluded.signal_key,
                  target_margin_pct=excluded.target_margin_pct,
                  first_margin_pct=excluded.first_margin_pct,
                  take_profit_pct=excluded.take_profit_pct,
                  status='stage1_planned',updated_at_utc=excluded.updated_at_utc""",
                (PositionSide(side).value, str(signal_key), str(target_margin_pct),
                 str(first_margin_pct), str(take_profit_pct), now))
        row = self.base_rebuild_plan(side)
        assert row is not None
        return row

    def base_rebuild_plan(self, side: PositionSide) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM account05_base_rebuild_plans WHERE side=?",
            (PositionSide(side).value,)).fetchone()

    def set_base_rebuild_status(self, side: PositionSide, status: str) -> sqlite3.Row:
        if status not in {"stage1_planned", "stage1_filled", "complete"}:
            raise ValueError("invalid staged base rebuild status")
        cursor = self.connection.execute(
            """UPDATE account05_base_rebuild_plans SET status=?,updated_at_utc=?
            WHERE side=?""",
            (status, datetime.now(timezone.utc).isoformat(),
             PositionSide(side).value))
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(PositionSide(side).value)
        row = self.base_rebuild_plan(side)
        assert row is not None
        return row

    def recovery_pool(self) -> dict[str, Decimal]:
        row = self.connection.execute(
            "SELECT * FROM account05_recovery_pool WHERE singleton=1").fetchone()
        if row is None:
            return {
                "pool_balance": Decimal("0"), "reserve_balance": Decimal("0"),
                "total_net_profit": Decimal("0"), "total_loss_paid": Decimal("0"),
                "total_uncovered_loss": Decimal("0")}
        return {key: Decimal(str(row[key])) for key in (
            "pool_balance", "reserve_balance", "total_net_profit", "total_loss_paid",
            "total_uncovered_loss")}

    def recovery_stats_since_utc(self) -> str | None:
        row = self.connection.execute(
            "SELECT reset_at_utc FROM account05_recovery_resets "
            "ORDER BY reset_at_utc DESC LIMIT 1").fetchone()
        return str(row[0]) if row else None

    def reset_recovery_accounting(self, *, clear_balances: bool) -> dict[str, Decimal]:
        """Start a new recovery accounting period without deleting lot history."""
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            before = self.recovery_pool()
            self.connection.execute(
                """INSERT INTO account05_recovery_resets
                (reset_at_utc,clear_balances,prior_values_json) VALUES(?,?,?)""",
                (now, int(clear_balances), json.dumps(
                    {key: str(value) for key, value in before.items()},
                    ensure_ascii=False, sort_keys=True)))
            if self.connection.execute(
                    "SELECT 1 FROM account05_recovery_pool WHERE singleton=1").fetchone():
                self.connection.execute(
                    """UPDATE account05_recovery_pool SET
                    pool_balance=CASE WHEN ? THEN '0' ELSE pool_balance END,
                    reserve_balance=CASE WHEN ? THEN '0' ELSE reserve_balance END,
                    total_net_profit='0',total_loss_paid='0',total_uncovered_loss='0',
                    updated_at_utc=? WHERE singleton=1""",
                    (int(clear_balances), int(clear_balances), now))
            else:
                self.connection.execute(
                    """INSERT INTO account05_recovery_pool
                    (singleton,pool_balance,reserve_balance,total_net_profit,
                     total_loss_paid,total_uncovered_loss,updated_at_utc)
                    VALUES(1,'0','0','0','0','0',?)""", (now,))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.recovery_pool()

    def credit_recovery_profit(self, net_profit: Decimal,
                               pool_share: Decimal = Decimal("0.80"),
                               credit_key: str | None = None) -> dict[str, Decimal]:
        if net_profit <= 0 or not (Decimal("0") <= pool_share <= Decimal("1")):
            raise ValueError("invalid recovery profit credit")
        pool_credit = net_profit * pool_share
        reserve_credit = net_profit - pool_credit
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if credit_key is not None:
                inserted = self.connection.execute(
                    "INSERT OR IGNORE INTO account05_profit_credits VALUES (?)", (credit_key,))
                if inserted.rowcount == 0:
                    self.connection.commit()
                    return self.recovery_pool()
            current = self.recovery_pool()
            self.connection.execute(
                """INSERT INTO account05_recovery_pool
                (singleton,pool_balance,reserve_balance,total_net_profit,total_loss_paid,
                 updated_at_utc) VALUES(1,?,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                  pool_balance=excluded.pool_balance,
                  reserve_balance=excluded.reserve_balance,
                  total_net_profit=excluded.total_net_profit,
                  total_loss_paid=excluded.total_loss_paid,
                  updated_at_utc=excluded.updated_at_utc""",
                (str(current["pool_balance"] + pool_credit),
                 str(current["reserve_balance"] + reserve_credit),
                 str(current["total_net_profit"] + net_profit),
                 str(current["total_loss_paid"]), now))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.recovery_pool()

    def debit_recovery_loss(self, loss: Decimal) -> dict[str, Decimal]:
        if loss <= 0:
            raise ValueError("invalid recovery loss debit")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.recovery_pool()
            if current["pool_balance"] < loss:
                raise ValueError("recovery pool cannot cover loss")
            self.connection.execute(
                """INSERT INTO account05_recovery_pool
                (singleton,pool_balance,reserve_balance,total_net_profit,total_loss_paid,
                 updated_at_utc) VALUES(1,?,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                  pool_balance=excluded.pool_balance,
                  reserve_balance=excluded.reserve_balance,
                  total_net_profit=excluded.total_net_profit,
                  total_loss_paid=excluded.total_loss_paid,
                  updated_at_utc=excluded.updated_at_utc""",
                (str(current["pool_balance"] - loss), str(current["reserve_balance"]),
                 str(current["total_net_profit"]),
                 str(current["total_loss_paid"] + loss), now))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.recovery_pool()

    def settle_filled_recovery_loss(self, loss: Decimal) -> dict[str, Decimal]:
        """Settle an irreversible fill and retain any deficit as a negative pool."""
        if loss <= 0:
            raise ValueError("invalid recovery loss settlement")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.recovery_pool()
            self.connection.execute(
                """INSERT INTO account05_recovery_pool
                (singleton,pool_balance,reserve_balance,total_net_profit,total_loss_paid,
                 total_uncovered_loss,updated_at_utc) VALUES(1,?,?,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                  pool_balance=excluded.pool_balance,
                  reserve_balance=excluded.reserve_balance,
                  total_net_profit=excluded.total_net_profit,
                  total_loss_paid=excluded.total_loss_paid,
                  total_uncovered_loss=excluded.total_uncovered_loss,
                  updated_at_utc=excluded.updated_at_utc""",
                (str(current["pool_balance"] - loss), str(current["reserve_balance"]),
                 str(current["total_net_profit"]),
                 str(current["total_loss_paid"] + loss),
                 str(current["total_uncovered_loss"]), now))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.recovery_pool()

    def record_paired_recovery(self, profit: Decimal, loss: Decimal,
                               pool_share: Decimal = Decimal("0.80")) -> dict[str, Decimal]:
        """Book a profitable addon and a covered losing addon as one pair."""
        if profit <= 0 or loss <= 0:
            raise ValueError("paired profit and loss must be positive")
        net = profit - loss
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.recovery_pool()
            if net >= 0:
                next_pool = current["pool_balance"] + net * pool_share
                next_reserve = current["reserve_balance"] + net * (Decimal("1") - pool_share)
            else:
                shortfall = -net
                next_pool = current["pool_balance"] - shortfall
                next_reserve = current["reserve_balance"]
            self.connection.execute(
                """INSERT INTO account05_recovery_pool
                (singleton,pool_balance,reserve_balance,total_net_profit,total_loss_paid,
                 total_uncovered_loss,updated_at_utc) VALUES(1,?,?,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                  pool_balance=excluded.pool_balance,
                  reserve_balance=excluded.reserve_balance,
                  total_net_profit=excluded.total_net_profit,
                  total_loss_paid=excluded.total_loss_paid,
                  total_uncovered_loss=excluded.total_uncovered_loss,
                  updated_at_utc=excluded.updated_at_utc""",
                (str(next_pool), str(next_reserve),
                 str(current["total_net_profit"] + profit),
                 str(current["total_loss_paid"] + loss),
                 str(current["total_uncovered_loss"]), now))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.recovery_pool()

    def claim_addon_exit_event(self, side: PositionSide, event_key: str) -> bool:
        """Allow at most one exit group per side for the same market event."""
        side_value = PositionSide(side).value
        row = self.connection.execute(
            "SELECT last_event_key FROM account05_addon_exit_latches WHERE side=?",
            (side_value,)).fetchone()
        if row is not None and str(row[0]) == str(event_key):
            return False
        self.connection.execute(
            """INSERT INTO account05_addon_exit_latches(side,last_event_key,updated_at_utc)
            VALUES(?,?,?) ON CONFLICT(side) DO UPDATE SET
            last_event_key=excluded.last_event_key,updated_at_utc=excluded.updated_at_utc""",
            (side_value, str(event_key), datetime.now(timezone.utc).isoformat()))
        self.connection.commit()
        return True

    def claim_extreme_rotation_event(self, event_key: str, direction: int) -> bool:
        """Claim one independent live 1m/5m extreme event exactly once."""
        if direction not in (-1, 1) or not str(event_key):
            raise ValueError("invalid extreme rotation event")
        row = self.connection.execute(
            "SELECT status FROM account05_extreme_rotation_events WHERE event_key=?",
            (str(event_key),)).fetchone()
        if row is not None:
            return str(row["status"]) == "claimed"
        try:
            with self.connection:
                self.connection.execute(
                    """INSERT INTO account05_extreme_rotation_events
                    (event_key,direction,status,created_at_utc)
                    VALUES(?,?,'claimed',?)""",
                    (str(event_key), int(direction),
                     datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            return False
        return True

    def complete_extreme_rotation_event(self, event_key: str) -> None:
        cursor = self.connection.execute(
            """UPDATE account05_extreme_rotation_events SET status='complete'
            WHERE event_key=? AND status='claimed'""", (str(event_key),))
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(event_key)

    def freeze_extreme_targets(self, event_key: str, lots: list[Account05Lot],
                               reverse_signal_id: str) -> tuple[list[tuple[str, Decimal]], str]:
        row = self.connection.execute(
            "SELECT * FROM account05_extreme_rotation_events WHERE event_key=?", (event_key,)).fetchone()
        if row is None or row["status"] != "claimed":
            raise ValueError("extreme event is not claimed")
        if not row["plan_recorded"]:
            with self.connection:
                self.connection.executemany(
                    "INSERT INTO account05_extreme_targets VALUES (?,?,?)",
                    [(event_key, lot.lot_id, str(lot.remaining_size)) for lot in lots])
                self.connection.execute(
                    "UPDATE account05_extreme_rotation_events SET plan_recorded=1,reverse_signal_id=? WHERE event_key=?",
                    (reverse_signal_id, event_key))
        row = self.connection.execute(
            "SELECT reverse_signal_id FROM account05_extreme_rotation_events WHERE event_key=?", (event_key,)).fetchone()
        targets = [(str(x["lot_id"]), Decimal(x["target_size"])) for x in self.connection.execute(
            "SELECT lot_id,target_size FROM account05_extreme_targets WHERE event_key=? ORDER BY lot_id", (event_key,))]
        return targets, str(row[0])

    def claim_new_signal_shapes(
            self, present: tuple[tuple, ...], observation_bar: str = "",
            five_minute_bar: str = ""
    ) -> set[tuple[str, PositionSide]]:
        """Claim only a genuinely new 1m structure, never a polling flicker.

        A live shape remains latched when one five-second scan misses it.  It is
        rearmed only after the signal stays absent across two distinct closed
        one-minute observations, so the next fill necessarily belongs to a new
        one-minute structure.
        """
        normalized_items: list[tuple[str, PositionSide, str]] = []
        for item in present:
            identity, side = item[:2]
            anchor = str(item[2]) if len(item) >= 3 else ""
            normalized_items.append((str(identity), PositionSide(side), anchor))
        normalized = tuple(dict.fromkeys(normalized_items))
        present_keys = {(identity, side.value) for identity, side, _ in normalized}
        now = datetime.now(timezone.utc).isoformat()
        rows = self.connection.execute(
            """SELECT identity,side,active,last_anchor,last_five_bar,missing_since_bar
            FROM account05_signal_latches""").fetchall()
        existing = {(str(row["identity"]), str(row["side"])): row for row in rows}
        fresh: set[tuple[str, PositionSide]] = set()
        with self.connection:
            for key, row in existing.items():
                if int(row["active"]) != 1 or key in present_keys:
                    continue
                missing_bar = str(row["missing_since_bar"] or "")
                if observation_bar and missing_bar and missing_bar != observation_bar:
                    self.connection.execute(
                        """UPDATE account05_signal_latches SET active=0,
                        missing_since_bar='',updated_at_utc=? WHERE identity=? AND side=?""",
                        (now, key[0], key[1]))
                elif observation_bar and not missing_bar:
                    self.connection.execute(
                        """UPDATE account05_signal_latches SET missing_since_bar=?,updated_at_utc=?
                        WHERE identity=? AND side=?""", (observation_bar, now, key[0], key[1]))
            for identity, side, anchor in normalized:
                key = (identity, side.value)
                row = existing.get(key)
                if row is not None and int(row["active"]) == 1:
                    # Reversal identities carry the confirmed 1m structure
                    # time as their anchor.  A different anchor in a later 5m
                    # candle is a genuinely new top/bottom event even if the
                    # broad identity never stayed absent for two polls.  This
                    # prevents a prior bottom/top latch from swallowing the
                    # next distinct reversal while retaining the one-per-5m
                    # hard lock.  Trend-chase anchors are intentionally not
                    # allowed to rearm this way.
                    new_reversal = (
                        ("底部" in identity or "顶部" in identity)
                        and bool(anchor)
                        and anchor != str(row["last_anchor"] or "")
                        and bool(five_minute_bar)
                        and five_minute_bar != str(row["last_five_bar"] or ""))
                    if new_reversal:
                        self.connection.execute(
                            """UPDATE account05_signal_latches SET active=1,
                            last_anchor=?,last_five_bar=?,missing_since_bar='',updated_at_utc=?
                            WHERE identity=? AND side=?""",
                            (anchor, five_minute_bar, now, identity, side.value))
                        fresh.add((identity, side))
                        continue
                    if str(row["missing_since_bar"] or ""):
                        self.connection.execute(
                            """UPDATE account05_signal_latches SET missing_since_bar='',updated_at_utc=?
                            WHERE identity=? AND side=?""", (now, identity, side.value))
                    continue
                if row is not None and anchor and anchor == str(row["last_anchor"] or ""):
                    continue
                # Even after a valid 1m rearm, the same identity/direction may
                # never open twice inside one 5m candle.
                if (row is not None and five_minute_bar
                        and five_minute_bar == str(row["last_five_bar"] or "")):
                    continue
                self.connection.execute(
                    """INSERT INTO account05_signal_latches
                    (identity,side,active,last_anchor,last_five_bar,missing_since_bar,updated_at_utc)
                    VALUES(?,?,1,?,?,'',?) ON CONFLICT(identity,side) DO UPDATE SET
                    active=1,last_anchor=excluded.last_anchor,last_five_bar=excluded.last_five_bar,
                    missing_since_bar='',
                    updated_at_utc=excluded.updated_at_utc""",
                    (identity, side.value, anchor, five_minute_bar, now),
                )
                fresh.add((identity, side))
        return fresh

    def claim_side_five_entry(self, side: PositionSide, five_minute_bar: str,
                              identity: str = "") -> bool:
        """Allow one entry per group; selected independent pairs may coexist."""
        side = PositionSide(side)
        five_minute_bar = str(five_minute_bar).strip()
        if not five_minute_bar:
            raise ValueError("five_minute_bar is required")
        row = self.connection.execute(
            "SELECT last_five_bar FROM account05_side_five_locks WHERE side=?",
            (side.value,)).fetchone()
        same_bar = row is not None and str(row["last_five_bar"]) == five_minute_bar
        st_group = {"1分钟超级趋势首次翻空补漏做空",
                    "1分钟超级趋势首次翻空局部反转做空",
                    "5分钟超级趋势首次翻空追空",
                    "1分钟超级趋势压力线反抽追空"}
        top_group = {"局部顶部做空", "真正顶部做空", "反转三阶段补漏做空",
                     "5分钟高位拒绝早触发追空"}
        group = ("supertrend" if side == PositionSide.SHORT and identity in st_group
                 else "top_reversal" if side == PositionSide.SHORT and identity in top_group
                 else "supertrend_long" if side == PositionSide.LONG
                      and identity == "超级趋势支撑早触发追多"
                 else "trend_long" if side == PositionSide.LONG
                      and identity == "上涨趋势回踩追多"
                 else "other")
        groups = {str(item["entry_group"]) for item in self.connection.execute(
            "SELECT entry_group FROM account05_side_five_groups WHERE side=? AND five_bar=?",
            (side.value, five_minute_bar)).fetchall()}
        pair = ({"supertrend", "top_reversal"} if side == PositionSide.SHORT
                else {"supertrend_long", "trend_long"})
        if group in groups or (same_bar and not (group in pair
                                                 and len(groups) == 1
                                                 and groups | {group} == pair)):
            return False
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT INTO account05_side_five_locks(side,last_five_bar,updated_at_utc)
            VALUES(?,?,?) ON CONFLICT(side) DO UPDATE SET
            last_five_bar=excluded.last_five_bar,updated_at_utc=excluded.updated_at_utc""",
            (side.value, five_minute_bar, now))
        self.connection.execute(
            "INSERT INTO account05_side_five_groups(side,five_bar,entry_group,updated_at_utc) VALUES(?,?,?,?)",
            (side.value, five_minute_bar, group, now))
        self.connection.commit()
        return True

    def unprotected_lots(self) -> list[Account05Lot]:
        rows = self.connection.execute(
            "SELECT * FROM account05_lots WHERE status='filled_unprotected' ORDER BY created_at_utc"
        ).fetchall()
        return [self._lot(row) for row in rows]

    def mark_lot_reconcile_required(self, lot_id: str) -> Account05Lot:
        """Quarantine a stale virtual lot without changing exchange state."""
        cursor = self.connection.execute(
            """UPDATE account05_lots SET status='reconcile_required',updated_at_utc=?
            WHERE lot_id=? AND status='filled_unprotected'""",
            (datetime.now(timezone.utc).isoformat(), lot_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(lot_id)
        return self.get_lot(lot_id)

    def close(self) -> None:
        self.connection.close()
