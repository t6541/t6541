import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from quantbot.hourly_missed_entry import read_hourly_missed_entries, scan_completed_hour


def test_hourly_replay_separates_candidate_and_order_gap(tmp_path, monkeypatch):
    database = tmp_path / "strategy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, created_at_utc TEXT, event_type TEXT, payload_json TEXT)")
    events = [
        (1, "2026-09-18T00:29:00+00:00", "three_timeframe_reversal_entry_candidate",
         {"direction": 1, "instrument": "ETH-USDT-SWAP"}),
        (2, "2026-09-18T00:30:00+00:00", "legacy_strategy_candidate_observe_only",
         {"direction": 1, "reason": "review only"}),
        (3, "2026-09-18T00:43:00+00:00", "dual_timeframe_ma5_pullback_qualified",
         {"direction": 1, "instrument": "ETH-USDT-SWAP"}),
        (4, "2026-09-18T00:43:30+00:00", "entry_all_gates_passed",
         {"direction": 1, "clOrdId": "test-long"}),
    ]
    connection.executemany("INSERT INTO events VALUES(?,?,?,?)",
                           [(i, at, kind, json.dumps(payload)) for i, at, kind, payload in events])
    connection.commit()
    connection.close()
    start = datetime(2026, 9, 18, 0, tzinfo=timezone.utc)
    candles = pd.DataFrame([
        {"date": start + timedelta(minutes=i), "close": 2440.0,
         "high": 2443.0 if i in (30, 44) else 2440.5, "low": 2439.5}
        for i in range(60)
    ])
    now = datetime(2026, 9, 18, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("quantbot.data.okx_history_market", lambda *args: candles.iloc[:-1])
    assert scan_completed_hour(database, "ETH-USDT-SWAP", candles.iloc[:-1], now) == 0
    assert scan_completed_hour(database, "ETH-USDT-SWAP", candles, now) == 2
    rows = read_hourly_missed_entries(database)
    assert {row[3] for row in rows} == {"候选机会漏单", "通过全部条件后未提交"}
    assert scan_completed_hour(database, "ETH-USDT-SWAP", candles, now) == 0


def test_bottom_stage_is_research_evidence_not_an_executable_missed_order(tmp_path):
    database = tmp_path / "strategy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, created_at_utc TEXT, event_type TEXT, payload_json TEXT)")
    connection.execute("INSERT INTO events VALUES(?,?,?,?)", (
        1, "2026-09-18T00:20:00+00:00", "market_pattern_observed",
        json.dumps({"direction": 1, "instrument": "ETH-USDT-SWAP",
                    "pattern_type": "one_minute_launch_freeze:confirmed_local_bottom"})))
    connection.commit()
    connection.close()
    start = datetime(2026, 9, 18, 0, tzinfo=timezone.utc)
    candles = pd.DataFrame([
        {"date": start + timedelta(minutes=i), "close": 100.0,
         "high": 103.0 if i == 21 else 100.5, "low": 99.5}
        for i in range(60)
    ])
    assert scan_completed_hour(
        database, "ETH-USDT-SWAP", candles,
        datetime(2026, 9, 18, 1, 1, tzinfo=timezone.utc)) == 1
    row = read_hourly_missed_entries(database)[0]
    assert row[3] == "阶段后续有行情（未形成完整候选）"
