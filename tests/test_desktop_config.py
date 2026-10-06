from pathlib import Path

from quantbot.credential_store import delete_credentials, load_credentials, save_credentials
from quantbot.okx import OkxCredentials
from quantbot.win32desktop import load_branding, load_instruments, migrate_legacy_config, save_instruments
from quantbot.version import (APP_VERSION, CURRENT_STRATEGY_RULE_SUMMARY, STRATEGY_RULES,
                              changelog_pages, changelog_text)
from quantbot.config import load_config
import quantbot.win32desktop as desktop


def test_legacy_isolated_config_migrates_with_backup(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[okx]\nmargin_mode = "isolated"\nleverage = 100\n', encoding="utf-8")
    assert migrate_legacy_config(config)
    assert 'margin_mode = "cross"' in config.read_text(encoding="utf-8")
    assert 'margin_mode = "isolated"' in (tmp_path / "config.toml.bak").read_text(encoding="utf-8")
    assert not migrate_legacy_config(config)


def test_startup_worker_immediately_scans_saved_demo_state(monkeypatch, tmp_path):
    messages = []
    monkeypatch.setattr(desktop, "CONFIG", Path("configs/default.toml"))
    monkeypatch.setattr(desktop, "WORKSPACE", tmp_path)
    monkeypatch.setattr(desktop, "HANDLES", {"status": 1})
    monkeypatch.setattr(desktop, "_restore_saved_demo_state", lambda actual, database: ())
    monkeypatch.setattr(desktop, "_text", lambda handle, value: messages.append(value))
    desktop._startup_restore_worker()
    assert messages and "启动恢复扫描完成" in messages[-1]
    assert "触发排队已扫描" in messages[-1]


def test_legacy_single_instrument_migrates_to_list(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[okx]\ninstrument = "ETH-USDT-SWAP"\n', encoding="utf-8")
    assert migrate_legacy_config(config)
    assert 'instruments = ["ETH-USDT-SWAP"]' in config.read_text(encoding="utf-8")


def test_retired_channel_keys_are_removed_by_workspace_migration(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[okx]\nmargin_mode = "cross"\n'
        'validation_channel_window = 20\n'
        'validation_channel_entry_fraction = 0.45\n'
        'validation_extreme_entry_fraction = 0.97\n'
        'validation_channel_bar = "1m"\n',
        encoding="utf-8",
    )
    assert migrate_legacy_config(config)
    migrated = config.read_text(encoding="utf-8")
    assert "validation_channel" not in migrated
    assert "validation_extreme_entry_fraction" not in migrated
    assert 'margin_mode = "cross"' in migrated
    assert "validation_channel_window" in (tmp_path / "config.toml.bak").read_text(encoding="utf-8")


def test_config_loader_ignores_only_retired_channel_keys(tmp_path):
    source = Path(__file__).parents[1] / "configs" / "default.toml"
    text = source.read_text(encoding="utf-8").replace(
        '[okx]\n',
        '[okx]\nvalidation_channel_window = 20\nvalidation_channel_bar = "1m"\n',
    )
    config = tmp_path / "config.toml"
    config.write_text(text, encoding="utf-8")
    assert load_config(config).okx.instruments == ("ETH-USDT-SWAP",)

    config.write_text(text.replace("validation_channel_window = 20", "unexpected_key = 20"),
                      encoding="utf-8")
    try:
        load_config(config)
    except ValueError as exc:
        assert "unexpected_key" in str(exc)
    else:
        raise AssertionError("unrelated unknown keys must remain rejected")


def test_instrument_list_can_be_replaced(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[okx]\ninstruments = ["ETH-USDT-SWAP"]\n', encoding="utf-8")
    save_instruments(config, ("BTC-USDT-SWAP", "ETH-USDT-SWAP"))
    assert load_instruments(config) == ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


def test_version_and_changelog_are_kept_in_sync():
    text = changelog_text()
    assert APP_VERSION == "0.7.283"
    assert text.startswith(f"v{APP_VERSION}")
    assert "新增内置更新日志" in text
    assert "Windows EXE" in text


def test_current_strategy_summary_is_chinese_and_separate_from_release_history():
    text = "\n".join(detail for _, detail in CURRENT_STRATEGY_RULE_SUMMARY)
    assert "真正顶底反转" in text
    assert "1至3根" in text
    assert "[v0." not in text


def test_changelog_exceeds_legacy_edit_limit_and_desktop_expands_it_before_writing():
    assert len(changelog_text()) > 32767
    source = Path(desktop.__file__).read_text(encoding="utf-8")
    assert "EM_SETLIMITTEXT" in source
    assert '_create(\n            hwnd, "EDIT", "",' in source


def test_changelog_is_paginated_at_fifty_lines_with_newest_page_first():
    pages = changelog_pages(50)
    assert len(pages) > 1
    assert all(len(page.splitlines()) <= 50 for page in pages)
    assert pages[0].startswith(f"v{APP_VERSION}")
    assert "每页50行" in Path(desktop.__file__).read_text(encoding="utf-8")


def test_live_aggressive_poll_interval_is_ten_seconds():
    assert desktop.LIVE_AGGRESSIVE_POLL_SECONDS == 10


def test_order_notice_is_tall_enough_for_full_trade_reason():
    assert desktop.ORDER_NOTICE_WIDTH == 560
    assert desktop.ORDER_NOTICE_HEIGHT == 500


def test_current_strategy_table_has_no_channel_percentage_gate():
    text = "\n".join(str(value) for rule in STRATEGY_RULES for value in rule.values())
    for retired in ("通道下方25%", "通道上方25%", "区间下部25%", "区间上部25%", "下沿25%", "上沿25%"):
        assert retired not in text


def test_live_main_audit_uses_chinese_instead_of_okx_enum_values():
    report = {
        "account": {"acctLv": "2", "posMode": "long_short_mode"},
        "usdt_balance": {"eq": "100", "availBal": "90"},
        "instrument": {"instId": "ETH-USDT-SWAP", "state": "live", "minSz": "0.01",
                       "lotSz": "0.01", "ctVal": "0.1", "ctValCcy": "ETH"},
        "market": {"last": "2460", "markPx": "2460.1"},
        "minimum_order": {"api_size_contracts": "0.01", "estimated_notional_usdt": "2.46"},
        "leverage": [{"posSide": "long", "lever": "100"}, {"posSide": "short", "lever": "100"}],
        "open_positions": [{"posSide": "short", "pos": "0.01", "mgnMode": "cross"}],
    }
    text = desktop._format_live_audit(report)
    assert "账户模式=单币种保证金" in text
    assert "持仓模式=双向持仓" in text
    assert "多仓=100倍" in text and "空仓=100倍" in text
    assert "合约状态=可交易" in text
    assert "long_short_mode" not in text and "acctLv" not in text and " cross" not in text


def test_live_runtime_action_and_timezone_error_are_chinese():
    assert desktop._live_message_zh("observe") == "继续观察"
    translated = desktop._live_message_zh("can't subtract offset-naive and offset-aware datetimes")
    assert "历史时间记录缺少时区" in translated
    assert "offset-naive" not in translated
    assert desktop._live_message_zh("exit_submitted") == "主动止盈已提交"
    partial = desktop._live_message_zh("1m pullback rejected at falling MA20")
    assert "rejected" not in partial and "反抽下降中的MA20后受阻回落" in partial
    periods = desktop._live_message_zh("15m↓｜5m↓｜1m↑")
    assert periods == "15分钟↓｜5分钟↓｜1分钟↑"


def test_retired_channel_config_error_is_fully_chinese():
    translated = desktop._live_message_zh(
        "Unknown OkxConfig keys: ['validation_channel_bar', "
        "'validation_channel_entry_fraction', 'validation_channel_window', "
        "'validation_extreme_entry_fraction']"
    )
    assert "配置文件中存在无法识别的OKX参数" in translated
    assert "已废弃的一分钟通道周期参数" in translated
    assert "validation_" not in translated
    assert "Unknown" not in translated


def test_live_timestamp_and_order_status_are_fully_chinese():
    timestamp = desktop._live_message_zh(
        'OKX Live HTTP 401: {"msg":"Timestamp request expired","code":"50102"}'
    )
    order = desktop._live_message_zh(
        "one-contract Demo order submitted with server-side ma5_turn take-profit"
    )
    assert "身份校验失败" in timestamp and "时间戳已过期" in timestamp
    assert "Timestamp" not in timestamp and "msg" not in timestamp and "code" not in timestamp
    assert "下单已提交" in order and "Demo" not in order and "ma5_turn" not in order


def test_strategy_rule_table_covers_every_current_strategy_and_protection_rule():
    assert [row["version"] for row in STRATEGY_RULES] == [
        "demo-frequency-validation-v191", "range_pivot_reversal_v59", "ma20_trend_retest_v60",
        "shared-rules-v44",
    ]
    assert all(row["trigger"] and row["take_profit"] and row["stop_loss"] and row["limits"]
               for row in STRATEGY_RULES)
    assert "【三阶段反转①" in STRATEGY_RULES[0]["trigger"]
    assert "【三阶段反转③" in STRATEGY_RULES[0]["trigger"]
    assert "顶部镜像" in STRATEGY_RULES[0]["trigger"]
    assert "取消价格通道" in STRATEGY_RULES[0]["trigger"]
    assert "顶部累计转弱" in STRATEGY_RULES[0]["trigger"]
    assert "多周期技术指标确认门" in STRATEGY_RULES[0]["trigger"]
    assert "下跌局部反抽失败" in STRATEGY_RULES[0]["continuation"]
    assert "取消三周期交叉" in STRATEGY_RULES[0]["trigger"]
    assert "双向分层持仓" in STRATEGY_RULES[0]["limits"]
    assert "形态经验库" in STRATEGY_RULES[-1]["limits"]
    assert "旧方向失效" in STRATEGY_RULES[2]["trigger"]
    assert "\n" in STRATEGY_RULES[0]["trigger"]
    shared = STRATEGY_RULES[-1]
    assert shared["strategy"].startswith("共享策略")
    assert "软件启动、开始观察或解锁时先扫描并恢复" in shared["trigger"]
    assert "仅OKX模拟盘，禁止实盘" in shared["trigger"]
    assert "MA偏离插针回归" in shared["trigger"]
    assert "恢复入口" in shared["limits"]
    assert all("【扎针熔断】" not in row["limits"] for row in STRATEGY_RULES[:3])
    assert all("取消扎针停机" in row["limits"] for row in STRATEGY_RULES[:3])
    assert all(label in shared["continuation"] for label in ("激进型", "保守型", "稳妥型"))


def test_branding_can_be_changed_without_code_changes(tmp_path):
    branding = tmp_path / "branding.toml"
    branding.write_text('[branding]\nname = "代理商甲"\nwechat = "agent001"\n', encoding="utf-8")
    assert load_branding(branding) == ("代理商甲", "agent001")


def test_invalid_branding_uses_owner_fallback(tmp_path):
    branding = tmp_path / "branding.toml"
    branding.write_text("not valid toml =", encoding="utf-8")
    assert load_branding(branding) == ("谭明泽", "tmz8873")


def test_windows_encrypted_credentials_round_trip(tmp_path):
    path = tmp_path / "credentials.bin"
    expected = OkxCredentials("demo-key", "demo-secret", "demo-passphrase")
    save_credentials(path, expected)
    assert b"demo-secret" not in path.read_bytes()
    assert load_credentials(path) == expected
    delete_credentials(path)
    assert load_credentials(path) is None


def test_eth_strategy_uses_supported_minute_bars():
    config = load_config(Path(__file__).parents[1] / "configs" / "eth-trend.toml")
    assert config.strategy.signal_bars == ("1m", "5m", "15m")
    assert config.backtest.fee_rebate_rate == 0.20
    assert config.risk.max_trades_per_day == 24
    assert config.okx.validation_take_profit_pct == 0.002
    assert config.okx.validation_stop_loss_pct == 0.0015
    assert config.okx.validation_trailing_callback_pct == 0.0005
    assert config.okx.validation_max_trades_per_day == 200
    assert config.okx.validation_max_leverage == 10
    assert config.okx.validation_contracts == 1


def test_range_pivot_strategy_has_safe_observation_defaults():
    config = load_config(Path(__file__).parents[1] / "configs" / "range-pivot.toml")
    assert config.strategy.name == "range_pivot_reversal"
    assert config.strategy.pivot_lookback == 8
    assert config.strategy.pivot_timeframe == "5m"
    assert config.strategy.pivot_lookback_min == 5
    assert config.strategy.pivot_lookback_max == 10
    assert config.backtest.fee_rebate_rate == .20
    assert config.okx.environment == "demo"
    assert config.okx.enable_demo_orders is False

