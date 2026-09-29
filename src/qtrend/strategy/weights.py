"""Pure portfolio weighting schemes."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from qtrend.config import WeightsConfig


def assign(
    selected: Sequence[str],
    sigmas: Mapping[str, float],
    config: WeightsConfig,
) -> dict[str, float]:
    """Assign equal or inverse-volatility weights and apply the name cap."""
    symbols = tuple(sorted(selected))
    if not symbols:
        return {}
    if config.scheme == "equal":
        raw = {symbol: 1.0 / len(symbols) for symbol in symbols}
    elif config.scheme == "inverse_vol":
        inverse = {
            symbol: 1.0 / sigmas[symbol]
            for symbol in symbols
            if math.isfinite(sigmas.get(symbol, math.nan)) and sigmas[symbol] > 0
        }
        if len(inverse) != len(symbols):
            return {}
        total = sum(inverse.values())
        raw = {symbol: inverse[symbol] / total for symbol in symbols}
    else:
        raise ValueError(f"unsupported weighting scheme {config.scheme!r}")

    remaining = set(symbols)
    result: dict[str, float] = {}
    budget = 1.0
    while remaining:
        uncapped_total = sum(raw[symbol] for symbol in remaining)
        if uncapped_total <= 0:
            break
        capped = {
            symbol for symbol in remaining
            if budget * raw[symbol] / uncapped_total >= config.max_single_name
        }
        if not capped:
            for symbol in sorted(remaining):
                result[symbol] = budget * raw[symbol] / uncapped_total
            break
        for symbol in sorted(capped):
            result[symbol] = config.max_single_name
            budget -= config.max_single_name
            remaining.remove(symbol)
    return {symbol: result[symbol] for symbol in symbols}
