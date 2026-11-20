from trading_engine.execution import OrderLifecycle, OrderState


def test_valid_entry_and_protection_transitions() -> None:
    lifecycle = OrderLifecycle("O1")
    lifecycle.transition(OrderState.RISK_APPROVED, "risk accepted")
    lifecycle.transition(OrderState.ENTRY_SUBMITTED, "submitted")
    lifecycle.transition(OrderState.ENTRY_PENDING, "pending")
    lifecycle.transition(OrderState.ENTRY_FILLED, "filled")
    lifecycle.transition(OrderState.PROTECTION_PENDING, "place stop")
    lifecycle.transition(OrderState.PROTECTED, "stop confirmed")
    assert lifecycle.state is OrderState.PROTECTED


def test_invalid_transition_is_rejected() -> None:
    lifecycle = OrderLifecycle("O2")
    try:
        lifecycle.transition(OrderState.PROTECTED, "invalid")
    except RuntimeError:
        return
    raise AssertionError("invalid transition should fail")
