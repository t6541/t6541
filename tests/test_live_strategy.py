from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import quantbot.live_strategy as live_strategy


def _audit():
    return {
        "execution_permitted": False,
        "open_positions": [],
        "account": {"acctLv": "2", "posMode": "long_short_mode"},
        "leverage": [
            {"posSide": "long", "lever": "100"},
            {"posSide": "short", "lever": "100"},
        ],
        "instrument": {"ctVal": "0.1"},
        "minimum_order": {"api_size_contracts": "0.01"},
    }


def test_live_policy_allows_core_three_timeframes_with_fixed_point_zero_five_contracts(monkeypatch):
    now = datetime.now(timezone.utc)
    signals = {bar: SimpleNamespace(direction=1, candle_time=now) for bar in (
        "1m", "5m", "15m", "30m", "1H", "4H")}
    markets = {bar: pd.DataFrame({"date": [now], "close": [100.0]}) for bar in signals}
    monkeypatch.setattr(live_strategy, "validation_market_context_from_public",
                        lambda: (signals, markets, "1m", .5, "aligned"))
    monkeypatch.setattr(live_strategy, "_continuation_setup",
                        lambda _: (1, "confirmed pullback continuation", 99.0))
    monkeypatch.setattr(live_strategy, "five_minute_primary_entry_gate",
                        lambda *_: SimpleNamespace(allowed=True, reason="5m primary", stop=99.0))
    monkeypatch.setattr(live_strategy, "trend_entry_runway", lambda *_: (True, "ok", 1.0))
    monkeypatch.setattr(live_strategy, "latest_atr", lambda *_: 1.0)
    monkeypatch.setattr(live_strategy, "structure_protection_plan",
                        lambda *_: SimpleNamespace(allowed=True, stop=99.0, reason="ok"))
    monkeypatch.setattr(live_strategy, "tradeable_profit_space",
                        lambda *_args, **_kwargs: (True, "ok", 1.5))
    candidate = live_strategy.scan_conservative_live_candidate(_audit())
    assert candidate.eligible is True
    assert candidate.contracts == "0.05"
    assert candidate.planned_loss_usdt == pytest.approx(0.005)
    assert candidate.branch == "five_minute_primary_trigger"


def test_live_policy_rejects_same_side_position_but_not_opposite_side(monkeypatch):
    audit = _audit()
    audit["open_positions"] = [{"posSide": "long", "pos": "0.01"}]
    now = datetime.now(timezone.utc)
    signals = {bar: SimpleNamespace(direction=1, candle_time=now) for bar in ("1m", "5m", "15m", "30m", "1H", "4H")}
    markets = {bar: pd.DataFrame({"date": [now], "close": [100.0]}) for bar in signals}
    monkeypatch.setattr(live_strategy, "validation_market_context_from_public", lambda: (signals, markets, "", 0.0, ""))
    monkeypatch.setattr(live_strategy, "_continuation_setup", lambda _: (1, "candidate", 99.0))
    monkeypatch.setattr(live_strategy, "five_minute_primary_entry_gate",
                        lambda *_: SimpleNamespace(allowed=True, reason="5m primary", stop=99.0))
    candidate = live_strategy.scan_conservative_live_candidate(audit)
    assert candidate.eligible is False
    assert "同方向已有持仓" in candidate.reason


def test_live_policy_does_not_block_when_only_four_hour_is_opposed(monkeypatch):
    now = datetime.now(timezone.utc)
    signals = {bar: SimpleNamespace(direction=1, candle_time=now) for bar in (
        "1m", "5m", "15m", "30m", "1H", "4H")}
    signals["4H"] = SimpleNamespace(direction=-1, candle_time=now)
    markets = {bar: pd.DataFrame({"date": [now], "close": [100.0]}) for bar in signals}
    monkeypatch.setattr(live_strategy, "validation_market_context_from_public",
                        lambda: (signals, markets, "1m", .5, "mixed"))
    monkeypatch.setattr(live_strategy, "_continuation_setup",
                        lambda _: (1, "candidate", 99.0))
    monkeypatch.setattr(live_strategy, "five_minute_primary_entry_gate",
                        lambda *_: SimpleNamespace(allowed=True, reason="5m primary", stop=99.0))
    candidate = live_strategy.scan_conservative_live_candidate(_audit())
    monkeypatch.setattr(live_strategy, "trend_entry_runway", lambda *_: (True, "ok", 1.0))
    monkeypatch.setattr(live_strategy, "latest_atr", lambda *_: 1.0)
    monkeypatch.setattr(live_strategy, "structure_protection_plan",
                        lambda *_: SimpleNamespace(allowed=True, stop=99.0, reason="ok"))
    monkeypatch.setattr(live_strategy, "tradeable_profit_space",
                        lambda *_args, **_kwargs: (True, "ok", 1.5))
    candidate = live_strategy.scan_conservative_live_candidate(_audit())
    assert candidate.eligible is True
    assert "大周期背景混合" in candidate.reason


def test_live_policy_allows_option_c_when_higher_structure_opposes(monkeypatch):
    now = datetime.now(timezone.utc)
    signals = {bar: SimpleNamespace(direction=1, candle_time=now) for bar in (
        "1m", "5m", "15m", "30m", "1H", "4H")}
    for bar in ("30m", "1H", "4H"):
        signals[bar] = SimpleNamespace(direction=-1, candle_time=now)
    markets = {bar: pd.DataFrame({"date": [now], "close": [100.0]}) for bar in signals}
    monkeypatch.setattr(live_strategy, "validation_market_context_from_public",
                        lambda: (signals, markets, "1m", .5, "mixed"))
    monkeypatch.setattr(live_strategy, "_continuation_setup",
                        lambda _: (1, "candidate", 99.0))
    monkeypatch.setattr(live_strategy, "five_minute_primary_entry_gate",
                        lambda *_: SimpleNamespace(allowed=True, reason="方案C通过：高周期只提示", stop=99.0))
    monkeypatch.setattr(live_strategy, "trend_entry_runway", lambda *_: (True, "ok", 1.0))
    monkeypatch.setattr(live_strategy, "latest_atr", lambda *_: 1.0)
    monkeypatch.setattr(live_strategy, "structure_protection_plan",
                        lambda *_: SimpleNamespace(allowed=True, stop=99.0, reason="ok"))
    monkeypatch.setattr(live_strategy, "tradeable_profit_space",
                        lambda *_args, **_kwargs: (True, "ok", 1.5))
    candidate = live_strategy.scan_conservative_live_candidate(_audit(), profile="prudent")
    assert candidate.eligible is True
    assert "方案C通过" in candidate.reason


def test_live_profiles_use_independent_risk_limits():
    assert live_strategy.LIVE_PROFILES["aggressive"]["max_successful_trades"] == 100
    assert live_strategy.LIVE_PROFILES["conservative"]["max_successful_trades"] == 100
    assert live_strategy.LIVE_PROFILES["prudent"]["max_successful_trades"] == 100
    assert live_strategy.LIVE_PROFILES["conservative"]["daily_loss"] == 5.0
    assert live_strategy.LIVE_PROFILES["prudent"]["daily_loss"] == 5.0
    assert live_strategy.LIVE_PROFILES["aggressive"]["daily_loss"] == 5.0
    assert live_strategy.LIVE_PROFILES["aggressive"]["cooldown_minutes"] == 0
    assert live_strategy.LIVE_PROFILES["conservative"]["cooldown_minutes"] == 0
    assert live_strategy.LIVE_PROFILES["prudent"]["cooldown_minutes"] == 0
    assert [live_strategy.LIVE_PROFILES[key]["label"] for key in
            ("aggressive", "conservative", "prudent")] == ["账户01", "账户02", "账户03"]


def test_account04_remains_observation_only_and_account05_has_own_executor():
    assert live_strategy.LIVE_PROFILES["external_observer"]["execution_enabled"] is False
    candidate = live_strategy.scan_conservative_live_candidate({}, "external_observer")
    assert not candidate.eligible
    assert "自动下单已锁定" in candidate.reason
    assert live_strategy.LIVE_PROFILES["clone_research"]["execution_enabled"] is True
    assert "双向对冲" in live_strategy.LIVE_PROFILES["clone_research"]["purpose"]


def test_aggressive_live_branch_allowlist_covers_requested_entry_classes():
    branches = set(live_strategy.AGGRESSIVE_LIVE_BRANCHES)
    assert "structure_pressure_post_only_short" in branches
    assert "structure_support_post_only_long" in branches
    assert "trend_continuation_market_entry" in branches
    assert "candidate_reversal_early_entry" in branches
    assert "early_high_sweep_reject_short" in branches
    assert "early_low_sweep_reclaim_long" in branches
    assert all("minimum_aligned" not in spec for spec in live_strategy.LIVE_PROFILES.values())
