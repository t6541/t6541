"""Retired base entries cannot be enabled by old settings or internal callers."""
from decimal import Decimal as D

import pytest

from test_extreme_profit_sweep import setup_case, add_lot
from quantbot.account05_execution import _submit_entry
from quantbot.account05_signals import Account05Signals
from quantbot.account05_strategy import PositionSide, Trend15m
from quantbot.live_account_settings import LiveAccountSettings
from quantbot.okx import OkxError


def test_old_enabled_setting_and_setter_cannot_enable_base(tmp_path):
    settings = LiveAccountSettings(tmp_path / "old.sqlite3")
    settings._set("account05_base_rebuild_enabled", "1")
    assert settings.base_rebuild_enabled() is False
    assert settings.set_base_rebuild_enabled(True) is False
    assert settings.base_rebuild_enabled() is False
    assert LiveAccountSettings(settings.path).base_rebuild_enabled() is False


def normal_case(tmp_path, monkeypatch):
    client, ledger, settings, tick = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr("quantbot.account05_execution.evaluate_account05_signals",
        lambda *_a, **_k: Account05Signals(Trend15m.UP, "上涨", ()))
    # Even an older caller overriding the settings accessor cannot bypass policy.
    monkeypatch.setattr(settings, "base_rebuild_enabled", lambda: True)
    return client, ledger, settings, tick


def test_empty_account_does_not_build_bases_despite_enabled_legacy_accessor(tmp_path, monkeypatch):
    client, ledger, _, tick = normal_case(tmp_path, monkeypatch)
    tick()
    assert not client.entries
    assert not client.take_profits
    assert not ledger.open_lots()


def test_base_entry_helper_blocks_before_claim_or_post(tmp_path, monkeypatch):
    client, ledger, _, _ = normal_case(tmp_path, monkeypatch)
    with pytest.raises(OkxError, match="基础仓功能已停用"):
        _submit_entry(client, ledger, signal_id="base-bypass", side=PositionSide.LONG,
                      kind="base", size=D("0.36"), spec=None, config=None)
    assert ledger.order_intent("base-bypass") is None
    assert not client.entries


def test_old_base_full_take_profit_does_not_rebuild(tmp_path, monkeypatch):
    client, ledger, _, tick = normal_case(tmp_path, monkeypatch)
    base = add_lot(client, ledger, "old", "2700", side=PositionSide.LONG, size="0.36", kind="base")
    ledger.attach_take_profit(base.lot_id, "old-tp")
    client.orders["old-tp"] = dict(ordId="old-tp", state="filled", accFillSz="0.36", avgPx="2715")
    client._position("long", -D("0.36"))
    tick()
    assert ledger.get_lot(base.lot_id).status == "closed"
    assert not client.entries
    assert not client.take_profits


def test_old_stage_two_plan_cannot_open_more_base(tmp_path, monkeypatch):
    client, ledger, _, tick = normal_case(tmp_path, monkeypatch)
    base = add_lot(client, ledger, "old", "2700", side=PositionSide.LONG, size="0.36", kind="base")
    ledger.attach_take_profit(base.lot_id, "old-tp")
    client.orders["old-tp"] = dict(ordId="old-tp", state="live", accFillSz="0")
    ledger.plan_base_rebuild(side=PositionSide.LONG, signal_key="old-stage",
        target_margin_pct=D("0.012"), first_margin_pct=D("0.006"), take_profit_pct=D("0.005"))
    ledger.set_base_rebuild_status(PositionSide.LONG, "stage1_filled")
    monkeypatch.setattr("quantbot.account05_execution.base_pullback_confirmation", lambda *_a, **_k: "fresh")
    tick()
    assert not client.entries
    assert ("cancel", "old-tp") not in client.events
    assert ledger.get_lot(base.lot_id).remaining_size == D("0.36")


def test_new_addon_extreme_entry_remains_enabled(tmp_path, monkeypatch):
    client, ledger, _, tick = setup_case(tmp_path, monkeypatch)
    tick()
    assert len(client.entries) == 1
    assert all(l.kind == "addon" for l in ledger.open_lots())
