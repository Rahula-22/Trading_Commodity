"""Independent risk gates enforced before live or paper entry."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .risk import RiskLimits


@dataclass(frozen=True)
class RiskState:
    realized_pnl: Decimal = Decimal("0")
    consecutive_losses: int = 0
    trades_today: int = 0
    open_positions: int = 0


class RiskEngine:
    def __init__(
        self,
        *,
        limits: RiskLimits,
        max_trades_today: int = 10,
        max_open_positions: int = 3,
        live_entries_enabled: bool = False,
        execution_mode: str = "paper",
    ) -> None:
        self.limits = limits
        self.max_trades_today = max_trades_today
        self.max_open_positions = max_open_positions
        self.live_entries_enabled = live_entries_enabled
        if execution_mode not in {"paper", "live"}:
            raise ValueError("execution_mode must be 'paper' or 'live'")
        self.execution_mode = execution_mode
        self.kill_switch = False

    def approve(self, state: RiskState) -> tuple[bool, str]:
        if self.kill_switch:
            return False, "kill switch is active"
        if self.execution_mode == "live" and not self.live_entries_enabled:
            return False, "live entries are disabled"
        if state.realized_pnl <= -self.limits.max_daily_loss:
            return False, "daily loss limit reached"
        if state.consecutive_losses >= self.limits.max_consecutive_losses:
            return False, "maximum consecutive losses reached"
        if state.trades_today >= self.max_trades_today:
            return False, "maximum trades for today reached"
        if state.open_positions >= self.max_open_positions:
            return False, "maximum open positions reached"
        return True, "approved"
