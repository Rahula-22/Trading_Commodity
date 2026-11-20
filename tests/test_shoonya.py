from decimal import Decimal

import pytest

from trading_engine.broker import Exchange, Instrument, OrderRequest, OrderSide, OrderType
from trading_engine.shoonya import ShoonyaApiError, ShoonyaBroker, ShoonyaConfig


def config(**kwargs: object) -> ShoonyaConfig:
    values = {
        "user_id": "U1",
        "client_id": "C1",
        "secret_code": "secret",
        "api_key": "key",
        "access_token": "token",
        "account_id": "U1",
        "live_trading": False,
    }
    values.update(kwargs)
    return ShoonyaConfig(**values)


def test_oauth_checksum_and_url() -> None:
    current = config()
    assert current.authorization_url() == (
        "https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=C1"
    )
    assert len(current.checksum("auth-code")) == 64


def test_mcx_permission_is_read_from_user_details() -> None:
    def post(url: str, body: bytes, headers: dict[str, str]) -> dict[str, object]:
        assert url.endswith("UserDetails")
        return {"stat": "Ok", "exarr": [{"exch": "MCX"}]}

    assert ShoonyaBroker(config(), http_post=post).check_mcx_permission()


def test_live_order_is_blocked_by_default() -> None:
    instrument = Instrument(
        exchange=Exchange.MCX,
        symbol="CRUDEOILM",
        token="T1",
        lot_size=10,
        price_multiplier=Decimal("1"),
        tick_size=Decimal("1"),
        expiry="2099-01-19",
    )
    order = OrderRequest(
        instrument=instrument,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=Decimal("7000"),
        client_order_id="paper-1",
    )

    with pytest.raises(ShoonyaApiError, match="live trading is disabled"):
        ShoonyaBroker(config(), http_post=lambda *_: {}).place_order(order)


def test_access_token_config_honors_live_trading_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOONYA_USER_ID", "U1")
    monkeypatch.setenv("SHOONYA_ACCESS_TOKEN", "token")
    monkeypatch.setenv("SHOONYA_LIVE_TRADING", "false")

    assert ShoonyaConfig.for_access_token().live_trading is False


def test_limits_and_available_margin() -> None:
    def post(url: str, body: bytes, headers: dict[str, str]) -> dict[str, object]:
        assert url.endswith("Limits")
        return {"stat": "Ok", "cash": "50000", "marginused": "30000"}

    broker = ShoonyaBroker(config(), http_post=post)
    assert broker.get_available_margin() == Decimal("20000")


def test_tp_series_payload_and_interval() -> None:
    def post(url: str, body: bytes, headers: dict[str, str]) -> list[dict[str, str]]:
        assert url.endswith("TPSeries")
        return [{"time": "1700000000", "into": "7000"}]

    broker = ShoonyaBroker(config(), http_post=post)
    result = broker.get_time_price_series(
        token="123",
        start_epoch=1700000000,
        end_epoch=1700003600,
        interval=1,
    )
    assert result[0]["time"] == "1700000000"


def test_search_scrip_no_match_is_empty() -> None:
    def post(url: str, body: bytes, headers: dict[str, str]) -> dict[str, object]:
        assert url.endswith("SearchScrip")
        return {"stat": "Not_Ok", "emsg": "No Data : "}

    matches = ShoonyaBroker(config(), http_post=post).search_scrip("UNKNOWN")
    assert matches == []
