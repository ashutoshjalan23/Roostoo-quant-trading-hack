"""Pure baseline target-weight helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from qtrend.data.panel import PanelView


def buy_and_hold(symbol: str) -> dict[str, float]:
    """Return a fully invested target for one named asset."""
    return {symbol: 1.0}


def equal_weight(symbols: Sequence[str]) -> dict[str, float]:
    """Return equal weights for the supplied deterministic symbol set."""
    ordered = tuple(sorted(symbols))
    if not ordered:
        return {}
    weight = 1.0 / len(ordered)
    return {symbol: weight for symbol in ordered}


def top_volume(view: PanelView, top_k: int, window_bars: int) -> dict[str, float]:
    """Return equal weights for the highest trailing-median-volume symbols."""
    if view.n_rows < window_bars:
        return {}
    ranked = sorted(
        (
            (float(np.median(view.quote_volume(symbol)[-window_bars:])), symbol)
            for symbol in view.symbols
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return equal_weight([symbol for _, symbol in ranked[:top_k]])


def without_vol_target(weights: Mapping[str, float]) -> dict[str, float]:
    """Return a copy of strategy weights without applying an exposure scale."""
    return {symbol: weights[symbol] for symbol in sorted(weights)}
