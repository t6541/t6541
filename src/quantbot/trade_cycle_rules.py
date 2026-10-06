"""Single executable contract for the user-approved multi-timeframe rules."""

LOCAL_EXTREME_CANDLES = 3
ADJACENT_BODY_COVER_MINIMUM = 0.45

LONG_CYCLE_ROWS = (
    ("1分钟", "价格从外沿进入MA5内侧，MA5走平并上弯；近3根形成局部底", "止损=近3根K线下沿外"),
    ("5分钟", "阳线实体覆盖相邻前阴线至少45%", "仅确认局部/真正底部反转；回踩追多不要求"),
    ("15分钟", "上升趋势中出现回踩结构", "升级为15分钟顺势回踩多"),
    ("1小时", "已确认上升趋势", "与15分钟同向时作为高周期背景"),
)

SHORT_CYCLE_ROWS = (
    ("1分钟", "价格从外沿进入MA5内侧，MA5走平并下弯；近3根形成局部顶", "止损=近3根K线上沿外"),
    ("5分钟", "阴线实体覆盖相邻前阳线至少45%", "仅确认局部/真正顶部反转；反抽追空不要求"),
    ("15分钟", "下降趋势中出现反抽结构", "升级为15分钟顺势反抽空"),
    ("1小时", "已确认下降趋势", "与15分钟同向时作为高周期背景"),
)

# Six separate rule cards.  Each card is an execution identity, not a generic
# timeframe summary.  The stage names match the frozen launch-pattern codes.
CYCLE_RULE_SECTIONS = (
    ("局部底部做多", (
        ("阶段1｜1m", "近3根局部低点/扫低收回，首根阳线转强", "冻结局部低点；等待5m相邻阳线覆盖前阴线≥45%"),
        ("阶段2｜1m", "价格从MA5外沿进入内侧，MA5走平上弯；已冻结同向局部底", "立即核对市价；同K线及同锚点去重"),
        ("阶段3｜1m", "阶段2未成交，新鲜MA5上穿MA10小金叉且底部锚点仍有效", "补漏市价；禁止把旧信号在上涨末端追多"),
        ("确认｜5m", "相邻前阴线被当前阳线实体覆盖至少45%；盘中可确认", "局部底反转准入门；未达到不下反转单"),
        ("保护/退出", "止损在近3根1m低点外；毛空间须明显大于双边手续费", "普通局部仓1m MA5走平下弯止盈；五分钟确认后可接管"),
    )),
    ("局部顶部做空", (
        ("阶段1｜1m", "近3根局部高点/扫高回落，首根阴线转弱", "冻结局部高点；等待5m相邻阴线覆盖前阳线≥45%"),
        ("阶段2｜1m", "价格从MA5外沿进入内侧，MA5走平下弯；已冻结同向局部顶", "立即核对市价；同K线及同锚点去重"),
        ("阶段3｜1m", "阶段2未成交，新鲜MA5下穿MA10小死叉且顶部锚点仍有效", "补漏市价；暂时小底标签不能覆盖已确认顶部"),
        ("确认｜5m", "相邻前阳线被当前阴线实体覆盖至少45%；盘中可确认", "升级为五分钟局部顶；21:08样本覆盖55%"),
        ("保护/退出", "止损在近3根1m高点外；毛空间须明显大于双边手续费", "普通局部仓1m MA5走平上弯止盈；五分钟确认后可接管"),
    )),
    ("真正底部做多", (
        ("位置｜1m", "近3根实体低于此前12根，位于MA5<MA10<MA20发散末端", "确认真正底部锚点；MA10只用于该身份分类"),
        ("阶段1｜1m", "扫低收回/外沿K线走弱后首根反向阳线", "结构低点外小止损；早期可先试单"),
        ("阶段2｜1m", "价格进入MA5内侧、MA5走平上弯", "新鲜同向锚点直接下单"),
        ("确认｜5m", "阳线仍在MA5/MA20外侧，覆盖相邻前阴线实体≥45%", "盘中1至3根累计约半覆盖可升级双周期反转"),
        ("阶段3/退出", "漏掉阶段2后新鲜小金叉；反转极值未破、未重复试单", "补漏；1m/5m MA5逐级止盈，服务器结构止损保留"),
    )),
    ("真正顶部做空", (
        ("位置｜1m", "近3根实体高于此前12根，位于MA5>MA10>MA20发散末端", "确认真正顶部锚点；MA10只用于该身份分类"),
        ("阶段1｜1m", "扫高回落/外沿K线走弱后首根反向阴线", "结构高点外小止损；早期可先试单"),
        ("阶段2｜1m", "价格进入MA5内侧、MA5走平下弯", "新鲜同向锚点直接下单"),
        ("确认｜5m", "阴线仍在MA5/MA20外侧，覆盖相邻前阳线实体≥45%", "盘中1至3根累计约半覆盖可升级双周期反转"),
        ("阶段3/退出", "漏掉阶段2后新鲜小死叉；反转极值未破、未重复试单", "补漏；1m/5m MA5逐级止盈，服务器结构止损保留"),
    )),
    ("上涨趋势回踩追多", (
        ("背景｜15m/1H", "已确认高低点同步抬高，上级上涨锁仍有效", "身份为顺势回踩，不能把上方追价当回踩"),
        ("回踩｜5m", "向MA5/MA10带回落并出现止跌转强迹象", "按趋势结构确认；不要求相邻实体覆盖45%"),
        ("触发｜1m", "局部低点后首根阳线转强；不等待价格上穿MA5", "最近3根局部低点外止损；新形态才允许加层"),
        ("保护/退出", "方向、挂单/持仓去重和成本后1.8R空间全部通过", "普通仓1m MA5，明确高周期趋势仓逐级接管"),
    )),
    ("下跌趋势反抽追空", (
        ("背景｜5m/15m", "上一级5分钟或15分钟下跌已确认；1小时仅升级身份", "反抽追空独立于真正顶部反转门；可挑战短暂相反局部锁"),
        ("早期｜1m", "新局部反抽高点后首根阴线在MA5外沿转弱", "立即核对下单；不等下穿MA5，也不等5m覆盖45%"),
        ("确认｜5m", "后续阴线覆盖前阳线≥45%可升级为双周期", "覆盖未到45%不否决合格的上级趋势先行单"),
        ("补漏｜1m", "先行单未成交、仍在高点附近且出现新鲜小死叉", "禁止已远离MA5后追低；同五分钟K线去重"),
        ("小止损", "止损在最近3根1m高点外；盈利明显覆盖双边手续费", "持仓/挂单去重和利润空间门通过才提交"),
        ("保护/退出", "挂单/持仓去重和成本后1.8R空间全部通过", "普通仓1m MA5，明确高周期趋势仓逐级接管"),
    )),
)

def build_entry_rule_audit(*, direction: int, classification: dict, entry_kind: str,
                           three_stage_audit: dict, cover_ok: bool | None, position_ok: bool | None,
                           profit_space_ok: bool, indicator_ok: bool, stop_side_ok: bool) -> dict:
    """Freeze checks applied to an order for lifecycle and desktop comparison."""
    conditions = [
        {"name": "下单结构", "matched": entry_kind != "standard", "evidence": entry_kind},
        {"name": "1m/5m方向实体确认", "matched": cover_ok,
         "evidence": "方向覆盖门通过" if cover_ok else "独立分支按自身结构准入"},
        {"name": "局部顶底/回踩反抽位置", "matched": position_ok,
         "evidence": str(classification.get("rule") or "-")},
        {"name": "结构止损方向", "matched": stop_side_ok, "evidence": "止损位于入场反方向"},
        {"name": "盈利覆盖双边手续费", "matched": profit_space_ok,
         "evidence": "盈利空间及最近结构边界均通过"},
        {"name": "技术指标准入", "matched": indicator_ok, "evidence": "对应分支确认门通过"},
    ]
    # Historical three-stage evidence is shown separately: legacy branches have
    # independent valid triggers and must not be vetoed by a different template.
    stage = list((three_stage_audit or {}).get("conditions") or [])
    complete = all(item["matched"] is not False for item in conditions)
    return {"direction": direction, "classification": classification.get("label", "-"),
            "category": classification.get("category", "-"), "entry_kind": entry_kind,
            "complete": complete, "conditions": conditions, "three_stage_conditions": stage,
            "summary": f"{'做多' if direction > 0 else '做空'}｜{classification.get('label', '-')}｜"
                       f"{sum(item['matched'] is True for item in conditions)}/{len(conditions)}项执行门"}
