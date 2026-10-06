from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json


@dataclass(frozen=True)
class LossDiagnosis:
    category: str
    cause: str
    evidence: str
    recommendation: str
    confidence: str


def _utc_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def diagnose_losing_trade(trade: dict) -> LossDiagnosis:
    """Produce a conservative, evidence-based preliminary loss diagnosis.

    This function never changes strategy parameters.  Its output is a review
    candidate that must be aggregated and validated before a release.
    """
    gross = float(trade.get("gross_pnl") or 0)
    fees = float(trade.get("total_fees") or 0)
    net = float(trade.get("net_pnl") if trade.get("net_pnl") is not None else gross + fees)
    entry = float(trade.get("entry_reference") or 0)
    stop = float(trade.get("stop_price") or 0)
    reason = str(trade.get("signal_reason") or "未记录触发原因")
    branch = str(trade.get("branch") or "legacy_unclassified")
    exit_reason = str(trade.get("exit_reason") or "unknown")
    context_raw = trade.get("signal_context_json") or "{}"
    try:
        context = json.loads(context_raw) if isinstance(context_raw, str) else dict(context_raw)
    except (ValueError, TypeError):
        context = {}

    duration_minutes = None
    try:
        if trade.get("signal_time") and trade.get("close_time"):
            duration_minutes = max(0.0, (
                _utc_datetime(trade["close_time"]) -
                _utc_datetime(trade["signal_time"])
            ).total_seconds() / 60)
    except (ValueError, TypeError):
        pass
    risk_pct = abs(entry - stop) / entry if entry > 0 and stop > 0 else None

    facts = [f"净亏损{net:.6f} USDT", f"毛盈亏{gross:.6f}", f"手续费{fees:.6f}",
             f"退出={exit_reason}", f"分支={branch}"]
    if duration_minutes is not None:
        facts.append(f"持仓{duration_minutes:.1f}分钟")
    if risk_pct is not None:
        facts.append(f"初始保护距离{risk_pct:.3%}")

    if gross >= 0 > net:
        return LossDiagnosis(
            "交易成本吞噬", "方向判断未产生足够毛利润，手续费把交易变成净亏损。",
            "；".join(facts),
            "提高最小预期净利润和结构空间门槛；汇总同类样本后评估是否减少低波动频繁交易。", "高",
        )
    if risk_pct is not None and risk_pct < .002:
        return LossDiagnosis(
            "保护空间过窄", "止损位处于一分钟常见噪声范围内，正常波动可能提前触发保护。",
            "；".join(facts),
            "执行共享绝对距离下限；复核该单是否由旧版本或旧服务器保护单产生。", "高",
        )
    if duration_minutes is not None and duration_minutes <= 3 and exit_reason == "stop_loss":
        return LossDiagnosis(
            "入场后快速失效", "信号入场后数分钟内即被否定，可能属于假突破、插针或确认不足。",
            "；".join(facts) + f"；触发={reason}",
            "统计同分支快速止损比例；若样本持续偏高，再测试增加收盘确认、成交量或波动冷却条件。", "中",
        )
    if "reversal" in branch or "extreme" in branch or "sweep" in reason.lower():
        return LossDiagnosis(
            "反转未成立", "极值或扫损出现后，价格没有按预期完成反转。",
            "；".join(facts) + f"；触发={reason}",
            "按趋势强度、结构位置和反转确认完整度分组统计；仅在累计样本支持时提高反转确认门槛。", "中",
        )
    if "continuation" in branch:
        return LossDiagnosis(
            "趋势延续失败", "顺势回踩/反抽后没有继续原方向，可能处于趋势衰竭或结构切换。",
            "；".join(facts) + f"；触发={reason}",
            "复核高周期趋势强度、入场距MA20和剩余利润空间；汇总后测试趋势衰减过滤。", "中",
        )
    useful_context = ", ".join(f"{key}={value}" for key, value in list(context.items())[:5])
    return LossDiagnosis(
        "待进一步归因", "现有证据能确认亏损结果，但不足以唯一确定市场原因。",
        "；".join(facts) + (f"；上下文={useful_context}" if useful_context else ""),
        "保留该笔触发、成交和保护证据；累计同类亏损后再提出规则修改，避免单样本过拟合。", "低",
    )
