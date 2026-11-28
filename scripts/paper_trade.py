"""Run paper-trading strategy replay from CSV or Shoonya TPSeries data.

This script never places broker orders.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import time
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_engine.broker import Exchange, Instrument
from trading_engine.engine import TradingEngine
from trading_engine.market import Candle
from trading_engine.risk import RiskLimits
from trading_engine.risk_engine import RiskState
from trading_engine.shoonya import ShoonyaConfig, ShoonyaBroker


def parse_epoch(value: object) -> datetime:
    if isinstance(value, (int, float, str)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raise ValueError("unsupported candle timestamp")


def decimal_value(row: dict[str, object], names: tuple[str, ...]) -> Decimal:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return Decimal(str(value))
    raise ValueError(f"missing candle field; expected one of {names}")


def optional_decimal_value(row: dict[str, object], names: tuple[str, ...]) -> Decimal:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return Decimal(str(value))
    return Decimal("0")


def timestamp_value(row: dict[str, object]) -> datetime:
    for name in ("timestamp", "time", "datetime", "date"):
        if name in row and row[name] not in (None, ""):
            value = row[name]
            if isinstance(value, (int, float)):
                return parse_epoch(value)
            if isinstance(value, str):
                try:
                    return parse_epoch(float(value))
                except ValueError:
                    for date_format in (
                        "%d-%m-%Y %H:%M:%S",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S",
                    ):
                        try:
                            return datetime.strptime(
                                value.replace("Z", ""), date_format
                            ).replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                        timezone.utc
                    )
    raise ValueError("missing candle timestamp")


def rows_from_csv(path: Path) -> list[Candle]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        result: list[Candle] = []
        for row in rows:
            result.append(
                Candle(
                    timestamp=timestamp_value(row),
                    open=decimal_value(row, ("open", "o", "into")),
                    high=decimal_value(row, ("high", "h", "inth")),
                    low=decimal_value(row, ("low", "l", "intl")),
                    close=decimal_value(row, ("close", "c", "intc")),
                    volume=decimal_value(row, ("volume", "v", "vol")),
                    delta=optional_decimal_value(row, ("delta", "net_delta", "signed_volume")),
                )
            )
        return result


def shoonya_candles(token: str, interval: int) -> list[Candle]:
    config = ShoonyaConfig.for_access_token()
    broker = ShoonyaBroker(config)
    end = int(time.time())
    start = end - (24 * 60 * 60)
    rows = broker.get_time_price_series(
        token=token,
        start_epoch=start,
        end_epoch=end,
        interval=interval,
    )
    result: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            Candle(
                timestamp=timestamp_value(row),
                open=decimal_value(row, ("open", "o", "into")),
                high=decimal_value(row, ("high", "h", "inth")),
                low=decimal_value(row, ("low", "l", "intl")),
                close=decimal_value(row, ("close", "c", "intc")),
                volume=decimal_value(row, ("volume", "v", "vol")),
                delta=optional_decimal_value(row, ("delta", "net_delta", "signed_volume")),
            )
        )
    return sorted(result, key=lambda candle: candle.timestamp)


def synthetic_candles(count: int = 180) -> list[Candle]:
    base = Decimal("7000")
    result: list[Candle] = []
    for index in range(count):
        if index < 120:
            close = base + Decimal(index // 4)
        else:
            close = base + Decimal(30) + Decimal((index - 120) * 2)
        volume = Decimal("100" if index < 120 else "500")
        result.append(
            Candle(
                timestamp=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
                + timedelta(minutes=index),
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                volume=volume,
            )
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the engine in paper-trading mode")
    parser.add_argument("--source", choices=("synthetic", "csv", "shoonya"), default="synthetic")
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--token", help="Shoonya MCX instrument token")
    parser.add_argument("--symbol", help="MCX symbol text to resolve through SearchScrip")
    parser.add_argument("--interval", type=int, choices=(1, 3, 5, 10, 15, 30, 60), default=1)
    parser.add_argument("--lot-size", type=int, default=10)
    parser.add_argument("--multiplier", type=Decimal, default=Decimal("1"))
    parser.add_argument("--tick-size", type=Decimal, default=Decimal("1"))
    args = parser.parse_args()

    if args.source == "csv":
        if not args.csv:
            parser.error("--csv is required with --source csv")
        candles = rows_from_csv(args.csv)
    elif args.source == "shoonya":
        if args.token:
            resolved_token = args.token
        elif args.symbol:
            config = ShoonyaConfig.for_access_token()
            matches = ShoonyaBroker(config).search_scrip(args.symbol)
            if not matches:
                parser.error(f"no Shoonya MCX contract matched: {args.symbol}")
            print("Matches:")
            for match in matches:
                print(match)
            resolved_token = str(matches[0].get("token", "")).strip()
            if not resolved_token:
                parser.error("matched contract did not contain a token")
        else:
            parser.error("--token or --symbol is required with --source shoonya")
        candles = shoonya_candles(resolved_token, args.interval)
    else:
        candles = synthetic_candles()

    instrument = Instrument(
        exchange=Exchange.MCX,
        symbol="PAPER-CONTRACT",
        token=args.token or "PAPER",
        lot_size=args.lot_size,
        price_multiplier=args.multiplier,
        tick_size=args.tick_size,
        expiry="PAPER",
    )
    engine = TradingEngine(
        instrument,
        limits=RiskLimits(),
        execution_mode="paper",
        live_entries_enabled=False,
        slippage_per_tick=Decimal("1"),
    )
    for candle in candles:
        engine.append_candle(candle)
        decision = engine.evaluate(risk_state=RiskState())
        if decision.order_id:
            print(
                f"{candle.timestamp.isoformat()} "
                f"{decision.signal.direction.value} "
                f"entry={decision.signal.entry_price} "
                f"stop={decision.signal.stop_price} "
                f"quantity={decision.quantity} "
                f"paper_order={decision.order_id}"
            )
    print(f"Processed {len(candles)} candles in paper mode; no broker orders were placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
