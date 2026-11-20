"""Paper-safe orchestration across market data, strategy, risk, and execution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .broker import Instrument, OrderRequest, OrderSide, OrderType
from .execution import OrderLifecycle, OrderState
from .market import Candle, CandleSeries
from .paper import PaperBroker
from .risk import RiskLimits, calculate_position_size
from .risk_engine import RiskEngine, RiskState
from .strategy import Direction, StrategyConfig, StrategySignal, generate_signal


@dataclass(frozen=True)
class PaperTradeDecision:
    signal: StrategySignal
    quantity: int
    estimated_loss: Decimal
    order_id: str | None
    rejected_reason: str | None = None


class TradingEngine:
    """Coordinates one instrument in paper or future live execution mode."""

    def __init__(
        self,
        instrument: Instrument,
        *,
        limits: RiskLimits,
        strategy_config: StrategyConfig = StrategyConfig(),
        live_entries_enabled: bool = False,
        execution_mode: str = "paper",
        slippage_per_tick: Decimal = Decimal("0"),
    ) -> None:
        self.instrument = instrument
        self.candles = CandleSeries()
        self.strategy_config = strategy_config
        self.risk_engine = RiskEngine(
            limits=limits,
            live_entries_enabled=live_entries_enabled,
            execution_mode=execution_mode,
        )
        self.paper_broker = PaperBroker(slippage_per_tick=slippage_per_tick)

    def append_candle(self, candle: Candle) -> None:
        self.candles.append(candle)

    def evaluate(
        self,
        *,
        risk_state: RiskState,
        costs_per_lot: Decimal = Decimal("0"),
        slippage_per_lot: Decimal = Decimal("0"),
    ) -> PaperTradeDecision:
        signal = generate_signal(self.candles.all(), config=self.strategy_config)
        if signal.direction is Direction.FLAT:
            return PaperTradeDecision(
                signal=signal,
                quantity=0,
                estimated_loss=Decimal("0"),
                order_id=None,
                rejected_reason=signal.reason,
            )

        risk_ok, risk_reason = self.risk_engine.approve(risk_state)
        if not risk_ok:
            return PaperTradeDecision(
                signal=signal,
                quantity=0,
                estimated_loss=Decimal("0"),
                order_id=None,
                rejected_reason=risk_reason,
            )

        stop_distance = abs(signal.entry_price - signal.stop_price)
        sizing = calculate_position_size(
            instrument=self.instrument,
            stop_distance=stop_distance,
            limits=self.risk_engine.limits,
            estimated_costs_per_lot=costs_per_lot,
            estimated_slippage_per_lot=slippage_per_lot,
        )
        if not sizing.accepted:
            return PaperTradeDecision(
                signal=signal,
                quantity=0,
                estimated_loss=Decimal("0"),
                order_id=None,
                rejected_reason=sizing.rejected_reason,
            )

        side = OrderSide.BUY if signal.direction is Direction.BUY else OrderSide.SELL
        order = OrderRequest(
            instrument=self.instrument,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=sizing.quantity,
            price=signal.entry_price,
            client_order_id="",
        )
        fill = self.paper_broker.place_order(order)
        lifecycle = OrderLifecycle(fill.order_id)
        lifecycle.transition(OrderState.RISK_APPROVED, "paper risk accepted")
        lifecycle.transition(OrderState.ENTRY_SUBMITTED, "paper entry submitted")

        return PaperTradeDecision(
            signal=signal,
            quantity=sizing.quantity,
            estimated_loss=sizing.estimated_loss,
            order_id=fill.order_id,
        )
