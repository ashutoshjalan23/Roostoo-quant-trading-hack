"""Public Roostoo metadata normalization tests."""

from __future__ import annotations

import pytest
from scripts.fetch_pair_info import pair_info_document


def test_pair_info_uses_current_amount_precision_and_minimum_order():
    document = pair_info_document(
        {
            "TradePairs": {
                "BTC/USD": {
                    "CanTrade": True,
                    "AmountPrecision": 5,
                    "MiniOrder": 1,
                },
                "OLD/USD": {
                    "CanTrade": False,
                    "AmountPrecision": 2,
                    "MiniOrder": 10,
                },
            }
        }
    )

    assert document == {"BTCUSD": {"step_size": "0.00001", "min_notional": "1"}}


def test_pair_info_rejects_missing_trade_pairs():
    with pytest.raises(ValueError, match="missing TradePairs"):
        pair_info_document({})
