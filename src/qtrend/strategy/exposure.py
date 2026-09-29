"""Pure constant-correlation volatility targeting."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

from qtrend.config import ExposureConfig
from qtrend.data.panel import PanelView

HOURS_PER_DAY = 24


def estimate_rho(
    view: PanelView,
    symbols: Sequence[str],
    config: ExposureConfig,
) -> float:
    """Estimate mean pairwise correlation from the trailing live hourly returns."""
    if config.rho_estimation != "rolling":
        raise ValueError(f"unsupported rho estimation {config.rho_estimation!r}")
    window_bars = config.rho_window_days * HOURS_PER_DAY
    returns: dict[str, np.ndarray] = {}
    stale: dict[str, np.ndarray] = {}
    for symbol in sorted(symbols):
        prices = np.asarray(view.close(symbol), dtype=np.float64)
        returns[symbol] = np.diff(np.log(prices))[-window_bars:]
        stale_mask = np.asarray(view.stale_mask(symbol), dtype=bool)
        stale[symbol] = (stale_mask[:-1] | stale_mask[1:])[-window_bars:]

    correlations: list[float] = []
    ordered = tuple(sorted(symbols))
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            live = ~stale[left] & ~stale[right]
            if live.sum() < 2:
                continue
            correlation = float(np.corrcoef(returns[left][live], returns[right][live])[0, 1])
            if math.isfinite(correlation):
                correlations.append(correlation)
    return float(np.mean(correlations)) if correlations else 0.0


def scale(
    weights: Mapping[str, float],
    sigmas: Mapping[str, float],
    view: PanelView,
    config: ExposureConfig,
) -> dict[str, float]:
    """Scale target weights to the configured daily volatility target."""
    symbols = tuple(sorted(weights))
    if not symbols:
        return {}
    rho = estimate_rho(view, symbols, config)
    sigma_p_squared = 0.0
    for symbol in symbols:
        sigma = sigmas.get(symbol, math.nan)
        weight = weights[symbol]
        if not math.isfinite(sigma) or not math.isfinite(weight):
            raise ValueError("portfolio volatility inputs must be finite")
        sigma_p_squared += weight * weight * sigma * sigma
    for left_index, left in enumerate(symbols):
        for right in symbols[left_index + 1 :]:
            sigma_p_squared += (
                2.0 * rho * weights[left] * weights[right] * sigmas[left] * sigmas[right]
            )
    if not math.isfinite(sigma_p_squared) or sigma_p_squared <= 0:
        raise ValueError("portfolio volatility is not finite and positive")
    portfolio_vol = math.sqrt(sigma_p_squared)
    scale_factor = min(config.max_leverage, config.target_daily_vol / portfolio_vol)
    return {symbol: weights[symbol] * scale_factor for symbol in symbols}
