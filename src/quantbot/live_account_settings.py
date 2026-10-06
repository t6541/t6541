"""Durable settings for each isolated Live account slot.

The strategy code stays shared.  An account may optionally layer a small,
validated set of dataclass field overrides over that shared baseline.
"""

from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3


DEFAULT_ORDER_CONTRACTS = "0.05"
MIN_ORDER_CONTRACTS = Decimal("0.01")
MAX_ORDER_CONTRACTS = Decimal("1.00")
ORDER_CONTRACT_STEP = Decimal("0.01")
DEFAULT_OPERATING_CAPITAL_USDT = "100.00"
DEFAULT_ADDON_TAKE_PROFIT_POINTS = "10.00"
DEFAULT_ACCOUNT05_LONG_SLOTS = "60"
DEFAULT_ACCOUNT05_SHORT_SLOTS = "60"
DEFAULT_ACCOUNT05_EXTREME_CONTRACTS = "0.03"
DEFAULT_ACCOUNT05_SLOT_CONTRACTS = "0.02"
# Product policy: new base exposure is retired. Old positions retain their TPs.
ACCOUNT05_BASE_ENTRIES_ENABLED = False
MINIMUM_ADDON_TAKE_PROFIT_POINTS = Decimal("10.00")
STRATEGY_SOURCES = frozenset({"shared", "account"})
OVERRIDABLE_SECTIONS = frozenset({"strategy", "risk"})


def normalize_order_contracts(value: object) -> str:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("下单数量必须是数字") from None
    if not amount.is_finite() or amount < MIN_ORDER_CONTRACTS or amount > MAX_ORDER_CONTRACTS:
        raise ValueError("下单数量必须在0.01至1.00张之间")
    if amount % ORDER_CONTRACT_STEP:
        raise ValueError("下单数量必须按0.01张递增")
    return format(amount.quantize(ORDER_CONTRACT_STEP), "f")


def _normalize_decimal_setting(value: object, *, label: str,
                               minimum: Decimal, maximum: Decimal) -> str:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}必须是数字") from None
    if not amount.is_finite() or not minimum <= amount <= maximum:
        raise ValueError(f"{label}必须在{minimum}至{maximum}之间")
    return format(amount.quantize(Decimal("0.01")), "f")


class LiveAccountSettings:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS account_settings (
                setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL
            )""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def _get(self, key: str, default: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM account_settings WHERE setting_key=?", (key,)
            ).fetchone()
        return str(row[0]) if row else default

    def _set(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO account_settings VALUES (?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
                (key, value),
            )

    def order_contracts(self) -> str:
        return normalize_order_contracts(self._get("order_contracts", DEFAULT_ORDER_CONTRACTS))

    def set_order_contracts(self, value: object) -> str:
        normalized = normalize_order_contracts(value)
        self._set("order_contracts", normalized)
        return normalized

    def operating_capital_usdt(self) -> str:
        return _normalize_decimal_setting(
            self._get("operating_capital_usdt", DEFAULT_OPERATING_CAPITAL_USDT),
            label="运行资金", minimum=Decimal("1"), maximum=Decimal("1000000"))

    def set_operating_capital_usdt(self, value: object) -> str:
        normalized = _normalize_decimal_setting(
            value, label="运行资金", minimum=Decimal("1"), maximum=Decimal("1000000"))
        self._set("operating_capital_usdt", normalized)
        return normalized

    def addon_take_profit_points(self) -> str:
        raw = self._get("addon_take_profit_points", DEFAULT_ADDON_TAKE_PROFIT_POINTS)
        try:
            amount = Decimal(raw)
        except (InvalidOperation, ValueError):
            amount = Decimal(DEFAULT_ADDON_TAKE_PROFIT_POINTS)
        if not amount.is_finite():
            amount = Decimal(DEFAULT_ADDON_TAKE_PROFIT_POINTS)
        # Older saved values below 10 are raised safely instead of making the
        # live-account worker fail while loading its existing settings.
        amount = max(amount, MINIMUM_ADDON_TAKE_PROFIT_POINTS)
        return _normalize_decimal_setting(
            amount, label="小单止盈点数",
            minimum=MINIMUM_ADDON_TAKE_PROFIT_POINTS, maximum=Decimal("1000"))

    def set_addon_take_profit_points(self, value: object) -> str:
        normalized = _normalize_decimal_setting(
            value, label="小单止盈点数",
            minimum=MINIMUM_ADDON_TAKE_PROFIT_POINTS, maximum=Decimal("1000"))
        self._set("addon_take_profit_points", normalized)
        return normalized

    def _slot_capacity(self, key: str, default: str) -> str:
        raw = self._get(key, default)
        try:
            value = int(raw)
        except ValueError:
            value = int(default)
        return str(max(0, min(500, value)))

    def long_slot_capacity(self) -> str:
        return self._slot_capacity("account05_long_slot_capacity", DEFAULT_ACCOUNT05_LONG_SLOTS)

    def short_slot_capacity(self) -> str:
        return self._slot_capacity("account05_short_slot_capacity", DEFAULT_ACCOUNT05_SHORT_SLOTS)

    def set_slot_capacities(self, long_value: object, short_value: object) -> tuple[str, str]:
        try:
            long_count, short_count = int(str(long_value).strip()), int(str(short_value).strip())
        except ValueError:
            raise ValueError("多空槽位容量必须是整数") from None
        if not (0 <= long_count <= 500 and 0 <= short_count <= 500):
            raise ValueError("多空槽位容量必须在0至500之间")
        self._set("account05_long_slot_capacity", str(long_count))
        self._set("account05_short_slot_capacity", str(short_count))
        return str(long_count), str(short_count)

    def _account05_contracts(self, key: str, default: str, label: str) -> str:
        return _normalize_decimal_setting(
            self._get(key, default), label=label,
            minimum=MIN_ORDER_CONTRACTS, maximum=MAX_ORDER_CONTRACTS)

    def extreme_rotation_contracts(self) -> str:
        return self._account05_contracts(
            "account05_extreme_rotation_contracts",
            DEFAULT_ACCOUNT05_EXTREME_CONTRACTS, "极值反手张数")

    def slot_contracts(self) -> str:
        return self._account05_contracts(
            "account05_slot_contracts", DEFAULT_ACCOUNT05_SLOT_CONTRACTS,
            "槽位单笔张数")

    def set_account05_contracts(self, extreme_value: object,
                                slot_value: object) -> tuple[str, str]:
        extreme = _normalize_decimal_setting(
            extreme_value, label="极值反手张数", minimum=MIN_ORDER_CONTRACTS,
            maximum=MAX_ORDER_CONTRACTS)
        slot = _normalize_decimal_setting(
            slot_value, label="槽位单笔张数", minimum=MIN_ORDER_CONTRACTS,
            maximum=MAX_ORDER_CONTRACTS)
        self._set("account05_extreme_rotation_contracts", extreme)
        self._set("account05_slot_contracts", slot)
        return extreme, slot

    def base_rebuild_enabled(self) -> bool:
        return ACCOUNT05_BASE_ENTRIES_ENABLED

    def set_base_rebuild_enabled(self, enabled: bool) -> bool:
        self._set("account05_base_rebuild_enabled", "0")
        return False

    def strategy_source(self) -> str:
        value = self._get("strategy_source", "shared")
        return value if value in STRATEGY_SOURCES else "shared"

    def set_strategy_source(self, value: str) -> str:
        if value not in STRATEGY_SOURCES:
            raise ValueError("策略来源只能是shared或account")
        self._set("strategy_source", value)
        return value

    def strategy_overrides(self) -> dict:
        value = json.loads(self._get("strategy_overrides", "{}"))
        if not isinstance(value, dict):
            raise ValueError("单账户策略覆盖必须是JSON对象")
        return value

    def set_strategy_overrides(self, value: dict) -> None:
        if not isinstance(value, dict) or set(value) - OVERRIDABLE_SECTIONS:
            raise ValueError("单账户策略覆盖只允许strategy和risk段")
        self._set("strategy_overrides", json.dumps(value, ensure_ascii=False, sort_keys=True))


def apply_account_strategy(base, settings: LiveAccountSettings):
    """Return the shared config or a validated per-account dataclass overlay."""
    if settings.strategy_source() == "shared":
        return base, "共享策略"
    overrides = settings.strategy_overrides()
    updated = base
    for section, values in overrides.items():
        if section not in OVERRIDABLE_SECTIONS or not isinstance(values, dict):
            raise ValueError(f"无效的单账户策略覆盖段: {section}")
        current = getattr(updated, section)
        allowed = {item.name for item in fields(current)}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"{section}包含未知策略参数: {sorted(unknown)}")
        updated = replace(updated, **{section: replace(current, **values)})
    return updated, "单账户策略覆盖"
