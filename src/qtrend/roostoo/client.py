"""Minimal typed Roostoo client primitives with conservative order semantics."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

import requests


class UncertainOrderError(RuntimeError):
    """The order may have reached the exchange and must not be retried."""


def signature(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float):
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("token bucket parameters must be positive")
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.refill_per_second = refill_per_second
        self.updated = time.monotonic()
        self.lock = Lock()

    def take(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity, self.tokens + (now - self.updated) * self.refill_per_second
            )
            self.updated = now
            if self.tokens < 1:
                raise RuntimeError("API rate limit would be exceeded")
            self.tokens -= 1


@dataclass(frozen=True, slots=True)
class ClientConfig:
    base_url: str
    max_clock_skew_ms: int


class Exchange(Protocol):
    def reconcile(self) -> Mapping[str, object]: ...

    def place_order(self, order: bytes) -> object: ...


class PaperExchange:
    """Deterministic in-memory exchange for the paper engine."""

    def __init__(self, initial_cash: float):
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.cash = initial_cash
        self.orders: list[bytes] = []

    def reconcile(self) -> Mapping[str, object]:
        return {"cash": self.cash, "positions": {}}

    def place_order(self, order: bytes) -> object:
        self.orders.append(order)
        return {"Success": True, "paper": True}


class MockRoostooExchange:
    def __init__(self, client: RoostooClient):
        self.client = client

    def reconcile(self) -> Mapping[str, object]:
        result = self.client.account()
        if not isinstance(result, dict):
            raise ValueError("account response is malformed")
        return result

    def place_order(self, order: bytes) -> object:
        return self.client.place_order(order)


class RoostooExchange(MockRoostooExchange):
    """Live adapter; credentials are supplied by the caller, never from defaults."""


class RoostooClient:
    def __init__(
        self,
        config: ClientConfig,
        api_key: str,
        secret_key: bytes,
        session=None,
        clock_ms=None,
    ):
        self.config = config
        self.api_key = api_key
        self.secret_key = secret_key
        self.session = session or requests.Session()
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.clock_offset_ms = 0

    def signed_body(self, body: bytes) -> bytes:
        """Return the exact body that is signed and sent."""
        return body

    def sync_clock(self, server_time_ms: int) -> int:
        local = self.clock_ms()
        self.clock_offset_ms = int(server_time_ms) - local
        if abs(self.clock_offset_ms) > self.config.max_clock_skew_ms:
            raise RuntimeError("exchange clock skew exceeds configured limit")
        return self.clock_offset_ms

    def server_time(self) -> int:
        result = self.public_request("GET", "/v3/server_time")
        if not isinstance(result, dict) or "serverTime" not in result:
            raise ValueError("server time response is malformed")
        return int(result["serverTime"])

    def public_request(self, method: str, path: str) -> object:
        response = self.session.request(method, self.config.base_url + path)
        response.raise_for_status()
        return response.json()

    def signed_request(self, method: str, path: str, body: bytes) -> object:
        body = self.signed_body(body)
        headers = {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": signature(self.secret_key, body),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response = self.session.request(
            method, self.config.base_url + path, data=body, headers=headers
        )
        response.raise_for_status()
        return response.json()

    def account(self, body: bytes = b"") -> object:
        return self.signed_request("GET", "/v3/account", body)

    def exchange_info(self) -> object:
        return self.public_request("GET", "/v3/exchange_info")

    def place_order(self, body: bytes) -> object:
        try:
            return self.signed_request("POST", "/v3/place_order", body)
        except (requests.Timeout, requests.ConnectionError, ValueError) as error:
            raise UncertainOrderError(
                "place_order response is uncertain; cycle must halt"
            ) from error
