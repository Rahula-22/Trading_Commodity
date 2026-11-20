"""Stream live Shoonya MCX ticks and build one-minute strategy candles.

This is a read-only market-data consumer. It never sends trading orders.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_engine.market import Candle, CandleSeries, TickCandleBuilder, floor_timestamp
from trading_engine.strategy import Direction, generate_signal
from trading_engine.shoonya import ShoonyaBroker, ShoonyaConfig, ShoonyaWebSocket


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture live Shoonya MCX prices and evaluate paper-safe signals"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--token", help="Exact Shoonya MCX instrument token")
    group.add_argument("--symbol", help="Exact Shoonya trading symbol")
    parser.add_argument(
        "--csv", type=Path, help="Optional CSV file for live tick snapshots"
    )
    parser.add_argument(
        "--freshness-seconds", type=int, default=15,
        help="Reject feed timestamps older than this (default: 15)",
    )
    return parser.parse_args()


def resolve_instrument(broker: ShoonyaBroker, token: str | None, symbol: str | None) -> tuple[str, str]:
    if token:
        details = broker.get_security_info(token)
        if details.get("stat") not in (None, "Ok"):
            raise SystemExit(f"Shoonya could not validate MCX token {token}: {details.get('emsg', 'unknown error')}")
        return token, str(details.get("tsym", token)).strip()
    else:
        matches = broker.search_scrip(symbol or "")
        selected = [row for row in matches if str(row.get("tsym", "")).strip() == symbol]
    if len(selected) != 1:
        choices = ", ".join(
            f"{row.get('tsym', '?')} (token {row.get('token', '?')})" for row in matches
        )
        raise SystemExit(
            "Could not resolve exactly one MCX contract. "
            f"Matches: {choices or 'none'}. Pass the exact contract symbol or token."
        )
    row = selected[0]
    resolved_token = str(row.get("token", "")).strip()
    resolved_symbol = str(row.get("tsym", "")).strip()
    if not resolved_token or not resolved_symbol:
        raise SystemExit("Shoonya contract lookup omitted its token or trading symbol")
    return resolved_token, resolved_symbol


def load_session_history(broker: ShoonyaBroker, token: str) -> list[Candle]:
    """Warm indicators with today's completed one-minute Shoonya candles."""
    india = ZoneInfo("Asia/Kolkata")
    session_date: date = datetime.now(india).date()
    end = int(time.time())
    rows = broker.get_time_price_series(
        token=token,
        start_epoch=end - 36 * 60 * 60,
        end_epoch=end,
        interval=1,
    )
    current_bar_start = floor_timestamp(datetime.now(timezone.utc), 1)
    result: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_time = row.get("time") or row.get("timestamp")
        if raw_time is None:
            continue
        try:
            try:
                timestamp = datetime.fromtimestamp(float(raw_time), tz=timezone.utc)
            except (ValueError, TypeError):
                timestamp = datetime.strptime(
                    str(raw_time), "%d-%m-%Y %H:%M:%S"
                ).replace(tzinfo=india).astimezone(timezone.utc)
            if timestamp.astimezone(india).date() != session_date:
                continue
            if timestamp >= current_bar_start:
                continue  # Leave the still-forming minute to the live ticks.
            result.append(Candle(
                timestamp=timestamp,
                open=Decimal(str(row.get("into", row.get("open")))),
                high=Decimal(str(row.get("inth", row.get("high")))),
                low=Decimal(str(row.get("intl", row.get("low")))),
                close=Decimal(str(row.get("intc", row.get("close")))),
                volume=Decimal(str(row.get("intv", row.get("volume", "0")))),
            ))
        except (ValueError, TypeError, InvalidOperation):
            continue
    return sorted(result, key=lambda candle: candle.timestamp)


def main() -> int:
    args = parse_args()
    if args.freshness_seconds <= 0:
        raise SystemExit("--freshness-seconds must be positive")

    config = ShoonyaConfig.for_access_token()
    broker = ShoonyaBroker(config)
    token, symbol = resolve_instrument(broker, args.token, args.symbol)
    print(f"Connecting to Shoonya live feed: MCX {symbol} (token {token})")
    print(
        "Signal-only mode: this script never submits orders. "
        f"SHOONYA_LIVE_TRADING={'true' if config.live_trading else 'false'}; "
        "you review signals and place any real orders manually on Shoonya."
    )

    builder = TickCandleBuilder(minutes=1)
    candles = CandleSeries(maxlen=2000)
    try:
        for candle in load_session_history(broker, token):
            candles.append(candle)
        print(f"Loaded {len(candles.all())} current-session historical candles for indicator warm-up.")
    except Exception as exc:
        # The stream is still useful without history, but strategy signals stay
        # unavailable until enough completed live bars accumulate.
        print(f"Historical warm-up unavailable; collecting live bars only: {exc}", file=sys.stderr)
    state_lock = Lock()
    last_tick_at: datetime | None = None
    output_handle = None
    writer = None
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        output_handle = args.csv.open("a", newline="", encoding="utf-8")
        writer = csv.DictWriter(
            output_handle,
            fieldnames=("received_at", "feed_time", "symbol", "token", "price", "volume", "bid", "ask"),
        )
        if output_handle.tell() == 0:
            writer.writeheader()

    def on_tick(tick: dict[str, Any]) -> None:
        nonlocal last_tick_at
        try:
            price = Decimal(str(tick["lp"]))
            feed_epoch = Decimal(str(tick.get("ft", "")))
            timestamp = datetime.fromtimestamp(float(feed_epoch), tz=timezone.utc)
            volume = Decimal(str(tick["v"])) if tick.get("v") not in (None, "") else None
            if not price.is_finite() or (volume is not None and not volume.is_finite()):
                return
        except (KeyError, InvalidOperation, ValueError, OverflowError):
            print(f"Ignoring malformed Shoonya tick: {tick}", flush=True)
            return

        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if age < -2 or age > args.freshness_seconds:
            print(f"Ignoring stale/future feed tick (age {age:.1f}s)", flush=True)
            return

        with state_lock:
            completed = builder.update(
                timestamp=timestamp,
                price=price,
                cumulative_volume=volume,
            )
            last_tick_at = datetime.now(timezone.utc)
            if completed is not None:
                candles.append(completed)

        received = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(
            f"{received} MCX {symbol} LTP={price} "
            f"bid={tick.get('bp1', '-')} ask={tick.get('sp1', '-')} "
            f"feed_age={age:.1f}s",
            flush=True,
        )
        if writer is not None and output_handle is not None:
            writer.writerow({
                "received_at": received,
                "feed_time": timestamp.isoformat(),
                "symbol": symbol,
                "token": token,
                "price": price,
                "volume": volume if volume is not None else "",
                "bid": tick.get("bp1", ""),
                "ask": tick.get("sp1", ""),
            })
            output_handle.flush()

        if completed is not None:
            with state_lock:
                count = len(candles.all())
                history = candles.all()
            print(
                f"CANDLE {completed.timestamp.isoformat()} "
                f"O={completed.open} H={completed.high} L={completed.low} "
                f"C={completed.close} V={completed.volume} (history={count})",
                flush=True,
            )
            signal = generate_signal(history)
            if signal.direction is not Direction.FLAT:
                print(
                    f"SIGNAL FOR MANUAL REVIEW {signal.direction.value}: {signal.reason}; "
                    f"entry={signal.entry_price} stop={signal.stop_price} "
                    f"target={signal.target_price}",
                    flush=True,
                )

    def on_error(error: Any) -> None:
        print(f"Shoonya feed error: {error}", file=sys.stderr, flush=True)

    def on_close() -> None:
        print("Shoonya WebSocket disconnected; reconnecting with backoff.", flush=True)

    feed = ShoonyaWebSocket(
        config,
        on_tick=on_tick,
        on_error=on_error,
        on_close=on_close,
    )
    feed.subscribe([f"MCX|{token}"])
    monitor_stop = Event()

    def monitor_freshness() -> None:
        stale_reported = False
        while not monitor_stop.wait(5):
            with state_lock:
                last_seen = last_tick_at
            if last_seen is None:
                continue
            age = (datetime.now(timezone.utc) - last_seen).total_seconds()
            if age > args.freshness_seconds and not stale_reported:
                print(
                    f"No fresh Shoonya ticks for {age:.0f}s. Treating market data as stale; "
                    "do not allow entries.",
                    file=sys.stderr,
                    flush=True,
                )
                stale_reported = True
            elif age <= args.freshness_seconds:
                stale_reported = False

    monitor_thread = Thread(target=monitor_freshness, name="feed-freshness", daemon=True)
    monitor_thread.start()
    try:
        feed.connect()
    except KeyboardInterrupt:
        print("Stopping live feed...", flush=True)
    finally:
        monitor_stop.set()
        feed.close()
        if output_handle is not None:
            output_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
