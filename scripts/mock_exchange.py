"""Local fault-injecting HTTP exchange mock for credential-free rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


@dataclass
class MockState:
    api_key: str
    secret_key: bytes
    max_clock_skew_ms: int
    faults: set[str] = field(default_factory=set)
    orders: list[bytes] = field(default_factory=list)


def _response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handler_for(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/v3/server_time":
                _response(self, 200, {"serverTime": int(time.time() * 1000)})
                return
            if self.path == "/v3/exchange_info":
                _response(self, 200, {"Success": True, "TradePairs": {}})
                return
            if self.path.startswith("/v3/account?"):
                body = self.path.partition("?")[2].encode("ascii")
                expected = hmac.new(state.secret_key, body, hashlib.sha256).hexdigest()
                if self.headers.get("RST-API-KEY") != state.api_key or not hmac.compare_digest(
                    expected, self.headers.get("MSG-SIGNATURE", "")
                ):
                    _response(self, 401, {"Success": False, "ErrMsg": "invalid credentials"})
                    return
                params = parse_qs(body.decode("ascii"), keep_blank_values=True)
                if "timestamp" not in params:
                    _response(self, 400, {"Success": False, "ErrMsg": "missing timestamp"})
                    return
                timestamp = int(params["timestamp"][0])
                if abs(int(time.time() * 1000) - timestamp) > state.max_clock_skew_ms:
                    _response(self, 400, {"Success": False, "ErrMsg": "timestamp outside window"})
                    return
                _response(self, 200, {"Success": True, "Assets": {}})
                return
            _response(self, 404, {"Success": False, "ErrMsg": "unknown endpoint"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            if "timeout" in state.faults:
                self.connection.close()
                return
            if self.headers.get("RST-API-KEY") != state.api_key:
                _response(self, 401, {"Success": False, "ErrMsg": "invalid api key"})
                return
            expected = hmac.new(state.secret_key, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, self.headers.get("MSG-SIGNATURE", "")):
                _response(self, 401, {"Success": False, "ErrMsg": "invalid signature"})
                return
            params = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            if "timestamp" not in params:
                _response(self, 400, {"Success": False, "ErrMsg": "missing timestamp"})
                return
            timestamp = int(params["timestamp"][0])
            if abs(int(time.time() * 1000) - timestamp) > state.max_clock_skew_ms:
                _response(self, 400, {"Success": False, "ErrMsg": "timestamp outside window"})
                return
            if "429" in state.faults:
                _response(self, 429, {"Success": False, "ErrMsg": "rate limited"})
                return
            if "malformed_json" in state.faults:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{")
                return
            state.orders.append(body)
            _response(self, 200, {"Success": True, "Order": {"status": "FILLED"}})

        def log_message(self, *_args):
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--api-key", default="mock-api-key")
    parser.add_argument("--secret-key", default="mock-secret-key")
    parser.add_argument("--max-clock-skew-ms", type=int, default=5_000)
    parser.add_argument("--fault", action="append", default=[])
    args = parser.parse_args()
    state = MockState(
        args.api_key, args.secret_key.encode(), args.max_clock_skew_ms, set(args.fault)
    )
    ThreadingHTTPServer((args.host, args.port), handler_for(state)).serve_forever()


if __name__ == "__main__":
    main()
