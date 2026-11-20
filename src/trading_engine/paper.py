"""Paper-trading execution separated from live broker adapters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .broker import Instrument, OrderRequest, OrderSide, OrderType


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    fill_price: Decimal
    quantity: int


class PaperBroker:
    """Deterministic fill simulator for backtests and paper trading."""

    def __init__(self, slippage_per_tick: Decimal = Decimal("0")) -> None:
        if slippage_per_tick < 0:
            raise ValueError("slippage_per_tick cannot be negative")
        self.slippage_per_tick = slippage_per_tick
        self._next_id = 1

    def place_order(self, request: OrderRequest) -> PaperFill:
        slip = request.instrument.tick_size * self.slippage_per_tick
        if request.side is OrderSide.BUY:
            fill = request.price + slip
        else:
            fill = request.price - slip
        order_id = f"PAPER-{self._next_id}"
        self._next_id += 1
        return PaperFill(order_id=order_id, fill_price=fill, quantity=request.quantity)
