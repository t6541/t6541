from __future__ import annotations

import re


_PHRASES = (
    ("5m downtrend confirmed; 1m pullback rejected at falling MA20", "5分钟下跌趋势确认；1分钟反抽下降中的MA20后再次转弱"),
    ("5m downtrend remains valid; 1m pullback rejected at falling MA20", "5分钟下跌趋势持续有效；1分钟反抽下降中的MA20后再次转弱"),
    ("5m uptrend remains valid; 1m pullback rebounded at rising MA20", "5分钟上涨趋势持续有效；1分钟回踩上升中的MA20后再次转强"),
    ("downtrend confirmed; waiting for 1m MA20 pullback and bearish rejection", "下跌趋势已确认；等待1分钟反抽MA20后转弱"),
    ("uptrend confirmed; waiting for 1m MA20 pullback and bullish rebound", "上涨趋势已确认；等待1分钟回踩MA20后转强"),
    ("5m downtrend or falling MA20 is not confirmed", "5分钟下跌趋势或MA20下降尚未确认"),
    ("5m uptrend or rising MA20 is not confirmed", "5分钟上涨趋势或MA20上升尚未确认"),
    ("not enough closed candles for MA20 pullback", "已收盘K线不足，暂不能判断MA20回抽"),
    ("daily validation trade limit reached", "已达到当日模拟验证交易上限"),
    ("validation cooldown is active", "模拟验证交易仍在冷却期"),
    ("1m market data is stale", "1分钟行情数据已过期"),
    ("mark price is stale", "标记价格已过期"),
    ("latest price is unavailable", "无法取得最新价格"),
    ("this 1m validation candle was already processed", "这根1分钟验证K线已经处理，避免重复"),
    ("entry abandoned", "放弃入场"),
    ("structure stop is", "结构止损距离为"),
)

_TOKENS = {
    "observe": "观察", "error": "异常", "blocked": "已阻止", "manage": "持仓管理",
    "submitted": "已提交", "exit_submitted": "主动止盈已提交", "duplicate": "已去重", "placed": "已补挂",
    "reanchored": "已重锚", "armed": "有效等待", "invalidated": "已撤失效单",
    "sibling_cancelled": "已撤反向预埋单", "skipped": "已跳过",
    "bearish_flip": "看跌翻转", "bullish_flip": "看涨翻转",
    "bearish_continuation": "下跌趋势延续", "bullish_continuation": "上涨趋势延续",
    "bearish_trend": "下跌趋势", "bullish_trend": "上涨趋势", "ranging": "横盘震荡",
    "bearish_reversal_candidate": "看跌反转候选",
    "bullish_reversal_candidate": "看涨反转候选",
    "bearish_reversal_waiting_higher_timeframe": "看跌反转等待高周期确认",
    "bullish_reversal_waiting_higher_timeframe": "看涨反转等待高周期确认",
    "terminal_acceleration_reversal": "末端加速反转",
    "open_long": "开多", "open_short": "开空", "wait_entry_zone": "等待入场区",
}


def localize_main_status(value: object) -> str:
    text = str(value)
    for source, target in _PHRASES:
        text = text.replace(source, target)
    for source, target in sorted(_TOKENS.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(source)}(?![A-Za-z0-9_])", target, text)
    text = re.sub(r"(?<![A-Za-z0-9])15m(?![A-Za-z0-9])", "15分钟", text)
    text = re.sub(r"(?<![A-Za-z0-9])5m(?![A-Za-z0-9])", "5分钟", text)
    text = re.sub(r"(?<![A-Za-z0-9])1m(?![A-Za-z0-9])", "1分钟", text)
    text = text.replace("ATR", "平均真实波幅（ATR）").replace("ADX", "趋势强度（ADX）")
    return text
