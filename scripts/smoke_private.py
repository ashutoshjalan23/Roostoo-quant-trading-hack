"""Credential-requiring private endpoint smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path

from qtrend.config import load_config, load_credentials
from qtrend.roostoo.client import ClientConfig, RoostooClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args()
    config = load_config(args.config)
    credentials = load_credentials(args.env)
    client = RoostooClient(
        ClientConfig(config.exchange.base_url, config.exchange.max_clock_skew_ms),
        credentials.api_key,
        credentials.secret_key.encode(),
    )
    client.sync_clock(client.server_time())
    client.account()
    print("private endpoint smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
