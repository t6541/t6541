"""Completed-hour, read-only-on-market replay of unfilled entry opportunities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading

import pandas as pd


BEIJING = timezone(timedelta(hours=8))
_BACKGROUND_LOCK = threading.Lock()
_BACKGROUND_HOURS: set[tuple[str, str]] = set()
CANDIDATES = {
    "three_timeframe_reversal_entry_candidate",
    "dual_timeframe_ma5_pullback_qualified",
    "aggressive_top_weakening_ma5_short_candidate",
    "downtrend_throwback_early_entry_qualified",
}
BOTTOM_STAGES = {"confirmed_local_bottom", "bottom_half_bearish_cover",
                 "price_reclaim_ma5"}
RESULTS = {
    "entry_all_gates_passed", "validation_order_submitted", "validation_order_failed",
    "directional_fresh_half_cover_rejected", "bottom_reversal_dual_ma20_rejected",
    "legacy_strategy_candidate_observe_only", "old_trend_entry_blocked_by_fresh_endpoint",
    "validation_observation",
}


def scan_completed_hour(database: Path, instrument: str, one_minute: pd.DataFrame,
                        now: datetime | None = None) -> int:
    """Scan exactly the previous closed Beijing hour once; never submit replay orders."""
    now = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    connection = sqlite3.connect(database, timeout=10)
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS hourly_missed_entries (
            hour_utc TEXT NOT NULL, minute_utc TEXT NOT NULL, instrument TEXT NOT NULL,
            direction INTEGER NOT NULL, candidate_type TEXT NOT NULL,
            result TEXT NOT NULL, reason TEXT NOT NULL, favorable_points REAL NOT NULL,
            client_order_id TEXT NOT NULL, source_event_id INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL,
            PRIMARY KEY(instrument,minute_utc,direction,candidate_type))""")
        start_utc = start.astimezone(timezone.utc).isoformat()
        end_utc = end.astimezone(timezone.utc).isoformat()
        connection.execute("""CREATE TABLE IF NOT EXISTS hourly_missed_entry_scans (
            instrument TEXT NOT NULL, hour_utc TEXT NOT NULL, candle_count INTEGER NOT NULL,
            scanned_at_utc TEXT NOT NULL, PRIMARY KEY(instrument,hour_utc))""")
        if connection.execute("SELECT 1 FROM hourly_missed_entry_scans WHERE instrument=? AND hour_utc=?",
                              (instrument, start_utc)).fetchone():
            return 0
        def closed_hour(frame: pd.DataFrame) -> pd.DataFrame:
            if frame.empty or "date" not in frame:
                return frame.iloc[0:0]
            frame = frame.copy()
            frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_convert(BEIJING)
            return frame[(frame["date"] >= start) & (frame["date"] < end)].drop_duplicates("date").sort_values("date")
        candles = closed_hour(one_minute)
        if len(candles) < 60:
            from .data import okx_history_market
            candles = closed_hour(okx_history_market((instrument,), "1m", 180))
        # A partial or delayed feed cannot establish a complete sixty-bar replay.
        if (len(candles) != 60 or candles.iloc[0]["date"] != start or
                candles.iloc[-1]["date"] != end - timedelta(minutes=1)):
            return 0
        events = connection.execute(
            "SELECT id,created_at_utc,event_type,payload_json FROM events "
            "WHERE created_at_utc>=? AND created_at_utc<? ORDER BY id",
            (start_utc, end_utc)).fetchall()
        parsed = [(id, datetime.fromisoformat(at).astimezone(BEIJING), kind,
                   json.loads(payload)) for id, at, kind, payload in events]
        count = 0
        seen: set[tuple[str, int, str]] = set()
        for event_id, at, kind, payload in parsed:
            stage = str(payload.get("pattern_type") or "")
            bottom_stage = (kind == "market_pattern_observed" and
                            stage.startswith("one_minute_launch_freeze:") and
                            stage.rsplit(":", 1)[-1] in BOTTOM_STAGES)
            if (kind not in CANDIDATES and not bottom_stage) or payload.get("instrument", instrument) != instrument:
                continue
            direction = int(payload.get("direction") or 0)
            if direction not in (-1, 1):
                continue
            minute = at.replace(second=0, microsecond=0)
            candidate_type = "1分钟底部反转阶段" if bottom_stage else kind
            key = (minute.isoformat(), direction, candidate_type)
            if key in seen:
                continue
            seen.add(key)
            later = candles[(candles["date"] > minute) &
                            (candles["date"] <= minute + timedelta(minutes=15))]
            current = candles[candles["date"] == minute]
            if current.empty or later.empty:
                continue
            entry = float(current.iloc[0]["close"])
            favorable = (float(later["high"].max()) - entry if direction > 0 else
                         entry - float(later["low"].min()))
            nearby = [(k, p) for _, t, k, p in parsed if at <= t <= at + timedelta(minutes=3)
                      and k in RESULTS and int(p.get("direction") or direction) == direction]
            passed = [p for k, p in nearby if k == "entry_all_gates_passed"]
            submitted_ids = {str(p.get("clOrdId") or "") for k, p in nearby
                             if k == "validation_order_submitted"}
            client_id = str(passed[0].get("clOrdId") or "") if passed else ""
            if passed and client_id in submitted_ids:
                continue
            if passed:
                failed = next((p for k, p in nearby if k == "validation_order_failed"
                               and p.get("clOrdId") == client_id), {})
                result = ("开仓有回执但后续异常" if failed.get("ordId") else
                          "通过全部条件后未提交")
                reason = str(failed.get("error") or
                             "未找到相同客户端订单号的提交回执；需核对交易所订单")
            elif favorable >= 2.0:
                # A frozen bottom stage is useful research evidence but is not
                # yet an executable candidate.  Label it separately so replay
                # statistics do not encourage loosening live risk gates from
                # hindsight-only favorable movement.
                result = ("阶段后续有行情（未形成完整候选）"
                          if bottom_stage else "候选机会漏单")
                reason = next((str(p.get("reason") or "") for k, p in reversed(nearby)
                               if k not in {"validation_observation"}), "候选未进入发单阶段")
            else:
                continue
            connection.execute("""INSERT OR IGNORE INTO hourly_missed_entries
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                    start_utc, minute.astimezone(timezone.utc).isoformat(), instrument,
                    direction, candidate_type, result, reason[:500], round(favorable, 3),
                    client_id, event_id, datetime.now(timezone.utc).isoformat()))
            count += 1
        connection.execute("INSERT INTO hourly_missed_entry_scans VALUES(?,?,?,?)",
                           (instrument, start_utc, 60, datetime.now(timezone.utc).isoformat()))
        connection.commit()
        return count
    finally:
        connection.close()


def schedule_completed_hour_scan(database: Path, instrument: str,
                                 now: datetime | None = None) -> bool:
    """Run the completed-hour replay off the order-decision thread, once per hour."""
    current = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    hour = (current.replace(minute=0, second=0, microsecond=0)
            - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    key = (instrument, hour)
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        already_scanned = bool(connection.execute(
            "SELECT 1 FROM hourly_missed_entry_scans WHERE instrument=? AND hour_utc=?",
            key).fetchone())
        connection.close()
        if already_scanned:
            return False
    except sqlite3.OperationalError:
        pass
    with _BACKGROUND_LOCK:
        if key in _BACKGROUND_HOURS:
            return False
        _BACKGROUND_HOURS.add(key)

    def worker() -> None:
        completed = False
        try:
            from .data import okx_history_market
            candles = okx_history_market((instrument,), "1m", 180)
            scan_completed_hour(database, instrument, candles, now=current)
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            completed = bool(connection.execute(
                "SELECT 1 FROM hourly_missed_entry_scans WHERE instrument=? AND hour_utc=?",
                key).fetchone())
            connection.close()
        finally:
            # Successful scans are also deduplicated by SQLite.  Releasing the
            # in-process key lets a delayed/partial public feed retry later.
            if not completed:
                with _BACKGROUND_LOCK:
                    _BACKGROUND_HOURS.discard(key)

    threading.Thread(target=worker, name="hourly-missed-entry-replay", daemon=True).start()
    return True


def read_hourly_missed_entries(database: Path, limit: int = 100) -> list[tuple[str, ...]]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("""SELECT minute_utc,direction,candidate_type,result,
            favorable_points,reason,client_order_id FROM hourly_missed_entries
            ORDER BY minute_utc DESC LIMIT ?""", (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()
    return [(datetime.fromisoformat(at).astimezone(BEIJING).strftime("%m-%d %H:%M"),
             "做多" if direction > 0 else "做空", kind,
             ("阶段后续有行情（未形成完整候选）"
              if kind == "1分钟底部反转阶段" and result == "候选机会漏单" else result),
             f"{favorable:.2f}", reason, client_id or "-")
            for at, direction, kind, result, favorable, reason, client_id in rows]
