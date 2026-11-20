from decimal import Decimal

import pytest

from trading_engine.broker import Exchange, Instrument, OrderSide
from trading_engine.risk import RiskLimits, calculate_position_size, validate_stop_direction


@pytest.fixture
def crude_mini() -> Instrument:
    return Instrument(
        exchange=Exchange.MCX,
        symbol="CRUDEOILM",
        token="test-token",
        lot_size=10,
        price_multiplier=Decimal("1"),
        tick_size=Decimal("1"),
        expiry="2099-01-19",
    )


def test_position_size_respects_800_rupee_trade_risk(crude_mini: Instrument) -> None:
    result = calculate_position_size(
        instrument=crude_mini,
        stop_distance=Decimal("10"),
        limits=RiskLimits(),
        estimated_costs_per_lot=Decimal("5"),
        estimated_slippage_per_lot=Decimal("5"),
    )

    assert result.accepted
    assert result.lots == 7
    assert result.quantity == 70
    assert result.estimated_loss == Decimal("770")


def test_rejects_contract_when_minimum_lot_exceeds_budget(crude_mini: Instrument) -> None:
    result = calculate_position_size(
        instrument=crude_mini,
        stop_distance=Decimal("100"),
        limits=RiskLimits(),
    )

    assert not result.accepted
    assert result.rejected_reason == "minimum lot exceeds per-trade risk budget"


def test_stop_direction() -> None:
    validate_stop_direction(
        side=OrderSide.BUY,
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
    )
    validate_stop_direction(
        side=OrderSide.SELL,
        entry_price=Decimal("100"),
        stop_price=Decimal("101"),
    )

    with pytest.raises(ValueError):
        validate_stop_direction(
            side=OrderSide.BUY,
            entry_price=Decimal("100"),
            stop_price=Decimal("101"),
        )
