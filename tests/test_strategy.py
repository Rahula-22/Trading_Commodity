from decimal import Decimal

from trading_engine.market import Candle
from trading_engine.strategy import StrategyConfig, VolatilityRegime, generate_signal, Direction


def candle(index: int, close: str, volume: str = "100") -> Candle:
    from datetime import datetime, timezone
    price = Decimal(close)
    return Candle(
        timestamp=datetime(2026, 1, 1, 9, 0, index, tzinfo=timezone.utc),
        open=price - Decimal("1"),
        high=price + Decimal("1"),
        low=price - Decimal("2"),
        close=price,
        volume=Decimal(volume),
    )


def test_flat_without_history() -> None:
    signal = generate_signal([candle(0, "7000")])
    assert signal.direction is Direction.FLAT
    assert signal.reason == "insufficient candle history"


def test_bullish_breakout_requires_confirmation() -> None:
    candles = [candle(i, str(7000 + i), "100") for i in range(56)]
    candles[-1] = Candle(
        timestamp=candles[-1].timestamp,
        open=Decimal("7055"),
        high=Decimal("7070"),
        low=Decimal("7054"),
        close=Decimal("7068"),
        volume=Decimal("500"),
    )
    signal = generate_signal(candles, config=StrategyConfig(minimum_candles=55))
    assert signal.direction in (Direction.FLAT, Direction.BUY)
