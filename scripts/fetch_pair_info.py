"""Fetch current public Roostoo pair constraints for reproducible backtests."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

import requests


def pair_info_document(exchange_info: dict[str, object]) -> dict[str, dict[str, str]]:
    """Convert public exchangeInfo precision/minimum fields into planner constraints."""
    pairs = exchange_info.get("TradePairs")
    if not isinstance(pairs, dict):
        raise ValueError("exchangeInfo response is missing TradePairs")
    result: dict[str, dict[str, str]] = {}
    for pair, raw in sorted(pairs.items()):
        if not isinstance(raw, dict) or not raw.get("CanTrade"):
            continue
        try:
            amount_precision = int(raw["AmountPrecision"])
            min_notional = Decimal(str(raw["MiniOrder"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid public metadata for {pair}: {error}") from error
        if amount_precision < 0 or min_notional <= 0:
            raise ValueError(f"invalid quantity precision or minimum notional for {pair}")
        symbol = str(pair).replace("/", "")
        result[symbol] = {
            "step_size": str(Decimal(1).scaleb(-amount_precision)),
            "min_notional": str(min_notional),
        }
    if not result:
        raise ValueError("exchangeInfo contains no tradeable pairs")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default="https://mock-api.roostoo.com", help="Roostoo public API base URL"
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    response = requests.get(f"{args.base_url.rstrip('/')}/v3/exchangeInfo", timeout=30)
    response.raise_for_status()
    document = pair_info_document(response.json())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"saved {len(document)} tradeable pairs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
