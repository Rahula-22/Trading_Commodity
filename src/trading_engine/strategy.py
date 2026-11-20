"""Deterministic volatility-breakout strategy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence

from .indicators import atr, relative_volume, vwap
from .market import Candle


ZERO = Decimal("0")


class Direction(str, Enum):
    FLAT = "FLAT"
    BUY = "BUY"
    SELL = "SELL"


class VolatilityRegime(str, Enum):
    CONTRACTION = "CONTRACTION"
    NORMAL = "NORMAL"
    EXPANSION = "EXPANSION"
    CHAOTIC = "CHAOTIC"


@dataclass(frozen=True)
class StrategyConfig:
    atr_period: int = 14
    atr_baseline_period: int = 50
    relative_volume_period: int = 20
    breakout_lookback: int = 5
    atr_expansion_ratio: Decimal = Decimal("1.25")
    atr_chaotic_ratio: Decimal = Decimal("2.50")
    relative_volume_threshold: Decimal = Decimal("1.20")
    movement_atr_threshold: Decimal = Decimal("0.75")
    minimum_candles: int = 55
    chase_atr_limit: Decimal = Decimal("1.50")

    def __post_init__(self) -> None:
        if self.breakout_lookback <= 0:
            raise ValueError("breakout_lookback must be positive")
        if self.minimum_candles < max(
            self.atr_period,
            self.atr_baseline_period,
            self.relative_volume_period + 1,
        ):
            raise ValueError("minimum_candles is too small for configured indicators")
        if self.atr_expansion_ratio <= ZERO:
            raise ValueError("atr_expansion_ratio must be positive")
        if self.relative_volume_threshold <= ZERO:
            raise ValueError("relative_volume_threshold must be positive")


@dataclass(frozen=True)
class StrategySignal:
    direction: Direction
    reason: str
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    atr: Decimal
    relative_volume: Decimal
    volatility_regime: VolatilityRegime


def volatility_regime(
    candles: Sequence[Candle],
    config: StrategyConfig,
) -> tuple[VolatilityRegime, Decimal, Decimal]:
    current = atr(candles, config.atr_period)
    if current is None or len(candles) < config.atr_baseline_period:
        return VolatilityRegime.NORMAL, ZERO, ZERO

    baseline_candles = candles[-config.atr_baseline_period:]
    values = [
        atr(baseline_candles[: index + 1], config.atr_period)
        for index in range(config.atr_period - 1, len(baseline_candles))
    ]
    valid_values = [value for value in values if value is not None]
    baseline = (
        sum(valid_values, ZERO) / Decimal(len(valid_values))
        if valid_values
        else ZERO
    )
    if baseline <= ZERO:
        return VolatilityRegime.NORMAL, current, ZERO

    ratio = current / baseline
    if ratio >= config.atr_chaotic_ratio:
        return VolatilityRegime.CHAOTIC, current, ratio
    if ratio >= config.atr_expansion_ratio:
        return VolatilityRegime.EXPANSION, current, ratio
    if ratio < Decimal("0.80"):
        return VolatilityRegime.CONTRACTION, current, ratio
    return VolatilityRegime.NORMAL, current, ratio


def generate_signal(
    candles: Sequence[Candle],
    *,
    config: StrategyConfig = StrategyConfig(),
) -> StrategySignal:
    if len(candles) < config.minimum_candles:
        return StrategySignal(
            Direction.FLAT,
            "insufficient candle history",
            candles[-1].close if candles else ZERO,
            ZERO,
            ZERO,
            ZERO,
            ZERO,
            VolatilityRegime.NORMAL,
        )

    latest = candles[-1]
    previous = candles[-2]
    current_atr, atr_ratio = (
        volatility_regime(candles, config)[1],
        volatility_regime(candles, config)[2],
    )
    regime = volatility_regime(candles, config)[0]
    rv = relative_volume(candles, config.relative_volume_period)

    if regime in (VolatilityRegime.CHAOTIC, VolatilityRegime.CONTRACTION):
        return StrategySignal(
            Direction.FLAT,
            f"volatility regime is {regime.value.lower()}",
            latest.close,
            ZERO,
            ZERO,
            current_atr,
            rv or ZERO,
            regime,
        )
    if current_atr <= ZERO or rv is None:
        return StrategySignal(
            Direction.FLAT,
            "indicators unavailable",
            latest.close,
            ZERO,
            ZERO,
            ZERO,
            rv or ZERO,
            regime,
        )

    vw = vwap(candles)
    swing_high = max(c.high for c in candles[-config.breakout_lookback - 1:-1])
    swing_low = min(c.low for c in candles[-config.breakout_lookback - 1:-1])
    movement = abs(latest.close - previous.close)

    if (
        latest.close > swing_high
        and latest.close > previous.close
        and (vw is None or latest.close > vw)
        and rv >= config.relative_volume_threshold
        and movement >= current_atr * config.movement_atr_threshold
        and movement <= current_atr * config.chase_atr_limit
    ):
        stop = latest.close - current_atr
        target = latest.close + 2 * (latest.close - stop)
        return StrategySignal(
            Direction.BUY,
            "bullish breakout confirmed by momentum, volume, VWAP, and ATR",
            latest.close,
            stop,
            target,
            current_atr,
            rv,
            regime,
        )

    if (
        latest.close < swing_low
        and latest.close < previous.close
        and (vw is None or latest.close < vw)
        and rv >= config.relative_volume_threshold
        and movement >= current_atr * config.movement_atr_threshold
        and movement <= current_atr * config.chase_atr_limit
    ):
        stop = latest.close + current_atr
        target = latest.close - 2 * (stop - latest.close)
        return StrategySignal(
            Direction.SELL,
            "bearish breakdown confirmed by momentum, volume, VWAP, and ATR",
            latest.close,
            stop,
            target,
            current_atr,
            rv,
            regime,
        )

    return StrategySignal(
        Direction.FLAT,
        "no confirmed breakout",
        latest.close,
        ZERO,
        ZERO,
        current_atr,
        rv,
        regime,
    )
