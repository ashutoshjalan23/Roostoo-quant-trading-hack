"""Pure configurable trading cost model."""

from __future__ import annotations

from decimal import Decimal

from qtrend.config import CostsConfig


def apply_costs(
    notional: Decimal,
    order_type: str,
    config: CostsConfig,
) -> Decimal:
    """Return fee plus slippage cost for a positive notional."""
    if notional < 0:
        raise ValueError("notional must not be negative")
    fee = config.taker_fee if order_type == "market" else config.maker_fee
    return (
        notional * Decimal(str(fee))
        + notional * Decimal(str(config.slippage_bps)) / Decimal("10000")
    )
