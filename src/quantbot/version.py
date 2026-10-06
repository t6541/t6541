"""Single source of truth for product version and release notes."""

from __future__ import annotations

APP_VERSION = "0.7.394"

SIMPLE_EXECUTION_RULES = (
    ("末端全记录", "1m / 5m / 15m / 1H",
     "每个周期仅记录MA5与MA20有效发散、收盘处于两线外侧的末端；MA10显示但不作硬门槛。普通局部高低点不入末端表。"),
    ("逐级配对", "1m↔5m→15m→1H",
     "先记录持续末端区；真正1m+5m反转只要求五分钟随后1至3根反向K线累计约半覆盖，不要求五分钟穿越MA5、MA20或慢均线，再按同方向和时间顺序配对。"),
    ("未配对噪音", "1m末端",
     "1分钟末端与5分钟配不上时，只是1分钟局部顶底；按上一级5分钟趋势解释为反抽追空或回踩追多触发点。"),
    ("试单升级", "1m+5m配对成功",
     "五分钟约半覆盖后交由一分钟形态执行；15分钟与1小时同向趋势中的一分钟反抽高点/回踩低点可在穿越MA5前提前触发。"),
    ("5分钟局部反转", "下降底/上升顶",
     "局部底部须收盘位于MA5和MA20下方的发散末端，局部顶部镜像位于两线上方；MA10允许交叉。再满足五分钟反向实体半覆盖才可试单。前单已平仓不压制下一形态；趋势成立且仍持仓时可加至三层。成交先用一分钟MA5止盈，连续两根已收盘5分钟K线站稳MA20新趋势侧后才由五分钟MA5接管。"),
    ("5分钟趋势追单", "明确周期",
     "标签明写‘5分钟下跌趋势反抽追空’或‘5分钟上涨趋势回踩追多’。"),
    ("15分钟上级追单", "5分钟真正顶底",
     "15分钟趋势中的反抽/回踩位可免等1分钟穿过MA5，但5分钟反向实体半覆盖仍不可缺少。"),
    ("五种身份", "每单唯一",
     "真正顶底反转、局部顶底、反转三阶段补漏、5分钟趋势追单、15分钟上级趋势追单；新身份以后只增补不混用。"),
)

# The desktop strategy table shows the current consolidated rules.  Historical
# per-release notes belong in CHANGELOG and must not be mixed into this view.
CURRENT_STRATEGY_RULE_SUMMARY: tuple[tuple[str, str], ...] = (
    ("原始反转三阶段", "底部做多逐项核对：1分钟价格进入MA5内侧、MA5走平上弯、最近3根实体低于此前12根形成局部底、位于三均线发散末端且MA5<MA10<MA20；5分钟反向阳线实体仍在MA5与MA20下方，并覆盖相邻阴线实体至少45%。顶部做空完全镜像。"),
    ("外部实时触发", "PineTS、Webhook、Edge统一向本机实时入口提交明确方向、结构、价格、止损、止盈和指标快照；90秒内的新事件可独立触发，event_id去重，来源和完整证据写入订单生命周期。"),
    ("方向判定", "只按普通K线收盘价确认结构：高点与低点同步抬高为上涨，二者同步降低为下跌，交替混合为横盘整理。平均K线和1/5/15/60/240分钟方向箭头均不参与开仓裁决。"),
    ("真正顶底反转", "真正顶底反转由一分钟末端与五分钟盘中约半覆盖共同触发；五分钟运行中随后1至3根反向K线实体累计约45%即可算数，不等待收盘，不要求五分钟穿越MA5、MA20或慢均线。"),
    ("局部顶底反转", "局部顶部、底部同样允许五分钟盘中约半覆盖提前触发，以一分钟局部高低点外的小止损保护，避免等待五分钟收盘后价格走远而漏单。"),
    ("5秒顶部快空", "独立1秒扫描并行读取1分钟与5分钟形态。第一高点必须位于MA20及三均线上方，且MA5＞MA10＞MA20保持向上发散；次高点必须继承同一多头发散结构、仍位于三均线上方，并只比第一高点低一小段，才定义为顶部走弱。连续两次秒级扫描保持转弱后执行；跌破任一组三均线、两高点距离过大或进入底部反弹区域禁止新开空。"),
    ("趋势追单", "不要求双周期配对，严格逐级锁定：1小时末端锁定15分钟趋势，15分钟末端锁定5分钟趋势，5分钟末端锁定1分钟趋势。最近一级没有自己的新锁定时继承上级方向；顶部后反抽追空，底部后回踩追多。"),
    ("一分钟反转接管", "一分钟顶底反转无论试单成交或漏单，只要穿过MA20后首次回踩守住，或不回踩但MA20已同向拐弯并继续运行，即建立新鲜趋势锁定并优先于旧上级趋势；破坏反转极值或重新失守MA20后标记失败，恢复旧趋势。"),
    ("五类身份", "做空分为真正高位扫顶、局部高点扫顶、反转三阶段补漏、本周期下降趋势反抽、上一级周期下降趋势反抽；做多完全镜像，身份贯穿候选、订单、成交、止盈止损和漏单复查。"),
    ("止盈管理", "底部/顶部反转三阶段成交后先由一分钟MA5管理；一分钟MA5必须先顺势运行，再发生真实反向拐弯才止盈。连续两根已收盘5分钟K线站稳MA20新趋势侧后，才交给五分钟MA5接管；五分钟MA5同样须先顺势运行后反向拐弯。正式MA5拐弯属于趋势失效退出，直接执行且不受手续费盈利门否决；手续费门只限制普通提前锁利。"),
    ("结构保护", "所有订单止损置于对应局部高点或低点之外；真正末端结构在未突破其结构极值前持续有效，新同向真正高低点可覆盖旧锚点但旧记录保留审计。"),
)

# Keep the user-facing strategy rule table next to the product version.  Any
# release that changes execution must update this table and the changelog in
# the same commit so the desktop view always describes the running code.
STRATEGY_RULES: tuple[dict[str, str], ...] = (
    {
        "strategy": "策略01｜双均线趋势",
        "version": "demo-frequency-validation-v111",
        "continuation": "【新增⑤ 下跌追空】5m与15m同时MA5<MA10<MA20、MA20下降；1m先反抽MA10/MA20附近，再由实体≥0.30 ATR阴线跌破前低确认。所有顺势空单统一要求距5m MA20不超过1.0 ATR；确认后成交前若又下跌超过0.35 ATR则取消。做多镜像。",
        "trigger": "【多规则并行原则】所有新增形态均作为独立触发源并行评估，满足哪一种就采用哪一种；不覆盖、不删除以前的MA5、MA20、趋势延续、反转候选、扫损及结构预埋规则。\n【激进型局部高点盘中吞没快空】前一根已收盘阳线位于一分钟局部高点；下一根未收盘阴线从阳线顶部附近向下运行，当阴线实体已经超过前阳线完整高低长度并跌破阳线开盘价时立即市价小风险做空，止损放局部高点外。\n【激进型横盘高点MA5快空】一分钟横盘局部高点出现阳线，随后已收盘十字星，当前未收盘阴线盘中下穿MA5超过0.05 ATR且距MA5不超过0.45 ATR时立即市价小风险做空；止损放本轮局部高点外。\n【激进型底部三阶段接力】第一阶段触碰支撑结构提前单；第二阶段为前一根已收盘底部十字星已令MA5向上，当前一分钟阳线盘中上穿MA5超过0.05 ATR且距MA5不超过0.45 ATR时立即市价追多；若第二阶段已经远离MA5则不追高，转入第三阶段等待。第三阶段为一分钟已经站上MA20后，当前未收盘K线第一次回踩MA20守住并回升、MA20不再下降且五分钟MA5继续向上时再次市价追多。同一分钟只处理一次，止损放各自局部结构低点外。\n【激进型顶部盘中MA20快空】顶部阳线后出现阴线转弱，一分钟MA20已经走平或向下、五分钟MA20没有强势上升时，当前尚未收盘的一分钟阴线盘中下穿MA20，或开线后继续位于MA20下方，即可局部小止损市价做空；低于MA20超过0.45倍一分钟ATR禁止追空，止损放本轮局部高点外。\n【激进型小底部反弹多】5m、15m仍为下跌背景；1m长阴后夹十字星，首根阳线收盘站上MA5且MA5向上拐头，小资金做多，止损放小底部外。本信号只表示下跌中的反抽，不把高周期误报成反转。\n【十五分钟恢复补进】两根已收盘15分钟阳线连续抬高，5分钟MA5上穿MA10并继续上升，1分钟MA5再次向上确认时允许补进多单，不等待15分钟旧均线完全翻多；不使用尚未收盘的15分钟阳线。\n【三阶段反转① 极值扫损】底部扫破近10根低点后由已收盘阳线收回针体≥55%提前做多；顶部镜像为扫破近10根高点后由已收盘阴线回落针体≥55%提前做空。均须靠近5m结构并出现放量/大振幅衰竭。\n【三阶段反转② 候选等待】5m较高低点/较低高点进入候选，由1m MA20长实体穿线或回踩/反抽后的短结构突破确认。若15m仍强势，只有已收盘5m实体反抽MA20上穿失败且收在MA5/MA10/MA20下方，才允许随后已收盘1m阴线下穿MA20抢先做空；做多镜像。\n【三阶段反转③ 确认回踩】5m连续4根有效换边并通过斜率、实体结构和15m过滤后，等待首次MA20回踩/反抽，不追确认K线。\n【原有】三周期、MA20回抽、放量突破、双向末端反转和0.35 ATR后二次追单继续保留。",
        "take_profit": "激进型趋势仓逐级接管：一分钟大交叉后由五分钟MA5管理，五分钟大交叉后升级到十五分钟MA5，十五分钟大交叉后升级到一小时MA5。高周期交叉只升级已有仓位止盈，不产生追单；接管周期MA5由顺势运行转为走平或反向拐弯时止盈。服务器结构止损始终保留。",
        "stop_loss": "突破回踩多单使用1m回踩低点/突破实体位外专属止损；其他分支仍用最近8根5m结构影线外动态止损；标记价触发。",
        "limits": "【激进型无时间冷却】激进型自动执行不设候选或成交后时间冷却；保守型、稳妥型继续保持5分钟。\n【取消扎针停机】不再因1分钟/5分钟长影线或异常放量统一暂停新开仓10分钟；提前反转与结构极值预埋可继续捕捉插针。仍保留15分钟强趋势方向过滤、结构止损、利润空间、账户安全与同一K线去重。\n仅OKX模拟盘；当前每次1张、最高10×；模拟测试不限制5分钟趋势或连续同方向次数；重启后重新人工解锁。",
    },
    {
        "strategy": "策略02｜相对极值反转＋趋势延续追单",
        "version": "range_pivot_reversal_v59",
        "continuation": "【分支B｜趋势延续追单】独立统计。5m、15m下跌共振后等待1m反抽MA10/MA20失败并跌破前低追空；确认时距5m MA20≤1.0 ATR，确认后成交前再下跌超过0.35 ATR即取消。订单前缀QBRC，交易次数、胜负、毛盈亏、手续费与净盈亏不计入极值反转成绩。",
        "trigger": "【分支A｜相对极值反转】真正执行高点做空、低点做多：保留5–10根5m结构高低区、上部25%做空、下部25%做多及1m反转确认；末端加速分支允许此前2次、3次或更多扩张，回到5m MA20±0.35 ATR复位后，下一次扫高/扫低距MA20≥2 ATR，再由1m放量反向实体突破前低/前高确认。订单前缀QBRX。\n【三阶段第一阶段｜双向扫损】结构下部25%扫低后由已收盘阳线收回针体≥55%提前做多；结构上部25%扫高后由已收盘阴线回落针体≥55%提前做空。均要求靠近5m结构并出现放量/大振幅衰竭，止损置于扫损极值外；只有针线没有收盘收回时继续等待。\n【共享快触发】5m较低高点/较高低点先进入反转候选；1m已收盘长实体≥0.30 ATR穿越1m MA20可抢先触发。首次穿线后已运行>0.35 ATR不追；回到1m MA10/MA20带±0.35 ATR后，以≥0.25 ATR实体再次突破最近两根结构执行二次追单。\n【分支隔离】每个信号、下单意图、订单事件和交易生命周期均写入branch；模拟测试取消连续同向3单上限，成绩仍按分支单独汇总。",
        "take_profit": "1分钟/5分钟相对极值与区间反转使用服务器固定目标止盈，以最新成交价触发，触及目标立即兑现；止盈距离必须大于止损距离。只有趋势延续分支保留服务器移动止盈。",
        "stop_loss": "普通分支仍用最近8根5m结构影线外加ATR缓冲；极端摸顶空放在最新5m/最近1m最高价之上；低位扫损收回多单放在扫损最低点下方。缓冲=max(对应周期ATR缓冲, 入场价0.03%)；标记价触发。",
        "limits": "【5分钟均线乖离提前反转】结构高区/低区距已收盘5分钟MA5/MA10/MA20均线带≥1.50 ATR后，一分钟扫过边缘并以长影线收回即可提前下单，不等待V形或均线突破；影线必须≥实体且≥0.35倍一分钟ATR。\n【五分钟限价预埋】条件成立即可在对应高低区提前挂post-only限价单，只挂满足乖离的方向；持续动态重锚、30分钟换新、失效撤销并支持重启恢复。\n【5分钟区间硬门】反转高低区固定由已收盘5分钟K线确定，宽度必须≥2.50 ATR；做多必须在下部25%，做空必须在上部25%，所有提前和末端反转均不得绕过；横盘中部禁止下单。\n【取消扎针停机】1分钟/5分钟插针不再触发10分钟暂停；仍保留15分钟强趋势过滤、结构止损、盈利空间和账户安全检查。\n仅OKX模拟盘；每次1张、最高10×；重启后重新人工解锁。",
    },
    {
        "strategy": "策略03｜MA20趋势翻转回抽",
        "version": "ma20_trend_retest_v60",
        "continuation": "【新增⑤ 下跌追空】原MA20首次/延续回抽与新增追空全部共用利润空间闸门：距5m MA20最多1.0 ATR，确认后成交前最多再运行0.35 ATR；超限整轮信号作废。做多镜像。",
        "trigger": "【三阶段① 极值扫损】底部扫低后已收盘阳线收回针体≥55%早多；顶部扫高后已收盘阴线回落针体≥55%早空。均要求靠近5m结构并有放量/大振幅衰竭。\n【三阶段② 候选确认】5m较高低点/较低高点进入候选，由1m MA20长实体穿线或回踩/反抽后的短结构突破确认；若15m仍强多，普通1m回落继续禁止做空。仅当最新已收盘5m阴线反抽MA20上穿失败、收在MA5/MA10/MA20下方且实体≥0.20 ATR，再由已收盘1m长阴下穿MA20并跌破短结构时，才提前小风险做空；做多完全镜像。\n【三阶段③ 确认回踩】原趋势前8根至少6根同侧；连续4根有效换边并通过MA20斜率、实体结构和15m过滤后，只验证确认后的首根5m MA20回踩/反抽，错过立即作废。\n【高位反转保留】末端加速、结构极值扫高回落仍走独立高位门槛，不受普通候选拦截。\n【原有】放量突破、双向末端反转与0.35 ATR后二次追单继续保留；翻转后20根动态回抽已于v0.6.70取消。",
        "take_profit": "1分钟/5分钟MA20回抽、反转和扫损收回使用最新成交价固定止盈；只有明确的高周期同向趋势延续分支保留服务器移动止盈。",
        "stop_loss": "普通MA20回抽放在结构低点/高点之外。低位扫损收回早多必须回溯原始5分钟扫损结构低点，并在其下方增加max(0.15×1m ATR, 入场价0.03%)缓冲；不得改用后续小平台局部低点。若真实止损距离>1.5×1m ATR或>0.8×5m ATR，直接取消本单。",
        "limits": "【取消扎针停机】不再以1分钟/5分钟插针暂停10分钟；MA20回抽、提前反转和结构预埋保持实时评估。仍保留15分钟方向过滤、结构保护、利润空间和账户异常阻止。\n仅OKX模拟盘；每次1张、最高10×；翻转只验证确认后的首根5分钟回踩/反抽；同一确认K线去重；重启后重新人工解锁。",
    },
    {
        "strategy": "共享策略｜三策略共用规则",
        "version": "shared-rules-v44",
        "continuation": "【激进型（Demo默认）】启用极值提前反转、候选区抢先、结构极值预埋狙击和趋势延续；仍必须通过全部安全闸门。\n【保守型】只启用趋势确认、放量突破回踩和MA20回踩/反抽；关闭极值提前单、候选抢先和结构预埋狙击。\n【稳妥型】只接受多周期完整确认后的突破/回踩与趋势延续，必须完整通过结构止损和剩余盈利空间检查。",
        "trigger": "【共享实验｜MA偏离插针回归】只观察、不下单。用上一根已收盘K线的MA5/MA10均值生成下一根假想预埋价；1m同时记录固定2U对照组与ATR+ADX动态组，5m/15m只记录ATR+ADX动态组。ADX越强动态距离越宽，最多扩大至基础ATR距离1.5倍；触碰后冻结均线目标，跨重启累计回归、超时和最大不利波动。\n【共享｜结构极值动态预埋】只在激进型启用。用已收盘5m/15m确认前高挂post-only空单、前低挂post-only多单；每轮观察重新验证结构、ADX、MA20斜率、当前价所在区间和盈亏比。\n【共享｜动态重锚】结构价移动达max(0.25×1m ATR,当前价0.05%)时撤旧挂新；单张挂单每30分钟换新一次。只要条件持续成立就可滚动续留，不因固定1–2分钟到期而漏掉后续插针。\n【共享｜失效撤单】15m ADX>35或MA20斜率绝对值>0.12 ATR、结构消失、当前价跑出高低区间、回归MA5/MA10空间不足或保护不合格时，立即撤销已有预埋单。\n【共享｜三阶段反转】极值扫损收回提前入场、候选区1m确认、完整反转后MA20首次回踩/反抽，多空完全镜像。",
        "take_profit": "【共享退出】1m/5m反转、回踩和普通确认使用最新成交价固定止盈；只有高周期同向趋势延续保留服务器移动止盈。结构狙击以MA5/MA10回归为目标，利润空间不足不挂单。",
        "stop_loss": "【共享保护】结构单在极值外使用标记价止损，缓冲和绝对距离下限统一由共享保护模块校验；任何逐条OKX回执失败都不得误报成功。",
        "limits": "【共享安全】仅OKX Demo；当前每张1张，同方向持仓与预埋最多2张；一侧成交立即撤销反向挂单；多空同时持仓、存在其他普通委托或异常状态时禁止新增。\n【共享｜恢复入口】每次点击开始观察，激进型先对三个已保存Demo账户逐一核对结构预埋并显示结果；点击单个策略解锁时，也立即只恢复该策略自己的预埋单。缺失且条件有效则补挂，正确单保留，失效/重复单清理；随后观察期间每60秒持续对账、动态重锚和30分钟换新。\n【共享｜OKX时间校准】明确收到50102时自动读取OKX公开服务器时间、计算本机偏移并重新签名。GET可安全重试；POST只在OKX明确以50102拒绝、确认原请求未被接受时重试一次，普通网络超时绝不重试下单。",
    },
)

_VOLUME_STOPPING_RULES = (
    "【共享｜双周期放量止跌】五分钟上涨趋势回踩先放量止跌且后续不再有效创新低；一分钟V形低点与收回阳线必须同步放量，阳线突破前高并站上MA5/MA10后市价做多。止损在V形下影线下方，止盈冻结在五分钟MA20上方，至少1.5R。",
    "【共享｜双周期放量止跌】五分钟放量止跌后由一分钟放量V形收回触发多单；仍必须位于已确认五分钟区间下部25%，止盈冻结在五分钟MA20上方且至少1.5R。",
    "【共享｜双周期放量止跌】五分钟上涨回踩放量止跌且不再有效创新低后，一分钟放量V形阳线突破前高、站上MA5/MA10触发多单；止损在V形下影线下方，止盈冻结在五分钟MA20上方且至少1.5R。",
    "【共享｜双周期放量止跌】五分钟负责上涨回踩环境、放量止跌及不再创新低；一分钟负责放量V形收回和突破前高。市价兜底与原限价预埋并行；策略02不得绕过区间下部25%硬门槛。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{volume_rule}\n{rule['trigger']}"}
    for rule, volume_rule in zip(STRATEGY_RULES, _VOLUME_STOPPING_RULES)
)

_V073_RULES = (
    "【v0.6.73｜追空门禁修正】十五分钟慢趋势仍明确向下时，五分钟短暂进入看涨反转等候区不再一刀切暂停追空；仍须通过五分钟三次降低高点、MA20下行、次高未破、一分钟反抽转弱和距MA20不超过1 ATR。做多镜像。",
    "【v0.6.73｜共享追空】接收同一高周期顺势反抽信号；策略02原有区间位置、结构和利润空间门槛保持不变，不因共享而无条件下单。",
    "【v0.6.73｜等候区顺势单】反向等候只代表五分钟候选，不等于十五分钟已经反转；高周期仍同向时允许原趋势反抽分支继续逐项验证，确认反转后立即禁止旧方向。",
    "【v0.6.73｜瀑布底部小仓反弹】五分钟放量长阴至少1.8倍均量并深度跌离MA20，随后停止有效创新低；优先用一分钟放量止跌下影结构挂Demo限价多单。未成交时仅在一分钟放量V形阳线突破前高、结构止损明确且到五分钟MA10/MA20首个目标至少1.5R时，允许小仓市价多单。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{v073_rule}\n{rule['trigger']}"}
    for rule, v073_rule in zip(STRATEGY_RULES, _V073_RULES)
)

_V070_RULES = (
    "【v0.6.70｜取消价格通道】策略01不再使用1分钟20根价格通道触发、过滤追高追低或显示状态；只依据多周期方向、五分钟结构、MA5/MA10/MA20、ATR、ADX及成交量。已收盘一分钟放量止跌线先在下影结构挂post-only限价多单，V形市价确认保留兜底；普通下跌趋势禁止逆势挂多，仅距5m MA20至少1 ATR的放量止跌极值允许小风险实验。",
    "【v0.6.70｜限价优先】共享一分钟放量止跌线可先生成下影结构post-only限价多单，未成交仍保留V形市价兜底；仍必须位于五分钟区间下部25%，不得绕过策略02硬门槛。",
    "【v0.6.70｜旧方向失效】策略03完全取消翻转后20根追踪；确认后的首根5分钟K线未形成有效MA20回踩/反抽即作废。已收盘五分钟反向穿越MA20、位于MA5/MA10反侧且MA5转向时也立即失效；普通下跌趋势禁止逆势挂多，仅深度乖离放量止跌极值例外。",
    "【v0.6.70｜限价优先与方向仲裁】所有有效下单候选先检查能否在结构位安全预埋；一分钟放量止跌线使用下影结构限价优先，后续市价确认兜底。相反方向已确认后立即淘汰旧候选，结构止损、至少1.5R、账户安全与Demo边界不变。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{v070_rule}\n{rule['trigger']}"}
    for rule, v070_rule in zip(STRATEGY_RULES, _V070_RULES)
)

_V071_RULES = (
    "【v0.6.71｜三次降低高点做空】五分钟最近三段反抽高点必须依次降低且每段至少下降0.05 ATR，MA20保持向下；反抽不得破坏次高结构，再由一分钟触及MA10/MA20后的阴线跌破短结构确认做空。",
    "【v0.6.71｜三次降低高点做空】共享同一结构识别，但策略02仍须通过五分钟区间边缘硬门槛；禁止在区间中部绕过规则做空。",
    "【v0.6.71｜三次降低高点做空】优先检查上方反抽结构能否挂Demo post-only限价空单；未成交或错过时保留一分钟转弱市价空单，距五分钟MA20超过1 ATR后禁止低位追空。",
    "【v0.6.71｜共享方向规则】三策略共享三次降低高点、均线反抽失败和一分钟转弱确认；限价优先、市价兜底及全部安全门保持不变。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{v071_rule}\n{rule['trigger']}"}
    for rule, v071_rule in zip(STRATEGY_RULES, _V071_RULES)
)

_V072_RULES = (
    "【v0.6.72｜共享瀑布趋势持仓】仅对已经通过策略01入场门槛的高周期同向趋势延续单生效，不新增、不放宽任何入场信号。1分钟与5分钟均线同向排列、15分钟不逆向、5分钟实体至少0.75 ATR、成交量至少1.5倍且1分钟连续加速时，改用宽幅服务器移动保护。",
    "【v0.6.72｜共享瀑布趋势持仓】策略02仅在趋势延续分支通过原五分钟区间边缘、结构、利润空间和方向门槛后才可进入；相对极值和普通区间反转仍使用固定止盈，禁止借瀑布规则绕过硬门槛。",
    "【v0.6.72｜共享瀑布趋势持仓】策略03仅在高周期同向趋势延续状态启用；普通MA20反转、扫损收回和短线回抽仍按固定目标退出。瀑布单保留结构止损，移动激活至少1R或0.8倍5分钟ATR，回撤至少0.65倍5分钟ATR或0.35R。",
    "【v0.6.72｜共享退出管理】入场方向与退出方式分离；三策略统一识别放量瀑布，多空镜像，判定和保护参数写入订单事件。仅OKX模拟盘，服务器止损始终保留，不把普通下跌误判为瀑布。",
)
STRATEGY_RULES = tuple(
    {**rule, "take_profit": f"{v072_rule}\n{rule['take_profit']}"}
    for rule, v072_rule in zip(STRATEGY_RULES, _V072_RULES)
)

_V074_RULES = (
    "【v0.6.74｜实体支撑预埋】五分钟连续3根已收盘实体站上MA20后，首次回踩优先在确认段前实体低点挂Demo限价多单；不追求长下影极值。连续实体收回MA20下方、超过6根仍未回踩或收益风险不足时撤销；市价确认继续兜底。",
    "【v0.6.74｜限价优先与主动退出】共享实体支撑/压力预埋，不机械挂在长影线极值；策略02原有区间边缘、结构和利润空间硬门保持。限价或市价成交后，若多周期反转、放量反向实体破坏均线带，或持仓30分钟横盘后向不利方向失守MA10，允许提前平仓。",
    "【v0.6.74｜首次回踩与主动退出】确认突破后的首次MA20回踩优先尝试实体位置限价单，错过后保留原市价单。限价和市价持仓统一接受主动风险退出；固定止盈止损仍在服务器保留，单纯横盘或等待时间长不构成退出。",
    "【v0.6.74｜共享预埋与情况不妙立刻退出】预埋价优先前实体柱高低点和MA20合理带，不贪长影线极值；每轮动态复核，位置明显移动则重锚，结构失效才撤销。所有限价与市价持仓统一保留固定止盈止损，并在多周期结构反转、放量反向破坏或长期横盘后不利破位时主动市价减仓退出。普通等待不触发退出；固定止盈利润至少覆盖1.5R，确认趋势移动止盈激活至少1R，两者还必须覆盖0.8倍1分钟ATR和入场价0.12%。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{v074_rule}\n{rule['trigger']}",
     "take_profit": f"【v0.6.74｜主动风险退出】固定止盈止损继续保留；明确情况不妙时可提前平仓，且对限价单与市价单一视同仁。\n{rule['take_profit']}"}
    for rule, v074_rule in zip(STRATEGY_RULES, _V074_RULES)
)

_V075_RULES = (
    "【v0.6.75｜多周期压力提前空】15分钟识别前高与实体压力区，5分钟确认冲高拒绝/衰竭；一分钟每次阴线实体下穿MA20都记录为候选，不再强制等待三个高点连续降低。优先在实体压力位挂Demo限价空单，失效或错过后保留市价确认兜底，并继续执行利润空间闸门。",
    "【v0.6.75｜共享信号接入】接收共享多周期压力提前空信号并进入统一排队；策略02原有区间边缘、结构止损、利润空间和风险门槛保持不变，不能仅凭一次下穿绕过安全条件。",
    "【v0.6.75｜提前下穿候选】接收15分钟压力、5分钟拒绝和1分钟MA20下穿候选；高点即使高于前高，只要随后出现明确拒绝也可触发，不再恢复旧的20根翻转追踪。实体压力限价优先，市价确认继续兜底。",
    "【v0.6.75｜共享信号中心与启动恢复】软件启动、开始观察或解锁时先扫描15分/5分/1分盘面，恢复仍有效的触发排队；一分钟每次阴线下穿MA20均留存审计。相同事件键禁止重复入队；激进型才允许提交OKX Demo预埋单，保守型和稳妥型只记录候选，严禁实盘。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{v075_rule}\n{rule['trigger']}"}
    for rule, v075_rule in zip(STRATEGY_RULES, _V075_RULES)
)

_V076_RULES = (
    "【v0.6.76｜持久动态双侧预埋】震荡、上涨或下跌状态均维护仍有效的支撑多单与压力空单；挂单不会因等待30分钟自动撤销，仅成交、结构失效、风险冲突或价格明显移动需要重锚时撤销。市价确认兜底保留。",
    "【v0.6.76｜策略02持久预埋】策略02的结构预埋单不再短暂闪现或固定时限消失；有效结构内持续等待，价格明显移动才动态重锚。原区间边缘、利润空间和风险门槛保持不变。",
    "【v0.6.76｜成交接管后风险退出】任一预埋成交后取消其余开仓挂单，但同一轮仍执行多周期反转、放量反向破坏与不利横盘的主动退出检查；平仓后重新验证候选排队。",
    "【v0.6.76｜共享恢复与Demo边界】软件启动、开始观察或解锁时先扫描并恢复有效双侧预埋和触发排队；仅OKX模拟盘，禁止实盘。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{v076_rule}\n{rule['trigger']}"}
    for rule, v076_rule in zip(STRATEGY_RULES, _V076_RULES)
)

_V077_RULES = (
    "【v0.6.77｜启动与重连恢复】软件启动后立即扫描已保存Demo账户、有效双侧预埋与共享触发排队；运行中发生连接异常后，首个完整恢复周期立即再次扫描。",
    "【v0.6.77｜策略02恢复硬门】启动、观察、解锁和断线重连恢复均重新计算五分钟区间宽度、MA乖离与允许方向；失效方向不得因恢复流程继续保留或补挂。",
    "【v0.6.77｜启动与重连恢复】软件启动及网络恢复后立即重新核对结构预埋和共享触发排队，仍须通过策略03原有结构、利润空间与风险门槛。",
    "【v0.6.77｜全入口恢复】启动、开始观察、单策略解锁及网络重连均调用统一恢复扫描；非激进型只扫描并记录候选，所有订单仍仅限OKX模拟盘。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{v077_rule}\n{rule['trigger']}"}
    for rule, v077_rule in zip(STRATEGY_RULES, _V077_RULES)
)

STRATEGY_RULES = tuple(
    {**rule, "trigger": (
        "【v0.6.78｜策略03横盘反转降频】近12根已收盘5分钟K线反复穿越MA20、方向效率低、MA5/MA10/MA20压缩且MA20斜率不足时，认定为横盘压缩；策略03不确认新趋势，也不执行快速反转候选。只有带量的五分钟MA20失败反抽/回踩等独立强证据可以例外；真正结构极值反转保持独立。\n"
        + rule["trigger"]
    )} if index == 2 else rule
    for index, rule in enumerate(STRATEGY_RULES)
)

_V079_SHARED_GATE = (
    "【v0.6.79｜共享预埋最终风控】所有结构预埋及后置叠加层完成后，统一复核止损、利润空间与强趋势方向。止损至少覆盖入场价0.20%或2倍1分钟ATR；目标至少覆盖入场价0.30%、3倍1分钟ATR及1.5R。15分钟ADX>35或MA20斜率绝对值>0.12 ATR时，普通结构预埋只保留趋势同向一侧；真正结构极值反转仍走独立证据链。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{shared_gate}\n{rule['limits']}"}
    for rule, shared_gate in zip(STRATEGY_RULES, _V079_SHARED_GATE)
)

_V080_SHARED_RELIABILITY = (
    "【v0.6.80｜断线隔离与下单通知】共享预埋恢复扫描出现读取超时时，只标记该子任务异常并继续本轮三个策略独立检查；连接恢复后继续复扫。每个新提交的Demo普通订单、结构预埋补挂或重锚订单均携带OKX订单号进入桌面弹窗；已有有效挂单的例行扫描不重复提示。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{reliability}\n{rule['limits']}"}
    for rule, reliability in zip(STRATEGY_RULES, _V080_SHARED_RELIABILITY)
)

_V081_DIRECT_ROLLOVER = (
    "【v0.6.81｜顶部直接转弱首次下穿】新增独立并行触发分支，不覆盖反转等待区、第一至第四次MA20下穿、趋势延续、极值反转或结构预埋。五分钟原上涨排列在顶部失速，已收盘阴线跌破MA5/MA10且MA5转下后，首次已收盘一分钟阴线下穿MA20可排队做空；若仅影线下穿而收盘仍在MA20上方，必须收盘紧贴MA20、MA20走平转下、阴线实体至少0.30 ATR且成交量不低于近期均量。仍执行去重、结构止损、利润空间和账户安全门槛。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{direct}\n{rule['trigger']}"}
    for rule, direct in zip(STRATEGY_RULES, _V081_DIRECT_ROLLOVER)
)

_V082_RANGE_STAGES = (
    "【v0.6.82｜真实局部边缘与三触发】策略02先识别位移K线后的局部五分钟区间，排除远端尖峰把区间拉宽；仅真实支撑/压力边缘允许反转。三入口并行：支撑多/压力空预埋；未成交时一分钟阳线收上MA5做多/阴线收下MA5做空；再次错过后，站上MA20后的回踩交叉点做多/跌破MA20后的反抽交叉点做空。第二、第三入口必须先有近期边缘触碰且仍处局部半区。",
    "【v0.6.82｜策略02局部区间】位移K线后至少形成3根已收盘五分钟K线，局部宽度至少0.80 ATR才有效；远端尖峰不再把局部中部误算成底部。原区间反转、趋势延续及其他并行入口继续保留。",
    "【v0.6.82｜浮亏不抢平】主动风险退出只在方向浮盈至少0.12%时用于保护收益；浮亏或不足手续费/滑点缓冲时继续由服务器原结构止损保护，不再仅因短时不利主动市价亏损平仓。",
    "【v0.6.82｜共享退出与镜像三触发】主动提前退出必须已有足够浮盈；策略02底部三触发与顶部镜像三触发独立排队、统一去重和风控，仅OKX模拟盘。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{stage}\n{rule['trigger']}"}
    for rule, stage in zip(STRATEGY_RULES, _V082_RANGE_STAGES)
)

_V083_UNLOCK_RESILIENCE = (
    "【v0.6.83｜解锁抗503】三个策略的解锁API校验改为串行，避免同时点击形成请求突发。GET及设置杠杆等幂等配置遇到OKX明确HTTP 503/50001临时不可用时，重新签名并按0.5、1.5、3秒退避重试；真实下单POST仍不因未知网络结果自动重发，防止重复订单。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{resilience}\n{rule['limits']}"}
    for rule, resilience in zip(STRATEGY_RULES, _V083_UNLOCK_RESILIENCE)
)

_V084_CONTINUOUS_LINES = (
    "【v0.6.84｜双线不断档】上涨、下跌和震荡均持续维护受保护的支撑多单线与压力空单线。动态重锚必须先提交新线并取得OKX订单号，再撤销旧线；新线失败时只清理新单，旧线继续显示。短暂结构不确定时保留旧线复核，仅成交、持仓冲突、价格穿越或保护硬失效允许撤销。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{continuity}\n{rule['limits']}"}
    for rule, continuity in zip(STRATEGY_RULES, _V084_CONTINUOUS_LINES)
)

_V085_CONTRACT_SUSPENSION = (
    "【v0.6.85｜51022暂停恢复】OKX模拟盘返回sCode 51022 Contract suspended时，不再记为网络异常或每5秒重复提交；进入60秒合约暂停等待态。三策略共享暂停冷却，期间保留可见旧线和排队状态；到期自动探测，合约恢复后立即重新扫描并补挂双侧结构线，无需重新解锁。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{suspension}\n{rule['limits']}"}
    for rule, suspension in zip(STRATEGY_RULES, _V085_CONTRACT_SUSPENSION)
)

_V087_TRIGGER = "【v0.6.87｜双周期均线发散追单】原三阶段错过后，已收盘1分钟与5分钟同时MA5/MA10/MA20同向排列、同向倾斜且间距扩张，立即市价追涨/追空，不等待回踩/反抽；距5分钟MA20超过2.0 ATR禁止末端追单。与所有旧触发并行。"
_V087_TAKE_PROFIT = "【v0.6.87｜MA5转向止盈】取消固定止盈与移动止盈，服务器结构止损始终保留；浮盈仓由已收盘5分钟反向K线触碰MA5且MA5走平/反向时强制本地止盈，多空镜像，1分钟不利证据仍可提前退出。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V087_TRIGGER}\n{rule['trigger']}", "take_profit": _V087_TAKE_PROFIT}
    for rule in STRATEGY_RULES
)

_V088_EXPERIMENT = "【v0.6.88｜共享实验验证下单】原1299+观察样本继续只观察；新增独立OKX Demo验证分支，仅用1分钟ATR+ADX动态距离、ADX<25、冻结MA5/MA10中轴止盈和1.5ATR服务器止损。与策略01/02/03成绩隔离；达到100笔真实Demo成交后停止新增并提示人工评审，绝不自动并入正式策略。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V088_EXPERIMENT}\n{rule['trigger']}"} if rule["strategy"].startswith("共享策略") else rule
    for rule in STRATEGY_RULES
)

_V089_TRIGGER = "【v0.6.89｜MA5优先反转与趋势追单】顶部第一根已收盘1分钟阴线向下穿越走平/向下的MA5优先做空，MA20下穿继续作为兜底；底部阳线上穿MA5优先做多、MA20兜底，完全镜像。趋势延续中，1分钟反抽/回踩MA20且5分钟也靠近MA20后，再次穿越MA5可提前追空/追多。所有旧触发继续独立并行排队。"
_V089_TAKE_PROFIT = "【v0.6.89｜针尖优先＋1分钟MA5精确止盈】仅对已有浮盈仓生效：快速拉升/下跌明显乖离后，已收盘1分钟长上影/长下影形成反向拒绝时优先兑现；否则空单在MA5走平/向上抬头、多单在MA5走平/向下弯头时强制止盈。原5分钟MA5止盈保留为兜底，服务器结构止损始终保留。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V089_TRIGGER}\n{rule['trigger']}", "take_profit": _V089_TAKE_PROFIT}
    for rule in STRATEGY_RULES
)

_V090_ORDER_MAINTENANCE = "【v0.6.90｜预埋线无缝换线与成交提示】动态重锚必须先挂出全部新单并确认每张均取得有效OKX订单号，随后才撤旧单；任一新单失败或订单号为空时保留旧支撑/压力线。挂单、重锚、启动恢复与断线恢复不弹窗，仅真实Demo成交后按订单号提示一次。"
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{_V090_ORDER_MAINTENANCE}\n{rule['limits']}"}
    for rule in STRATEGY_RULES
)

_V091_SIX_TIMEFRAME = "【v0.6.91｜共享六周期判断】策略01/02/03统一读取已收盘1分钟、5分钟、15分钟、30分钟、1小时、4小时K线：4小时和1小时定主环境，30分钟识别延续/衰竭过渡，15分钟确认中级结构，5分钟确定交易区，1分钟精确触发。高周期冲突影响风险与后续加仓，但不一票否决结构顶底早期反转。"
_V091_COLOR_REVERSAL = "【v0.6.91｜顶底颜色反转第一触发】5分钟结构上部局部顶点出现前阳后阴，反向实体、量能或破前低证据合格即可小风险抢先做空；即使仍在MA5上方且MA20向上也允许。底部阴转阳完全镜像。与MA5优先、MA20兜底、预埋和趋势追单并行。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V091_SIX_TIMEFRAME}\n{_V091_COLOR_REVERSAL}\n{rule['trigger']}"}
    for rule in STRATEGY_RULES
)

_V092_SMALL_CROSS = "【v0.7.67策略v92｜一/五分钟局部小交叉与3点风险兜底】一分钟和五分钟均独立识别MA5/MA10小金叉、小死叉：只在各自最近16根区间下部38%/上部38%且最近6根触及局部低点/高点时有效，区间中部交叉仅记录不下单。两个周期每次交叉均持久记录时间、价格、区间位置、有效性、处理结果，并与另一周期最近同方向交叉记录时间差；谁先形成有效局部交叉谁先触发，另一周期随后只确认比较、不重复下单。顶部走弱做空不再限定单根阴线精确穿线：首次阴线下穿MA5后的第1至3根，只要价格持续位于MA5下方仍是有效触发；若一分钟新鲜小死叉形成，即使实体未完全收在MA5下方，只要下影线已经下穿MA5，也作为第二个防漏做空触发。两者均须先通过局部高区、较低高点/双顶/头肩顶及距离门，横盘中部无效。原MA5/MA10共同穿越MA20并形成三均线发散称大金叉/大死叉，作为更晚趋势确认。已经通过各自方向、结构、位置和时效条件的局部反转、趋势启动、趋势中续、回踩/反抽及突破追单，若结构高低点止损超过3点，则改为以下单时最新成交参考价为基准，做多下方3点、做空上方3点设置服务器标记价止损，并按3点实际风险重新核验利润空间；普通横盘中部、方向未确认及入场信号本身不成立时不得使用该兜底。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V092_SMALL_CROSS}\n{rule['trigger']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V093_REVIEW_SEQUENCE = "【v0.7.68策略v93｜回踩完成与多周期走弱中续】强拉升后首次回踩只进入观察；若最新已收盘15分钟阴线实体覆盖前一根阳线，暂停突破回踩追多，等待下一根15分钟收盘重新确认。做空新增组合中续：5分钟阴线覆盖阳线，后续连续两根阴线并形成MA5/MA10小死叉；同时1分钟先出现小死叉、随后MA5<MA10<MA20向下发散形成大死叉，且价格距5分钟MA20不超过0.90 ATR，才允许中续追空。普通横盘中部的小死叉仍只记录，不得单独下单。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V093_REVIEW_SEQUENCE}\n{rule['trigger']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V094_ENTRY_AND_TAKEOVER = "【v0.7.69策略v94｜一分钟入场与逐级MA5接管】所有趋势启动区和反转区只允许在一分钟三阶段内的最佳有效位置下单。五分钟小交叉只记录/确认，不独立开仓；一分钟大金叉/大死叉只把已有仓位升级为五分钟MA5止盈，五分钟大交叉继续升级为十五分钟MA5止盈，十五分钟大交叉继续升级为一小时MA5止盈。没有一分钟先行仓时，高周期交叉只记录、禁止补追。删除“错过三个阶段立即追涨/追空”、五分钟主门独立开仓和高周期排列直接开仓。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V094_ENTRY_AND_TAKEOVER}\n{rule['trigger']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V095_ONE_MINUTE_LAUNCH = "【v0.7.70策略v95｜一分钟启动防漏】激进型三阶段启动/反转信号只要已经通过一分钟局部下部38%做多或上部38%做空位置门，即由一分钟独立授权先行单，不再等待五分钟主触发二次批准。五分钟仅确认加分并负责后续止盈升级。该最佳位置先行单不再被旧五分钟实体压力按普通1.5R机械拒绝；保留信号自身结构止损，结构止损超过3点时按下单位置固定3点保护。横盘中部交叉、位置不合格、晚到大交叉和普通追单均不得使用本例外。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V095_ONE_MINUTE_LAUNCH}\n{rule['trigger']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V096_FROZEN_BIG_CROSS = "【v0.7.75策略v100｜提前、再提前】冻结阶段同时也是正式下单机会：局部底部/扫损阳线收回立即做多；若未成交，价格站上MA5且MA5向上弯曲再次做多；仍未成交，MA5上穿MA10小金叉再次做多。MA5上穿MA20和完整大金叉只作后续兜底；顶部完全镜像。已有同向仓位/订单时不重复加仓。每一步使用最近一分钟局部结构外真实止损，实际风险1点或1.5点就原样使用；只有结构距离超过3点时才改用下单价固定3点，3点是上限而不是最低止损。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V096_FROZEN_BIG_CROSS}\n{rule['trigger']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V101_INDEPENDENT_HEDGE = "【v0.7.76策略v101｜多空双向独立】每一轮20秒扫描同时维护做多与做空两套候选、冻结和触发状态；底部多头成立不暂停顶部空头识别，顶部空头成立也不暂停底部多头识别。只禁止同方向重复仓位/订单，反方向有效信号仍可独立下单，允许OKX双向持仓模式下多空同时持仓。若同一轮双向同时满足，信号分别保留并逐笔安全提交，不允许一个方向覆盖或清除另一方向。多空同时持仓后，各自独立执行MA5逐级止盈与服务器结构止损。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V101_INDEPENDENT_HEDGE}\n{rule['trigger']}",
     "take_profit": f"【v0.7.76｜双持仓独立退出】多仓与空仓逐边检查、逐边止盈；任一方向持仓不得使另一方向跳过退出管理。\n{rule['take_profit']}",
     "limits": f"【v0.7.76｜对冲门禁】只拦截同方向重复开仓；反方向持仓、保护单或生命周期记录不构成阻止。\n{rule['limits']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V102_REMOVE_38_GATE = "【v0.7.77策略v102｜取消38%否决门】一分钟有效的局部顶底、扫损收回/拒绝、半覆盖、价格穿越MA5、小金叉/小死叉、MA5穿越MA20及完整大交叉，均按自身形态、方向和局部结构止损独立触发；动态区间位置只记录，不得再以做多不在下部38%或做空不在上部38%否决。前置形态出现后，后续阶段离开初始顶底属于正常启动，不得因价格已运行而漏单。顶部与底部完全镜像。账户实时持仓/委托仍是同方向重复门；旧策略版本遗留的open生命周期不再单独阻止新版本订单。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V102_REMOVE_38_GATE}\n{rule['trigger']}",
     "limits": f"【v0.7.77｜位置仅审计】取消一分钟下部/上部38%硬门；仍保留形态有效性、同向去重、结构止损、账户保护和服务器回执检查。\n{rule['limits']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V103_STRICT_EARLY_PROBE = "【v0.7.78策略v103｜MA5新鲜转向前置抢单】局部底部/顶部、单独扫损和单独半覆盖只建立冻结，不再直接下单。最早抢多只要求价格位于MA5上方，并且MA5由下降转为走平或向上拐；不强制阳线精确覆盖前阴线。抢空完全镜像为价格位于MA5下方、MA5由上升转为走平或向下拐。必须是新鲜站位或新鲜拐头，禁止价格长期在MA5同侧时每轮重复下单。该前置单真实止损最多1.5点，首个目标至少3点。小金叉/小死叉、MA5穿越MA20及大交叉仍是后续独立补救，但不得在已远离冻结价后追单。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V103_STRICT_EARLY_PROBE}\n{rule['trigger']}",
     "take_profit": f"【v0.7.78｜前置单保护】最早组合反转单止损不超过1.5点，首个目标至少3点；其余确认阶段继续使用原逐级MA5管理。\n{rule['take_profit']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V104_LATEST_STRUCTURE_PRIORITY = "【v0.7.79策略v104｜最新局部结构优先】冻结和大交叉只是漏单补救，不得压过最新一分钟局部结构。最新局部高点转弱、顶部覆盖/扫损拒绝、价格跌到MA5下方或一分钟方向转空时，旧多头冻结及滞后大金叉立即失去下单资格，优先执行有效抢空；底部反转完全镜像。该规则不关闭多空双向判断，只阻止旧方向信号覆盖新结构。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V104_LATEST_STRUCTURE_PRIORITY}\n{rule['trigger']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V105_UNLOCK_PROFIT_EXIT = "【v0.7.80策略v105｜解除MA5锚点止盈锁】退役v0.7.62的入场前MA5局部峰谷解锁门。新旧持仓均不再因为MA5尚未重新触达旧锚点而禁止止盈；一分钟、五分钟或升级接管周期的原有效止盈条件一旦成立即可立即平仓，接受较早止盈，避免利润回吐到服务器止损。"
STRATEGY_RULES = tuple(
    {**rule, "take_profit": f"{_V105_UNLOCK_PROFIT_EXIT}\n{rule['take_profit']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V106_TRADE_QUALITY = "【v0.7.81策略v106｜交易质量门】MA5走平/拐头和小交叉只有在同方向局部顶底、扫损或半覆盖冻结之后，才有前置下单资格；只允许冻结后的第一次有效转向。价格距冻结点超过1.75倍一分钟ATR、距MA5超过1倍一分钟ATR，或前方局部压力/支撑不足3.5点时禁止高位追多、低位追空。趋势延续必须走原回踩MA5分支；MA5/MA20与完整大交叉仅补漏，不重新追高追低。所有新单预计毛空间至少3.5点，按1点手续费和滑点估算后净空间至少1点；止盈锁继续保持解除。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V106_TRADE_QUALITY}\n{rule['trigger']}"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V107_INDICATOR_CONFIRMATION = "【v0.7.82策略v107｜多周期技术指标确认门】浏览器图表只用于人工对照，自动决策固定从OKX已收盘1m/5m/15m K线独立计算MA5/10/20、成交量、MACD(12,26,9)和Wilder RSI(6/12/24)。指标不得单独创造订单，只能在现有形态、结构止损、距离门和利润空间全部通过后作最终确认。反转要求1m至少2/3同向且15m不得三项全反向；趋势延续要求三周期各至少1票且合计至少6/9；普通候选要求1m至少2票、5m至少1票且合计至少5/9。一分钟已收盘成交量低于前20根中位数0.60倍拒绝；追多RSI6≥78或追空RSI6≤22拒绝。每次通过/拒绝均保存三周期指标快照。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V107_INDICATOR_CONFIRMATION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v107"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V108_MARKET_CONTEXT = "【v0.7.83策略v108｜八小时复盘衔接与多维市场上下文】运行时只从OKX已确认1m/5m/15m K线独立重算结构、趋势、动量、成交量、RSI、MACD、Supertrend、支撑压力、BOS、流动性扫损收回及FVG兼容原语；不声称与Smart Money Concepts [LuxAlgo]专有算法完全一致。以上指标不得创造候选，仅当1m与5m的Supertrend、确认价格结构和MACD零轴区域三项同时反向时辅助否决。Edge五分钟快照固定为只读离线研究样本，decision_eligible=false，不进入下单调用链。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V108_MARKET_CONTEXT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v108"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V109_LUXALGO_RESEARCH = "【v0.7.84策略v109｜LuxAlgo/PAC研究证据层】OKX已确认K线新增CHoCH、EQH/EQL、Premium/Discount区、Chaikin Money Flow和归一化动量；继续只作既有候选辅助。LuxAlgo/TradingView Webhook必须通过密钥、品种、事件、时区、时效和重复校验后才能写入外部研究表，且decision_eligible固定为false。每根5m连续样本在未来6根5m K线完整后自动生成前瞻收益、最大有利和最大不利波动标签；标签仅供离线统计，运行时决策不读取。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V109_LUXALGO_RESEARCH}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v109"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V110_SNAPSHOT_UI = "【v0.7.85策略v110｜五分钟快照明细】主界面新增只读快照列表，按本地五分钟节点合并OKX自动研究样本、Edge浏览器旁路快照和未来30分钟标签；明确显示Edge有效或缺失，不以OKX样本冒充浏览器样本。该窗口不提供下单控件、不修改数据库，Edge及标签继续固定为decision_eligible=false，不改变真实下单阈值。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V110_SNAPSHOT_UI}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v110"}
    if rule["strategy"].startswith("策略01") else rule
    for rule in STRATEGY_RULES
)

_V111_EARLY_REVERSAL_RUNWAY = "【v0.7.89策略v111｜早期反转盈利空间硬门槛】双周期MA5早期反转及三连阴MA20回落入口，做空按最近五分钟实体支撑、做多按最近五分钟实体压力核算；剩余空间必须同时不少于1.2倍止损风险和0.8倍五分钟ATR，否则只记录观察、不提交订单。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V111_EARLY_REVERSAL_RUNWAY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v111"}
    if rule["version"] == "demo-frequency-validation-v110" else rule
    for rule in STRATEGY_RULES
)

_V112_PRICE_REVERSAL_AND_RECOVERY_RUNWAY = "【v0.7.90策略v112｜顶部价格反转与恢复单防追高】顶部走弱/均线发散顶部专属分支在一分钟高位拒绝且五分钟CHoCH向下时，允许已确认价格结构覆盖滞后的超级趋势和MACD零轴冲突；仍须通过最近支撑盈利空间门槛。十五分钟恢复多必须通过最近五分钟实体压力门槛，禁止在压力附近追高。做多价格结构覆盖采用低位收回与五分钟CHoCH向上的镜像条件。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V112_PRICE_REVERSAL_AND_RECOVERY_RUNWAY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v112"}
    if rule["version"] == "demo-frequency-validation-v111" else rule
    for rule in STRATEGY_RULES
)

_V113_PINETS_LIVE_CONSENSUS = "【v0.7.97策略v113｜PineTS双角色实盘共识】原策略无候选时，4项可数LuxAlgo指标至少3票同向可独立建立候选；原策略有候选时，仅PineTS强反向共识否决，同向、中性或运行不可用均放行。PineTS候选不得绕过0.05张、同侧去重、每日限额、收益空间及OKX服务器标记价止损。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V113_PINETS_LIVE_CONSENSUS}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v117"}
    if rule["version"] == "demo-frequency-validation-v112" else rule
    for rule in STRATEGY_RULES
)

_V118_REVERSAL_CONTEXT = (
    "【v0.7.103策略v118｜双周期均线形态分类】真正顶部反转要求转向前一分钟、五分钟均出现"
    "MA5>MA10>MA20完整多头排列，真正底部反转采用MA5<MA10<MA20镜像排列；一分钟第一阶段扫顶/扫底后，"
    "第二阶段价格穿越MA5即可独立执行，第三阶段MA5/MA10交叉只负责补漏。五分钟反向实体覆盖前一根实体过半"
    "作为超前增强证据，不等待五分钟变色，也不阻挡一分钟。若双周期不满足旧趋势同侧完整排列，则按下跌趋势"
    "中继反抽做空或上涨趋势中继回踩做多分类，避免误写成真正扫顶/扫底反转。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V118_REVERSAL_CONTEXT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v118"}
    if rule["version"] == "demo-frequency-validation-v117" else rule
    for rule in STRATEGY_RULES
)

_V119_LATCHED_STAGE_TWO = (
    "【v0.7.104策略v119｜反转第二阶段锁存】一分钟扫顶/扫底第一阶段与价格穿越MA5第二阶段一旦成立即持久锁存；"
    "随后少量反向小K线只视为回抽干扰，不清空原候选。候选在30分钟有效期内重新沿原方向收线且回到MA5同侧时再次激活，"
    "仅在超时、相反结构成立、已成交或既有订单与持仓去重时停止执行。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V119_LATCHED_STAGE_TWO}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v119"}
    if rule["version"] == "demo-frequency-validation-v118" else rule
    for rule in STRATEGY_RULES
)

_V120_COMPLETE_ONE_MINUTE_FRAME = (
    "【v0.7.105策略v120｜完整一分钟质量门】反转质量门与第二阶段锁存恢复统一使用已收盘一分钟历史K线加当前实时K线；"
    "同一时间戳由实时K线覆盖，禁止因只传入单根实时K线而误报数据不足、吞掉第二或第三阶段订单。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V120_COMPLETE_ONE_MINUTE_FRAME}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v120"}
    if rule["version"] == "demo-frequency-validation-v119" else rule
    for rule in STRATEGY_RULES
)

_V121_LATE_CONTINUATION_AND_POST_ENTRY_EXIT = (
    "【v0.7.106策略v121｜末段禁追与新仓退出隔离】上涨中继做多必须仍处于最近30根一分钟K线的回踩区域，"
    "高于68%位置禁止趋势末段追多；下跌中继做空完全镜像，真正顶部/底部反转不套用普通中继位置门。"
    "十五分钟和一小时均线趋势止盈只接受开仓后新形成的K线拐点，禁止新仓继承入场前已经存在的退出状态。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V121_LATE_CONTINUATION_AND_POST_ENTRY_EXIT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v121"}
    if rule["version"] == "demo-frequency-validation-v120" else rule
    for rule in STRATEGY_RULES
)

_V122_STRUCTURE_OUTSIDE_STOP = (
    "【v0.7.107策略v122｜结构外止损】反转先行单取消1.5点止损硬封顶；止损必须位于最近12根一分钟K线有效结构高低点之外，"
    "并增加至少0.15倍一分钟ATR波动缓冲。若结构外止损距离超过3点最大风险则放弃入场，禁止为了成交把止损压回结构内部。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V122_STRUCTURE_OUTSIDE_STOP}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v122"}
    if rule["version"] == "demo-frequency-validation-v121" else rule
    for rule in STRATEGY_RULES
)

_V123_DIRECTION_SAFE_PATTERN_AUDIT = (
    "【v0.7.108策略v123｜顺势优先与反转区订单方向强一致】一分钟、五分钟同向上涨时优先回踩追多，同向下跌时优先反抽追空；"
    "普通逆势阶段禁止抢单，只有明确扫损收回且五分钟实体确认的强反转才能逆势启动。反转区只有在自身方向与实际提交订单方向一致时才结算为已下单；"
    "订单回写保存订单方向与提交时间，复查窗口反查生命周期并标记历史异向误关联。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V123_DIRECTION_SAFE_PATTERN_AUDIT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v123"}
    if rule["version"] == "demo-frequency-validation-v122" else rule
    for rule in STRATEGY_RULES
)

_V124_STAGED_CONTINUATION_OWNS_ENTRY = (
    "【v0.7.109策略v124｜顺势三阶段自主执行】取消最近30根一分钟K线68%固定位置否决；位置百分比只保留审计，不再覆盖反转区/三阶段自身下单规则。"
    "一分钟与五分钟同向的回踩追多或反抽追空，结构止损允许超过3点并通过仓位风险缩减控制；止盈同步扩展到至少1.2R且不少于4点，避免结构止损放宽后盈亏比不足。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V124_STAGED_CONTINUATION_OWNS_ENTRY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v124"}
    if rule["version"] == "demo-frequency-validation-v123" else rule
    for rule in STRATEGY_RULES
)

_V125_EARLY_CONFIRMED_TOP_BOTTOM = (
    "【v0.7.110策略v125｜顶部底部确认反转提前执行】五分钟高位阴线覆盖前阳线一半以上并被分类为真正顶部反转时，"
    "由一分钟跌破MA5、转弱或小死叉阶段执行，不等待五分钟MA5斜率走平/下弯；底部完全镜像。"
    "确认反转不再套普通逆势3点门，止损改放最近12根一分钟真实极值外并增加ATR缓冲，目标至少1.2R。"
    "五分钟顺势明确时，一分钟在回踩/反抽过程中允许短暂反向，重新穿MA5后仍按顺势中继处理。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V125_EARLY_CONFIRMED_TOP_BOTTOM}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v125"}
    if rule["version"] == "demo-frequency-validation-v124" else rule
    for rule in STRATEGY_RULES
)

_V126_FRESH_STAGE_RISK_GATE = (
    "【v0.7.111策略v126｜三阶段末端追单统一治理】一分钟第二/第三阶段仅在触发后90秒内有效；超过窗口视为价格和局部结构已经换段，禁止用旧冻结信号补追。"
    "三阶段止损仍放结构外，不把止损压小；但止损超过2倍一分钟ATR且已是最小1张、无法继续缩仓时直接放弃。"
    "多空完全镜像，不恢复38%或68%固定位置否决；顺势单仍由自身回踩/反抽规则决定。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V126_FRESH_STAGE_RISK_GATE}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v126"}
    if rule["version"] == "demo-frequency-validation-v125" else rule
    for rule in STRATEGY_RULES
)

_V127_WRAPPED_GET_RECOVERY = (
    "【v0.7.112策略v127｜只读GET包装异常自动恢复】OKX行情/签名只读GET超时即使被底层网络库或执行器包装为非OkxError，"
    "仍按安全网络故障无限退避并保持自动总闸运行；恢复后先重做账户、持仓、委托与保护审计。"
    "任何POST结果不明、保护失败或账户异常仍立即故障停止，不允许盲目重试。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V127_WRAPPED_GET_RECOVERY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v127"}
    if rule["version"] == "demo-frequency-validation-v126" else rule
    for rule in STRATEGY_RULES
)

_V128_REVIEW_RETENTION = (
    "【v0.7.113策略v128｜反转区复查只保留最新30条】复查窗口每次打开或刷新时自动删除第31条以后的旧反转区列表记录，"
    "只加载最新30条；订单、成交、交易生命周期和亏损审计不删除。窗口同步显示最新扫描、最新原始阶段和最新反转区时间，"
    "明确区分市场暂时没有新反转区与程序停止更新。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V128_REVIEW_RETENTION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v128"}
    if rule["version"] == "demo-frequency-validation-v127" else rule
    for rule in STRATEGY_RULES
)

_V129_MISSED_MA5_PULLBACK_LIMIT = (
    "【v0.7.114策略v129｜错过MA5市价窗口后短时限价等待】第一阶段仍只冻结局部底部/顶部，不直接成交；"
    "第二阶段盘中刚站回MA5且仍贴近MA5时继续立即市价成交。若第二阶段本身有效、仅因网络或扫描延迟已远离MA5，"
    "禁止市价追高/追空，改在当时MA5附近挂一次post-only回踩限价，附带原结构外标记价止损和至少1.2R且不少于4点目标；"
    "120秒内不回踩即自动撤销，同向已有仓位或挂单不重复，多空完全镜像。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V129_MISSED_MA5_PULLBACK_LIMIT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v129"}
    if rule["version"] == "demo-frequency-validation-v128" else rule
    for rule in STRATEGY_RULES
)

_V130_OPPOSING_ANCHOR_ARBITRATION = (
    "【v0.7.115策略v130｜同分钟反向极值优先仲裁】第二阶段MA5触发所在分钟若已经形成反向确认顶部/底部或扫损锚点，"
    "立即作废正在抢跑的旧方向，不允许上涨中继多单穿过新顶部、也不允许下降中继空单穿过新底部。"
    "反向锚点只阻断同分钟或更早触发，不阻断随后新一分钟已经明确分开的对应方向；多空镜像。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V130_OPPOSING_ANCHOR_ARBITRATION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v130"}
    if rule["version"] == "demo-frequency-validation-v129" else rule
    for rule in STRATEGY_RULES
)

_V131_DUAL_STAGE_ARBITRATION = (
    "[v0.7.116 strategy v131 | dual-stage direction arbitration] A single MA5 trigger is still "
    "blocked by an opposite same-minute local extreme. When two distinct same-direction stage-2 "
    "confirmations form on that minute, the direction is treated as resolved and may override one "
    "transient opposite local label; long and short are mirrored."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V131_DUAL_STAGE_ARBITRATION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v131"}
    if rule["version"] == "demo-frequency-validation-v130" else rule
    for rule in STRATEGY_RULES
)

_V132_CONFIRMED_TURN_OVERRIDE = (
    "[v0.7.117 strategy v132 | confirmed-turn trend override] After a confirmed local top/bottom "
    "or matching sweep anchor, two distinct same-time stage-2 confirmations may enter the fresh "
    "reversal before lagging 1m/5m trend labels flip. A single countertrend trigger remains blocked."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V132_CONFIRMED_TURN_OVERRIDE}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v132"}
    if rule["version"] == "demo-frequency-validation-v131" else rule
    for rule in STRATEGY_RULES
)

_V133_CURRENT_EXTREME_AND_STOP_CAP = (
    "[v0.7.118 strategy v133 | current-extreme ownership and stop cap] A relative top/bottom "
    "anchor must be reached by the current candle itself; an extreme from either prior candle "
    "cannot be re-timestamped as current. Confirmed reversal probes with structural risk above "
    "3 points now receive the documented fixed 3-point launch stop."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V133_CURRENT_EXTREME_AND_STOP_CAP}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v133"}
    if rule["version"] == "demo-frequency-validation-v132" else rule
    for rule in STRATEGY_RULES
)

_V134_INTRABAR_FIVE_MINUTE_TOP_CONFIRM = (
    "[v0.7.119 strategy v134 | intrabar five-minute top confirmation] After a one-minute "
    "top anchor and stage-2 short trigger, a still-open five-minute bearish candle that has "
    "covered at least half of the prior bullish body confirms the reversal immediately. When "
    "the frozen top is above an intact MA5>MA10>MA20 stack, the bullish trend labels describe "
    "the completed rise into the top and no longer veto the short; no five-minute close wait."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V134_INTRABAR_FIVE_MINUTE_TOP_CONFIRM}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v134"}
    if rule["version"] == "demo-frequency-validation-v133" else rule
    for rule in STRATEGY_RULES
)

_V135_LOCAL_TOP_AND_STAGE3_MICRO_STOP = (
    "[v0.7.120 strategy v135 | local-top half-cover and stage-3 recovery] A frozen "
    "one-minute top only needs to be above MA5, MA10 and MA20; the averages need not "
    "remain in strict bullish order while rolling over. A live five-minute bearish "
    "half-body cover confirms that local top without requiring proximity to the "
    "24-candle absolute high. If stage 2 was missed, stage 3 first uses the fresh "
    "four-bar micro swing and then the accepted three-point launch protection."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V135_LOCAL_TOP_AND_STAGE3_MICRO_STOP}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v135"}
    if rule["version"] == "demo-frequency-validation-v134" else rule
    for rule in STRATEGY_RULES
)

_V136_INTRABAR_FIVE_MINUTE_BOTTOM_CONFIRM = (
    "[v0.7.121 strategy v136 | mirrored intrabar bottom confirmation] After a "
    "one-minute bottom anchor and stage-2 long trigger, a live five-minute bullish "
    "candle covering at least half of the prior bearish body confirms the local "
    "bottom immediately. When the frozen low is below MA5, MA10 and MA20, lagging "
    "bearish trend labels cannot veto the long. This mirrors the top-short rule."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V136_INTRABAR_FIVE_MINUTE_BOTTOM_CONFIRM}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v136"}
    if rule["version"] == "demo-frequency-validation-v135" else rule
    for rule in STRATEGY_RULES
)

_V137_MATURE_THREE_TIMEFRAME_TOP = (
    "[v0.7.122 strategy v137 | mature three-timeframe top half-cover] A short "
    "can bypass MA5 chase/pullback timing only after the one-minute top chain "
    "is complete, the 1m/5m/15m highs are in their recent highest zones above "
    "already-expanded bullish MA5/MA10/MA20 stacks, and the live 5m bearish "
    "candle covers at least half of the prior bullish body. Newly started MA "
    "stacks never qualify. Existing stop, deduplication and account guards remain."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V137_MATURE_THREE_TIMEFRAME_TOP}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v137"}
    if rule["version"] == "demo-frequency-validation-v136" else rule
    for rule in STRATEGY_RULES
)

_V138_TWO_TIMEFRAME_EXTREMES = (
    "[v0.7.123 strategy v138 | two-timeframe extremes and downtrend arbitration] "
    "A mature 1m+5m high plus a live 5m bearish half-cover can execute the top "
    "short; 15m only upgrades strength and never vetoes it. During a confirmed "
    "5m decline, a newly confirmed 1m lower-high pullback short is executable and "
    "has priority over a countertrend long candidate. Countertrend bottom longs "
    "require both 1m and 5m to be in mature expanded lowest zones; mid-trend 1m "
    "bottom noise is rejected. Existing same-side deduplication remains active."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V138_TWO_TIMEFRAME_EXTREMES}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v138"}
    if rule["version"] == "demo-frequency-validation-v137" else rule
    for rule in STRATEGY_RULES
)

_V139_SIDEWAYS_NEUTRAL = (
    "[v0.7.124 strategy v139 | 1m+5m sideways neutral gate] Detect compressed, "
    "low-efficiency 1m+5m ranges before stale trend labels are applied. The "
    "reversal review classifies them as a neutral sideways zone. Pullback longs "
    "are blocked inside the range; a short is permitted only after an explicit "
    "MA5 breakdown or small death cross. Otherwise the zone is observation-only."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V139_SIDEWAYS_NEUTRAL}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v139"}
    if rule["version"] == "demo-frequency-validation-v138" else rule
    for rule in STRATEGY_RULES
)

_V140_SIDEWAYS_TOP_REENTRY = (
    "[v0.7.125 strategy v140 | sideways top confirmed short re-entry] After a "
    "range-top short is stopped, a fresh 5m bearish half-cover combined with a "
    "1m MA5 breakdown or small death cross may execute one new market short "
    "instead of waiting for an MA5 pullback that may never trade. Ordinary range "
    "breaks, longs, missing half-covers, duplicates and account guards are unchanged."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V140_SIDEWAYS_TOP_REENTRY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v140"}
    if rule["version"] == "demo-frequency-validation-v139" else rule
    for rule in STRATEGY_RULES
)

_V141_DOWNTREND_CONTINUATION_RANGE = (
    "[v0.7.126 strategy v141 | downtrend continuation range] A compressed, "
    "low-efficiency 1m+5m range that follows a material selloff and recent "
    "bearish MA stack is classified as a downtrend-continuation range, not a "
    "neutral range. It remains short-biased: no pullback longs, no middle-range "
    "entries, and shorts wait for a fresh MA5 breakdown/death-cross after a pullback."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V141_DOWNTREND_CONTINUATION_RANGE}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v141"}
    if rule["version"] == "demo-frequency-validation-v140" else rule
    for rule in STRATEGY_RULES
)

_V142_TWO_TIMEFRAME_MATURE_EXTREME = (
    "[v0.7.127 strategy v142 | two-timeframe mature extreme priority] A mature "
    "1m+5m MA5/MA10/MA20 spread endpoint on the same extreme side confirms the "
    "reversal stage without requiring one 5m candle to perform the entire half-"
    "cover. One or two consecutive 5m candles may cumulatively cover half. Once "
    "a mature bottom stage is confirmed, a transient opposite anchor or lagging "
    "bearish direction cannot replace it with a continuation short. The rule is "
    "mirrored for tops; 15m remains an optional strength reference."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V142_TWO_TIMEFRAME_MATURE_EXTREME}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v142"}
    if rule["version"] == "demo-frequency-validation-v141" else rule
    for rule in STRATEGY_RULES
)

_V143_REVERSAL_CLUSTER = (
    "[v0.7.128 strategy v143 | reversal cluster] A reversal zone may consist "
    "of up to six mixed bullish/bearish candles. Five-minute confirmation uses "
    "the cumulative displacement of the cluster rather than requiring adjacent "
    "same-colour candles. One-minute stage-1 and stage-2 location checks search "
    "the same six-candle mature extreme zone. Mid-range and structurally broken "
    "clusters remain ineligible."
)
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V143_REVERSAL_CLUSTER}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v143"}
    if rule["version"] == "demo-frequency-validation-v142" else rule
    for rule in STRATEGY_RULES
)

_V093_MARK_STOP = "【v0.6.93｜止损统一标记价】策略01/02/03全部市价单、结构预埋单及共享实验Demo验证单的服务器止损统一使用OKX标记价触发；止盈仍按原规则使用最新成交价。"
_V093_REVIEW_CUTOFF = "【v0.6.93｜阶段二复盘交接】交易明细、亏损复盘、共享观察和共享Demo验证分别冻结行ID、业务主键、时间与数量；下一次只统计阶段三增量，阶段二遗留未平仓仅回填原阶段。"
STRATEGY_RULES = tuple(
    {**rule, "stop_loss": f"{_V093_MARK_STOP}\n{rule['stop_loss']}",
     "limits": f"{_V093_REVIEW_CUTOFF}\n{rule['limits']}"}
    for rule in STRATEGY_RULES
)

_V094_SNIPER_DISASTER_STOP = "【v0.6.94｜结构预埋两级止损】取消最近1分钟K线入场/止损碰撞硬拦截。结构预埋始终附带标记价外层灾难止损；灾难距离取正常风险1.75倍、入场价0.60%、4倍1分钟ATR中的较大者，并受入场价1.20%与8倍1分钟ATR双重上限约束，风险超限不挂单。"
_V094_POST_FILL_CONFIRM = "【v0.6.94｜成交后收回确认】结构预埋成交后保留灾难止损，等待入场K线收盘及后两根完整1分钟K线；收回入场结构后原地收紧到正常结构止损，正常结构失效立即只减1张，状态不明最多再等两根完整1分钟K线。灾难止损不得撤销或向外放宽。"
STRATEGY_RULES = tuple(
    {**rule, "stop_loss": f"{_V094_SNIPER_DISASTER_STOP}\n{_V094_POST_FILL_CONFIRM}\n{rule['stop_loss']}"}
    for rule in STRATEGY_RULES
)

_V095_BEARISH_ACCUMULATION = "【v0.6.95｜顶部累计转弱】一分钟上涨均线明显发散并处于局部高区后，不再只观察单根阴线；随后最多4根已收盘K线内累计至少2根阴线、阴线收盘降低、累计实体达标且MA5转弱时，发布提前做空候选。同一顶部按确认K线去重。"
_V095_PULLBACK_REJECT = "【v0.6.95｜下跌局部反抽失败】一分钟MA5<MA10<MA20且向下发散、MA20下降时，局部阳线反抽形成高点后紧跟有效阴线转弱，发布续势追空候选；止损仅使用本轮局部反抽高点加ATR缓冲，不沿用远端旧针。仍执行多周期、距5m MA20、利润空间和账户安全门槛。"
_V095_EXPERIENCE = "【v0.6.95｜形态经验库】顶部累计转弱与下跌反抽失败均持久保存均线间距、阴线数量/实体、局部高点、止损距离和15根1分钟K线固定观察结果；只供离线复盘，不在运行中自动修改阈值。"
STRATEGY_RULES = tuple(
    {**rule,
     "continuation": f"{_V095_PULLBACK_REJECT}\n{rule['continuation']}",
     "trigger": f"{_V095_BEARISH_ACCUMULATION}\n{rule['trigger']}",
     "limits": f"{_V095_EXPERIENCE}\n{rule['limits']}"}
    for rule in STRATEGY_RULES
)

_V077_THREE_BEAR_SHORT = "【v0.7.7｜一分钟三阴确认转跌】激进型独立早空：三个已收盘一分钟阴线高点依次降低，首阴实体下穿MA20，后两阴收盘持续位于MA20下方；一分钟MA20走平转下且五分钟MA20未上升时，在第三根确认阴线附近小风险做空。只认最新三根，错过后不得低位补追；结构止损、利润空间及账户安全门槛保持。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V077_THREE_BEAR_SHORT}\n{rule['trigger']}"}
    if index == 0 else rule
    for index, rule in enumerate(STRATEGY_RULES)
)

_V078_FIRST_BEAR_EXIT = "【v0.7.8｜激进型首阴早空与MA5退出】不再等待三阴完成：第一根已收盘阴线实体下穿1分钟MA20，且1分钟MA20走平转下、5分钟MA20未上升时即可小风险做空，止损置于本轮局部高点上方。盈利持仓沿1分钟MA5下降持有；MA5转平/上拐，或出现实体≥1.5ATR、量≥20根均量1.8倍且收盘远离MA5≥0.75ATR的长阴衰竭线时，只减仓市价平空。三阴结构保留为加强证据标签。"
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V078_FIRST_BEAR_EXIT}\n{rule['trigger']}"}
    if index == 0 else rule
    for index, rule in enumerate(STRATEGY_RULES)
)

_V079_DOUBLE_REJECTION = "【v0.7.9｜下跌延续双局部高点】激进型独立追空：5分钟与15分钟MA20下降且价格位于MA20下方；1分钟反抽上穿MA20后出现两组阳线紧跟阴线，第二局部高点不明显突破第一高点，第二根确认阴线收盘时MA5走平向下弯即可追空。止损置于两组局部高点较高者上方；本分支距5分钟MA20最多放宽至1.50 ATR，超过仍禁止迟到追空。"
STRATEGY_RULES = tuple(
    {**rule, "continuation": f"{_V079_DOUBLE_REJECTION}\n{rule['continuation']}"}
    if index == 0 else rule
    for index, rule in enumerate(STRATEGY_RULES)
)

_V0710_LATE_CHASE_STOP = "【v0.7.12修订｜底部追空与远端止损】v0.7.10曾使用的一分钟通道25%门槛已经撤销；现在不按通道百分比过滤。普通同向单仍用最近8根一分钟K线局部高低点加缓冲作为止损，不引用五分钟远端旧高低点，并受1分钟1.5 ATR及5分钟0.8 ATR双重距离上限约束。首阴、双高点等形态继续使用各自精确结构止损。"
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{_V0710_LATE_CHASE_STOP}\n{rule['trigger']}",
     "stop_loss": f"{_V0710_LATE_CHASE_STOP}\n{rule['stop_loss']}"}
    if index == 0 else rule
    for index, rule in enumerate(STRATEGY_RULES)
)

# Older release overlays remain in source history, but their retired channel
# percentage wording must not appear as an active rule in the current table.
_RETIRED_CHANNEL_TEXT = {
    "上部25%做空、下部25%做多及": "实际结构高低点及",
    "结构下部25%": "实际结构低点区域",
    "结构上部25%": "实际结构高点区域",
    "五分钟区间下部25%": "实际五分钟低点结构",
    "区间下部25%": "实际低点结构",
    "区间上部25%": "实际高点结构",
    "下部25%": "实际低点结构",
    "上部25%": "实际高点结构",
    "下沿25%": "实际低点结构",
    "上沿25%": "实际高点结构",
    "五分钟区间边缘硬门槛": "五分钟实际结构安全门槛",
    "区间边缘硬门槛": "实际结构安全门槛",
    "区间边缘、": "实际结构、",
    "原有区间位置、": "实际结构位置、",
    "区间硬门槛": "实际结构安全门槛",
}


def _without_retired_channel_text(value: str) -> str:
    for old, new in _RETIRED_CHANNEL_TEXT.items():
        value = value.replace(old, new)
    return value


STRATEGY_RULES = tuple(
    {
        key: _without_retired_channel_text(value) if key not in {"strategy", "version"} else value
        for key, value in rule.items()
    }
    for rule in STRATEGY_RULES
)

_V725_STRUCTURE_AND_HEDGE = (
    "【v0.7.25｜5分钟结构主判】趋势以已确认5分钟摆动高低点定义：高点抬高且低点抬高为上涨；高点降低且低点降低为下跌；高低点混合、突破后回到原区间均为横盘/反转等待区。15分钟箭头仅显示风险背景，不再因与5分钟或1分钟冲突而一票否决。",
    "【v0.7.25｜结构优先】策略02继续保留区间、结构止损和利润空间硬门；5分钟高低点结构决定趋势/等待状态，1分钟MA5、MA10、MA20回踩后重新转向决定精确触发。",
    "【v0.7.25｜结构优先】策略03仍只在其各自已确认的结构机会中执行；15分钟仅作风险提示，不能机械否决5分钟结构与1分钟均线回踩确认。",
    "【v0.7.25｜双向独立持仓】OKX双向持仓模式下，已有受保护多仓可接受独立空头结构/反转候选，已有受保护空仓可接受独立多头候选；同方向已有持仓或委托仍禁止无依据重复叠加，止损、利润空间、冷却和账户安全闸保持。",
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{structure_rule}\n{rule['trigger']}"}
    for rule, structure_rule in zip(STRATEGY_RULES, _V725_STRUCTURE_AND_HEDGE)
)

_V726_TOP_WEAKENING = (
    "【v0.7.26｜顶部走弱MA5快空】仅激进型独立并行：一分钟冲顶后两组局部高点至少降低0.05 ATR、MA20三根斜率≤0.25 ATR且最近6根有十字整理时，当前阴线盘中或收盘有效跌破MA5即可小风险做空；不等待MA20完全下行。距MA5超过0.90 ATR拒绝迟到追空，止损放最近12根结构最高点外。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{top_weakening}\n{rule['trigger']}"}
    for rule, top_weakening in zip(STRATEGY_RULES, _V726_TOP_WEAKENING)
)

_V732_TREND_BASE_AND_BOTTOM_STAGES = (
    "【v0.7.32｜六周期基础仓＋底部三阶段提速】4小时、1小时、30分钟、15分钟、5分钟、1分钟全部同向且当前无同向仓位时，允许建立一张基础顺势仓，但不得绕过MA20距离、结构止损、利润空间、冷却和账户安全。激进型底部第一阶段严格扫低收回可小风险执行；第二阶段盘中同时收复MA5/MA10且仍在MA20下方；第三阶段包含MA20首次有效突破及其后第一次回踩，两次独立接力。旧支撑若已被跌破，不机械补挂在失效价位。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V732_TREND_BASE_AND_BOTTOM_STAGES)
)

_V735_FIVE_MINUTE_PRIMARY = (
    "【v0.7.35｜五分钟唯一主触发】所有实盘开仓必须先由已收盘5分钟K线完成局部顶部/反抽高点转弱或局部底部/回踩低点转强；1分钟只辅助验证、优化成交和提供局部缓冲，不得独立授权。15分钟/1小时按确认高低点执行方案A：至少一个同向，另一个只能同向或震荡；任何一个明确反向即拒绝。4小时只作背景，30分钟周期已从本轮策略读取和方向判断移除。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V735_FIVE_MINUTE_PRIMARY)
)

_V736_OPTION_C = (
    "【v0.7.36｜方案C高周期背景】五分钟已收盘主触发仍是唯一开仓硬门；15分钟和1小时完整高低点方向、局部高点降低/低点抬高及MA20走弱只作背景说明，不再机械拦截五分钟早期顶底。1分钟仍不得独立授权。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V736_OPTION_C)
)

_V737_INTRABAR_FIVE_MINUTE = (
    "【v0.7.37｜五分钟盘中快触发】不再一律等待五分钟收盘。顶部走弱证据已由前面已收盘五分钟K线建立后，当前未收盘五分钟阴线盘中覆盖前阳线至少一半、有效下穿动态MA5，并由当前一分钟阴线同步确认时可立即做空；止损仍在五分钟局部顶部外。做多镜像。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V737_INTRABAR_FIVE_MINUTE)
)

_V738_MA20_PULLBACK_AND_15M_HOLD = (
    "【v0.7.38｜五分钟MA20反抽与十五分钟持仓】下跌趋势中，五分钟MA5位于下滑MA20下方，当前五分钟反抽MA20后盘中重新跌破，并由一分钟局部高点转弱确认时可顺势做空；做多镜像。十五分钟MA5连续沿持仓方向运行后接管持仓，普通一分钟/五分钟反抽不再提前止盈，等十五分钟MA5走平或反向拐头正常退出。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V738_MA20_PULLBACK_AND_15M_HOLD)
)

_V739_LOWER_HIGH_CONTINUATION = (
    "【v0.7.39｜较低反抽高点漏单修复】五分钟原下降结构未破坏且反抽形成较低高点时，不再要求MA5必须已经低于MA20；若MA5暂时位于MA20上方，则由当前十五分钟长阴覆盖前阳线一半并盘中下穿动态MA20加强确认，再结合一分钟局部顶部转弱做空。一小时完整上涨结构未翻转但顶部已弱化时显示顶部弱化↘，只作背景。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V739_LOWER_HIGH_CONTINUATION)
)

_V740_HIGHER_LOW_EARLY_LONG = (
    "【v0.7.40｜五分钟抬高低点早多】最近两组五分钟局部低点抬高，当前未收盘阳线有效上穿动态MA5时形成主触发；一分钟已经站上MA20、完成一次有效回踩不破并重新转强即可确认，不等待第二次回踩，也不等待五分钟收盘。十五分钟早期反转背景不机械拦截。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V740_HIGHER_LOW_EARLY_LONG)
)

_V741_UPTREND_PULLBACK_RESUME_LONG = (
    "【v0.7.41｜上涨趋势回踩续涨】五分钟和十五分钟MA5>MA10>MA20、五分钟MA20上升且前段已有推动后，最近五分钟回踩MA5/MA10带但收盘未有效破坏MA10；当前未收盘五分钟阳线重新站上动态MA5，并由一分钟同步转强即可做多。不要求一分钟再次回踩MA20，不等待五分钟收盘；离动态MA5超过0.45 ATR禁止迟到追涨。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V741_UPTREND_PULLBACK_RESUME_LONG)
)

_V742_EARLY_LONG_MA20_HOLD = (
    "【v0.7.42｜底部反转MA20持续站稳】五分钟最近两组低点抬高、当前未收盘阳线有效上穿动态MA5时，一分钟突破MA20后连续两根已收盘K线和当前K线持续运行在MA20上方并重新转强即可确认；回踩MA20守住仅为加分证据，不再是硬门槛。五分钟仍是唯一开仓主触发。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V742_EARLY_LONG_MA20_HOLD)
)

_V743_SELECTED_OKX_DOMAIN = (
    "【v0.7.43｜指定欧易访问域名】本部署的公开行情、只读审计、下单及保护单REST请求固定使用用户明确指定并已通过V5公共接口验证的https://www.tpouxyihas.com；K线和API管理按钮使用同一域名。签名请求不自动跟随到其他主机。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{new_rule}\n{rule['limits']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V743_SELECTED_OKX_DOMAIN)
)

_V744_STABLE_NETWORK_REVIEW = (
    "【v0.7.44｜五分钟主触发与稳定网络分界】五分钟继续作为唯一开仓主触发：已收盘结构确认，或前序顶底证据成立后当前五分钟盘中穿越动态MA5/MA20快触发；一分钟只同步确认。下降趋势反抽MA20转弱做空、上涨趋势回踩MA5/MA10转强做多、底部抬高低点上穿MA5且一分钟持续站稳MA20早多均纳入同一五分钟框架。2026-08-27 18:45:13以前的掉线成交、亏损和共享实验仅作低可信历史归档；以后完整数据独立累计，旧样本不得直接推动自动升级。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V744_STABLE_NETWORK_REVIEW)
)

_V745_LOCAL_SHAPE_STOPS = (
    "【v0.7.45｜固定根数规则退役与局部高点校正】取消普通入场使用最近8根一分钟/五分钟极值扩大止损，也取消最近8根中6根位于MA20同侧的趋势兜底判断；统一使用最近一组确认局部形态、突破/反转以及MA5/MA20位置。做空局部高点必须反抽触及或站上一分钟MA20；若五分钟MA5位于MA20下方，则反抽必须到达五分钟MA20压力区。完全位于MA20下方的MA5小波动不属于局部高点，不得因跌破MA5追空。形态成立后止损只放本轮局部高点外，盈利空单顺着MA5下滑持有并在MA5走平/上拐时止盈。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}",
     "stop_loss": f"【v0.7.45｜本轮局部结构止损】形态专属止损不再被固定8根远端极值扩宽。\n{rule['stop_loss']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V745_LOCAL_SHAPE_STOPS)
)

_V746_BOTTOM_STAGE_TWO = (
    "【v0.7.46｜底部第二阶段放宽】五分钟底部阳线收盘上穿MA5后即可作为主触发，不再等待MA10；一分钟刚刚有效突破MA20就立即小风险做多，不等待回踩，也不等待MA5/MA10/MA20发散。单根一分钟阳线有效站上MA5即可确认第二阶段；若首根力度较小或轮询错过，则允许连续两根阳线合力形成V形，第二根到达MA10时继续确认。一分钟双底和五分钟双底均为独立有效底部证据：第二底允许近似相等或轻微抬高，不能有效跌破第一底，两个低点之间必须出现清晰反弹。阳线反包只是独立加分形态，不是总门槛；扫底、双底、单阳MA5、两阳V形、MA20突破及首次回踩全部并行，任何新增规则不得覆盖其他反转分支。原双周期均线发散追多继续作为更晚的补追独立保留：前面入口错过后，一分钟和五分钟MA5>MA10>MA20同步向上倾斜并扩大、且距五分钟MA20不超过2 ATR，再立即追多。止损放本轮五分钟低点与一分钟确认低点下方的小缓冲。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V746_BOTTOM_STAGE_TWO)
)

_V747_TOP_STAGNATION = (
    "【v0.7.47｜双顶/头肩顶MA5弯曲早空】一分钟与五分钟顶部停滞时，双顶和头肩顶均作为独立顶部反转证据并与原较低高点、吞没、MA20快空等分支并行。局部高点必须触及或站上一分钟MA20；双顶两峰允许近似相等，头肩顶要求中间头部高于左右肩且两肩近似。MA20已走弱（下降、走平或仅轻微上升≤0.12 ATR）后，阴线有效下穿MA5且MA5同步向下弯曲即可立即小风险做空，不等待跌破MA20。止损只放本轮双顶或头肩顶最高点上方的小缓冲。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V747_TOP_STAGNATION)
)

_V748_BOLD_COMPACT_STOP = (
    "【v0.7.48｜顶部小止损勇敢早空】双顶、头肩顶或较低高点已经确认，一分钟MA20走弱、阴线下穿MA5且MA5向下弯曲时，该顶部停滞分支独立取得执行权；五分钟MA20仍快速上升只作风险背景，不再重复拒绝。头肩顶/双顶结构搜索扩展至最近60根一分钟K线，并使用间隔明确的主要峰值，避免18分钟窗口和微小毛刺漏判。形态止损仍紧贴本轮最高点外；信号时效按独立一分钟确认计算，普通五分钟已收盘信号允许最长12分钟以纠正从K线开盘时间计算造成的误过期。账户安全、同向持仓、冷却、行情新鲜度和服务器保护单硬门槛继续保留。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V748_BOLD_COMPACT_STOP)
)

_V749_BEARISH_START_AUTHORITY = (
    "【v0.7.49｜下跌启动抢先空】五分钟反抽MA20上穿失败或顶部降低形成看跌反转候选后，只要已收盘一分钟长阴有效下穿一分钟MA20并跌破短结构，即视为下跌启动确认，使用该段一分钟局部高点外的小止损立即市价做空。该分支与双顶、头肩顶、顶部颜色反转共同取得独立执行权；五分钟MA20仍快速上升只作背景提示，不再重复拒绝。不得等到下跌趋势完全形成后再追空；确认后已经运行超过原迟到限制且没有反抽重置时仍取消追单。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V749_BEARISH_START_AUTHORITY)
)

_V750_ALL_COMPACT_TOP_SHORTS = (
    "【v0.7.50｜所有顶部小止损早空统一独立执行】激进型顶部扩张衰竭、双顶/头肩顶/较低高点MA5转弱、局部顶部盘中阴线吞没、横盘高点十字星后盘中破MA5、盘中破MA20、顶部颜色反转/长阴破短结构以及扫高收回，只要各自专属分支已经确认，就立即使用本轮局部高点外小止损做空。统一跳过普通五分钟MA20斜率和高周期同向趋势的二次否决，避免在盘整顶部磨到瀑布下跌后再追空；普通趋势追空仍保留原门槛，专属分支自身的形态确认、迟到限制、账户/持仓/冷却/实时行情/服务器保护继续保留。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V750_ALL_COMPACT_TOP_SHORTS)
)

_V751_FRESH_LOCAL_STOP = (
    "【v0.7.51｜最新局部高点止损与瀑布预留】激进型顶部小止损早空的止损只取触发前最近10根一分钟K线的最新局部高点外缓冲，不再使用30—60分钟前头肩顶头部或远端旧高点扩大风险。专属顶部早空确认后，最近五分钟支撑只作可能的第一反应位，不再以1.5R剩余空间硬拒绝；允许预判支撑被瀑布行情跌破。形态自身迟到限制与结构保护距离仍保留，普通趋势追空继续使用利润空间门槛。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}",
     "stop_loss": f"【v0.7.51｜顶部早空最新局部止损】只保护最近10根一分钟触发结构高点，不回溯远端旧头部。\n{rule['stop_loss']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V751_FRESH_LOCAL_STOP)
)

_V752_EARLY_HIGH_SHORTS = (
    "【v0.7.52｜一分钟MA5与五分钟半覆盖抢先空】一分钟高点出现已收盘阴线下穿MA5，且MA5走平或向下弯曲即可小风险做空，不要求下穿MA10。五分钟最高区域出现阴线覆盖前一根阳线实体至少一半时形成另一条独立早空规则，不要求五分钟或一分钟已经下穿MA5；一分钟MA5走平/下弯只作同步走弱确认。两条规则与双顶、头肩顶及原有顶部反转并存，止损只放触发附近最新微型高点外，专门适配长时间横盘磨顶。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}",
     "stop_loss": f"【v0.7.52｜顶部微型止损】抢先空只保护触发附近最新微型高点；位置不够高或止损距离超限仍放弃。\n{rule['stop_loss']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V752_EARLY_HIGH_SHORTS)
)

_V753_WATERFALL_HOLD_REENTRY = (
    "【v0.7.53｜瀑布持有与多阶段续空】已建立的下跌趋势中，每次一分钟反抽MA5/MA10形成新的较低高点，随后阴线收盘重新跌破MA5且MA5走平向下，均可重新武装为独立续空机会，不强制反抽到MA20；止损只放本次微型反抽高点外。同方向已有持仓时不无限叠仓，平仓后须重新满足形态、冷却与全部账户安全门。五分钟MA5第一次走平只作止盈预警；空单须连续两根已收盘五分钟MA5走平/上拐且价格收回MA5才正常止盈，多单镜像。十五分钟MA5接管与明确放量衰竭紧急止盈继续保留。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "continuation": f"{new_rule}\n{rule['continuation']}",
     "take_profit": f"【v0.7.53｜一次走平不平仓】五分钟MA5首次走平只预警；连续两根确认且价格反向收回MA5才正常止盈。\n{rule['take_profit']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V753_WATERFALL_HOLD_REENTRY)
)

_V754_DELAYED_BOTTOM_RELAY = (
    "【v0.7.54｜错位底部接力多】底部形态并列判断：扫底收回、单V形低点、双底、第二底或较高低点回踩任意一种成立即可保存底部证据，双底不是必需条件。一分钟在底点后阳线站上MA5，随后最多四根内MA5走平或上扬即可确认；不要求同时上穿MA10。底部证据保留到五分钟第2至第4根连续阳线真正上穿MA5时独立做多，五分钟同样不检查MA10；止损只保护最近一分钟回踩低点，距离过大仍放弃。此规则与原三阶段反转及其他做多规则并存。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}",
     "stop_loss": f"【v0.7.54｜错位底部微型止损】延迟五分钟确认后只保护最近一分钟回踩低点，不回溯最初扫底极值。\n{rule['stop_loss']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V754_DELAYED_BOTTOM_RELAY)
)

_V755_TRIPLE_INTRABAR_MA5_REVERSAL = (
    "【v0.7.55｜三周期MA5盘中汇合反转】一分钟先盘中穿越动态MA5时保存候选证据，五分钟、十五分钟可在随后30分钟窗口（两个15分钟周期）内依次完成盘中穿越；三个周期证据齐全且三条MA5均走平或上扬时，独立小止损做多。顶部完全镜像：三个周期在30分钟内先后盘中下穿动态MA5，三条MA5走平或下弯时做空。不等待五分钟、十五分钟收盘，不检查MA10或MA20。超过30分钟的旧证据自动失效；只在五分钟、十五分钟近期局部极值附近启用，止损放最近一分钟局部低点/高点外。止盈先由一分钟MA5管理，趋势延伸后依次交给五分钟、十五分钟MA5接管。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}",
     "take_profit": f"【v0.7.55｜MA5逐级接管】三周期盘中反转仓先由一分钟MA5保护，五分钟MA5延伸后接管，十五分钟MA5形成连续方向后最终接管。\n{rule['take_profit']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V755_TRIPLE_INTRABAR_MA5_REVERSAL)
)

_V756_TWO_LEVEL_MA5_REVERSAL = (
    "【v0.7.56｜MA5反转双级别】小波段级别：一分钟与五分钟在30分钟候选窗口内先后盘中穿越各自动态MA5，且两条MA5走平或顺向弯曲，立即以最近一分钟局部极值小止损入场，不等待十五分钟；先由一分钟MA5保护，五分钟MA5形成延伸后接管。大波段级别：十五分钟在同一30分钟窗口内继续完成MA5盘中穿越后升级。若同方向小波段仓仍持有，不重复加仓，只升级为十五分钟MA5最终接管；若小波段仓已离场，才按当前局部止损重新评估大波段新单。顶部做空完全镜像。两级均不检查MA10或MA20。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}",
     "take_profit": f"【v0.7.56｜小波段转大波段】双周期仓由1分钟→5分钟MA5管理；15分钟补齐后不叠仓，升级为1分钟→5分钟→15分钟MA5管理。\n{rule['take_profit']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V756_TWO_LEVEL_MA5_REVERSAL)
)

_V757_NO_AGGRESSIVE_TIME_COOLDOWN = (
    "【v0.7.57｜激进型取消时间冷却】激进型自动实盘不再设置全局2分钟冷却：平仓后新形态立即重新评估，同向续单或多空反手均不等待时间。正常扫描仍按20秒轮询，不进行无间隔高频请求。同一信号K线/候选ID去重、同方向已有持仓禁止重复叠仓、服务器保护单、单笔风险、每日亏损与成功交易上限继续保留。保守型、稳妥型仍为5分钟冷却。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{new_rule}\n{rule['limits']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V757_NO_AGGRESSIVE_TIME_COOLDOWN)
)

_V758_MA5_VALID_SIDE_ALIGNMENT = (
    "【v0.7.58｜双/三周期MA5有效站位】双周期小波段与三周期大波段不再要求某一根阳线刚好上穿MA5：价格在动态MA5上方且MA5走平或上扬即为做多有效，不限阳线、阴线，不等待收盘；第二根、第三根及后续K线只要状态仍成立均可参与30分钟汇合。做空完全镜像：价格在MA5下方且MA5走平或下弯即有效。触发当下所有参与周期必须仍处于同一有效侧，陈旧证据不得下单；仍不检查MA10或MA20。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V758_MA5_VALID_SIDE_ALIGNMENT)
)

_V759_UNATTENDED_GET_RECONNECT = (
    "【v0.7.59｜只读网络故障全自动恢复】激进型遇到明确发生在下单前的OKX只读GET、SSL握手或连接超时，不再于连续5轮后故障停止；保持自动总闸运行，按30/60/90/120秒（最高120秒）无限期退避刷新。网络恢复后先重新读取账户净盈亏、持仓、委托与保护状态并通过全部风险门槛，才恢复策略评估。POST下单结果不明、账户模式异常、保护失败、亏损熔断等非只读网络故障仍立即停止，禁止盲目自动重启。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "limits": f"{new_rule}\n{rule['limits']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V759_UNATTENDED_GET_RECONNECT)
)

_V760_WATERFALL_REENTRY = (
    "【v0.7.60｜瀑布趋势微型回抽续单】当1分钟、5分钟、15分钟MA5<MA10<MA20且一分钟反抽后重新压回MA5下方，顺势空单改用最近5根一分钟微型高点外止损，不再被旧五分钟远端高点拒绝；做多镜像。瀑布持仓期间跳过一分钟MA5拐弯、连续两根长阴和一分钟放量衰竭止盈，直接沿五分钟MA5下滑线持有；五分钟MA5走平/上拐并收回后才退出。仅对已经成立的趋势延续触发放宽距五分钟MA20至1.25 ATR，仍保留服务器止损、防叠仓与去重。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "continuation": f"{new_rule}\n{rule['continuation']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V760_WATERFALL_REENTRY)
)

_V761_REVERSAL_LOCATION = (
    "【v0.7.61｜反转区间位置硬门】所有激进型顶部小止损反转空与底部小止损反转多，必须同时位于最近12根一分钟和最近6根五分钟可见区间的正确一侧：上部38%才做空、下部38%才做多。顶部候选漂移到横盘低位或底部候选漂移到横盘高位立即作废；趋势延续续单不套用本反转门。成交弹窗逐项显示一分钟/五分钟区间百分位、局部高低点、触发规则、入场、止损和止盈。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{new_rule}\n{rule['trigger']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V761_REVERSAL_LOCATION)
)

_V762_MA5_ANCHOR_HOLD = (
    "【v0.7.62｜MA5局部高低位解锁止盈】激进型盘中/收盘下穿MA5的顶部小止损空单，成交时保存最近一次一分钟MA5局部峰值；此后即使已有浮盈、浮盈回吐、短时转亏或十五分钟MA5发出普通止盈，只要一分钟MA5尚未重新上扬触达该峰值，客户端禁止提前平仓，只保留服务器局部结构止损。触达后才恢复正常动态止盈。底部做多镜像使用MA5局部谷值。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "take_profit": f"{new_rule}\n{rule['take_profit']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V762_MA5_ANCHOR_HOLD)
)

_V763_DUAL_MA20_CROSS = (
    "【v0.7.63｜双周期三均线发散启动】新增独立趋势延续启动规则：一分钟与五分钟在短窗口内先后完成MA5、MA10下穿MA20，并形成MA5<MA10<MA20向下张口，当前阴线位于MA5下方时快速追空；金叉追多完全镜像。15分钟与1小时必须同向，价格距五分钟MA20不得超过0.75 ATR，止损仅放最近6根一分钟局部高低点外。该规则用于趋势刚启动，不经过反转高低区位置门，也不等待行情远离后再追。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "continuation": f"{new_rule}\n{rule['continuation']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V763_DUAL_MA20_CROSS)
)

_V764_CROSS_TIME_SYNC = (
    "【v0.7.64｜一分钟先行＋双周期升级】适用于趋势走弱区和横盘震荡区：一分钟局部高点下移、价格继续走低并形成三均线死叉后，立即在最近一分钟局部高点外设小止损并市价做空，不等待五分钟；做多镜像要求局部低点抬高、价格继续走高并形成金叉后小止损市价做多。15分钟、1小时只作背景，不拦截该局部启动；五分钟在15分钟内完成同向交叉后，只把已有仓位升级为五分钟MA5趋势接管，不重复加仓。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "continuation": f"{new_rule}\n{rule['continuation']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V764_CROSS_TIME_SYNC)
)

_V767_PROTECTION_RECOVERY = (
    "【v0.7.67｜预埋成交保护恢复】预埋单成交后先保留灾难止损；本机保护状态缺失时，使用OKX持仓、服务器保护单和本机预埋快照恢复观察状态。无法恢复或找不到匹配保护单时，交易界面弹出中文保护异常，不再静默跳过；完成观察后再缩至正常结构止损。",
) * 4
STRATEGY_RULES = tuple(
    {**rule, "stop_loss": f"{new_rule}\n{rule['stop_loss']}"}
    for rule, new_rule in zip(STRATEGY_RULES, _V767_PROTECTION_RECOVERY)
)

_V767_TWO_TIMEFRAME_AND_HEDGE = (
    "【v0.7.67｜取消三周期交叉】激进型不再执行1分钟+5分钟+15分钟三周期MA5汇合下单；快速行情以1分钟金叉/死叉和局部结构先行，最多使用1分钟+5分钟双周期确认，15分钟只作持仓背景。",
) * 4
_V767_LAYERED_HEDGE_LIMIT = (
    "【v0.7.67｜双向分层持仓】双向持仓模式下，多单与空单互不阻塞，可同时各保留1张；结构预埋阶段每方向最多2层。若猛烈插针令同方向两层都成交，等待两根完整1分钟K线后减掉成交较差的一层，恢复为该方向1张；另一方向仍可独立识别和成交。",
) * 4
_V767_PROTECTION_NOTICE_DEDUP = (
    "【v0.7.67｜保护归属与弹窗去重】旧预埋状态不得只凭同方向持仓认领当前仓位；订单编号、服务器保护编号或35分钟有效归属窗口不匹配时自动归档。相同保护动作与订单编号每次软件运行只弹窗一次，变化的行情说明只更新状态栏。",
) * 4
_V767_FAST_NETWORK_RECOVERY = (
    "【v0.7.67｜签名API快速自恢复】网页可访问与签名API可用分别判断。只读连接单次超时缩至8秒，失败后的恢复探测按10/15/20/30秒递进并以30秒封顶；启动审计遇到临时网络故障也持续自恢复，不再直接停机。恢复后必须先复核账户、持仓、保护单和风险状态；POST结果不明仍绝不自动重试。",
) * 4
STRATEGY_RULES = tuple(
    {**rule,
     "trigger": f"{trigger_rule}\n{rule['trigger']}",
     "limits": f"{network_rule}\n{notice_rule}\n{limit_rule}\n{rule['limits']}"}
    for rule, trigger_rule, limit_rule, notice_rule, network_rule in zip(
        STRATEGY_RULES, _V767_TWO_TIMEFRAME_AND_HEDGE,
        _V767_LAYERED_HEDGE_LIMIT, _V767_PROTECTION_NOTICE_DEDUP,
        _V767_FAST_NETWORK_RECOVERY)
)

_V144_FAST_MA5_PROFIT_EXIT = (
    "[v0.7.129 strategy v144 | fast MA5 profit exit and exact audit linkage] "
    "After a position makes a fast favorable move of at least one 1m ATR, a "
    "profitable long exits immediately when price returns to MA5 and 1m MA5 "
    "flattens or bends down; shorts use the exact mirror. This exit overrides "
    "a lagging 5m hold signal while the server structure stop remains backup. "
    "A reversal-review zone may be marked submitted only by a same-side order "
    "from its current trigger window; later unrelated orders cannot backfill it. "
    "When a mature same-side 1m+5m MA20 extreme confirms the core reversal, "
    "current MA5-break/death-cross evidence overrides stale anchors and local "
    "range percentiles, and the resulting core position is managed by 5m MA5. "
    "During the confirmed 1m downtrend, qualified pullback highs may add short "
    "continuation layers up to three total, with pending-order deduplication."
)
STRATEGY_RULES = tuple(
    {**rule,
     "take_profit": f"{_V144_FAST_MA5_PROFIT_EXIT}\n{rule['take_profit']}",
     "version": "demo-frequency-validation-v144"}
    if rule["version"] == "demo-frequency-validation-v143" else rule
    for rule in STRATEGY_RULES
)

_V145_DUAL_BOTTOM_PRIORITY = (
    "[v0.7.130 strategy v145] Fresh same-direction 1m+5m reversal zones invalidate every older opposite frozen chain and ordinary continuation candidate. "
    "After a confirmed dual bottom, a roughly three-candle bullish recovery covering at least half of the pullback may enter before crossing 1m MA5 when both pullback lows are below their MA5/MA10/MA20 stacks. "
    "Each new same-direction pullback structure may add one layer, up to three total, with same-candle and pending-order deduplication. Every submitted lifecycle order is displayed in the reversal review even when no zone association exists."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V145_DUAL_BOTTOM_PRIORITY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v145"}
    if rule["version"] == "demo-frequency-validation-v144" else rule
    for rule in STRATEGY_RULES
)

_V146_FAST_MA_PULLBACK_LOCATION = (
    "[v0.7.131 strategy v146] Every trend-continuation long must originate from a recent "
    "one-minute pullback whose low reached below both MA5 and MA10; MA20 side is not a veto. "
    "Trend-continuation shorts use the exact mirror above MA5 and MA10. This gate also applies "
    "to second and third same-direction continuation layers. After a confirmed top leads into "
    "a downtrend, a bearish candle covering at least half of the preceding bullish body may add "
    "a short before MA5 breaks, with protection beyond that pullback high."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V146_FAST_MA_PULLBACK_LOCATION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v146"}
    if rule["version"] == "demo-frequency-validation-v145" else rule
    for rule in STRATEGY_RULES
)

_V147_MA5_OUTER_EDGE_TURN = (
    "[v0.7.132 strategy v147] Continuation entries are location-first MA5 outer-edge turns. "
    "Once the pullback reaches the MA5/MA10 outer side, a directional candle moving away from "
    "that edge with a minimum 0.10 ATR body is sufficient. No single candle must cover 50% of "
    "the previous candle and no MA5 recross is required; MA20 side remains unrestricted."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V147_MA5_OUTER_EDGE_TURN}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v147"}
    if rule["version"] == "demo-frequency-validation-v146" else rule
    for rule in STRATEGY_RULES
)

_V148_DURABLE_DUAL_REVERSAL_REGIME = (
    "[v0.7.133 strategy v148] Once a mature 1m reversal and a same-direction 5m reversal/cover "
    "confirm a dual top or bottom, persist that directional regime across later mixed MA stacks, "
    "a missed first entry, and a stopped first entry. Only a newer true opposite dual reversal "
    "invalidates it. The reversal review displays the inherited mature endpoint, and later MA5/MA10 "
    "outer-edge reactions are evaluated as continuation entries up to three layers."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V148_DURABLE_DUAL_REVERSAL_REGIME}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v148"}
    if rule["version"] == "demo-frequency-validation-v147" else rule
    for rule in STRATEGY_RULES
)

_V149_DURABLE_REGIME_CONTINUATION_GATE = (
    "[v0.7.134 strategy v149] After a durable 1m+5m reversal regime is confirmed, a same-direction "
    "MA5/MA10 outer-edge continuation owns its entry timing and bypasses the generic five-minute "
    "cover/MA5-break gate. Structure stop, deduplication, account safeguards, and the three-layer "
    "maximum remain mandatory; the regime remains valid after a missed or stopped first order."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V149_DURABLE_REGIME_CONTINUATION_GATE}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v149"}
    if rule["version"] == "demo-frequency-validation-v148" else rule
    for rule in STRATEGY_RULES
)

_V150_EXECUTABLE_FIVE_MINUTE_COVER = (
    "[v0.7.135 strategy v150] A detected five-minute high bearish cover is now an executable "
    "short branch, not audit-only evidence. Five-minute confirmation uses only the adjacent pair; "
    "the up-to-six mixed-bar cluster belongs to one minute, keeping the stop at the current pullback high. It does not wait "
    "for an expired one-minute staged trigger. Existing risk, deduplication, and layer caps remain."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V150_EXECUTABLE_FIVE_MINUTE_COVER}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v150"}
    if rule["version"] == "demo-frequency-validation-v149" else rule
    for rule in STRATEGY_RULES
)

_V151_PERSISTENT_LOW_ENDPOINT = (
    "[v0.7.136 strategy v151] Preserve the lowest mature one-minute MA-spread endpoint across a "
    "longer base. A later smaller MA expansion is uptrend continuation once five-minute structure "
    "confirms. Every continuation-classified staged entry must still reach the MA5/MA10 pullback "
    "outer side, preventing shorts at bottom support; the newer bottom regime replaces stale shorts."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V151_PERSISTENT_LOW_ENDPOINT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v151"}
    if rule["version"] == "demo-frequency-validation-v150" else rule
    for rule in STRATEGY_RULES
)

_V152_EXECUTABLE_QUALIFIED_TOP_SHORT = (
    "[v0.7.137 strategy v152] A qualified specialised top-short remains executable even while a "
    "previous MA-spread endpoint regime is still persisted. One-minute stage evidence plus the "
    "five-minute top confirmation may execute with its own compact stop; the legacy observe-only "
    "gate cannot clear it before its dedicated primary gate. The displayed 1m, 5m, 15m, 1H "
    "and 4H direction arrows are audit-only and have no entry veto or voting authority."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V152_EXECUTABLE_QUALIFIED_TOP_SHORT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v152"}
    if rule["version"] == "demo-frequency-validation-v151" else rule
    for rule in STRATEGY_RULES
)

_V153_MEDIUM_TREND_LOCAL_SWING_EXIT = (
    "[v0.7.138 strategy v153] Classify an order as a medium-trend position only when its direction "
    "matches a persisted one-minute plus five-minute true MA5/MA10/MA20 spread-end reversal. The "
    "reversal core and at most two same-direction continuation add-ons share the five-minute MA5 "
    "profit exit. All other valid local-top/local-bottom entries remain local-swing positions and "
    "use the one-minute MA5 turn for profit exit."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V153_MEDIUM_TREND_LOCAL_SWING_EXIT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v153"}
    if rule["version"] == "demo-frequency-validation-v152" else rule
    for rule in STRATEGY_RULES
)

_V154_ENDPOINT_ONLY_FIVE_MINUTE_EXIT = (
    "[v0.7.139 strategy v154] Reserve the five-minute MA5 profit exit exclusively for the actual "
    "one-minute plus five-minute MA5/MA10/MA20 spread-end sweep-top short or sweep-bottom long. "
    "Every pullback long, rebound short, continuation add-on and local swing uses the one-minute "
    "MA5 turn for profit exit, even when it follows a durable endpoint reversal trend. A downtrend "
    "rebound short triggers on the first live one-minute weakening candle after touching the MA5 "
    "outer edge; it does not wait for MA10, an MA5 break, or the five-minute close."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V154_ENDPOINT_ONLY_FIVE_MINUTE_EXIT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v154"}
    if rule["version"] == "demo-frequency-validation-v153" else rule
    for rule in STRATEGY_RULES
)

_V155_PRECISE_PULLBACK_DIRECTION_OWNERSHIP = (
    "[v0.7.140 strategy v155] Execute a downtrend rebound short on the first one-minute bearish "
    "turn while price is still at or above the MA5 outer edge; reject the setup after price has "
    "crossed below MA5. A successful five-minute top is persistent direction evidence only; it "
    "must not be reused as a direct entry, and execution steps down to the precise one-minute "
    "pullback high. Mirror the "
    "timing principle for an uptrend pullback long: the first strengthening candle below MA5 owns "
    "the entry, without waiting for an MA5 reclaim. A persisted 1m+5m true bottom reversal owns "
    "the following uptrend and suppresses ordinary local-top shorts; only a fresh endpoint reversal "
    "trial may challenge it, and a matching 1m+5m opposite endpoint replaces it. Record 15m as the "
    "maximum same-direction confidence upgrade; 1H and 4H never participate in this hierarchy."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V155_PRECISE_PULLBACK_DIRECTION_OWNERSHIP}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v155"}
    if rule["version"] == "demo-frequency-validation-v154" else rule
    for rule in STRATEGY_RULES
)

_V156_PERSISTED_ENDPOINT_HOLD_PROMOTION = (
    "[v0.7.141 strategy v156] Manage the original one-minute endpoint trial with the one-minute "
    "MA5 until the review list contains a durable same-direction 1m+5m true endpoint reversal. "
    "Then promote that still-open core position once to five-minute MA5 management and never "
    "downgrade it because of a later pullback or a repeated same-direction bottom/top record. "
    "The next two pullback-long or rebound-short continuation layers remain independent one-minute "
    "MA5 exits; they never inherit the core position's five-minute hold."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V156_PERSISTED_ENDPOINT_HOLD_PROMOTION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v156"}
    if rule["version"] == "demo-frequency-validation-v155" else rule
    for rule in STRATEGY_RULES
)

_V157_FIVE_CLASS_MIRRORED_ENTRY_IDENTITY = (
    "[v0.7.142 strategy v157] Persist one of five mutually exclusive entry identities for every "
    "original-strategy short: true high sweep reversal, local-high sweep reversal, stage-three "
    "miss recovery, current-timeframe downtrend rebound short, or parent-timeframe downtrend "
    "rebound short. Apply the exact mirrored five identities to longs. A true endpoint is always "
    "relative to its own timeframe: the same price may be a true 1m high and only a local 5m/15m "
    "high. Store the identity in the trade lifecycle and show the same Chinese label in market "
    "shape, miss class and full reason. A newer qualified same-side true endpoint replaces the old "
    "active anchor; the prior record remains audit history, and the newest endpoint stays active "
    "until broken or replaced by a confirmed opposite dual endpoint."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V157_FIVE_CLASS_MIRRORED_ENTRY_IDENTITY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v157"}
    if rule["version"] == "demo-frequency-validation-v156" else rule
    for rule in STRATEGY_RULES
)

_V158_RAW_CANDLE_REVERSAL_DECISION = (
    "[v0.7.143 strategy v158] Remove Heikin-Ashi/average candles from every live reversal, "
    "direction, entry-release and exit-hold decision. Detect new sweep-top/sweep-bottom zones "
    "from ordinary exchange OHLC candles using sweep/reclaim, opposite-candle coverage, local "
    "extreme turns and the MA5 edge. Only ordinary-candle price-reversal zones may establish or "
    "replace durable 1m+5m or maximum 1m+5m+15m trend ownership. Keep historical Heikin-Ashi "
    "records readable in the review list as audit-only evidence; they can never release a new "
    "order or set current direction. Confirmed endpoint core positions retain raw 5m close plus "
    "5m MA5 management, while continuation layers retain their existing 1m MA5 management."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V158_RAW_CANDLE_REVERSAL_DECISION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v158"}
    if rule["version"] == "demo-frequency-validation-v157" else rule
    for rule in STRATEGY_RULES
)

_V159_CLOSE_STRUCTURE_ENDPOINT_HALF_COVER = (
    "[v0.7.144 strategy v159] Define uptrend, downtrend and sideways consolidation from "
    "confirmed K-line closing-price swing highs and lows: higher highs plus higher lows is "
    "uptrend, lower highs plus lower lows is downtrend, and mixed/alternating edges is sideways. "
    "At a recent one-minute MA5/MA10/MA20 fan endpoint on the corresponding closing-price edge, "
    "allow the live five-minute opposite candle to release the mirrored reversal as soon as its "
    "dynamic close covers at least half of the immediately preceding opposite candle body. Do not "
    "wait for either timeframe to cross MA5; retain local-structure stop, deduplication, exposure "
    "limits and the maximum three same-direction layers. Ordinary local entries that are not an "
    "MA-fan endpoint continue to use their own stricter MA5-cross classification."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V159_CLOSE_STRUCTURE_ENDPOINT_HALF_COVER}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v159"}
    if rule["version"] == "demo-frequency-validation-v158" else rule
    for rule in STRATEGY_RULES
)

_V160_LOCAL_BOTTOM_SIX_BAR_COVER = (
    "[v0.7.146 strategy v160] A local-bottom long is released when the current up-to-six-bar "
    "one-minute mixed bottom cluster reclaims MA5 and the current five-minute bullish candle "
    "recovers at least half of the adjacent bearish body. This local setup does not also need "
    "true dual-timeframe MA-spread exhaustion. Its structural stop uses only the current six-bar "
    "one-minute cluster and adjacent five-minute lows; the mirrored local-top rule is unchanged."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V160_LOCAL_BOTTOM_SIX_BAR_COVER}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v160"}
    if rule["version"] == "demo-frequency-validation-v159" else rule
    for rule in STRATEGY_RULES
)

_V161_FRESH_PULLBACK_AND_ZONE_DEDUPE = (
    "[v0.7.147 strategy v161] A trend-continuation long must come from the latest three "
    "one-minute bars reaching below MA5 and MA10, and its execution close must still be at or "
    "below MA5; the short rule is mirrored above MA5. Older pullback extremes cannot authorise "
    "a later chase. An existing same-side reversal lifecycle blocks another reversal entry "
    "across strategy-version changes; only a later independent continuation structure may add."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V161_FRESH_PULLBACK_AND_ZONE_DEDUPE}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v161"}
    if rule["version"] == "demo-frequency-validation-v160" else rule
    for rule in STRATEGY_RULES
)

_V162_LEAN_CORE = (
    "[v0.7.148 strategy v162] Remove obsolete PineTS, browser/Webhook research, "
    "five-minute snapshot and shared MA-deviation experiment paths from the live runtime "
    "and package. At startup retire only their dedicated database tables; preserve order "
    "lifecycle, reversal state, missed-order review and risk-control data."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V162_LEAN_CORE}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v162"}
    if rule["version"] == "demo-frequency-validation-v161" else rule
    for rule in STRATEGY_RULES
)

_V163_FRESH_MA5_EDGE_AND_DIRECTIONAL_REVIEW = (
    "[v0.7.149 strategy v163] A weakening-top short belongs only to the current first "
    "MA5 failure and must remain within 0.50 one-minute ATR of MA5; an older 1-to-3-bar "
    "top trigger cannot authorize a lower-edge short. Sideways review rows retain their "
    "event direction as bullish or bearish candidates while execution remains gated."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V163_FRESH_MA5_EDGE_AND_DIRECTIONAL_REVIEW}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v163"}
    if rule["version"] == "demo-frequency-validation-v162" else rule
    for rule in STRATEGY_RULES
)

_V164_LOCKED_ACCESS_DOMAIN = (
    "[v0.7.150 strategy v164] Lock Demo, Live read-only, manual Live and automatic Live "
    "transports to the single deployment access domain www.tpouxyihas.com. Reject every "
    "constructor override before any request or credential header is created."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V164_LOCKED_ACCESS_DOMAIN}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v164"}
    if rule["version"] == "demo-frequency-validation-v163" else rule
    for rule in STRATEGY_RULES
)

_V165_FEE_AWARE_EXIT_AND_STRUCTURE_AUDIT = (
    "[v0.7.151 strategy v165] Defer every local active profit exit until the expected "
    "0.05-contract gross profit covers both taker fees plus 0.005 USDT net. Persist MFE, "
    "MAE and 5/10/20-minute post-stop prices. Never compress a valid structural stop to "
    "three points; reject unaffordable minimum-size entries. A 5m high half-cover requires "
    "a fresh bearish 1m candle at the MA5 edge and cannot authorize repeated shorts alone."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V165_FEE_AWARE_EXIT_AND_STRUCTURE_AUDIT}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v165"}
    if rule["version"] == "demo-frequency-validation-v164" else rule
    for rule in STRATEGY_RULES
)

_V166_ENDPOINT_LINEAGE = (
    "[v0.7.152 strategy v166] Persist every 1m/5m/15m/1H MA-spread endpoint in "
    "a dedicated lineage table. Pair nearest same-side endpoints level by level; "
    "keep unmatched 1m endpoints as local noise interpreted by the 5m trend. A "
    "completed 1m+5m pair promotes every matching open lifecycle to 5m-MA5 "
    "management and cancels its older server trailing-profit order first."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V166_ENDPOINT_LINEAGE}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v166"}
    if rule["version"] == "demo-frequency-validation-v165" else rule
    for rule in STRATEGY_RULES
)

_V167_CONFIRMED_ENDPOINT_ZONES = (
    "[v0.7.153 strategy v167] A three-MA spread endpoint is one persistent zone: "
    "later same-side extremes extend the same row instead of creating repeated reversals. "
    "A 1m+5m pair is forbidden until the 5m opposite candle covers at least half of "
    "the prior candle body and the 5m close reaches the reversal side of MA20. Ordinary "
    "1m true-top/bottom entries must also close across 1m MA5. A separate upper-level "
    "5m local-bottom/top reversal may execute on the 5m half-cover without the 1m MA5 "
    "cross, but never becomes a true dual-timeframe pair. An unmatched 1m bottom/top "
    "is noise and is classified as a 1m downtrend pullback-short/uptrend pullback-long trigger."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V167_CONFIRMED_ENDPOINT_ZONES}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v167"}
    if rule["version"] == "demo-frequency-validation-v166" else rule
    for rule in STRATEGY_RULES
)

_V168_REPEATED_LOCAL_REVERSALS = (
    "[v0.7.154 strategy v168] Every distinct 5m half-cover local reversal owns a new "
    "entry opportunity. A closed earlier trial never suppresses the next shape. While "
    "same-side exposure remains, a fresh shape in the established trend may add layer two "
    "or three; the same 5m candle, a pending opening order and a fourth layer remain blocked. "
    "Each layer uses its own recent local extreme as the server stop and 5m MA5 from entry. "
    "A completed 1m+5m endpoint pair locks the trend independently of whether any reversal "
    "order filled, so later fresh MA5/MA10 pullbacks remain executable from zero through "
    "three layers."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V168_REPEATED_LOCAL_REVERSALS}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v168"}
    if rule["version"] == "demo-frequency-validation-v167" else rule
    for rule in STRATEGY_RULES
)

_V169_MA5_MA20_ENDPOINT_EDGES = (
    "[v0.7.155 strategy v169] A local bottom/top must be a genuine MA-fan endpoint, "
    "not merely a recent price low/high. A bottom close must be below both MA5 and MA20 "
    "with those outer averages materially expanded and MA5 still falling; a top is the "
    "exact mirror. MA10 remains visible and persisted but may cross and is not an entry "
    "gate. Require this endpoint condition on both 1m and 5m before a 5m half-cover local "
    "reversal trial. Only qualified endpoints enter the four-level lineage or pair; retain "
    "legacy unverified rows as audit noise but exclude them from direction locks."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V169_MA5_MA20_ENDPOINT_EDGES}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v169"}
    if rule["version"] == "demo-frequency-validation-v168" else rule
    for rule in STRATEGY_RULES
)

_V170_LATEST_ENDPOINT_LOCK_OWNS_DIRECTION = (
    "[v0.7.156 strategy v170] The newest completed 1m+5m MA-fan endpoint pair owns "
    "the current trend direction. A later top lock releases the prior bottom lock and "
    "starts the downtrend; a later bottom lock mirrors this rule. Released locks remain "
    "visible for audit but cannot authorize entries or position upgrades. Endpoint zones "
    "and pairing windows are limited to their actual parent-candle neighbourhood, and a "
    "live 5m half-cover may use only the immediately preceding 5m endpoint candle. Once "
    "the latest lock exists, its fresh MA5/MA10 stage-three cross is a mandatory missed-entry "
    "fallback and is not rejected solely by a transient MA5-side mismatch."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V170_LATEST_ENDPOINT_LOCK_OWNS_DIRECTION}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v170"}
    if rule["version"] == "demo-frequency-validation-v169" else rule
    for rule in STRATEGY_RULES
)

_V171_SIMPLE_FIVE_MINUTE_WEAKENING = (
    "[v0.7.157 strategy v171] A 5m top/bottom needs only approximate half-cover "
    "weakening/strengthening: one to three following opposite candle bodies may combine "
    "to about 45% of the impulse body. No 5m MA5, MA20, or slow-MA cross is required for "
    "pairing; 1m owns execution. When both 15m and 1H are in a downtrend, the first 1m "
    "weakening candle at a fresh pullback high may short before crossing below 1m MA5."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V171_SIMPLE_FIVE_MINUTE_WEAKENING}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v171"}
    if rule["version"] == "demo-frequency-validation-v170" else rule
    for rule in STRATEGY_RULES
)

_V172_ONE_MINUTE_ENDPOINT_TRIAL_BEFORE_LOCK = (
    "[v0.7.158 strategy v172] Every qualified 1m MA-fan endpoint independently checks "
    "the current 5m one-to-three-candle approximate half-cover. Once the 1m execution "
    "turn is present, submit the local reversal trial even if the 5m endpoint, pair, or "
    "durable top/bottom lock is absent or was missed. Locking only controls later trend identity."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V172_ONE_MINUTE_ENDPOINT_TRIAL_BEFORE_LOCK}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v172"}
    if rule["version"] == "demo-frequency-validation-v171" else rule
    for rule in STRATEGY_RULES
)

_V173_CLOSED_REVERSAL_AND_FIVE_MINUTE_LOCK = (
    "[v0.7.159 strategy v173] Local and true top/bottom reversals accept 5m approximate "
    "half-cover only after that 5m candle has closed. Intrabar 5m half-cover remains valid "
    "only inside explicitly classified uptrend pullback-long or downtrend rebound-short "
    "continuation branches. A closed 5m MA-fan endpoint independently locks direction even "
    "without a 1m pair; later pullbacks follow that newest lock."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V173_CLOSED_REVERSAL_AND_FIVE_MINUTE_LOCK}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v173"}
    if rule["version"] == "demo-frequency-validation-v172" else rule
    for rule in STRATEGY_RULES
)

_V174_INTRABAR_REVERSAL_WITH_DURABLE_FIVE_MINUTE_LOCK = (
    "[v0.7.160 strategy v174] Restore intrabar 5m approximate half-cover for local and "
    "true top/bottom reversal entries so the 1m structural stop remains compact. The bad "
    "21:26 long is addressed by the separate closed-5m MA-fan endpoint direction lock, not "
    "by delaying every reversal until the 5m close. The newest closed 5m endpoint owns trend."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V174_INTRABAR_REVERSAL_WITH_DURABLE_FIVE_MINUTE_LOCK}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v174"}
    if rule["version"] == "demo-frequency-validation-v173" else rule
    for rule in STRATEGY_RULES
)

_V175_SINGLE_HIGHER_TIMEFRAME_DIRECTION_LOCK = (
    "[v0.7.161 strategy v175] Trend direction no longer requires any dual-timeframe pair. "
    "A closed 1H high/low MA-fan endpoint directly locks the 15m down/up trend; a closed "
    "15m endpoint locks the 5m trend; a closed 5m endpoint locks the 1m trend. The hierarchy "
    "is strictly 1H -> 15m -> 5m -> 1m, and the newest applicable direct or inherited lock "
    "owns direction while each timeframe retains an independent audit lock."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V175_SINGLE_HIGHER_TIMEFRAME_DIRECTION_LOCK}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v175"}
    if rule["version"] == "demo-frequency-validation-v174" else rule
    for rule in STRATEGY_RULES
)

_V176_LIVE_POST_TRANSPORT_RECOVERY = (
    "[v0.7.162 strategy v176] Live order transport distinguishes failures before HTTP "
    "transmission from ambiguous lost responses. TLS handshake, DNS, and connect failures "
    "retry safely with the same clOrdId; ambiguous ordinary-order responses are reconciled "
    "read-only by clOrdId. Only genuinely unresolvable outcomes fault the automatic engine."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V176_LIVE_POST_TRANSPORT_RECOVERY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v176"}
    if rule["version"] == "demo-frequency-validation-v175" else rule
    for rule in STRATEGY_RULES
)

_V177_FRESH_ONE_MINUTE_REVERSAL_TREND_LOCK = (
    "[v0.7.163 strategy v177] A real 1m bottom/top reversal trial may temporarily supersede "
    "the older inherited trend after price crosses MA20 and either holds the first retest or "
    "continues without a retest while MA20 turns in the new direction. A broken endpoint, or "
    "two closes back on the old side with MA20 turning back, marks the lock failed and restores "
    "the prior higher-timeframe trend. Long and short are mirrored."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V177_FRESH_ONE_MINUTE_REVERSAL_TREND_LOCK}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v177"}
    if rule["version"] == "demo-frequency-validation-v176" else rule
    for rule in STRATEGY_RULES
)

_V178_MISSED_REVERSAL_STILL_LOCKS_TREND = (
    "[v0.7.164 strategy v178] A successfully confirmed 1m reversal locks the new trend "
    "even when its trial order was missed or never filled. Market structure owns direction, "
    "not order status. The MA20 cross plus first held retest, or direct continuation with an "
    "MA20 turn, promotes the endpoint; the existing failure rules restore the old trend."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V178_MISSED_REVERSAL_STILL_LOCKS_TREND}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v178"}
    if rule["version"] == "demo-frequency-validation-v177" else rule
    for rule in STRATEGY_RULES
)

_V179_OPPOSITE_REVERSAL_TAKEOVER_EXIT = (
    "[v0.7.170 strategy v179] Once a locked top/downtrend has produced up to three short "
    "layers, a tradeable 1m local-bottom upturn plus the live 5m bullish half-cover takes "
    "over profit exit immediately; close every short layer without waiting for the 5m MA5 "
    "turn, confirm the short position is flat, then submit the long reversal trial. A locked "
    "bottom/uptrend uses the exact mirrored rule for long-to-short reversal."
)
STRATEGY_RULES = tuple(
    {**rule, "take_profit": f"{_V179_OPPOSITE_REVERSAL_TAKEOVER_EXIT}\n{rule['take_profit']}",
     "version": "demo-frequency-validation-v179"}
    if rule["version"] == "demo-frequency-validation-v178" else rule
    for rule in STRATEGY_RULES
)

_V180_CONFIRMED_REVERSAL_EXIT_ORDER = (
    "[v0.7.171 strategy v180] 盘中5分钟半覆盖只产生反转试单候选，不再提前平掉旧趋势仓。"
    "反转试单成功提交后，下一轮立即平掉旧方向最多三层仓位；若试单漏掉，但一分钟反转形态随后完成MA20趋势锁定，"
    "仍独立平掉旧方向全部仓位。所有策略内做空必须由反转区阴线族累计覆盖前段阳线实体约一半，做多完全镜像；"
    "一分钟综合最近6根K线族，五分钟综合最近3根K线族，不要求单根硬吞单根。趋势追单以盘中5分钟区域转弱/转强直接确认，"
    "不再等待一分钟穿越MA5。多空完全镜像。"
)
STRATEGY_RULES = tuple(
    {**rule, "take_profit": f"{_V180_CONFIRMED_REVERSAL_EXIT_ORDER}\n{rule['take_profit']}",
     "version": "demo-frequency-validation-v180"}
    if rule["version"] == "demo-frequency-validation-v179" else rule
    for rule in STRATEGY_RULES
)

_V181_THREE_TIMEFRAME_FAST_AND_RECOVERY = (
    "[v0.7.172 strategy v181] 三周期反转独立执行：一分钟站到MA5有效侧，运行中的五分钟"
    "单根K线覆盖相邻反向实体45%，十五分钟由弱转强或由强转弱即可快速触发，十五分钟不要求"
    "半覆盖。一分钟最多6根、五分钟最多3根只用于漏单或断线重连后的补单，不等待凑满。"
    "五分钟穿过MA20继续确认既有一分钟底部/顶部并锁定新趋势；保留随后两个新鲜回踩/反抽追单，"
    "含首层最多三层。多空镜像。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V181_THREE_TIMEFRAME_FAST_AND_RECOVERY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v181"}
    if rule["version"] == "demo-frequency-validation-v180" else rule
    for rule in STRATEGY_RULES
)

_V182_MANDATORY_LIVE_FIVE_COVER = (
    "[v0.7.174 strategy v182] 所有顶部反转做空必须由运行中的5分钟阴线覆盖相邻阳线实体至少45%；"
    "所有底部反转做多完全镜像。任何一分钟、专用顶部/底部、趋势或补漏分支均不得绕过。"
    "首次达到45%立即冻结当时一分钟局部高点/低点止损，后续行情远离不得扩大。"
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V182_MANDATORY_LIVE_FIVE_COVER}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v182"}
    if rule["version"] == "demo-frequency-validation-v181" else rule
    for rule in STRATEGY_RULES
)

_V183_PERSISTED_45_RECOVERY = (
    "[v0.7.175 strategy v183] The first running 5m adjacent-body cover at 45% remains "
    "the mandatory event for every top-short and bottom-long reversal. At that instant, "
    "persist the candidate and compact 1m local-extreme stop. If submission is missed or "
    "the signed API reconnects, recover only from that same frozen event for up to 15 "
    "minutes, while the frozen stop is intact and fresh 1m plus 15m turning evidence is "
    "present. A confirmed 1m MA20 takeover locks the newer trend even when no trial order "
    "filled; a broken anchor invalidates it and restores the parent trend. Long and short "
    "rules are exact mirrors, 42% remains invalid, and an older opposite continuation "
    "branch cannot replace the fresh recovery candidate."
)
STRATEGY_RULES = tuple(
    {**rule, "trigger": f"{_V183_PERSISTED_45_RECOVERY}\n{rule['trigger']}",
     "version": "demo-frequency-validation-v183"}
    if rule["version"] == "demo-frequency-validation-v182" else rule
    for rule in STRATEGY_RULES
)

STRATEGY_RULES = tuple(
    {**rule, "trigger": "[v184复盘优化] 快速及恢复做空转观察；反转须通过1m/5m区域位置与同极值一次试单校验；预计空间至少1.8R及3倍往返费用滑点。服务器浮盈保护在费用+0.5R后收紧，费用+1R后跟随；跨版本对账。\n" + rule["trigger"],
     "version": "demo-frequency-validation-v184"}
    if rule["version"] == "demo-frequency-validation-v183" else rule
    for rule in STRATEGY_RULES
)

STRATEGY_RULES = tuple(
    {**rule, "trigger": "[v185五分钟回踩补单] MA5漏单回踩限价有效300秒；仅本版本登记订单快速盯价，接近原挂价并正在回踩时，确认原单已撤且累计成交为零后仅尝试一次市价承接。沿用原挂单1.2R与冻结保护价，重新扣除吃单成本；普通市价门槛不变。6根识别窗口内较新的MA5外沿局部极值优先确定首次冻结止损。\n" + rule["trigger"],
     "version": "demo-frequency-validation-v185"}
    if rule["version"] == "demo-frequency-validation-v184" else rule
    for rule in STRATEGY_RULES
)

STRATEGY_RULES = tuple(
    {**rule,
     "trigger": ("[v186盈利结构与完整生命周期] 将134条净盈利仓位中可重复核验的77条固定为四类身份："
                 "底部反转做多21、顶部反转做空24、回踩做多15、反抽追空17；每个新订单把模板身份和冻结样本数写入事件、快照与生命周期。"
                 "MA5回踩限价和结构狙击的预期毛空间须至少达到3倍估算往返手续费与滑点；"
                 "原限价或结构狙击成交后立即建立生命周期，随后按真实成交对账平仓。\n" + rule["trigger"]),
     "version": "demo-frequency-validation-v191"}
    if rule["version"] == "demo-frequency-validation-v185" else rule
    for rule in STRATEGY_RULES
)

CHANGELOG: tuple[dict[str, object], ...] = (
    {"version": "0.7.341", "date": "2026-09-30", "changes": (
        "账户05独立0.01张、解套池普通槽位及极值反手小单不再因1分钟或5分钟MA5反向平掉亏损；盈利配对、解套池覆盖和极值反手释放亏损单的自动出口停用，浮亏批次保持持仓。",
        "新小单盈利退出按本地开仓价及预计净收益审核，严格超过至少10点后提交只减仓限价单；限价距开仓价有利方向至少10.01点，未成交订单持续对账，不重复提交。",
        "基础仓止盈、账户风险入场门及交易所/外部平仓对账保留独立口径；历史成交不改写。",
    )},
    {"version": "0.7.340", "date": "2026-09-30", "changes": (
        "账户05小单亏损或扣费后亏损的1分钟MA5退出要求连续两根已收盘1分钟K线确认；5分钟MA5退出仍用一根已收盘5分钟K线。",
        "盈利止盈提交前继续严格超过10点且预计本地净收益为正；保留现有极值结构与5分钟MA5风险出口。",
        "解套利润池点击结构表头按当前列表已实现本地净收益分组排序，再次点击反向排序；未平仓记录不计入结构收益。",
    )},
    {"version": "0.7.339", "date": "2026-09-30", "changes": (
        "解套利润池并列展示开仓时间和平仓时间；点击任一时间表头按该时间从近到远排序，未平仓记录排在平仓时间列表末尾。",
    )},
    {"version": "0.7.338", "date": "2026-09-30", "changes": (
        "解套利润池用最左侧单列展示时间，点击时间单元格可在开仓时间与平仓时间间切换。",
        "账户05利润型小单止盈须按本地该笔开仓价计算严格超过至少10个价格点，并在提交退出前按当前市价复核；亏损风险退出与解套池覆盖退出保持可用。",
        "小单止盈点数设置最低为10；旧版低于10的保存值安全提升到10，不影响更高设置。",
    )},
    {"version": "0.7.337", "date": "2026-09-29", "changes": (
        "独立1分钟超级趋势支撑回收追多现在只读取1分钟运行K线，不再等待或检查5分钟阴线、5分钟趋势、5分钟覆盖或5分钟支撑。",
        "要求1分钟运行价格先下穿或探入当前支撑线，随后在阴线末端轻微回收、刚转阳或收回支撑线时快速触发；普通上涨后段贴线不触发。",
        "解套利润池列表把极值反手及槽位策略标识显示为中文；普通槽位仍固定0.02张，极值反手数量规则保持独立。",
    )},
    {"version": "0.7.323", "date": "2026-09-29", "changes": (
        "新增5分钟超级趋势压力线附近的上涨K线即时反抽追空：运行中高点接近压力线并出现轻微回收即可触发，不等待阴线收盘。",
        "新增1分钟超级趋势运行中首次翻到支撑侧的底部快速追多，保留底部位置与5分钟支撑区域约束，不允许上涨后段跟随线追多。",
        "原有收盘确认、5分钟覆盖和反转补漏入口继续保留，独立规则可在同一准确位置按现有防重与槽位规则运行。",
    )},
    {"version": "0.7.322", "date": "2026-09-29", "changes": (
        "修复超级趋势支撑追多在上涨后段重复触发：1分钟跟随支撑线单独上移不再构成追多条件。",
        "只允许底部附近的1分钟超级趋势首次翻多，或当前价格确实回到5分钟超级趋势支撑附近时触发支撑追多；沿趋势的多次回踩仍可触发，远离5分钟支撑的高位小回落不再追多。",
        "空头底部保护与多头支撑入场共用真实底部/5分钟支撑区域判断；离开底部支撑区后不再因1分钟支撑线跟随上移而禁空。",
    )},
    {"version": "0.7.321", "date": "2026-09-29", "changes": (
        "修正v0.7.320过宽的多头方向锁：仅当1分钟超级趋势在支撑侧且价格仍处于底部支撑保护带时暂缓空头，避免底部附近沿旧压力线继续追空。",
        "保护带按1分钟ATR的1.5倍与价格0.16%两者取大；价格明显离开支撑带后恢复高位拒绝、局部顶部和其他空头规则评估，多头回踩规则保持不变。",
    )},
    {"version": "0.7.320", "date": "2026-09-29", "changes": (
        "账户05新增1分钟超级趋势底部翻到支撑侧后的多头方向锁：收盘超级趋势维持支撑侧期间，暂停压力线反抽空、首次翻空、高位拒绝、反转三阶段空单、趋势追空和极值反手空。",
        "支撑回踩多、既有多头信号及原有槽位/结构防重规则继续运行；1分钟超级趋势收盘翻回空头侧后解除方向锁。",
    )},
    {"version": "0.7.319", "date": "2026-09-29", "changes": (
        "账户05新增独立1分钟超级趋势红色压力线反抽追空：下跌侧运行K线反抽至压力线附近并初始转弱即可触发，不要求5分钟覆盖或MA5确认。",
        "与首次翻空局部反转、5分钟高位拒绝及旧顶部反转各自防重；每个新回抽结构可再触发，保留同5分钟上限与72槽位风险门。",
    )},
    {"version": "0.7.318", "date": "2026-09-29", "changes": (
        "账户05新增独立1分钟超级趋势首次翻空局部反转做空：运行K线在新压力线附近初始回落即可触发，不等5分钟45%覆盖或MA5确认。",
        "超级趋势翻空可与5分钟高位拒绝早触发追空在同一根5分钟K线各下一笔；保留组内防重、槽位容量及原风险门。",
    )},
    {"version": "0.7.317", "date": "2026-09-29", "changes": (
        "账户05收紧超级趋势支撑追多：1分钟运行K线低点须贴近支撑，且只允许阴线末端或刚转阳的小幅回收；拦截04:26一类离线较远的迟到追多。",
        "独立入口仍不要求5分钟阳线覆盖前阴线或1分钟阳线上穿MA5；保留与原趋势追多各自触发、组内防重及解套利润池风险门。",
    )},
    {"version": "0.7.316", "date": "2026-09-29", "changes": (
        "账户05超级趋势支撑早触发追多改为1分钟阴线下端靠近底部支撑并即时回收即可触发，不等阳线覆盖或上穿MA5。",
        "超级趋势支撑追多与原上涨趋势回踩追多独立，同侧同5分钟各允许一笔；各组内部仍防重并保留解套利润池风险门。",
    )},
    {"version": "0.7.315", "date": "2026-09-29", "changes": (
        "账户05新增1分钟超级趋势首次翻空补漏做空：运行中5分钟阴线覆盖最近有效阳线实体45%即可提前触发，跳过中间十字线。",
        "超级趋势首次翻空与顶部反转三阶段独立：同侧同5分钟各允许一笔，组内仍防重，并保留解套利润池容量和风险门。",
    )},
    {"version": "0.7.314", "date": "2026-09-29", "changes": (
        "账户05新增独立5分钟超级趋势首次翻空追空：运行中5分钟从上涨侧切换至下跌侧，1分钟即时转弱时可使用解套利润池空侧槽位，不要求5分钟阴线覆盖45%。",
        "按翻空前绿色支撑线限制追单距离；横盘频繁翻转时等待，同侧同5分钟防重与既有风险门保持有效。",
    )},
    {"version": "0.7.313", "date": "2026-09-29", "changes": (
        "账户05高位拒绝早触发追空提前识别5分钟首段回落：降低运行中5分钟最小回落幅度，使1分钟近顶阴线更早触发。",
        "继续保留1分钟距峰值过远不追空、同5分钟防重、解套利润池容量及既有风险门。",
    )},
    {"version": "0.7.312", "date": "2026-09-29", "changes": (
        "账户05新增5分钟局部高位运行中拒绝早触发追空：先前有效阳线上冲、夹十字线后，5分钟近顶回落且1分钟即时转弱即可使用解套利润池空侧槽位。",
        "独立入口不等待5分钟收盘覆盖45%、1分钟下穿MA5或双周期正式下跌趋势确认；保留近顶距离、波动幅度、同5分钟防重及既有风险门。",
    )},
    {"version": "0.7.311", "date": "2026-09-29", "changes": (
        "账户05大极值反手去掉15分钟极值硬门槛；运行中的1分钟和5分钟快速极值共同确认即可触发。",
        "5分钟使用运行中K线的当前极值与此前已收盘波动基准，快速冲顶/探底时不再等待5分钟收盘；同一5分钟极值事件只执行一次，保留既有批量释放与风险门。",
    )},
    {"version": "0.7.310", "date": "2026-09-29", "changes": (
        "账户05新增5分钟MA20与上涨订单块重合区回踩早触发追多：5分钟连续站上MA20后回踩重合区，1分钟运行K线出现轻微止跌回收即可进入解套利润池槽位。",
        "此入口不等待5分钟阳线覆盖前阴线45%，也不要求1分钟阳线上穿MA5；维持同向同5分钟K线防重与既有无止损设置。",
    )},
    {"version": "0.7.309", "date": "2026-09-29", "changes": (
        "账户05新增独立超级趋势支撑附近早触发追多：运行中1分钟K线接近支撑并轻微回收即可触发，无需实际触线、MA5上穿或5分钟45%覆盖。",
        "新触发优先于反转三阶段；候选形态按优先级逐一占用，保留后续三阶段补漏机会。",
    )},
    {"version": "0.7.308", "date": "2026-09-28", "changes": (
        "优化超级趋势线震荡区方向：上涨趋势高位震荡优先局部顶部做空和反抽追空，下跌趋势末端震荡优先局部底部做多和回踩追多。",
    )},
    {"version": "0.7.307", "date": "2026-09-28", "changes": (
        "账户05新增超级趋势线频繁翻转震荡等待区，横盘期间暂停新的反转和趋势追单。",
        "新增Smart Money Concepts订单块确认：超级趋势线支撑/压力触发需与对应需求区/供应区重合或接近。",
    )},
    {"version": "0.7.306", "date": "2026-09-28", "changes": (
        "移除超级趋势线换线点追多/追空对MA20斜率的硬过滤；MA20走平或短时反向不再否决有效支撑回踩和压力反抽。",
    )},
    {"version": "0.7.305", "date": "2026-09-28", "changes": (
        "账户05新增1分钟/5分钟超级趋势线换线点判断：上升侧支撑回踩收回可追多，下降侧压力触及回落可提前追空。",
        "超级趋势线追空不再强制等待1分钟阴线覆盖或下穿MA5；多头回踩同时要求MA20同向上升，空头反抽要求MA20同向下降。",
    )},
    {"version": "0.7.304", "date": "2026-09-28", "changes": (
        "账户05解套利润池普通MA5止盈增加4点最低有效盈利门槛，1至3点的小盈利不再因短暂拐弯立即下车。",
        "反转候选使用较早形态锚点时改标为反转三阶段补漏，并同时记录实际开仓锚点与形态锚点。",
    )},
    {"version": "0.7.303", "date": "2026-09-28", "changes": (
        "账户05新增下跌末端深位恢复追多：1分钟与5分钟同步收复MA20、5分钟阳线覆盖有效阴线达到45%、1分钟首根有效阳线即可快速入场。",
        "深位判定允许恢复K线位于最近8根低点区域，避免必须精确回踩最低点而漏掉有效回踩追多。",
    )},
    {"version": "0.7.302", "date": "2026-09-28", "changes": (
        "账户05只读审计遇服务器校时SSL EOF/超时不再故障停止，按网络退避自动重试；恢复后重新审计，未执行任何订单重试。",
    )},
    {"version": "0.7.301", "date": "2026-09-28", "changes": (
        "账户05基础仓止盈改单遇OKX/EdgeOne HTTP 554响应超时不再直接故障停止：保留原止盈单，下一轮只读复核改单结果后再完成状态。",
    )},
    {"version": "0.7.300", "date": "2026-09-28", "changes": (
        "账户05增加无效末端追空拦截：连续单边下跌、价格远离MA5、接近近期低点且没有新反抽结构时不再开空；顶部反转和有效反抽追空继续保留。",
    )},
    {"version": "0.7.299", "date": "2026-09-27", "changes": (
        "废弃账户05局部反转的MA5/MA10方向硬门槛，恢复趋势追单和反转在价格重新站上MA5或出现小金叉时的正常触发；保留合格扩张K线的快速末端止盈优化。",
    )},
    {"version": "0.7.298", "date": "2026-09-27", "changes": (
        "账户05统一局部反转方向均线门槛：做空要求入场候选MA5>MA10，做多要求入场候选MA5<MA10；快速末端止盈增加合格扩张K线分支，可在MA5拐头前提前退出并过滤短小K线。",
    )},
    {"version": "0.7.297", "date": "2026-09-27", "changes": (
        "账户05基础仓第二阶段增加单边瀑布中的首次反抽转弱补齐：已收盘重新走弱且相对第一阶段仍在2倍ATR内时允许补仓，避免行情持续下行后始终没有补单位置。",
    )},
    {"version": "0.7.296", "date": "2026-09-27", "changes": (
        "账户05新增5分钟运行K线上影线提前反抽追空：上影线回落达到45%且1分钟阴线或MA5/MA10小死叉确认时，不等待5分钟换线，直接允许追空。",
    )},
    {"version": "0.7.295", "date": "2026-09-27", "changes": (
        "账户05下跌趋势反抽追空加入账户01兼容触发：1分钟已收盘阴线且5分钟反向实体覆盖最近有效阳线达到45%即可做空，不再要求1分钟先进入MA5内侧。",
    )},
    {"version": "0.7.294", "date": "2026-09-27", "changes": (
        "账户05小单快速末端止盈增加MA5走平或反向拐头确认；MA5仍顺势上涨/下跌时继续持有，避免21:20类趋势单过早退出。",
    )},
    {"version": "0.7.293", "date": "2026-09-27", "changes": (
        "账户05五分钟45%覆盖判断跳过连续十字线，向前寻找最近有效实体K线后计算反向实体覆盖；一分钟进入MA5内侧并出现MA5/MA10小死叉时支持反转三阶段补漏空单。",
    )},
    {"version": "0.7.292", "date": "2026-09-27", "changes": (
        "账户05趋势追单方向改为只由1分钟触发趋势与5分钟方向共同确认，移除15分钟箭头作为开仓门槛。",
    )},
    {"version": "0.7.291", "date": "2026-09-27", "changes": (
        "账户05极值反手允许1分钟运行K线在极值轻微回落或末端继续冲高时即时触发，不再等待反向K线收盘；先批量释放同方向盈利小单，再提交反手利润池小单。",
    )},
    {"version": "0.7.290", "date": "2026-09-27", "changes": (
        "账户05趋势追单改为5分钟与15分钟同向确认；上涨回踩/下跌反抽追单强制使用解套利润池72个槽位（多空各36个）。",
        "利润池趋势追单成交不挂止损，沿用现有小单MA5退出和利润池对账流程。",
    )},
    {"version": "0.7.289", "date": "2026-09-27", "changes": (
        "基础仓第二阶段不再要求相对首笔便宜0.1%；改按局部摆动回撤幅度、已收盘止跌/止涨和距回撤极值范围确认，允许上涨趋势浅回踩后在首笔价上方补满。",
    )},
    {"version": "0.7.288", "date": "2026-09-27", "changes": (
        "基础仓第二阶段独立低位回踩补满，要求至少0.1%价格改善与已收盘止跌确认，重建周期只补一次。",
        "快速冲顶反手接入实验开关，历史验证亏损，默认关闭，不宣称盈利。",
    )},
    {"version": "0.7.287", "date": "2026-09-27", "changes": (
        "增加快速冲顶候选离线研究模块及三参数27组时序回放；未通过收益门槛，未连接实盘入口。",
    )},
    {"version": "0.7.286", "date": "2026-09-27", "changes": (
        "解套利润池增加带确认的统计与余额归零：保留订单明细和未平仓槽位，以平仓时间开始新统计周期。",
        "重置操作审计旧余额；利润入账、亏损覆盖、配对结算与归零统一使用SQLite写锁，防止并发覆盖。",
    )},
    {"version": "0.7.285", "date": "2026-09-27", "changes": (
        "修正0.01张独立小单与72槽位利润池混用：普通addon固定0.01张，recovery-addon按0.05%资金换算。",
        "独立0.01张菜单按订单来源过滤，利润池排除独立小单；相同成交张数不代表同一板块。",
    )},
    {"version": "0.7.284", "date": "2026-09-27", "changes": (
        "账户05解套利润池升级为72个循环槽位，多空各36个。",
        "普通小单按运行资金0.05%保证金预算动态换算张数，向下取整；极值反手同用0.05%预算。",
        "每侧3%保证金和20%合计浮亏门、同结构及同5分钟防重复继续保留；旧批次数量不变。",
    )},
    {"version": "0.7.283", "date": "2026-09-27", "changes": (
        "账户05每次开仓提交前保存六类规则判定、原始多周期K线和均线、实际入口与原因；历史无快照订单显示证据不足。",
        "逐单分析包含基础仓、0.01张小单和0.03张大极值反手，窗口每5秒刷新。",
        "大极值反手必须是已收盘首根反向K线，同一组5分钟/15分钟极值只执行一次，防止旧极值每分钟重复开仓。",
        "历史规则与优化报告不构成未来盈利保证；未通过验证的研究结果不得宣称已优化盈利。",
    )},
    {"version": "0.7.282", "date": "2026-09-27", "changes": (
        "修复账户05真实多仓归零后，历史未保护基础仓仍阻断启动的问题。",
        "仅在交易所同向仓位确认为零时隔离旧的未保护虚拟批次，并按既定0.6%第一阶段重建缺失基础仓。",
        "交易所仍有非零仓位但不足覆盖基础仓时继续停止人工对账，避免重复叠仓。",
    )},
    {"version": "0.7.281", "date": "2026-09-25", "changes": (
        "修复基础仓止盈恢复仍把全部历史重复虚拟批次相加，导致真实多仓0.57无法覆盖本地3.15而故障停止的问题。",
        "止盈恢复与基础仓存在判断统一采用最新有效基础仓批次；旧的未保护重复批次转为reconcile_required隔离记录，不创建重复止盈。",
        "仅当前最新有效基础仓可恢复独立reduce-only止盈；不提交开仓、平仓或调整交易所持仓数量。",
    )},
    {"version": "0.7.280", "date": "2026-09-25", "changes": (
        "修复账户05历史基础仓止盈被交易所取消后长期停在filled_unprotected、每次启动都故障停止的问题。",
        "仅当交易所同方向真实仓位足以覆盖全部本地开放批次时，使用确定性客户端订单号恢复基础仓reduce-only独立止盈；不提交新开仓。",
        "真实仓位不足、恢复订单未确认活动或响应结果不确定时继续故障停止，禁止伪造已保护状态。",
    )},
    {"version": "0.7.279", "date": "2026-09-25", "changes": (
        "修复账户05历史基础仓虚拟批次累加后反复误判缺仓、重复重建，造成单方向基础仓异常累积的问题。",
        "基础仓存在判断改为交易所同向真实仓位覆盖最新有效基础仓批次；固定0.01张和普通0.1%小单仍不能冒充基础仓。",
        "多空基础仓目标统一固定为运行资金1.2%，第一阶段0.6%，剩余0.6%仅在新的同方向精准结构事件后补齐。",
    )},
    {"version": "0.7.278", "date": "2026-09-25", "changes": (
        "三周期大极值成立时，即使没有可批量释放的同方向盈利小单，也独立开一笔按运行资金0.1%计算的反手单。",
        "极值反手不再受普通18个/方向循环槽位和普通小单方向保证金上限阻断；账户级20%浮亏硬保护及极值事件去重继续保留。",
        "顶部反手做空和底部反手做多保持完全对称，基础仓与固定0.01张测试单生命周期不变。",
    )},
    {"version": "0.7.277", "date": "2026-09-25", "changes": (
        "修复基础仓重建把同方向0.01/0.03张小单误判为基础仓存在的问题。",
        "基础仓是否覆盖改为核对本地未平基础仓数量与交易所同方向总数量；数量不足时继续执行基础仓独立重建。",
        "基础仓1.2%止盈/新建生命周期、0.1%资金循环小单和固定0.01张测试单保持完全隔离。",
    )},
    {"version": "0.7.276", "date": "2026-09-25", "changes": (
        "账户05新增独立三周期大极值轮转：15分钟确认大极值背景、5分钟定位区域、1分钟已收盘反向结构触发。",
        "顶部批量释放盈利多单、底部批量释放盈利空单；最多用1至3笔盈利覆盖一笔亏损单，或由解套池覆盖一笔亏损单，基础仓永久排除。",
        "全部减仓逐笔确认后只反手一笔按运行资金0.1%保证金计算的小单；固定0.01张六类测试单规则保持独立。",
        "极值事件使用独立可恢复状态锁，同一事件只完成一次，不占用或压制原六类新一分钟结构事件。",
    )},
    {"version": "0.7.275", "date": "2026-09-25", "changes": (
        "修复账户05亏损小单已经成交退出后，最终手续费与滑点使净亏损超过解套池余额而触发recovery pool cannot cover loss故障停止。",
        "平仓前的利润池覆盖门槛保持不变；真实成交后按最终净亏损全额扣款，允许解套池显示负余额，自动执行继续运行。",
        "1至3笔盈利单配对覆盖亏损时采用相同的成交后全额记账；20%储备不用于掩盖解套池欠额。",
    )},
    {"version": "0.7.274", "date": "2026-09-25", "changes": (
        "交易主界面新增“0.01小单”菜单，独立展示账户05固定0.01张六类触发订单，不混入基础仓和历史其他尺寸小单。",
        "列表展示方向、触发身份、开平仓时间与价格、张数、订单号、手续费、本地净盈亏、持仓状态和退出原因，并汇总持仓、释放、手续费与净盈亏。",
        "本功能只读复用账户05独立小单账本，不改变下单、平仓、基础仓隔离、同向五分钟去重或新一分钟结构事件规则。",
    )},
    {"version": "0.7.273", "date": "2026-09-24", "changes": (
        "账户05六类触发产生的循环小单固定为0.01张；名义价值、保证金和占比按实时价格反算。",
        "交易所最小下单量或步长不兼容0.01张时拒绝生成订单计划；基础仓、重建仓、账本隔离、同向五分钟去重及新已收盘一分钟结构事件规则保持不变。",
    )},
    {"version": "0.7.272", "date": "2026-09-24", "changes": (
        "修复解套利润池覆盖小单时本地净亏损为零仍调用扣款，导致账户05因invalid recovery loss debit自动停止的问题。",
        "仅在实际本地净亏损大于零时扣减解套利润池；基础仓隔离、36个0.1%循环槽位及新已收盘1分钟结构事件规则保持不变。",
    )},
    {"version": "0.7.271", "date": "2026-09-24", "changes": (
        "复盘确认09:05附近顶部反抽做空和10:12至10:15底部反转做多存在漏单；移除小单必须靠近对侧总持仓均价的旧硬门槛。",
        "新出现的顶部/底部反转只要锚点属于后续5分钟K线，即使旧宽泛形态仍显示，也可作为新的已收盘1分钟结构事件；同方向同一根5分钟K线仍最多一笔。",
        "新增交易界面“解套利润池”：展示多空各18个0.1%循环槽位的开平仓价、本地虚拟盈亏、欧易部分平仓记录及两种口径差异。",
        "每笔小单退出时持久化本地毛盈亏、估算手续费、本地净盈亏及可读取的欧易已实现盈亏/手续费；历史退出单可只读回补。",
        "利润池继续只管理小单本地账本，不得用于基础仓减仓；基础仓仍只能由自身止盈单退出。",
    )},
    {"version": "0.7.270", "date": "2026-09-24", "changes": (
        "明确采用本地虚拟分仓口径：0.1%小单的盈利、亏损、1至3笔盈利配对与解套利润池，均按本地记录的该笔开仓价和实际退出价计算。",
        "欧易同方向合并仓位产生的部分平仓盈亏只属于交易所总仓记录，不反向覆盖本地小单身份，也不改变本地配对结果。",
        "解套利润池与基础仓执行永久隔离：利润池余额、盈利配对和小单退出均不得触发基础仓部分减仓。",
        "基础仓只能由自身0.5%/0.7%止盈单退出；小单改变欧易总持仓均价后，仅安全重算基础仓止盈价，不缩减基础仓数量。",
        "小单平仓入口新增类型硬校验，即使以后误传基础仓批次也会拒绝执行并停止对账。",
    )},
    {"version": "0.7.269", "date": "2026-09-24", "changes": (
        "账户05正式启用多空各18个、合计36个可循环0.1%小单槽位；槽位是同时占用上限而非必须补满，盈利退出后可复用。",
        "新增持久化解套利润池：小单净利润80%进入解套池、20%进入手续费/资金费/净增长储备。",
        "亏损小单只有在解套池足额覆盖，或1至3笔盈利小单合计覆盖亏损与估算费用时才允许退出。",
        "配对退出先逐笔确认盈利单成交，再退出亏损单；同一行情事件最多处理一组，释放2至4个循环槽位，不再连平全部历史小单。",
        "修复旧版已记录51169但无订单号的陈旧意图：仅关闭原虚拟小单，绝不误减后来重建的同方向基础仓。",
    )},
    {"version": "0.7.268", "date": "2026-09-24", "changes": (
        "修复v0.7.267启动时本地小单账本仍有未平批次、但OKX该方向实际已空仓，减仓返回51169后故障停止的问题。",
        "收到51169后必须再次读取交易所持仓；仅在确认对应方向确实为零时，才关闭该方向全部陈旧虚拟批次并继续循环。",
        "一次对账关闭同方向全部陈旧批次后，当前内存循环会跳过其余旧对象，不再连续提交无效减仓单。",
        "若刷新后该方向仍有真实仓位，仍保留故障停止，避免把交易所拒单错误地当作已经平仓。",
    )},
    {"version": "0.7.267", "date": "2026-09-24", "changes": (
        "账户05基础仓制度的趋势周期由15分钟统一改为5分钟，避免快速行情中判断滞后。",
        "5分钟明确上涨或下跌时基础仓使用0.7%止盈；5分钟震荡或方向不明确时使用0.5%止盈。",
        "新软件首次启动同样由5分钟趋势决定：上涨为多1.2%/空0.6%，下跌为多0.6%/空1.2%，不明确为0.6%/0.6%。",
        "基础仓止盈后的目标仓位、分级重建以及界面趋势说明全部同步使用5分钟判断。",
    )},
    {"version": "0.7.266", "date": "2026-09-24", "changes": (
        "账户05基础仓止盈后的重建改为两级，不再等待固定8至21秒后一次性追满。",
        "第一级只建立运行资金0.6%的防守基础仓；目标不超过0.6%时即完成，目标更大时将剩余额度持久化等待。",
        "第二级必须等同方向新的精准反转或趋势追单结构才补齐，且占用该方向当前5分钟K线唯一一次入场权限。",
        "第二级成交后立即按交易所同方向实际均价重算整组基础仓止盈；初次启动首仓规则保持不变。",
    )},
    {"version": "0.7.265", "date": "2026-09-24", "changes": (
        "账户05增加一次性95.5U亏损恢复程序；所有临时调节单均为每笔0.1%，绝不是1%。",
        "恢复第一阶段只补空方：缺失空头基础仓按对侧实际总仓重建但封顶1.2%，随后仅在空头自身均价附近且出现新结构时补到6笔0.1%。",
        "空方配平后临时开放12次多头与6次空头0.1%机会；仍执行新已收盘1分钟结构、同方向每根5分钟最多一笔及3%单边保证金上限。",
        "0.1%小单的10点盈利要求改为软目标；MA5顺势运行后确认反向拐弯并收盘穿回时，允许不足10点或小幅亏损及时退出。",
        "程序会快照本轮被套多头批次；权益恢复到100U或快照批次全部平仓后，临时恢复模式自动结束。",
    )},
    {"version": "0.7.264", "date": "2026-09-24", "changes": (
        "修复账户05在止盈单明确被时间戳拒绝后，同步OKX服务器时间遇SSL握手超时即误转为故障停止。",
        "将该情形明确标记为订单尚未建立的只读GET网络故障，按10/15/20/30秒退避自动恢复，不再停止循环。",
        "新版启动时会对账v0.7.263遗留的已声明无订单号意图：先按clOrdId查询，已存在则接管，确认不存在才使用同一clOrdId安全重发。",
        "保留POST结果不明时必须停止人工对账的安全边界，不会因本次修复而盲目重复下单。",
    )},
    {"version": "0.7.263", "date": "2026-09-23", "changes": (
        "基础仓止盈后重建改为按对侧实际总仓计算但硬性封顶1.2%，15分钟明确顺势也不再放大到1.8%。",
        "6笔0.1%调节仓不再于基础仓重建后立即消耗；只有价格到达对侧持仓均价附近0.1%或更外侧才允许。",
        "价格区域未到时仅暂缓新小单，不停止循环、不平旧仓，容量上限和20%浮亏门槛保持独立。",
    )},
    {"version": "0.7.262", "date": "2026-09-23", "changes": (
        "账本确认13:10:52为真正底部做多、13:13:05为局部底部做多；两个名称在同一根5分钟K线内绕过了旧形态锁。",
        "账户05新增方向级5分钟硬锁：同一方向所有反转与追单身份合计每根5分钟K线最多只能开一笔0.1%小单。",
        "5分钟反向实体覆盖改为只使用已收盘K线确认，运行中的瞬时阳线或阴线不再提前触发。",
        "保留各形态自身的一分钟新结构重置；方向级硬锁与形态锁同时生效，避免频繁消耗6次机会。",
    )},
    {"version": "0.7.261", "date": "2026-09-23", "changes": (
        "账户05基础仓取消同时挂0.5%和0.7%两档止盈，改为整笔基础仓只挂一根全量止盈。",
        "15分钟震荡或方向不明确时使用0.5%止盈；15分钟上涨或下跌方向明确时使用0.7%止盈。",
        "基础仓全量止盈成交后立即按对侧实际总仓位与15分钟方向动态重建，不再等待另一档止盈。",
        "修复同方向仍有0.1%小单时被误认为基础仓尚在、导致止盈后不重建的问题。",
        "旧版本已存在的0.5%/0.7%分拆止盈会自动统一到当前应采用的单一止盈比例。",
    )},
    {"version": "0.7.260", "date": "2026-09-23", "changes": (
        "核查确认12:01:28的0.1%多单被系统错误归类为上涨趋势回踩追多，根因是追单确认条件过宽。",
        "趋势追单现在要求5分钟价格进入MA5/MA10带且K线已经止跌转阳/止涨转阴，不再把仍在延续的回落或反抽当成完成。",
        "一分钟局部极值改为只使用开盘价与收盘价实体，完全排除上下影线。",
        "一分钟触发必须等待已收盘的首根反向实体K线确认；未收盘瞬时翻色不再消耗每方向6次机会。",
    )},
    {"version": "0.7.259", "date": "2026-09-23", "changes": (
        "修复账户05小单达到止盈条件后，addon_ma5_exit减仓意图被账本误判为非法类型而故障停止的问题。",
        "MA5止盈减仓意图现在可持久记录、提交、成交确认并在重启后安全对账，不需要清空既有批次。",
        "经故障日志确认，11:21及11:37附近的重复停止均由同一类型校验错误造成。",
        "新增MA5减仓完整生命周期回归测试；账户05相关测试共51项全部通过。",
    )},
    {"version": "0.7.258", "date": "2026-09-23", "changes": (
        "账户05一侧基础仓止盈后，不再固定按0.6%重开，而是读取对侧实际总持仓保证金比例动态配平。",
        "15分钟震荡或方向不明确时，缺失方向按对侧总仓位1:1重建；例如对侧1.2%，新基础仓也开1.2%。",
        "15分钟明确顺着重建方向时，在对侧总仓位基础上增加0.6个百分点；例如空侧1.2%、明确上涨时，多侧开1.8%。",
        "明确逆着重建方向时保留0.6个百分点差额，所有动态重建均受单方向3%持仓上限保护。",
        "启动首仓规则保持不变：不明确为0.6%比0.6%，上涨为1.2%比0.6%，下跌为0.6%比1.2%。",
    )},
    {"version": "0.7.257", "date": "2026-09-23", "changes": (
        "修复账户05在5秒扫描条件短暂消失后，同一分钟形态被重新解锁并扎堆重复下单的问题。",
        "同一方向、同一规则、同一根5分钟K线最多成交一次；5分钟周期内绝不重复补单。",
        "跨入新5分钟K线后，仍必须完整经历新的无信号已收盘一分钟K线并形成新结构，才允许再次开仓。",
        "保留每方向最多6笔的持仓上限，但6笔仅是容量上限，绝不作为连续补满任务。",
    )},
    {"version": "0.7.256", "date": "2026-09-23", "changes": (
        "账户05每笔0.1%小单增加独立最低盈利点数门槛，默认10点并可在界面修改。",
        "小单浮盈小于10点时，即使一分钟或五分钟MA5拐弯也继续持有，不执行止盈。",
        "浮盈达到10点后不机械平仓；只有MA5已拐弯或快速行情已到末端时才独立止盈，行情仍顺畅则继续持有。",
        "保留v0.7.255的5秒并行扫描、6根反转三阶段追单窗口、实体3对12与次高/次低漏单修复。",
    )},
    {"version": "0.7.255", "date": "2026-09-23", "changes": (
        "账户05每笔0.1%小单改为独立的1分钟→5分钟MA5顺势运行后拐弯止盈，不再使用固定10点止盈。",
        "一分钟快速下跌或快速拉升形成盈利末端、收盘价过度远离MA5时，小单提前独立止盈，不等MA5拐弯。",
        "修复次高点/次低点反转漏单：3根与12根极值只使用开盘价和收盘价组成的K线实体，完全排除上下影线。",
        "一分钟反转候选保留最多6根；当后续五分钟反向实体覆盖达到45%时仍可触发，修复09:16形态与09:17覆盖不同步的漏单。",
        "账户05改为5秒快速扫描，四周期K线并行单次拉取；反转三阶段候选追单窗口放宽到6根一分钟K线。",
        "历史固定点数小单止盈会先核对成交、确认撤销，再转入MA5本地管理；基础大仓0.5%/0.7%止盈保持不变。",
    )},
    {"version": "0.7.254", "date": "2026-09-23", "changes": (
        "修复基础大仓新旧两张全额减仓止盈重叠时，OKX自动撤销新单导致“新大仓止盈未生效”的问题。",
        "基础大仓止盈调价改用OKX原订单改单接口，在同orderId上原子修改价格，不再重叠创建全额reduce-only订单。",
        "改单固定c使用cxlOnFail=false；改单被拒绝时原止盈继续有效，查询确认新价格后才更新本地账本。",
        "v0.7.252/v0.7.253遗留的新单自动撤销记录会自动改为原止盈改价并完成恢复。",
    )},
    {"version": "0.7.253", "date": "2026-09-23", "changes": (
        "修复账户05基础止盈换单恢复时，OKX 51603（Order does not exist）被误判为致命故障的问题。",
        "查询新客户单号返回51603时，按“尚未创建”处理并使用同一客户单号安全提交。",
        "旧止盈撤单后复查返回51603时，按“旧单已不存在”完成本地换单账本。",
        "已持久化的planned换单可在重启后自动续办；原旧止盈在新单确认前不会被撤销。",
    )},
    {"version": "0.7.252", "date": "2026-09-23", "changes": (
        "账户05每笔0.1%小单开仓成交后，按同方向交易所最新持仓均价重算基础大仓0.5%与0.7%止盈。",
        "0.1%小单独立止盈完全成交后，同方向基础大仓也再按最新持仓均价重算两档止盈。",
        "换单顺序固定为新止盈提交、查询确认生效、撤销旧止盈、查询确认撤销，避免基础大仓短暂失去止盈。",
        "新增持久化止盈换单账本，进程或网络中断后会先继续未完成换单，不重复创建。",
    )},
    {"version": "0.7.251", "date": "2026-09-23", "changes": (
        "将账户05合计浮亏20%从故障停止改为动态暂停新开仓，自动循环和已有止盈对账持续运行。",
        "当多空合计未实现盈亏小于等于-20U时禁止基础补仓和0.1%小单；已有持仓与独立止盈保持管理。",
        "止盈成交或行情恢复使合计浮亏重新大于-20U后，自动解除暂停并继续接收新形态。",
    )},
    {"version": "0.7.250", "date": "2026-09-23", "changes": (
        "修复账户05达到6笔小单上限时被误报为“浮亏达到20%”并故障停止的状态混用问题。",
        "6笔数量上限或单方3%保证金上限现只暂停新增该笔订单，账户05自动循环继续运行。",
        "20%风控严格使用多仓upl+空仓upl的USDT合计值；100U运行资金只在合计未实现盈亏小于等于-20.00U时停止。",
        "界面停止原因现显示实际合计未实现盈亏、USDT阈值和运行资金，便于核对。",
    )},
    {"version": "0.7.249", "date": "2026-09-23", "changes": (
        "修复账户05同一持续反转/追单形态因一分钟锚点变化而重复建仓的问题。",
        "新增持久化形态生命周期锁：从未成立到成立只下一笔，持续成立不重复，失效后重新形成才可追加。",
        "每个方向六笔0.1%是同时未平仓上限，不是需要补满的下单任务；独立止盈只释放一个名额。",
    )},
    {"version": "0.7.248", "date": "2026-09-23", "changes": (
        "修复账户05公开K线、持仓快照或止盈查询遇到SSL握手超时后仍会故障停止的问题。",
        "账户05所有安全GET读取现按10/15/20/30秒退避持续恢复，恢复前不评估信号也不下单。",
        "订单POST结果不明、批次账本异常和20%浮亏风控仍会立即停止，防止重复下单。",
    )},
    {"version": "0.7.247", "date": "2026-09-23", "changes": (
        "修复账户05专用自动线程在只读审计连续5次SSL握手超时后直接故障停止的问题。",
        "启动、杠杆复核和运行阶段的只读审计现按10/15/20/30秒退避持续重试，连接恢复后才进入策略评估。",
        "网络恢复前账户05不进入策略评估且不下单；POST结果未知、账本异常和风险熔断仍保持故障停止。",
    )},
    {"version": "0.7.246", "date": "2026-09-22", "changes": (
        "账户05启用专用自动实盘线程，不复用账户01至03固定张数和止损执行器。",
        "六类0.1%小单触发身份独立实现：局部底/顶、真正底/顶、上涨回踩追多、下跌反抽追空。",
        "基础双向仓按15分钟结构配置2:1、1:2或1:1，并拆分0.5%与0.7%两档独立减仓止盈。",
        "小单按可配置固定点数逐批止盈；同一身份、方向和锚点K线严格去重，每方向最多六笔。",
        "多空合计浮亏达到运行资金20%时冻结所有新风险并停止自动循环，不设置止损、不自动平仓。",
    )},
    {"version": "0.7.245", "date": "2026-09-22", "changes": (
        "修复账户05重新绑定API时错误复用旧凭据导致持续HTTP 401；保存操作现始终验证当前输入框内容。",
        "账户05新增可持久化运行资金与小单止盈点数，默认100 USDT与10点。",
        "账户05新增15分钟趋势结构、反转小单及顺势追单的公开行情观察。",
        "账户05小单使用独立批次账本和独立减仓止盈身份；自动实盘执行仍保持锁定。",
    )},
    {"version": "0.7.244", "date": "2026-09-21", "changes": (
        "主界面新增账户04与账户05，两个账户各自使用独立API凭据、strategy.sqlite3和state.sqlite3。",
        "账户04定位为第三方量化策略观察账户，账户05定位为独立复刻与优化策略预留账户。",
        "本版仅开放账户04/05的API绑定、加密保存和只读审计；界面、候选扫描和后台线程三层同时锁定自动下单。",
        "五个账户禁止复用同一API Key，新账户不与账户01–03共享数据库、运行开关或策略状态。",
    )},
    {"version": "0.7.243", "date": "2026-09-19", "changes": (
        "收紧次高点定义：第一高点必须位于三均线及MA20上方，且MA5大于MA10大于MA20并具有可测发散宽度。",
        "次高点必须仍位于同一多头发散三均线上方，并低于第一高点但距离不超过2倍一分钟ATR，确认顶部小幅走弱。",
        "第一高点无资格、次高点跌入均线或两高点距离过大时，五秒快空直接拒绝。",
    )},
    {"version": "0.7.242", "date": "2026-09-19", "changes": (
        "修复五秒快空热路径只读取一分钟形态、遗漏五分钟位置过滤的问题。",
        "所有快空必须同时位于一分钟和五分钟MA20上方；次高点快空必须仍在两个周期MA5、MA10、MA20三线之上。",
        "跌破任一组三均线、进入下跌末端、支撑区或底部反弹后禁止新开空；一分钟与五分钟行情并发获取以保留五秒预算。",
    )},
    {"version": "0.7.241", "date": "2026-09-19", "changes": (
        "修复22:15附近未收盘一分钟K线盘中短暂翻阴触发快空、随后收回阳线造成反弹途中做空。",
        "次高点快空须连续两次一秒扫描保持阴线转弱，瞬时翻阴后立即转阳不再下单。",
        "连续第2至3根阴线只能继承第一根在顶部已经成立的信号，禁止跌至支撑或局部底部后重新生成迟到空单。",
    )},
    {"version": "0.7.240", "date": "2026-09-19", "changes": (
        "五秒快空新增高位次高点反抽转弱分支，不再把阴线吞没前阳线或下穿一分钟MA5作为必需条件。",
        "次高点第一根转弱阴线即可执行；连续2至3根阴线仍保留补偿触发，以覆盖没有可吞没前阳线的连续下跌形态。",
        "同一轮连续阴线共用局部高点锚点和近端结构止损，避免等待MA5后止损扩大，并防止同一形态重复下单。",
    )},
    {"version": "0.7.239", "date": "2026-09-19", "changes": (
        "修复19:25五秒快空在19:28被未成交底部候选生成的多头趋势锁提前平仓。",
        "五秒快空不再接受仅由错过候选生成的相反趋势锁接管；只有真实相反试单已获OKX接受，或1分钟MA5先下行后正式上拐，才允许平空。",
        "保留服务器三根1分钟K线局部高点止损；相反方向候选仍记录审计，但未成交不得越过持仓止盈规则。",
    )},
    {"version": "0.7.238", "date": "2026-09-19", "changes": (
        "拆出独立1秒顶部转弱快执行线程，不再等待完整策略审计和主循环10秒间隔。",
        "首根阴线覆盖前阳线至少45%且回到1分钟MA5内侧后直接做空；止损放最近三根1分钟K线最高点外。",
        "每个局部顶部按锚点时间独立编号，前一次错过或未成交不消耗下一次机会；同一顶部禁止重复下单。",
        "订单记录识别至OKX回执毫秒数和5秒预算结果；POST结果未知时立即停止自动执行并要求对账。",
    )},
    {"version": "0.7.237", "date": "2026-09-19", "changes": (
        "修复17:57顶部局部反转空单：18:09与18:11一分钟MA5正式拐弯止盈被手续费盈利门连续否决。",
        "一分钟或接管周期MA5完成先顺势运行、再反向拐弯后，按趋势失效直接退出，不再等待覆盖手续费或最低净利润。",
        "手续费盈利门继续约束普通提前锁利；对向反转接管仍保留为最后兜底。",
        "策略升级为demo-frequency-validation-v237。",
    )},
    {"version": "0.7.236", "date": "2026-09-19", "changes": (
        "修复17:40底部反转三阶段多单过早止盈：成交后先由一分钟MA5管理，连续两根已收盘5分钟K线站稳MA20上方后才交给五分钟MA5。",
        "MA5止盈必须先有顺势斜率，再发生真实反向拐弯；已经向下的五分钟MA5不再被误判为刚刚拐弯。",
        "正常策略生命周期改为跨版本识别，应用升级后旧版本持仓不会再被结构狙击通用早退越权平仓。",
        "行情与账户快照并发获取，账户八路快照和九路行情内部并发；状态库结构初始化只在进程首次执行，记录5秒核心预算是否达标。",
        "策略升级为demo-frequency-validation-v236。",
    )},
    {"version": "0.7.234", "date": "2026-09-19", "changes": (
        "修复普通策略多单被结构狙击模块越权平仓：已开放生命周期持仓只由自身退出链管理。",
        "一分钟MA5微观规则不再抢先触发声明为五分钟MA5拐弯管理的正常持仓。",
        "持仓归属采用轻量只读查询，避免在下单热路径重复初始化状态库。",
        "策略升级为demo-frequency-validation-v234。",
    )},
    {"version": "0.7.233", "date": "2026-09-19", "changes": (
        "实时执行链新增行情/信号、账户模式、初始快照、挂单整理/结构狙击及决策执行分阶段毫秒耗时事件。",
        "结构狙击仅返回armed且账户未变化时复用当前安全快照，减少一次重复账户请求。",
        "整点60根K线漏单回放改为完成决策后的独立后台线程，不再占用下单热路径。",
        "整点列表将一分钟底部冻结阶段标记为研究证据，不再误报为已满足下单资格的漏单。",
        "策略升级为demo-frequency-validation-v233。",
    )},
    {"version": "0.7.232", "date": "2026-09-19", "changes": (
        "修复12:30急拉诱多：市价追高被MA5距离门拒绝后，不再让已反转的实时1分钟/5分钟K线触发滞后MA5回踩多单。",
        "实时价已到达或穿过原挂价时直接作废后备限价，避免行情回落时被动接多。",
        "策略升级为demo-frequency-validation-v232。",
    )},
    {"version": "0.7.231", "date": "2026-09-19", "changes": (
        "修复10:35顶部第一阶段误空：15分钟/1小时空头背景不得覆盖当时仍看多的已收盘5分钟方向。",
        "顶部第一阶段新增一分钟真实转弱门槛：阴线须回到MA5下方且MA5停止上升；仍在MA5上方或MA5向上时继续观察。",
        "更新的5分钟底部半覆盖做多事件会立即作废旧顶部第一阶段空单，防止底部反弹途中追空。",
        "策略升级为demo-frequency-validation-v231。",
    )},
    {"version": "0.7.230", "date": "2026-09-19", "changes": (
        "同一段行情内每个新的1分钟局部低点/高点都建立独立三阶段事件号；第一次未成交、被拒绝或已失效不会消耗第二次机会。",
        "MA5收复、恢复同向K线和MA5/MA10交叉只绑定到其前最近的同向局部极值，不再被30分钟窗口内更早的冻结事件占用。",
        "试单去重改为按具体局部极值时间识别；新形态条件成立且没有同向持仓/挂单时，可再次触发真实下单。",
        "策略升级为demo-frequency-validation-v230。",
    )},
    {"version": "0.7.229", "date": "2026-09-19", "changes": (
        "修复底部局部反转做多三阶段漏单：第二阶段收复MA5、锁存第二阶段恢复及第三阶段MA5/MA10金叉不再重复要求1m与5m同时位于MA20下方。",
        "已确认的三周期局部反转保留原执行资格；15分钟同向趋势仅作回踩背景，不再将候选改判为旧策略仅观察。",
        "首次5分钟覆盖冻结后，三阶段执行使用冻结的局部保护价，禁止后续4/6/12根K线将止损扩大并在入场前误拒单。",
        "策略升级为demo-frequency-validation-v229。",
    )},
    {"version": "0.7.228", "date": "2026-09-19", "changes": (
        "恢复5分钟颜色与均线转折独立执行链：原上涨排列顶部失速、已收盘阴线跌破MA5/MA10且MA5转下后，由一分钟首次跌破MA5或MA20精确触发做空。",
        "该分支属于已确认的5分钟趋势转折/下跌启动，不再被普通顶部反转的相邻实体45%覆盖门重复阻断。",
        "5分钟、15分钟负责转折背景与方向；5分钟三均线外沿和已收盘变色负责确认；一分钟三均线末端转弱负责入场与局部止损。仍禁止首次穿越后超过0.35 ATR的迟到追空。",
        "策略升级为demo-frequency-validation-v228。",
    )},
    {"version": "0.7.227", "date": "2026-09-19", "changes": (
        "修复回踩追多用已收盘信号价通过MA5位置门、下单时最新价已冲到局部顶部却仍追价的问题。",
        "所有趋势追单在提交前重新核对实际执行价：相对原信号及一分钟MA5任一顺向远离超过0.35 ATR即取消市价追单。",
        "修复1分钟+5分钟趋势锁被误标成15分钟趋势来源；只有真实15分钟同向锁才显示为上一级15分钟趋势。",
        "策略升级为demo-frequency-validation-v227。",
    )},
    {"version": "0.7.226", "date": "2026-09-19", "changes": (
        "修复新顶部累计覆盖已冻结、进入独立执行链后，又被通用相邻五分钟覆盖门重复否决的漏单。",
        "独立顶部最多三根五分钟阴线累计达到45%后直接复用同一冻结证据；后续阴线相邻阴线导致即时覆盖显示0%时，不再要求重复制造覆盖。",
        "仍保留顶部新鲜度、局部止损、确认后最多0.35 ATR追价、最近支撑空间、手续费与账户安全门；策略升级为demo-frequency-validation-v226。",
    )},
    {"version": "0.7.225", "date": "2026-09-18", "changes": (
        "新鲜一分钟顶部建立独立冻结链；随后最多三根五分钟阴线累计覆盖前一阳线实体45%即可冻结，不因末根阴线相邻的也是阴线而丢失覆盖证据。",
        "独立顶部覆盖使用该顶部之后的一分钟最高点外缓冲止损，并优先进入受控顶部试空；旧上涨锁和同轮回踩追多候选只作背景，不能覆盖新顶部事件。",
        "修复一分钟阶段时间一端带时区、一端无时区时被误判为‘时间无效’的问题；统一转为UTC后再核验90秒新鲜度。",
        "保留确认后最多0.35 ATR追价、最近支撑空间、手续费与账户安全门；策略升级为demo-frequency-validation-v225。",
    )},
    {"version": "0.7.224", "date": "2026-09-18", "changes": (
        "持续上涨中，已由趋势锁和新鲜1分钟MA5/MA10回踩确认的追多，不再因五分钟MA20滞后超过固定1 ATR而漏单。",
        "继续核对局部结构止损、最近压力至少2点空间及确认后最多0.35 ATR追价，避免把取消MA20距离误解为无条件追高。",
        "三层上限只约束当前同时持有的同向仓位；仓位全部平掉后层数归零，每个新的独立回踩可继续参与，不累计历史成交次数。",
        "策略升级为demo-frequency-validation-v224；运行中的旧实盘程序不会热更新。",
    )},
    {"version": "0.7.223", "date": "2026-09-18", "changes": (
        "修复上级上涨趋势把局部顶部的一分钟转强误标为回踩追多：趋势续单恢复30根一分钟K线位置门。",
        "回踩追多只允许位于最近局部区间68%以下，反抽追空只允许位于32%以上；真正顶底反转仍走独立规则。",
        "位置门在趋势重分类之前执行，禁止用‘上级趋势延续’覆盖顶部追多/底部追空风险。",
        "策略升级为demo-frequency-validation-v223；运行中的旧实盘程序不会热更新。",
    )},
    {"version": "0.7.222", "date": "2026-09-18", "changes": (
        "五分钟盘中实体覆盖首次达到45%时持久记录检测时间、K线时间和局部止损，同一根K线只记录一次。",
        "九路公开行情读取改为最多三路并发，缩短一分钟顶部候选进入执行评估的等待。",
        "顶部三阶段信号错过市价窗口后，仅在三分钟内且局部止损、最近支撑及成本空间均通过时挂一次五分钟MA5反抽限价；不放宽市价追空门槛。",
        "策略升级为demo-frequency-validation-v222；运行中的旧实盘程序不会热更新。",
    )},
    {"version": "0.7.221", "date": "2026-09-18", "changes": (
        "上涨回踩的5分钟结构允许MA5暂时回落；仍须已收盘K线站上上行MA20、MA5高于MA10且回踩低点抬高。",
        "一分钟回踩续单须有最近3根K线触及MA5/MA10、最新收盘转强且不超过MA5上方0.35 ATR。",
        "继续执行局部结构止损、最近阻力空间及账户风控；策略升级为demo-frequency-validation-v221。",
    )},
    {"version": "0.7.220", "date": "2026-09-18", "changes": (
        "先按已收盘5分钟上涨结构识别回踩续单，再决定是否适用底部反转首单的双周期MA20下方门；旧下降锁在上涨接管后不再压制新鲜回踩。",
        "上涨回踩必须有新鲜1分钟MA5/MA10边沿触及与近MA5转强；确认后可用1m/5m各2票、15m至少1票替代延续单合计6票的等待。",
        "对已确认且未远离MA5的上涨回踩，不再用距离5分钟MA20固定1 ATR否决；仍保留局部追价、结构止损、最近阻力至少2点空间及账户风控。",
        "策略升级为demo-frequency-validation-v220，旧实盘版本不会热更新。",
    )},
    {"version": "0.7.219", "date": "2026-09-18", "changes": (
        "反转区漏单菜单增加整点回放板块；每小时只扫描前一小时完整60根已收盘一分钟K线，并保存候选机会漏单与发单异常供复盘。",
        "实盘发单前记录全部入场门槛已通过及客户端订单号；仅收到有效交易所订单号才记为提交成功，失败时保留订单号用于对账。",
        "回放只做历史审计，不据后续涨跌补发实时订单；策略升级为demo-frequency-validation-v219。",
    )},
    {"version": "0.7.218", "date": "2026-09-18", "changes": (
        "修正v0.7.217双周期MA20底部门误用于独立确认的上涨趋势回踩追多；底部反转首单仍须双周期在MA20下方。",
        "激进型自动循环故障停止时把完整Python堆栈写入对应账户本地日志并在界面显示路径，便于精确定位截图中的None解包故障；故障仍保持停机，不自动重试下单。",
        "补充范围回归与异常记录测试；策略升级为demo-frequency-validation-v218。",
    )},
    {"version": "0.7.217", "date": "2026-09-17", "changes": (
        "修复22:54过早做多：该单一分钟在MA20下方，但五分钟收盘2465.09仍高于MA20 2459.19，五分钟回升仅覆盖23.5%，不应把底部小反弹改名为上涨趋势回踩。",
        "底部反转来源的首笔做多新增双周期位置门：触发锚点必须同时位于一分钟MA20和五分钟MA20下方，否则继续观察。",
        "该限制只用于底部反转首单；已经独立确认的上涨趋势回踩追多继续按趋势回踩规则执行。策略升级为demo-frequency-validation-v217。",
    )},
    {"version": "0.7.216", "date": "2026-09-17", "changes": (
        "核查22:32漏空：一分钟局部顶部、跌破MA5、15分钟转空证据与小止损追空均已形成，旧版最终被4.09点利润门拒绝；现按2.00点门槛执行。",
        "补齐局部双周期MA5反抽追空/回踩追多的趋势身份继承：已有同向方向锁时直接按趋势追单，不再错误回到五分钟相邻阳线/阴线45%覆盖门。",
        "45%覆盖只用于严格顶部/底部反转确认；趋势追单及连续同色五分钟K线延续不强制覆盖前一根反向K线。策略升级为demo-frequency-validation-v216。",
    )},
    {"version": "0.7.215", "date": "2026-09-17", "changes": (
        "利润空间规则改为大于或等于2.00点即可通过最终准入；结构止损、仓位、重复单、持仓和账户风险继续独立核对。",
        "符合新鲜完整下单形态时，旧方向锁退到形态质量、位置、止损和风险审查之后，不再在冻结候选阶段提前删除信号。",
        "顶部做空与底部做多、反抽追空与回踩追多使用同一后置方向锁规则；策略升级为demo-frequency-validation-v215。",
    )},
    {"version": "0.7.214", "date": "2026-09-17", "changes": (
        "修复21:40与22:05回踩追多漏单：新鲜一分钟局部底部与15分钟上涨趋势同向时，先按上级趋势回踩追多保留候选，旧五分钟空头锁不再提前删除。做空镜像支持15分钟下降趋势反抽追空。",
        "趋势追单身份在覆盖率与区间位置审查前完成，不要求一分钟重新穿越MA5，不要求五分钟覆盖45%，也不套用严格反转锚点边缘门；结构止损、ATR、手续费、利润空间、重复单和账户风险仍保留。",
        "修复旧一分钟方向锁失败后同一端点在同一次核验中立即复活的抖动；利润空间加入最多0.02点或估算成本2%的报价容差，避免4.00点对4.02点的边界误杀。策略升级为demo-frequency-validation-v214。",
    )},
    {"version": "0.7.213", "date": "2026-09-17", "changes": (
        "新增下降趋势连续阴线续单：保留已确认的反抽追空方向，连续两根已收盘五分钟阴线降低后，新五分钟换线前两分钟继续走阴且一分钟同步走弱即可形成新机会。",
        "该分支不要求五分钟阴线覆盖前一根阳线；每个五分钟周期独立去重，继续核对反向新鲜底部、结构止损、ATR、手续费、利润空间和账户风险。",
        "修复21:05之后持续下跌只能等待新反抽、无法利用五分钟连续阴线续跌的问题。",
    )},
    {"version": "0.7.212", "date": "2026-09-17", "changes": (
        "修复21:10迟到空单：一分钟新鲜反向局部转折出现后，旧趋势追单不得重新释放，等待反转接管成立或结构失效。",
        "保留趋势反抽追空与回踩追多的提前入口，不新增一分钟穿越MA5或五分钟覆盖45%的硬门槛。",
        "新增late_continuation_blocked_by_fresh_opposite_turn审计事件，运行日志明确显示被阻止的旧方向与待接管方向。",
    )},
    {"version": "0.7.211", "date": "2026-09-17", "changes": (
        "修复20:31高位多单：PineTS、Webhook、Edge外部信号恢复为只读研究证据，不能覆盖本地策略方向或独立触发实盘订单。",
        "修复19:48底部反转漏单：五分钟确认耗时不再被固定90秒误杀；确认后价格仍在0.35 ATR有效位置时允许最多180秒完成提交。",
        "保留上涨趋势高位防追单：价格远离五分钟MA20或确认后继续运行过远时仍拒绝追多。",
        "运行审计新增external_signal_research_only记录，明确外部事件未取得下单权限。",
    )},
    {"version": "0.7.210", "date": "2026-09-17", "changes": (
        "实盘界面统一为账户01、账户02、账户03，三个账户均可独立运行同一激进型策略基线。",
        "三个账户的API、策略数据库、状态数据库、风控、启停与下单数量相互隔离。",
        "新增共享策略与单账户策略覆盖层；运行状态显示实际策略来源。",
        "每个账户可保存0.01至1.00张、按0.01递增的独立下单数量；修改需先停止该账户。",
    )},
    {"version": "0.7.209", "date": "2026-09-17", "changes": (
        "新鲜一分钟底部或顶部端点出现且极值未破时，立即暂停相反方向旧趋势追单；待MA20确认后再正式替换持久趋势锁。",
        "上涨趋势回踩追多补齐与下跌趋势反抽追空完全镜像的提前入口：局部低点首根阳线转强即可执行，不等待上穿MA5或五分钟覆盖45%。",
        "小止损反转和趋势追单取消固定3点利润门，改用1.2R、0.6倍一分钟ATR和1.5倍估算往返成本三者取高；普通订单继续使用更严格门槛。",
        "策略升级为demo-frequency-validation-v213；重复单、持仓、止损方向、利润空间和账户安全检查继续保留。",
    )},
    {"version": "0.7.208", "date": "2026-09-16", "changes": (
        "修复PineTS、Webhook等外部事件编号含连字符时，客户订单编号在本地校验阶段被拒绝并停止自动执行的问题。",
        "外部订单编号统一规范为ASCII字母数字，并对原始event_id加入稳定短哈希；相同事件保持同一编号，仅标点不同的事件也不会碰撞。",
        "主订单、移动止盈和附带保护单统一保留32字符上限；校验失败仍发生在POST前，不会形成未知订单结果。",
        "策略升级为demo-frequency-validation-v212；趋势追单与局部反转规则不变。",
    )},
    {"version": "0.7.207", "date": "2026-09-16", "changes": (
        "上一级已确认趋势锁与当前局部转折同向时，优先采用反抽追空或回踩追多身份，即使同时存在严格反转候选。",
        "趋势追单不要求一分钟穿越MA5，也不要求五分钟相邻实体覆盖45%；反转证据只作辅助说明，不再抢占趋势准入。",
        "做多做空完全镜像；继续保留新鲜局部结构、止损方向、利润空间、重复单、挂单、持仓和账户风险检查。",
    )},
    {"version": "0.7.206", "date": "2026-09-16", "changes": (
        "修正20:24迟到空单：快速反转从信号到实际核验最多允许90秒和0.35倍一分钟ATR，超限明确拒绝追空或追多。",
        "运行日志分别显示信号价、核验价、运行点数、ATR、年龄、盘中五分钟覆盖和已收盘三阶段覆盖，避免0%与99%证据混写。",
        "旧顶部/底部配对不得把十分钟后的趋势反抽/回踩订单事后改名为真正顶底；后续机会保持趋势追单身份。",
        "继承v0.7.205的固定API域名HTTPS DNS备用解析。",
    )},
    {"version": "0.7.205", "date": "2026-09-16", "changes": (
        "修复本机DNS把固定OKX访问域名解析为0.0.0.0时，签名API只会反复掉线的问题。",
        "直连失败时仅为锁定的API域名通过HTTPS DNS取得公共IP，并继续使用原域名完成TLS证书校验；只读GET恢复后仍先完整审计账户、持仓、保护单和风险状态。",
        "订单POST继续保持单次提交，未知结果绝不盲目重试；代理变化仍在下一次请求生效。",
    )},
    {"version": "0.7.204", "date": "2026-09-16", "changes": (
        "修复16:12至16:15一分钟底部阶段仍被旧下降锁否决：最近10分钟内已冻结的同向五分钟45%覆盖可供新鲜一分钟阶段复用，前提是结构止损未被触及。",
        "局部顶底的旧锁交接以一分钟新形态和五分钟覆盖为执行依据；十五分钟、一小时仅作背景，保留成交量、利润空间及账户风控。",
    )},
    {"version": "0.7.203", "date": "2026-09-16", "changes": (
        "新鲜一分钟局部顶底与实时五分钟反向实体覆盖达标时，可对称释放更早的相反方向锁；十五分钟与一小时作为背景，不再单独否决。",
        "顶部空单进入一分钟MA5内侧后仍按已确认局部反转候选继续核对位置、实体止损、利润空间和账户风控。",
    )},
    {"version": "0.7.202", "date": "2026-09-16", "changes": (
        "修复旧五分钟顶部覆盖锚点在一分钟局部低位恢复做空的问题，恢复单须重新位于当前一分钟实体区间相应入场半区。",
        "一分钟最多6根、五分钟最多3根只作反转形态观察窗，按最近相反实体的实际价格覆盖判断，不再把窗口内阴阳实体分别相加为282%等伪覆盖。",
        "用14:27至14:38原始K线回放验证错误空单被拦下；其他订单、风险和持仓检查不变。",
    )},
    {"version": "0.7.201", "date": "2026-09-16", "changes": (
        "修复局部高低点候选归类为反抽追空/回踩追多后仍误套五分钟45%覆盖门、导致整点附近有效候选漏单的问题。",
        "新增最近30天720个北京时间整点的离线多周期复盘表；整点前已收盘上级结构与整点后0至5分钟实体触发窗分别记录。",
        "整点只作为反转或趋势追单的增强证据；重复单、挂单、实体止损、利润空间和账户风险门继续保留。",
    )},
    {"version": "0.7.200", "date": "2026-09-16", "changes": (
        "修复v0.7.199自动循环启动时提前读取五分钟局部反转与端点覆盖标志导致UnboundLocalError、激进型自动总闸故障停止的问题。",
        "两个主入场门标志在候选归类前显式初始化；新增启动顺序回归检查，v0.7.199的快速入场规则保持。",
    )},
    {"version": "0.7.199", "date": "2026-09-16", "changes": (
        "三周期顶部反转做空解除只观察硬编码；新鲜1分钟与5分钟反转达到45%后直接替换旧相反趋势锁，不再被迟到MA5门否决。",
        "局部反转、反抽追空、回踩追多及实体小止损快速反转采用独立利润空间门：至少1R且毛空间不少于3点，不再套用约7.9点的三倍成本门。",
        "局部转折与5分钟或15分钟方向同向时优先归类为反抽追空或回踩追多，不要求一分钟穿越MA5，也不要求五分钟相邻实体覆盖45%。",
    )},
    {"version": "0.7.198", "date": "2026-09-16", "changes": (
        "局部反转、真正反转、反抽追空及回踩追多统一按最近三根完整一分钟K线实体边界加缓冲设置止损，做空取实体上沿，做多取实体下沿。",
        "五分钟覆盖冻结结构和后续保护计算不得把最终订单止损重新放宽到影线极值；实体保护侧、利润空间、合约与账户风控继续验证。",
    )},
    {"version": "0.7.197", "date": "2026-09-16", "changes": (
        "局部顶部第一阶段与均线发散顶部快空使用最近3根一分钟K线实体上沿加缓冲作小止损，不再用上影线最高点。",
        "下降趋势反抽追空独立通道保留自身实体小止损，不再被反转专用五分钟45%冻结影线止损覆盖；利润空间、最小合约及账户风控继续约束。",
    )},
    {"version": "0.7.196", "date": "2026-09-16", "changes": (
        "反抽追空事件分别记录实时5分钟K线阴阳与较慢的5分钟方向箭头，避免把盘中阴线误称仍上涨。",
        "同步记录行情源最近3根一分钟最高点、缓冲及有效外沿止损；原始三根结构仍覆盖远端高点时不压窄止损。",
    )},
    {"version": "0.7.195", "date": "2026-09-16", "changes": (
        "修复底部反转后一分钟与五分钟同向MA5回踩仍误走真正反转45%覆盖门的问题；新鲜底部锁定后的同向回踩按趋势延续执行。",
        "回踩追多与反抽追空不要求一分钟重新穿过MA5，也不要求五分钟相邻实体覆盖45%；结构止损、利润空间、持仓、重复单和账户安全检查保持。",
        "整点样本新增五分钟MA20突破证据；一分钟/五分钟已转多而15分钟/1小时仍向下时，明确记录为回踩追多及上级局部底部反转待确认。",
    )},
    {"version": "0.7.194", "date": "2026-09-16", "changes": (
        "运行日志移出主界面，新增独立全屏菜单；按北京时间一页一天显示全天事件，可切换上一天、下一天和今天。",
        "最新一条仍每5秒刷新，当日历史页只在人工刷新或切换日期时重载，方便稳定核对盘面。",
        "1分钟三均线发散末端的第一阶段顶部，在5分钟/15分钟下降背景下可直接归入反抽追空，不再误用反转单45%覆盖门。",
        "15分钟换线以及1小时与15分钟同步换线后的前2分钟写入触发证据，作为多周期转弱增强信息。",
        "新增整点多周期样本表：每个北京时间小时只记一次，保存1分钟三均线末端与5分钟、15分钟、1小时同步换线证据，每天最多24份。",
    )},
    {"version": "0.7.193", "date": "2026-09-16", "changes": (
        "主界面把最新一条结构事件独立显示并每5秒刷新，历史79条只在出现新事件时更新，滚动查询不再被定时重置。",
        "长触发证据最多拆为三行，历史原因列加宽，订单号收窄并固定在最右侧。",
        "OKX完整只读审计改为顺序访问，避免九个接口并发造成Windows或代理线路TLS握手峰值。",
        "日内净盈亏在同一10秒执行轮内复用一次安全快照，减少成交历史接口的重复握手；断网禁单与恢复复核保持有效。",
    )},
    {"version": "0.7.192", "date": "2026-09-16", "changes": (
        "主界面清除保守型、稳妥型空白状态区，将实时结构与挡单日志扩展到约15行。",
        "实时日志将三周期反转、五分钟覆盖、恢复候选等英文触发证据翻译为中文。",
        "真正反转允许使用同一反转链已冻结的首次5分钟相邻45%覆盖，不要求后续K线重复覆盖。",
        "三周期真正反转及有效45%恢复候选不再被重复的1分钟区间位置门二次拦截。",
    )},
    {"version": "0.7.191", "date": "2026-09-16", "changes": (
        "明确分离反转与趋势延续门槛：局部反转、真正反转保留5分钟相邻实体45%覆盖。",
        "上涨回踩追多与下跌反抽追空不要求5分钟45%覆盖，也不等待1分钟价格穿过MA5。",
        "趋势单由上级趋势、均线附近回踩/反抽及1分钟首根转强/转弱迹象触发，继续使用小结构止损和成本风控。",
    )},
    {"version": "0.7.190", "date": "2026-09-16", "changes": (
        "下降趋势反抽高点追空使用独立早期通道：上一级5分钟或15分钟下跌、1分钟局部高点首根阴线在MA5外沿转弱即可核对下单。",
        "早期反抽单不等待1分钟下穿MA5或5分钟阴线覆盖45%，止损在最近3根1分钟K线高点外，仍核对风险和成本。",
        "主界面激进型区域新增每5秒刷新的结构、挡单和订单事件列表，时间显示北京时间。",
        "OKX订单POST的expTime使用同步服务器时钟，明确50036拒绝后只以同一订单编号重试一次。",
    )},
    {"version": "0.7.189", "date": "2026-09-15", "changes": (
        "修复数据库字符串时间与盘中Timestamp混合比较导致激进型自动循环故障停止。",
        "顶部锚点排序统一使用UTC时间，保持新鲜顶部优先于旧底部锁的逻辑。",
    )},
    {"version": "0.7.188", "date": "2026-09-15", "changes": (
        "交易周期全屏窗口分列六类结构，分别显示早期、MA5内侧、交叉补漏、五分钟确认和保护条件。",
        "顶部第一阶段局部高点转弱与五分钟半覆盖进入独立执行核对；新鲜顶证据解除较早底锁的方向冲突。",
        "确认顶部和五分钟覆盖仍有效时，小死叉补漏不再被同一分钟临时小底标签覆盖。",
        "主界面收起重复的核对表和订单表，统一由生命周期菜单打开。",
    )},
    {"version": "0.7.187", "date": "2026-09-15", "changes": (
        "新增全屏生命周期菜单：左侧做多和回踩追多，右侧做空和反抽追空，底部逐笔订单结构、价格、止盈止损与平仓。",
        "每笔新订单冻结实际执行门核对结果及反转三阶段证据，旧版无证据订单明确标注，不把模板差异当成失败。",
    )},
    {"version": "0.7.186", "date": "2026-09-15", "changes": (
        "新增1分钟价格进入MA5内侧、MA5走平/下弯且5分钟阴线覆盖前阳线至少45%的局部顶部做空。",
        "15分钟和1小时同步下降时升级为下降趋势反抽追空，止损冻结在最近3根1分钟K线上沿外。",
        "主界面新增交易周期菜单，全屏界面左侧显示做多条件、右侧显示做空条件。",
    )},
    {"version": "0.7.185", "date": "2026-09-15", "changes": (
        "彻底取消每日20单任务及其代码、界面规则和新订单生命周期字段；下单数量完全由实际有效结构决定。",
        "反转与趋势分支继续逐项验证各自的结构、位置、半覆盖、保护和盈利空间。",
    )},
    {"version": "0.7.184", "date": "2026-09-15", "changes": (
        "修正v0.7.182事故：每日20单只作为统计目标，不能绕过结构、均线、半覆盖、盈利空间、止损与指标门槛。",
        "保留主界面内嵌逐笔下单结构、生命周期、入场/止损/止盈规则和原始反转三阶段条件汇总。",
    )},
    {"version": "0.7.183", "date": "2026-09-15", "changes": (
        "停止使用每笔交易弹窗；订单提交、成交和保护变化只刷新主交易界面的常驻表格。",
        "主界面上半部分逐项显示原始反转三阶段条件的符合、不符合及下单时冻结证据，底部做多与顶部做空完全镜像。",
        "主界面下半部分显示三个实盘账户全部生命周期，包括下单结构、触发阶段、下单价、止损价、止盈规则、订单号、状态、平仓原因和净收益。",
        "每个新订单把一分钟和五分钟原始反转条件矩阵冻结进生命周期，复盘不再依赖冗长弹窗文字。",
    )},
    {"version": "0.7.182", "date": "2026-09-15", "changes": (
        "激进型设定每日20笔目标；当天前20笔识别到反转或趋势追单候选后采用机会优先，原5分钟半覆盖、旧趋势、位置、盈利空间和指标门改为成交后验证。",
        "账户审计、每日5U亏损熔断、同K线去重、同侧持仓与挂单冲突、有效方向止损和服务器保护仍为下单硬边界，不用无候选订单凑数量。",
        "PineTS、Webhook、Edge统一接入本机实时执行入口；只接受90秒内、明确多空、价格、止损、止盈、结构和指标证据齐全的事件，并按event_id防止重复执行。",
        "每笔订单生命周期新增信号来源、外部指标完整快照、机会优先状态、每日目标和提交前计数，支持成交后统计三类技术指标的准确率。",
    )},
    {"version": "0.7.181", "date": "2026-09-14", "changes": (
        "把134条净盈利仓位中可稳定还原的77条归入底反转多、顶反转空、回踩多、反抽空四类持久模板；57条证据不足样本不伪造统一触发。",
        "普通订单把盈利模板身份和历史证据数写入提交事件与生命周期；MA5回踩限价和结构狙击成交也写入同一审计口径。",
        "修复原MA5回踩限价直接成交及结构狙击成交后缺少trade_lifecycle，后续平仓可由统一成交对账关闭。",
        "MA5回踩限价和结构狙击预期毛空间至少达到3倍估算往返手续费与滑点；普通市价原有1.8R及3倍成本门槛保留。",
    )},
    {"version": "0.7.180", "date": "2026-09-11", "changes": (
        "修复只读审计错误加入API路径后不匹配旧恢复判断的问题：WinError 10054等网络错误在有限GET重试耗尽后进入外层退避与重新审计，不再误判为故障停止。",
        "HTTP 429及临时500/502/503/504纳入只读恢复；认证失败与来源不明错误仍不自动放行。",
        "强化POST和结果不明请求的排除规则，即使内部包含GET或DNS错误也不得自动重放交易。",
        "保留v0.7.179的代理刷新、同域名短时解析恢复、并行审计以及五分钟回踩规则。",
    )},
    {"version": "0.7.179", "date": "2026-09-11", "changes": (
        "直连同域名DNS短时失败时，仅使用最近300秒内通过正常TLS证书校验的连接地址建立连接；保持原域名Host与SNI，继续严格验证证书。",
        "缓存仅驻留当前进程，代理请求不绕过代理，缓存过期或连接失败不继续使用，回退使用不延长有效期。连接层不重放HTTP交易请求。",
        "完整账户审计最多3个只读请求并行，全部成功才返回结果；保留失败重试、恢复后的风险核验和五分钟回踩规则。",
    )},
    {"version": "0.7.178", "date": "2026-09-11", "changes": (
        "签名API、只读审计及基础行情请求每次重新读取当前Windows代理设置，避免启动时缓存直连或旧代理端口后持续掉线。",
        "自动恢复改为通过当前代理实际探测HTTPS API，不再自动切换系统DNS；恢复后仍须完成账户与风险审计。",
        "只读审计重试重新生成时间戳和签名，50102过期签名使用服务器校时后有限重试；临时GET网关故障有限重试。",
        "禁止API重定向携带认证信息，保持TLS证书校验；不确定POST不新增自动重试。掉线提示增加失败类别与API路径。",
        "保留v0.7.177五分钟回踩与接近挂价承接规则，本次不变更策略版本。",
    )},
    {"version": "0.7.177", "date": "2026-09-11", "changes": (
        "MA5漏单回踩限价从120秒延长至300秒；已挂出的价格、止损和目标冻结。",
        "激进型执行线程对已登记且账户空仓的单张回踩挂单进行快速盯价；两次新鲜可成交报价向挂价靠近，差距不超过0.20点及0.15ATR时核验承接。",
        "先确认原单终态撤销且累计成交为零，再重新检查报价、持仓、日亏损和停止开关，最后仅尝试一次带原保护价的市价单；部分成交、状态不明与重启均不自动重试。",
        "市价承接沿用已挂单的1.2R门槛并扣除吃单成本，普通新市价信号的1.8R与3倍成本门槛不变。",
        "6根K线识别窗口与止损范围解耦：较新的MA5外沿已确认小拐点优先，包含随后全部影线；缺少新拐点时保留原结构，不强行缩止损。",
    )},
    {"version": "0.7.176", "date": "2026-09-10", "changes": (
        "复盘亏损集中的三周期快速做空和恢复做空转为观察；其余反转统一检查最近20根区域边缘，6/3根为回看窗口。",
        "同一反转极值仅允许一次提交尝试，跨K线、重启及升级保持去重；不确定提交不会自动重试。",
        "预计目标空间至少1.8R及3倍估算往返手续费滑点，保留原目标，不为通过门槛机械扩大目标。",
        "浮盈达到估算成本+0.5R后保护成本，达到成本+1R后跟随净浮盈；只收紧唯一匹配的服务器止损，失败保留原止损并记录。",
        "生命周期对账纳入旧策略版本；分页查成交、按数量分配且防止跨轮复用，关闭时间使用实际成交时间；证据不足保持待核查。",
    )},
    {"version": "0.7.175", "date": "2026-09-10", "changes": (
        "首次达到运行中5分钟相邻反向实体45%覆盖时，持久化反转候选、对应5分钟K线与当时的一分钟局部极值小止损；后续行情不得扩大该止损。",
        "增加断线、API恢复或首次提交漏单后的限时补单：最多保留15分钟，仅在冻结止损未失效、一分钟重新转向且十五分钟由弱转强或由强转弱时执行；42%仍然不合格。",
        "一分钟上穿MA20并完成首次回踩，或MA20直接同向拐弯后，即使试单未成交也锁定较新的趋势；冻结极值失效则撤销新锁定并恢复上一级旧趋势。",
        "修复旧下降趋势局部高位追空覆盖新鲜底部恢复做多候选的问题；顶部做空与底部做多完全镜像，策略升级为demo-frequency-validation-v183。",
    )},
    {"version": "0.7.174", "date": "2026-09-10", "changes": (
        "所有顶部反转做空入口统一强制要求运行中的5分钟阴线覆盖相邻阳线实体至少45%；一分钟形态、专用顶部、趋势追空和断线补漏均不得绕过。",
        "所有底部反转做多完全镜像：运行中的5分钟阳线必须覆盖相邻阴线实体至少45%，一分钟分支和历史3根/6根聚合形态仅保留为识别与审计证据。",
        "首次达到45%覆盖时立即将候选所属5分钟K线和当时的一分钟局部高点/低点止损写入数据库；同一候选后续只读首次锚点，禁止瀑布或拉升后按整段最高/最低点扩大止损。",
        "网络自愈同时包含v0.7.173的多候选DNS实测优选、失败60秒重试和跨进程互斥；策略升级为demo-frequency-validation-v182。",
    )},
    {"version": "0.7.173", "date": "2026-09-10", "changes": (
        "掉线自愈升级为多候选 DNS 实测优选：依次验证国内优先与公共备用 DNS，只有域名解析和 OKX 公共时间接口均恢复后才锁定该组。",
        "明确 DNS 解析错误连续 3 轮触发自愈；TLS 握手或连接超时等请求前网络故障连续 6 轮也触发，避免只有 getaddrinfo 错误才修复。",
        "失败的 DNS 优选仅冷却 60 秒后重试，验证成功后冷却 30 分钟；增加跨进程互斥锁，避免同一电脑两个软件实例同时改 DNS。",
        "修复期间继续暂停新订单，恢复后必须重新核验账户、持仓、保护单和风险状态；POST 结果未知仍禁止重试，也不会触发 DNS 修改。",
        "交易策略版本保持 demo-frequency-validation-v181，本次仅增强网络恢复与实盘安全。",
    )},
    {"version": "0.7.172", "date": "2026-09-09", "changes": (
        "三周期反转新增独立快速通道：一分钟站回MA5有效侧、运行中的五分钟单根K线覆盖相邻反向实体45%、十五分钟由弱转强或由强转弱即可触发；十五分钟不要求半覆盖。",
        "一分钟最多6根、五分钟最多3根只作为漏单或断线重连后的补单回看窗口，不再等待凑满根数，也不要求窗口最后一根必须同色。",
        "恢复旧趋势接管规则：五分钟K线穿过MA20后确认已有一分钟底部/顶部并锁定新趋势；最多三层是当前同向持仓上限，旧追单已平仓就释放名额，后续新鲜回踩/反抽可继续追单，且追单不要求一分钟穿过MA5。",
        "修复覆盖比例已经超过45%却因最后一根颜色而被错误拒绝，以及三周期候选被旧的仅观察入口拦截的问题；多空完全镜像。",
        "策略升级为demo-frequency-validation-v181。",
    )},
    {"version": "0.7.171", "date": "2026-09-09", "changes": (
        "修正反转下车过早：一分钟局部反转加盘中五分钟半覆盖只允许先提交反转试单，不再仅凭候选形态平掉旧趋势仓。",
        "反转试单已成功提交后，系统在下一次安全快照中识别其交易生命周期，再一次性只减仓平掉旧方向最多三层仓位。",
        "即使反转试单漏单，只要后续一分钟MA20反转趋势锁定确认有效，仍会独立触发旧方向全部仓位止盈；旧锁定早于持仓时不得误平。",
        "统一方向K线底线改为反转区域累计判断：一分钟使用最近6根K线族，五分钟使用最近3根K线族；允许两三根同方向实体合计覆盖前段反向实体约45%，不要求相邻单根硬覆盖。",
        "下降趋势反抽追空和上升趋势回踩追多必须由盘中五分钟3根K线族确认区域转弱/转强；确认后不再额外等待一分钟K线穿越MA5。",
        "做多转做空完全镜像；策略升级为demo-frequency-validation-v180。",
    )},
    {"version": "0.7.170", "date": "2026-09-09", "changes": (
        "下降趋势最多三层追空不再只等待5分钟MA5拐弯止盈；1分钟局部底部向上拐弯且运行中的5分钟阳线半覆盖前一根阴线时，反转信号立即接管止盈。",
        "反转接管时一次性只减仓平掉全部空单，绕过普通盈利手续费等待门槛；确认交易所空仓后，原底部反转试单链继续提交多单，避免双向仓位重叠。",
        "上涨趋势多单在1分钟局部顶部转弱且5分钟阴线半覆盖时执行完全镜像的平多反手做空。",
        "保留服务器止损、真实持仓核验、重复订单拦截和最多三层限制；策略升级为demo-frequency-validation-v179。",
    )},
    {"version": "0.7.169", "date": "2026-09-09", "changes": (
        "修复分层仓位部分平仓后旧止损未及时清除：平仓成交确认后立即刷新OKX真实持仓与全部止损，仅保留仍然开放的交易生命周期对应保护。",
        "真实剩余持仓数量拥有最高优先级；即使本地数据库仍误留开放层，止损覆盖总量也不得超过交易所真实持仓。",
        "新开仓附带止损增加稳定的分层客户标识；历史无标识止损按方向、止损价和开放层数量保守核对，同价重复止损只保留实际所需数量。",
        "每次自动清理写入closed_layer_protective_stops_cancelled审计事件；不改动入场、止盈和趋势判断规则。",
    )},
    {"version": "0.7.168", "date": "2026-09-09", "changes": (
        "连续3轮出现明确的Windows DNS解析错误后，自动暂停新订单，定位IPv4默认路由对应的活动物理网卡，将DNS优选为223.5.5.5和119.29.29.29并刷新缓存。",
        "DNS调整后自动验证OKX域名解析与公共时间接口；连接恢复后仍须重新完成账户、持仓、保护单和风险状态的完整只读审计，才恢复策略循环。",
        "自动修复设置30分钟冷却并写入live_dns_self_heal审计事件；SSL握手超时和POST结果未知等非DNS故障绝不修改系统DNS。",
        "Windows程序启动时申请管理员权限，以便本地网络电脑掉线后自动调整网卡DNS；策略仍为v178，交易规则不变。",
    )},
    {"version": "0.7.167", "date": "2026-09-09", "changes": (
        "15:22高位回落不得再标为五分钟底部局部反转：一分钟底部要求MA5在MA20下方；五分钟前置底部K线至少同时收在MA5和MA10下方，允许在MA20中间附近反转上升。",
        "顶部局部反转完全镜像：一分钟MA5在MA20上方；五分钟前置顶部K线至少同时收在MA5和MA10上方，允许在MA20附近反转下降。",
        "五分钟局部顶底反转只使用最近四根一分钟的新鲜局部极值；结构外止损超过3点则放弃入场，禁止借用旧低点/高点扩大到6点。",
        "数据库确认15:22:46订单3906949414983553024入场2511.51、止损2505.18、风险6.33点，属于身份错分和远端止损锚定双重错误。",
    )},
    {"version": "0.7.166", "date": "2026-09-09", "changes": (
        "实机诊断确认www.tpouxyihas.com被本机代理DNS映射到198.18.0.6，公共时间API能返回HTTP 200/code=0，但冷连接耗时约8.9秒，超过旧的8秒超时。",
        "将激进型启动审计、断线恢复审计和自动执行签名客户端统一改为20秒网络超时，允许Fake-IP/代理链完成首次连接。",
        "仍保留DNS/SSL故障时暂停新下单、持续退避探测、恢复后必须先完成账户与风险只读审计的安全边界。",
        "策略仍为v178，未改动趋势锁定、下单、止损或止盈规则。",
    )},
    {"version": "0.7.165", "date": "2026-09-09", "changes": (
        "修复Windows DNS短暂解析失败（Errno 11004/getaddrinfo failed）造成只读审计过快失败：安全GET重试增加到5次，并使用0.5/1.5/3/5秒退避。",
        "明确将DNS 11004归类为订单POST之前的可恢复只读网络故障；恢复前不下单，恢复后先重做完整只读审计。",
        "主界面手动刷新遇到可恢复DNS/SSL故障时改为显示临时连接不可用，不再误报成账户或策略故障。",
        "本次仅修复Live只读网络恢复链，策略v178与下单、止损、止盈规则保持不变。",
    )},
    {"version": "0.7.164", "date": "2026-09-09", "changes": (
        "一分钟底部或顶部反转即使试单漏掉、未成交，也不影响趋势锁定；趋势方向由后续K线与MA20确认决定，不再依赖订单状态。",
        "底部末端上穿MA20后首次回踩守住，或未回踩但继续上涨且MA20向上拐弯，照样锁定有效上涨趋势；顶部完全镜像。",
        "漏单后的新鲜趋势锁定同样优先于旧上级趋势，并允许后续回踩追多或反抽追空；失败后仍恢复旧趋势。",
    )},
    {"version": "0.7.163", "date": "2026-09-09", "changes": (
        "一分钟底部反转做多试单后，有效上穿MA20并完成首次回踩守住，立即把该三均线发散底部末端锁定为新鲜上涨趋势；顶部做空完全镜像。",
        "若没有回踩而价格继续沿新方向运行，只要一分钟MA20已经同向拐弯并连续站在新趋势侧，也建立新鲜趋势锁定。",
        "新鲜一分钟趋势锁定优先于旧的五分钟或十五分钟继承方向；只有实际反转试单存在才允许升级，普通未成交候选不能抢占方向。",
        "价格破坏反转极值，或连续回到MA20旧趋势侧且MA20重新反向时，标记锁定失败并自动恢复上级旧趋势。",
    )},
    {"version": "0.7.162", "date": "2026-09-08", "changes": (
        "修复Live订单POST在SSL/TLS握手超时时被误判为结果未知并立即停机：握手、DNS和建连失败发生在HTTP订单发送前，可使用同一clOrdId安全重试。",
        "POST可能已经送达但回执丢失时，不盲目重复下单；先按instId与clOrdId调用OKX只读订单查询，查到原订单即恢复成功结果。",
        "只有只读对账仍无法确认的POST结果才保持故障停机，兼顾网络稳定性与绝不重复下单。",
        "行情快照、逐级趋势锁定和五分钟盘中约半覆盖策略规则保持不变。",
    )},
    {"version": "0.7.161", "date": "2026-09-08", "changes": (
        "取消趋势方向对双周期配对的依赖：单个已收盘上级周期三均线发散末端即可直接锁定下级趋势。",
        "严格逐级锁定：一小时末端锁定十五分钟趋势，十五分钟末端锁定五分钟趋势，五分钟末端锁定一分钟趋势。",
        "同一执行层级采用最新的直接或继承锁定；不同周期各自保留独立有效锁定和历史审计记录。",
        "五分钟盘中约半覆盖的顶底反转早单与一分钟局部小止损继续保留。",
    )},
    {"version": "0.7.160", "date": "2026-09-08", "changes": (
        "恢复局部顶底与真正顶底反转的五分钟盘中约半覆盖：运行中的五分钟达到约一半即可配合一分钟局部小止损提前试单，不等待收盘后追远。",
        "21:26错误底部多单改由五分钟三均线末端独立方向锁定修复：最新已收盘五分钟顶部锁定下降趋势并释放旧底部方向，而不是延迟所有反转。",
        "顶部早空与底部早多保持镜像；一分钟结构止损、防重复、挂单冲突、最多三层及账户风险门槛保持不变。",
    )},
    {"version": "0.7.159", "date": "2026-09-08", "changes": (
        "局部顶底反转和真正顶底反转的五分钟约半覆盖一律等待五分钟K线收盘后确认，禁止盘中瞬时覆盖放行反转单。",
        "只有明确归类为上涨趋势回踩追多或下降趋势反抽追空的独立趋势分支，才允许盘中五分钟约半覆盖提前触发。",
        "已收盘五分钟三均线发散末端可不等一分钟配对而独立锁定方向；最新顶部锁定后按下降趋势优先寻找反抽追空，底部完全镜像。",
        "审计事件明确记录closed_reversal与趋势盘中触发身份，结构止损、防重复、挂单冲突和账户风险门槛保持不变。",
    )},
    {"version": "0.7.158", "date": "2026-09-08", "changes": (
        "每一个合格的一分钟三均线发散末端都独立对照当前五分钟1至3根反向K线累计约半覆盖。",
        "一分钟转向触发与五分钟约半覆盖同时成立即可先下局部反转试单，不再等待五分钟自身末端、周期配对或顶底锁定。",
        "配对锁定只负责后续趋势方向与反抽追空/回踩追多身份；锁定遗漏不得压住首笔局部反转单。",
        "多空规则镜像，结构止损、防重复、挂单冲突、最多三层和账户风险门槛保持不变。",
    )},
    {"version": "0.7.157", "date": "2026-09-08", "changes": (
        "五分钟顶部/底部保留三均线发散末端位置定义，但取消后续MA5、MA20及慢均线穿越门槛；随后一至三根反向K线实体累计约45%即视为约半覆盖转弱/转强。",
        "一分钟与五分钟末端在时间窗口内同向且五分钟约半覆盖即可配对；一分钟负责实际执行确认。",
        "15分钟与1小时同步下降时，一分钟反抽形成新局部高点后的首根转弱阴线可提前追空，不等待下穿一分钟MA5。",
        "保留局部高点止损、同K线与同信号去重、挂单防重、最多三层及账户风险门槛。",
    )},
    {"version": "0.7.156", "date": "2026-09-08", "changes": (
        "最新完成的1分钟+5分钟三均线末端配对独占当前趋势方向；顶部锁定后立即形成下跌趋势并释放旧底部锁定，底部锁定完全镜像。",
        "旧锁定和旧末端继续保留在四级配对表用于审计，但已释放记录不再参与下单方向、试单升级或趋势追单。",
        "末端持续区与逐级配对窗口收紧到相邻上级K线范围；五分钟半覆盖只允许使用紧邻的上一根五分钟末端K线，禁止隔很久复用旧底部或旧顶部。",
        "最新双周期锁定后的MA5/MA10第三阶段交叉作为前两阶段漏单的强制补漏；只豁免瞬时MA5侧别误判，过期、重复持仓、结构止损与账户风险门槛不变。",
        "同轮多个有效阶段按最新完成时间选取，避免较早的相反方向候选抢先覆盖后形成的顶部或底部。",
    )},
    {"version": "0.7.155", "date": "2026-09-08", "changes": (
        "局部低点或高点不再自动称为局部底部或顶部；必须先处在本周期MA5与MA20有效发散的末端。",
        "底部要求末端收盘位于MA5和MA20下方且MA5仍向下运行；顶部完全镜像，要求收盘位于两线上方且MA5仍向上运行。",
        "MA10继续显示、记录和参与解释，但允许在末端区交叉，不再作为局部顶底的硬否决条件。",
        "一分钟和五分钟必须各自通过上述末端复核，随后五分钟反向K线实体覆盖前一根一半，才可归类为五分钟局部反转试单。",
        "四级末端表只接收复核合格的末端；旧版普通局部高低点记录原样保留为审计噪音，但不再参与逐级配对、趋势锁定或持仓升级。",
    )},
    {"version": "0.7.154", "date": "2026-09-08", "changes": (
        "同一底部区域内每个独立五分钟半覆盖局部反转形态均取得新的下单资格；前一试单已经平仓时，后一形态不得被旧记录去重。",
        "已有同向持仓且真正上升/下降趋势仍有效时，新局部反转形态可作为第二层、第三层；第四层、同一五分钟K线和待成交开仓单继续拦截。",
        "每次局部反转均重新使用最近四根一分钟K线的局部极值外止损，不再继承前一个圈的远端极值，也不再被旧3点或2 ATR试单上限漏掉。",
        "所有五分钟局部反转首单和加仓单从成交起使用五分钟MA5止盈，并在生命周期中记录入场前层数与是否属于局部反转加仓。",
        "一分钟与五分钟真正底部配对锁定不依赖底部订单是否成交；即使底部漏单，后续上涨趋势中的新MA5/MA10回踩仍可从第一单开始执行，持仓后继续加至三层。",
    )},
    {"version": "0.7.153", "date": "2026-09-07", "changes": (
        "纠正末端误配对：一分钟与五分钟仅时间接近、方向相同不再算成功；五分钟反向K线覆盖前一根反向实体至少一半成为硬门槛。",
        "三均线发散末端改为持续区域：从进入末端开始保留一行，后续同向新低或新高更新该区域的极值和最新时间。",
        "普通一分钟真正顶底须在五分钟半覆盖后，再由一分钟收盘价成功穿过MA5；明确的十五分钟上级回踩/反抽身份只豁免一分钟MA5，不能豁免五分钟半覆盖。",
        "完整双周期反转新增五分钟MA20门槛：底部须有效站上MA20、顶部须有效跌破MA20；否则不得建立持久反转方向。",
        "五分钟半覆盖但尚未通过完整双周期门槛时，独立归类为上一级5分钟底部/顶部局部反转；不要求一分钟穿MA5，也不误称回踩或反抽。",
        "五分钟局部反转试单从成交起即使用五分钟MA5止盈，避免第一次正常回踩被一分钟MA5洗出；完整配对后只升级结构身份。",
        "未配对的一分钟底部标为一分钟下降趋势反抽追空触发点，未配对顶部做镜像解释；列表新增区域起止、极值、半覆盖和MA5穿越证据。",
    )},
    {"version": "0.7.152", "date": "2026-09-07", "changes": (
        "新增四级三均线末端谱系表：1分钟、5分钟、15分钟、1小时全部记录，逐级显示已配对与未配对。",
        "同一真正高位/低位允许连续末端覆盖或叠加；未配对的一分钟末端保留为局部噪音，并按五分钟趋势解释为反抽追空或回踩追多触发点。",
        "一分钟与五分钟末端配对成功后，同向全部持仓生命周期升级为五分钟MA5管理，并先撤销会抢先离场的旧服务器移动止盈。",
        "新增新版简明规则表，旧规则总表改为旧版保留；趋势追单明确标注5分钟或上一级15分钟来源。",
    )},
    {"version": "0.7.151", "date": "2026-09-07", "changes": (
        "按最近100笔实盘复盘加入手续费感知主动止盈：预计毛收益必须覆盖0.05张双边手续费并至少保留0.005 USDT净收益。",
        "交易生命周期新增MFE、MAE、最后观察价及止损后5/10/20分钟价格，供下一轮离线校准结构缓冲。",
        "禁止把真实结构止损压缩为固定3点；最小仓位无法承受结构风险时直接放弃订单。",
        "五分钟高位半覆盖必须同时出现一分钟MA5外沿当前阴线转弱，仅作跨周期确认，不得单独高频做空。",
    )},
    {"version": "0.7.150", "date": "2026-09-07", "changes": (
        "模拟盘、实盘只读审计、手动实盘和激进型自动实盘统一硬锁定到www.tpouxyihas.com。",
        "删除客户端覆盖为其他REST主机的能力；非指定域名在构造请求和加入鉴权头之前立即拒绝。",
        "清理源码、测试及随包说明中的旧域名残留，网页K线和API管理按钮继续使用同一个指定域名。",
    )},
    {"version": "0.7.149", "date": "2026-09-06", "changes": (
        "复核21:00空单：实际由旧顶部走弱授权在最近区间下沿释放，并非局部高位扫顶；禁止该分支复用穿越MA5后1至3根的旧触发。",
        "顶部走弱快空只允许当前阴线首次跌破MA5且距MA5不超过0.50倍一分钟ATR；错过上沿后不得在下沿补追。",
        "反转区复查取消中性观察文案：横盘记录按事件方向显示偏多候选或偏空候选，是否下单仍由后续结构和新鲜MA5门槛决定。",
    )},
    {"version": "0.7.148", "date": "2026-09-06", "changes": (
        "移除交易明细、亏损复盘、共享实验、PineTS对照、5分钟快照和手动刷新三账户六个过时入口。",
        "卸载 PineTS、Edge/Webhook 外部研究、五分钟研究快照和共享均线偏离实验的运行线程、源码模块及打包资源。",
        "首次启动仅清理上述退役业务表；保留订单生命周期、反转状态、风控、漏单复查及后台安全账户刷新。",
    )},
    {"version": "0.7.147", "date": "2026-09-06", "changes": (
        "修复上涨趋势回踩追多追到局部高点：只认最近3根1分钟K线的当前回踩，入场收盘必须仍在MA5下沿，不再用历史旧低点授权后续高位阳线追多。",
        "镜像修复下跌趋势反抽追空：入场收盘必须仍在MA5上沿，保留刚转弱的1–2分钟提前触发，禁止跌到底部才追空。",
        "同向持仓防重改为跨策略版本检查：已有同向反转仓时，同一顶底区不得因版本切换或相邻K线再重复开仓；只有后续独立回踩/反抽结构才可按三层规则加仓。",
    )},
    {"version": "0.7.146", "date": "2026-09-06", "changes": (
        "修复局部底部扫底做多的异步确认：一分钟六根混合底部簇上穿MA5，且五分钟阳线覆盖前阴线一半时，按局部底部规则放行，不再额外要求真正双周期末端。",
        "上述局部底部候选的止损只参考当前六根一分钟簇及相邻五分钟低点，不再被较旧的12根结构低点错误拉远。",
        "更新日志改为每页50行分页，默认显示最新页，可前后翻页查看保留的历史记录。",
    )},
    {"version": "0.7.145", "date": "2026-09-06", "changes": (
        "修复更新日志超过 Win32 文本框默认容量后显示空白的问题，改为先扩容再写入完整日志。",
        "策略规则窗口改为当前中文规则与历史版本记录分离，不再把内部英文升级说明混入现行规则表。",
        "新增当前方向、真正顶底、局部顶底、趋势追单、五类身份、止盈和结构保护的中文汇总。",
    )},
    {"version": "0.7.144", "date": "2026-09-06", "changes": (
        "Classify uptrend, downtrend and sideways consolidation from confirmed closing-price swing highs and lows instead of wick extremes.",
        "Recognise a recent one-minute MA5/MA10/MA20 fan endpoint only when it lies on the matching local closing-price edge.",
        "At that endpoint, execute when the live five-minute opposite candle dynamically covers at least half of the preceding opposite body, before MA5 crossing.",
        "Apply the exact mirrored bottom-long rule while retaining structural stops, deduplication, exposure controls and the three-layer cap.",
        "Keep non-endpoint local-top/local-bottom entries on their separate stricter MA5-cross path.",
    )},
    {"version": "0.7.143", "date": "2026-09-06", "changes": (
        "Remove Heikin-Ashi/average candles from all new reversal, direction, order-release and exit-hold decisions.",
        "Detect live reversal zones from ordinary exchange candles using sweep/reclaim, candle coverage, local extremes and the MA5 edge.",
        "Allow only ordinary-candle reversal zones to own durable 1m+5m or maximum 1m+5m+15m trend direction.",
        "Keep old Heikin-Ashi records visible as audit-only history that cannot release orders or change current direction.",
        "Manage confirmed endpoint cores with raw five-minute close and MA5; keep continuation layers on their existing one-minute MA5 exits.",
    )},
    {"version": "0.7.142", "date": "2026-09-06", "changes": (
        "Divide original-strategy shorts into five persisted identities: true high reversal, local high reversal, stage-three recovery, current-timeframe rebound short and parent-timeframe rebound short.",
        "Apply the exact mirrored five identities to longs and use one identity consistently in order lifecycle, review shape, miss class and full reason.",
        "Treat true versus local high/low as timeframe-relative, so a true one-minute endpoint may simultaneously be only a local endpoint on five or fifteen minutes.",
        "Let a newer qualified same-direction true endpoint replace the older active anchor while retaining the older record for audit history.",
    )},
    {"version": "0.7.141", "date": "2026-09-06", "changes": (
        "Use the persisted successful 1m+5m endpoint-reversal record, rather than a transient MA10/MA20 price snapshot, to promote an open endpoint trial.",
        "Keep the initial sweep-bottom long or sweep-top short on the 1m MA5 while it is still only a trial, then switch the surviving core position to the 5m MA5 after confirmation.",
        "Make the 5m-MA5 core classification sticky across normal pullbacks and repeated same-direction endpoint records.",
        "Keep up to two later pullback-long or rebound-short continuation layers classified as local 1m-MA5 exits instead of inheriting the core hold.",
    )},
    {"version": "0.7.140", "date": "2026-09-06", "changes": (
        "Replay the 07:15 dual-timeframe true bottom as the durable uptrend owner, so later ordinary local-top shorts cannot override it.",
        "Trigger a valid downtrend rebound short on the first bearish turn while the one-minute price is still at or above MA5.",
        "Use a successful five-minute top only to establish the downtrend; execute later rebound shorts exclusively at the one-minute local high, never by reusing the five-minute cover as a direct late order.",
        "Keep the mirrored pullback-long rule early: the first strengthening one-minute candle below MA5 may enter without waiting for an MA5 reclaim.",
        "Retain one-minute endpoint trial entries and replace the durable trend only after a matching opposite one-minute plus five-minute true reversal is recorded.",
        "Persist 15-minute endpoint observations as the maximum third confirmation level while excluding one-hour and four-hour arrows from direction decisions.",
    )},
    {"version": "0.7.139", "date": "2026-09-05", "changes": (
        "Reserve 5m MA5 profit exit exclusively for the actual confirmed 1m+5m MA-spread endpoint sweep-top short or sweep-bottom long.",
        "Use the 1m MA5 turn for every rebound short, pullback long, continuation add-on and ordinary local-top/local-bottom entry.",
        "Do not inherit the core reversal's 5m exit merely because an add-on direction matches the persisted trend bias.",
        "Persist endpoint-reversal versus local-swing classification in the trade lifecycle for exit auditing.",
        "Advance downtrend rebound shorts to the first live 1m weakening candle at the MA5 edge and show a clear Chinese branch name in reversal review.",
    )},
    {"version": "0.7.138", "date": "2026-09-05", "changes": (
        "Classify only a direction-matched persistent 1m+5m MA-spread endpoint reversal family as a medium-trend position.",
        "Use the 5m MA5 profit exit for the reversal core and at most two same-direction continuation add-ons, retaining the three-layer cap.",
        "Keep every other valid local-top or local-bottom entry as a local-swing position using the faster 1m MA5 turn for profit exit.",
        "Persist the position class and selected MA5 exit timeframe in each trade lifecycle for review and audit.",
    )},
    {"version": "0.7.137", "date": "2026-09-05", "changes": (
        "Keep qualified five-minute high-cover, expanded-MA top, MA5 weakening, compact-top and intrabar bearish top branches executable instead of clearing them through the legacy observe-only gate.",
        "Allow a complete one-minute stage chain plus five-minute top confirmation to trigger its short independently of whether an older MA-spread endpoint record remains active.",
        "Remove 1m, 5m, 15m, 1H and 4H direction-arrow voting and vetoes from reversal execution; arrows remain display and audit metadata only.",
        "Retain compact structural stops, duplicate-order protection, exposure controls and the same-direction layer cap.",
    )},
    {"version": "0.7.136", "date": "2026-09-05", "changes": (
        "Persist a deeply exhausted lowest one-minute MA-spread endpoint across a longer base instead of requiring the later launch to recreate the same large spread.",
        "Treat the later smaller MA expansion as uptrend continuation when the five-minute structure confirms, replacing the older durable short regime.",
        "In that confirmed long regime, enter a valid one-minute pullback as soon as its candle turns stronger; MA5 defines pullback location but a fresh cross above MA5 is not required.",
        "Apply the MA5/MA10 outer-edge location gate to continuation-classified one-minute staged entries too, preventing a short launched directly into bottom support.",
    )},
    {"version": "0.7.135", "date": "2026-09-05", "changes": (
        "Connect the repeatedly detected five-minute high bearish-cover candidate to its executable short branch instead of leaving its entry flag false.",
        "Use only the adjacent five-minute pullback/turn pair; apply the up-to-six mixed-bar cluster to one minute so the structural stop cannot inherit an older half-hour high.",
        "Execute this fresh five-minute continuation/reversal trigger independently of an expired one-minute staged launch while retaining risk checks, deduplication, and the three-layer cap.",
    )},
    {"version": "0.7.134", "date": "2026-09-05", "changes": (
        "Prevent a qualified downtrend MA5/MA10 outer-edge continuation short from being rejected a second time by the generic five-minute cover or MA5-break gate.",
        "Require the continuation direction to match the persisted 1m+5m reversal regime and retain the fast-average location check and local structural stop.",
        "Allow the persisted regime to authorize up to three same-direction layers even when the first reversal order was missed or stopped, while retaining pending-order and exposure caps.",
    )},
    {"version": "0.7.133", "date": "2026-09-05", "changes": (
        "Persist a confirmed dual-timeframe top/bottom regime when a prior mature reversal endpoint is followed by a same-direction five-minute reversal or body-cover confirmation.",
        "Do not erase the regime when the first order is missed, stopped, or current MA stacks become mixed; only a newer true opposite dual reversal replaces it.",
        "Show the inherited mature reversal endpoint in the review and route later MA5/MA10 outer-edge reactions into the three-layer continuation path.",
    )},
    {"version": "0.7.132", "date": "2026-09-05", "changes": (
        "Replace the fixed single-candle 50-percent cover requirement with a location-first MA5/MA10 outer-edge turn.",
        "Accept a bullish or bearish candle moving away from the fast-average outer edge with a minimum 0.10 ATR body, without waiting for an MA5 recross.",
        "Keep MA20 side unrestricted, structure stops, independent-signal deduplication and the three-layer cap.",
    )},
    {"version": "0.7.131", "date": "2026-09-05", "changes": (
        "Require every uptrend continuation long to originate from a recent one-minute pullback below both MA5 and MA10; MA20 side remains unrestricted.",
        "Reject continuation longs while price has stayed above MA5, including second and third same-direction add-on layers.",
        "Apply the exact mirror to downtrend continuation shorts: the recent reaction high must reach above both MA5 and MA10.",
        "After a confirmed high reversal leads into a downtrend, let a bearish candle covering at least half of the preceding bullish body trigger a continuation short before MA5 breaks, protected beyond the pullback high and capped at three layers.",
    )},
    {"version": "0.7.130", "date": "2026-09-05", "changes": (
        "Invalidate stale opposite frozen chains when fresh same-direction one-minute and five-minute reversal zones coexist; this blocks the audited 02:30:48 short after the dual bottom.",
        "Add an early uptrend pullback long before MA5 crossing when both timeframe pullback lows are below MA5/MA10/MA20 and a roughly three-candle bullish recovery covers at least half of the decline.",
        "Allow independently refreshed same-direction trend pullbacks to build up to three total layers, while retaining same-candle and pending-order deduplication.",
        "Display every submitted trade lifecycle in the reversal review with signal time, branch, reason, order ID and submitted status, even without a reversal-zone link.",
    )},
    {"version": "0.7.129", "date": "2026-09-05", "changes": (
        "Exit a profitable long immediately after a fast one-minute-ATR rise when price returns to MA5 and 1m MA5 flattens or bends down; apply the exact mirror to shorts.",
        "Let the fast 1m MA5 profit exit override a still-aligned 5m hold signal, while retaining the server structure stop as disaster protection.",
        "Prevent a later unrelated same-direction order from falsely changing an old reversal-review zone to submitted; require a current trigger-window match.",
        "Let a mature same-side 1m+5m MA20 top plus a current 1m MA5 break or death cross override stale frozen anchors and local-range percentile rejection; manage that core reversal with 5m MA5.",
        "After that core top-short is open, allow qualified one-minute downtrend pullback-high continuation shorts up to three total layers; block while another opening order is pending.",
    )},
    {"version": "0.7.128", "date": "2026-09-04", "changes": (
        "Treat up to six mixed bullish/bearish five-minute candles as one reversal cluster and evaluate their cumulative recovery or rejection.",
        "Search the preceding six one-minute candles for the shared mature MA-spread endpoint so stage one and stage two may form across a short mixed cluster.",
        "Retain mature extreme location, final directional confirmation and structure protection to keep sideways middle noise ineligible.",
    )},
    {"version": "0.7.127", "date": "2026-09-04", "changes": (
        "Accept one or two consecutive five-minute reversal candles whose cumulative move covers half of the preceding opposite candle.",
        "Let a mature same-side 1m+5m MA5/MA10/MA20 spread endpoint confirm stage two without making exact single-candle five-minute half-cover mandatory; 15m remains optional.",
        "Give a mature bottom/top stage priority over transient opposite anchors and lagging trend direction, preventing a post-V-bottom continuation short.",
    )},
    {"version": "0.7.126", "date": "2026-09-04", "changes": (
        "Classify a low-efficiency 1m+5m compression after a material selloff and recent bearish MA stack as a downtrend-continuation range rather than neutral sideways.",
        "Keep the continuation range short-biased: block pullback longs and middle-range entries; wait for a pullback followed by MA5 breakdown or small death cross.",
        "Show the review direction as pullback-short and the shape as downtrend continuation range for audit clarity.",
    )},
    {"version": "0.7.125", "date": "2026-09-04", "changes": (
        "Repair the 22:38 missed second short after the first range-top short was stopped: the fallback limit waited at MA5 and expired before the waterfall.",
        "Allow one fresh market short when a neutral range top has both a live 5m bearish half-cover and a 1m MA5 breakdown or small death cross, bypassing only the MA5-distance/pullback wait.",
        "Keep ordinary sideways breaks, all sideways pullback longs, same-side duplicates, risk checks and account guards blocked as before.",
    )},
    {"version": "0.7.124", "date": "2026-09-04", "changes": (
        "Add an explicit neutral 1m+5m sideways-compression classification ahead of stale directional trend labels.",
        "Block pullback longs inside a neutral range; permit only a confirmed MA5-break/small-death-cross short, otherwise observe without ordering.",
        "Display neutral sideways zones in the reversal-miss review instead of mislabeling them as uptrend pullback longs or true reversals.",
    )},
    {"version": "0.7.123", "date": "2026-09-04", "changes": (
        "Make mature 1m+5m high exhaustion sufficient for a half-cover top short; 15m is an optional strength upgrade.",
        "Make confirmed 5m-downtrend lower-high pullback shorts executable, with priority over same-cycle countertrend long candidates and existing same-side deduplication preserved.",
        "Require mature expanded lowest-zone agreement on both 1m and 5m before a countertrend bottom long, rejecting mid-trend one-minute bottom noise.",
    )},
    {"version": "0.7.122", "date": "2026-09-04", "changes": (
        "新增一分钟、五分钟、十五分钟成熟高位发散末端识别；高点须处于近期最高区且三条均线已经充分拉开，刚启动的多头排列不算顶部。",
        "一分钟扫顶阶段完成且五分钟阴线盘中覆盖前阳线实体一半时，若三周期成熟顶部共同成立，直接按反转确认入场，不再改挂可能踏空的MA5回抽限价。",
        "保留小止损、同向去重、持仓和账户安全检查。",
    )},
    {"version": "0.7.121", "date": "2026-09-04", "changes": (
        "Mirror the intrabar top-short override for bottoms: a live five-minute bullish half-cover confirms a frozen one-minute bottom below MA5, MA10 and MA20.",
        "Release lagging synchronized-downtrend labels after that bottom confirmation so the valid stage-2 long is executed before a nearby rebound can be misread as a new short.",
    )},
    {"version": "0.7.120", "date": "2026-09-04", "changes": (
        "Recognise a frozen local top above MA5, MA10 and MA20 even while their strict bullish ordering is already converging.",
        "Let an intrabar five-minute bearish half-body cover confirm the frozen local top without requiring the pair to be near the 24-candle absolute high.",
        "After a missed stage-2 entry, use the fresh four-bar micro swing for stage-3 protection and fall back to the accepted fixed three-point launch stop instead of rejecting a remote 12-bar structure.",
    )},
    {"version": "0.7.119", "date": "2026-09-04", "changes": (
        "Feed the five-minute reversal check with confirmed history plus the current live candle, replacing duplicate timestamps.",
        "Combine a one-minute top anchor and stage-2 short with a live five-minute bearish half-body cover instead of leaving it as an observe-only legacy candidate.",
        "Treat a frozen top above bullish MA5>MA10>MA20 as end-of-uptrend launch-zone evidence that can release the lagging bullish trend gate.",
    )},
    {"version": "0.7.118", "date": "2026-09-04", "changes": (
        "Require the current candle itself to be near the relative extreme before freezing a top/bottom anchor.",
        "Prevent a prior candle high from being re-timestamped as a current top during a bottom rebound.",
        "Apply the documented fixed 3-point stop cap to confirmed reversal probes whose structural stop is wider.",
    )},
    {"version": "0.7.117", "date": "2026-09-04", "changes": (
        "Correctly separate reversal-zone anchor time from actual stage-2 order-submission time in the audit.",
        "Allow a confirmed local reversal anchor plus two independent stage-2 confirmations to override lagging aligned-trend labels.",
        "Keep isolated single countertrend MA5 triggers blocked; long and short remain mirrored.",
    )},
    {"version": "0.7.116", "date": "2026-09-04", "changes": (
        "Refine same-minute long/short arbitration: one MA5 trigger remains blocked by an opposite local extreme.",
        "Two distinct same-direction stage-2 confirmations resolve direction and override one transient opposite local label; mirrored for long and short.",
        "This preserves the 01:47 wrong-direction guard while allowing the 02:23 confirmed short transition.",
    )},
    {"version": "0.7.115", "date": "2026-09-04", "changes": (
        "修复01:47同一分钟已形成确认顶部却仍由上涨中继第二阶段抢先做多：反向局部极值现在优先于同分钟MA5触发。",
        "反向锚点只否决同分钟抢跑方向，不否决随后一分钟已经明确分开的新方向；做多做空镜像。",
    )},
    {"version": "0.7.114", "date": "2026-09-04", "changes": (
        "第一阶段保持只冻结局部极值；第二阶段刚站回MA5且仍贴近时照常立即市价成交。",
        "第二阶段有效但因网络或扫描延迟已经远离MA5时禁止市价追单，改在原MA5附近等待一次120秒post-only回踩限价；不回踩自动撤销。",
        "短时限价附带原结构外标记价止损及至少1.2R且不少于4点目标；同向仓位/挂单去重，多空镜像。",
    )},
    {"version": "0.7.113", "date": "2026-09-03", "changes": (
        "反转区漏单复查改为每次打开或刷新时自动清理旧记录，只保留并展示最新30条；不删除订单、成交、交易生命周期或亏损审计。",
        "状态栏新增最新扫描、最新一分钟原始阶段、最新反转区及本次清理数量，避免没有新反转区时被误判为系统停止更新。",
    )},
    {"version": "0.7.112", "date": "2026-09-03", "changes": (
        "修复v0.7.111遇到OKX只读GET超时被网络库包装为非OkxError后错误进入永久故障停止：现在按安全文本识别并无限退避，恢复后先完整只读审计。",
        "POST结果不明、账户模式异常、保护失败和亏损熔断继续立即停止，网络恢复逻辑不会重试任何结果未知的下单请求。",
    )},
    {"version": "0.7.111", "date": "2026-09-03", "changes": (
        "统一修复21:39高位追多与21:51下降末段追空：第二/第三阶段触发超过90秒即失效，禁止旧冻结信号在结构换段后补追。",
        "三阶段止损保持在真实结构外；当风险超过2倍一分钟ATR且已是最小1张无法缩仓时拒绝订单，不再以远端12根结构止损强行成交；多空镜像。",
    )},
    {"version": "0.7.110", "date": "2026-09-03", "changes": (
        "修复21:00顶部空单漏单：五分钟高位阴线半覆盖且形态为真正顶部反转时，不再等待五分钟MA5斜率走平或下弯，由一分钟第二/第三阶段提前执行。",
        "确认顶部/底部反转取消普通逆势3点拒单，使用最近12根一分钟真实结构极值外止损并设置至少1.2R目标；顺势回踩允许一分钟方向短暂反向。",
    )},
    {"version": "0.7.109", "date": "2026-09-03", "changes": (
        "取消上涨中继最近30根一分钟K线68%高位、下跌中继镜像低位的固定否决；位置百分比改为只读审计，不再覆盖已成立的反转区三阶段规则。",
        "顺势三阶段采用结构外止损并允许超过3点固定上限，由动态仓位风险控制；目标同步调整为至少1.2R且不少于4点，修复20:23与20:30两轮回踩追多漏单链。",
    )},
    {"version": "0.7.108", "date": "2026-09-03", "changes": (
        "修复反转区复查与真实订单方向串线：反转区订单结算改为数据库方向条件更新，异向订单无法再把观察区标记为已下单。",
        "复查窗口新增订单方向、关联订单号和提交时间；既有数据库中的历史异向关联会显示为关联异常，不再伪装成正常成交。",
    )},
    {"version": "0.7.107", "date": "2026-09-03", "changes": (
        "修复14:50与15:00两笔空单止损位于结构内部：取消反转先行单1.5点止损硬封顶，统一采用最近12根一分钟K线结构极值外加至少0.15 ATR缓冲。",
        "结构外止损若超过3点最大风险则放弃入场，不再为了保留订单把止损压进局部高低点内部；多空完全镜像。",
    )},
    {"version": "0.7.106", "date": "2026-09-03", "changes": (
        "修复14:46上涨末段追多：上涨趋势中继回踩做多若已处于最近30根一分钟K线68%以上高位则拒绝，下降趋势中继做空镜像；真正反转不受普通中继位置门影响。",
        "修复14:42新空单47秒即平仓：十五分钟/一小时趋势止盈必须由开仓后新形成的周期K线拐点触发，禁止继承入场前已存在的慢周期退出状态；服务器止损与紧急保护保持即时有效。",
    )},
    {"version": "0.7.105", "date": "2026-09-03", "changes": (
        "修复12:45扫底漏单根因：一分钟启动质量门不再只接收单根实时K线，改为已收盘一分钟历史加当前实时K线的排序去重合并帧，同时间戳以实时数据覆盖。",
        "第二阶段锁存恢复与质量门复用完全相同的合并帧，避免第二阶段穿MA5和第三阶段小金叉被错误记录为一分钟启动质量门数据不足。",
    )},
    {"version": "0.7.104", "date": "2026-09-03", "changes": (
        "反转第一、第二阶段改为30分钟持久锁存：中间出现两根或多根小幅反向K线不再抹掉已成立候选；随后价格重新沿原方向收线并回到MA5同侧时，再次产生执行候选。",
        "保留相反结构失效、已成交、同侧订单/持仓去重、账户安全与服务器止损等硬保护；同步修正0.7.103遗留的包版本元数据不一致。",
    )},
    {"version": "0.7.103", "date": "2026-09-03", "changes": (
        "新增双周期均线形态分类：转向前1分钟与5分钟均为完整同向三均线排列时，标记为真正顶部/底部反转启动。",
        "双周期未满足旧趋势同侧完整排列时，按下跌中继反抽空或上涨中继回踩多分类，反转区漏单列表同步显示形态分类。",
        "一分钟第一阶段加第二阶段仍可独立触发；五分钟反向实体覆盖前一根过半只作超前增强证据，不等待五分钟变色。",
    )},
    {"version": "0.7.102", "date": "2026-09-02", "changes": (
        "一分钟反转三阶段恢复即时执行：第一阶段扫顶/扫底成立后，第二阶段价格有效穿越MA5立即产生实盘候选；五分钟只记录形态参照，不再等待变色，也不得否决一分钟入场。",
        "修复同一轮10秒询盘同时识别第一阶段与第二阶段时看不到本轮新锚点的问题；若第二阶段未成交，第三阶段MA5/MA10交叉仍作为强制补漏候选。",
    )},
    {"version": "0.7.101", "date": "2026-09-02", "changes": (
        "修正上升结构中的短暂平均K线转红被误判为扫顶做空：一分钟反转方向除五分钟平均K线同向变色外，五分钟MA5斜率也必须同向；五分钟MA5仍向上时，红色回踩只等待后续扫底做多，不允许反向追空。下跌结构完全镜像。",
        "修正反转区漏单列表的阶段串行：每个第二阶段或第三阶段只归属其前方最近的同方向一分钟反转区，不再把20:14等同一个触发时间重复挂到多个旧区域。",
    )},
    {"version": "0.7.100", "date": "2026-09-02", "changes": (
        "原策略实盘自动入口收口为一分钟平均K线反转区：第一阶段形成反转区后，第二阶段价格穿过MA5立即执行；若第二阶段漏单，第三阶段MA5/MA10交叉必须补漏。五分钟不判断大趋势，但必须出现与一分钟同向的平均K线变色，作为同一反转形态的跨周期确认。",
        "趋势延续、MA20回踩及其他旧策略候选继续写入审计，但不再自动下单，避免下跌局部底部追空和上涨局部顶部追多；一分钟反转入场统一采用更小的1.5点风险上限并保留OKX服务器止损。",
        "反转区漏单列表改为规范化三阶段明细，直接显示第二/第三阶段真实触发时间、漏单分类和简明原因，不再重复堆叠旧门槛的等待文字。",
        "一分钟反转持仓的普通MA5拐头只作首次止盈预警；当五分钟MA5仍沿持仓方向且五分钟平均K线尚未反色时继续持有，待五分钟延续条件失效后再交回原止盈链，服务器止损始终有效。",
    )},
    {"version": "0.7.99", "date": "2026-09-02", "changes": (
        "校正多周期职责：5分钟和15分钟用于识别顶部/底部翻转背景，不作为单纯趋势方向，也不得凭高周期共识直接追单；1分钟扫损/局部极值冻结后，价格穿MA5作为优先提前开仓点，若漏单则MA5穿MA10交叉必须再次产生补漏候选，同侧去重防止重复加仓。",
        "新增OKX原始K线到平均K线(Heikin-Ashi)的本地双轨转换：1分钟/5分钟平均K线负责反转区与MA触发识别，原始K线继续负责真实价格、扫损极值和服务器止损；平均K线早期反转触发不再被15分钟、1小时或滞后指标否决。",
        "建立平均K线反转区持续资料库：每个1分钟/5分钟转色区、方向、极值、关联未下单原因、成交状态和下一反转前漏单状态写入SQLite市场形态与执行审计，知识库文档固定后续分阶段复盘口径。",
        "放宽反转三阶段第一阶段：不再要求必须覆盖前一根K线；最近3根靠近12根相对局部高低点，且出现平均K线转色或最近成交量达到20根中位数1.20倍，即冻结为相对扫顶/扫底候选，仍必须等待价格穿MA5或MA5/MA10补漏触发才允许下单。",
        "新增独立/trade-webhook实盘事件入口：只接受带独立密钥、明确open_long/open_short/close_long/close_short、ETH合约、时区和120秒新鲜度的JSON；研究Webhook历史事件绝不进入实盘。",
        "Webhook开仓可在原策略无信号时独立提交0.05张实盘订单，并强制校验止损止盈方向、至少1R、重复事件、同侧仓位、每日亏损与价格新鲜度；OKX订单同时附带服务器标记价止损和最新价止盈。",
        "Webhook平仓按10秒无人值守循环逐层提交0.05张只减仓订单，直到指定多仓或空仓清空；每次回执与剩余层数写入审计。",
        "五分钟快照取消重复的无告警/无外部告警正常占位；PineTS窗口升级为Pine源码逐项投票、综合方向、原策略、Webhook、信号所有者、最终动作、服务器止损止盈及拦截原因的实盘综合决策表。",
    )},
    {"version": "0.7.98", "date": "2026-09-02", "changes": (
        "修复激进型实盘累计持仓超过10层（0.50张）时被错误判定为非法并故障停止：只要总持仓仍是0.05张的整数层，就继续无人值守运行和风险管理。",
        "每次新订单仍严格固定0.05张；非0.05整数层的遗留或外部仓位继续安全拦截，并显示独立的数量不对齐原因，禁止按错误数量自动处理。",
    )},
    {"version": "0.7.97", "date": "2026-09-01", "changes": (
        "PineTS/LuxAlgo升级为实盘双角色：原策略无信号时，4项可数指标至少3票强共识可独立产生候选；原策略有候选时，PineTS强反向共识否决，同向、中性或运行不可用均允许原策略继续。",
        "PineTS独立候选复用策略01同一安全执行链：固定0.05张映射、同侧去重、每日限额、收益空间、结构保护和OKX服务器标记价止损均不得绕过。",
        "新增PineTS共识、原策略方向、否决原因和信号归属审计；按已确认5分钟K线缓存，避免10秒轮询重复运行或同K线重复下单。",
        "发布包内置Node运行时、PineTS依赖和5个已核验LuxAlgo公开源码，避免仅开发电脑可用；运行失败明确降级为中性并留痕。",
    )},
    {"version": "0.7.96", "date": "2026-09-01", "changes": (
        "新增PineTS/现有策略逐K线对照回撤菜单和只读明细列表，显示五分钟节点、双方方向、未来30分钟收益及各自最大不利回撤。",
        "导入500根OKX公开已确认5分钟历史K线并建立第一批参考Pine对照数据；PineTS结果固定research_only，不进入实盘下单调用链。",
        "新增可替换Pine源码接口、OHLCV结构校验、逐K线时间对齐和现有策略研究快照对照；未提供LuxAlgo源码时明确使用codex-reference.pine，不冒充LuxAlgo专有信号。",
    )},
    {"version": "0.7.95", "date": "2026-09-01", "changes": (
        "OKX只读GET 51290交易机器人引擎临时升级纳入无人值守自动恢复，不再错误触发故障停止；POST结果不明仍禁止自动重试。",
        "签名API恢复后自动执行审计已保存API的等效流程，重新核验账户模式、多空杠杆、交易所最小张数、持仓、委托、保护单和风险状态后才继续策略循环。",
        "建立北京时间2026-09-01 16:45统一第一阶段节点，冻结OKX、Edge、30分钟标签、Webhook、交易明细、亏损复盘和共享实验的最大ID，后续新增数据进入第二阶段。",
        "五分钟快照新增Edge/OKX同节点MA偏差审计；偏差较大时明确标注仅作研究、不参与决策。基于当前小样本不扩大止损、不删除v112分支。",
    )},
    {"version": "0.7.94", "date": "2026-09-01", "changes": (
        "实盘激进型、保守型和稳妥型的新订单统一固定为0.05张；交易所最小下单单位仍核验为0.01张。",
        "修复零止损距离候选触发position sizing inputs must be positive并导致自动总闸故障停止：现在只拒绝该候选，自动循环继续运行。",
        "激进型正常循环询盘由20秒缩短为10秒；只读网络异常仍保留30/60/90/120秒退避，避免故障时密集请求。",
    )},
    {"version": "0.7.93", "date": "2026-09-01", "changes": (
        "五分钟明细将Webhook空白节点改为“无外部告警（正常）”，不再把事件驱动的正常静默误显示成故障。",
        "新增TradingView告警消息剪贴板脚本，密钥只从Windows用户环境变量读取并直接复制，不在控制台回显。",
        "明确Webhook只有TradingView/LuxAlgo实际触发告警时才产生事件；Edge与OKX仍负责连续五分钟采样。",
    )},
    {"version": "0.7.92", "date": "2026-09-01", "changes": (
        "Webhook端到端链路完成实机验证：公网HTTPS隧道转发到本机/research-webhook，正确密钥请求返回HTTP 200并落库。",
        "新增Windows独立研究接收器、Pinggy临时HTTPS隧道和无密钥回显测试脚本；研究事件固定decision_eligible=0，不进入下单决策。",
        "修复接收器Windows端口绑定兼容性问题，保留禁止地址复用和后台请求线程，避免旧独占套接字选项导致部分电脑请求无响应。",
        "测试脚本明确启用TLS 1.2；临时隧道地址可替换，密钥只从当前Windows用户环境变量读取。",
    )},
    {"version": "0.7.91", "date": "2026-09-01", "changes": (
        "修复Windows下桌面程序与独立接收器可能同时复用17885端口、导致Webhook请求归属不确定的问题；接收器改为独占端口。",
        "新增只读/receiver-status状态接口，仅报告Webhook是否配置、本机监听及公网HTTPS转发要求，不返回密钥内容。",
        "五分钟明细状态栏明确显示Webhook未配置、端口冲突或已配置，避免把零事件误认为接收链路已经正常。",
        "明确TradingView/LuxAlgo云端Webhook不能直接访问127.0.0.1，必须配置带密钥的公网HTTPS转发到本机/research-webhook。",
    )},
    {"version": "0.7.90", "date": "2026-09-01", "changes": (
        "策略01升级至v112：修复顶部走弱候选被滞后趋势指标一票否决的问题。",
        "仅当一分钟高位拒绝与五分钟向下CHoCH同时确认时，顶部专属分支才允许价格结构覆盖超级趋势和MACD零轴冲突。",
        "所有获准覆盖的顶部空单仍须通过最近五分钟实体支撑盈利空间门槛，禁止确认过晚后追空。",
        "十五分钟恢复多新增最近五分钟实体压力门槛，修复区间顶部上方空间不足仍追多的问题。",
    )},
    {"version": "0.7.89", "date": "2026-09-01", "changes": (
        "策略01升级至v111：早期双周期MA5反转与三连阴MA20回落入口新增最近五分钟结构盈利空间硬门槛。",
        "做空时按最近五分钟实体支撑、做多时按最近五分钟实体压力对称核算，不再用更远历史极值夸大可交易空间。",
        "剩余空间必须同时不少于1.2倍止损风险和0.8倍五分钟ATR；不足时仅记录观察，不提交订单。",
        "修复一分钟最佳位置及激进顶部入口绕过通用盈利空间检查后，仍可能在支撑附近追空的问题。",
    )},
    {"version": "0.7.88", "date": "2026-09-01", "changes": (
        "修复同方向多层持仓只有部分数量被OKX止损覆盖时，旧逻辑仅按品种/方向误判整个持仓已保护的问题。",
        "保护审计改为按algoId去重并比较持仓总数、有效止损覆盖数与未保护缺口数。",
        "首次发现保护缺口时连续获取3份独立OKX安全快照，排除急跌/成交期间持仓与算法委托更新不同步的瞬时假警报。",
        "连续3份快照仍确认保护数量不足时，立即市价只减仓平掉未被止损覆盖的数量；保留已保护层并禁止本轮新开仓。",
        "紧急平仓POST结果不明时不自动重试，立即故障停止并要求核对OKX，避免重复平仓或反向开仓。",
    )},
    {"version": "0.7.87", "date": "2026-09-01", "changes": (
        "修复Webhook只有解析器却没有HTTP接收路由的缺口，新增本机/research-webhook研究入口。",
        "Webhook支持独立环境变量密钥，也支持告警JSON内secret字段，不在日志或研究表保存密钥。",
        "无Webhook触发的五分钟桶改为显示无事件/尚无事件，不再误报为数据缺失。",
        "Webhook仍固定decision_eligible=false，接收器没有订单客户端，不进入真实下单链。",
        "修复OKX 51054请求超时被误判为永久故障：只读GET进入自动退避重连，POST结果不明仍立即停止并要求核对。",
    )},
    {"version": "0.7.86", "date": "2026-09-01", "changes": (
        "内置仅监听127.0.0.1的Edge五分钟只读快照接收器，随软件启动并写入研究样本库。",
        "同一五分钟表格合并OKX样本、Edge快照与已校验Webhook研究事件。",
        "浏览器与Webhook数据继续固定为decision_eligible=false，不直接触发真实订单。",
        "保留上次节点、统计区间和本次新节点，便于持续累计与离线复盘。",
    )},
    {"version": "0.7.85", "date": "2026-09-01", "changes": (
        "主界面新增5分钟快照菜单和独立只读明细窗口。",
        "按五分钟节点合并OKX自动样本、Edge快照、假设多空评估及30分钟前瞻标签。",
        "Edge未到达时明确记录缺失原因，不用其他数据冒充浏览器快照。",
        "列表不提供交易控件，不修改数据库，也不改变真实下单规则。",
    )},
    {"version": "0.7.84", "date": "2026-09-01", "changes": (
        "Python原生新增CHoCH、EQH/EQL、Premium/Discount区、CMF资金流及归一化动量。",
        "新增LuxAlgo/TradingView Webhook研究入口，执行密钥、时效、品种、事件和重复校验，固定不可决策。",
        "连续5分钟样本在未来30分钟完整后自动生成前瞻收益、最大有利/不利波动标签。",
        "不捆绑PineTS、不复制Premium闭源逻辑；研究表和标签表不进入实时下单决策。",
    )},
    {"version": "0.7.83", "date": "2026-08-31", "changes": (
        "八小时盘面对照结论接入为可审计市场上下文，不把浏览器DOM作为自动交易数据源。",
        "OKX已确认K线新增Supertrend、支撑压力、结构偏向、BOS、流动性扫损收回及FVG兼容原语。",
        "多维指标仍只辅助既有候选；仅在1m与5m三类独立证据同时反向时拒绝，不单独创造订单。",
        "新增Edge五分钟只读快照离线契约，强制记录上次节点、统计区间和新节点，并永久禁止进入决策链。",
    )},
    {"version": "0.7.82", "date": "2026-08-30", "changes": (
        "策略01所有新订单在最终提交前增加OKX K线多周期技术指标确认门；浏览器DOM不参与自动下单。",
        "独立计算1m/5m/15m MA、成交量、MACD与RSI；指标只否决低质量候选，不单独创造交易信号。",
        "反转、趋势延续和普通候选使用不同票数门，保留首次反转同时阻止低量、极端RSI追价。",
        "每次通过或拒绝保存完整指标快照，供入场证据、亏损复盘与后续离线校准。",
    )},
    {"version": "0.7.81", "date": "2026-08-30", "changes": (
        "MA5走平/拐头与小交叉只允许在同方向局部顶底、扫损或半覆盖冻结后的第一次有效转向下单。",
        "新增非38%启动距离门：距冻结点超过1.75倍一分钟ATR或距MA5超过1倍ATR时禁止追高追低。",
        "前方局部压力/支撑空间不足3.5点时拒绝；趋势延续继续要求先回踩MA5，大交叉只补漏。",
        "所有新单接入真实成本空间门：毛空间至少3.5点，扣除1点成本估算后净空间至少1点；止盈锁保持解除。",
    )},
    {"version": "0.7.80", "date": "2026-08-30", "changes": (
        "退役v0.7.62遗留的MA5锚点持仓锁定，不再清空已经成立的止盈信号。",
        "新开仓不再启用MA5锚点锁，旧持仓中的历史锁定标记也不再生效。",
        "一分钟、五分钟及升级周期原有MA5止盈条件成立后允许立即主动止盈。",
        "服务器结构止损、真实持仓核对和多空逐边退出继续保留。",
    )},
    {"version": "0.7.79", "date": "2026-08-30", "changes": (
        "修复旧方向冻结和滞后大交叉补漏压过最新一分钟局部反转结构的优先级漏洞。",
        "一分钟当前方向已经反向时，禁止旧方向大交叉补漏下单；做多做空完全镜像。",
        "最新反向局部结构成立后，旧方向冻结失去执行资格并记录作废原因。",
        "保留多空独立评估和双向持仓能力，只纠正同一方向信号的新旧优先级。",
    )},
    {"version": "0.7.78", "date": "2026-08-30", "changes": (
        "修复v102把任意局部底部/顶部直接升级成订单导致下跌中连续四次抢多的问题。",
        "最早抢多改为价格在MA5上方且MA5由下降转为走平/上拐；不强制阳线精确覆盖前阴线，抢空完全镜像。",
        "局部顶底、单独扫损和单独半覆盖恢复为冻结观察，不再各自单独下单。",
        "最早组合反转及极值扫损单止损封顶1.5点，首个盈利目标至少3点。",
    )},
    {"version": "0.7.77", "date": "2026-08-30", "changes": (
        "取消一分钟下部/上部38%硬否决门；动态区间位置改为只记录、不阻止有效形态下单。",
        "底部扫损、站上MA5、小金叉及后续MA5/MA20交叉不再因价格已经运行到49%、53%或更高位置漏单；顶部镜像。",
        "保留形态自身有效性、局部结构小止损、实时账户同向去重和服务器保护检查。",
        "旧策略版本遗留的open生命周期不再单独阻止新版本；真实持仓和委托仍以交易所实时快照为准。",
    )},
    {"version": "0.7.76", "date": "2026-08-30", "changes": (
        "做多与做空改为每轮同步维护的独立状态；一个方向的候选、冻结或持仓不再覆盖另一方向。",
        "只阻止同方向重复单；反方向有效触发仍可下单，允许OKX双向持仓模式下同时持有多仓和空仓。",
        "同轮双向信号分别持久保存并逐笔安全提交，避免单一方向仲裁造成另一侧漏单。",
        "修复同时存在多仓和空仓时跳过主动止盈检查；现按持仓方向逐边执行MA5退出管理。",
    )},
    {"version": "0.7.75", "date": "2026-08-30", "changes": (
        "局部扫损收回、价格重新站上弯曲MA5、小金叉由冻结观察升级为逐级正式入场机会。",
        "前一步已成交则同向持仓门禁止重复加仓；未成交才继续使用下一阶段补救。",
        "MA5穿越MA20及完整大交叉降为更晚的兜底，不再强迫早期结构等待。",
        "使用真实一分钟局部小止损；1点、1.5点均保留，超过3点才使用固定3点上限。",
    )},
    {"version": "0.7.74", "date": "2026-08-30", "changes": (
        "修复小交叉冻结分支抢占旧MA5快空决策槽的问题；新旧规则继续并行。",
        "一分钟两个局部高点走弱、阴线跌破MA5且MA5走平/下弯，仍可直接触发空单。",
        "五分钟高位阴线覆盖前阳线一半以上作为增强参照，不要求五分钟同步跌破MA5。",
        "实际订单仍由一分钟触发；冻结前置启动不会废除原顶部走弱、覆盖和盘中快空。",
    )},
    {"version": "0.7.73", "date": "2026-08-30", "changes": (
        "已有一分钟同方向冻结后，MA5穿越MA20成为完整大交叉前的正式启动点。",
        "做多要求MA5上穿MA20、MA5>MA10且价格在MA5上方；做空完全镜像。",
        "不再等待MA10穿越MA20，完整三均线大交叉继续作为未成交时的兜底。",
        "交叉无需仍位于绝对底部或顶部；一分钟负责入场，五分钟成交后接管MA5止盈。",
    )},
    {"version": "0.7.72", "date": "2026-08-30", "changes": (
        "冻结链改为任一证据即可建立资格，不要求三个阶段凑齐或按固定顺序出现。",
        "同方向重复扫损刷新最近候选；即使后面没有再次小交叉，一分钟大交叉启动仍执行对应方向订单。",
        "一分钟独立负责大金叉/大死叉入场，五分钟不得否决或延迟，只在成交后以MA5接管止盈。",
        "顶部和底部继续完全镜像；反方向冻结不会被错误用于当前大交叉。",
    )},
    {"version": "0.7.71", "date": "2026-08-30", "changes": (
        "底部扫损收回、价格上穿MA5、小金叉三个阶段分别冻结，后一次证据可覆盖最新入场与止损参考。",
        "冻结候选有效30分钟；只有一分钟MA5>MA10>MA20刚开始同步张口的大金叉才正式做多。",
        "顶部使用完全镜像的三次冻结和大死叉启动做空。",
        "撤回小金叉直接下单；没有冻结候选的大交叉只记录，禁止高位补追。",
    )},
    {"version": "0.7.70", "date": "2026-08-30", "changes": (
        "一分钟三阶段启动或反转信号通过局部下部/上部38%最佳位置门后，直接授权激进型先行单，不再等待五分钟主触发二次批准。",
        "五分钟只负责确认加分和已有仓位止盈升级；不再否决已经成立的一分钟最佳位置。",
        "最佳位置先行单把旧五分钟实体压力降为预警，不再使用普通1.5R硬拒绝；结构过大时继续使用固定3点服务器标记价止损。",
        "横盘中部交叉、位置不合格、晚到大交叉和普通追单不享受例外，继续禁止高位补追。",
    )},
    {"version": "0.7.69", "date": "2026-08-30", "changes": (
        "重新划清入场与升级权限：一分钟三阶段最佳位置负责全部下单，五分钟和十五分钟大交叉只负责已有仓位的MA5止盈升级。",
        "删除错过三阶段后的双周期均线发散立即追单、五分钟主门独立开仓和高周期排列直接开仓。",
        "五分钟小交叉保留记录与确认作用，不再独立产生订单；五分钟大交叉没有一分钟先行仓时只记录。",
        "反转位置门改为一分钟局部区间最佳位置；五分钟远端区间不再错误否决已成立的一分钟顶部/底部结构。",
        "修复多单生命周期被错误标记成downtrend_continuation_short的问题，按实际方向记录上下行中续分支。",
    )},
    {"version": "0.7.68", "date": "2026-08-30", "changes": (
        "保存8月29日盘面复盘：一分钟/五分钟小交叉、三均线大交叉、多周期MA5接管、3点止损与顶部走弱防漏空规则形成可迁移知识条目。",
        "强拉升后的突破回踩多单增加15分钟阴线实体覆盖前阳线暂停门：首次回踩只观察，等待下一根15分钟收盘重新确认，避免半程回踩过早追多。",
        "新增多周期走弱中续做空：5分钟阴线覆盖阳线后连续两根阴线并小死叉，且1分钟先小死叉后大死叉时允许中续做空。",
        "中部小死叉仍不得单独下单；只有完整的五分钟走弱序列和一分钟大小死叉接力成立时才可作为组合中续信号。",
        "已成立的反转、启动和中续信号若结构止损超过3点，继续使用以下单位置为基准的固定3点服务器标记价止损，并重新核验利润空间。",
    )},
    {"version": "0.7.67", "date": "2026-08-29", "changes": (
        "取消三周期MA5汇合下单：快速行情以一分钟金叉/死叉和局部结构先行，最多使用一分钟+五分钟双周期确认，十五分钟只作持仓背景。",
        "修复双向持仓被原方向MA5锚点提前返回拦截的问题：多单和空单可以独立同时存在，不再互相隐藏有效信号。",
        "结构预埋每方向最多两层；两层都成交后观察两根完整一分钟K线，减掉成交较差的一层，最终每方向最多保留一张。",
        "修复旧电脑数据库中的历史预埋状态误认领当前同方向持仓，并按保护动作和订单编号对交易界面弹窗去重。",
        "优化无人值守网络恢复：只读超时8秒，恢复探测10/15/20/30秒并以30秒封顶；启动审计临时失败不再停机，POST未知结果仍禁止自动重试。",
        "进入一分钟MA20有效侧后继续使用最近4根一分钟局部回踩/反抽极值止损，并跳过初始38%反转位置门。",
        "恢复预埋单分阶段保护：成交后先保留灾难止损，观察完成后再收紧正常止损；撤销v0.7.68的立即缩止损实验。",
        "本机保护状态缺失时，从OKX持仓、服务器保护单和本机预埋快照自动恢复；缺少快照或匹配保护单时通过交易界面中文弹窗告警，不再静默跳过。",
    )},
    {"version": "0.7.66", "date": "2026-08-29", "changes": (
        "双周期、三周期MA5反转只判断当前价格是否位于MA5有效侧，不限阴阳线、不限第几根，也不要求滞后的MA5已经走平或转向。",
        "价格突破一分钟MA20进入第三阶段后，使用最近4根一分钟回踩低点或反抽高点外止损，不再沿用最初扫损极值，也不再套用初始反转38%位置门。",
        "激进型取消15分钟、1小时、4小时对一分钟和五分钟快速单的否决权：一分钟触发、五分钟确认，十五分钟仅作升级参考，一小时和四小时只显示大方向。",
    )},
    {"version": "0.7.65", "date": "2026-08-29", "changes": (
        "统一交易用语：删除原先不准确的“试”字表述，改为局部小止损市价做空、局部小止损市价做多。",
        "同步修正策略规则、触发原因、订单说明及历史更新说明中的相关措辞，不改变触发条件。",
    )},
    {"version": "0.7.64", "date": "2026-08-29", "changes": (
        "一分钟局部高点下移后死叉可先行局部小止损市价做空；局部低点抬高后金叉可镜像局部小止损市价做多，不再强制等待五分钟交叉。",
        "五分钟在15分钟内完成同向交叉后升级为五分钟MA5趋势接管，已有同向仓位不重复加仓。",
        "该局部启动规则同时适用于趋势走弱区和横盘震荡区，15分钟与1小时仅作背景，不作为硬拦截。",
        "取消不对称的最近12根K线窗口，避免一分钟12分钟、五分钟60分钟造成漏单或过期误触发。",
        "追多完全镜像；成交原因明确显示一分钟交叉、五分钟交叉及实际分钟差。",
    )},
    {"version": "0.7.63", "date": "2026-08-29", "changes": (
        "新增一分钟与五分钟三均线发散启动独立规则：MA5、MA10下穿MA20并向下张口追空，向上金叉追多镜像。",
        "MA5与MA10允许在短窗口内先后换边，不要求同一根K线同时交叉；当前方向K线必须同步穿过MA5。",
        "规则仅用于15分钟、1小时同向的趋势延续启动，距五分钟MA20超过0.75 ATR不追，止损放最近一分钟局部极值外。",
    )},
    {"version": "0.7.62", "date": "2026-08-29", "changes": (
        "盘中/收盘下穿MA5的激进型顶部空单，成交时保存最近一分钟MA5局部峰值作为止盈解锁线；做多镜像。",
        "MA5未重新触达该水平前，禁止一分钟、五分钟、十五分钟及衰竭规则提前平仓；允许浮盈回吐或短时转亏，只保留服务器结构止损。",
        "修复横盘高点快空在三分钟后被十五分钟MA5普通止盈结束、毛盈利不足手续费并漏掉后续下跌的问题。",
    )},
    {"version": "0.7.61", "date": "2026-08-29", "changes": (
        "修复顶部候选延续到横盘偏低位置仍做空、底部候选延续到偏高位置仍做多的问题。",
        "反转单新增一分钟与五分钟双区间位置硬门：上部38%做空、下部38%做多；趋势延续单保持独立。",
        "订单弹窗恢复560×500，并使用可滚动明细框逐行展示周期位置、规则、入场、局部止损和止盈依据。",
    )},
    {"version": "0.7.60", "date": "2026-08-29", "changes": (
        "一小时方向显示纳入当前未收盘一小时K线，避免瀑布行情仍显示旧小时向上箭头。",
        "主界面通用等待文案改为区分五分钟收盘触发与盘中独立触发；成交弹窗放大并逐项展示方向、规则、入场、止损、移动止盈和持仓管理。",
        "新增瀑布趋势微型回抽续单保护：三周期空头排列时使用最近一分钟微型高点止损，减少远端结构止损造成的漏单；做多镜像。",
        "瀑布空单改由五分钟MA5下滑线直接管理；下滑期间忽略一分钟MA5和短线衰竭止盈，等五分钟MA5走平或上拐确认后退出。",
    )},
    {"version": "0.7.59", "date": "2026-08-29", "changes": (
        "修复新版OKX GET网络错误文本未被自动重试识别、导致SSL握手超时直接故障停止的问题。",
        "明确的只读GET/SSL故障改为30/60/90/120秒封顶的无限期自动重连；恢复后重新核验账户和风险状态再继续。",
        "POST结果不明、账户异常、保护失败和亏损熔断仍故障停止，避免自动重启造成重复下单或失控交易。",
    )},
    {"version": "0.7.58", "date": "2026-08-28", "changes": (
        "双周期、三周期MA5反转由单根穿线事件改为持续有效站位状态：价格在MA5上方且MA5走平/上扬即可做多，做空镜像。",
        "不限制K线阴阳、不等待收盘、不要求本根刚好穿线；第二根、第三根及后续K线只要状态有效均可完成30分钟汇合。",
        "触发当下全部参与周期必须仍在同一有效侧；MA10、MA20继续不作为门槛，局部极值小止损与逐级MA5接管保持不变。",
    )},
    {"version": "0.7.57", "date": "2026-08-28", "changes": (
        "取消激进型自动实盘的全局2分钟候选/扫描冷却和最近成功下单后的2分钟时间门槛。",
        "平仓后同向新形态或多空反手均可在下一次20秒轮询立即重新评估；保守型、稳妥型仍保留5分钟冷却。",
        "同一K线与候选去重、同方向持仓防叠仓、服务器保护单、单笔风险、每日亏损和交易次数上限全部保留。",
    )},
    {"version": "0.7.56", "date": "2026-08-28", "changes": (
        "将MA5盘中反转拆为两个独立级别：1分钟+5分钟先触发小波段，1分钟+5分钟+15分钟确认大波段。",
        "小波段持仓期间15分钟条件补齐时不重复加仓，直接升级为15分钟MA5最终接管；小波段已离场才重新评估大波段新单。",
        "多空完全镜像，30分钟候选窗口、局部极值小止损以及不检查MA10/MA20继续保留。",
    )},
    {"version": "0.7.55", "date": "2026-08-28", "changes": (
        "新增一分钟、五分钟、十五分钟MA5盘中汇合反转独立规则；一分钟先穿线后保存候选，五分钟、十五分钟可在30分钟（两个15分钟周期）内依次补齐。",
        "做多与做空完全镜像；不等待五分钟、十五分钟收盘，不检查MA10或MA20，止损只放最近一分钟局部极值外。",
        "反转仓止盈按一分钟MA5、五分钟MA5、十五分钟MA5逐级接管；成交提示窗口高度约扩大一倍，并移除顶部独立通过说明中的MA20字样。",
    )},
    {"version": "0.7.54", "date": "2026-08-28", "changes": (
        "新增错位底部接力多：扫底收回、单V、双底、第二底或较高低点回踩任一成立即可保存证据，双底不是硬门槛。",
        "一分钟只看阳线站上MA5以及随后MA5走平/上扬；五分钟允许第2至第4根连续阳线才上穿MA5，两周期均彻底取消MA10确认。",
        "延迟确认后使用最近一分钟回踩低点外的小止损；与原三阶段反转及其他做多触发并存。",
    )},
    {"version": "0.7.53", "date": "2026-08-28", "changes": (
        "新增下跌趋势多阶段续空：一分钟每次反抽MA5/MA10形成较低高点、阴线再破MA5均可重新评估，不再强制触及MA20。",
        "五分钟MA5第一次走平只预警；连续两根走平/上拐且价格收回MA5后才正常平空，多单镜像。",
        "同方向已有持仓不无限叠仓；十五分钟趋势接管、明确放量衰竭和全部账户安全保护继续保留。",
    )},
    {"version": "0.7.52", "date": "2026-08-28", "changes": (
        "一分钟高点阴线收盘下穿MA5且MA5走平或下弯即可早空，不再要求下穿MA10。",
        "新增五分钟高位阴线覆盖前阳至少一半的独立早空；不等待价格下穿MA5。",
        "两条规则与原有顶部形态并存，止损缩至触发附近最新微型高点；位置不高或止损过远仍拒绝。",
    )},
    {"version": "0.7.51", "date": "2026-08-28", "changes": (
        "顶部小止损早空改用最近10根一分钟K线的最新局部高点，不再引用远端旧头部扩大止损。",
        "顶部专属早空不再被最近五分钟支撑的1.5R利润空间硬拒绝，支撑只作第一反应位。",
        "普通趋势追空仍保留利润空间门槛；顶部形态迟到限制与结构保护距离继续有效。",
    )},
    {"version": "0.7.50", "date": "2026-08-28", "changes": (
        "统一所有激进型顶部小止损分支的独立执行权，不再逐个分支修补。",
        "盘中吞没、横盘高点破MA5、盘中破MA20、双顶/头肩顶、颜色反转和扫高收回确认后，跳过五分钟MA20与高周期方向的二次否决。",
        "普通趋势追空仍使用原有门槛；专属顶部形态确认、迟到限制和账户安全保护全部保留。",
    )},
    {"version": "0.7.49", "date": "2026-08-28", "changes": (
        "五分钟反抽MA20失败/顶部降低候选配合一分钟长阴下穿MA20并跌破短结构后，直接取得小止损早空执行权。",
        "顶部颜色反转同样纳入独立早空执行，不再被五分钟MA20快速上升重复否决。",
        "保留确认后运行超过0.35 ATR且未反抽重置时不追空，避免先等待趋势形成、再在低位追单。",
    )},
    {"version": "0.7.48", "date": "2026-08-28", "changes": (
        "顶部停滞小止损分支取得独立执行权：一分钟结构确认后，五分钟MA20快速上升只作背景，不再重复否决。",
        "双顶/头肩顶搜索由18根扩展至60根一分钟K线，并采用间隔明确的主要峰值，减少长跨度形态和微小毛刺漏判。",
        "顶部形态信号使用一分钟确认时间执行新鲜度检查；普通五分钟信号有效期由420秒调整为720秒，纠正按开盘时间计算造成的提前过期。",
        "继续保留局部最高点外小止损，以及账户、持仓、冷却、实时行情和服务器保护单硬门槛。",
    )},
    {"version": "0.7.47", "date": "2026-08-28", "changes": (
        "新增顶部停滞早空：一分钟/五分钟双顶和头肩顶作为独立顶部反转证据。",
        "局部高点必须触及或站上一分钟MA20，MA20下降、走平或轻微上升不超过0.12 ATR时允许候选。",
        "阴线有效下穿MA5并使MA5同步向下弯曲即可立即做空，不等待跌破MA20。",
        "形态止损只放本轮双顶或头肩顶最高点上方的小缓冲；原较低高点、吞没及MA20快空分支继续并行。",
    )},
    {"version": "0.7.46", "date": "2026-08-27", "changes": (
        "取消底部反转第二阶段的MA10硬门槛：单根阳线有效上穿并站稳MA5即可确认。",
        "新增连续两根阳线合力V形恢复：首根未形成完整单K触发时，第二根到达MA10仍可确认，不强制完整反包前阴。",
        "五分钟阳线收盘上穿MA5作为主触发，一分钟反转低点并站上MA20作为同向确认；止损使用本轮局部低点小缓冲。",
        "新增刚突破MA20快多：五分钟底部阳线已收盘站上MA5后，一分钟首次有效突破MA20立即执行，不等待回踩或均线发散。",
        "保留双周期均线发散无回踩追多：一分钟和五分钟同步向上发散且未超过2 ATR迟到上限时立即执行。",
        "新增一分钟/五分钟双底形态证据：第二底近似相等或轻微抬高、未有效跌破第一底且中间存在清晰反弹时成立。",
    )},
    {"version": "0.7.45", "date": "2026-08-27", "changes": (
        "取消最近8根一分钟/五分钟极值扩大止损及6/8根位于MA20同侧的旧趋势兜底，改用最新确认局部摆动结构。",
        "修正局部高点定义：反抽必须触及或站上MA20压力区；完全位于MA20下方的MA5小波动不再生成顶部走弱追空候选。",
        "形态专属做空使用本轮局部高点小止损，不再被远端五分钟旧高点扩宽；盈利后继续按MA5下滑与拐头管理止盈。",
    )},
    {"version": "0.7.44", "date": "2026-08-27", "changes": (
        "策略规则总表补齐五分钟唯一主触发的现行口径：盘中MA5/MA20快触发、下降反抽MA20空、上涨回踩续涨及底部持续站稳MA20早多统一展示。",
        "以2026-08-27 18:45:13为网络稳定分界；交易明细和亏损复盘增加数据阶段与记录质量，旧掉线数据保留归档但不直接驱动升级。",
        "共享实验拆分新旧阶段统计；历史样本继续保留，新阶段独立积累，避免网络超时污染回归率和策略判断。",
    )},
    {"version": "0.7.43", "date": "2026-08-27", "changes": (
        "按用户明确要求，将本部署所有欧易V5 REST请求固定绑定到https://www.tpouxyihas.com；公共时间和ETH-USDT-SWAP合约接口已验证返回code=0。",
        "桌面端ETH永续K线和API管理按钮同步改用该域名，不再打开旧官方域名。",
        "签名请求不自动跟随到其他主机；本次只修改程序和构建包，没有读取API密钥、下单、撤单或自动切换运行版本。",
    )},
    {"version": "0.7.42", "date": "2026-08-27", "changes": (
        "底部反转启动取消一分钟必须回踩MA20的硬门槛：突破后连续两根已收盘一分钟K线及当前K线持续站在MA20上方，并继续转强即可确认。",
        "回踩MA20守住仍作为加分证据；五分钟低点抬高和当前盘中上穿动态MA5仍是唯一开仓主触发，一分钟不得独立授权。",
        "止损在没有MA20回踩低点时改用最近一分钟结构低点、五分钟抬高低点及当前低点的最低者外侧。",
    )},
    {"version": "0.7.41", "date": "2026-08-27", "changes": (
        "新增成熟上涨趋势回踩续涨快多：五分钟与十五分钟多头排列，五分钟回踩MA5/MA10带守住后，当前未收盘五分钟重新站上动态MA5，由一分钟同步转强即可触发。",
        "本分支不再要求一分钟重新回踩MA20，避免把趋势延续误套成底部反转；但离五分钟动态MA5超过0.45 ATR仍禁止迟到追涨。",
        "三张原始截图和漏单复盘已归档到经验资料库；本次只完成程序、规则、测试与安装包升级，没有切换正在运行的版本。",
    )},
    {"version": "0.7.40", "date": "2026-08-27", "changes": (
        "新增五分钟抬高低点MA5盘中快多：最近两组五分钟局部低点抬高，当前未收盘阳线有效上穿动态MA5时进入主触发。",
        "一分钟已经站上MA20并完成一次有效回踩不破、随后当前阳线重新转强即可确认；不等待第二次回踩，十五分钟尚未完全翻多只作早期反转背景。",
        "修复独立五分钟主触发仍依赖旧候选方向的问题；三张原始截图已追加到实盘交易经验知识库。",
    )},
    {"version": "0.7.39", "date": "2026-08-27", "changes": (
        "修复五分钟下降结构反抽漏空：不再硬性要求反抽时MA5已经位于MA20下方；确认较低反抽高点、MA20没有重新快速上升、五分钟重新跌破MA20和一分钟局部顶部转弱后可做空。",
        "新增未收盘十五分钟确认：MA5暂时位于MA20上方时，当前十五分钟长阴必须覆盖前阳线一半并盘中下穿动态MA20；一分钟仍不能独立授权。",
        "一小时完整上涨结构尚未翻转但当前小时形成较低高点并转弱时，主界面显示顶部弱化↘；九张原始截图及复盘已归档到经验知识库。",
    )},
    {"version": "0.7.38", "date": "2026-08-27", "changes": (
        "新增五分钟下跌趋势反抽MA20快空：五分钟MA5位于下滑MA20下方，当前五分钟反抽MA20后盘中重新跌破，并由一分钟局部高点转弱确认即可做空；不等待五分钟收盘。做多镜像。",
        "激进型趋势持仓升级为十五分钟MA5接管：十五分钟MA5连续沿持仓方向运行后，普通一分钟/五分钟反抽不再提前止盈；十五分钟MA5走平或反向拐头时执行正常止盈。服务器结构止损及异常衰竭锁利继续保留。",
    )},
    {"version": "0.7.37", "date": "2026-08-27", "changes": (
        "新增五分钟盘中快触发：顶部走弱证据已由已收盘五分钟K线建立后，当前未收盘五分钟阴线覆盖前阳线至少一半并有效下穿动态MA5，再由当前一分钟阴线同步确认即可做空，不等待五分钟收盘；做多镜像。",
        "盘中快触发仍属于五分钟主触发，止损放五分钟局部结构外；五分钟MA20仍快速朝反方向时拒绝，一分钟不得单独授权。",
    )},
    {"version": "0.7.36", "date": "2026-08-27", "changes": (
        "五分钟唯一主触发保持不变；十五分钟和一小时由方案A机械同向门改为方案C背景提示，不再因完整高低点结构尚未翻向而错过五分钟早期顶底。",
        "十五分钟和一小时的局部高点降低、顶部收缩及MA20走弱继续写入中文原因；一分钟仍仅辅助，不能独立授权开仓。",
    )},
    {"version": "0.7.35", "date": "2026-08-27", "changes": (
        "实盘开仓升级为五分钟唯一主触发；一分钟旧分支全部降级为辅助证据，同一根五分钟K线只允许一个订单意图。",
        "五分钟、十五分钟、一小时统一以确认高低点结构判断趋势；采用方案A：十五分钟或一小时至少一个同向，另一个不得明确反向。",
        "四小时只作大趋势背景；移除三十分钟行情读取和方向参与；结构止损、利润空间、冷却、账户与网络安全门保持。",
    )},
    {"version": "0.7.34", "date": "2026-08-27", "changes": (
        "激进型实盘正常扫描与冷却等待由30秒缩短为20秒；实际两次扫描开始时间的间隔仍包含上一轮API请求和策略计算耗时。",
        "网络异常重试继续保持30/60/90/120秒递增退避，不因正常扫描加快而在代理不稳定时密集请求OKX。",
    )},
    {"version": "0.7.33", "date": "2026-08-27", "changes": (
        "新增一分钟顶部走弱两段式做空：较低高点后阴线跌破MA5为第一切入点；若轮询到达时止损过远则不追，继续保留候选。",
        "新增第二切入点：首次转弱后数分钟内小幅回抽一分钟MA20，不能站稳并再次盘中转阴时，按新的局部回抽高点保护确认做空。",
        "第二切入点要求MA20下降、走平或仅轻微上升，距MA20不超过0.45倍一分钟ATR；有效站稳MA20或明显突破原顶部时不触发。",
    )},
    {"version": "0.7.32", "date": "2026-08-26", "changes": (
        "新增六周期同向基础仓：4小时、1小时、30分钟、15分钟、5分钟、1分钟全部同向且当前无同向仓位时，允许建立一张基础顺势仓；仍执行MA20距离、结构止损、利润空间、冷却和账户安全门槛。",
        "修复底部三阶段迟钝：激进型严格扫低收回可执行第一阶段小风险多；第二阶段盘中同时收复MA5/MA10且仍在MA20下方；第三阶段增加MA20首次有效突破和首次回踩两次独立接力。",
        "底部/顶部极值阶段证据保留时间由5分钟延长至30分钟，避免第一阶段刚识别、后续确认尚未来临时提前失效。",
        "补齐主界面英文翻译，包括反抽下降MA20受阻、主动止盈已提交、结构止损距离和未保护仓位提示。",
    )},
    {"version": "0.7.31", "date": "2026-08-26", "changes": (
        "修正止盈说明：正常止盈不是价格触碰五分钟MA5，而是参照已收盘五分钟K线MA5；空单在MA5由下降转为走平或向上拐弯时必须止盈，多单镜像。",
        "收紧一分钟深长影紧急止盈：必须已有至少1.0倍ATR有利行程、收盘仍保留至少0.25倍ATR利润，且一分钟与五分钟MA5均不再沿持仓方向顺畅延伸；趋势仍强时继续持有。",
        "主界面将exit_submitted显示为“主动止盈已提交”，成交弹窗同步显示实际中文触发依据与方向化止盈规则。",
    )},
    {"version": "0.7.30", "date": "2026-08-26", "changes": (
        "修正底部反转第二阶段：一分钟阳线必须同时收盘站上MA5和MA10，且MA20仍位于价格上方，才属于底部微型反转做多。",
        "新增底部位置闸门：入场价必须位于近20根一分钟区间下部45%以内；已经站上MA20、进入中部或局部高点时，第二阶段失效，改等第三阶段MA20首次回踩或其他独立规则。",
        "实盘每日100次额度由候选周期改为成功交易：只有OKX接受订单并返回有效订单号才计数；观察、候选、风控拒绝和下单失败均不占额度。",
    )},
    {"version": "0.7.29", "date": "2026-08-26", "changes": (
        "为旧的一分钟MA5提前追空/追多增加趋势翻转闸门：MA20三根加速、MA5/MA10/MA20同向发散时，识别为一分钟趋势正在翻转，禁止按普通反抽/回踩提前入场。",
        "该闸门仅收紧普通趋势延续追单；独立顶部/底部反转规则仍各自按原有条件判断，不作覆盖。",
    )},
    {"version": "0.7.28", "date": "2026-08-26", "changes": (
        "收紧旧的一分钟MA5提前追空/追多：一分钟已强势穿越MA20时，不再以首次MA5回落/反弹入场；必须进一步收盘穿越MA10且MA5走平或反向，避免V形反弹中逆向开仓。",
        "自动成交提示改为展示实际中文触发依据与保护方式；生命周期同步记录具体规则分支，便于逐笔复盘。",
    )},
    {"version": "0.7.27", "date": "2026-08-26", "changes": (
        "新增激进型均线发散顶部快空：原MA5>MA10>MA20向上发散时，局部高点阳线转阴并盘中或收盘跌破MA5即可独立做空。",
        "趋势候选/等待区改为观察背景，不再暂停已经独立成立的MA20回踩候选；旧规则全部继续并行。",
    )},
    {"version": "0.7.26", "date": "2026-08-26", "changes": (
        "新增激进型顶部走弱MA5快空：冲顶后两组局部高点降低、MA20斜率收敛且十字整理后，阴线盘中或收盘有效跌破MA5即可小风险做空。",
        "不等待MA20完全下行；MA20下降、走平、微升或原上升明显收敛均可进入候选，距MA5超过0.90倍一分钟ATR仍拒绝迟到追空。",
        "每次命中写入独立候选事件；后续反抽失败再次跌破MA5可重新评估，但同方向已有持仓/委托、保护、冷却及利润空间闸门继续生效。",
    )},
    {"version": "0.7.25", "date": "2026-08-26", "changes": (
        "趋势主判断改为已确认5分钟摆动高低点：高高低高为上涨，低低高低为下跌，混合结构或假突破回归按横盘/反转等待区处理。",
        "15分钟、5分钟、1分钟方向箭头不再因冲突一票否决；15分钟仅作风险背景，1分钟MA5/MA10/MA20回踩后的重新转向负责精确入场。",
        "双向持仓下允许已有受保护多仓时独立评估空头反转/压力候选，已有受保护空仓时独立评估多头候选；同方向重复叠加仍被阻止。",
    )},
    {"version": "0.7.24", "date": "2026-08-26", "changes": (
        "为局部高点盘中阴线吞没快空增加一分钟MA20斜率过滤。",
        "MA20最近三根归一化斜率必须不超过0.08倍一分钟ATR：下降、基本走平和轻微上扬允许，明显陡峭上涨时禁止逆势做空。",
        "该过滤只约束本次阴线吞没快空，不覆盖或改变其他并行做空规则。",
    )},
    {"version": "0.7.23", "date": "2026-08-26", "changes": (
        "新增独立的局部高点盘中阴线吞没快空：未收盘阴线下跌实体超过前一根阳线完整长度并跌破其开盘价时立即小风险做空。",
        "止损放一分钟局部高点外；该形态不要求十字星，与横盘MA5快空、MA20快空及全部旧规则并行。",
        "空单新增连续两根长阴快速止盈：两根实体均至少0.65倍一分钟ATR且继续创新低时，在第二根长阴下方锁利；MA5拐弯止盈继续保留。",
        "激进型自动执行候选冷却由5分钟缩短为2分钟；保守型、稳妥型仍为5分钟。",
    )},
    {"version": "0.7.22", "date": "2026-08-26", "changes": (
        "纠正本次盘面分类：该形态属于横盘震荡区局部高点做空，不再强制归入下跌趋势反抽。",
        "新增独立的横盘高点MA5盘中快空：阳线后已收盘十字星，当前未收盘阴线盘中下穿MA5即可小风险做空。",
        "要求位于一分钟局部高点、横盘方向效率较低、五分钟MA20没有强斜率；距MA5超过0.45倍一分钟ATR禁止追空，止损放局部高点外。",
        "新增规则与MA20盘中快空及全部旧规则并行，不覆盖任何既有触发；止盈继续使用MA5跟随和长阴/长下影衰竭快速锁利。",
    )},
    {"version": "0.7.21", "date": "2026-08-26", "changes": (
        "复盘一分钟下降启动漏空：旧规则等待一分钟阴线收盘，无法在长阴盘中刚跌破MA20时及时市价做空。",
        "激进型新增盘中MA20快空：顶部阳转阴、1分钟MA20走平转下且5分钟MA20未强升时，未收盘阴线盘中跌破MA20即可局部小止损市价做空。",
        "开线已经位于MA20下方但继续转弱同样允许；低于MA20超过0.45倍一分钟ATR禁止追空。",
        "止损位于本轮一分钟局部高点外；原已收盘触发、三阴确认、做多规则及全部账户安全总闸继续保留。",
    )},
    {"version": "0.7.20", "date": "2026-08-26", "changes": (
        "新增OKX当前一分钟未收盘K线读取通道；原有策略仍只使用已收盘历史K线。",
        "新增激进型底部十字星后盘中MA5快追多：当前阳线有效上穿MA5即市价追多，不等待一分钟收盘。",
        "要求十字星已令MA5向上、当前价超过MA5至少0.05 ATR且距MA5不超过0.45 ATR，防止虚穿和迟到追高。",
        "第二阶段过晚不代表整轮作废：新增站上MA20后的第一次盘中回踩守住追多，要求MA20不再下降且五分钟MA5继续向上。",
        "各阶段止损固定在局部结构低点外，同一分钟去重，并继续通过至少1.5R、冷却、账户和每日亏损总闸。",
    )},
    {"version": "0.7.19", "date": "2026-08-26", "changes": (
        "复盘01:30后上涨漏单：系统曾两次识别一分钟MA5向上做多，但因原底部结构止损约7至8.5点超过旧短ATR上限而放弃。",
        "激进型底部MA5恢复多允许保留真实底部止损，专属距离上限调整为3倍一分钟ATR及1.2倍五分钟ATR；仍要求至少1.5R。",
        "新增两根已收盘十五分钟阳线恢复补进：五分钟MA5上穿MA10并上升、一分钟再次确认时做多，不等待十五分钟旧均线完全翻多。",
        "禁止使用尚未收盘的十五分钟阳线；其他普通入场的ATR距离限制不变。",
    )},
    {"version": "0.7.18", "date": "2026-08-26", "changes": (
        "将一分钟、五分钟MA5接力止盈完整镜像到盈利空单。",
        "一分钟MA5向上只触发空单止盈预警；五分钟MA5仍持续向下时继续持有空单。",
        "当一分钟MA5已向上且五分钟MA5也走平或向上时，确认双周期主动平空止盈。",
        "异常放量长阴、过度远离MA5及深度长下影衰竭继续优先紧急锁利。",
    )},
    {"version": "0.7.17", "date": "2026-08-26", "changes": (
        "新增盈利多单的一分钟、五分钟MA5接力止盈：一分钟MA5向下只触发预警，五分钟MA5仍上升时继续持有。",
        "当一分钟MA5已经向下且五分钟MA5也走平或向下时，确认双周期主动止盈，以争取吃到大趋势利润。",
        "一分钟异常放量长阳、过度远离MA5或深度长上影衰竭继续优先紧急锁利，不被五分钟延迟规则覆盖。",
        "入场结构止损、每日亏损、冷却、同向暴露和账户安全总闸均未放宽。",
    )},
    {"version": "0.7.16", "date": "2026-08-26", "changes": (
        "取消激进型、保守型、稳妥型至少4/5/6周期同向及六周期完全同向的实盘开仓硬门槛。",
        "实盘核心改为一分钟确认、五分钟结构、十五分钟方向；三十分钟、一小时、四小时只显示大趋势顺风、混合或逆风提示。",
        "激进型提前反转不再被三十分钟、一小时、四小时凑数否决；普通反转仍保留五分钟与十五分钟同时反向时的核心保护。",
        "六周期同向仅可影响已合格趋势仓的瀑布持有方式，不再决定能否开仓。",
    )},
    {"version": "0.7.15", "date": "2026-08-26", "changes": (
        "新增激进型小底部反弹多：5分钟、15分钟下跌背景中，一分钟长阴、十字星、首阳收复MA5且MA5向上拐头时小风险做多。",
        "新增激进型下跌局部高点追空：一分钟阳线后紧跟阴线且MA5走平向下，在五分钟、十五分钟同步下跌时追空。",
        "补齐盈利多单的一分钟MA5拐头主动止盈；多空均保留局部极值外结构止损、至少1.5R利润空间及原账户安全总闸。",
        "小底部反弹仅标记为高周期下跌中的反抽，不把五分钟、十五分钟误判为趋势反转。",
    )},
    {"version": "0.7.14", "date": "2026-08-26", "changes": (
        "复盘实盘订单QBVAL202608251544S：修复盈利空单错过一分钟MA5首次拐头后不再补退出的问题。",
        "新增最近两根一分钟深度长下影衰竭锁利；趋势与MA20冲突时明确显示反转观察区，不再给出果断单向箭头。",
        "Live接口收到50102后同步OKX服务器时间并重新签名；普通POST网络超时仍绝不自动重发。",
        "修复旧开仓时间无时区、平仓时间带时区导致复盘和自动总闸故障停止；补齐实盘状态中文翻译。",
    )},
    {"version": "0.7.13", "date": "2026-08-25", "changes": (
        "修复取消通道规则后旧工作区残留四个废弃参数，导致激进型自动总闸立即故障停止的问题。",
        "加载器只兼容忽略四个明确退役字段，其他未知字段仍严格报错；启动迁移同步清理旧字段并保留备份。",
        "旧通道配置错误完整翻译为中文，不再向主界面直接显示英文错误和英文参数名。",
        "本次不改变信号、下单、止盈止损、仓位及风险限制。",
    )},
    {"version": "0.7.12", "date": "2026-08-25", "changes": (
        "彻底取消一分钟与五分钟通道位置百分比规则；信号改由真实支撑压力、K线形态、均线方向、结构止损与盈利空间共同判断。",
        "修复OKX只读GET遇到SSL握手超时即永久故障停止的问题：单次GET自动重试3次，自动总闸连续5轮网络失败才安全停止。",
        "网络重试期间不提交新订单；POST下单遇到未知网络结果仍绝不自动重试，防止重复下单。",
        "保留最近一分钟局部结构止损和ATR距离上限；取消通道规则不等于取消迟到追单与风险保护。",
    )},
    {"version": "0.7.11", "date": "2026-08-25", "changes": (
        "修复旧时间记录没有时区、新记录带UTC时区时相减导致激进型自动执行故障停止的问题；历史无时区记录统一按UTC兼容读取。",
        "打包过程不控制已运行旧版本；本次确认故障来自时间格式不一致，而不是PyInstaller打包停止旧进程。",
        "主界面账户等级、持仓模式、多空方向、全仓/逐仓、合约状态、自动循环动作及常见异常全部改为中文显示。",
        "经验知识库新增1张故障界面原图，原始截图累计36张。",
    )},
    {"version": "0.7.10", "date": "2026-08-25", "changes": (
        "复盘真实订单QBVAL202608241729S：普通多周期同向分支在一分钟通道11.5%底部追空，入场2459.87却引用五分钟旧高点将止损放到2491.63。",
        "普通同向分支在一分钟通道下方25%禁止追空、上方25%禁止追多，避免趋势末端迟到入场。",
        "普通同向止损改用最近一分钟局部结构，并执行1分钟1.5 ATR、5分钟0.8 ATR双上限；过远则放弃下单。",
        "经验知识库新增4张原图及底部追空/远端止损缺陷复盘，原始截图累计35张。",
    )},
    {"version": "0.7.9", "date": "2026-08-25", "changes": (
        "激进型新增下跌延续双局部高点追空：一分钟反抽上穿MA20后，两组阳线紧跟阴线确认反抽失败。",
        "第二根确认阴线收盘时MA5须走平向下弯，止损放在两组局部高点较高者上方。",
        "该独立分支距五分钟MA20上限放宽至1.50 ATR，超过仍禁止低位迟到追空。",
        "经验知识库新增4张原图和PAT-20260825-004专项复盘，原始截图累计31张。",
    )},
    {"version": "0.7.8", "date": "2026-08-25", "changes": (
        "激进型将入场提前到第一根已收盘阴线实体下穿一分钟MA20；一分钟MA20须走平转下且五分钟MA20不得继续上升。",
        "止损使用本轮局部高点上方缓冲；三阴结构继续作为加强证据标签，不再是入场前提。",
        "新增只减仓主动退出：盈利空单在一分钟MA5转平/上拐或异常放量长阴衰竭时立即平仓，并记录退出原因。",
        "建立实盘交易经验知识库，归档本次对话27张原始截图及MA20转跌复盘。",
    )},
    {"version": "0.7.7", "date": "2026-08-25", "changes": (
        "激进型新增一分钟三阴确认转跌早空：三个阴线高点降低，首阴下穿MA20，后两阴持续收在MA20下方。",
        "要求一分钟MA20走平转下、五分钟MA20不再上升，并在第三根已收盘确认阴线附近触发。",
        "信号只认最新三根，仍执行结构止损、利润空间、高周期极端冲突与账户安全检查，禁止下跌过远后补追。",
    )},
    {"version": "0.7.6", "date": "2026-08-25", "changes": (
        "方向栏同时显示MA5/MA10排列与已收盘MA20斜率，避免把MA20已下弯的五分钟转跌等候区误写为普通上涨。",
        "MA5仍略高于MA10但MA20已下降时明确显示5m转跌等候；镜像显示转涨等候。",
        "保留一分钟反抽MA20后看跌拒绝的快速做空确认，不把转跌等候直接改成无确认追空。",
    )},
    {"version": "0.7.5", "date": "2026-08-25", "changes": (
        "修复主界面与激进型API小窗口共用按钮句柄导致的启停文字不同步。",
        "激进型自动总闸改为明确的正在启动、停止、正在停止和故障重启状态。",
        "正在停止时屏蔽重复点击，程序启动时自动总闸仍保持已停止。",
    )},
    {"version": "0.7.4", "date": "2026-08-25", "changes": (
        "主窗口重排为单一LIVE实盘控制台，隐藏模拟盘观察、品种切换、风险按钮和三套模拟API入口。",
        "激进型自动总闸、保守型/稳妥型扫描与精确确认语全部移入主窗口；自动启动时关闭实盘子窗口。",
        "策略规则表置顶新增激进型、保守型、稳妥型实盘规则；交易明细与亏损复盘改为汇总三个实盘子账户。",
        "实盘订单记录触发原因、生命周期及推定止盈止损平仓原因；订单成功提示改为主窗口弹窗。共享实验显示本轮新增和累计触碰。",
    )},
    {"version": "0.7.3", "date": "2026-08-24", "changes": (
        "激进型接入独立自动启停总闸，无需逐候选精确确认；重启后自动恢复关闭。",
        "复用策略01激进型Demo执行器，实盘传输边界将每个内部测试单位严格转换为0.01张，并限制交易与撤单接口白名单。",
        "按北京时间分页统计当日成交净盈亏；达到-5 USDT立即停止并撤销本策略预埋开仓单。每日最多100个自动候选周期，冷却5分钟。",
    )},
    {"version": "0.7.2", "date": "2026-08-24", "changes": (
        "激进型风控改为每日亏损5 USDT、每日最多100个候选、候选冷却5分钟。",
        "激进型实盘白名单映射现有Demo已验证规则：上下结构预埋、顶部早空、底部早多、中段顺势追单、候选反转与末端反转提前单。",
        "激进型界面取消逐笔精确确认输入；第三个API绑定只做审计，自动执行总闸仍保持关闭，防止保存API瞬间下单。",
    )},
    {"version": "0.7.1", "date": "2026-08-24", "changes": (
        "接入实盘候选一次性精确人工确认；同一候选只能消费一次，失败或结果未知不得重试。",
        "严格锁定ETH-USDT-SWAP全仓双向模式、0.01张市价单，并强制同单附带标记价止损和止盈。",
        "下单前重新执行账户审计；已有持仓或普通/条件挂单、双向100倍杠杆不符时拒绝发送。自动下单仍未开放。",
    )},
    {"version": "0.7.0", "date": "2026-08-24", "changes": (
        "保守型与稳妥型改为每日亏损上限5 USDT、每日最多100个候选、候选冷却5分钟。",
        "三个实盘子账户新增完全隔离的SQLite候选状态库，重复扫描同一候选不会重复计数。",
        "合格候选生成确定性编号及一次性精确人工确认语；本版本仍未接入OKX实盘POST下单传输。",
    )},
    {"version": "0.6.99", "date": "2026-08-24", "changes": (
        "实盘控制台扩展为激进型、保守型、稳妥型三个独立子账户API槽位，凭据文件和会话完全隔离。",
        "原已绑定实盘API无损保留在稳妥型槽位；激进型和保守型使用新的独立DPAPI密文文件。",
        "三个槽位禁止复用同一个API Key，并分别显示单笔亏损、每日亏损、每日次数、冷却和周期同向门槛。",
        "新增三账户实盘候选扫描；本版本仍无实盘下单能力。",
    )},
    {"version": "0.6.98", "date": "2026-08-24", "changes": (
        "实盘只读控制台新增合约张数与USDT名义价值换算：ETH-USDT-SWAP按实时ctVal、minSz、lotSz和标记价计算。",
        "最小实盘单固定使用交易所返回的最小张数作为未来API sz，不把约2.5 USDT名义价值误传为2.5张。",
        "当前仍仅为只读审计，不包含任何实盘下单能力。",
    )},
    {"version": "0.6.97", "date": "2026-08-24", "changes": (
        "新增独立 LIVE 实盘控制台第一阶段：仅允许本机加密绑定和白名单 GET 只读账户审计。",
        "实盘凭据写入独立 QuantBotWorkspace-Live 目录，并使用与 Demo 不同的 Windows DPAPI 加密域。",
        "只读审计显示账户模式、双向持仓模式、USDT余额、ETH合约规格、全仓杠杆、行情和当前持仓。",
        "实盘传输层拒绝所有非GET请求及未列入白名单的接口；本版本仍无实盘下单、撤单、划转、提现或设置杠杆能力。",
    )},
    {"version": "0.6.96", "date": "2026-08-23", "changes": (
        "顶部累计转弱必须同时位于已收盘5分钟和15分钟真实压力区；第一根颜色反转只记录候选，至少等待第二根累计阴线并跌破短结构。",
        "底部早多必须确认5分钟停止创新低；高周期至少四周期反向时，策略一早期逆势转色只观察。",
        "策略二市场单必须完成1分钟确认并通过持久生命周期同向仓门；普通结构止损过近等待二次确认，不机械放大风险。",
        "修复OKX平仓成交与生命周期按入场数量和最早平仓顺序配对；六周期同向续势使用服务器追踪退出。仅OKX Demo。",
    )},
    {"version": "0.6.95", "date": "2026-08-23", "changes": (
        "新增一分钟顶部累计转弱：上涨均线发散后的2至3根阴线在最多4根K线窗口内累计确认，不再仅依赖单根阴线或首次MA20下穿。",
        "新增下跌趋势局部反抽失败追空：空头均线发散后阳线形成局部高点、下一根有效阴线转弱即可排队；使用本轮局部高点保护，避免远端旧针放大风险。",
        "压力提前空的止损改用当前5分钟/1分钟局部顶部，远端15分钟孤立影线只作为压力证据，不再直接进入短线止损和利润空间计算。",
        "新增形态经验库及15根一分钟K线固定观察结果；只累计审计样本，不在线自调参数。修复无服务器止盈时本地MA5退出标签误作OKX触发类型导致市价单失败。仅OKX模拟盘。",
    )},
    {"version": "0.6.94", "date": "2026-08-23", "changes": (
        "取消结构预埋最近一分钟K线入场/止损碰撞硬拦截；历史碰撞不再直接否决后续支撑、压力和顶底针尖预埋机会。",
        "每张结构预埋单同时保存正常结构止损与更外层的标记价灾难止损；灾难距离按正常风险、价格比例和1分钟ATR取大，并受百分比与ATR双重风险上限约束。",
        "结构预埋成交后保留服务器灾难止损，等待入场K线及后两根完整1分钟K线；针尖收回则原地收紧到正常止损，结构失效或最长观察结束仍未收回则只减1张。",
        "新增结构保护状态持久化和附属止损唯一ID；断线或重启后可继续识别观察状态，止损修改失败时绝不先撤旧保护。仅OKX模拟盘。",
    )},
    {"version": "0.6.93", "date": "2026-08-23", "changes": (
        "策略01、策略02、策略03的普通Demo订单、结构预埋订单及共享实验验证订单，服务器止损统一改为OKX标记价触发；止盈触发口径不变。",
        "补建阶段二三板块复盘截止清单：交易明细rowid 127、亏损复盘rowid 290、共享观察id 3582、共享Demo验证rowid 66。",
        "下一次复盘严格从阶段三增量开始；阶段二遗留未关闭交易只回填原阶段，不混入新开仓成绩。仅OKX模拟盘。",
    )},
    {"version": "0.6.92", "date": "2026-08-22", "changes": (
        "修复亏损复盘跨时区快照匹配：未来快照和超过六小时的模糊快照不再自动归因，改为人工复核。",
        "结构预埋新增同一分钟K线入场/止损碰撞拦截，并把最小保护空间提高到0.35%或2.5倍一分钟ATR；止损不取消。",
        "修复共享实验与策略01共用账户时混入其他策略平仓的问题；只认到达本实验冻结止盈或止损边界的首个平仓订单。仅OKX模拟盘。",
    )},
    {"version": "0.6.91", "date": "2026-08-22", "changes": (
        "策略01、策略02、策略03共用六周期判断：1分钟、5分钟、15分钟、30分钟、1小时和4小时均读取已收盘K线。",
        "新增顶部阳线后首根阴线早空及底部镜像早多；无需等待穿越MA5/MA20，不再因1分钟MA20仍沿旧方向而漏掉顶底第一触发。",
        "新触发与原MA5优先、MA20兜底、预埋和趋势追单并行；策略02继续执行5分钟区间边缘硬门槛。仅OKX模拟盘。",
    )},
    {"version": "0.6.90", "date": "2026-08-21", "changes": (
        "结构预埋动态换线改为严格先挂新单、确认每张新单均取得有效OKX订单号后再撤旧单；任一新单失败或返回空订单号时保留全部旧线。",
        "预埋提交、重锚、启动恢复和断线恢复不再弹窗；仅当已登记的入场订单真实出现在OKX Demo成交记录后弹出一次成交提示。",
        "成交弹窗按订单号持久去重，避免轮询重复提示；仅OKX模拟盘。",
    )},
    {"version": "0.6.89", "date": "2026-08-21", "changes": (
        "三策略新增多空镜像MA5优先反转启动：顶部阴线首次下穿走平/向下MA5先做空，MA20下穿兜底；底部阳线上穿走平/向上MA5先做多，MA20兜底。",
        "新增MA5提前趋势追单：1分钟反抽/回踩MA20且5分钟也靠近MA20后，阴线下穿MA5追空、阳线上穿MA5追多；原MA10/MA20与三段结构追单继续并行。",
        "浮盈仓在快速拉升/下跌后出现已收盘1分钟长影针尖拒绝时优先兑现；否则空单在MA5走平/抬头、多单镜像时强制止盈。原5分钟MA5止盈兜底，亏损仓不因利润规则主动割肉。",
        "所有入口继续通过结构止损、利润空间、同K线去重、持仓保护与策略02区间边缘门槛；仅OKX模拟盘。",
    )},
    {"version": "0.6.88", "date": "2026-08-21", "changes": (
        "新增独立共享实验验证下单分支：只使用1分钟ATR+ADX动态偏离，ADX必须低于25；5分钟与15分钟同步趋势发散时禁止逆势均值回归。",
        "使用下一根1分钟K线有效的双侧post-only预埋，动态距离为2.5ATR乘ADX系数；成交后立即撤销另一侧，冻结MA5/MA10中轴止盈，并附带1.5ATR服务器结构止损；每日最多提交200张验证线。",
        "验证分支使用策略01独立Demo接口但不抢占账户；存在正式策略持仓、保护单或委托时让路，所有成交、手续费、净盈亏与三个正式策略完全隔离统计。",
        "累计100笔真实OKX Demo成交后自动停止新增，只提示人工评审，不自动并入策略01/02/03。原观察样本继续累计。仅OKX模拟盘。",
    )},
    {"version": "0.6.87", "date": "2026-08-21", "changes": (
        "三策略新增双周期均线发散市价追单：原三阶段错过后，已收盘1分钟与5分钟MA5/MA10/MA20同向排列、同向倾斜且间距扩张时立即追涨或追空，不等待回踩/反抽。",
        "追涨追空与原结构预埋、MA5/MA20穿线、扫损回收、候选确认和完整反转回踩并行，不覆盖旧触发；距5分钟MA20超过2.0 ATR时禁止末端追单。",
        "取消固定止盈和移动止盈；保留服务器结构止损。浮盈仓在已收盘5分钟反向K线触碰MA5且MA5走平或反向时强制本地止盈，多空镜像，并保留1分钟高位/低位恶化提前退出。",
        "仅OKX模拟盘；同K线去重、冷却、利润空间、持仓保护和账户安全门槛保持有效。",
    )},
    {"version": "0.6.86", "date": "2026-08-21", "changes": (
        "策略02顶部反转触发明确优先级：第一根已收盘阴线由上向下穿透1分钟MA5时优先排队做空；若错过，再由首次收盘跌破MA20作为第二触发。",
        "MA5、MA20首次跌破、原MA20反抽受阻、扫高回落及结构预埋继续并行存在，不覆盖旧规则；同一根K线同时满足MA5与MA20时只采用MA5优先证据。",
        "修复结构支撑多单维护提前结束本轮判断的问题：独立做空信号通过全部风控后，撤销相冲突的开仓预埋，再提交空单；无有效信号时双侧结构线继续保留。",
        "三策略统一允许OKX双向持仓：已有受保护多仓不阻止新的空头信号，已有受保护空仓不阻止新的多头信号；普通市价信号禁止无依据重复叠加。",
        "结构预埋例外允许每侧最多两层：普通压力线与更高扎针压力线可各成交1张空单，支撑侧镜像；保留未成交层，不因第一层成交撤销全部挂单，主动风险退出每次只减1张。",
        "任一侧缺少有效服务器保护时继续禁止所有新开仓；多空两侧风险退出分别执行，不因一侧退出自动平掉另一侧。",
        "仅OKX模拟盘；不改变持仓保护、利润空间、冷却、去重和账户安全门槛。",
    )},
    {"version": "0.6.85", "date": "2026-08-21", "changes": (
        "识别OKX模拟盘订单级51022 Contract suspended为合约暂停等待态，而非网络断线或策略损坏。",
        "三策略共享60秒暂停冷却，避免每5秒重复提交；旧线如仍在交易所则继续保留，排队状态不丢失。",
        "冷却到期自动探测，恢复交易后立即补挂双侧结构线，无需重新解锁。策略版本不变，共享规则升级为v23，仅OKX模拟盘。",
    )},
    {"version": "0.6.84", "date": "2026-08-20", "changes": (
        "支撑多单线与压力空单线在上涨、下跌、震荡中持续双侧呈现，不再因强趋势方向过滤消失。",
        "动态重锚改为先挂新线、确认OKX订单号后再撤旧线；新线失败时保留旧线并只清理新产生的半成品。",
        "短暂结构不确定继续保留旧线复核；成交、持仓冲突、价格穿越或保护硬失效仍可安全撤销。策略版本不变，共享规则升级为v22，仅OKX模拟盘。",
    )},
    {"version": "0.6.83", "date": "2026-08-20", "changes": (
        "修复三个策略解锁遇到OKX HTTP 503、code 50001临时服务不可用时立即失败的问题。",
        "三个解锁流程统一串行；安全GET和设置杠杆幂等配置使用0.5、1.5、3秒退避并重新签名，降低请求突发。",
        "普通下单POST继续保持未知网络结果不重发，避免重复下单；策略版本不变，共享规则升级为v21，仅OKX模拟盘。",
    )},
    {"version": "0.6.82", "date": "2026-08-20", "changes": (
        "复盘策略02订单3849577535580291072：旧区间2266.38-2320.00被尖峰拉宽，使2279.69误落入下部25%；改用位移后的局部区间，禁止把局部中部当底部。",
        "策略02新增底部三触发及顶部镜像：支撑/压力预埋、MA5收盘确认、MA20突破后的交叉点回踩/反抽确认，三者并行且不覆盖旧规则。",
        "主动风险退出仅在方向浮盈至少0.12%时允许；浮亏继续由服务器结构止损保护，不因几分钟不利走势抢先亏损平仓。",
        "三策略升级为v43/v42/v43，共享规则v20；仅OKX模拟盘。",
    )},
    {"version": "0.6.81", "date": "2026-08-20", "changes": (
        "新增顶部直接转弱首次下穿独立分支：五分钟上涨顶部失速并跌破MA5/MA10、MA5转下后，首次一分钟阴线有效下穿MA20即可排队做空。",
        "一分钟收盘下穿允许直接确认；仅影线下穿但收盘仍在MA20上方时，要求收盘紧贴MA20、MA20走平转下、实体至少0.30 ATR且成交量达标。",
        "新分支与原反转等待区、第一至第四次下穿、趋势延续、极值反转和结构预埋并行存在，不覆盖旧规则；策略02继续执行区间上沿硬门槛。",
        "三策略升级为v42/v41/v42，共享规则v19；仅OKX模拟盘。",
    )},
    {"version": "0.6.80", "date": "2026-08-20", "changes": (
        "修复共享预埋恢复扫描读取超时时中断整个观察周期的问题；恢复子任务失败后，三个策略仍继续独立检查并在后续周期复扫。",
        "恢复所有新提交Demo订单的桌面弹窗：普通市价单、结构预埋补挂和重锚均逐笔显示策略、时间与OKX订单号。",
        "已有有效挂单的例行扫描不重复弹窗；POST仍保持未知网络结果不自动重发，防止重复下单。",
        "共享规则升级为shared-rules-v18；三个策略交易规则版本不变；仅OKX模拟盘。",
    )},
    {"version": "0.6.79", "date": "2026-08-20", "changes": (
        "固定亏损复盘阶段一分界：239行，截止2026-08-20 13:19:42.948，后续新增亏损按严格晚于该时间独立统计。",
        "根据三策略亏损证据，对所有结构预埋叠加层追加最终共享风控，禁止后置叠加层绕过止损、利润空间和强趋势方向门槛。",
        "结构预埋止损至少覆盖入场价0.20%或2倍1分钟ATR；目标至少覆盖入场价0.30%、3倍1分钟ATR及1.5R。",
        "强趋势环境下普通结构预埋只保留趋势同向一侧；真正结构极值反转继续使用独立证据链。三策略升级为v41/v40/v41，共享规则v17；仅OKX模拟盘。",
    )},
    {"version": "0.6.78", "date": "2026-08-20", "changes": (
        "根据策略03最新Demo记录收紧高位横盘反转：近12根五分钟K线反复穿越MA20、方向效率低、均线压缩且MA20斜率不足时，只观察不下单。",
        "横盘压缩期间不把普通较低高点/较高低点和一分钟MA20穿越认定为可交易趋势反转，避免箱体内频繁反向开仓。",
        "保留带量五分钟MA20失败反抽/回踩的强证据例外；结构极值扫高/扫低、策略01与策略02规则不受影响。",
        "策略03升级为ma20_trend_retest_v40；仅OKX模拟盘。",
    )},
    {"version": "0.6.77", "date": "2026-08-20", "changes": (
        "软件启动后立即扫描并恢复有效双侧预埋与共享触发排队，不再等待点击开始观察或解锁。",
        "运行期间任一策略或共享行情发生连接异常后，在首个完整恢复周期立即重新扫描三策略预埋和全部触发排队。",
        "策略02所有恢复入口重新执行五分钟区间宽度、MA乖离和允许方向硬门，禁止恢复流程绕过策略02规则。",
        "恢复扫描保持激进型执行边界、人工解锁普通市价策略边界及OKX模拟盘限制。",
    )},
    {"version": "0.6.76", "date": "2026-08-19", "changes": (
        "持久动态双侧预埋：震荡、上涨和下跌均维护有效支撑多单与压力空单；不再因等待30分钟自动撤销，仅成交、结构失效、风险冲突或价格明显移动才撤销或重锚。",
        "策略02改为持久结构预埋，避免挂单闪现后短暂消失；保留区间边缘、利润空间与风险门槛。",
        "任一预埋成交后撤销其余开仓挂单，但继续执行同一轮风险退出检查；不利横盘、反向多周期和放量反向破坏可主动退出。",
        "软件启动、开始观察或解锁时先扫描并恢复有效双侧预埋及触发排队；仅OKX模拟盘。",
    )},
    {"version": "0.6.75", "date": "2026-08-17", "changes": (
        "新增共享多周期压力提前空：15分钟实体压力、5分钟拒绝与1分钟阴线下穿MA20联合触发，不强制要求三个高点连续降低。",
        "每次1分钟阴线下穿MA20都写入候选审计；可执行信号优先在实体压力位挂OKX Demo限价空单，市价确认继续兜底。",
        "启动、开始观察和解锁时先扫描并恢复有效排队；相同事件键去重，非激进型只记录不下单。",
        "三策略升级为v39/v37/v38，共享规则升级为v14；仅OKX模拟盘。",
    )},
    {"version": "0.6.74", "date": "2026-08-17", "changes": (
        "新增五分钟MA20突破后首次回踩实体支撑预埋：连续3根已收盘实体站上MA20后，在前实体低点优先挂Demo限价多单，失效才撤销，市价确认仍作后备。",
        "预埋价不再贪长影线极值；收益空间至少同时满足1.5R、0.8倍1分钟ATR和入场价0.12%，避免很小利润空间的订单。",
        "新增限价单和市价单共用的主动风险退出：多周期反转、放量反向破坏，或持仓30分钟横盘后不利破位时提前平仓；仅横盘等待不会退出。",
        "三策略升级为v38/v36/v37，共享规则升级为v13；仅OKX模拟盘。",
    )},
    {"version": "0.6.73", "date": "2026-08-17", "changes": (
        "修复反转等候区误挡顺势追空：十五分钟慢趋势仍向下时，五分钟看涨等候不再直接否决，但三次降低高点、一分钟转弱、结构和1 ATR利润空间门槛全部保留。",
        "新增共享瀑布底部小仓反弹：五分钟1.8倍放量长阴深度乖离并停止创新低后，优先挂一分钟下影结构Demo限价多单。",
        "限价错过后仅允许一分钟放量V形突破前高的市价兜底；止损在瀑布/V形低点下方，到五分钟MA10/MA20首个目标不足1.5R则取消。",
        "三策略升级为v37/v35/v36，共享规则升级v12；仅OKX模拟盘。",
    )},
    {"version": "0.6.72", "date": "2026-08-17", "changes": (
        "新增三策略共享的瀑布趋势持仓：只改变合格趋势延续单的退出管理，不新增或放宽入场条件。",
        "瀑布必须同时满足1分钟与5分钟均线同向、15分钟不逆向、5分钟实体至少0.75 ATR、成交量至少1.5倍以及1分钟连续加速。",
        "保留服务器结构止损，移动激活至少1R或0.8倍5分钟ATR，回撤至少0.65倍5分钟ATR或0.35R，避免固定小止盈过早离场。",
        "三策略升级为v36/v34/v35，共享规则升级v11；仅OKX模拟盘。",
    )},
    {"version": "0.6.71", "date": "2026-08-17", "changes": (
        "固化五分钟三次降低高点后的反抽做空：三段高点连续下降、MA20向下、结构未破坏，再由一分钟均线带转弱跌破确认。",
        "有效结构优先尝试Demo限价预埋空单，错过后保留市价确认；距五分钟MA20超过1 ATR禁止低位追空。",
        "三策略升级为v35/v33/v34，共享规则升级v10；仅OKX模拟盘。",
    )},
    {"version": "0.6.70", "date": "2026-08-17", "changes": (
        "取消策略01早期1分钟20根价格通道的触发、追高追低过滤和界面状态依赖；改由多周期、结构、均线、ATR与成交量证据决定下单。",
        "策略03完全取消翻转后的20根价格通道追踪，只允许首次有效MA20回踩/反抽当下结构；错过即作废。反向结构破坏也立即失效。",
        "新增一分钟放量止跌限价优先层：已收盘止跌线先在下影结构挂Demo post-only多单，至少1.5R；普通五分钟下跌趋势禁止逆势挂多，仅距5m MA20至少1 ATR的放量止跌极值允许小风险实验，后续V形市价确认继续兜底。",
        "顶部转空候选不再被旧价格通道过滤器误杀；三策略升级为v34/v32/v33，共享规则升级v9，仅OKX模拟盘。",
    )},
    {"version": "0.6.69", "date": "2026-08-17", "changes": (
        "新增共享双周期放量止跌小波段：五分钟上涨趋势回踩先出现放量止跌且不再有效创新低，再由一分钟放量V形阳线收回并突破前高触发市价多单。",
        "结构止损固定在一分钟V形下影线下方；止盈冻结在触发时五分钟MA20上方缓冲位，目标不足1.5R时禁止下单。",
        "策略02继续执行五分钟区间下部25%硬门槛，任何共享触发不得绕过；三策略升级为v33/v31/v32，共享规则升级v8，仅OKX模拟盘。",
    )},
    {"version": "0.6.68", "date": "2026-08-17", "changes": (
        "新增反转等候区双周期提前确认：5分钟反抽MA20上穿失败后，已收盘1分钟长阴下穿MA20并跌破短结构即可小风险做空；做多镜像。",
        "保留15分钟强趋势保护；没有5分钟MA20失败反抽证据时，单独一根1分钟阴线仍不得逆势抢跑。",
        "斐波那契50%至23.8%只记作方向纠结观察带，不作为独立下单触发；其中的小单仍须通过现有候选确认和全部安全门槛。",
        "三策略升级为v32/v30/v31，共享规则升级v7；仅OKX模拟盘。",
    )},
    {"version": "0.6.67", "date": "2026-08-17", "changes": (
        "反转等候区改为双情景分层：候选反转方向在五分钟MA20缓冲位与最近次高/次低预埋；原趋势延续方向在最近两个回踩/反抽结构预埋。",
        "上涨趋势回踩延续多单固定目标为下单时五分钟MA20上沿；下跌趋势镜像目标为MA20下沿，且每层至少1.5R。",
        "任一预埋成交继续撤销其余开仓单；未成交时保留一分钟扫低收回/扫高回落市价确认兜底。",
        "三策略升级为v31/v29/v30，共享规则升级v6；仅OKX模拟盘。",
    )},
    {"version": "0.6.66", "date": "2026-08-17", "changes": (
        "新增共享信号中心：任一策略识别高位扫高回落或低位扫损收回后，将确认K线、方向、结构止损和5分钟有效期写入SQLite。",
        "另外两套策略轮询较晚时可补读同一事件；仍须分别通过策略身份、区间位置、风险风格、账户状态、冷却和去重检查。",
        "三策略升级为v30/v28/v29，共享规则升级v5；仅OKX模拟盘。",
    )},
    {"version": "0.6.65", "date": "2026-08-17", "changes": (
        "修复策略03普通反转候选逆高周期抢跑：15分钟均线保持多头排列且MA20上升时，禁止仅凭1分钟回落抢先做空；空头趋势镜像禁止抢先做多。",
        "末端加速与结构极值扫高/扫低反转仍使用独立极值门槛，真正运行到高位后的反转机会继续保留。",
        "策略03升级为ma20_trend_retest_v28；仅OKX模拟盘。",
    )},
    {"version": "0.6.64", "date": "2026-08-17", "changes": (
        "新增共享分阶段预埋：V型极值、反转等候、反转确认第一单及趋势回踩第二/第三单均使用最近确认的5分钟局部结构分层挂单。",
        "每阶段保留原市价确认；预埋未成交或失效后，市价规则继续按原条件工作。",
        "任一规则成交后撤销全部其他开仓挂单；当前交易独占账户，平仓后按最新行情重新验证排队候选。",
        "三策略升级为v29/v27/v27，共享规则升级v4；仅OKX模拟盘。",
    )},
    {"version": "0.6.63", "date": "2026-08-16", "changes": (
        "策略02保留限价预埋与原市价入场双通道；预埋失效或错过后，仍有效的一分钟反转信号继续走市价兜底。",
        "同一方向最多分层预埋两个最近确认的五分钟局部极值，每张1张；一侧成交撤销反方向预埋。",
        "已有持仓、有效委托或状态不明时禁止市价兜底，避免重复开仓。",
        "策略02升级为range_pivot_reversal_v26。",
    )},
    {"version": "0.6.62", "date": "2026-08-16", "changes": (
        "策略02固定使用已收盘5分钟K线识别反转高低区，一分钟K线只负责精确触发下单。",
        "5分钟反转高低区宽度不足2.50 ATR时认定为横盘震荡，频繁测试模式也禁止开仓。",
        "所有反转分支必须通过5分钟上下25%边缘门槛，不能在区间中部绕过。",
        "新增5分钟MA5/MA10/MA20均线带乖离提前反转：距离至少1.50 ATR，一分钟扫损长影线收回即可触发，不等待V形或均线突破。",
        "满足乖离与区间条件时提前挂对应方向post-only限价预埋，并沿用动态重锚、失效撤销和重启恢复。",
        "策略02预埋优先选择最近确认的五分钟局部摆动高低点，新局部结构形成后动态重锚，不只等待长窗口绝对极值。",
        "策略02五分钟预埋成交后使用服务器固定止盈回归五分钟MA5/MA10带，重锚时重算、成交后冻结。",
        "策略02升级为range_pivot_reversal_v25。",
    )},
    {"version": "0.6.61", "date": "2026-08-16", "changes": (
        "按用户确认取消三策略扎针熔断停机：1分钟/5分钟长影线和异常放量不再统一暂停新开仓10分钟。",
        "提前反转、候选抢先和结构极值动态预埋继续实时评估；15分钟强趋势过滤、结构止损、利润空间和账户安全检查继续保留。",
        "主界面策略状态统一中文化，观察、异常、趋势状态和英文原因均转换为中文；ATR/ADX显示中文名称并保留指标缩写。",
        "策略版本升级为策略01 v28、策略02 v24、策略03 v26；共享规则目录移除扎针熔断规则ID。",
    )},
    {"version": "0.6.60", "date": "2026-08-16", "changes": (
        "新增结构极值预埋恢复器：每次点击开始观察，激进型先核对三个已保存的OKX Demo账户，缺单且条件成立时自动补挂。",
        "扫描结果逐个显示已补挂、有效单已存在、已重锚、已撤失效单、安全阻止、条件未成立或无API；不解锁普通市价策略。",
        "开始观察后每60秒持续对账；有效单保留，结构移动或等待30分钟后重锚，失效、重复及反向兄弟单按规则清理。",
        "策略01/02/03单独解锁安全校验成功后，也立即恢复各自结构预埋；已有本策略预埋单不再阻止解锁，其他委托或持仓仍会阻止。",
        "强趋势、结构无效、价格离开区间、保护空间不足、异常持仓或普通委托存在时仍拒绝强行补挂。",
    )},
    {"version": "0.6.59", "date": "2026-08-16", "changes": (
        "新增共享实验‘MA偏离插针回归’，主界面可直接查看累计触碰、回归、观察中、超时和最大不利波动。",
        "实验只读取OKX公开已收盘K线并写入本地SQLite，绝不连接实盘、绝不提交任何订单。",
        "1分钟同时记录固定2 USDT对照组与ATR+ADX动态组；5分钟、15分钟只使用ATR+ADX动态距离。",
        "严格以上一根收盘数据生成下一根假想限价，避免未来函数；触碰后冻结均线目标并跨重启去重累计。",
    )},
    {"version": "0.6.58", "date": "2026-08-16", "changes": (
        "修复OKX 50102 Timestamp request expired反复出现：明确收到该错误后调用官方公开时间接口，计算OKX服务器与Windows本机时钟的毫秒偏移。",
        "同步成功后用校准时间生成新时间戳和新签名，避免策略状态因本机时钟漂移长期停在error。",
        "GET查询在50102后安全重试；POST只在OKX明确返回50102、证明原请求已拒绝时重签并重试一次。",
        "POST遇到TLS、断网或读取超时继续保持单次提交，不会因网络结果未知而重复下单。",
    )},
    {"version": "0.6.57", "date": "2026-08-16", "changes": (
        "结构极值预埋单由2分钟固定到期改为滚动有效：每轮观察复核结构、趋势、价格区间和保护空间。",
        "结构价明显移动时撤旧挂新；单张挂单每30分钟换新，条件持续成立时可长期滚动续留。",
        "趋势过强、结构失效、价格跑出区间或盈利空间不足时立即撤单，不允许无条件永久挂单。",
        "策略规则表新增共享策略栏，完整归纳共享触发、退出、保护、动态挂单及激进/保守/稳妥三种风格。",
        "主界面当前选中的交易风格按钮会置灰禁用，切换后新选中项立即置灰，仅影响后续新信号。",
    )},
    {"version": "0.6.56", "date": "2026-08-16", "changes": (
        "新增永久开仓触发快照：每笔普通市价单和结构狙击预埋单保存策略版本、方向、规则分支、完整触发原因、行情上下文、入场参考价、止盈止损与订单号。",
        "亏损复盘表新增开仓触发快照列；平仓亏损时优先匹配同策略、同方向、最近且尚未使用的真实开仓快照。",
        "匹配成功后，初步原因、证据和优化建议引用真实下单依据；同一快照只允许关联一笔亏损，避免重复归因。",
        "历史订单没有当时快照时继续明确标记历史订单无快照和待人工复核，不根据后续行情补造原因。",
    )},
    {"version": "0.6.55", "date": "2026-08-16", "changes": (
        "主界面新增亏损复盘入口，集中显示策略01/02/03每笔净亏损的原因分类、证据、优化建议、可信度与处理状态。",
        "平仓生命周期确认净亏损后自动生成复盘；打开复盘中心时还会从三个独立OKX Demo账户补录缺失的近期亏损成交。",
        "新增手续费吞噬、保护过窄、入场后快速失效、反转未成立、趋势延续失败和待进一步归因等保守诊断类别。",
        "证据不足的订单明确标记待人工复核；优化建议只进入候选列表，不会自动修改策略参数或放宽安全规则。",
        "同一订单和近似生命周期复盘自动去重，为后续按分支统一汇总、回测验证和版本升级保留依据。",
    )},
    {"version": "0.6.54", "date": "2026-08-15", "changes": (
        "修复三套策略固定止盈止损只有相对盈亏比、没有绝对距离下限的问题。",
        "短线固定保护统一要求：止损距离至少为入场价0.20%或2倍1分钟ATR；止盈距离至少为入场价0.30%、3倍1分钟ATR和1.5R三者中的最大值。",
        "结构高低点狙击预埋单同步执行相同下限；结构点到MA5/MA10的回归空间不足时直接放弃订单。",
        "短线固定止盈与止损统一使用最新成交价触发，使一分钟K线穿越保护线时与用户看到的图表一致。",
        "新增OKX逐条下单回执校验；即使顶层返回成功，只要某一订单sCode失败，程序也会明确报错而不会误记为提交成功。",
    )},
    {"version": "0.6.53", "date": "2026-08-15", "changes": (
        "新增三策略共享的结构极值预埋狙击：用已收盘5分钟/15分钟K线确认前高前低，在结构高点挂空、结构低点挂多的Demo post-only限价单。",
        "每张预埋单原子附带标记价结构止损和MA5/MA10回归固定止盈；ADX或MA20斜率显示15分钟强趋势时禁止逆势预埋，2分钟未成交自动撤单。",
        "允许已有1张同方向持仓时再预埋1张同方向狙击单，最多2张；一侧成交撤销反方向挂单，多空同时持仓或达到上限后禁止继续加仓。",
        "新增三策略共享退出策略：短周期反转、回踩和普通确认统一使用最新成交价固定止盈；仅高周期同向趋势延续使用移动止盈。",
        "策略01/02/03均调用同一共享退出模块，避免各策略分别出现上影线激活移动止盈后利润回吐的问题。",
        "策略02按行情分支拆分退出方式：1分钟/5分钟区间及极值反转改用最新成交价触发的固定止盈，趋势延续分支才使用移动止盈。",
        "避免上影线短暂激活移动止盈后大幅回吐；固定目标继续满足止盈距离大于初始止损距离的统一约束。",
        "扩大主界面策略状态区并移除无用API提示，完整保留三套策略的触发条件、测量结果、持仓与止盈止损管理说明。",
        "统一策略01独立验证交易状态显示：主界面和弹窗使用已解锁/未解锁一致文案，并在已解锁时禁用重复解锁按钮。",
        "统一策略02每笔订单的止盈止损收益风险约束：止盈距离至少为初始止损距离的1.10倍，避免止盈空间小于止损空间。",
        "修复主界面底部策略03入口被窗口可视区域裁切的问题，三套策略入口现在在同一可见区域完整显示。",
    )},
    {"version": "0.6.46", "date": "2026-08-15", "changes": (
        "持仓管理状态不再覆盖触发原因；策略01/02/03状态行会保留最近一次形成订单的触发条件。",
        "客户端重启后从未平仓交易生命周期恢复触发原因，避免只看到已有持仓而无法追溯下单依据。",
    )},
    {"version": "0.6.45", "date": "2026-08-15", "changes": (
        "主界面移除暂停观察、策略02观察和策略03观察入口；开始观察统一启动三套策略的持续观察。",
        "策略01 API与独立验证交易改为独立弹窗，主界面底部与策略02、策略03统一显示三行策略入口。",
        "主界面保留策略状态、风险档位、品种和交易明细，减少凭据控件占用并避免重复观察入口。",
    )},
    {"version": "0.6.44", "date": "2026-08-15", "changes": (
        "新增激进型、保守型、稳妥型三档交易风格，统一控制共享触发规则，策略代码不再复制。",
        "交易界面新增风险级别切换按钮；切换会同步策略01/02配置，仅影响后续新信号，不改变已有持仓和服务器保护单。",
        "激进型允许提前反转和候选抢先；保守型只启用趋势确认、突破回踩和MA20回踩；稳妥型只接受完整确认后的延续规则。",
        "所有档位继续执行Demo、安全熔断、结构止损和盈利空间过滤。",
    )},
    {"version": "0.6.43", "date": "2026-08-15", "changes": (
        "补齐共享三阶段反转第一阶段的顶部镜像：高位扫破近10根高点后，已收盘阴线回落针体55%以上可提前做空。",
        "顶部早空要求靠近5分钟结构阻力并出现放量或大振幅衰竭；只有上影线没有收盘回落时继续等待，止损置于扫高最高点上方。",
        "策略01、02、03全部接入共享顶部早空；策略02仍执行结构上部只做空、下部只做多硬门，并保持极值与趋势成绩隔离。",
        "共享架构和桌面规则表明确三阶段：极值扫损早入场、候选区1分钟确认、5分钟反转确认后MA20首次回踩。",
    )},
    {"version": "0.6.42", "date": "2026-08-13", "changes": (
        "三套策略共享趋势回抽续势组件：下跌反抽追空与上涨回踩追多采用同一套完全镜像的结构判断。",
        "上涨回踩追多要求5分钟与15分钟多头共振、回踩MA10/MA20附近形成更高低点，并由1分钟强阳突破短结构高点确认。",
        "双向续势统一限制距5分钟MA20不超过1 ATR，并使用最近1分钟结构与MA20外侧缓冲止损，避免远离均线追单。",
        "修复策略02执行层曾硬编码空单价格边界、保护方向与止盈方向的问题；多空现在按信号方向对称执行。",
    )},
    {"version": "0.6.41", "date": "2026-08-13", "changes": (
        "策略03低位扫损收回提前做多改用真实5分钟扫损结构低点止损，不再误用后续局部低点。",
        "新增0.35 ATR信号过期与最大风险距离校验；真实结构止损过远时直接放弃，不以放宽止损勉强入场。",
        "提前做多必须拥有至少1.2R的最近结构盈利空间，移动止盈也延后至1.2R启动。",
        "策略03升级为ma20_trend_retest_v19，并补齐规则档案、注册表和自动化测试。",
    )},
    {"version": "0.6.40", "date": "2026-08-13", "changes": (
        "策略02新增低位扫损收回早触发：结构下部出现1分钟放量或大振幅新低后，首个已收盘阳线收回针体55%以上即可提前做多。",
        "同一共享规则已接入策略01与策略03真实执行链路，适用于趋势等待期和即将反转期；不再只是规则注册表中的声明。",
        "早触发不再等待MA5/MA10金叉或重新站上1分钟MA20；只有下影线而没有收盘收回时仍保持候选，禁止盲目接飞刀。",
        "早触发止损放在扫损最低点下方ATR缓冲；继续保留低位禁止做空、扎针熔断、利润空间和服务器保护规则。",
    )},
    {"version": "0.6.39", "date": "2026-08-13", "changes": (
        "修复策略02共享候选信号绕过高低位门槛、在结构下沿做空的问题。",
        "策略02在信号生成及订单提交前双重校验实时区间位置：低位禁止做空，高位禁止做多。",
        "趋势延续追单继续独立统计，但进入下部25%后停止追空并等待低点做多；做多镜像。",
    )},
    {"version": "0.6.38", "date": "2026-08-13", "changes": (
        "三策略共享新增顶部走弱快速候选：5分钟形成较低高点后提前武装做空候选，底部较高低点做多镜像。",
        "真正触发改为已收盘1分钟长实体穿越1分钟MA20；空单由长阴实体从MA20上方向下穿线，多单完全镜像，不等待5分钟四根反转确认。",
        "轮询会回看最近6根已收盘1分钟K线，修复确认发生在上一根而读取尾K线已变化造成的漏单；确认后已运行超过0.35 ATR仍取消追单。",
        "新增候选区反抽二次追单：价格回到1分钟MA10/MA20带附近后，已收盘反向实体再次突破最近两根短结构即可重新武装，不把0.35 ATR取消视为永久失效。",
    )},
    {"version": "0.6.37", "date": "2026-08-13", "changes": (
        "反转候选区不再等同于完全禁开仓：暂停旧趋势延续单，但允许候选方向由已收盘1分钟K线严格确认后抢先小风险入场。",
        "候选空单要求1分钟MA5<MA10<MA20、实体至少0.30 ATR、放量跌破前三根低点并收在K线下部35%；多单镜像执行。",
        "修复历史已确认趋势掩盖当前新候选区的状态机优先级；候选反转独立标记，不混入确认趋势延续追单统计。",
    )},
    {"version": "0.6.36", "date": "2026-08-13", "changes": (
        "三策略共享趋势状态机：明确区分上涨、下跌、横盘、反转候选区、等待区及反转成功，禁止把1至3根MA20换边误判为新趋势。",
        "反转成功要求连续4根换边，其中至少3根保持0.10 ATR有效距离、MA20三根归一化斜率至少0.05 ATR同向，并以实体收盘突破前8根实体结构。",
        "5分钟反转若遭15分钟强趋势反对则继续等待；确认成功也不在确认K线追单，必须等待后续首次MA20回踩或反抽。",
    )},
    {"version": "0.6.35", "date": "2026-08-13", "changes": (
        "此版本曾采用另一套REST与网页入口；该旧入口现已废止并从当前安装包清除。",
        "当前部署的K线、API管理和签名请求统一锁定到用户指定访问域名。",
        "复盘上涨回踩：箭头前一处属于合格候选；当前5分钟MA20测试区仍须等待已收盘1分钟强阳突破前高并重回快均线后才能触发。",
    )},
    {"version": "0.6.34", "date": "2026-08-13", "changes": (
        "取消策略01、02、03同一5分钟趋势及连续同方向最多3单的固定限制；后续正式运行也不按订单次数拦截。",
        "每个候选订单仍独立通过已有仓位、冷却、每日风险上限、扎针熔断、入场位置、止损距离及手续费后盈利空间检查。",
        "建立稳定规则ID共享目录与双向5分钟结构盈利空间组件，为策略04、05及未来组合策略复用触发规则。",
        "修正策略01 MA20回踩多单的精准1分钟止损被旧5分钟结构错误放宽的问题。",
    )},
    {"version": "0.6.33", "date": "2026-08-13", "changes": (
        "策略02升级为range_pivot_reversal_v10，正式拆分为相对极值反转与趋势延续追单两个执行分支。",
        "相对极值反转使用QBRX订单前缀，专门统计高点做空、低点做多及末端加速反转；趋势延续使用QBRC前缀。",
        "信号、下单意图、订单事件及交易生命周期全部保存branch字段；次数上限按分支计算，成交数量、胜负、毛盈亏、手续费和净盈亏按分支独立汇总。",
        "旧数据库自动增加分支字段，历史无法可靠归类的数据标记为legacy_unclassified，不强行并入任一新分支。",
    )},
    {"version": "0.6.32", "date": "2026-08-13", "changes": (
        "三策略统一新增扎针熔断器：使用已收盘1分钟与5分钟K线，同时检测极端振幅、长影线与异常放量。",
        "触发条件为振幅至少3 ATR、最长影线至少为实体3倍且不小于1.5 ATR、成交量至少为前20根均量3倍。",
        "熔断后暂停新开仓10分钟，等待5分钟收回与1分钟结构确认；已有持仓和OKX服务器保护单继续管理。",
        "被熔断的信号在创建订单意图之前拦截，不计入每趋势最多3单，不追针尖、不在针尖盲目猜底或摸顶。",
    )},
    {"version": "0.6.31", "date": "2026-08-12", "changes": (
        "复盘策略01在1886.21追空样本：新版追空曾正确拦截，但旧MA20回抽分支以2.447 ATR宽止损绕过防追价，现已修复。",
        "三策略所有顺势追单与MA20回抽统一使用利润空间闸门：确认价距5分钟MA20最多1.0 ATR，超过即放弃。",
        "信号确认后到读取最新成交价之间若继续沿趋势运行超过0.35 ATR，取消本轮订单；上涨做多采用完全镜像规则。",
        "结构止损小于2.5 ATR不再能单独放行迟到信号，必须同时通过利润空间闸门。",
    )},
    {"version": "0.6.30", "date": "2026-08-12", "changes": (
        "策略01、策略02、策略03共同新增下跌趋势追空：5分钟与15分钟必须同时满足MA5<MA10<MA20且MA20下降，再等1分钟反抽均线后以阴线跌破前低确认。",
        "追空禁止在低位直接追价：确认价低于5分钟MA20超过1.5 ATR时放弃；初始止损置于1分钟反抽高点或MA20上方并保留缓冲。",
        "三策略统一执行双向次数上限：同一方向连续下单最多3次，上涨趋势做多与下跌趋势做空完全镜像，达到3次后禁止第4次。",
        "追空单采用服务器动态移动止盈；盈利达到设定风险倍数后启动，保护价只向盈利方向移动，以争取保留较长下跌趋势利润。",
    )},
    {"version": "0.6.29", "date": "2026-08-12", "changes": (
        "三套策略共同植入MA20复位后的第三次末端加速失败规则，并加入下跌末端完全镜像做多。",
        "允许此前出现2次、3次、4次或更多扩张；取最近两次有效波峰/波谷，价格回到MA20附近复位后，下一次扫高/扫低达到2 ATR乖离，再由1分钟1.5倍放量反转K线确认。",
        "末端反转单0.5R启动服务器动态追踪，使用ATR/风险自适应回撤；保护价格只沿盈利方向移动，以保留大幅趋势空间。",
    )},
    {
        "version": "0.6.28",
        "date": "2026-08-12",
        "changes": (
            "策略02升级为range_pivot_reversal_v5，新增极端乖离摸顶做空：5分钟或15分钟最高价距MA20至少2 ATR，并出现长上影拒绝或大阴线快速吞回。",
            "高周期只定义候选高点；必须再由已收盘1分钟大阴线跌破前低、收在K线下方35%确认，普通上涨和单纯价格偏高不得猜顶。",
            "摸顶空止损置于最新高点上方小缓冲；移动止盈由普通分支的1.5R提前为1R启动，确认后若价格已运行超过0.5R则禁止追空。",
            "原有高低区、上部25%做空、下部25%做多、窄幅过滤及普通趋势安全规则全部保留。",
        ),
    },
    {
        "version": "0.6.27",
        "date": "2026-08-12",
        "changes": (
            "复盘策略01约1910.71入场样本：原一分钟确认只要求阳线突破前高，容易把冲高后的弱反弹当成回踩完成；策略01升级为v12。",
            "放量突破分支改为两阶段确认：必须先出现触及突破区域的1分钟阴线或下跌收盘，再等待后续阳线实体至少0.30倍1分钟ATR、突破前高、收复MA5/MA10并收在本K线上方35%区域。",
            "策略03共享的正式放量突破分支同步升级为v9；原有三周期、极值、MA20回抽和趋势翻转规则均保留。",
            "策略规则窗口改为策略、版本、规则分类、详细规则四列表；原有与新增规则逐条分行展示。",
        ),
    },
    {
        "version": "0.6.26",
        "date": "2026-08-12",
        "changes": (
            "根据策略01实际成交复盘，修复突破回踩多单被最近8根5分钟旧结构低点拖宽止损的问题，改用1分钟回踩低点/突破实体位外的专属止损。",
            "突破回踩多单的移动止盈由1.5R提前到max(1R, 0.20%)启动，避免已有明显浮盈但因启动线过远而完全没有保护。",
            "移动回撤改为max(入场价0.05%, 0.5倍1分钟ATR)，在快速趋势与正常一分钟震荡之间动态平衡；策略01其他分支保持原规则。",
        ),
    },
    {
        "version": "0.6.25",
        "date": "2026-08-12",
        "changes": (
            "策略01升级v10、策略03升级v8：放量突破基准由前8根5分钟K线最高影线改为前8根最高实体顶部。",
            "当前已收盘5分钟K线以最高价（上影线极值）穿过该实体顶部即视为进入候选，不再要求收盘价突破此前最高影线。",
            "继续保留阳线实体至少0.60 ATR、成交量至少1.50倍均量、1分钟回踩转强、距离MA20不超过2 ATR和结构止损过滤。",
        ),
    },
    {
        "version": "0.6.24",
        "date": "2026-08-12",
        "changes": (
            "策略01升级v9、策略03升级v7：共同新增15分钟多头过滤、5分钟放量突破及1分钟回踩转强做多分支。",
            "突破必须以已收盘5分钟实体站上前8根结构高点且成交量至少为前20根均量1.5倍；确认后最多等待6分钟回踩。",
            "价格距离5分钟MA20超过2 ATR时拒绝追涨；止损继续位于回踩/5分钟结构之外，移动止盈至少达到既有风险收益门槛才启动。",
            "修复策略01解锁后同轮重复下载三周期行情；GET握手/读取超时自动短间隔重试3次，POST订单保持单次提交防止重复下单。",
            "自动交易解锁时观察间隔由10秒缩短为5秒，锁定观察由60秒缩短为30秒；单个策略网络失败不阻断其他策略。",
        ),
    },
    {
        "version": "0.6.23",
        "date": "2026-08-12",
        "changes": (
            "主交易界面新增“策略规则”入口，以表格集中展示策略01、02、03当前触发下单、止盈、止损、限制和策略版本。",
            "策略规则与应用版本集中维护；以后修改任何下单或保护规则时，必须同步更新该表和对应策略档案。",
            "三账户交易明细由拥挤的纯文本重做为原生表格，当前持仓与成交历史分区显示，并单列突出盈亏和手续费。",
            "交易明细支持横向与纵向滚动、固定表头、清晰列宽和刷新状态，便于对照OKX成交记录复盘。",
        ),
    },
    {
        "version": "0.6.22",
        "date": "2026-08-12",
        "changes": (
            "策略02升级为range_pivot_reversal_v4：止损放在最近8根已收盘5分钟K线结构最高/最低影线之外，并保留ATR缓冲。",
            "新增窄幅横盘硬过滤：最近5–10根缺少显著长实体、长影线或足够结构跨度时禁止开仓。",
            "策略02只在5分钟结构出现暴拉、暴跌或明显长影线波动后寻找高低位，1分钟仍只负责精确入场确认。",
            "策略01继续使用v8动态结构止损和按止损距离缩量，两套策略参数与执行模块彼此隔离。",
        ),
    },
    {
        "version": "0.6.21",
        "date": "2026-08-12",
        "changes": (
            "策略01全部入场统一采用5分钟结构动态止损：空单放在最近8根已收盘K线上影线最高价上方，多单执行镜像规则。",
            "结构止损缓冲取0.20倍5分钟ATR与入场价0.03%两者中的较大值，减少普通影线扫损。",
            "仓位按固定风险预算随止损距离自动缩减；当前1张测试模式无法拆分为小数合约，超过2.5倍ATR则放弃交易。",
            "动态止损距离与实际合约数写入策略01订单审计记录，策略02和策略03的保护参数不受影响。",
        ),
    },
    {
        "version": "0.6.20",
        "date": "2026-08-12",
        "changes": (
            "策略02升级为range_pivot_reversal_v3：固定使用已收盘5分钟K线确定高位区和低位区，1分钟K线只负责精确入场确认。",
            "5分钟高位取回看窗口内最长上影线对应的最高成交价，低位取最长下影线对应的最低成交价；当前5分钟K线不参与自身基准计算。",
            "相同长度的影线优先采用最近一根已收盘5分钟K线，减少过旧极值对当前入场的干扰。",
            "提交订单前继续读取OKX最新成交价复核其仍处于5分钟高位区或低位区；标记价不参与高低位和入场区计算，仅用于服务器端止损触发。",
            "策略01、策略02、策略03继续保持独立信号版本、订单前缀、保护委托和审计记录，避免跨策略修改相互覆盖。",
        ),
    },
    {
        "version": "0.6.19",
        "date": "2026-08-11",
        "changes": (
            "策略01新增下跌趋势MA20回抽做空：5分钟确认MA20下降，1分钟触碰MA20后转弱，按最新价开空。",
            "策略03升级为ma20_trend_retest_v6：同时识别趋势刚确认后的首次MA20回抽，以及同一趋势延续过程中的再次MA20回抽。",
            "两类做空点均要求价格回抽至下降MA20附近并重新收在MA20下方；禁止在已经远离均线的低位追空。",
            "策略01与策略03分别保存信号版本、入场K线和保护参数；同一时刻每个策略只保留一单，同一入场K线禁止重复下单。",
            "前一单完全平仓且保护委托清理后，若原趋势仍有效并形成新的MA20回抽拒绝，可继续开第二单、第三单。",
            "上涨趋势采用完全镜像规则：5分钟MA20向上，1分钟回踩MA20后转强做多，结构止损置于回踩低点或MA20下方。",
            "同一趋势段最多完成3次顺势交易；第4次信号因趋势末端风险被拦截，确认新趋势段后重新计数。",
            "空单结构止损放在回抽上影线高点或MA20上方较高者并留ATR缓冲；标记价仅用于服务端止损触发，入场区使用最新价。",
        ),
    },
    {
        "version": "0.6.18",
        "date": "2026-08-11",
        "changes": (
            "策略03升级为ma20_trend_retest_v5：两三根5分钟K线短暂穿越MA20只视为反弹或假突破，不立即改变原趋势。",
            "趋势翻转必须同时满足连续4根已收盘5分钟K线位于MA20新方向、MA20斜率同向，并突破前8根K线结构高点或低点。",
            "未确认上涨时继续沿用已确认的下跌趋势，在20根有效期内等待回踩通道上沿35%并由1分钟转弱确认做空；上涨规则镜像。",
            "策略03下单前改用OKX最新成交价复核35%入场区；标记价仅用于服务器端止损触发。",
        ),
    },
    {
        "version": "0.6.17",
        "date": "2026-08-11",
        "changes": (
            "策略02升级为range_pivot_reversal_v2，三套策略继续使用独立执行模块、订单前缀和保护参数。",
            "策略02成交前改用OKX最新成交价复核区间位置；做多只允许下沿25%，做空只允许上沿25%，禁止信号后价格移到中部仍追单。",
            "高位做空止损放在区间上沿与拒绝K线上影线最高点二者更高者上方并增加ATR缓冲；做多规则镜像。",
            "移动止盈启动距离改为实际入场至结构止损风险的至少1.5倍，修复止盈启动距离小于止损距离的问题。",
            "策略02每次10秒观察轮询优先清理持仓归零后残留的QBRG移动止盈，只处理策略02自有委托。",
        ),
    },
    {
        "version": "0.6.16",
        "date": "2026-08-11",
        "changes": (
            "策略03新增错过首次有效回踩后的趋势通道追踪入场，确认回踩后继续观察最多20根已收盘5分钟K线。",
            "下跌趋势只在动态价格通道上沿35%区域等待1分钟冲高转弱后做空，禁止在通道下沿追空；上涨趋势完全镜像。",
            "追踪通道随窗口内新高和新低更新，第21根K线自动放弃本段趋势，等待下一次有效趋势翻转。",
            "继续保持每趋势段最多一单、结构止损、1R移动止盈、独立Demo API和人工解锁。",
        ),
    },
    {
        "version": "0.6.15",
        "date": "2026-08-11",
        "changes": (
            "策略03回撤择价区由顶部/底部25%扩大到35%，提高有效回撤订单的捕捉率。",
            "做空允许区间位置65%–100%的一分钟冲高转弱信号；做多镜像允许0%–35%的探底转强信号。",
            "继续保留5分钟趋势翻转与MA20回踩前置条件、结构止损、1R移动止盈和每趋势段最多一单。",
        ),
    },
    {
        "version": "0.6.14",
        "date": "2026-08-11",
        "changes": (
            "根据策略03真实亏损单复盘升级为ma20_trend_retest_v3：回踩确认后不再立即市价入场。",
            "做空等待价格回到5分钟回撤区上方25%优选区再开仓；做多镜像等待回踩区下方25%优选区。",
            "一分钟K线只在5分钟回踩成立后负责精确择价：上沿冲高转弱做空、下沿探底转强做多。",
            "取消策略03固定0.15%窄止损，改为放在MA20回踩结构高点上方/低点下方，并增加结构缓冲。",
            "移动止盈改为浮盈至少达到1R后启动，回撤价差由约0.05%放宽到约0.10%，减少正常波动过早离场。",
            "修复策略03交易归档可能混入同账户后续平仓成交的问题，平仓记录按该笔开仓数量匹配最早成交。",
            "核对本次空单：1889.06开空、1891.16止损，毛亏0.21 USDT；原0.15%止损小于回撤K线振幅，是亏损的重要原因。",
        ),
    },
    {
        "version": "0.6.13",
        "date": "2026-08-11",
        "changes": (
            "更新日志与交易明细窗口改为打开即最大化，并随窗口尺寸自动扩展正文和底部操作按钮。",
            "点击顶部最新版状态时，同样打开最大化更新日志，便于在高分辨率屏幕完整阅读。",
            "交易明细新增当前未平仓区，统一查询策略01、02、03三个独立Demo账户的实际持仓。",
            "未平仓订单固定排列在历史成交之前，显示策略、方向、数量、开仓均价、最新价、浮动盈亏并标注未平仓。",
            "历史成交继续按北京时间由新到旧排列，并保留刷新及官方OKX一分钟K线入口。",
        ),
    },
    {
        "version": "0.6.12",
        "date": "2026-08-11",
        "changes": (
            "策略03升级为ma20_trend_retest_v2：5分钟确认趋势翻转后，必须等待后续已收盘5分钟K线回踩蓝色MA20并收回趋势一侧才入场。",
            "取消1分钟回踩突破作为策略03开仓依据；1分钟行情仅用于界面观察，不再改变开仓动作。",
            "回踩信号仅检查最新已收盘5分钟K线，禁止重启或解锁后补做已经错过的历史回踩订单。",
            "每段趋势的回踩等待窗口限制为10根5分钟K线，过期后继续观察下一次趋势翻转；多空规则完全镜像。",
            "策略03客户端订单号升级为v2命名空间，继续保持每个趋势段最多1单、独立Demo API和人工解锁。",
        ),
    },
    {
        "version": "0.6.11",
        "date": "2026-08-11",
        "changes": (
            "Windows主界面升级为流式自适应布局，窗口最大化、还原或调整大小时，全部主控件按客户区同步重新定位和缩放。",
            "状态区、API输入区、策略状态和操作按钮不再固定挤在屏幕左上角；高分辨率屏幕会利用可用宽度和高度。",
            "新增Per-Monitor DPI感知和微软雅黑UI字体缩放，在Windows显示缩放及高DPI屏幕上提高可读性。",
            "现有移动PWA继续使用760px与430px响应式断点、单列控制区和安全区适配；后续新增策略页面必须沿用响应式规范。",
            "保留v0.6.10三账户交易明细、下单提醒、三行观察状态和全部独立API配置。",
        ),
    },
    {
        "version": "0.6.10",
        "date": "2026-08-11",
        "changes": (
            "主界面新增交易明细入口，统一读取策略01、策略02、策略03三个独立OKX Demo账户最近成交记录。",
            "交易明细按北京时间倒序显示策略、买卖方向、持仓方向、数量、成交价、盈亏、手续费和OKX订单号。",
            "每笔Demo开仓成功后新增桌面提醒，明确显示策略名称、下单时间和订单号。",
            "下单提醒和交易明细窗口新增官方OKX ETH-USDT永续K线入口；程序不跳转非官方交易所域名。",
            "继续保留三行独立策略状态、独立API、人工解锁和网络异常安全拒绝机制。",
        ),
    },
    {
        "version": "0.6.09",
        "date": "2026-08-11",
        "changes": (
            "主界面观察栏目固定显示策略01、策略02、策略03三行状态；单个策略网络异常不会遮盖另外两套策略的状态。",
            "策略03新增独立OKX Demo自动下单：5分钟确认MA20趋势翻转，1分钟回踩后突破确认入场，每段趋势最多1单。",
            "策略03自动测试必须人工单独解锁；解锁时校验独立Demo账户为空仓、无委托、双向持仓模式，并设置最高10倍杠杆。",
            "策略03每次只开1张合约，附带标记价0.15%固定止损，以及盈利0.20%启动、回撤0.05%的服务器端移动止盈。",
            "移除主界面不再使用的运行模拟研究、打开报告、工作目录按钮和旧回测指标行，使实时交易观察信息更清晰。",
            "API加密文件与原有配置保持不变，升级不会要求重新填写三个策略的API。",
        ),
    },
    {
        "version": "0.6.08",
        "date": "2026-08-11",
        "changes": (
            "修复策略02频率测试可能在价格已经反弹到区间中高位后继续追多的问题。",
            "新增不可绕过的成交位置过滤：做多只允许在最近区间下部25%，做空只允许在上部25%。",
            "价格触及低点后若已经反弹离开低位区域，本轮信号作废并等待下一次回落；高位做空规则镜像处理。",
            "保留1分钟频率测试、ATR止损、单笔1张、防重复和明确强趋势反向禁入。",
        ),
    },
    {
        "version": "0.6.07",
        "date": "2026-08-11",
        "changes": (
            "新增策略03 MA20趋势翻转回抽模块：默认用5分钟已收盘K线确认趋势翻转，用1分钟K线寻找回踩。",
            "上涨翻转时记录1分钟回踩最低点作为保护基准，并等待最新价突破回踩K线高点后确认做多；做空规则镜像实现。",
            "新增策略03独立OKX Demo API绑定、验证、加密保存和删除界面，不覆盖策略01或策略02凭据。",
            "策略03第一版默认只观察、不自动下单，待观察信号验证后再增加独立人工解锁。",
        ),
    },
    {
        "version": "0.6.06",
        "date": "2026-08-11",
        "changes": (
            "策略02新增1分钟频率测试模式，用MA5/MA10当前方向确认替代必须在同一根K线恰好交叉的稀疏条件。",
            "频率测试期间放宽ADX和收益成本过滤，但仍保留15分钟明确强趋势反向禁入、ATR止损、每次1张和防重复。",
            "策略02测试上限调整为每日200次，冷却时间缩短为60秒，仅用于独立OKX Demo账户采集样本。",
            "升级时自动迁移策略02频率测试配置并备份旧配置，不修改任何加密保存的API凭据。",
        ),
    },
    {
        "version": "0.6.05",
        "date": "2026-08-11",
        "changes": (
            "修复策略01止损平仓后独立移动止盈委托可能残留的问题：仅清理本策略创建、且已无对应持仓的移动止盈单。",
            "根据夜间实测亏损记录，取消1分钟通道0%-5%无条件逆势做多及95%-100%无条件逆势做空。",
            "极值区域入场现在必须满足15分钟与5分钟趋势同向，并由1分钟反转信号确认，避免强下跌中连续接飞刀。",
            "按实盘观察将1分钟极值触发区收紧为下沿0%-3%、上沿97%-100%，减少普通波动中的误触发。",
            "新增逐笔交易生命周期台账，记录信号原因、通道位置、多周期方向、入场订单、止损、移动止盈和最终退出结果。",
            "平仓后自动汇总成交价、毛盈亏、手续费、净盈亏及止损或移动止盈退出原因，为后续策略复盘提供数据。",
        ),
    },
    {
        "version": "0.6.04",
        "date": "2026-08-10",
        "changes": (
            "策略02新增独立的自动下单测试解锁按钮，只使用策略02加密保存的OKX Demo API。",
            "解锁前强制验证单币种保证金、双向持仓、账户空仓、普通委托和策略委托全部为空，并设置最高10倍。",
            "策略02合格区间反转信号可自动提交1张Demo订单，附带ATR标记价固定止损和1R启动的服务器端移动止盈。",
            "新增策略02每日24次上限、5分钟冷却、同一确认K线防重复、行情/账户查询失败安全拒绝及SQLite审计记录。",
            "有持仓或保护单时只进入管理状态，不重复开仓；发现未保护持仓时报警并禁止新开仓。",
            "策略01与策略02可使用两个独立Demo账户并分别解锁；关闭或重启客户端后两套自动交易都会恢复锁定。",
            "首轮每次仅1张，因此暂不执行1R平50%；待整单开平仓验证稳定后再用至少2张测试分批止盈。",
        ),
    },
    {
        "version": "0.6.03",
        "date": "2026-08-10",
        "changes": (
            "修复持仓或保护单存在时状态栏不再显示1分钟通道百分比的问题。",
            "无论空仓、持仓、普通委托或OKX服务器保护单管理中，状态栏都会持续刷新通道位置。",
            "通道百分比旁固定显示15分钟、5分钟和1分钟方向箭头，便于观察极值交易是否逆势。",
            "每笔新订单审计日志新增触发通道百分比、三个周期方向、确认K线时间、收盘价和完整触发原因。",
            "经审计确认20:18多单由20:17已收盘K线的1分钟通道约3.10%触发，属于0%–5%绝对下沿做多。",
        ),
    },
    {
        "version": "0.6.02",
        "date": "2026-08-10",
        "changes": (
            "策略01 Demo验证新增对称的绝对极值反转：1分钟通道进入0%–5%时直接触发1张做多测试单。",
            "1分钟通道进入95%–100%时直接触发1张做空测试单，与下沿做多构成镜像规则。",
            "绝对极值测试不再要求15分钟和5分钟趋势同向，因此可观察下跌趋势末端反弹和上涨趋势末端回落。",
            "状态栏继续显示15m、5m、1m方向，便于区分顺势极值交易与逆势极值交易。",
            "本功能仅限OKX Demo小仓验证；单边突破可能连续触发止损，不能把通道极值理解为必然反转。",
            "每次1张、账户空仓检查、5分钟冷却、行情过期保护、标记价止损和服务器移动止盈保持不变。",
        ),
    },
    {
        "version": "0.6.1",
        "date": "2026-08-10",
        "changes": (
            "策略01新增1分钟通道极值回调快速入场：15分钟与5分钟同为下跌时，通道达到95%–100%允许触发Demo做空。",
            "上涨趋势执行镜像规则：15分钟与5分钟同为上涨时，通道达到0%–5%允许触发Demo做多。",
            "极值快速入场不再要求1分钟均线已经完成同向交叉，避免反弹到上沿后回落才确认而错过更合理价格。",
            "15分钟与5分钟方向冲突时仍禁止快速入场；账户非空、冷却、行情过期和网络异常保护保持不变。",
            "每次仍只下1张Demo合约，并继续附带标记价固定止损和OKX服务器端移动止盈。",
        ),
    },
    {
        "version": "0.6.0",
        "date": "2026-08-10",
        "changes": (
            "新增策略02“5–10根K线区间高低点反转策略”，策略ID为strategy_02，首个策略版本为range_pivot_reversal_v1。",
            "区间高低点严格使用shift(1)排除当前K线，所有信号只使用OKX已确认收盘K线，避免未来函数。",
            "免费测试默认使用1分钟区间周期；支持空仓校验后切换1m、5m、15m、30m或1H。",
            "新增ATR触碰区域与止损、ADX和15分钟强趋势过滤、1分钟MA5/MA10交叉确认、盈亏比及20%返佣后成本过滤。",
            "新增先平反向仓位、确认归零后再反向开仓的可恢复状态机与SQLite防重复意图；本版本策略02仍只观察，不自动下单。",
            "桌面端明确显示策略01/策略02的名称和策略版本，并新增策略02独立OKX Demo API加密绑定窗口。",
            "策略02的API凭据使用独立Windows加密文件，不覆盖策略01凭据；绑定API不会自动解锁或启动交易。",
            "策略01状态栏新增15m/5m/1m方向箭头，并把Demo过滤原因写入SQLite，便于诊断长时间未下单的原因。",
            "不提供无限加仓或保证解套功能；任何后续仓位管理必须受到最大仓位、最大加仓次数和总止损约束。",
        ),
    },
    {
        "version": "0.5.14",
        "date": "2026-08-10",
        "changes": (
            "优化Demo自动开仓为分层三周期逻辑：15分钟决定主趋势，5分钟确认趋势，1分钟负责回调后的精确入场。",
            "修复此前Demo执行只比较1分钟与5分钟、15分钟未参与实际下单过滤的问题。",
            "上涨趋势中允许1分钟暂时向下回调，但必须在通道下部重新转强并完成反转确认后才开多；下跌趋势执行镜像规则。",
            "状态栏新增明确等待原因，可区分高周期方向冲突、1分钟仍在回调以及回调反转尚未确认。",
            "继续保留1分钟20根价格通道、每日200次测试上限、每次1张、最高10倍、5分钟冷却及服务器端止盈止损。",
        ),
    },
    {
        "version": "0.5.13",
        "date": "2026-08-10",
        "changes": (
            "免费模拟账户验证阶段将价格通道从5分钟切换为1分钟，状态栏显示1分钟通道位置百分比。",
            "Demo验证模式每日交易上限由24次提高到200次，用于更快积累自动开平仓测试数据。",
            "正式双均线策略仍保留每日24次限制，200次上限不会作用于未来实盘配置。",
            "新增可配置通道周期，预留1分钟、5分钟、15分钟、30分钟和1小时切换能力。",
            "每次仍限制1张、最高10倍、5分钟冷却，并保留通道位置过滤和1分钟回落反转确认。",
        ),
    },
    {
        "version": "0.5.12",
        "date": "2026-08-10",
        "changes": (
            "修复状态栏高度不足导致验证提示第二行被遮挡的问题，显示区域由28像素增加到50像素。",
            "状态栏改为两行：第一行固定显示验证执行状态，第二行显示主策略观察状态和模拟交易锁定状态。",
            "无论正在等待方向一致、回落确认还是通道过滤，均优先显示当前5分钟通道位置百分比。",
            "通道提示改为中文，明确显示中上部禁止追多、中下部禁止追空。",
        ),
    },
    {
        "version": "0.5.11",
        "date": "2026-08-10",
        "changes": (
            "新增5分钟20根K线高低点趋势通道位置过滤器，避免上涨通道上沿追多和下跌通道下沿追空。",
            "上涨趋势仅在通道位置不高于45%时考虑做多；下跌趋势仅在通道位置不低于55%时考虑做空。",
            "新增1分钟回落反转确认：多单需回落后重新站上快均线，空单需反弹后重新跌破快均线。",
            "移动止盈平仓后的下一笔订单同样必须重新满足通道位置和回落确认，不会立即追单。",
            "状态栏会显示被通道中上部或中下部过滤的具体位置百分比，便于模拟盘观察。",
        ),
    },
    {
        "version": "0.5.10",
        "date": "2026-08-10",
        "changes": (
            "根据OKX错误码51311修复移动止盈提交失败：交易所不接受低于0.10%的callbackRatio。",
            "为保留约0.05%的快速回撤测试，改用服务器端callbackSpread固定价格距离。",
            "回撤距离按开仓参考价的0.05%换算并按ETH最小价格精度向上取整。",
            "移动止盈成功提交后保存独立策略单ID，可在OKX策略委托中核验；K线绘制方式由OKX网页端决定。",
        ),
    },
    {
        "version": "0.5.9",
        "date": "2026-08-10",
        "changes": (
            "修复OKX仅生成固定止损、没有真正生成移动止盈委托的问题。",
            "开仓继续原子附带0.15%标记价固定止损，确保第一时间具备风险保护。",
            "开仓成交后通过OKX专用move_order_stop接口独立提交只减仓移动止盈。",
            "移动止盈保持盈利0.20%启动、按最新价追踪、反向回撤0.05%市价平仓。",
            "审计日志分别保存普通开仓订单ID和移动止盈策略单ID，便于逐笔核验。",
        ),
    },
    {
        "version": "0.5.8",
        "date": "2026-08-10",
        "changes": (
            "修复v0.5.6/v0.5.7移动止盈与固定止损拆分提交造成自动下单失败的问题。",
            "移动止盈和标记价固定止损现在作为同一个OKX服务器端附属保护组合提交。",
            "新增下单失败审计事件，完整保存OKX错误信息，便于在不操作账户的情况下快速诊断。",
            "保留盈利0.20%启动、回撤0.05%止盈和标记价0.15%止损参数。",
        ),
    },
    {
        "version": "0.5.7",
        "date": "2026-08-10",
        "changes": (
            "为便于1分钟、5分钟和15分钟模拟盘快速验证，将移动止盈回撤比例从0.10%收紧到0.05%。",
            "盈利达到0.20%后启动追踪；例如最高盈利达到0.25%，回落约0.05%时将在原0.20%止盈线附近触发平仓。",
            "移动止盈按最新成交价追踪，0.15%固定止损继续按标记价触发。",
            "实际平仓收益可能因手续费、市价滑点和比例计算基准产生小幅差异。",
        ),
    },
    {
        "version": "0.5.6",
        "date": "2026-08-10",
        "changes": (
            "验证模式将固定止盈升级为OKX服务器端移动止盈。",
            "价格盈利达到0.20%后启动追踪；多单跟随最高成交价、空单跟随最低成交价。",
            "启动后价格反向回撤0.10%时，以市价止盈平仓，使强趋势行情有机会延续利润。",
            "原0.15%标记价固定止损继续保留，降低瞬时最新价插针导致误止损的概率。",
            "移动止盈和固定止损均保存于OKX服务器；本机断网后，已成功生效的保护委托仍由交易所执行。",
        ),
    },
    {
        "version": "0.5.5",
        "date": "2026-08-09",
        "changes": (
            "更新日志改为固定尺寸窗口，避免内容过长占满整个屏幕。",
            "日志正文新增右侧垂直滚动条，可上下滚动阅读全部历史版本。",
            "确定按钮固定显示在窗口底部偏上位置，在常用屏幕分辨率下均可直接点击。",
        ),
    },
    {
        "version": "0.5.4",
        "date": "2026-08-09",
        "changes": (
            "修复图表最新价已穿过止盈线、但标记价尚未触发而造成的止盈观感不一致：验证模式止盈改用最新成交价触发。",
            "止损继续使用标记价触发，降低瞬时成交价尖刺导致意外止损的概率。",
            "新增交易所保护单核验：检测到持仓但没有生效的止盈止损时，立即显示严重警报并禁止继续开新仓。",
            "明确止盈止损由OKX服务器执行，本地观察轮询速度不再被误认为止盈触发速度。",
        ),
    },
    {
        "version": "0.5.3",
        "date": "2026-08-09",
        "changes": (
            "新增独立的Demo频繁交易验证模式，用于测试自动开仓、交易所止盈止损、自动恢复观察和下一轮交易闭环。",
            "人工点击解锁后，1分钟与5分钟MA5/MA10方向一致、账户空仓且冷却结束时，自动提交1张Demo验证订单。",
            "验证订单固定附带0.20%止盈和0.15%止损，止盈止损由OKX服务器保存。",
            "验证模式强制每次1张、最高10×、最短5分钟冷却、每天最多24次；正式双均线策略参数不被覆盖。",
            "有持仓、普通委托或策略委托时只进入管理等待，不重复开仓；同一根1分钟K线使用唯一客户端订单号。",
            "网络断开时禁止新开仓，自动重连后先查询账户状态，再决定是否继续。",
            "关闭或重启客户端后自动恢复锁定，必须再次人工确认才能继续Demo自动验证交易。",
        ),
    },
    {
        "version": "0.5.2",
        "date": "2026-08-09",
        "changes": (
            "修复网络或SSL握手超时导致持续观察退出的问题。",
            "新增2–60秒指数退避自动重连及状态栏重试提示。",
        ),
    },
    {
        "version": "0.5.1",
        "date": "2026-08-09",
        "changes": (
            "桌面端新增“开始观察”“暂停观察”“解锁模拟交易”三个运行控制按钮。",
            "观察线程每60秒刷新三周期公开行情和安全决策；暂停后不再计算新信号。",
            "模拟交易解锁仅对当前进程有效，关闭或重启客户端后自动恢复锁定。",
            "解锁前强制验证 Demo API、单币种保证金模式、双向持仓模式和账户空仓状态。",
            "默认手续费返佣设为20%，并支持通过配置为不同用户设置实际返佣比例。",
            "成本门槛按20%返佣后的净手续费、双边滑点重新计算，返佣不抵扣滑点。",
            "修正v0.5.0仅有后台观察命令、桌面端缺少运行按钮的问题。",
        ),
    },
    {
        "version": "0.5.0",
        "date": "2026-08-09",
        "changes": (
            "新增 1分钟、5分钟、15分钟三周期双均线策略：15分钟判断方向、5分钟确认趋势、1分钟等待回踩入场。",
            "新增三周期冲突过滤；方向不一致、入场未确认或行情过期时保持空仓。",
            "新增 ATR 动态止损、按账户风险预算计算合约数，并将新策略默认杠杆调整为 10×。",
            "新增每日亏损熔断、交易冷却时间与成本覆盖门槛；取消固定每日 3 次限制，改用 24 次安全上限。",
            "新增可配置手续费返佣模型，分别记录毛手续费、返佣、净手续费和滑点。",
            "新增 SQLite 状态与审计日志，支持重启恢复、行情过期事件和同一根 K 线防重复订单意图。",
            "强化 OKX Demo 账户等级、双向持仓、全仓杠杆、持仓和挂单检查，并显示交易所具体错误码。",
            "新增右上角版本状态入口、详细更新日志、HTTPS更新清单及SHA-256下载校验框架。",
            "API凭据继续由 Windows DPAPI 加密保存在 QuantBotWorkspace，升级程序不会覆盖凭据文件。",
            "自动模拟下单仍默认锁定；当前版本先完成安全观察与升级基础设施。",
        ),
    },
    {
        "version": "0.4.0",
        "date": "2026-08-04",
        "changes": (
            "新增 Windows 用户级加密保存 Demo API，软件重启后可自动载入。",
            "新增安全更换和删除本机 API 功能；有持仓或未完成委托时禁止操作。",
            "新增 1–10 个 USDT 永续合约白名单及安全切换功能。",
            "切换交易品种前验证全部永续持仓、普通委托和策略委托均已结束。",
            "新增交易品种在线有效性验证，并支持为全部选定合约设置全仓杠杆。",
            "明确本机删除不会撤销欧易网页中的 API Key。",
        ),
    },
    {
        "version": "0.3.0",
        "date": "2026-08-04",
        "changes": (
            "新增可贴牌品牌配置，默认显示制作者谭明泽及微信 tmz8873。",
            "左上角品牌信息替换原 Codex QuantBot 标识。",
            "新增“修改品牌”入口，可用记事本更换姓名和微信号。",
            "品牌配置独立保存，升级软件时无需修改程序代码。",
            "品牌配置异常时自动使用制作者默认信息，避免界面无法启动。",
        ),
    },
    {
        "version": "0.2.0",
        "date": "2026-08-04",
        "changes": (
            "新增客户端版本号显示。",
            "新增内置更新日志，可在客户端中直接查看。",
            "新增 Windows EXE 文件版本信息，便于分发和核对版本。",
            "确认 OKX Demo API 使用 ETH-USDT-SWAP、全仓模式和模拟盘标头。",
            "保留 API 凭据仅存当前进程内存、关闭即清除的安全机制。",
        ),
    },
    {
        "version": "0.1.0",
        "date": "2026-08-03",
        "changes": (
            "首个 Windows 桌面客户端版本。",
            "提供数据、因子、策略、回测、风控和 HTML 报告工作流。",
            "接入 OKX Demo API 验证及 ETH-USDT 永续全仓杠杆设置。",
            "默认禁止真实资金交易。",
        ),
    },
)


def changelog_text() -> str:
    sections: list[str] = []
    for release in CHANGELOG:
        lines = [f"v{release['version']}  ({release['date']})"]
        lines.extend(f"• {item}" for item in release["changes"])
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def changelog_pages(lines_per_page: int = 50) -> tuple[str, ...]:
    """Return complete release history split into bounded, newest-first pages."""
    if lines_per_page <= 0:
        raise ValueError("lines_per_page must be positive")
    lines = changelog_text().splitlines()
    return tuple(
        "\n".join(lines[index:index + lines_per_page])
        for index in range(0, len(lines), lines_per_page)
    ) or ("",)



























