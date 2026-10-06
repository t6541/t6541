from quantbot.loss_review import diagnose_losing_trade
from quantbot.state import StateStore


def test_fee_dominated_loss_has_specific_recommendation():
    diagnosis = diagnose_losing_trade({
        "gross_pnl": .02, "total_fees": -.03, "net_pnl": -.01,
        "signal_reason": "range entry", "branch": "relative_extreme_reversal",
        "exit_reason": "take_profit",
    })
    assert diagnosis.category == "交易成本吞噬"
    assert "最小预期净利润" in diagnosis.recommendation


def test_loss_duration_accepts_legacy_naive_signal_time_and_aware_close_time():
    diagnosis = diagnose_losing_trade({
        "gross_pnl": -1, "total_fees": 0, "net_pnl": -1,
        "signal_time": "2026-08-25T15:44:00",
        "close_time": "2026-08-25T15:46:00+00:00",
        "entry_reference": 100, "stop_price": 99, "exit_reason": "stop_loss",
    })
    assert "持仓2.0分钟" in diagnosis.evidence


def test_closing_loss_creates_one_durable_review(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.open_trade_lifecycle(
        trade_uid="LOSS1", strategy_id="strategy_01", strategy_version="v1",
        instrument="ETH-USDT-SWAP", direction=1,
        signal_time="2026-08-16T00:00:00+00:00", signal_reason="test reversal",
        signal_context={"atr_1m": .8}, order_id="1", algo_id="2",
        entry_reference=1880, stop_price=1879.9,
        trailing_activation=1881, trailing_callback=.5, branch="extreme_reversal",
    )
    store.close_trade_lifecycle(
        "LOSS1", close_price=1879.9, gross_pnl=-.1, total_fees=-.01,
        exit_reason="stop_loss",
    )
    reviews = store.loss_reviews()
    assert len(reviews) == 1
    assert reviews[0]["trade_uid"] == "LOSS1"
    assert reviews[0]["category"] == "保护空间过窄"
    assert reviews[0]["trigger_snapshot"] == "test reversal"
    store.close()


def test_same_side_lifecycle_can_be_scoped_to_current_strategy_version(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.open_trade_lifecycle(
        trade_uid="OLD-LONG", strategy_id="strategy_01", strategy_version="v101",
        instrument="ETH-USDT-SWAP", direction=1,
        signal_time="2026-08-30T00:00:00+00:00", signal_reason="old version",
        signal_context={}, order_id="1", algo_id="",
        entry_reference=100, stop_price=99,
        trailing_activation=102, trailing_callback=.5,
    )
    assert store.has_open_same_side_trade("strategy_01", "ETH-USDT-SWAP", 1)
    assert store.has_open_same_side_trade(
        "strategy_01", "ETH-USDT-SWAP", 1, strategy_version="v101")
    assert not store.has_open_same_side_trade(
        "strategy_01", "ETH-USDT-SWAP", 1, strategy_version="v102")
    store.close()


def test_partial_same_side_exit_is_attached_to_only_one_lifecycle_layer(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    common = dict(
        strategy_id="strategy_02", strategy_version="v156",
        instrument="ETH-USDT-SWAP", direction=1,
        signal_reason="layered long", algo_id="",
        stop_price=99, trailing_activation=102, trailing_callback=.5,
    )
    store.open_trade_lifecycle(
        trade_uid="CORE-LONG", signal_time="2026-09-06T00:00:00+00:00",
        signal_context={"ma5_exit_timeframe": "5m"}, order_id="core-entry",
        entry_reference=100, branch="early_one_minute_frozen_stage_launch", **common,
    )
    store.open_trade_lifecycle(
        trade_uid="ADDON-LONG", signal_time="2026-09-06T00:10:00+00:00",
        signal_context={"ma5_exit_timeframe": "1m"}, order_id="addon-entry",
        entry_reference=101, branch="core_reversal_continuation_addon", **common,
    )

    store.mark_trade_exit_requested_by_uid("ADDON-LONG", "one_minute_ma5_take_profit")

    rows = {row["trade_uid"]: row for row in store.open_trade_lifecycles("v156")}
    assert rows["ADDON-LONG"]["exit_reason"] == "one_minute_ma5_take_profit"
    assert rows["CORE-LONG"]["exit_reason"] in (None, "")
    store.close()


def test_external_loss_backfill_is_deduplicated(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    kwargs = dict(
        trade_uid="OKX-1", strategy_id="strategy_02",
        close_time="2026-08-16T01:00:00+08:00", direction=-1,
        gross_pnl=-1.0, fee=-.01, evidence="OKX order 1",
    )
    store.record_external_loss(**kwargs)
    store.record_external_loss(**kwargs)
    assert len(store.loss_reviews()) == 1
    assert store.loss_reviews()[0]["confidence"] == "低"
    store.close()


def test_external_loss_uses_nearest_unmatched_entry_snapshot(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.record_entry_snapshot(
        snapshot_uid="SNAP1", strategy_id="strategy_02", strategy_version="v2",
        order_id="open-1", signal_time="2026-08-16T01:00:00+08:00", direction=1,
        branch="relative_extreme_reversal", trigger_reason="结构低点扫损收回做多",
        context={"atr_1m": .8, "risk_profile": "aggressive"},
        entry_reference=1880, stop_price=1876, take_profit_price=1886,
    )
    store.record_external_loss(
        trade_uid="CLOSE1", strategy_id="strategy_02",
        close_time="2026-08-16T01:05:00+08:00", direction=1,
        gross_pnl=-1, fee=-.01, evidence="OKX close",
    )
    review = store.loss_reviews()[0]
    assert review["trigger_snapshot"] == "结构低点扫损收回做多"
    assert "开仓快照" in review["evidence"]
    assert review["review_status"] == "待汇总"
    store.close()
