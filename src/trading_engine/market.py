"""Deterministic market-data structures and candle aggregation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Iterable


ZERO = Decimal("0")


@dataclass(frozen=True)
class Tick:
    timestamp: datetime
    price: Decimal
    volume: Decimal = ZERO


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = ZERO
    delta: Decimal = ZERO


class TickCandleBuilder:
    """Build completed time bars from ordered ticks with cumulative volume.

    Shoonya's touchline ``v`` field is cumulative traded volume. This class
    converts observed increments into bar volume and never invents volume for
    the first tick or after a session volume reset.
    """

    def __init__(self, minutes: int = 1) -> None:
        if minutes <= 0:
            raise ValueError("minutes must be positive")
        self.minutes = minutes
        self._current: Candle | None = None
        self._last_timestamp: datetime | None = None
        self._last_cumulative_volume: Decimal | None = None
        self._last_trade_price: Decimal | None = None

    def update(
        self,
        *,
        timestamp: datetime,
        price: Decimal,
        cumulative_volume: Decimal | None = None,
    ) -> Candle | None:
        """Add a tick and return the previous candle when a bar closes."""
        if timestamp.tzinfo is None:
            raise ValueError("tick timestamp must be timezone-aware")
        if price <= ZERO:
            raise ValueError("tick price must be positive")
        timestamp = timestamp.astimezone(timezone.utc)
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            return None  # Ignore out-of-order market updates.

        delta = ZERO
        signed_delta = ZERO
        if cumulative_volume is not None and cumulative_volume >= ZERO:
            if (self._last_cumulative_volume is not None
                    and cumulative_volume >= self._last_cumulative_volume):
                delta = cumulative_volume - self._last_cumulative_volume
            self._last_cumulative_volume = cumulative_volume
            if delta > ZERO and self._last_trade_price is not None:
                if price > self._last_trade_price:
                    signed_delta = delta
                elif price < self._last_trade_price:
                    signed_delta = -delta
        self._last_trade_price = price

        bucket = floor_timestamp(timestamp, self.minutes)
        completed: Candle | None = None
        if self._current is None:
            self._current = Candle(bucket, price, price, price, price, delta, signed_delta)
        elif bucket == self._current.timestamp:
            current = self._current
            self._current = Candle(
                current.timestamp,
                current.open,
                max(current.high, price),
                min(current.low, price),
                price,
                current.volume + delta,
                current.delta + signed_delta,
            )
        elif bucket > self._current.timestamp:
            completed = self._current
            self._current = Candle(bucket, price, price, price, price, delta, signed_delta)

        self._last_timestamp = timestamp
        return completed

    @property
    def current(self) -> Candle | None:
        return self._current


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def floor_timestamp(timestamp: datetime, minutes: int) -> datetime:
    timestamp = timestamp.astimezone(timezone.utc)
    epoch_minutes = timestamp.hour * 60 + timestamp.minute
    start_minute = epoch_minutes - (epoch_minutes % minutes)
    return timestamp.replace(
        hour=start_minute // 60,
        minute=start_minute % 60,
        second=0,
        microsecond=0,
    )


def aggregate_ticks(ticks: Iterable[Tick], minutes: int) -> list[Candle]:
    grouped: dict[datetime, list[Tick]] = defaultdict(list)
    for tick in ticks:
        grouped[floor_timestamp(tick.timestamp, minutes)].append(tick)

    candles: list[Candle] = []
    for timestamp in sorted(grouped):
        values = grouped[timestamp]
        signed_delta = ZERO
        previous_price: Decimal | None = None
        for tick in values:
            if previous_price is not None:
                if tick.price > previous_price:
                    signed_delta += tick.volume
                elif tick.price < previous_price:
                    signed_delta -= tick.volume
            previous_price = tick.price
        candles.append(
            Candle(
                timestamp=timestamp,
                open=values[0].price,
                high=max(t.price for t in values),
                low=min(t.price for t in values),
                close=values[-1].price,
                volume=sum((t.volume for t in values), ZERO),
                delta=signed_delta,
            )
        )
    return candles


class CandleSeries:
    """Small bounded series used for live candle calculations."""

    def __init__(self, maxlen: int = 1000) -> None:
        self._candles: deque[Candle] = deque(maxlen=maxlen)

    def append(self, candle: Candle) -> None:
        if self._candles and candle.timestamp < self._candles[-1].timestamp:
            raise ValueError("candles must be appended in chronological order")
        if self._candles and candle.timestamp == self._candles[-1].timestamp:
            self._candles[-1] = candle
        else:
            self._candles.append(candle)

    def last(self, count: int) -> list[Candle]:
        if count <= 0:
            raise ValueError("count must be positive")
        return list(self._candles)[-count:]

    def all(self) -> list[Candle]:
        return list(self._candles)
