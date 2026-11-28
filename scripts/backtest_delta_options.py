"""Backtest the delta strategy on Shoonya MCX option history.

This script is read-only and never submits an order.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_engine.market import Candle
from trading_engine.shoonya import ShoonyaApiError, ShoonyaBroker, ShoonyaConfig
from trading_engine.strategy import Direction, StrategyConfig, generate_signal


def parse_epoch(row: dict[str, object]) -> datetime:
    for name in ("ssboe", "time"):
        value = row.get(name)
        if value in (None, ""):
            continue
        try:
            if name == "ssboe":
                return datetime.fromtimestamp(float(str(value)), tz=timezone.utc)
            return datetime.strptime(str(value), "%d-%m-%Y %H:%M:%S").replace(
                tzinfo=ZoneInfo("Asia/Kolkata")
            ).astimezone(timezone.utc)
        except (ValueError, OverflowError):
            continue
    raise ValueError("row omitted a usable timestamp")


def decimal(row: dict[str, object], names: tuple[str, ...]) -> Decimal:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return Decimal(str(value))
    return Decimal("0")


def candles(rows: list[dict[str, object]]) -> list[Candle]:
    result: list[Candle] = []
    previous_close: Decimal | None = None
    for row in sorted(rows, key=parse_epoch):
        close = decimal(row, ("intc", "close", "c"))
        volume = decimal(row, ("intv", "v", "volume"))
        signed_delta = Decimal("0")
        if previous_close is not None:
            if close > previous_close:
                signed_delta = volume
            elif close < previous_close:
                signed_delta = -volume
        result.append(
            Candle(
                timestamp=parse_epoch(row),
                open=decimal(row, ("into", "open", "o")),
                high=decimal(row, ("inth", "high", "h")),
                low=decimal(row, ("intl", "low", "l")),
                close=close,
                volume=volume,
                delta=signed_delta,
            )
        )
        previous_close = close
    return result


def session_date(candle: Candle) -> str:
    return candle.timestamp.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--futures-token", default="556")
    parser.add_argument("--put-token", default="592012")
    parser.add_argument("--call-token", default="592008")
    parser.add_argument("--delta-ratio", type=Decimal, default=Decimal("6"))
    args = parser.parse_args()

    config = ShoonyaConfig.for_access_token()
    broker = ShoonyaBroker(config)
    end = int(datetime.now(timezone.utc).timestamp())
    start = end - args.days * 36 * 60 * 60

    try:
        future_rows = broker.get_time_price_series(
            token=args.futures_token,
            start_epoch=start,
            end_epoch=end,
            interval=1,
        )
    except ShoonyaApiError as exc:
        raise SystemExit(f"Could not retrieve futures history: {exc}")

    futures = candles(future_rows)
    sessions: dict[str, list[Candle]] = {}
    for candle in futures:
        sessions.setdefault(session_date(candle), []).append(candle)

    put_rows: list[dict[str, object]] = []
    call_rows: list[dict[str, object]] = []
    for token, target in ((args.put_token, put_rows), (args.call_token, call_rows)):
        try:
            target.extend(
                broker.get_time_price_series(
                    token=token,
                    start_epoch=start,
                    end_epoch=end,
                    interval=1,
                )
            )
        except ShoonyaApiError:
            pass

    puts = {candle.timestamp: candle for candle in candles(put_rows)}
    calls = {candle.timestamp: candle for candle in candles(call_rows)}

    trades: list[dict[str, object]] = []
    strategy_config = StrategyConfig(delta_threshold_ratio=args.delta_ratio)
    for date, bars in sorted(sessions.items()):
        if len(bars) < 55:
            continue
        direction: Direction | None = None
        entry_underlying: Decimal = Decimal("0")
        entry_option: Decimal = Decimal("0")
        stop: Decimal = Decimal("0")
        target: Decimal = Decimal("0")
        option_token: str | None = None
        option_bars: dict[datetime, Candle] = {}

        for index in range(55, len(bars)):
            history = bars[: index + 1]
            latest = bars[index]
            signal = generate_signal(history, config=strategy_config)

            if direction is None:
                if signal.direction is Direction.BUY:
                    direction = Direction.BUY
                    option_token = args.call_token
                    option_bars = calls
                    stop = signal.stop_price
                    target = signal.target_price
                elif signal.direction is Direction.SELL:
                    direction = Direction.SELL
                    option_token = args.put_token
                    option_bars = puts
                    stop = signal.stop_price
                    target = signal.target_price
                else:
                    continue

                entry_underlying = latest.close
                entry_candle = option_bars.get(latest.timestamp)
                if entry_candle is None:
                    direction = None
                    continue
                entry_option = entry_candle.close
                continue

            exited = False
            exit_reason = ""
            if direction is Direction.BUY:
                if latest.close <= stop:
                    exit_reason = "underlying stop"
                    exited = True
                elif latest.close >= target:
                    exit_reason = "underlying target"
                    exited = True
                elif latest.delta <= Decimal("0"):
                    exit_reason = "delta weak/reversed"
                    exited = True
            else:
                if latest.close >= stop:
                    exit_reason = "underlying stop"
                    exited = True
                elif latest.close <= target:
                    exit_reason = "underlying target"
                    exited = True
                elif latest.delta >= Decimal("0"):
                    exit_reason = "delta weak/reversed"
                    exited = True

            if exited:
                option_candle = option_bars.get(latest.timestamp)
                if option_candle is not None:
                    exit_option = option_candle.close
                    if direction is Direction.BUY:
                        points = exit_option - entry_option
                    else:
                        points = entry_option - exit_option
                    profit = points * Decimal("10")
                    trades.append(
                        {
                            "date": date,
                            "direction": direction.value,
                            "option_token": option_token,
                            "entry_underlying": entry_underlying,
                            "exit_underlying": latest.close,
                            "entry_option": entry_option,
                            "exit_option": exit_option,
                            "points": points,
                            "profit_inr": profit,
                            "exit_reason": exit_reason,
                        }
                    )
                direction = None
                continue

        if direction is not None:
            latest = bars[-1]
            option_candle = option_bars.get(latest.timestamp)
            if option_candle is not None:
                exit_option = option_candle.close
                if direction is Direction.BUY:
                    points = exit_option - entry_option
                else:
                    points = entry_option - exit_option
                profit = points * Decimal("10")
                trades.append(
                    {
                        "date": date,
                        "direction": direction.value,
                        "option_token": option_token,
                        "entry_underlying": entry_underlying,
                        "exit_underlying": latest.close,
                        "entry_option": entry_option,
                        "exit_option": exit_option,
                        "points": points,
                        "profit_inr": profit,
                        "exit_reason": "end of session",
                    }
                )

    print(f"Available trading sessions: {', '.join(sorted(sessions))}")
    print(f"Put option bars: {len(puts)}")
    print(f"Call option bars: {len(calls)}")
    print(f"Option trades: {len(trades)}")
    total = sum((Decimal(str(trade["profit_inr"])) for trade in trades), Decimal("0"))
    for trade in trades:
        print(
            f"{trade['date']} {trade['direction']} "
            f"option={trade['option_token']} "
            f"premium {trade['entry_option']}->{trade['exit_option']} "
            f"points={trade['points']} P&L=INR {trade['profit_inr']} "
            f"reason={trade['exit_reason']}"
        )
    print(f"Net option P&L: INR {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
