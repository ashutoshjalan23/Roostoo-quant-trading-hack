"""Phase 10 client safety tests."""

from __future__ import annotations

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
