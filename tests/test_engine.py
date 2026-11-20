from datetime import datetime, timezone
from decimal import Decimal

from trading_engine.broker import Exchange, Instrument
from trading_engine.engine import TradingEngine
from trading_engine.market import Candle
from trading_engine.risk import RiskLimits
from trading_engine.risk_engine import RiskState
from trading_engine.risk_engine import RiskEngine


def test_engine_rejects_when_live_entries_disabled() -> None:
    instrument = Instrument(
        exchange=Exchange.MCX,
        symbol="CRUDEOILM",
        token="T1",
        lot_size=10,
        price_multiplier=Decimal("1"),
        tick_size=Decimal("1"),
        expiry="2099-01-19",
    )
    engine = TradingEngine(instrument, limits=RiskLimits())
    for index in range(55):
        close = Decimal(str(7000 + index))
        engine.append_candle(
            Candle(
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                volume=Decimal("100"),
                timestamp=datetime(2026, 1, 1, 9, 0, index, tzinfo=timezone.utc),
            )
        )
    decision = engine.evaluate(risk_state=RiskState())
    assert decision.order_id is None


def test_paper_mode_approves_when_live_entries_disabled() -> None:
    engine = RiskEngine(
        limits=RiskLimits(),
        execution_mode="paper",
        live_entries_enabled=False,
    )
    approved, reason = engine.approve(RiskState())
    assert approved
    assert reason == "approved"
