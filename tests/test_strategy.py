from decimal import Decimal

from trading_engine.market import Candle
from trading_engine.strategy import StrategyConfig, generate_signal, Direction


def candle(index: int, close: str, volume: str = "100", delta: str = "0") -> Candle:
    from datetime import datetime, timezone
    price = Decimal(close)
    return Candle(
        timestamp=datetime(2026, 1, 1, 9, 0, index, tzinfo=timezone.utc),
        open=price - Decimal("1"),
        high=price + Decimal("1"),
        low=price - Decimal("2"),
        close=price,
        volume=Decimal(volume),
        delta=Decimal(delta),
    )


def test_flat_without_history() -> None:
    signal = generate_signal([candle(0, "7000")])
    assert signal.direction is Direction.FLAT
    assert signal.reason == "insufficient candle history"


def test_bullish_delta_entry_uses_signal_candle_low_for_stop() -> None:
    candles = [
        candle(i, str(7000 + i), "100", "100")
        for i in range(55)
    ]
    candles[-1] = Candle(
        timestamp=candles[-1].timestamp,
        open=Decimal("7055"),
        high=Decimal("7060"),
        low=Decimal("7054"),
        close=Decimal("7058"),
        volume=Decimal("500"),
        delta=Decimal("600"),
    )
    signal = generate_signal(
        candles,
        config=StrategyConfig(
            minimum_candles=55,
            delta_period=20,
            delta_threshold_ratio=Decimal("6"),
        ),
    )
    assert signal.direction is Direction.BUY
    assert signal.stop_price == Decimal("7054")
    assert signal.delta == Decimal("600")
    assert signal.average_delta == Decimal("100")


def test_bearish_delta_entry_uses_signal_candle_high_for_stop() -> None:
    candles = [
        candle(i, str(7000 - i), "100", "-100")
        for i in range(55)
    ]
    candles[-1] = Candle(
        timestamp=candles[-1].timestamp,
        open=Decimal("6946"),
        high=Decimal("6947"),
        low=Decimal("6943"),
        close=Decimal("6944"),
        volume=Decimal("500"),
        delta=Decimal("-600"),
    )
    signal = generate_signal(
        candles,
        config=StrategyConfig(
            minimum_candles=55,
            delta_period=20,
            delta_threshold_ratio=Decimal("6"),
        ),
    )
    assert signal.direction is Direction.SELL
    assert signal.stop_price == Decimal("6947")
    assert signal.delta == Decimal("-600")
    assert signal.consolidation_ceiling == Decimal("100")


def test_delta_spike_does_not_depend_on_signed_average() -> None:
    candles = [
        candle(i, str(7000 + i), "100", "100" if i % 2 == 0 else "-100")
        for i in range(55)
    ]
    candles[-1] = Candle(
        timestamp=candles[-1].timestamp,
        open=Decimal("7053"),
        high=Decimal("7060"),
        low=Decimal("7052"),
        close=Decimal("7058"),
        volume=Decimal("600"),
        delta=Decimal("600"),
    )
    signal = generate_signal(
        candles,
        config=StrategyConfig(
            minimum_candles=55,
            consolidation_period=5,
            delta_threshold_ratio=Decimal("6"),
        ),
    )
    assert signal.direction is Direction.BUY


def test_thousand_delta_spike_to_thirteen_thousand_detected() -> None:
    candles = [
        candle(i, str(7720 - i), "100", str(1000 if i % 2 == 0 else -1000))
        for i in range(55)
    ]
    candles[-1] = Candle(
        timestamp=candles[-1].timestamp,
        open=Decimal("7521"),
        high=Decimal("7520"),
        low=Decimal("7510"),
        close=Decimal("7518"),
        volume=Decimal("13000"),
        delta=Decimal("-13000"),
    )
    signal = generate_signal(
        candles,
        config=StrategyConfig(
            minimum_candles=55,
            consolidation_period=5,
            delta_threshold_ratio=Decimal("6"),
        ),
    )
    assert signal.direction is Direction.SELL
    assert signal.delta == Decimal("-13000")
    assert signal.consolidation_ceiling == Decimal("1000")
    assert signal.delta_ratio == Decimal("-13")
