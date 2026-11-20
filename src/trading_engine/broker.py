"""Broker-neutral interfaces used by strategy and execution components."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence


class Exchange(str, Enum):
    MCX = "MCX"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LMT"
    STOP_LIMIT = "SL-LMT"


@dataclass(frozen=True)
class Instrument:
    """Tradable contract metadata sourced from the broker/exchange."""

    exchange: Exchange
    symbol: str
    token: str
    lot_size: int
    price_multiplier: Decimal
    tick_size: Decimal
    expiry: str

    def __post_init__(self) -> None:
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if self.price_multiplier <= 0:
            raise ValueError("price_multiplier must be positive")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if not self.symbol or not self.token or not self.expiry:
            raise ValueError("symbol, token, and expiry are required")


@dataclass(frozen=True)
class OrderRequest:
    """Validated order intent; adapters translate it to broker payloads."""

    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Decimal
    trigger_price: Decimal | None = None
    client_order_id: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.quantity % self.instrument.lot_size != 0:
            raise ValueError("quantity must be a positive multiple of lot_size")
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.order_type is OrderType.STOP_LIMIT:
            if self.trigger_price is None or self.trigger_price <= 0:
                raise ValueError("stop-limit orders require a positive trigger_price")
        elif self.trigger_price is not None:
            raise ValueError("limit orders must not have a trigger_price")


class BrokerInterface(Protocol):
    """Minimal interface required by execution code."""

    def get_instruments(self, exchange: Exchange) -> Sequence[Instrument]:
        ...

    def get_available_margin(self) -> Decimal:
        ...

    def place_order(self, request: OrderRequest) -> str:
        ...
