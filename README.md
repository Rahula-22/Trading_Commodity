# MCX Trading Engine

This repository currently contains the paper-trading-safe domain foundation and
Shoonya account-permission adapter for an MCX volatility trading engine. Live
trading is disabled by default.

The current implementation includes:

- Tick and candle structures with bounded in-memory series
- ATR, VWAP, relative volume, and true range calculations
- Deterministic bullish/bearish breakout signals
- Volatility regime classification
- Per-trade and daily-loss risk gates
- Market-data freshness and abnormal-tick validation
- Paper order fills with configurable tick slippage
- Order lifecycle transitions and emergency-protection states
- Shoonya OAuth and MCX permission checks
- Shoonya REST methods for quotes, security info, TPSeries, limits, order book,
  trade book, positions, modify/cancel/status/history, and Scrip search
- Shoonya WebSocket touchline and order-update streaming

## Shoonya permission prerequisite

Shoonya documents MCX as a separately enabled exchange segment. Account-level
permission cannot be inferred from API documentation. Before adding a live
adapter, confirm in the Shoonya account/API response that:

- API access is enabled.
- The MCX segment is enabled.
- The intended MCX product type is enabled.
- The registered static IP is the deployment egress IP.
- OAuth/2FA and any required algo tagging are configured.

The adapter exposes `check_mcx_permission()` using the authenticated
`UserDetails` response. Do not enable live orders until that check succeeds and
Shoonya confirms the intended product and algo configuration.

## Shoonya environment

Required for authenticated checks:

```text
SHOONYA_USER_ID
SHOONYA_CLIENT_ID
SHOONYA_SECRET_CODE
```

`SHOONYA_API_KEY` is not required by the REST adapter. It can be omitted.
`SHOONYA_ACCESS_TOKEN` is created after OAuth login and expires with the
Shoonya session.

Optional:

```text
SHOONYA_ACCOUNT_ID
SHOONYA_LIVE_TRADING=true
```

For local development, copy [.env.example](.env.example) to `.env`, then add
the credentials:

```powershell
Copy-Item .env.example .env
notepad .env
```

The real `.env` is excluded by `.gitignore` and must never be committed.

Never commit these values. `SHOONYA_LIVE_TRADING` must remain unset or false
while developing and paper trading.

## Current risk defaults

- Capital: INR 40,000
- Risk per trade: 2% (INR 800)
- Daily loss limit: 5% (INR 2000)
- Maximum consecutive losses: 3

All monetary calculations use `Decimal`. Contract metadata must be loaded from
the current broker/exchange instrument master; it must not be hardcoded from
third-party lot-size tables.

## Start paper trading

Run a deterministic synthetic replay:

```powershell
python scripts/paper_trade.py --source synthetic
```

Replay a local CSV containing `timestamp/open/high/low/close/volume` or
`time/into/inth/intl/intc/v` columns:

```powershell
python scripts/paper_trade.py --source csv --csv C:\path\to\candles.csv
```

Use current Shoonya intraday TPSeries data:

```powershell
$env:SHOONYA_USER_ID="YOUR_USER_ID"
$env:SHOONYA_ACCESS_TOKEN="YOUR_ACCESS_TOKEN"
python scripts/paper_trade.py --source shoonya --token YOUR_MCX_TOKEN --interval 1
```

Shoonya paper mode does not place an order. It uses read-only historical
data and an internal paper broker. The script must never be used with
`SHOONYA_LIVE_TRADING=true`.

## Capture live Shoonya prices

The WebSocket live feed can be monitored without placing orders. The account
must have Shoonya API access, a valid OAuth access token, and the deployment
IP whitelisted. Resolve the exact active MCX contract before subscribing:

```powershell
python scripts/shoonya_live_feed.py --symbol EXACT_SYMBOL_FROM_SHOONYA
```

Or pass the exact MCX token from the broker's contract master:

```powershell
python scripts/shoonya_live_feed.py --token YOUR_MCX_TOKEN --csv logs\mcx_live_ticks.csv
```

The symbol must exactly match a single active contract returned by Shoonya's
MCX search. The script streams live LTP/bid/ask updates, reconnects and restores its
subscription after a dropped socket, loads current-session candles for
indicator warm-up, and forms one-minute candles from the live feed. It is
read-only: it does not place paper or live orders. Keep new entries disabled
whenever the feed is disconnected or stale; after reconnect, reconcile broker
orders and positions before resuming.

`SHOONYA_LIVE_TRADING=false` keeps the broker adapter's `place_order()` call
blocked. The live-feed script is signal-only and never calls `place_order()`,
even if the setting is changed. This switch controls orders sent by this code;
it does not prevent you from placing orders manually in Shoonya's platform.
The intended workflow is to review a signal and place any real order yourself.

## Test

```powershell
python -m pip install -e ".[test]"
python -m pytest
```
