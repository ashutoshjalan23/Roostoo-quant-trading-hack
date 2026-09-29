"""Liquidity-based universe eligibility."""

from __future__ import annotations

import numpy as np

from qtrend.config import UniverseConfig
from qtrend.data.panel import PanelView

HOURS_PER_DAY = 24


def eligible(view: PanelView, config: UniverseConfig) -> tuple[str, ...]:
    """Return symbols meeting the trailing median daily dollar-volume floor."""
    window_bars = config.volume_window_days * HOURS_PER_DAY
    if view.n_rows < window_bars:
        return ()

    result: list[str] = []
    for symbol in view.symbols:
        if symbol in config.exclude:
            continue
        volumes = np.asarray(view.quote_volume(symbol)[-window_bars:], dtype=np.float64)
        if float(np.median(volumes)) * HOURS_PER_DAY >= config.min_daily_dollar_volume:
            result.append(symbol)
    return tuple(result)
