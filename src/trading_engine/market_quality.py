"""Market-data freshness and abnormal-tick validation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .market import Tick, utc_now


def validate_tick(
    tick: Tick,
    *,
    previous_price: Decimal | None = None,
    max_move_ratio: Decimal = Decimal("0.10"),
    freshness: timedelta = timedelta(seconds=15),
    now: object | None = None,
) -> tuple[bool, str]:
    current_time = now if now is not None else utc_now()
    age = current_time - tick.timestamp
    if age < timedelta(0):
        return False, "tick timestamp is in the future"
    if age > freshness:
        return False, "market data is stale"
    if tick.price <= 0:
        return False, "tick price is not positive"
    if previous_price is not None and previous_price > 0:
        ratio = abs(tick.price - previous_price) / previous_price
        if ratio > max_move_ratio:
            return False, "abnormal tick move"
    return True, "valid"
