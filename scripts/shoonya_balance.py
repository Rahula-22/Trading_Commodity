"""Display Shoonya account balance and margin in read-only mode."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_engine.shoonya import ShoonyaBroker, ShoonyaConfig


def decimal_or_zero(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def main() -> int:
    config = ShoonyaConfig.for_access_token()
    broker = ShoonyaBroker(config)
    limits = broker.get_limits()

    cash = decimal_or_zero(limits.get("cash"))
    margin_used = decimal_or_zero(limits.get("marginused"))
    payin = decimal_or_zero(limits.get("payin"))
    payout = decimal_or_zero(limits.get("payout"))
    uncleared_cash = decimal_or_zero(limits.get("unclearedcash"))
    blocked = decimal_or_zero(limits.get("blk_amt"))
    available = cash - margin_used

    print(f"User ID: {config.user_id}")
    print(f"Cash: INR {cash:.2f}")
    print(f"Pay-in: INR {payin:.2f}")
    print(f"Payout: INR {payout:.2f}")
    print(f"Uncleared cash: INR {uncleared_cash:.2f}")
    print(f"Blocked amount: INR {blocked:.2f}")
    print(f"Margin used: INR {margin_used:.2f}")
    print(f"Available margin: INR {available:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
