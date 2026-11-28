"""Order-flow delta strategy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence

from .market import Candle


ZERO = Decimal("0")


class Direction(str, Enum):
    FLAT = "FLAT"
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class StrategyConfig:
    delta_period: int = 20
    consolidation_period: int = 5
    delta_threshold_ratio: Decimal = Decimal("6")
    minimum_candles: int = 21

    def __post_init__(self) -> None:
        if self.delta_period <= 0:
            raise ValueError("delta_period must be positive")
        if self.consolidation_period <= 0:
            raise ValueError("consolidation_period must be positive")
        if self.consolidation_period >= self.delta_period:
            raise ValueError("consolidation_period must be smaller than delta_period")
        if self.delta_threshold_ratio <= ZERO:
            raise ValueError("delta_threshold_ratio must be positive")
        if self.minimum_candles < self.delta_period + 1:
            raise ValueError("minimum_candles must include the delta period")


@dataclass(frozen=True)
class StrategySignal:
    direction: Direction
    reason: str
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    delta: Decimal = ZERO
    consolidation_ceiling: Decimal = ZERO
    delta_ratio: Decimal = ZERO

    @property
    def average_delta(self) -> Decimal:
        """Compatibility alias: this value is the previous-consolidation ceiling."""
        return self.consolidation_ceiling


def generate_signal(
    candles: Sequence[Candle],
    *,
    config: StrategyConfig = StrategyConfig(),
) -> StrategySignal:
    if len(candles) < config.minimum_candles:
        latest = candles[-1].close if candles else ZERO
        return StrategySignal(
            Direction.FLAT,
            "insufficient candle history",
            latest,
            ZERO,
            ZERO,
        )

    latest = candles[-1]
    previous = candles[-2]
    current_delta = latest.delta
    prior_deltas = candles[-config.consolidation_period - 1:-1]
    consolidation_ceiling = max(
        (abs(candle.delta) for candle in prior_deltas),
        default=ZERO,
    )
    if consolidation_ceiling > ZERO:
        delta_ratio = current_delta / consolidation_ceiling
    elif current_delta != ZERO:
        delta_ratio = abs(current_delta)
    else:
        delta_ratio = ZERO

    threshold = consolidation_ceiling * config.delta_threshold_ratio
    if consolidation_ceiling <= ZERO:
        prior_delta_average = ZERO
    else:
        prior_delta_average = sum(
            (candle.delta for candle in prior_deltas),
            ZERO,
        ) / Decimal(len(prior_deltas))

    if (
        current_delta >= threshold
        and threshold > ZERO
        and latest.close > previous.close
        and latest.close > latest.open
    ):
        stop = latest.low
        risk = latest.close - stop
        if risk > ZERO:
            return StrategySignal(
                Direction.BUY,
                "bullish Delta spike after consolidation",
                latest.close,
                stop,
                latest.close + 2 * risk,
                current_delta,
                consolidation_ceiling,
                delta_ratio,
            )

    if (
        current_delta <= -threshold
        and threshold > ZERO
        and latest.close < previous.close
        and latest.close < latest.open
    ):
        stop = latest.high
        risk = stop - latest.close
        if risk > ZERO:
            return StrategySignal(
                Direction.SELL,
                "bearish Delta spike after consolidation",
                latest.close,
                stop,
                latest.close - 2 * risk,
                current_delta,
                consolidation_ceiling,
                delta_ratio,
            )

    return StrategySignal(
        Direction.FLAT,
        "no confirmed delta imbalance",
        latest.close,
        ZERO,
        ZERO,
        current_delta,
        consolidation_ceiling,
        delta_ratio,
    )
