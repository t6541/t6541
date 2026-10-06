from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading

from .intraday import DailyRiskState
from .strategy_control import StrategyState, transition_allowed


_SCHEMA_READY_LOCK = threading.Lock()
_SCHEMA_READY_PATHS: set[str] = set()


@dataclass(frozen=True)
class SignalIntent:
    instrument: str
    bar_time: str
    direction: int
    strategy_version: str
    stop_distance_pct: float
    status: str = "observed"


@dataclass(frozen=True)
class RangePivotIntent:
    instrument: str
    strategy_id: str
    strategy_version: str
    timeframe: str
    confirmed_bar_time: str
    signal_action: str
    signal_direction: int
    pivot_price: float
    atr: float
    stop_price: float
    take_profit_price: float
    config_fingerprint: str
    status: str = "observed"
    branch: str = "relative_extreme_reversal"


@dataclass(frozen=True)
class StrategyRuntimeState:
    account_id: str
    strategy_id: str
    state: StrategyState
    revision: int
    cycle_id: str | None
    updated_at_utc: str


class StrategyRevisionConflict(RuntimeError):
    """The caller based a transition on a stale strategy revision."""


class InvalidStrategyTransition(ValueError):
    """The requested lifecycle transition violates the state machine."""


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        schema_key = str(self.path.resolve())
        with _SCHEMA_READY_LOCK:
            schema_ready = schema_key in _SCHEMA_READY_PATHS
        if schema_ready:
            # Schema creation, column probes, historical backfill and retired
            # table cleanup belong to process startup. Repeating them for the
            # dozens of short-lived stores in one decision tick was the main
            # local hot-path burden.
            self.connection.execute("PRAGMA busy_timeout=5000")
            return
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS reversal_trial_claims (
                instrument TEXT NOT NULL, direction INTEGER NOT NULL,
                anchor_time TEXT NOT NULL, trade_uid TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                PRIMARY KEY(instrument,direction,anchor_time)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_execution_claims (
                event_id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
                detail TEXT NOT NULL, updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS directional_cover_anchors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument TEXT NOT NULL,
                direction INTEGER NOT NULL,
                five_bar_time TEXT NOT NULL,
                one_anchor_time TEXT NOT NULL,
                stop_reference REAL NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(instrument,direction,five_bar_time)
            );
            CREATE TABLE IF NOT EXISTS ma_cross_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument TEXT NOT NULL, timeframe TEXT NOT NULL,
                cross_time TEXT NOT NULL, direction INTEGER NOT NULL,
                cross_name TEXT NOT NULL, price REAL NOT NULL,
                range_position REAL NOT NULL, valid_local_extreme INTEGER NOT NULL,
                decision TEXT NOT NULL, reason TEXT NOT NULL,
                compared_timeframe TEXT, compared_cross_time TEXT,
                comparison_gap_seconds REAL, created_at_utc TEXT NOT NULL,
                UNIQUE(instrument,timeframe,cross_time,direction)
            );
            CREATE TABLE IF NOT EXISTS hourly_multiframe_boundary_samples (
                instrument TEXT NOT NULL,
                beijing_hour TEXT NOT NULL,
                observed_at_utc TEXT NOT NULL,
                one_minute_endpoint_direction INTEGER NOT NULL,
                five_direction INTEGER NOT NULL,
                fifteen_direction INTEGER NOT NULL,
                hour_direction INTEGER NOT NULL,
                classification TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY(instrument,beijing_hour)
            );
            CREATE TABLE IF NOT EXISTS hourly_multiframe_replay_samples (
                instrument TEXT NOT NULL, beijing_hour TEXT NOT NULL,
                boundary_utc TEXT NOT NULL, classification TEXT NOT NULL,
                direction INTEGER NOT NULL, one_minute_fan_direction INTEGER NOT NULL,
                five_direction INTEGER NOT NULL, fifteen_direction INTEGER NOT NULL,
                hour_direction INTEGER NOT NULL, entry_reference REAL NOT NULL,
                forward_30m_points REAL, forward_60m_points REAL,
                mfe_30m_points REAL, mae_30m_points REAL,
                mfe_60m_points REAL, mae_60m_points REAL,
                success_3_points_30m INTEGER NOT NULL, evidence_json TEXT NOT NULL,
                replay_version TEXT NOT NULL, created_at_utc TEXT NOT NULL,
                PRIMARY KEY(instrument,beijing_hour,replay_version)
            );
            CREATE TABLE IF NOT EXISTS signal_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc TEXT NOT NULL,
                instrument TEXT NOT NULL,
                bar_time TEXT NOT NULL,
                direction INTEGER NOT NULL,
                strategy_version TEXT NOT NULL,
                stop_distance_pct REAL NOT NULL,
                status TEXT NOT NULL,
                UNIQUE(instrument, bar_time, direction, strategy_version)
            );
            CREATE TABLE IF NOT EXISTS daily_risk (
                trading_date TEXT PRIMARY KEY,
                realized_pnl_pct REAL NOT NULL,
                consecutive_losses INTEGER NOT NULL,
                trades INTEGER NOT NULL,
                circuit_breaker INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS range_pivot_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc TEXT NOT NULL,
                instrument TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                confirmed_bar_time TEXT NOT NULL,
                signal_action TEXT NOT NULL,
                signal_direction INTEGER NOT NULL,
                pivot_price REAL NOT NULL,
                atr REAL NOT NULL,
                stop_price REAL NOT NULL,
                take_profit_price REAL NOT NULL,
                config_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                branch TEXT NOT NULL DEFAULT 'legacy_unclassified',
                UNIQUE(instrument, strategy_version, confirmed_bar_time, signal_action)
            );
            CREATE TABLE IF NOT EXISTS reversal_states (
                instrument TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                pending_action TEXT NOT NULL,
                confirmed_bar_time TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY(instrument, strategy_version)
            );
            CREATE TABLE IF NOT EXISTS trade_lifecycle (
                trade_uid TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                instrument TEXT NOT NULL,
                direction INTEGER NOT NULL,
                status TEXT NOT NULL,
                signal_time TEXT NOT NULL,
                signal_reason TEXT NOT NULL,
                signal_context_json TEXT NOT NULL,
                order_id TEXT,
                algo_id TEXT,
                entry_reference REAL,
                stop_price REAL,
                trailing_activation REAL,
                trailing_callback REAL,
                close_time TEXT,
                close_price REAL,
                gross_pnl REAL,
                total_fees REAL,
                net_pnl REAL,
                exit_reason TEXT,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS loss_reviews (
                trade_uid TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                close_time TEXT NOT NULL,
                direction INTEGER NOT NULL,
                net_pnl REAL NOT NULL,
                category TEXT NOT NULL,
                cause TEXT NOT NULL,
                evidence TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                confidence TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT '待汇总',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                trigger_snapshot TEXT NOT NULL DEFAULT '历史订单无快照'
            );
            CREATE TABLE IF NOT EXISTS entry_snapshots (
                snapshot_uid TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                order_id TEXT,
                signal_time TEXT NOT NULL,
                direction INTEGER NOT NULL,
                branch TEXT NOT NULL,
                trigger_reason TEXT NOT NULL,
                context_json TEXT NOT NULL,
                entry_reference REAL,
                stop_price REAL,
                take_profit_price REAL,
                status TEXT NOT NULL,
                matched_loss_uid TEXT,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_signal_events (
                event_key TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                direction INTEGER NOT NULL,
                confirmed_bar_time TEXT NOT NULL,
                reason TEXT NOT NULL,
                stop_price REAL NOT NULL,
                source_strategy TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                expires_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_fill_notices (
                order_id TEXT PRIMARY KEY,
                strategy_label TEXT NOT NULL,
                filled_at_utc TEXT NOT NULL,
                notified_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sniper_protection_states (
                client_order_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                instrument TEXT NOT NULL,
                direction INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                normal_stop REAL NOT NULL,
                disaster_stop REAL NOT NULL,
                status TEXT NOT NULL,
                filled_at_utc TEXT,
                entry_bar_time TEXT,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS market_pattern_observations (
                event_key TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                pattern_type TEXT NOT NULL,
                direction INTEGER NOT NULL,
                confirmed_bar_time TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_reference REAL NOT NULL,
                stop_reference REAL NOT NULL,
                features_json TEXT NOT NULL,
                outcome_status TEXT NOT NULL DEFAULT 'pending',
                outcome_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ma_endpoint_lineage (
                event_key TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction INTEGER NOT NULL,
                confirmed_bar_time TEXT NOT NULL,
                endpoint_price REAL NOT NULL,
                extreme_price REAL NOT NULL,
                ma5 REAL,
                ma10 REAL,
                ma20 REAL,
                fan_endpoint_confirmed INTEGER NOT NULL DEFAULT 0,
                half_cover_confirmed INTEGER NOT NULL DEFAULT 0,
                slow_ma_cross_confirmed INTEGER NOT NULL DEFAULT 0,
                ma5_cross_confirmed INTEGER NOT NULL DEFAULT 0,
                zone_last_seen_at TEXT,
                zone_status TEXT NOT NULL DEFAULT 'forming',
                confirmation_reason TEXT NOT NULL DEFAULT '',
                parent_timeframe TEXT,
                parent_event_key TEXT,
                pair_status TEXT NOT NULL DEFAULT 'unpaired',
                identity_code TEXT NOT NULL DEFAULT 'local_endpoint_reversal',
                identity_label TEXT NOT NULL DEFAULT '',
                upgraded_trade_uids_json TEXT NOT NULL DEFAULT '[]',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS one_minute_reversal_trend_locks (
                instrument TEXT PRIMARY KEY,
                endpoint_event_key TEXT NOT NULL,
                direction INTEGER NOT NULL,
                anchor_time TEXT NOT NULL,
                anchor_extreme REAL NOT NULL,
                status TEXT NOT NULL,
                confirmation_reason TEXT NOT NULL,
                confirmed_at TEXT,
                failed_at TEXT,
                failure_reason TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_runtime_state (
                account_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                cycle_id TEXT,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY(account_id, strategy_id)
            );
            CREATE TABLE IF NOT EXISTS strategy_state_history (
                transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_state_history_lookup
            ON strategy_state_history(account_id, strategy_id, transition_id DESC);
            """
        )
        self._ensure_column("range_pivot_intents", "branch", "TEXT NOT NULL DEFAULT 'legacy_unclassified'")
        self._ensure_column("trade_lifecycle", "branch", "TEXT NOT NULL DEFAULT 'legacy_unclassified'")
        for column, declaration in (
            ("max_favorable_price", "REAL"), ("max_adverse_price", "REAL"),
            ("mfe_points", "REAL NOT NULL DEFAULT 0"), ("mae_points", "REAL NOT NULL DEFAULT 0"),
            ("last_observed_price", "REAL"), ("last_observed_at_utc", "TEXT"),
            ("stop_followup_price_5m", "REAL"), ("stop_followup_price_10m", "REAL"),
            ("stop_followup_price_20m", "REAL"),
        ):
            self._ensure_column("trade_lifecycle", column, declaration)
        for column, declaration in (
            ("half_cover_confirmed", "INTEGER NOT NULL DEFAULT 0"),
            ("fan_endpoint_confirmed", "INTEGER NOT NULL DEFAULT 0"),
            ("slow_ma_cross_confirmed", "INTEGER NOT NULL DEFAULT 0"),
            ("ma5_cross_confirmed", "INTEGER NOT NULL DEFAULT 0"),
            ("zone_last_seen_at", "TEXT"),
            ("zone_status", "TEXT NOT NULL DEFAULT 'forming'"),
            ("confirmation_reason", "TEXT NOT NULL DEFAULT ''"),
        ):
            self._ensure_column("ma_endpoint_lineage", column, declaration)
        self._ensure_column(
            "loss_reviews", "trigger_snapshot",
            "TEXT NOT NULL DEFAULT '历史订单无快照'",
        )
        for table_name in (
            "market_research_labels",
            "market_research_snapshots",
            "external_research_signals",
            "webhook_trade_signals",
            "ma_deviation_touches",
            "ma_experiment_demo_orders",
        ):
            self.connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        self._backfill_ma_endpoint_lineage()
        self.connection.commit()
        with _SCHEMA_READY_LOCK:
            _SCHEMA_READY_PATHS.add(schema_key)

    def _backfill_ma_endpoint_lineage(self) -> None:
        """Make the new view useful immediately from the retained reversal history."""
        now = datetime.now(timezone.utc).isoformat()
        rows = self.connection.execute(
            """SELECT * FROM market_pattern_observations
            WHERE pattern_type IN ('price_reversal_zone:1m','price_reversal_zone:5m',
                                   'price_reversal_zone:15m','price_reversal_zone:1H')""").fetchall()
        instruments: set[str] = set()
        for row in rows:
            timeframe = str(row["pattern_type"]).rsplit(":", 1)[-1]
            direction = int(row["direction"])
            side = "顶部" if direction < 0 else "底部"
            features = json.loads(str(row["features_json"] or "{}"))
            if not bool(features.get("ma_fan_endpoint_confirmed")):
                continue
            half_cover = bool(features.get("five_minute_half_cover")) if timeframe == "5m" else (
                "过半覆盖" in str(features.get("price_action_trigger") or ""))
            self.connection.execute(
                """INSERT OR IGNORE INTO ma_endpoint_lineage
                (event_key,instrument,timeframe,direction,confirmed_bar_time,
                 endpoint_price,extreme_price,pair_status,identity_code,
                 identity_label,fan_endpoint_confirmed,half_cover_confirmed,zone_last_seen_at,
                 confirmation_reason,created_at_utc,updated_at_utc)
                VALUES(?,?,?,?,?,?,?,'unpaired','local_endpoint_reversal',?,1,?,?,?,?,?)""",
                (str(row["event_key"]), str(row["instrument"]), timeframe,
                 direction, str(row["confirmed_bar_time"]),
                 float(row["entry_reference"]), float(row["stop_reference"]),
                 f"{timeframe}{side}三均线发散末端（历史回填）",
                 int(half_cover), str(row["confirmed_bar_time"]),
                 "前一根K线实体过半覆盖" if half_cover else "未覆盖前一根K线实体一半",
                 now, now))
            self.connection.execute(
                """UPDATE ma_endpoint_lineage SET half_cover_confirmed=?,
                confirmation_reason=?,zone_last_seen_at=coalesce(zone_last_seen_at,?)
                WHERE event_key=?""",
                (int(half_cover),
                 "前一根K线实体过半覆盖" if half_cover else "未覆盖前一根K线实体一半",
                 str(row["confirmed_bar_time"]), str(row["event_key"])))
            instruments.add(str(row["instrument"]))
        instruments.update(
            str(row["instrument"])
            for row in self.connection.execute(
                "SELECT DISTINCT instrument FROM ma_endpoint_lineage"))
        for instrument in instruments:
            self._rebuild_ma_endpoint_pairs(instrument)

    def record_ma_endpoint(self, *, event_key: str, instrument: str,
                           timeframe: str, direction: int,
                           confirmed_bar_time: str, endpoint_price: float,
                           extreme_price: float, ma5: float | None,
                           ma10: float | None, ma20: float | None,
                           half_cover_confirmed: bool = False,
                           slow_ma_cross_confirmed: bool = False,
                           ma5_cross_confirmed: bool = False,
                           fan_endpoint_confirmed: bool = True,
                           confirmation_reason: str = "") -> bool:
        """Record every 1m/5m/15m/1H MA-spread endpoint, including noise."""
        now = datetime.now(timezone.utc).isoformat()
        side = "顶部" if int(direction) < 0 else "底部"
        label = f"{timeframe}{side}三均线发散末端（待上级配对）"
        current_at = datetime.fromisoformat(str(confirmed_bar_time).replace("Z", "+00:00"))
        # One endpoint zone is deliberately short lived.  Keeping a 5m bottom
        # "forming" for hours allowed a later top/downtrend to inherit that old
        # bottom identity and launch a long from the wrong side of the market.
        maximum_minutes = {"1m": 6, "5m": 20, "15m": 75, "1H": 240}.get(timeframe, 6)
        recent = self.connection.execute(
            """SELECT * FROM ma_endpoint_lineage WHERE instrument=? AND timeframe=?
            AND direction=? AND zone_status='forming'
            ORDER BY confirmed_bar_time DESC LIMIT 1""",
            (instrument, timeframe, int(direction))).fetchone()
        if recent is not None:
            recent_at = datetime.fromisoformat(
                str(recent["zone_last_seen_at"] or recent["confirmed_bar_time"]).replace("Z", "+00:00"))
            if 0 <= (current_at - recent_at).total_seconds() <= maximum_minutes * 60:
                merged_extreme = (min(float(recent["extreme_price"]), float(extreme_price))
                                  if int(direction) > 0 else
                                  max(float(recent["extreme_price"]), float(extreme_price)))
                self.connection.execute(
                    """UPDATE ma_endpoint_lineage SET endpoint_price=?,extreme_price=?,
                    ma5=?,ma10=?,ma20=?,fan_endpoint_confirmed=max(fan_endpoint_confirmed,?),
                    half_cover_confirmed=max(half_cover_confirmed,?),
                    slow_ma_cross_confirmed=max(slow_ma_cross_confirmed,?),
                    ma5_cross_confirmed=max(ma5_cross_confirmed,?),zone_last_seen_at=?,
                    confirmation_reason=?,updated_at_utc=? WHERE event_key=?""",
                    (float(endpoint_price), merged_extreme, ma5, ma10, ma20,
                     int(fan_endpoint_confirmed), int(half_cover_confirmed), int(slow_ma_cross_confirmed),
                     int(ma5_cross_confirmed), confirmed_bar_time,
                     confirmation_reason, now, str(recent["event_key"])))
                self._rebuild_ma_endpoint_pairs(instrument)
                self.connection.commit()
                return False
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO ma_endpoint_lineage
            (event_key,instrument,timeframe,direction,confirmed_bar_time,
             endpoint_price,extreme_price,ma5,ma10,ma20,fan_endpoint_confirmed,half_cover_confirmed,
             slow_ma_cross_confirmed,ma5_cross_confirmed,zone_last_seen_at,
             confirmation_reason,pair_status,
             identity_code,identity_label,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unpaired','local_endpoint_reversal',?,?,?)""",
            (event_key, instrument, timeframe, int(direction), confirmed_bar_time,
             float(endpoint_price), float(extreme_price), ma5, ma10, ma20,
             int(fan_endpoint_confirmed), int(half_cover_confirmed), int(slow_ma_cross_confirmed),
             int(ma5_cross_confirmed), confirmed_bar_time,
             confirmation_reason, label, now, now),
        )
        self._rebuild_ma_endpoint_pairs(instrument)
        self.connection.commit()
        return bool(cursor.rowcount)

    def _rebuild_ma_endpoint_pairs(self, instrument: str) -> None:
        """Pair each endpoint with the nearest same-side parent; parents may stack."""
        order = ("1m", "5m", "15m", "1H")
        # Pair only contemporaneous endpoint zones.  The child may lead or lag
        # the parent by roughly one parent candle, but an old endpoint must not
        # be recycled by a later reversal.
        windows = {"1m": 6, "5m": 20, "15m": 75}
        rows = list(self.connection.execute(
            """SELECT * FROM ma_endpoint_lineage WHERE instrument=?
            AND fan_endpoint_confirmed=1
            ORDER BY confirmed_bar_time""", (instrument,)))
        by_tf = {tf: [r for r in rows if str(r["timeframe"]) == tf] for tf in order}
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """UPDATE ma_endpoint_lineage SET parent_timeframe=NULL,parent_event_key=NULL,
            pair_status='unpaired',zone_status='forming',
            identity_code='local_endpoint_reversal'
            WHERE instrument=? AND timeframe<>'1H'""", (instrument,))
        self.connection.execute(
            """UPDATE ma_endpoint_lineage SET identity_code='legacy_unverified_noise',
            identity_label=timeframe||'旧版局部高低点（未通过MA5/MA20末端复核，不参与配对）'
            WHERE instrument=? AND fan_endpoint_confirmed=0""", (instrument,))
        for tf in order:
            if tf == "1H":
                continue
            parent_tf = order[order.index(tf) + 1]
            for row in by_tf[tf]:
                at = datetime.fromisoformat(str(row["confirmed_bar_time"]).replace("Z", "+00:00"))
                candidates = []
                for parent in by_tf[parent_tf]:
                    if int(parent["direction"]) != int(row["direction"]):
                        continue
                    if not bool(parent["half_cover_confirmed"]):
                        continue
                    parent_at = datetime.fromisoformat(
                        str(parent["confirmed_bar_time"]).replace("Z", "+00:00"))
                    gap = abs((parent_at - at).total_seconds())
                    if gap <= windows[tf] * 60:
                        candidates.append((gap, parent))
                if not candidates:
                    continue
                parent = min(candidates, key=lambda item: item[0])[1]
                side = "高位做空" if int(row["direction"]) < 0 else "低位做多"
                label = f"{tf}+{parent_tf}三均线末端已配对：真正{side}"
                self.connection.execute(
                    """UPDATE ma_endpoint_lineage SET parent_timeframe=?,
                    parent_event_key=?,pair_status='paired',zone_status='confirmed',
                    identity_code='true_endpoint_reversal',identity_label=?,updated_at_utc=?
                    WHERE event_key=?""",
                    (parent_tf, str(parent["event_key"]), label, now,
                    str(row["event_key"])))

        # Pair rows remain available as reversal identities, but trend locking
        # does not depend on a pair. Keep the newest pair active for audit.
        paired_one = list(self.connection.execute(
            """SELECT child.event_key,child.confirmed_bar_time,child.direction,
            parent.confirmed_bar_time AS parent_bar_time
            FROM ma_endpoint_lineage child
            JOIN ma_endpoint_lineage parent ON parent.event_key=child.parent_event_key
            WHERE child.instrument=? AND child.timeframe='1m'
            AND child.parent_timeframe='5m' AND child.pair_status='paired'""",
            (instrument,)))
        if paired_one:
            latest_pair = max(
                paired_one,
                key=lambda row: max(
                    datetime.fromisoformat(str(row["confirmed_bar_time"]).replace("Z", "+00:00")),
                    datetime.fromisoformat(str(row["parent_bar_time"]).replace("Z", "+00:00"))))
            self.connection.execute(
                """UPDATE ma_endpoint_lineage SET zone_status='released',updated_at_utc=?
                WHERE instrument=? AND timeframe='1m' AND parent_timeframe='5m'
                AND pair_status='paired'""",
                (now, instrument))
            self.connection.execute(
                """UPDATE ma_endpoint_lineage SET zone_status='confirmed',updated_at_utc=?
                WHERE event_key=?""", (now, str(latest_pair["event_key"])))

        # Each higher timeframe independently locks the trend of its child.
        # No 1m+5m or 15m+1H pair is required. Keep one current lock per
        # timeframe so 1H can own 15m while 15m/5m own 1m concurrently.
        lock_scope = {"5m": "1分钟", "15m": "5分钟", "1H": "15分钟"}
        for timeframe, child_label in lock_scope.items():
            locks = by_tf[timeframe]
            if not locks:
                continue
            latest = max(
                locks,
                key=lambda row: datetime.fromisoformat(
                    str(row["confirmed_bar_time"]).replace("Z", "+00:00")))
            self.connection.execute(
                """UPDATE ma_endpoint_lineage SET zone_status='released',updated_at_utc=?
                WHERE instrument=? AND timeframe=? AND fan_endpoint_confirmed=1""",
                (now, instrument, timeframe))
            side = "下降趋势" if int(latest["direction"]) < 0 else "上涨趋势"
            self.connection.execute(
                """UPDATE ma_endpoint_lineage SET zone_status='confirmed',identity_code=?,
                identity_label=?,updated_at_utc=? WHERE event_key=?""",
                ("single_timeframe_direction_lock",
                 f"{timeframe}三均线发散末端独立锁定：{child_label}{side}",
                 now, str(latest["event_key"])))

    def confirm_recent_endpoint_ma5_cross(self, instrument: str, direction: int,
                                          confirmed_at: str,
                                          maximum_age_minutes: int = 15) -> str | None:
        at = datetime.fromisoformat(str(confirmed_at).replace("Z", "+00:00"))
        rows = self.connection.execute(
            """SELECT event_key,confirmed_bar_time FROM ma_endpoint_lineage
            WHERE instrument=? AND timeframe='1m' AND direction=?
            ORDER BY confirmed_bar_time DESC LIMIT 20""",
            (instrument, int(direction))).fetchall()
        for row in rows:
            event_at = datetime.fromisoformat(
                str(row["confirmed_bar_time"]).replace("Z", "+00:00"))
            age = (at - event_at).total_seconds()
            if 0 <= age <= maximum_age_minutes * 60:
                self.connection.execute(
                    """UPDATE ma_endpoint_lineage SET ma5_cross_confirmed=1,
                    confirmation_reason=CASE WHEN confirmation_reason='' THEN ?
                    ELSE confirmation_reason||'；'||? END,updated_at_utc=? WHERE event_key=?""",
                    ("1分钟收盘价成功穿过MA5", "1分钟收盘价成功穿过MA5",
                     datetime.now(timezone.utc).isoformat(), str(row["event_key"])))
                self.connection.commit()
                return str(row["event_key"])
        return None

    def latest_confirmed_one_five_pair(self, instrument: str, direction: int,
                                       require_ma5_cross: bool = True) -> sqlite3.Row | None:
        clause = " AND child.ma5_cross_confirmed=1" if require_ma5_cross else ""
        return self.connection.execute(
            """SELECT child.* FROM ma_endpoint_lineage child
            WHERE child.instrument=? AND child.timeframe='1m' AND child.direction=?
            AND child.pair_status='paired' AND child.zone_status='confirmed'""" + clause
            + " ORDER BY child.confirmed_bar_time DESC LIMIT 1",
            (instrument, int(direction))).fetchone()

    def latest_active_one_five_pair(self, instrument: str) -> sqlite3.Row | None:
        """Return the sole unreleased 1m+5m directional lock."""
        return self.connection.execute(
            """SELECT child.*,parent.confirmed_bar_time AS parent_bar_time
            FROM ma_endpoint_lineage child
            JOIN ma_endpoint_lineage parent ON parent.event_key=child.parent_event_key
            WHERE child.instrument=? AND child.timeframe='1m'
            AND child.parent_timeframe='5m' AND child.pair_status='paired'
            AND child.zone_status='confirmed'
            ORDER BY max(child.confirmed_bar_time,parent.confirmed_bar_time) DESC LIMIT 1""",
            (instrument,)).fetchone()

    def latest_active_endpoint_direction_lock(self, instrument: str,
                                              target_timeframe: str = "1m") -> dict | None:
        """Return the newest single higher-timeframe lock for a child timeframe."""
        sources = (("5m",) if target_timeframe == "1m" else
                   ("15m",) if target_timeframe == "5m" else
                   ("1H",) if target_timeframe == "15m" else ())
        if not sources:
            return None
        placeholders = ",".join("?" for _ in sources)
        row = self.connection.execute(
            f"""SELECT event_key,direction,confirmed_bar_time,confirmed_bar_time AS parent_bar_time,
            timeframe,identity_code,identity_label FROM ma_endpoint_lineage
            WHERE instrument=? AND zone_status='confirmed' AND fan_endpoint_confirmed=1
            AND timeframe IN ({placeholders})
            ORDER BY confirmed_bar_time DESC LIMIT 1""", (instrument, *sources)).fetchone()
        return dict(row) if row is not None else None

    def latest_one_minute_fan_endpoint(self, instrument: str) -> dict | None:
        row = self.connection.execute(
            """SELECT * FROM ma_endpoint_lineage WHERE instrument=? AND timeframe='1m'
            AND fan_endpoint_confirmed=1 ORDER BY confirmed_bar_time DESC LIMIT 1""",
            (instrument,)).fetchone()
        return dict(row) if row is not None else None

    def active_one_minute_reversal_trend_lock(self, instrument: str) -> dict | None:
        row = self.connection.execute(
            """SELECT * FROM one_minute_reversal_trend_locks
            WHERE instrument=? AND status='confirmed'""", (instrument,)).fetchone()
        return dict(row) if row is not None else None

    def has_reversal_trial_since(self, instrument: str, direction: int,
                                 anchor_time: str) -> bool:
        row = self.connection.execute(
            """SELECT 1 FROM trade_lifecycle WHERE instrument=? AND direction=?
            AND signal_time>=? AND status IN ('submitted','open','closed')
            AND (branch LIKE '%reversal%' OR branch LIKE '%frozen_stage%') LIMIT 1""",
            (instrument, int(direction), anchor_time)).fetchone()
        return row is not None

    def confirm_one_minute_reversal_trend_lock(
        self, *, instrument: str, endpoint_event_key: str, direction: int,
        anchor_time: str, anchor_extreme: float, confirmed_at: str, reason: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT INTO one_minute_reversal_trend_locks
            (instrument,endpoint_event_key,direction,anchor_time,anchor_extreme,status,
             confirmation_reason,confirmed_at,failed_at,failure_reason,updated_at_utc)
            VALUES(?,?,?,?,?,'confirmed',?,?,NULL,'',?)
            ON CONFLICT(instrument) DO UPDATE SET endpoint_event_key=excluded.endpoint_event_key,
            direction=excluded.direction,anchor_time=excluded.anchor_time,
            anchor_extreme=excluded.anchor_extreme,status='confirmed',
            confirmation_reason=excluded.confirmation_reason,confirmed_at=excluded.confirmed_at,
            failed_at=NULL,failure_reason='',updated_at_utc=excluded.updated_at_utc""",
            (instrument, endpoint_event_key, int(direction), anchor_time,
             float(anchor_extreme), reason, confirmed_at, now))
        self.connection.commit()

    def fail_one_minute_reversal_trend_lock(
        self, instrument: str, failed_at: str, reason: str,
    ) -> None:
        self.connection.execute(
            """UPDATE one_minute_reversal_trend_locks SET status='failed',failed_at=?,
            failure_reason=?,updated_at_utc=? WHERE instrument=? AND status='confirmed'""",
            (failed_at, reason, datetime.now(timezone.utc).isoformat(), instrument))
        self.connection.commit()

    def mark_ma_endpoint_trade_upgrades(self, event_keys: list[str],
                                        trade_uids: list[str]) -> None:
        if not event_keys:
            return
        payload = json.dumps(sorted(set(trade_uids)), ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        self.connection.executemany(
            """UPDATE ma_endpoint_lineage SET upgraded_trade_uids_json=?,
            updated_at_utc=? WHERE event_key=?""",
            [(payload, now, key) for key in event_keys],)
        self.connection.commit()

    def clear_trade_trailing_algo(self, trade_uid: str) -> None:
        self.connection.execute(
            "UPDATE trade_lifecycle SET algo_id='',updated_at_utc=? WHERE trade_uid=?",
            (datetime.now(timezone.utc).isoformat(), trade_uid))
        self.connection.commit()

    def recent_ma_endpoint_lineage(self, instrument: str,
                                   limit: int = 300) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT child.*, parent.confirmed_bar_time AS parent_bar_time,
            parent.endpoint_price AS parent_price
            FROM ma_endpoint_lineage child
            LEFT JOIN ma_endpoint_lineage parent ON parent.event_key=child.parent_event_key
            WHERE child.instrument=? ORDER BY child.confirmed_bar_time DESC LIMIT ?""",
            (instrument, int(limit))))

    def record_market_pattern(self, *, event_key: str, instrument: str,
                              pattern_type: str, direction: int,
                              confirmed_bar_time: str, status: str,
                              entry_reference: float, stop_reference: float,
                              features: dict) -> bool:
        """Persist a deduplicated feature snapshot for later offline review.

        Observations never tune thresholds or submit orders by themselves.
        """
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO market_pattern_observations
            (event_key,instrument,pattern_type,direction,confirmed_bar_time,status,
             entry_reference,stop_reference,features_json,outcome_status,outcome_json,
             created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,'pending','{}',?,?)""",
            (event_key, instrument, pattern_type, direction, confirmed_bar_time,
             status, entry_reference, stop_reference,
             json.dumps(features, ensure_ascii=False, sort_keys=True), now, now),
        )
        self.connection.commit()
        if cursor.rowcount:
            self.record_event("market_pattern_observed", {
                "event_key": event_key, "instrument": instrument,
                "pattern_type": pattern_type, "direction": direction,
                "status": status, "confirmed_bar_time": confirmed_bar_time,
            })
            return True
        return False

    def pending_market_patterns(self, instrument: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT * FROM market_pattern_observations
            WHERE instrument=? AND outcome_status='pending'
            ORDER BY confirmed_bar_time""", (instrument,),
        ))

    def recent_market_patterns(self, instrument: str, limit: int = 500) -> list[sqlite3.Row]:
        """Return recent pattern history including resolved/missed observations."""
        return list(self.connection.execute(
            """SELECT * FROM market_pattern_observations
            WHERE instrument=? ORDER BY confirmed_bar_time DESC LIMIT ?""",
            (instrument, limit),
        ))

    def resolve_market_pattern(self, event_key: str, *, outcome_status: str,
                               outcome: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """UPDATE market_pattern_observations
            SET outcome_status=?,outcome_json=?,updated_at_utc=? WHERE event_key=?""",
            (outcome_status, json.dumps(outcome, ensure_ascii=False, sort_keys=True),
             now, event_key),
        )
        self.connection.commit()
        self.record_event("market_pattern_resolved", {
            "event_key": event_key, "outcome_status": outcome_status, **outcome,
        })

    def resolve_market_pattern_for_order(self, event_key: str, *, direction: int,
                                         outcome: dict, submitted_bar_time=None,
                                         maximum_age_minutes: int = 10) -> bool:
        """Resolve only a same-side pattern belonging to the current order window."""
        now = datetime.now(timezone.utc).isoformat()
        if submitted_bar_time is not None:
            row = self.connection.execute(
                """SELECT confirmed_bar_time,direction,outcome_status
                FROM market_pattern_observations WHERE event_key=?""", (event_key,),
            ).fetchone()
            if row is None or int(row["direction"]) != int(direction) or row["outcome_status"] != "pending":
                return False
            def utc_stamp(value):
                if isinstance(value, datetime):
                    parsed = value
                else:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
                        else parsed.astimezone(timezone.utc))
            confirmed = utc_stamp(row["confirmed_bar_time"])
            submitted = utc_stamp(submitted_bar_time)
            age_seconds = (submitted - confirmed).total_seconds()
            if age_seconds < -60 or age_seconds > maximum_age_minutes * 60:
                return False
        payload = {**outcome, "order_direction": int(direction),
                   "submitted_at_utc": now}
        cursor = self.connection.execute(
            """UPDATE market_pattern_observations
            SET outcome_status='triggered_order_submitted',outcome_json=?,updated_at_utc=?
            WHERE event_key=? AND direction=? AND outcome_status='pending'""",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), now,
             event_key, int(direction)),
        )
        self.connection.commit()
        if not cursor.rowcount:
            return False
        self.record_event("market_pattern_resolved", {
            "event_key": event_key, "outcome_status": "triggered_order_submitted",
            **payload,
        })
        return True

    def record_sniper_protection(self, *, client_order_id: str, order_id: str,
                                 strategy_id: str, strategy_version: str,
                                 instrument: str, direction: int, entry_price: float,
                                 normal_stop: float, disaster_stop: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT OR REPLACE INTO sniper_protection_states
            (client_order_id,order_id,strategy_id,strategy_version,instrument,direction,
             entry_price,normal_stop,disaster_stop,status,filled_at_utc,entry_bar_time,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,'pending',NULL,NULL,?)""",
            (client_order_id, order_id, strategy_id, strategy_version, instrument,
             direction, entry_price, normal_stop, disaster_stop, now),
        )
        self.connection.commit()

    def sniper_protections(self, strategy_id: str, instrument: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT * FROM sniper_protection_states
            WHERE strategy_id=? AND instrument=? AND status IN ('pending','observing')
            ORDER BY updated_at_utc""", (strategy_id, instrument),
        ).fetchall())

    def sniper_protection(self, client_order_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sniper_protection_states WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()

    def entry_snapshot(self, snapshot_uid: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM entry_snapshots WHERE snapshot_uid=?",
            (snapshot_uid,),
        ).fetchone()

    def update_sniper_protection(self, client_order_id: str, *, status: str,
                                 filled_at_utc: str | None = None,
                                 entry_bar_time: str | None = None) -> None:
        self.connection.execute(
            """UPDATE sniper_protection_states SET status=?,
            filled_at_utc=COALESCE(?,filled_at_utc),entry_bar_time=COALESCE(?,entry_bar_time),
            updated_at_utc=? WHERE client_order_id=?""",
            (status, filled_at_utc, entry_bar_time,
             datetime.now(timezone.utc).isoformat(), client_order_id),
        )
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {str(row[1]) for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _runtime_from_row(row: sqlite3.Row) -> StrategyRuntimeState:
        return StrategyRuntimeState(
            account_id=str(row["account_id"]),
            strategy_id=str(row["strategy_id"]),
            state=StrategyState(str(row["state"])),
            revision=int(row["revision"]),
            cycle_id=(str(row["cycle_id"]) if row["cycle_id"] is not None else None),
            updated_at_utc=str(row["updated_at_utc"]),
        )

    def strategy_runtime_state(self, account_id: str, strategy_id: str) -> StrategyRuntimeState:
        """Load lifecycle state, creating a safe STOPPED row on first use."""
        account_id, strategy_id = str(account_id).strip(), str(strategy_id).strip()
        if not account_id or not strategy_id:
            raise ValueError("account_id and strategy_id are required")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT OR IGNORE INTO strategy_runtime_state
            (account_id,strategy_id,state,revision,cycle_id,updated_at_utc)
            VALUES(?,?,?,0,NULL,?)""",
            (account_id, strategy_id, StrategyState.STOPPED.value, now),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM strategy_runtime_state WHERE account_id=? AND strategy_id=?",
            (account_id, strategy_id),
        ).fetchone()
        assert row is not None
        return self._runtime_from_row(row)

    def transition_strategy_state(
        self, account_id: str, strategy_id: str, target: StrategyState, *,
        actor: str, reason_code: str, request_id: str, expected_revision: int,
        snapshot: dict | None = None, health_check_passed: bool = False,
        close_reconciled: bool = False, cycle_id: str | None = None,
    ) -> StrategyRuntimeState:
        """Atomically apply and audit one idempotent lifecycle transition."""
        account_id, strategy_id = str(account_id).strip(), str(strategy_id).strip()
        request_id, reason_code = str(request_id).strip(), str(reason_code).strip()
        actor, target = str(actor).strip().lower(), StrategyState(target)
        if not account_id or not strategy_id or not request_id or not reason_code:
            raise ValueError("account_id, strategy_id, request_id and reason_code are required")
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            replay = self.connection.execute(
                """SELECT account_id,strategy_id,to_state,revision
                FROM strategy_state_history WHERE request_id=?""", (request_id,),
            ).fetchone()
            if replay is not None:
                if (str(replay["account_id"]), str(replay["strategy_id"]),
                        str(replay["to_state"])) != (account_id, strategy_id, target.value):
                    raise ValueError("request_id was already used for a different transition")
                row = self.connection.execute(
                    "SELECT * FROM strategy_runtime_state WHERE account_id=? AND strategy_id=?",
                    (account_id, strategy_id),
                ).fetchone()
                self.connection.commit()
                assert row is not None
                return self._runtime_from_row(row)

            self.connection.execute(
                """INSERT OR IGNORE INTO strategy_runtime_state
                (account_id,strategy_id,state,revision,cycle_id,updated_at_utc)
                VALUES(?,?,?,0,NULL,?)""",
                (account_id, strategy_id, StrategyState.STOPPED.value, now),
            )
            row = self.connection.execute(
                "SELECT * FROM strategy_runtime_state WHERE account_id=? AND strategy_id=?",
                (account_id, strategy_id),
            ).fetchone()
            assert row is not None
            current, revision = StrategyState(str(row["state"])), int(row["revision"])
            if revision != int(expected_revision):
                raise StrategyRevisionConflict(
                    f"expected revision {expected_revision}, current revision is {revision}")
            if not transition_allowed(
                current, target, actor=actor,
                health_check_passed=health_check_passed,
                close_reconciled=close_reconciled,
            ):
                raise InvalidStrategyTransition(
                    f"transition {current.value}->{target.value} is not allowed for {actor}")
            next_revision = revision + 1
            next_cycle = cycle_id if cycle_id is not None else row["cycle_id"]
            self.connection.execute(
                """UPDATE strategy_runtime_state SET state=?,revision=?,cycle_id=?,updated_at_utc=?
                WHERE account_id=? AND strategy_id=? AND revision=?""",
                (target.value, next_revision, next_cycle, now,
                 account_id, strategy_id, revision),
            )
            self.connection.execute(
                """INSERT INTO strategy_state_history
                (account_id,strategy_id,from_state,to_state,reason_code,requested_by,
                 request_id,revision,snapshot_json,created_at_utc)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (account_id, strategy_id, current.value, target.value, reason_code,
                 actor, request_id, next_revision,
                 json.dumps(snapshot or {}, ensure_ascii=False, sort_keys=True), now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.strategy_runtime_state(account_id, strategy_id)

    def strategy_state_history(self, account_id: str, strategy_id: str,
                               limit: int = 100) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT * FROM strategy_state_history
            WHERE account_id=? AND strategy_id=? ORDER BY transition_id DESC LIMIT ?""",
            (account_id, strategy_id, max(1, int(limit))),
        ).fetchall()

    def entry_order_label(self, order_id: str) -> str:
        row = self.connection.execute(
            "SELECT strategy_id FROM entry_snapshots WHERE order_id=? ORDER BY created_at_utc DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        labels = {
            "strategy_01": "策略01｜双均线趋势",
            "strategy_02": "策略02｜区间高低点反转",
            "strategy_03": "策略03｜MA20趋势翻转回抽",
        }
        if row:
            return labels.get(str(row[0]), str(row[0]))
        row = self.connection.execute(
            "SELECT strategy_id FROM trade_lifecycle WHERE order_id=? ORDER BY updated_at_utc DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        if row:
            return labels.get(str(row[0]), str(row[0]))
        return ""

    def claim_order_fill_notice(self, order_id: str, strategy_label: str, filled_at_utc: str) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO order_fill_notices
            (order_id,strategy_label,filled_at_utc,notified_at_utc) VALUES(?,?,?,?)""",
            (order_id, strategy_label, filled_at_utc, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def record_event(self, event_type: str, payload: dict) -> None:
        self.connection.execute(
            "INSERT INTO events(created_at_utc,event_type,payload_json) VALUES(?,?,?)",
            (datetime.now(timezone.utc).isoformat(), event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        self.connection.commit()

    def record_hourly_boundary_sample(self, *, instrument: str, beijing_hour: str,
                                      endpoint_direction: int, five_direction: int,
                                      fifteen_direction: int, hour_direction: int,
                                      classification: str, evidence: dict) -> bool:
        """Persist at most one 5m/15m/1H joint-boundary sample per Beijing hour."""
        observed = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO hourly_multiframe_boundary_samples
            (instrument,beijing_hour,observed_at_utc,one_minute_endpoint_direction,
             five_direction,fifteen_direction,hour_direction,classification,evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (instrument, beijing_hour, observed, int(endpoint_direction), int(five_direction),
             int(fifteen_direction), int(hour_direction), classification,
             json.dumps(evidence, ensure_ascii=False, sort_keys=True)),
        )
        if cursor.rowcount == 1:
            self.connection.execute(
                "INSERT INTO events(created_at_utc,event_type,payload_json) VALUES(?,?,?)",
                (observed, "hourly_multiframe_boundary_sample",
                 json.dumps({"instrument": instrument, "direction": int(endpoint_direction),
                             "reason": classification + "；" + str(evidence.get("reason", "")),
                             "beijing_hour": beijing_hour},
                            ensure_ascii=False, sort_keys=True)),
            )
        self.connection.commit()
        return cursor.rowcount == 1

    def freeze_directional_cover_anchor(self, *, instrument: str, direction: int,
                                        five_bar_time: str, one_anchor_time: str,
                                        stop_reference: float,
                                        cover_evidence: str = "") -> sqlite3.Row:
        """Persist the first compact 1m stop seen when live 5m reaches 45% cover."""
        first_seen = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO directional_cover_anchors
            (instrument,direction,five_bar_time,one_anchor_time,stop_reference,created_at_utc)
            VALUES(?,?,?,?,?,?)""",
            (instrument, int(direction), five_bar_time, one_anchor_time,
             float(stop_reference), first_seen),
        )
        if cursor.rowcount == 1:
            self.connection.execute(
                "INSERT INTO events(created_at_utc,event_type,payload_json) VALUES(?,?,?)",
                (first_seen, "five_minute_cover_first_threshold_reached",
                 json.dumps({"instrument": instrument, "direction": int(direction),
                             "five_bar_time": five_bar_time,
                             "first_reached_at_utc": first_seen,
                             "one_anchor_time": one_anchor_time,
                             "stop_reference": float(stop_reference),
                             "cover_evidence": cover_evidence},
                            ensure_ascii=False, sort_keys=True)),
            )
        self.connection.commit()
        return self.connection.execute(
            """SELECT * FROM directional_cover_anchors
            WHERE instrument=? AND direction=? AND five_bar_time=?""",
            (instrument, int(direction), five_bar_time),
        ).fetchone()

    def directional_cover_anchor(self, *, instrument: str, direction: int,
                                 five_bar_time: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT * FROM directional_cover_anchors
            WHERE instrument=? AND direction=? AND five_bar_time=?""",
            (instrument, int(direction), five_bar_time),
        ).fetchone()

    def latest_directional_cover_anchor(self, *, instrument: str,
                                        direction: int) -> sqlite3.Row | None:
        """Return the newest frozen 45% cover anchor for reconnect recovery."""
        return self.connection.execute(
            """SELECT * FROM directional_cover_anchors
            WHERE instrument=? AND direction=?
            ORDER BY five_bar_time DESC,id DESC LIMIT 1""",
            (instrument, int(direction)),
        ).fetchone()

    def record_market_research_snapshot(self, *, instrument: str,
                                        confirmed_five_minute_time: str,
                                        strategy_version: str, source: str,
                                        context: dict) -> bool:
        """Persist one de-duplicated 5m research sample; never an order input."""
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO market_research_snapshots
            (instrument,confirmed_five_minute_time,strategy_version,source,
             decision_eligible,context_json,created_at_utc) VALUES(?,?,?,?,0,?,?)""",
            (instrument, confirmed_five_minute_time, strategy_version, source,
             json.dumps(context, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def record_external_research_signal(self, *, event_id: str, source: str,
                                        instrument: str, event_time: str,
                                        event_type: str, payload: dict) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO external_research_signals
            (event_id,source,instrument,event_time,event_type,decision_eligible,
             payload_json,created_at_utc) VALUES(?,?,?,?,?,0,?,?)""",
            (event_id, source, instrument, event_time, event_type,
             json.dumps(payload, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def unlabeled_market_research_snapshots(self, *, instrument: str,
                                            strategy_version: str,
                                            source: str, direction: int,
                                            horizon_bars: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT s.* FROM market_research_snapshots s
            LEFT JOIN market_research_labels l ON l.snapshot_id=s.id
             AND l.direction=? AND l.horizon_bars=?
            WHERE s.instrument=? AND s.strategy_version=? AND s.source=?
             AND l.snapshot_id IS NULL ORDER BY s.confirmed_five_minute_time""",
            (direction, horizon_bars, instrument, strategy_version, source),
        ).fetchall()

    def record_market_research_label(self, *, snapshot_id: int, direction: int,
                                     horizon_bars: int, label: dict) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO market_research_labels
            (snapshot_id,direction,horizon_bars,label_json,created_at_utc)
            VALUES(?,?,?,?,?)""",
            (snapshot_id, direction, horizon_bars,
             json.dumps(label, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def record_ma_cross(self, *, instrument: str, timeframe: str, cross_time: str,
                        direction: int, cross_name: str, price: float,
                        range_position: float, valid_local_extreme: bool,
                        decision: str, reason: str, compared_timeframe: str | None = None,
                        compared_cross_time: str | None = None,
                        comparison_gap_seconds: float | None = None) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO ma_cross_events
            (instrument,timeframe,cross_time,direction,cross_name,price,range_position,
             valid_local_extreme,decision,reason,compared_timeframe,compared_cross_time,
             comparison_gap_seconds,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (instrument, timeframe, cross_time, direction, cross_name, price, range_position,
             int(valid_local_extreme), decision, reason, compared_timeframe,
             compared_cross_time, comparison_gap_seconds,
             datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def latest_ma_cross(self, instrument: str, timeframe: str,
                        direction: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT * FROM ma_cross_events WHERE instrument=? AND timeframe=? AND direction=?
            ORDER BY cross_time DESC,id DESC LIMIT 1""",
            (instrument, timeframe, direction),
        ).fetchone()

    def publish_shared_signal(self, *, instrument: str, signal_type: str, direction: int,
                              confirmed_bar_time: str, reason: str, stop_price: float,
                              source_strategy: str, ttl_seconds: int = 300) -> bool:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        event_key = f"{instrument}|{signal_type}|{direction}|{confirmed_bar_time}"
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO shared_signal_events
            (event_key,instrument,signal_type,direction,confirmed_bar_time,reason,stop_price,
             source_strategy,created_at_utc,expires_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (event_key, instrument, signal_type, direction, confirmed_bar_time, reason,
             stop_price, source_strategy, now.isoformat(),
             (now + timedelta(seconds=ttl_seconds)).isoformat()),
        )
        self.connection.commit()
        if cursor.rowcount == 1:
            self.record_event("shared_signal_published", {
                "event_key": event_key, "instrument": instrument, "signal_type": signal_type,
                "direction": direction, "confirmed_bar_time": confirmed_bar_time,
                "source_strategy": source_strategy, "expires_in_seconds": ttl_seconds,
            })
        return cursor.rowcount == 1

    def latest_shared_signal(self, instrument: str, signal_types: tuple[str, ...]) -> sqlite3.Row | None:
        if not signal_types:
            return None
        marks = ",".join("?" for _ in signal_types)
        return self.connection.execute(
            f"""SELECT * FROM shared_signal_events WHERE instrument=?
            AND signal_type IN ({marks}) AND expires_at_utc>=?
            ORDER BY confirmed_bar_time DESC,created_at_utc DESC LIMIT 1""",
            (instrument, *signal_types, datetime.now(timezone.utc).isoformat()),
        ).fetchone()

    def record_shared_signal_consumed(self, event_key: str, strategy_id: str) -> None:
        self.record_event("shared_signal_consumed", {
            "event_key": event_key, "strategy_id": strategy_id,
        })

    def record_loss_review(self, trade: dict) -> None:
        from .loss_review import diagnose_losing_trade
        diagnosis = diagnose_losing_trade(trade)
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT INTO loss_reviews
            (trade_uid,strategy_id,strategy_version,close_time,direction,net_pnl,category,cause,
             evidence,recommendation,confidence,review_status,created_at_utc,updated_at_utc,trigger_snapshot)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trade_uid) DO UPDATE SET
            net_pnl=excluded.net_pnl,category=excluded.category,cause=excluded.cause,
            evidence=excluded.evidence,recommendation=excluded.recommendation,
            confidence=excluded.confidence,updated_at_utc=excluded.updated_at_utc""",
            (str(trade.get("trade_uid", "")), str(trade.get("strategy_id", "unknown")),
             str(trade.get("strategy_version", "unknown")), str(trade.get("close_time") or now),
             int(trade.get("direction") or 0), float(trade.get("net_pnl") or 0),
             diagnosis.category, diagnosis.cause, diagnosis.evidence, diagnosis.recommendation,
             diagnosis.confidence, "待汇总", now, now,
             str(trade.get("signal_reason") or "历史订单无快照")),
        )
        self.connection.commit()

    def record_entry_snapshot(self, *, snapshot_uid: str, strategy_id: str, strategy_version: str,
                              order_id: str, signal_time: str, direction: int, branch: str,
                              trigger_reason: str, context: dict, entry_reference: float,
                              stop_price: float, take_profit_price: float,
                              status: str = "submitted") -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO entry_snapshots
            (snapshot_uid,strategy_id,strategy_version,order_id,signal_time,direction,branch,
             trigger_reason,context_json,entry_reference,stop_price,take_profit_price,status,
             matched_loss_uid,created_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)""",
            (snapshot_uid, strategy_id, strategy_version, order_id, signal_time, direction, branch,
             trigger_reason, json.dumps(context, ensure_ascii=False, sort_keys=True),
             entry_reference, stop_price, take_profit_price, status,
             datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def loss_reviews(self, limit: int = 500) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM loss_reviews ORDER BY close_time DESC LIMIT ?", (limit,),
        ).fetchall())

    def record_external_loss(self, *, trade_uid: str, strategy_id: str, close_time: str,
                             direction: int, gross_pnl: float, fee: float,
                             evidence: str) -> None:
        """Backfill an OKX losing close when no local lifecycle evidence exists."""
        net = gross_pnl + fee
        if net >= 0:
            return
        duplicate = self.connection.execute(
            """SELECT 1 FROM loss_reviews WHERE strategy_id=? AND ABS(net_pnl-?)<0.000001
            AND ABS(strftime('%s',close_time)-strftime('%s',?))<=600 LIMIT 1""",
            (strategy_id, net, close_time),
        ).fetchone()
        if duplicate:
            return
        now = datetime.now(timezone.utc).isoformat()
        # ISO strings with different UTC offsets are not chronologically
        # sortable as plain SQLite text. Compare actual UTC instants instead.
        try:
            closed_at = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)
            closed_at = closed_at.astimezone(timezone.utc)
        except ValueError:
            closed_at = None
        candidates = self.connection.execute(
            """SELECT * FROM entry_snapshots WHERE strategy_id=? AND direction=?
            AND matched_loss_uid IS NULL ORDER BY created_at_utc DESC LIMIT 200""",
            (strategy_id, direction),
        ).fetchall()
        eligible = []
        for candidate in candidates:
            try:
                signal_at = datetime.fromisoformat(
                    str(candidate["signal_time"]).replace("Z", "+00:00"))
                if signal_at.tzinfo is None:
                    signal_at = signal_at.replace(tzinfo=timezone.utc)
                signal_at = signal_at.astimezone(timezone.utc)
            except ValueError:
                continue
            if closed_at is not None and 0 <= (closed_at - signal_at).total_seconds() <= 21600:
                eligible.append((signal_at, candidate))
        snapshot = max(eligible, key=lambda item: item[0])[1] if eligible else None
        if snapshot:
            from .loss_review import diagnose_losing_trade
            snapshot_dict = dict(snapshot)
            diagnosis = diagnose_losing_trade({
                **snapshot_dict, "gross_pnl": gross_pnl, "total_fees": fee, "net_pnl": net,
                "exit_reason": "OKX亏损成交", "signal_reason": snapshot_dict["trigger_reason"],
                "signal_context_json": snapshot_dict["context_json"],
            })
            category, cause = diagnosis.category, diagnosis.cause
            recommendation, confidence = diagnosis.recommendation, diagnosis.confidence
            trigger_snapshot = str(snapshot["trigger_reason"])
            evidence = f"{evidence}；开仓快照={trigger_snapshot}；上下文={snapshot['context_json']}"
        else:
            category, cause = "待补充行情证据", "已确认该笔成交为净亏损，但本地没有完整开仓触发快照，不能武断归因。"
            recommendation = "保留订单号并与当时K线、触发条件和保护单核对；归因完成前不得据此自动修改策略。"
            confidence, trigger_snapshot = "低", "历史订单无快照"
        self.connection.execute(
            """INSERT OR IGNORE INTO loss_reviews
            (trade_uid,strategy_id,strategy_version,close_time,direction,net_pnl,category,cause,
             evidence,recommendation,confidence,review_status,created_at_utc,updated_at_utc,trigger_snapshot)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_uid, strategy_id, "OKX成交补录", close_time, direction, net,
             category, cause, evidence, recommendation, confidence,
             "待汇总" if snapshot else "待人工复核", now, now, trigger_snapshot),
        )
        if snapshot:
            self.connection.execute(
                "UPDATE entry_snapshots SET matched_loss_uid=? WHERE snapshot_uid=?",
                (trade_uid, snapshot["snapshot_uid"]),
            )
        self.connection.commit()

    def open_trade_lifecycle(self, *, trade_uid: str, strategy_id: str, strategy_version: str,
                             instrument: str, direction: int, signal_time: str, signal_reason: str,
                             signal_context: dict, order_id: str, algo_id: str,
                             entry_reference: float, stop_price: float,
                             trailing_activation: float, trailing_callback: float,
                             branch: str = "legacy_unclassified") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT OR REPLACE INTO trade_lifecycle
            (trade_uid,strategy_id,strategy_version,instrument,direction,status,signal_time,signal_reason,
             signal_context_json,order_id,algo_id,entry_reference,stop_price,trailing_activation,
             trailing_callback,updated_at_utc,branch)
            VALUES(?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_uid, strategy_id, strategy_version, instrument, direction, signal_time, signal_reason,
             json.dumps(signal_context, ensure_ascii=False, sort_keys=True), order_id, algo_id,
             entry_reference, stop_price, trailing_activation, trailing_callback, now, branch),
        )
        self.connection.commit()
        self.record_entry_snapshot(
            snapshot_uid=trade_uid, strategy_id=strategy_id, strategy_version=strategy_version,
            order_id=order_id, signal_time=signal_time, direction=direction, branch=branch,
            trigger_reason=signal_reason, context=signal_context,
            entry_reference=entry_reference, stop_price=stop_price,
            take_profit_price=trailing_activation, status="submitted",
        )

    def open_trade_lifecycle_if_missing(
            self, *, trade_uid: str, strategy_id: str, strategy_version: str,
            instrument: str, direction: int, signal_time: str, signal_reason: str,
            signal_context: dict, order_id: str, algo_id: str,
            entry_reference: float, stop_price: float,
            trailing_activation: float, trailing_callback: float,
            branch: str = "legacy_unclassified") -> bool:
        """Create a lifecycle after a resting order fills without replacing history."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO trade_lifecycle
            (trade_uid,strategy_id,strategy_version,instrument,direction,status,signal_time,signal_reason,
             signal_context_json,order_id,algo_id,entry_reference,stop_price,trailing_activation,
             trailing_callback,updated_at_utc,branch)
            VALUES(?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_uid, strategy_id, strategy_version, instrument, direction, signal_time,
             signal_reason, json.dumps(signal_context, ensure_ascii=False, sort_keys=True),
             order_id, algo_id, entry_reference, stop_price, trailing_activation,
             trailing_callback, now, branch),
        )
        self.connection.commit()
        if not cursor.rowcount:
            return False
        self.record_entry_snapshot(
            snapshot_uid=trade_uid, strategy_id=strategy_id,
            strategy_version=strategy_version, order_id=order_id,
            signal_time=signal_time, direction=direction, branch=branch,
            trigger_reason=signal_reason, context=signal_context,
            entry_reference=entry_reference, stop_price=stop_price,
            take_profit_price=trailing_activation, status="filled",
        )
        self.record_event("trade_lifecycle_opened_from_resting_fill", {
            "trade_uid": trade_uid, "order_id": order_id, "branch": branch,
            "strategy_version": strategy_version,
        })
        return True

    def claim_reversal_trial(self, instrument: str, direction: int,
                             anchor_time: str, trade_uid: str) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO reversal_trial_claims VALUES(?,?,?,?,?)",
            (instrument, direction, anchor_time, trade_uid,
             datetime.now(timezone.utc).isoformat()))
        self.connection.commit()
        return cursor.rowcount == 1

    def strategy_trade_lifecycles(self, instrument: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM trade_lifecycle WHERE strategy_id='strategy_01' "
            "AND instrument=? ORDER BY signal_time,trade_uid", (instrument,)))

    def open_trade_lifecycles(self, strategy_version: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM trade_lifecycle WHERE strategy_version=? AND status='open' ORDER BY signal_time",
            (strategy_version,),
        ).fetchall())

    def update_trade_lifecycle_context(self, trade_uid: str, context: dict) -> None:
        """Persist a post-entry classification upgrade without creating another order."""
        self.connection.execute(
            """UPDATE trade_lifecycle SET signal_context_json=?,updated_at_utc=?
            WHERE trade_uid=? AND status='open'""",
            (json.dumps(context, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat(), trade_uid),
        )
        self.connection.commit()

    def update_trade_excursion(self, trade_uid: str, *, observed_high: float,
                               observed_low: float, observed_price: float) -> None:
        """Persist MFE/MAE for an open trade without changing execution state."""
        row = self.connection.execute(
            "SELECT direction,entry_reference,max_favorable_price,max_adverse_price "
            "FROM trade_lifecycle WHERE trade_uid=? AND status='open'", (trade_uid,),
        ).fetchone()
        if not row or not row["entry_reference"]:
            return
        entry, direction = float(row["entry_reference"]), int(row["direction"])
        favorable = observed_high if direction > 0 else observed_low
        adverse = observed_low if direction > 0 else observed_high
        old_favorable, old_adverse = row["max_favorable_price"], row["max_adverse_price"]
        best = favorable if old_favorable is None else (max(old_favorable, favorable) if direction > 0 else min(old_favorable, favorable))
        worst = adverse if old_adverse is None else (min(old_adverse, adverse) if direction > 0 else max(old_adverse, adverse))
        self.connection.execute(
            """UPDATE trade_lifecycle SET max_favorable_price=?,max_adverse_price=?,
            mfe_points=?,mae_points=?,last_observed_price=?,last_observed_at_utc=?,updated_at_utc=?
            WHERE trade_uid=? AND status='open'""",
            (best, worst, max(0.0, direction * (best - entry)),
             max(0.0, -direction * (worst - entry)), observed_price,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), trade_uid),
        )
        self.connection.commit()

    def update_stop_followups(self, observed_price: float) -> int:
        """Fill 5/10/20-minute post-stop audit checkpoints from normal scan prices."""
        now = datetime.now(timezone.utc)
        changed = 0
        rows = self.connection.execute(
            """SELECT trade_uid,close_time,stop_followup_price_5m,stop_followup_price_10m,
            stop_followup_price_20m FROM trade_lifecycle WHERE status='closed'
            AND exit_reason IN ('stop_loss','服务器止损触发')
            AND stop_followup_price_20m IS NULL"""
        ).fetchall()
        for row in rows:
            closed = datetime.fromisoformat(str(row["close_time"]).replace("Z", "+00:00"))
            if closed.tzinfo is None:
                closed = closed.replace(tzinfo=timezone.utc)
            age = (now - closed.astimezone(timezone.utc)).total_seconds()
            updates = []
            for minutes, column in ((5, "stop_followup_price_5m"), (10, "stop_followup_price_10m"), (20, "stop_followup_price_20m")):
                if age >= minutes * 60 and row[column] is None:
                    updates.append(column)
            if updates:
                self.connection.execute(
                    f"UPDATE trade_lifecycle SET {','.join(f'{name}=?' for name in updates)} WHERE trade_uid=?",
                    (*([observed_price] * len(updates)), row["trade_uid"]),
                )
                changed += 1
        self.connection.commit()
        return changed

    def has_open_same_side_trade(self, strategy_id: str, instrument: str, direction: int,
                                 *, strategy_version: str | None = None) -> bool:
        sql = ("SELECT 1 FROM trade_lifecycle WHERE strategy_id=? AND instrument=? "
               "AND direction=? AND status='open'")
        params: list[object] = [strategy_id, instrument, direction]
        if strategy_version is not None:
            sql += " AND strategy_version=?"
            params.append(strategy_version)
        sql += " LIMIT 1"
        return self.connection.execute(sql, params).fetchone() is not None

    def mark_trade_exit_requested(self, strategy_id: str, instrument: str, direction: int,
                                  exit_reason: str) -> None:
        self.connection.execute(
            """UPDATE trade_lifecycle SET exit_reason=?,updated_at_utc=?
            WHERE strategy_id=? AND instrument=? AND direction=? AND status='open'""",
            (exit_reason, datetime.now(timezone.utc).isoformat(), strategy_id, instrument, direction),
        )
        self.connection.commit()

    def mark_trade_exit_requested_by_uid(self, trade_uid: str, exit_reason: str) -> None:
        """Attach a partial same-side exit to exactly one lifecycle layer."""
        self.connection.execute(
            """UPDATE trade_lifecycle SET exit_reason=?,updated_at_utc=?
            WHERE trade_uid=? AND status='open'""",
            (exit_reason, datetime.now(timezone.utc).isoformat(), trade_uid),
        )
        self.connection.commit()

    def close_trade_lifecycle(self, trade_uid: str, *, close_price: float | None,
                              gross_pnl: float | None, total_fees: float | None,
                              exit_reason: str, close_time: str | None = None) -> None:
        net = None if gross_pnl is None else gross_pnl + (total_fees or 0.0)
        self.connection.execute(
            """UPDATE trade_lifecycle SET status='closed',close_time=?,close_price=?,gross_pnl=?,
            total_fees=?,net_pnl=?,exit_reason=?,updated_at_utc=? WHERE trade_uid=?""",
            (close_time or datetime.now(timezone.utc).isoformat(), close_price, gross_pnl, total_fees, net,
            exit_reason, datetime.now(timezone.utc).isoformat(), trade_uid),
        )
        self.connection.commit()
        if net is not None and net < 0:
            row = self.connection.execute(
                "SELECT * FROM trade_lifecycle WHERE trade_uid=?", (trade_uid,),
            ).fetchone()
            if row:
                self.record_loss_review(dict(row))

    def record_intent(self, intent: SignalIntent) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO signal_intents
            (created_at_utc,instrument,bar_time,direction,strategy_version,stop_distance_pct,status)
            VALUES(?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), *asdict(intent).values()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_intent_status(self, intent: SignalIntent, status: str) -> None:
        self.connection.execute(
            """UPDATE signal_intents SET status=? WHERE
            instrument=? AND bar_time=? AND direction=? AND strategy_version=?""",
            (status, intent.instrument, intent.bar_time, intent.direction, intent.strategy_version),
        )
        self.connection.commit()

    def record_range_pivot_intent(self, intent: RangePivotIntent) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO range_pivot_intents
            (created_at_utc,instrument,strategy_id,strategy_version,timeframe,confirmed_bar_time,signal_action,
             signal_direction,pivot_price,atr,stop_price,take_profit_price,config_fingerprint,status,branch)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), intent.instrument, intent.strategy_id,
             intent.strategy_version, intent.timeframe, intent.confirmed_bar_time, intent.signal_action,
             intent.signal_direction, intent.pivot_price, intent.atr, intent.stop_price,
             intent.take_profit_price, intent.config_fingerprint, intent.status, intent.branch),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_range_pivot_intent_status(self, intent: RangePivotIntent, status: str) -> None:
        self.connection.execute(
            """UPDATE range_pivot_intents SET status=? WHERE instrument=? AND strategy_version=?
            AND confirmed_bar_time=? AND signal_action=?""",
            (status, intent.instrument, intent.strategy_version, intent.confirmed_bar_time, intent.signal_action),
        )
        self.connection.commit()

    def submitted_range_intent_count(self, trading_date: date, strategy_version: str) -> int:
        return int(self.connection.execute(
            "SELECT COUNT(*) FROM range_pivot_intents WHERE created_at_utc LIKE ? AND strategy_version=? AND status='submitted'",
            (trading_date.isoformat() + "%", strategy_version),
        ).fetchone()[0])

    def latest_submitted_range_at(self, strategy_version: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT created_at_utc FROM range_pivot_intents WHERE strategy_version=? AND status='submitted' ORDER BY id DESC LIMIT 1",
            (strategy_version,),
        ).fetchone()
        if not row:
            return None
        parsed = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc))

    def consecutive_submitted_range_direction_count(self, strategy_version: str, direction: int,
                                                    branch: str | None = None) -> int:
        branch_sql = " AND branch=?" if branch else ""
        params = (strategy_version, branch) if branch else (strategy_version,)
        rows = self.connection.execute(
            "SELECT signal_direction FROM range_pivot_intents WHERE strategy_version=? AND status='submitted'" +
            branch_sql + " ORDER BY id DESC LIMIT 20", params,
        ).fetchall()
        count = 0
        for row in rows:
            if int(row[0]) != direction:
                break
            count += 1
        return count

    def strategy_branch_statistics(self, strategy_id: str) -> list[sqlite3.Row]:
        """Closed-trade performance grouped by rule branch; branches never share PnL."""
        return list(self.connection.execute(
            """SELECT branch,COUNT(*) AS trades,
            SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) AS wins,
            COALESCE(SUM(gross_pnl),0) AS gross_pnl,
            COALESCE(SUM(total_fees),0) AS total_fees,
            COALESCE(SUM(net_pnl),0) AS net_pnl
            FROM trade_lifecycle WHERE strategy_id=? AND status='closed'
            GROUP BY branch ORDER BY branch""", (strategy_id,),
        ).fetchall())

    def save_reversal_state(self, instrument: str, strategy_version: str, pending_action: str, confirmed_bar_time: str) -> None:
        self.connection.execute(
            """INSERT INTO reversal_states VALUES(?,?,?,?,?) ON CONFLICT(instrument,strategy_version) DO UPDATE SET
            pending_action=excluded.pending_action, confirmed_bar_time=excluded.confirmed_bar_time,
            updated_at_utc=excluded.updated_at_utc""",
            (instrument, strategy_version, pending_action, confirmed_bar_time, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def load_reversal_state(self, instrument: str, strategy_version: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            "SELECT pending_action,confirmed_bar_time FROM reversal_states WHERE instrument=? AND strategy_version=?",
            (instrument, strategy_version),
        ).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def submitted_intent_count(self, trading_date: date, strategy_version: str) -> int:
        prefix = trading_date.isoformat()
        return int(self.connection.execute(
            "SELECT COUNT(*) FROM signal_intents WHERE created_at_utc LIKE ? AND strategy_version=? AND status='submitted'",
            (prefix + "%", strategy_version),
        ).fetchone()[0])

    def external_execution_claimed(self, event_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM external_execution_claims WHERE event_id=?", (event_id,)
        ).fetchone() is not None

    def set_external_execution_status(self, event_id: str, source: str,
                                      status: str, detail: str) -> None:
        self.connection.execute(
            """INSERT INTO external_execution_claims(event_id,source,status,detail,updated_at_utc)
            VALUES(?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,
            detail=excluded.detail,updated_at_utc=excluded.updated_at_utc""",
            (event_id, source, status, detail, datetime.now(timezone.utc).isoformat()))
        self.connection.commit()

    def successful_lifecycle_order_count(self, trading_date: date, strategy_id: str) -> int:
        """Count orders accepted by OKX; candidates and failed submissions are absent."""
        prefix = trading_date.isoformat()
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM trade_lifecycle
            WHERE signal_time LIKE ? AND strategy_id=? AND COALESCE(order_id,'')<>''""",
            (prefix + "%", strategy_id),
        ).fetchone()[0])

    def submitted_intent_count_since(self, strategy_version: str, direction: int, bar_time: str) -> int:
        return int(self.connection.execute(
            """SELECT COUNT(*) FROM signal_intents WHERE strategy_version=? AND direction=?
            AND bar_time>=? AND status='submitted'""",
            (strategy_version, direction, bar_time),
        ).fetchone()[0])

    def consecutive_submitted_direction_count(self, strategy_version: str, direction: int) -> int:
        rows = self.connection.execute(
            """SELECT direction FROM signal_intents WHERE strategy_version=? AND status='submitted'
            ORDER BY id DESC""",
            (strategy_version,),
        ).fetchall()
        count = 0
        for row in rows:
            if int(row[0]) != direction:
                break
            count += 1
        return count

    def latest_submitted_at(self, strategy_version: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT created_at_utc FROM signal_intents WHERE strategy_version=? AND status='submitted' ORDER BY id DESC LIMIT 1",
            (strategy_version,),
        ).fetchone()
        if not row:
            return None
        parsed = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc))

    def load_daily_risk(self, trading_date: date | None = None) -> DailyRiskState:
        key = (trading_date or datetime.now(timezone.utc).date()).isoformat()
        row = self.connection.execute("SELECT * FROM daily_risk WHERE trading_date=?", (key,)).fetchone()
        if row is None:
            return DailyRiskState()
        return DailyRiskState(float(row["realized_pnl_pct"]), int(row["consecutive_losses"]), int(row["trades"]), bool(row["circuit_breaker"]))

    def save_daily_risk(self, state: DailyRiskState, trading_date: date | None = None) -> None:
        key = (trading_date or datetime.now(timezone.utc).date()).isoformat()
        self.connection.execute(
            """INSERT INTO daily_risk VALUES(?,?,?,?,?) ON CONFLICT(trading_date) DO UPDATE SET
            realized_pnl_pct=excluded.realized_pnl_pct,
            consecutive_losses=excluded.consecutive_losses,
            trades=excluded.trades,
            circuit_breaker=excluded.circuit_breaker""",
            (key, state.realized_pnl_pct, state.consecutive_losses, state.trades, int(state.circuit_breaker)),
        )
        self.connection.commit()
