"""Deterministic pre-trade risk calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from .broker import Instrument, OrderSide


ZERO = Decimal("0")


@dataclass(frozen=True)
class RiskLimits:
    capital: Decimal = Decimal("40000")
    risk_per_trade: Decimal = Decimal("0.02")
    daily_loss_limit: Decimal = Decimal("0.05")
    max_consecutive_losses: int = 3

    def __post_init__(self) -> None:
        if self.capital <= ZERO:
            raise ValueError("capital must be positive")
        if not ZERO < self.risk_per_trade <= 1:
            raise ValueError("risk_per_trade must be in (0, 1]")
        if not ZERO < self.daily_loss_limit <= 1:
            raise ValueError("daily_loss_limit must be in (0, 1]")
        if self.max_consecutive_losses <= 0:
            raise ValueError("max_consecutive_losses must be positive")

    @property
    def max_trade_loss(self) -> Decimal:
        return self.capital * self.risk_per_trade

    @property
    def max_daily_loss(self) -> Decimal:
        return self.capital * self.daily_loss_limit


@dataclass(frozen=True)
class PositionSizing:
    lots: int
    quantity: int
    estimated_loss: Decimal
    rejected_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.rejected_reason is None and self.lots > 0


def calculate_position_size(
    *,
    instrument: Instrument,
    stop_distance: Decimal,
    limits: RiskLimits,
    estimated_costs_per_lot: Decimal = ZERO,
    estimated_slippage_per_lot: Decimal = ZERO,
) -> PositionSizing:
    """Return the largest whole-lot position within the per-trade risk budget."""

    if stop_distance <= ZERO:
        raise ValueError("stop_distance must be positive")
    if estimated_costs_per_lot < ZERO or estimated_slippage_per_lot < ZERO:
        raise ValueError("cost and slippage estimates cannot be negative")

    loss_per_lot = (
        stop_distance * instrument.price_multiplier * instrument.lot_size
        + estimated_costs_per_lot
        + estimated_slippage_per_lot
    )
    if loss_per_lot <= ZERO:
        raise ValueError("loss_per_lot must be positive")

    lots = int((limits.max_trade_loss / loss_per_lot).to_integral_value(rounding=ROUND_DOWN))
    if lots == 0:
        return PositionSizing(
            lots=0,
            quantity=0,
            estimated_loss=ZERO,
            rejected_reason="minimum lot exceeds per-trade risk budget",
        )

    estimated_loss = loss_per_lot * lots
    return PositionSizing(
        lots=lots,
        quantity=lots * instrument.lot_size,
        estimated_loss=estimated_loss,
    )


def validate_stop_direction(
    *, side: OrderSide, entry_price: Decimal, stop_price: Decimal
) -> None:
    """Reject stops placed on the wrong side of the entry."""

    if entry_price <= ZERO or stop_price <= ZERO:
        raise ValueError("entry_price and stop_price must be positive")
    if side is OrderSide.BUY and stop_price >= entry_price:
        raise ValueError("a long stop must be below entry price")
    if side is OrderSide.SELL and stop_price <= entry_price:
        raise ValueError("a short stop must be above entry price")
