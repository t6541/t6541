"""Read-only, operator-facing trace of recent live entry decisions."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


ENTRY_EVENT_LABELS = {
    "aggressive_five_minute_high_half_cover_short_candidate": ("局部顶部／5m覆盖", "候选"),
    "aggressive_top_weakening_ma5_short_candidate": ("局部顶部／MA5转弱", "候选"),
    "aggressive_expanded_ma_top_ma5_short_candidate": ("真正顶部／均线末端", "候选"),
    "downtrend_lower_high_short_priority": ("下降趋势／反抽高点", "候选"),
    "three_timeframe_reversal_entry_candidate": ("三周期反转", "候选"),
    "persisted_directional_cover_recovery_candidate": ("五分钟覆盖／恢复", "候选"),
    "fresh_top_45pct_releases_old_bottom_for_execution": ("新顶解除旧底锁", "条件通过"),
    "confirmed_top_stage_three_overrides_transient_bottom": ("顶部小死叉补漏", "条件通过"),
    "downtrend_throwback_early_entry_qualified": ("下降趋势／早期反抽", "结构通过"),
    "early_launch_quality_rejected": ("反转三阶段", "位置拒绝"),
    "stale_opposite_freeze_rejected_by_dual_reversal": ("方向锁", "拒绝"),
    "directional_fresh_half_cover_rejected": ("五分钟相邻覆盖", "拒绝"),
    "review_branch_observe_only": ("分支执行策略", "仅观察"),
    "legacy_strategy_candidate_observe_only": ("旧策略分支", "仅观察"),
    "validation_order_submitted": ("实盘下单", "已提交"),
    "entry_all_gates_passed": ("实盘下单", "全部条件通过，待接口回执"),
    "validation_order_failed": ("实盘下单", "失败"),
    "aggressive_live_post_rejected": ("OKX下单接口", "明确拒绝"),
    "validation_observation": ("本轮策略观察", "未下单"),
    "hourly_multiframe_boundary_sample": ("整点多周期样本", "已记录"),
}


def chinese_decision_reason(value: object) -> str:
    """Translate recurring engine evidence before it reaches the operator table."""
    text = str(value)
    replacements = {
        "independent three-timeframe fast bottom/top reversal long": "独立三周期快速底部反转做多",
        "independent three-timeframe fast bottom/top reversal short": "独立三周期快速顶部反转做空",
        "independent three-timeframe recovery bottom/top reversal long": "独立三周期恢复型底部反转做多",
        "independent three-timeframe recovery bottom/top reversal short": "独立三周期恢复型顶部反转做空",
        "persisted first-45% bottom-long recovery": "已持久化的首次5分钟45%底部做多恢复",
        "persisted first-45% top-short recovery": "已持久化的首次5分钟45%顶部做空恢复",
        "compact stop remains intact": "冻结的小结构止损仍有效",
        "1m is on the valid MA5 side": "1分钟价格位于MA5有效一侧",
        "1m has turned on the MA5 side": "1分钟已在MA5附近转向",
        "1m fresh up-to-6-candle zone bullish bodies cumulatively cover the bearish bodies by": "1分钟最新最多6根区域内阳线实体累计覆盖阴线实体",
        "1m fresh up-to-6-candle zone bearish bodies cumulatively cover the bullish bodies by": "1分钟最新最多6根区域内阴线实体累计覆盖阳线实体",
        "5m fresh up-to-3-candle zone bullish bodies cumulatively cover the bearish bodies by": "5分钟最新最多3根区域内阳线实体累计覆盖阴线实体",
        "5m fresh up-to-3-candle zone bearish bodies cumulatively cover the bullish bodies by": "5分钟最新最多3根区域内阴线实体累计覆盖阳线实体",
        "fresh up-to-6-candle zone bullish bodies cumulatively cover the bearish bodies by": "最新最多6根区域内阳线实体累计覆盖阴线实体",
        "fresh up-to-6-candle zone bearish bodies cumulatively cover the bullish bodies by": "最新最多6根区域内阴线实体累计覆盖阳线实体",
        "15m has turned after the opposite decline/rise without a half-cover requirement": "15分钟已在此前反向走势后转色，不要求15分钟半覆盖",
        "15m colour turned after the opposite move": "15分钟已在此前反向走势后转色",
        "mandatory live 5m bullish adjacent cover": "实盘5分钟相邻阳线覆盖门",
        "mandatory live 5m bearish adjacent cover": "实盘5分钟相邻阴线覆盖门",
        "current candle adjacent-body cover is": "当前K线对前一根相邻反向实体覆盖率为",
        "current candle covers the adjacent opposite body by": "当前K线覆盖前一根相邻反向实体",
        "MA20 state=confirmed": "MA20状态=已确认",
        "15m aligned: audit only": "15分钟同向，仅作审计",
        "recovery": "恢复",
        "fast": "快速",
        "waiting": "等待",
        "confirmed": "已确认",
        "bullish": "看多",
        "bearish": "看空",
        "long": "做多",
        "short": "做空",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\s*:\s*", "：", text)
    text = re.sub(r"\s*;\s*", "；", text)
    text = re.sub(r"\s*\|\s*", "｜", text)
    return text


def condition_event_row(record: sqlite3.Row | dict) -> tuple[str, str, str, str, str]:
    event_type = str(record["event_type"])
    payload = json.loads(record["payload_json"])
    label, state = ENTRY_EVENT_LABELS[event_type]
    try:
        time_text = datetime.fromisoformat(str(record["created_at_utc"])).astimezone(
            timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
    except ValueError:
        time_text = str(record["created_at_utc"])
    reason = chinese_decision_reason(payload.get("reason") or payload.get("hierarchy_reason") or
                                     payload.get("stage") or payload.get("policy") or "-")
    direction = payload.get("direction")
    if direction in (-1, 1):
        label = ("做空｜" if direction < 0 else "做多｜") + label
    order_id = str(payload.get("order_id") or payload.get("ordId") or "-")
    return time_text, label, state, reason, order_id


def read_recent_condition_events(database: Path, limit: int = 80) -> list[tuple[str, str, str, str, str]]:
    """Return latest decision events without writing to the live database."""
    if limit <= 0 or not database.exists():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True,
                                 timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        names = tuple(ENTRY_EVENT_LABELS)
        placeholders = ",".join("?" for _ in names)
        records = connection.execute(
            f"SELECT created_at_utc,event_type,payload_json FROM events "
            f"WHERE event_type IN ({placeholders}) ORDER BY id DESC LIMIT ?",
            (*names, min(limit, 500)),
        ).fetchall()
        return [condition_event_row(record) for record in records]
    finally:
        connection.close()


def read_recent_condition_event_records(
        database: Path, limit: int = 80) -> list[tuple[int, tuple[str, str, str, str, str]]]:
    """Return stable event ids with rows so the UI can update without resetting history."""
    if limit <= 0 or not database.exists():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True,
                                 timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        names = tuple(ENTRY_EVENT_LABELS)
        placeholders = ",".join("?" for _ in names)
        records = connection.execute(
            f"SELECT id,created_at_utc,event_type,payload_json FROM events "
            f"WHERE event_type IN ({placeholders}) ORDER BY id DESC LIMIT ?",
            (*names, min(limit, 500)),
        ).fetchall()
        return [(int(record["id"]), condition_event_row(record)) for record in records]
    finally:
        connection.close()


def read_condition_events_for_beijing_day(
        database: Path, beijing_day: date) -> list[tuple[int, tuple[str, str, str, str, str]]]:
    """Read every operator event for one Beijing calendar day as one UI page."""
    if not database.exists():
        return []
    china = timezone(timedelta(hours=8))
    start = datetime.combine(beijing_day, time.min, tzinfo=china).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True,
                                 timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        names = tuple(ENTRY_EVENT_LABELS)
        placeholders = ",".join("?" for _ in names)
        records = connection.execute(
            f"SELECT id,created_at_utc,event_type,payload_json FROM events "
            f"WHERE event_type IN ({placeholders}) AND created_at_utc>=? AND created_at_utc<? "
            "ORDER BY id DESC",
            (*names, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [(int(record["id"]), condition_event_row(record)) for record in records]
    finally:
        connection.close()
