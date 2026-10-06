"""Pure planning rules for the isolated account-05 hedge/grid strategy.

This module performs no exchange I/O.  Live execution remains locked until a
separate adapter can reconcile fills and one reduce-only take-profit per lot.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from enum import StrEnum


class Trend15m(StrEnum):
    UP = "up"
    DOWN = "down"
    UNCLEAR = "unclear"


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


MINIMUM_ADDON_TAKE_PROFIT_POINTS = Decimal("10")


def addon_profit_points(entry_price: Decimal, current_price: Decimal,
                        side: PositionSide) -> Decimal:
    """Return favorable absolute price movement for one local add-on lot."""
    direction = Decimal("1") if side is PositionSide.LONG else Decimal("-1")
    return direction * (Decimal(str(current_price)) - Decimal(str(entry_price)))


def addon_take_profit_threshold(configured_points: Decimal) -> Decimal:
    """Keep the add-on profit target at least 10 absolute price points."""
    return max(MINIMUM_ADDON_TAKE_PROFIT_POINTS, Decimal(configured_points))


def addon_take_profit_unlocked(entry_price: Decimal, current_price: Decimal,
                               side: PositionSide,
                               configured_points: Decimal) -> bool:
    """Profit-taking requires a strict move beyond the configured point target."""
    return addon_profit_points(entry_price, current_price, side) > addon_take_profit_threshold(
        configured_points)


@dataclass(frozen=True)
class Account05Config:
    operating_capital_usdt: Decimal = Decimal("100")
    leverage: Decimal = Decimal("100")
    trend_margin_pct: Decimal = Decimal("0.012")
    hedge_margin_pct: Decimal = Decimal("0.006")
    addon_margin_pct: Decimal = Decimal("0.001")
    recovery_slot_contracts: Decimal = Decimal("0.02")
    fixed_addon_contracts: Decimal = Decimal("0.01")
    extreme_rotation_contracts: Decimal = Decimal("0.03")
    slot_contracts: Decimal = Decimal("0.02")
    experimental_fast_top_enabled: bool = False
    max_addons_per_side: int = 36
    long_slot_capacity: int = 60
    short_slot_capacity: int = 60
    max_margin_pct_per_side: Decimal = Decimal("0.036")
    max_combined_floating_loss_pct: Decimal = Decimal("0")
    base_take_profit_pcts: tuple[Decimal, Decimal] = (
        Decimal("0.005"), Decimal("0.007"))
    addon_take_profit_points: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        if self.operating_capital_usdt <= 0 or self.leverage <= 0:
            raise ValueError("operating capital and leverage must be positive")
        if self.max_addons_per_side < 0:
            raise ValueError("max_addons_per_side cannot be negative")
        if self.fixed_addon_contracts <= 0:
            raise ValueError("fixed add-on contracts must be positive")
        if self.recovery_slot_contracts <= 0:
            raise ValueError("recovery slot contracts must be positive")
        if self.extreme_rotation_contracts <= 0 or self.slot_contracts <= 0:
            raise ValueError("account05 contract sizes must be positive")
        if not (Decimal("0") <= self.max_combined_floating_loss_pct < Decimal("1")):
            raise ValueError("floating loss limit must be between 0 and 1")


@dataclass(frozen=True)
class ContractSpec:
    ct_val: Decimal
    lot_size: Decimal
    min_size: Decimal
    ct_mult: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if min(self.ct_val, self.lot_size, self.min_size, self.ct_mult) <= 0:
            raise ValueError("contract metadata must be positive")


@dataclass(frozen=True)
class EntryPlan:
    side: PositionSide
    margin_pct: Decimal
    margin_usdt: Decimal
    notional_usdt: Decimal
    contracts: Decimal


@dataclass(frozen=True)
class AddonPermission:
    allowed: bool
    reason_code: str


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def contracts_for_notional(notional_usdt: Decimal, mark_price: Decimal,
                           spec: ContractSpec) -> Decimal:
    """Convert USDT notional to OKX contract size using live metadata."""
    if notional_usdt <= 0 or mark_price <= 0:
        raise ValueError("notional and mark price must be positive")
    raw = notional_usdt / (mark_price * spec.ct_val * spec.ct_mult)
    sized = _floor_step(raw, spec.lot_size)
    return sized if sized >= spec.min_size else Decimal("0")


def initial_margin_pcts(trend: Trend15m) -> dict[PositionSide, Decimal]:
    """Return the requested 2:1, 1:2, or neutral 1:1 hedge allocation."""
    trend = Trend15m(trend)
    if trend is Trend15m.UP:
        return {PositionSide.LONG: Decimal("0.012"), PositionSide.SHORT: Decimal("0.006")}
    if trend is Trend15m.DOWN:
        return {PositionSide.LONG: Decimal("0.006"), PositionSide.SHORT: Decimal("0.012")}
    return {PositionSide.LONG: Decimal("0.006"), PositionSide.SHORT: Decimal("0.006")}


def initial_entry_plans(config: Account05Config, trend: Trend15m,
                        mark_price: Decimal, spec: ContractSpec) -> tuple[EntryPlan, EntryPlan]:
    allocations = initial_margin_pcts(trend)
    plans = []
    for side in (PositionSide.LONG, PositionSide.SHORT):
        margin_pct = allocations[side]
        margin = config.operating_capital_usdt * margin_pct
        notional = margin * config.leverage
        plans.append(EntryPlan(
            side, margin_pct, margin, notional,
            contracts_for_notional(notional, mark_price, spec),
        ))
    return tuple(plans)  # type: ignore[return-value]


def replacement_margin_pct(trend: Trend15m, side: PositionSide,
                           opposing_margin_pct: Decimal | None = None,
                           max_margin_pct: Decimal = Decimal("0.012")) -> Decimal:
    """Size a rebuilt base leg, never exceeding 1.2% operating capital.

    The six independent 0.1% adjustment lots remain future capacity.  A
    favourable 15-minute trend must not pre-spend that capacity by rebuilding
    a 1.8% base against an opposite 1.2% position.
    """
    trend, side = Trend15m(trend), PositionSide(side)
    follows_trend = ((trend is Trend15m.UP and side is PositionSide.LONG)
                     or (trend is Trend15m.DOWN and side is PositionSide.SHORT))
    opposes_trend = ((trend is Trend15m.UP and side is PositionSide.SHORT)
                     or (trend is Trend15m.DOWN and side is PositionSide.LONG))
    if opposing_margin_pct is None or opposing_margin_pct <= 0:
        if trend is Trend15m.UNCLEAR:
            return Decimal("0.006")
        return Decimal("0.012") if follows_trend else Decimal("0.006")
    return min(Decimal("0.012"), max_margin_pct, opposing_margin_pct)


def addon_price_zone_allows(side: PositionSide, mark_price: Decimal,
                            opposite_average_price: Decimal,
                            proximity_pct: Decimal = Decimal("0.001")) -> bool:
    """Reserve adjustment lots until price reaches the opposite cost zone.

    A short adjustment is useful near/above the long average; a long
    adjustment is useful near/below the short average.  ``proximity_pct``
    admits a narrow 0.1% approach band so tick noise does not miss the zone.
    """
    side = PositionSide(side)
    if mark_price <= 0 or opposite_average_price <= 0:
        return False
    if side is PositionSide.SHORT:
        return mark_price >= opposite_average_price * (Decimal("1") - proximity_pct)
    return mark_price <= opposite_average_price * (Decimal("1") + proximity_pct)


def take_profit_price(entry_price: Decimal, side: PositionSide,
                      profit_pct: Decimal, tick_size: Decimal) -> Decimal:
    """Create a favorable, tick-aligned TP trigger for one specific lot."""
    if entry_price <= 0 or profit_pct <= 0 or tick_size <= 0:
        raise ValueError("entry price, profit percentage and tick size must be positive")
    side = PositionSide(side)
    raw = (entry_price * (Decimal("1") + profit_pct) if side is PositionSide.LONG
           else entry_price * (Decimal("1") - profit_pct))
    rounding = ROUND_UP if side is PositionSide.LONG else ROUND_DOWN
    return (raw / tick_size).to_integral_value(rounding=rounding) * tick_size


def take_profit_price_by_points(entry_price: Decimal, side: PositionSide,
                                profit_points: Decimal, tick_size: Decimal) -> Decimal:
    """Price an account-05 small lot by a configurable absolute point target."""
    if entry_price <= 0 or profit_points <= 0 or tick_size <= 0:
        raise ValueError("entry price, profit points and tick size must be positive")
    side = PositionSide(side)
    raw = (entry_price + profit_points if side is PositionSide.LONG
           else entry_price - profit_points)
    if raw <= 0:
        raise ValueError("short take-profit price must remain positive")
    rounding = ROUND_UP if side is PositionSide.LONG else ROUND_DOWN
    return (raw / tick_size).to_integral_value(rounding=rounding) * tick_size


def addon_permission(config: Account05Config, *, addon_count: int,
                     slot_capacity: int | None = None,
                     side_margin_used_usdt: Decimal,
                     combined_unrealized_pnl_usdt: Decimal) -> AddonPermission:
    """Apply the configured per-side slot count and floating-loss gate.

    Slot capacity is the only per-side position-size limit; the old
    operating-capital percentage ceiling is intentionally disabled.
    """
    loss_limit = config.operating_capital_usdt * config.max_combined_floating_loss_pct
    if config.max_combined_floating_loss_pct > 0 and combined_unrealized_pnl_usdt <= -loss_limit:
        return AddonPermission(False, "combined_floating_loss_limit_reached")
    capacity = config.max_addons_per_side if slot_capacity is None else slot_capacity
    if addon_count >= capacity:
        return AddonPermission(False, "side_addon_count_limit_reached")
    return AddonPermission(True, "addon_allowed")


def addon_entry_plan(config: Account05Config, side: PositionSide,
                     mark_price: Decimal, spec: ContractSpec) -> EntryPlan:
    """Independent fixed-contract small-order board."""
    contracts = config.slot_contracts
    if contracts < spec.min_size or _floor_step(contracts, spec.lot_size) != contracts:
        raise ValueError("fixed small order is below minimum or not aligned")
    notional = contracts * mark_price * spec.ct_val * spec.ct_mult
    margin = notional / config.leverage
    return EntryPlan(PositionSide(side), margin / config.operating_capital_usdt,
                     margin, notional, contracts)


def recovery_slot_entry_plan(config: Account05Config, side: PositionSide,
                             mark_price: Decimal, spec: ContractSpec) -> EntryPlan:
    """Recovery pool slot using a fixed 0.02-contract board."""
    if mark_price <= 0:
        raise ValueError("mark price must be positive")
    contracts = config.slot_contracts
    if contracts < spec.min_size or _floor_step(contracts, spec.lot_size) != contracts:
        raise ValueError("0.02-contract recovery slot is below minimum or not aligned")
    if contracts < spec.min_size:
        raise ValueError("0.02-contract recovery slot is below exchange minimum size")
    notional = contracts * mark_price * spec.ct_val * spec.ct_mult
    margin = notional / config.leverage
    margin_pct = margin / config.operating_capital_usdt
    return EntryPlan(PositionSide(side), margin_pct, margin, notional, contracts)


def extreme_rotation_entry_plan(config: Account05Config, side: PositionSide,
                                mark_price: Decimal, spec: ContractSpec) -> EntryPlan:
    """Size the independent large-extreme reversal using the slot budget."""
    contracts = config.extreme_rotation_contracts
    notional = contracts * mark_price * spec.ct_val * spec.ct_mult
    margin = notional / config.leverage
    return EntryPlan(PositionSide(side), config.addon_margin_pct, margin, notional,
                     contracts)
