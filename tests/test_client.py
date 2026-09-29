"""Phase 10 client safety tests."""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest
import requests

from qtrend.roostoo.client import (
    ClientConfig,
    RoostooClient,
    TokenBucket,
    UncertainOrderError,
    signature,
)


def test_signature_is_exact_bytes_hmac():
    assert signature(b"secret", b"a=1&b=2") == (
        "604fe97c66c6393ff22e3cae366eee1131e351ebc736bf12f5d62e1755b7a233"
    )


def test_signed_get_uses_query_bytes_and_synced_timestamp():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"Success": True}

    class Session:
        call = None

        def request(self, *args, **kwargs):
            self.call = (args, kwargs)
            return Response()

    session = Session()
    client = RoostooClient(
        ClientConfig("https://exchange.invalid", 100), "api-key", b"secret", session,
        clock_ms=lambda: 1_000,
    )
    assert client.sync_clock(1_025) == 25

    assert client.account(b"asset=USD") == {"Success": True}
    (method, url), kwargs = session.call
    query = urlsplit(url).query.encode("ascii")
    assert method == "GET"
    assert query == b"asset=USD&timestamp=1025"
    assert "data" not in kwargs
    assert kwargs["headers"]["MSG-SIGNATURE"] == signature(b"secret", query)


def test_signed_body_rejects_duplicate_caller_timestamp():
    client = RoostooClient(ClientConfig("https://exchange.invalid", 100), "key", b"secret")
    with pytest.raises(ValueError, match="must not supply its own timestamp"):
        client.signed_body(b"timestamp=1")


def test_client_rehearses_signed_account_and_order_against_local_mock():
    from scripts.mock_exchange import MockState, handler_for

    state = MockState("mock-api-key", b"mock-secret", max_clock_skew_ms=5_000)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(state))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = RoostooClient(
            ClientConfig(f"http://127.0.0.1:{server.server_port}", 5_000),
            state.api_key,
            state.secret_key,
        )
        client.sync_clock(client.server_time())
        assert client.account() == {"Success": True, "Assets": {}}
        assert client.place_order(b"symbol=AAA") == {
            "Success": True,
            "Order": {"status": "FILLED"},
        }
        assert len(state.orders) == 1
        assert b"timestamp=" in state.orders[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_uncertain_place_order_is_never_retried():
    class Session:
        calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            raise requests.Timeout()

    session = Session()
    client = RoostooClient(ClientConfig("http://invalid", 1), "key", b"secret", session)
    with pytest.raises(UncertainOrderError):
        client.place_order(b"symbol=AAA")
    assert session.calls == 1


def test_token_bucket_rejects_over_rate():
    bucket = TokenBucket(1, 0.001)
    bucket.take()
    with pytest.raises(RuntimeError, match="rate limit"):
        bucket.take()


def test_clock_sync_rejects_excessive_skew():
    client = RoostooClient(
        ClientConfig("http://invalid", 10), "key", b"secret", clock_ms=lambda: 1000
    )
    with pytest.raises(RuntimeError, match="clock skew"):
        client.sync_clock(2000)


def test_paper_exchange_reconciles_without_credentials():
    from qtrend.roostoo.client import PaperExchange

    exchange = PaperExchange(100.0)
    assert exchange.reconcile() == {"cash": 100.0, "positions": {}}
