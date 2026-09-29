"""Pure ranked selection with hysteresis."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from qtrend.config import SelectionConfig


def select(
    signals: Mapping[str, float],
    held: Iterable[str],
    config: SelectionConfig,
) -> tuple[str, ...]:
    """Select entries and retain existing holdings within the configured rank band."""
    ranked = sorted(
        ((symbol, value) for symbol, value in signals.items() if math.isfinite(value)),
        key=lambda item: (-item[1], item[0]),
    )
    rank_of = {symbol: rank for rank, (symbol, _) in enumerate(ranked, 1)}
    held_set = set(held)
    retained = {
        symbol
        for symbol in held_set
        if rank_of.get(symbol, config.keep_rank + 1) <= config.keep_rank
    }

    selected: list[str] = [symbol for symbol, _ in ranked if symbol in retained]
    for symbol, value in ranked:
        if len(selected) >= config.top_k:
            break
        if symbol in retained or value < config.min_signal:
            continue
        selected.append(symbol)
    return tuple(selected)
