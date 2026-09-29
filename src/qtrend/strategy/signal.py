"""Pure multi-horizon, volatility-adjusted momentum signals."""

from __future__ import annotations

import math

import numpy as np

from qtrend.config import SignalConfig
from qtrend.data.panel import PanelView

HOURS_PER_DAY = 24


def compute(
    view: PanelView,
    symbols: tuple[str, ...] | list[str],
    sigmas: dict[str, float],
    config: SignalConfig,
) -> dict[str, float]:
    """Return the mean risk-adjusted log return across configured horizons."""
    result: dict[str, float] = {}
    for symbol in sorted(symbols):
        sigma = sigmas.get(symbol, math.nan)
        if not math.isfinite(sigma) or sigma <= 0:
            result[symbol] = math.nan
            continue

        prices = np.asarray(view.close(symbol), dtype=np.float64)
        stale = np.asarray(view.stale_mask(symbol), dtype=bool)
        current = prices.size - 1 - config.skip_hours
        horizon_signals: list[float] = []
        for horizon_days in config.horizons_days:
            offset = horizon_days * HOURS_PER_DAY
            past = current - offset
            if past < 0 or stale[past : current + 1].any():
                horizon_signals = []
                break
            log_return = math.log(prices[current] / prices[past])
            horizon_signals.append(log_return / (sigma * math.sqrt(horizon_days)))
        result[symbol] = (
            float(np.mean(horizon_signals)) if horizon_signals else math.nan
        )
    return result
