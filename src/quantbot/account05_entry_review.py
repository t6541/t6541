"""Durable entry-time evidence; never reconstruct a past verdict from live bars."""
from datetime import datetime, timezone
import json

from .account05_signals import _cover_45, _one_minute_reversal
from .version import APP_VERSION

IDENTITIES = ("真正底部做多", "真正顶部做空", "局部底部做多", "局部顶部做空",
              "反转三阶段补漏做多", "反转三阶段补漏做空",
              "上涨趋势回踩追多", "下跌趋势反抽追空", "超级趋势支撑早触发追多",
              "5分钟MA20订单块回踩早触发追多", "5分钟高位拒绝早触发追空",
              "5分钟超级趋势首次翻空追空", "1分钟超级趋势首次翻空补漏做空",
              "1分钟超级趋势首次翻空局部反转做空",
              "1分钟超级趋势压力线反抽追空")


def entry_context(signals, one, five, fifteen, hour=None):
    matched = {item.identity: item for item in signals.triggers}
    rules = []
    for name in IDENTITIES:
        item = matched.get(name)
        direction = 1 if "底部" in name or "追多" in name or "做多" in name else -1
        cover, ratio = _cover_45(five, direction)
        candidates = []
        data = one.sort_values("date").drop_duplicates("date", keep="last")
        for lag in range(0, max(0, min(6, len(data) - 23))):
            ok, terminal, _ = _one_minute_reversal(data.iloc[:len(data)-lag], direction)
            if ok:
                candidates.append("真正" if terminal else "局部")
                break
        reason = (item.reason if item else
                  f"一分钟候选={','.join(candidates) or '无'}；已收盘5分钟反向覆盖={ratio:.1%}，"
                  f"45%门槛={'通过' if cover else '未通过'}；本身份完整组合未成立")
        if "追" in name and not item:
            reason = "上级趋势、5分钟回抽带、1分钟已收盘反向确认的完整组合未成立"
        rules.append({"identity": name, "passed": item is not None, "reason": reason})
    frames = {}
    for label, frame in (("1m", one), ("5m", five), ("15m", fifteen), ("1h", hour)):
        if frame is None:
            continue
        data = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
        close = data["close"].astype(float)
        for window in (5, 10, 20):
            data[f"ma{window}"] = close.rolling(window).mean()
        # The last supplied row is running; preserve it without calling it closed.
        columns = ["date", "open", "high", "low", "close", "ma5", "ma10", "ma20"]
        frames[label] = json.loads(data[columns].tail(50).to_json(orient="records", date_format="iso"))
    return {"version": APP_VERSION, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "rules": rules, "frames": frames, "last_row_running": True,
            "trend_5m": signals.trend_5m.value, "trend_reason": signals.trend_reason}


def review_summary(payload):
    if not payload:
        return "证据不足：旧单未保存入场快照", "无法逐项验证六类规则"
    value = json.loads(payload)
    rules = value["rules"]
    verdict = "；".join(f"{r['identity']}={'满足' if r['passed'] else '未满足'}" for r in rules)
    source = value["source"]
    return (source + ("（六类之外）" if source not in IDENTITIES else ""),
            verdict + "；实际依据：" + value["reason"])
