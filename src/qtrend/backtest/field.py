"""Deterministic rule-generated competitor field."""

from __future__ import annotations

from qtrend.data.panel import PanelView
from qtrend.strategy.baselines import top_volume


def top_volume_competitor(
    view: PanelView,
    top_k: int,
    volume_window_bars: int,
) -> dict[str, float]:
    """Return the same-interface top-volume competitor target weights."""
    return top_volume(view, top_k, volume_window_bars)
