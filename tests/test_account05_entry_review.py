import json
import numpy as np
import pandas as pd

from quantbot.account05_entry_review import entry_context, review_summary
from quantbot.account05_signals import Account05Signals, Account05Trigger, _extreme_rotation
from quantbot.account05_state import Account05StateStore
from quantbot.account05_strategy import Trend15m


def market():
    close = np.linspace(100, 120, 40)
    return pd.DataFrame(dict(date=pd.date_range("2026-01-01", periods=40, freq="min"),
                             open=close-.2, close=close, high=close+1, low=close-1))


def test_review_keeps_original_preorder_evidence(tmp_path):
    store = Account05StateStore(tmp_path / "ledger.sqlite")
    frame = market()
    signals = Account05Signals(Trend15m.UP, "上涨", (
        Account05Trigger("局部顶部做空", -1, "t", "新鲜局部顶部"),))
    value = {**entry_context(signals, frame, frame, frame),
             "source": "局部顶部做空", "reason": "新鲜局部顶部"}
    store.record_entry_review("signal", value)
    store.record_entry_review("signal", {**value, "reason": "稍后不同市场"})
    payload = store.entry_review("signal")
    assert json.loads(payload)["reason"] == "新鲜局部顶部"
    assert len(json.loads(payload)["rules"]) == 8
    assert "局部顶部做空=满足" in review_summary(payload)[1]
    assert "真正顶部做空=未满足" in review_summary(payload)[1]
    assert json.loads(payload)["last_row_running"]
    store.close()


def test_historical_orders_are_unknown_and_extra_sources_are_explicit():
    assert "证据不足" in review_summary("")[0]
    assert "六类之外" in review_summary(json.dumps(
        dict(source="大极值反手", reason="测试", rules=[])))[0]


def test_extreme_rejects_consecutive_same_color_and_mirrors_long(monkeypatch):
    monkeypatch.setattr("quantbot.account05_signals._large_extreme", lambda *a, **k: (True, "test"))
    for direction in (-1, 1):
        frame = market()
        frame.loc[30:36, ["open", "close"]] = [120, 120]
        frame.loc[37, ["open", "close"]] = [120, 120 + direction]
        frame.loc[38, ["open", "close"]] = [120 + direction, 120 + direction*2]
        assert _extreme_rotation(frame, market(), market()) is None


def test_same_higher_extremes_have_same_dedup_key(monkeypatch):
    monkeypatch.setattr("quantbot.account05_signals._large_extreme", lambda *a, **k: (True, "test"))
    frame = market()
    frame.loc[30:37, ["open", "close"]] = [120, 120]
    frame.loc[38, ["open", "close"]] = [120, 117]
    first = _extreme_rotation(frame, market(), market())
    frame.loc[38, "date"] += pd.Timedelta(seconds=30)
    second = _extreme_rotation(frame, market(), market())
    assert first and second and first.anchor_time != second.anchor_time
    assert first.structure_key == second.structure_key
