from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from .entry_classification import classify_review_shape


BEIJING = timezone(timedelta(hours=8))
STAGE2 = {
    "price_above_flat_rising_ma5", "price_below_flat_falling_ma5",
    "price_reclaim_ma5", "price_break_ma5",
}
STAGE3 = {"small_golden_cross", "small_death_cross"}


def _json(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _inherit_confirmed_reversal_shape(zone: sqlite3.Row, zones: list[sqlite3.Row],
                                      features: dict) -> dict:
    """Show a later mixed 5m confirmation as the active prior mature reversal."""
    direction = int(zone["direction"])
    true_code = "true_top_reversal" if direction < 0 else "true_bottom_reversal"
    if (str(zone["pattern_type"]) not in {
            "price_reversal_zone:5m", "heikin_reversal_zone:5m"}
            or str(features.get("market_shape_code")) != "mixed_structure_candidate"
            or not bool(features.get("five_minute_half_cover"))):
        return features
    current = _utc(str(zone["confirmed_bar_time"]))
    for prior in zones:
        if int(prior["direction"]) != direction:
            continue
        prior_features = _json(prior["features_json"])
        age = current - _utc(str(prior["confirmed_bar_time"]))
        if timedelta(0) <= age <= timedelta(minutes=30) and str(
                prior_features.get("market_shape_code")) == true_code:
            return {
                **features,
                "market_shape_code": true_code,
                "market_shape_label": ("顶部反转区已生效（继承前序三均线成熟发散末端）"
                                       if direction < 0 else
                                       "底部反转区已生效（继承前序三均线成熟发散末端）"),
                "inherited_reversal_anchor_time": str(prior["confirmed_bar_time"]),
                # The parent endpoint keeps the direction active, but this
                # later 5m row is still a local high/low in its own timeframe.
                "classification_is_inherited_parent": True,
            }
    return features


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_time(value: str) -> str:
    return _utc(value).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _clock(value: str) -> str:
    return _utc(value).astimezone(BEIJING).strftime("%H:%M:%S")


def _stage_name(pattern_type: str) -> str:
    return str(pattern_type).split(":")[-1]


def _direction_label(direction: int, features: dict) -> str:
    shape = str(features.get("market_shape_code") or "")
    if shape == "sideways_neutral":
        return "横盘偏多候选" if direction > 0 else "横盘偏空候选"
    if shape == "downtrend_continuation_range":
        return "反抽追空"
    if shape in {"true_top_reversal", "downtrend_continuation_short"}:
        return "扫顶做空" if shape == "true_top_reversal" else "反抽追空"
    if shape in {"true_bottom_reversal", "uptrend_continuation_long"}:
        return "扫底做多" if shape == "true_bottom_reversal" else "回踩追多"
    # Legacy fallback: do not promote a raw HA colour change to a sweep.
    confirmed = features.get("five_minute_shape_confirmed")
    if confirmed is True:
        return "扫底做多" if direction > 0 else "扫顶做空"
    if confirmed is False:
        return "回踩候选（五分钟未确认）" if direction < 0 else "反抽候选（五分钟未确认）"
    return "旧版原始变色（未重算）"


def _first_stage(patterns: list[sqlite3.Row], zone: sqlite3.Row,
                 accepted: set[str]) -> sqlite3.Row | None:
    start = _utc(zone["confirmed_bar_time"])
    end = _utc(zone["confirmed_bar_time"]) + timedelta(minutes=30)
    direction = int(zone["direction"])
    matches = [row for row in patterns if int(row["direction"]) == direction
               and _stage_name(row["pattern_type"]) in accepted
               and start <= _utc(row["confirmed_bar_time"]) <= end]
    return min(matches, key=lambda row: _utc(row["confirmed_bar_time"])) if matches else None


def _assign_stages_once(zones: list[sqlite3.Row], patterns: list[sqlite3.Row],
                        accepted: set[str]) -> dict[str, sqlite3.Row]:
    """Assign each stage to only the most recent preceding same-side 1m zone."""
    assignments: dict[str, sqlite3.Row] = {}
    one_minute = [zone for zone in zones if str(zone["pattern_type"]).endswith(":1m")]
    for pattern in patterns:
        if _stage_name(pattern["pattern_type"]) not in accepted:
            continue
        at = _utc(pattern["confirmed_bar_time"])
        candidates = [zone for zone in one_minute
                      if int(zone["direction"]) == int(pattern["direction"])
                      and _utc(zone["confirmed_bar_time"]) <= at
                      <= _utc(zone["confirmed_bar_time"]) + timedelta(minutes=30)]
        if candidates:
            latest = max(candidates, key=lambda zone: _utc(zone["confirmed_bar_time"]))
            assignments.setdefault(str(latest["event_key"]), pattern)
    return assignments


def _reason_class(status: str, stage2: sqlite3.Row | None,
                  stage3: sqlite3.Row | None) -> str:
    if status == "triggered_order_submitted":
        return "-"
    if stage2 is None:
        return "第二阶段未触发"
    if stage3 is None:
        return "第二阶段漏单"
    return "第三阶段补漏未成交"


def _standard_reason(status: str, direction: int, stage2: sqlite3.Row | None,
                     stage3: sqlite3.Row | None, raw_reason: str) -> str:
    side = "扫底做多" if direction > 0 else "扫顶做空"
    if status == "triggered_order_submitted":
        stage = "第二阶段价格穿过MA5" if stage2 is not None else "第三阶段MA5/MA10交叉补漏"
        return f"{side}第一阶段成立；{stage}已提交实盘订单。5分钟仅作形态参照。"
    if stage2 is None:
        return f"{side}第一阶段成立；尚未出现同方向价格穿过MA5，未进入下单阶段。"
    if stage3 is None:
        suffix = "旧门槛曾在第二阶段后继续等待；新规则改为第二阶段立即执行。"
    else:
        suffix = "第二阶段未成交且第三阶段交叉已出现；新规则必须在第三阶段补漏。"
    if "same-side" in raw_reason or "同向" in raw_reason and "已有" in raw_reason:
        suffix = "同方向已有持仓或订单，正常去重，不能重复开仓。"
    elif "PineTS" in raw_reason and ("反向" in raw_reason or "veto" in raw_reason.lower()):
        suffix = "PineTS明确反向，按已授权规则拦截。"
    elif "API" in raw_reason or "timeout" in raw_reason.lower():
        suffix = "触发后遇到API或网络异常，保留审计并按安全恢复规则处理。"
    return f"{side}第一阶段成立；第二阶段{_clock(stage2['confirmed_bar_time'])}已触发。{suffix}"


def prune_reversal_miss_history(database: Path, keep: int = 30) -> int:
    """Keep only the newest review rows; order/trade/loss audit tables are untouched."""
    if keep < 1:
        raise ValueError("reversal review retention must be positive")
    connection = sqlite3.connect(database)
    try:
        cursor = connection.execute(
            """DELETE FROM market_pattern_observations
               WHERE (pattern_type LIKE 'price_reversal_zone:%'
                      OR pattern_type LIKE 'heikin_reversal_zone:%')
                 AND event_key NOT IN (
                     SELECT event_key FROM market_pattern_observations
                     WHERE (pattern_type LIKE 'price_reversal_zone:%'
                            OR pattern_type LIKE 'heikin_reversal_zone:%')
                     ORDER BY confirmed_bar_time DESC, created_at_utc DESC LIMIT ?
                 )""", (keep,)
        )
        connection.commit()
        return max(0, int(cursor.rowcount))
    finally:
        connection.close()


def reversal_feed_freshness(database: Path) -> dict[str, str]:
    """Return the latest scanner, raw-stage and displayed-zone timestamps."""
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        latest_scan = connection.execute(
            "SELECT MAX(created_at_utc) FROM events"
        ).fetchone()[0]
        latest_stage = connection.execute(
            """SELECT MAX(confirmed_bar_time) FROM market_pattern_observations
               WHERE pattern_type LIKE 'one_minute_launch_freeze:%'"""
        ).fetchone()[0]
        latest_zone = connection.execute(
            """SELECT MAX(confirmed_bar_time) FROM market_pattern_observations
               WHERE (pattern_type LIKE 'price_reversal_zone:%'
                      OR pattern_type LIKE 'heikin_reversal_zone:%')"""
        ).fetchone()[0]
    finally:
        connection.close()
    return {
        "scan": _local_time(latest_scan) if latest_scan else "-",
        "stage": _local_time(latest_stage) if latest_stage else "-",
        "zone": _local_time(latest_zone) if latest_zone else "-",
    }


def read_reversal_misses(database: Path, limit: int = 30) -> list[dict]:
    """Build a read-only, stage-oriented review table from reversal evidence."""
    uri = f"file:{database.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        zones = connection.execute(
            """SELECT * FROM market_pattern_observations
               WHERE (pattern_type LIKE 'price_reversal_zone:%'
                      OR pattern_type LIKE 'heikin_reversal_zone:%')
               ORDER BY confirmed_bar_time DESC LIMIT ?""", (limit,)
        ).fetchall()
        patterns = connection.execute(
            """SELECT confirmed_bar_time,pattern_type,direction
               FROM market_pattern_observations
               WHERE pattern_type LIKE 'one_minute_launch_freeze:%'
               ORDER BY confirmed_bar_time"""
        ).fetchall()
        events = connection.execute(
            """SELECT payload_json FROM events
               WHERE event_type='validation_observation' ORDER BY id DESC"""
        ).fetchall()
        lifecycles = connection.execute(
            """SELECT order_id,direction,signal_time,signal_reason,signal_context_json,
                      entry_reference,stop_price,strategy_version,branch,updated_at_utc
               FROM trade_lifecycle
               WHERE COALESCE(order_id,'')<>'' ORDER BY updated_at_utc DESC"""
        ).fetchall()
        submissions = connection.execute(
            """SELECT order_id,created_at_utc FROM entry_snapshots
               WHERE COALESCE(order_id,'')<>'' ORDER BY created_at_utc DESC"""
        ).fetchall()
    finally:
        connection.close()

    reasons: dict[str, tuple[str, str]] = {}
    orders = {str(row["order_id"]): row for row in lifecycles}
    submitted_times = {str(row["order_id"]): str(row["created_at_utc"])
                       for row in submissions}
    for event in events:
        payload = _json(event["payload_json"])
        reason = str(payload.get("reason") or "")
        zone_keys = list(payload.get("price_reversal_zone_keys") or [])
        zone_keys.extend(payload.get("heikin_reversal_zone_keys") or [])
        for key in zone_keys:
            reasons.setdefault(str(key), (reason, str(payload.get("strategy_version") or "-")))

    stage2_assignments = _assign_stages_once(zones, patterns, STAGE2)
    stage3_assignments = _assign_stages_once(zones, patterns, STAGE3)
    rows: list[dict] = []
    for zone in zones:
        features = _json(zone["features_json"])
        features = _inherit_confirmed_reversal_shape(zone, zones, features)
        raw_reason, version = reasons.get(
            zone["event_key"], ("", str(features.get("strategy_version") or "-")))
        status = str(zone["outcome_status"])
        outcome = _json(zone["outcome_json"])
        order_id = str(outcome.get("order_id") or "")
        order = orders.get(order_id)
        order_direction = (int(outcome["order_direction"])
                           if outcome.get("order_direction") is not None
                           else int(order["direction"]) if order is not None else 0)
        direction_mismatch = bool(
            status == "triggered_order_submitted" and order_direction
            and order_direction != int(zone["direction"])
        )
        stage2 = stage2_assignments.get(str(zone["event_key"]))
        stage3 = stage3_assignments.get(str(zone["event_key"]))
        order_context = _json(order["signal_context_json"]) if order is not None else {}
        classification = classify_review_shape(
            int(zone["direction"]),
            ("mixed_structure_candidate"
             if features.get("classification_is_inherited_parent") else
             str(features.get("market_shape_code") or "")),
            stage3_recovery=bool(stage3 is not None and stage2 is not None),
        )
        classification_label = str(
            order_context.get("entry_classification_label") or classification["label"])
        classification_rule = str(
            order_context.get("entry_classification_rule") or classification["rule"])
        result = ("关联异常" if direction_mismatch else
                  "已下单" if status == "triggered_order_submitted" else
                  "确认漏单" if status == "missed_before_next_reversal" else
                  "观察中/未成交")
        legacy_average = str(zone["pattern_type"]).startswith("heikin_reversal_zone:")
        if legacy_average:
            classification_label = f"历史平均K线记录（仅审计）｜{classification_label}"
            classification_rule = (
                "该记录仅为升级前历史审计，不参与当前方向或新订单放行；"
                f"原分类参考：{classification_rule}")
        rows.append({
            "time": _local_time(zone["confirmed_bar_time"]),
            "timeframe": str(zone["pattern_type"]).split(":")[-1],
            "direction": _direction_label(int(zone["direction"]), features),
            "market_shape": (
                f"{classification_label}｜"
                f"{str(features.get('market_shape_label') or '旧版未按双周期均线重算')}"),
            "stage1": ("历史：平均K线反转（仅审计）" if legacy_average
                       else "已成立：普通K线扫高/扫低、覆盖或局部极值转向"),
            "stage2": (f"已触发 {_clock(stage2['confirmed_bar_time'])}"
                       if stage2 is not None else "未触发：等待价格穿MA5"),
            "stage3": (f"已触发 {_clock(stage3['confirmed_bar_time'])}"
                       if stage3 is not None else "未触发：仅作补漏"),
            "result": result,
            "reason_class": ("异向订单误关联" if direction_mismatch else
                             f"{classification_label}｜{_reason_class(status, stage2, stage3)}"),
            "reason": ((f"审计异常：该反转区方向={int(zone['direction'])}，"
                        f"关联订单方向={order_direction}，不得视为该反转区成交。")
                       if direction_mismatch else
                       f"分类：{classification_label}；分类规则：{classification_rule}；"
                       f"{_standard_reason(status, int(zone['direction']), stage2, stage3, raw_reason)}"),
            "entry": f"{float(zone['entry_reference']):.2f}",
            "stop": f"{float(zone['stop_reference']):.2f}",
            "version": version,
            "order_side": ("做多" if order_direction > 0 else
                           "做空" if order_direction < 0 else "-"),
            "order_id": order_id or "-",
            "submitted_at": (_local_time(str(outcome.get("submitted_at_utc")))
                             if outcome.get("submitted_at_utc") else
                             _local_time(submitted_times[order_id])
                             if order_id in submitted_times else "-"),
        })

    # A reversal-zone review is also the operator's order audit.  An actual
    # submitted order must never disappear merely because it was produced by
    # a continuation branch or could not be associated with a reversal zone.
    represented_order_ids = {
        str(item.get("order_id") or "") for item in rows
        if str(item.get("order_id") or "") not in {"", "-"}
    }
    for order in lifecycles:
        order_id = str(order["order_id"] or "")
        if not order_id or order_id in represented_order_ids:
            continue
        direction = int(order["direction"])
        context = _json(order["signal_context_json"])
        branch = str(order["branch"] or "legacy_unclassified")
        persisted_label = str(context.get("entry_classification_label") or "")
        branch = {
            "aggressive_two_timeframe_intrabar_ma5_reversal": "局部双周期MA5反抽追空或回踩追多",
            "downtrend_continuation_short": "下降趋势反抽追空",
            "uptrend_continuation_long": "上涨趋势回踩追多",
            "aggressive_five_minute_high_half_cover_short": "下降趋势五分钟覆盖追空",
            "early_dual_timeframe_pullback_cover_long": "上涨趋势提前回踩追多",
        }.get(branch, branch)
        if persisted_label:
            classification_label = persisted_label
            classification_rule = str(context.get("entry_classification_rule") or "按已持久化入场身份执行")
        else:
            legacy_trend = str(order["branch"] or "") in {
                "downtrend_continuation_short", "uptrend_continuation_long",
                "early_dual_timeframe_pullback_cover_long",
                "multi_timeframe_weakness_continuation_short",
            }
            legacy_higher = bool(context.get("higher_timeframe_trend_confirmed"))
            legacy_classification = classify_review_shape(
                direction,
                ("downtrend_continuation_short" if direction < 0
                 else "uptrend_continuation_long") if legacy_trend else "mixed_structure_candidate",
                stage3_recovery=str(order["branch"] or "") == "frozen_one_minute_big_cross_launch",
                higher_timeframe_trend=legacy_higher,
            )
            classification_label = str(legacy_classification["label"])
            classification_rule = str(legacy_classification["rule"])
        submitted_at = submitted_times.get(order_id) or str(order["updated_at_utc"])
        rows.append({
            "time": _local_time(str(order["signal_time"])),
            "timeframe": "订单",
            "direction": "做多" if direction > 0 else "做空",
            "market_shape": f"真实下单｜{classification_label}｜{branch}",
            "stage1": "订单已提交",
            "stage2": "-",
            "stage3": "-",
            "result": "已下单",
            "reason_class": f"{classification_label}｜真实订单补录",
            "reason": (f"分类：{classification_label}；分类规则：{classification_rule}；"
                       f"{str(order['signal_reason'] or context.get('reason') or branch)}"),
            "entry": f"{float(order['entry_reference'] or 0):.2f}",
            "stop": f"{float(order['stop_price'] or 0):.2f}",
            "version": str(order["strategy_version"] or "-"),
            "order_side": "做多" if direction > 0 else "做空",
            "order_id": order_id,
            "submitted_at": _local_time(submitted_at),
        })
    rows.sort(key=lambda item: item["time"], reverse=True)
    return rows[:limit]


def reversal_miss_table_row(item: dict) -> tuple[str, ...]:
    return tuple(str(item.get(key, "-")) for key in (
        "time", "timeframe", "direction", "market_shape", "stage1", "stage2", "stage3",
        "result", "reason_class", "reason", "entry", "stop", "version",
        "order_side", "order_id", "submitted_at",
    ))
