from quantbot.ui_localization import localize_main_status


def test_main_status_translates_strategy_actions_and_states():
    value = localize_main_status(
        "策略01：observe｜5m downtrend confirmed; 1m pullback rejected at falling MA20｜ATR 0.5｜ADX 20"
    )
    assert "观察" in value
    assert "5分钟下跌趋势确认" in value
    assert "1分钟反抽下降中的MA20后再次转弱" in value
    assert "平均真实波幅（ATR）" in value
    assert "趋势强度（ADX）" in value
    assert "observe" not in value


def test_main_status_translates_internal_state_names():
    value = localize_main_status("策略03：observe｜5m状态 bearish_flip")
    assert value == "策略03：观察｜5分钟状态 看跌翻转"
