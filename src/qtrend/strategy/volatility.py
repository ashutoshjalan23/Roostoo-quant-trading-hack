"""Pure EWMA daily volatility forecasts."""

from __future__ import annotations

import math

import numpy as np

from qtrend.config import VolConfig
from qtrend.data.panel import PanelView

HOURS_PER_DAY = 24


def qlike(forecast_variance: object, realized_variance: object) -> float:
    """Return mean QLIKE for forecast and realized variances.

    The additive constant is omitted, so lower values are better. Forecast variance must
    be strictly positive; realized variance must be finite and non-negative.
    """
    forecast = np.asarray(forecast_variance, dtype=np.float64)
    realized = np.asarray(realized_variance, dtype=np.float64)
    if forecast.shape != realized.shape:
        raise ValueError(
            f"forecast and realized variance shapes differ: {forecast.shape} != {realized.shape}"
        )
    if forecast.size == 0:
        raise ValueError("QLIKE requires at least one observation")
    if not np.all(np.isfinite(forecast)) or np.any(forecast <= 0):
        raise ValueError("forecast variance must be finite and strictly positive")
    if not np.all(np.isfinite(realized)) or np.any(realized < 0):
        raise ValueError("realized variance must be finite and non-negative")
    return float(np.mean(np.log(forecast) + realized / forecast))


def forecast(
    view: PanelView,
    symbols: tuple[str, ...] | list[str] | VolConfig,
    config: VolConfig | None = None,
) -> dict[str, float]:
    """Return floored daily EWMA volatility, or NaN when observations are insufficient."""
    if config is None:
        config = symbols
        symbols = view.symbols
    if not isinstance(config, VolConfig):
        raise TypeError("config must be a VolConfig")
    result: dict[str, float] = {}
    for symbol in sorted(symbols):
        prices = np.asarray(view.close(symbol), dtype=np.float64)
        stale = np.asarray(view.stale_mask(symbol), dtype=bool)
        if prices.size < 2:
            result[symbol] = math.nan
            continue

        live_pairs = ~stale[:-1] & ~stale[1:]
        returns = np.diff(np.log(prices))[live_pairs]
        if returns.size < config.min_observations:
            result[symbol] = math.nan
            continue

        variance = float(returns[0] ** 2)
        for value in returns[1:]:
            variance = config.lam * variance + (1.0 - config.lam) * float(value**2)
        result[symbol] = max(math.sqrt(variance * HOURS_PER_DAY), config.floor)
    return result
