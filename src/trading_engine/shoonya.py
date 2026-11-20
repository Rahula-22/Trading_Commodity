"""Shoonya OAuth and account-permission integration.

This module intentionally does not authenticate or place orders on import.
Live order placement requires an explicit runtime opt-in.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Iterable
from threading import Event, Lock
from time import monotonic, sleep
import random

from dotenv import load_dotenv

from .broker import Exchange, Instrument, OrderRequest, OrderType


class ShoonyaApiError(RuntimeError):
    """Raised when Shoonya returns an API-level error."""


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShoonyaConfig:
    user_id: str
    client_id: str
    secret_code: str
    api_key: str = ""
    access_token: str | None = None
    account_id: str | None = None
    api_base: str = "https://api.shoonya.com/NorenWClientAPI/"
    oauth_base: str = "https://api.shoonya.com/OAuthlogin/authorize/oauth"
    live_trading: bool = False

    @classmethod
    def from_environment(cls) -> "ShoonyaConfig":
        load_dotenv()

        def required(name: str) -> str:
            value = os.getenv(name)
            if not value:
                raise ValueError(f"missing required environment variable: {name}")
            return value

        return cls(
            user_id=required("SHOONYA_USER_ID"),
            client_id=required("SHOONYA_CLIENT_ID"),
            secret_code=required("SHOONYA_SECRET_CODE"),
            access_token=os.getenv("SHOONYA_ACCESS_TOKEN"),
            account_id=os.getenv("SHOONYA_ACCOUNT_ID"),
            live_trading=os.getenv("SHOONYA_LIVE_TRADING", "").lower() == "true",
        )

    @classmethod
    def for_access_token(cls) -> "ShoonyaConfig":
        load_dotenv()
        user_id = os.getenv("SHOONYA_USER_ID")
        access_token = os.getenv("SHOONYA_ACCESS_TOKEN")
        if not user_id:
            raise ValueError("missing required environment variable: SHOONYA_USER_ID")
        if not access_token:
            raise ValueError("missing required environment variable: SHOONYA_ACCESS_TOKEN")
        return cls(
            user_id=user_id,
            client_id=os.getenv("SHOONYA_CLIENT_ID", ""),
            secret_code=os.getenv("SHOONYA_SECRET_CODE", ""),
            api_key=os.getenv("SHOONYA_API_KEY", ""),
            access_token=access_token,
            account_id=os.getenv("SHOONYA_ACCOUNT_ID"),
        )

    def authorization_url(self) -> str:
        query = urllib.parse.urlencode({"client_id": self.client_id})
        return f"{self.oauth_base}?{query}"

    def checksum(self, auth_code: str) -> str:
        if not auth_code:
            raise ValueError("auth_code is required")
        return hashlib.sha256(
            f"{self.client_id}{self.secret_code}{auth_code}".encode("utf-8")
        ).hexdigest()


HttpPost = Callable[[str, bytes, dict[str, str]], Any]


def _default_http_post(url: str, body: bytes, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ShoonyaApiError(f"HTTP {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Do not blindly retry POST requests: an order submission can have
        # reached the broker even when the client timed out.
        raise ShoonyaApiError(f"Shoonya request failed: {exc}") from exc


class TokenBucket:
    """Conservative client-side order-rate limiter."""

    def __init__(self, requests_per_second: float = 8.0, burst: float = 8.0) -> None:
        if requests_per_second <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self.rate = requests_per_second
        self.capacity = burst
        self.tokens = burst
        self.updated = monotonic()

    def acquire(self) -> None:
        now = monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens < 1:
            sleep((1 - self.tokens) / self.rate)
            self.tokens = 0
        else:
            self.tokens -= 1


class ShoonyaBroker:
    """Small adapter around documented Shoonya REST operations."""

    def __init__(
        self,
        config: ShoonyaConfig,
        *,
        http_post: HttpPost = _default_http_post,
    ) -> None:
        self.config = config
        self._http_post = http_post
        self._order_limiter = TokenBucket()

    def exchange_access_token(self, auth_code: str) -> str:
        payload = {
            "code": auth_code,
            "checksum": self.config.checksum(auth_code),
        }
        response = self._post("GenAcsTok", payload, authenticated=False)
        token = response.get("susertoken") or response.get("access_token")
        if not isinstance(token, str) or not token:
            raise ShoonyaApiError("access token missing from successful response")
        return token

    def get_user_details(self) -> dict[str, Any]:
        response = self._post(
            "UserDetails",
            {
                "uid": self.config.user_id,
                "actid": self.config.account_id or self.config.user_id,
            },
        )
        if not isinstance(response, dict):
            raise ShoonyaApiError("UserDetails returned an invalid response")
        return response

    def check_mcx_permission(self) -> bool:
        """Return whether the authenticated account advertises MCX access.

        Shoonya account responses have varied field names across API versions,
        so this checks the documented exchange/product collections without
        assuming a single response shape.
        """

        details = self.get_user_details()
        if details.get("stat") != "Ok":
            raise ShoonyaApiError(str(details.get("emsg", "UserDetails failed")))
        exchanges = details.get("exarr", details.get("exchanges", []))
        if isinstance(exchanges, list):
            for item in exchanges:
                if isinstance(item, str) and item.upper() == Exchange.MCX.value:
                    return True
                if isinstance(item, dict) and str(
                    item.get("exch", item.get("exchange", ""))
                ).upper() == Exchange.MCX.value:
                    return True
        return False

    def place_order(self, request: OrderRequest) -> str:
        if not self.config.live_trading:
            raise ShoonyaApiError(
                "live trading is disabled; set SHOONYA_LIVE_TRADING=true explicitly"
            )
        if request.instrument.exchange is not Exchange.MCX:
            raise ValueError("this adapter is currently restricted to MCX")

        payload: dict[str, Any] = {
            "uid": self.config.user_id,
            "actid": self.config.account_id or self.config.user_id,
            "exch": request.instrument.exchange.value,
            "tsym": request.instrument.symbol,
            "qty": str(request.quantity),
            "dscqty": "0",
            "prc": str(request.price),
            "prd": "I",
            "trantype": "B" if request.side.value == "BUY" else "S",
            "prctyp": request.order_type.value,
            "ret": "DAY",
            "ordersource": "API",
            "remarks": request.client_order_id,
        }
        if request.order_type is OrderType.STOP_LIMIT:
            payload["trgprc"] = str(request.trigger_price)
        self._order_limiter.acquire()
        response = self._post("PlaceOrder", payload)
        if not isinstance(response, dict):
            raise ShoonyaApiError("PlaceOrder returned an invalid response")
        order_id = response.get("norenordno")
        if response.get("stat") != "Ok" or not isinstance(order_id, str):
            raise ShoonyaApiError(str(response.get("emsg", "order rejected")))
        return order_id

    def modify_order(
        self,
        *,
        order_id: str,
        instrument: Instrument,
        quantity: int,
        price: Decimal,
        order_type: OrderType,
        trigger_price: Decimal | None = None,
    ) -> str:
        if not self.config.live_trading:
            raise ShoonyaApiError("live trading is disabled")
        if not order_id:
            raise ValueError("order_id is required")
        payload: dict[str, Any] = {
            "uid": self.config.user_id,
            "norenordno": order_id,
            "exch": instrument.exchange.value,
            "tsym": instrument.symbol,
            "qty": str(quantity),
            "prc": str(price),
            "prctyp": order_type.value,
            "ret": "DAY",
            "ordersource": "API",
        }
        if order_type is OrderType.STOP_LIMIT:
            if trigger_price is None:
                raise ValueError("trigger_price is required for SL-LMT")
            payload["trgprc"] = str(trigger_price)
        self._order_limiter.acquire()
        response = self._post("ModifyOrder", payload)
        if not isinstance(response, dict):
            raise ShoonyaApiError("ModifyOrder returned an invalid response")
        if response.get("stat") != "Ok":
            raise ShoonyaApiError(str(response.get("emsg", "order modification rejected")))
        return str(response.get("result", order_id))

    def cancel_order(self, order_id: str) -> str:
        if not self.config.live_trading:
            raise ShoonyaApiError("live trading is disabled")
        if not order_id:
            raise ValueError("order_id is required")
        self._order_limiter.acquire()
        response = self._post(
            "CancelOrder",
            {
                "uid": self.config.user_id,
                "norenordno": order_id,
                "ordersource": "API",
            },
        )
        if not isinstance(response, dict) or response.get("stat") != "Ok":
            raise ShoonyaApiError(
                str(response.get("emsg", "order cancellation rejected"))
                if isinstance(response, dict)
                else "CancelOrder returned an invalid response"
            )
        return str(response.get("result", order_id))

    def get_order_book(self) -> list[dict[str, Any]]:
        response = self._post("OrderBook", {"uid": self.config.user_id})
        if not isinstance(response, list):
            raise ShoonyaApiError("OrderBook returned an invalid response")
        return response

    def get_trade_book(self) -> list[dict[str, Any]]:
        response = self._post(
            "TradeBook",
            {
                "uid": self.config.user_id,
                "actid": self.config.account_id or self.config.user_id,
            },
        )
        if not isinstance(response, list):
            raise ShoonyaApiError("TradeBook returned an invalid response")
        return response

    def get_positions(self) -> list[dict[str, Any]]:
        response = self._post(
            "PositionBook",
            {
                "uid": self.config.user_id,
                "actid": self.config.account_id or self.config.user_id,
            },
        )
        if not isinstance(response, list):
            raise ShoonyaApiError("PositionBook returned an invalid response")
        return response

    def get_limits(self) -> dict[str, Any]:
        response = self._post(
            "Limits",
            {
                "uid": self.config.user_id,
                "actid": self.config.account_id or self.config.user_id,
            },
        )
        if not isinstance(response, dict) or response.get("stat") not in (None, "Ok"):
            raise ShoonyaApiError(str(response.get("emsg", "Limits failed")))
        return response

    def get_available_margin(self) -> Decimal:
        limits = self.get_limits()
        try:
            cash = Decimal(str(limits.get("cash", "0")))
            margin_used = Decimal(str(limits.get("marginused", "0")))
        except Exception as exc:
            raise ShoonyaApiError("invalid margin values") from exc
        return cash - margin_used

    def get_order_status(self, order_id: str) -> list[dict[str, Any]]:
        if not order_id:
            raise ValueError("order_id is required")
        response = self._post(
            "SingleOrdStatus",
            {"uid": self.config.user_id, "norenordno": order_id},
        )
        if not isinstance(response, list):
            raise ShoonyaApiError("SingleOrdStatus returned an invalid response")
        return response

    def get_order_history(self, order_id: str) -> list[dict[str, Any]]:
        if not order_id:
            raise ValueError("order_id is required")
        response = self._post(
            "SingleOrdHist",
            {"uid": self.config.user_id, "norenordno": order_id},
        )
        if not isinstance(response, list):
            raise ShoonyaApiError("SingleOrdHist returned an invalid response")
        return response

    def search_scrip(self, text: str) -> list[dict[str, Any]]:
        if not text:
            raise ValueError("text is required")
        response = self._post(
            "SearchScrip",
            {"uid": self.config.user_id, "exch": "MCX", "stext": text},
        )
        if isinstance(response, list):
            return response
        if isinstance(response, dict) and isinstance(response.get("values"), list):
            return response["values"]
        if isinstance(response, dict) and response.get("stat") == "Not_Ok":
            return []
        if isinstance(response, dict):
            raise ShoonyaApiError(
                f"SearchScrip failed: stat={response.get('stat')}, "
                f"emsg={response.get('emsg') or 'unknown error'}"
            )
        raise ShoonyaApiError("SearchScrip returned an invalid response")

    def get_security_info(self, token: str) -> dict[str, Any]:
        if not token:
            raise ValueError("token is required")
        response = self._post(
            "GetSecurityInfo",
            {"uid": self.config.user_id, "exch": "MCX", "token": token},
        )
        if not isinstance(response, dict):
            raise ShoonyaApiError("GetSecurityInfo returned an invalid response")
        return response

    def get_quotes(self, token: str) -> dict[str, Any]:
        if not token:
            raise ValueError("token is required")
        response = self._post(
            "GetQuotes",
            {"uid": self.config.user_id, "exch": "MCX", "token": token},
        )
        if not isinstance(response, dict):
            raise ShoonyaApiError("GetQuotes returned an invalid response")
        return response

    def get_time_price_series(
        self,
        *,
        token: str,
        start_epoch: int,
        end_epoch: int,
        interval: int = 1,
    ) -> list[dict[str, Any]]:
        if interval not in {1, 3, 5, 10, 15, 30, 60, 120, 240}:
            raise ValueError("unsupported candle interval")
        if start_epoch >= end_epoch:
            raise ValueError("start_epoch must precede end_epoch")
        response = self._post(
            "TPSeries",
            {
                "uid": self.config.user_id,
                "exch": "MCX",
                "token": token,
                "st": str(start_epoch),
                "et": str(end_epoch),
                "intrv": str(interval),
            },
        )
        if isinstance(response, list):
            return response
        if isinstance(response, dict) and isinstance(response.get("values"), list):
            return response["values"]
        if isinstance(response, dict) and isinstance(response.get("result"), list):
            return response["result"]
        if isinstance(response, dict):
            raise ShoonyaApiError(
                f"TPSeries failed: stat={response.get('stat')}, "
                f"emsg={response.get('emsg') or 'unknown error'}"
            )
        raise ShoonyaApiError(
            f"TPSeries returned unexpected response type: {type(response).__name__}"
        )

    def _post(
        self, endpoint: str, payload: dict[str, Any], *, authenticated: bool = True
    ) -> Any:
        token = self.config.access_token
        if authenticated and not token:
            raise ValueError("SHOONYA_ACCESS_TOKEN is required for this operation")
        encoded_data = json.dumps(payload, separators=(",", ":"))
        if endpoint == "GenAcsTok":
            raw_body = f"jData={encoded_data}".encode("utf-8")
            headers = {"Content-Type": "text/plain"}
        else:
            raw_body = f"jData={encoded_data}".encode("utf-8")
            headers = {
                "Content-Type": "text/plain",
                "Authorization": f"Bearer {token}",
            }
        response = self._http_post(
            f"{self.config.api_base}{endpoint}",
            raw_body,
            headers,
        )
        return response


class ShoonyaWebSocket:
    """Shoonya feed with reconnect, subscription replay, and merged tick state.

    ``connect`` blocks until ``close`` is called. A dropped connection is
    retried with exponential backoff. The client must still reconcile broker
    positions and orders after reconnect before allowing new entries.
    """

    def __init__(
        self,
        config: ShoonyaConfig,
        *,
        on_tick: Callable[[dict[str, Any]], None] | None = None,
        on_order: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Any], None] | None = None,
        on_close: Callable[[], None] | None = None,
        reconnect: bool = True,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
        max_reconnect_attempts: int | None = None,
    ) -> None:
        if not config.access_token:
            raise ValueError("SHOONYA_ACCESS_TOKEN is required for WebSocket streaming")
        self.config = config
        self.on_tick = on_tick
        self.on_order = on_order
        self.on_error = on_error
        self.on_close = on_close
        if initial_backoff <= 0 or max_backoff < initial_backoff:
            raise ValueError("invalid reconnect backoff settings")
        if max_reconnect_attempts is not None and max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts cannot be negative")
        self.reconnect = reconnect
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.max_reconnect_attempts = max_reconnect_attempts
        self._app: Any = None
        self._connected = False
        self._stop = Event()
        self._lock = Lock()
        self._subscriptions: set[str] = set()
        self._tick_state: dict[str, dict[str, Any]] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tick_state(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of the latest merged touchline state per token."""
        with self._lock:
            return {token: dict(tick) for token, tick in self._tick_state.items()}

    def connect(self) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise ShoonyaApiError(
                "websocket-client is required; install project dependencies"
            ) from exc

        websocket_url = "wss://api.shoonya.com/NorenWSAPI/"
        self._stop.clear()
        attempt = 0
        while not self._stop.is_set():
            self._connected = False
            self._app = websocket.WebSocketApp(
                websocket_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            try:
                self._app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self._report_error(exc)
            finally:
                if self._connected:
                    attempt = 0
                self._connected = False

            if self._stop.is_set() or not self.reconnect:
                break
            attempt += 1
            if self.max_reconnect_attempts is not None and attempt > self.max_reconnect_attempts:
                self._report_error(ShoonyaApiError("maximum WebSocket reconnect attempts reached"))
                break
            delay_cap = min(self.max_backoff, self.initial_backoff * (2 ** (attempt - 1)))
            # Jitter avoids synchronized reconnect storms after a broker outage.
            delay = random.uniform(delay_cap * 0.5, delay_cap)
            if self._stop.wait(delay):
                break

    def close(self) -> None:
        """Stop reconnecting and close the active socket, if any."""
        self._stop.set()
        self._connected = False
        app = self._app
        if app is not None:
            try:
                app.close()
            except Exception as exc:
                self._report_error(exc)

    def subscribe(self, exchange_tokens: Iterable[str]) -> None:
        tokens = {str(token).strip() for token in exchange_tokens if str(token).strip()}
        if not tokens:
            return
        with self._lock:
            self._subscriptions.update(tokens)
        self._send_subscriptions(tokens)

    def unsubscribe(self, exchange_tokens: Iterable[str]) -> None:
        tokens = {str(token).strip() for token in exchange_tokens if str(token).strip()}
        if not tokens:
            return
        with self._lock:
            self._subscriptions.difference_update(tokens)
            for token in tokens:
                self._tick_state.pop(token.split("|")[-1], None)
        if self._connected and self._app:
            self._app.send(json.dumps({"t": "u", "k": "#".join(sorted(tokens))}))

    def _send_subscriptions(self, tokens: Iterable[str] | None = None) -> None:
        if not self._app or not self._connected:
            return
        if tokens is None:
            with self._lock:
                tokens = tuple(self._subscriptions)
        values = sorted(set(tokens))
        if values:
            self._app.send(json.dumps({"t": "t", "k": "#".join(values)}))

    def _on_open(self, app: Any) -> None:
        app.send(
            json.dumps(
                {
                    "t": "a",
                    "uid": self.config.user_id,
                    "actid": self.config.account_id or self.config.user_id,
                    "source": "API",
                    "accesstoken": self.config.access_token,
                }
            )
        )

    def _on_message(self, _app: Any, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            if self.on_error:
                self._report_error(ShoonyaApiError("invalid WebSocket JSON"))
            return
        if not isinstance(payload, dict):
            return
        if payload.get("t") == "ak":
            self._connected = payload.get("s") == "Ok"
            if not self._connected:
                self._report_error(ShoonyaApiError("Shoonya WebSocket authentication failed"))
                # Reconnecting with a rejected/expired token cannot help and
                # can cause a tight authentication loop.
                self.close()
            elif self._connected:
                self._send_subscriptions()
            return
        if payload.get("t") == "om":
            if self.on_order:
                self._call_callback(self.on_order, payload)
        elif payload.get("t") in {"tk", "tf", "df"}:
            token = str(payload.get("tk", ""))
            if token:
                with self._lock:
                    merged = self._tick_state.setdefault(token, {})
                    merged.update(payload)
                    payload = dict(merged)
            if self.on_tick:
                self._call_callback(self.on_tick, payload)

    def _on_error(self, _app: Any, error: Any) -> None:
        self._report_error(error)

    def _on_close(
        self,
        _app: Any,
        _close_status_code: Any = None,
        _close_reason: Any = None,
    ) -> None:
        self._connected = False
        if self.on_close:
            self._call_callback(self.on_close)

    def _report_error(self, error: Any) -> None:
        if self.on_error:
            self._call_callback(self.on_error, error)

    @staticmethod
    def _call_callback(callback: Callable[..., Any], *args: Any) -> None:
        try:
            callback(*args)
        except Exception:
            # User callbacks must not terminate the socket's receive loop.
            _LOGGER.exception("Shoonya WebSocket callback failed")
