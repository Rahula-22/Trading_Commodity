"""Order lifecycle and emergency-protection state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderState(str, Enum):
    SIGNAL = "SIGNAL"
    RISK_APPROVED = "RISK_APPROVED"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_PENDING = "ENTRY_PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    ENTRY_FILLED = "ENTRY_FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    ENTRY_CANCELLED = "ENTRY_CANCELLED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    EMERGENCY_EXIT_REQUIRED = "EMERGENCY_EXIT_REQUIRED"


TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.SIGNAL: frozenset({OrderState.RISK_APPROVED}),
    OrderState.RISK_APPROVED: frozenset({OrderState.ENTRY_SUBMITTED}),
    OrderState.ENTRY_SUBMITTED: frozenset({
        OrderState.ENTRY_PENDING,
        OrderState.ENTRY_REJECTED,
        OrderState.ENTRY_CANCELLED,
    }),
    OrderState.ENTRY_PENDING: frozenset({
        OrderState.PARTIALLY_FILLED,
        OrderState.ENTRY_FILLED,
        OrderState.ENTRY_REJECTED,
        OrderState.ENTRY_CANCELLED,
        OrderState.BROKER_DISCONNECTED,
    }),
    OrderState.PARTIALLY_FILLED: frozenset({
        OrderState.ENTRY_FILLED,
        OrderState.ENTRY_CANCELLED,
        OrderState.RECONCILIATION_REQUIRED,
    }),
    OrderState.ENTRY_FILLED: frozenset({
        OrderState.PROTECTION_PENDING,
        OrderState.EMERGENCY_EXIT_REQUIRED,
    }),
    OrderState.PROTECTION_PENDING: frozenset({
        OrderState.PROTECTED,
        OrderState.PROTECTION_FAILED,
        OrderState.BROKER_DISCONNECTED,
        OrderState.EMERGENCY_EXIT_REQUIRED,
    }),
    OrderState.PROTECTED: frozenset({
        OrderState.EXIT_REQUESTED,
        OrderState.BROKER_DISCONNECTED,
        OrderState.EMERGENCY_EXIT_REQUIRED,
    }),
    OrderState.EXIT_REQUESTED: frozenset({
        OrderState.EXIT_PENDING,
        OrderState.ENTRY_CANCELLED,
        OrderState.EMERGENCY_EXIT_REQUIRED,
    }),
    OrderState.EXIT_PENDING: frozenset({
        OrderState.CLOSED,
        OrderState.RECONCILIATION_REQUIRED,
        OrderState.EMERGENCY_EXIT_REQUIRED,
    }),
    OrderState.PROTECTION_FAILED: frozenset({
        OrderState.EMERGENCY_EXIT_REQUIRED,
        OrderState.PROTECTION_PENDING,
    }),
    OrderState.BROKER_DISCONNECTED: frozenset({
        OrderState.RECONCILIATION_REQUIRED,
    }),
    OrderState.RECONCILIATION_REQUIRED: frozenset({
        OrderState.CLOSED,
        OrderState.PROTECTED,
        OrderState.EMERGENCY_EXIT_REQUIRED,
    }),
    OrderState.EMERGENCY_EXIT_REQUIRED: frozenset({
        OrderState.EXIT_PENDING,
        OrderState.RECONCILIATION_REQUIRED,
    }),
    OrderState.ENTRY_REJECTED: frozenset(),
    OrderState.ENTRY_CANCELLED: frozenset(),
    OrderState.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class OrderTransition:
    order_id: str
    previous: OrderState
    current: OrderState
    reason: str


class OrderLifecycle:
    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        self.state = OrderState.SIGNAL
        self.history: list[OrderTransition] = []

    def transition(self, new_state: OrderState, reason: str) -> OrderTransition:
        allowed = TRANSITIONS[self.state]
        if new_state not in allowed:
            raise RuntimeError(
                f"invalid order transition: {self.state.value} -> {new_state.value}"
            )
        transition = OrderTransition(
            order_id=self.order_id,
            previous=self.state,
            current=new_state,
            reason=reason,
        )
        self.state = new_state
        self.history.append(transition)
        return transition
