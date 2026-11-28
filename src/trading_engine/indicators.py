"""Technical indicators for the deterministic strategy."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from .market import Candle


ZERO = Decimal("0")


def true_range(candle: Candle, previous_close: Decimal | None) -> Decimal:
    if previous_close is None:
        return candle.high - candle.low
    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )


def atr(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    if period <= 0:
        raise ValueError("ATR period must be positive")
    if len(candles) < period:
        return None
    values: list[Decimal] = []
    for index in range(len(candles)):
        previous_close = candles[index - 1].close if index > 0 else None
        values.append(true_range(candles[index], previous_close))
    return sum(values[-period:], ZERO) / Decimal(period)


def vwap(candles: Sequence[Candle]) -> Decimal | None:
    if not candles:
        return None
    numerator = ZERO
    denominator = ZERO
    for candle in candles:
        typical = (candle.high + candle.low + candle.close) / Decimal("3")
        volume = candle.volume
        numerator += typical * volume
        denominator += volume
    if denominator <= ZERO:
        return None
    return numerator / denominator


def relative_volume(candles: Sequence[Candle], period: int = 20) -> Decimal | None:
    if period <= 0:
        raise ValueError("relative-volume period must be positive")
    if len(candles) < period + 1:
        return None
    baseline = sum((c.volume for c in candles[-period - 1:-1]), ZERO) / Decimal(period)
    if baseline <= ZERO:
        return None
    return candles[-1].volume / baseline


def average_delta(candles: Sequence[Candle], period: int = 20) -> Decimal | None:
    """Return the mean signed candle delta over the requested prior candles."""
    if period <= 0:
        raise ValueError("delta period must be positive")
    if len(candles) < period:
        return None
    return sum((candle.delta for candle in candles[-period:]), ZERO) / Decimal(period)


def returns(candle: Candle, previous_close: Decimal) -> Decimal:
    if previous_close <= ZERO:
        raise ValueError("previous_close must be positive")
    return (candle.close - previous_close) / previous_close
