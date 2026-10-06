from quantbot.exit_policy import normalize_fixed_protection, shared_exit_policy


def test_all_entries_use_closed_five_minute_ma5_turn_profit_exit():
    for kind in ("extreme_reversal", "early_low_sweep_reclaim", "ma20_pullback", "breakout_pullback"):
        policy = shared_exit_policy(kind, higher_timeframe_trend_confirmed=False)
        assert policy.mode == "ma5_turn"
        assert policy.take_profit_trigger_type == "local_closed_5m_ma5_turn"


def test_confirmed_trend_continuation_does_not_use_trailing_exit():
    assert not shared_exit_policy(
        "trend_continuation", higher_timeframe_trend_confirmed=True
    ).uses_trailing


def test_endpoint_half_cover_probe_uses_one_minute_ma5_until_durable_confirmation():
    kinds = ("one_minute_ma_fan_endpoint_five_minute_half_cover_short",
             "one_minute_ma_fan_endpoint_five_minute_half_cover_long")
    for kind in kinds:
        policy = shared_exit_policy(kind, higher_timeframe_trend_confirmed=False)
        assert policy.mode == "ma5_turn"
        assert policy.take_profit_trigger_type == "local_closed_1m_ma5_turn"


def test_five_minute_local_reversal_trial_uses_five_minute_ma5_immediately():
    for kind in ("five_minute_top_local_reversal_half_cover_short",
                 "five_minute_bottom_local_reversal_half_cover_long"):
        policy = shared_exit_policy(kind, higher_timeframe_trend_confirmed=False)
        assert policy.mode == "ma5_turn"
        assert policy.take_profit_trigger_type == "local_closed_5m_ma5_turn"


def test_waterfall_mode_uses_server_trailing_for_confirmed_continuation_only():
    waterfall = shared_exit_policy(
        "trend_continuation", higher_timeframe_trend_confirmed=True,
        waterfall_confirmed=True,
    )
    assert waterfall.uses_trailing
    assert waterfall.take_profit_trigger_type == "server_trailing"
    assert not shared_exit_policy(
        "extreme_reversal", higher_timeframe_trend_confirmed=False,
        waterfall_confirmed=True,
    ).uses_trailing


def test_fixed_protection_has_absolute_atr_and_reward_risk_floors():
    plan = normalize_fixed_protection(1880, 1, 1879.9, 1880.2, .8)
    assert plan.risk >= 3.76
    assert plan.reward >= 5.64
    assert plan.reward >= plan.risk * 1.5
    assert plan.stop < 1880 < plan.take_profit
    assert not shared_exit_policy(
        "trend_continuation", higher_timeframe_trend_confirmed=False
    ).uses_trailing
