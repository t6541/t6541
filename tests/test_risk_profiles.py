import pytest

from quantbot.config import load_config
from quantbot.risk_profiles import profile_allows_entry, profile_display, normalize_profile


def test_profiles_are_nested_by_entry_family():
    assert profile_allows_entry("aggressive", "early_low_sweep_reclaim")
    assert not profile_allows_entry("conservative", "early_low_sweep_reclaim")
    assert not profile_allows_entry("prudent", "candidate_reversal_entry")
    assert profile_allows_entry("prudent", "bullish_breakout_pullback")
    assert profile_allows_entry("conservative", "downtrend_continuation_short")


def test_profile_validation_and_labels():
    assert normalize_profile(" prudent ") == "prudent"
    assert profile_display("aggressive") == "激进型"
    with pytest.raises(ValueError):
        normalize_profile("unknown")


def test_config_reads_risk_profile():
    cfg = load_config("configs/eth-trend.toml")
    assert cfg.strategy.risk_profile == "aggressive"
