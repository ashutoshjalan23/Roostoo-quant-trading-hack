"""Pure return and equity-curve metrics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np


def sharpe(returns: Sequence[float], periods_per_year: float) -> float:
    values = np.asarray(returns, dtype=float)
    if values.size < 2 or np.std(values, ddof=1) == 0:
        return 0.0
    return float(np.mean(values) / np.std(values, ddof=1) * math.sqrt(periods_per_year))


def sortino(returns: Sequence[float], periods_per_year: float, mar: float) -> float:
    values = np.asarray(returns, dtype=float)
    downside = np.minimum(values - mar, 0.0)
    deviation = math.sqrt(float(np.mean(downside**2)))
    if deviation == 0:
        return 0.0
    return float(np.mean(values - mar) / deviation * math.sqrt(periods_per_year))


def max_drawdown(equity_curve: Sequence[float]) -> float:
    values = np.asarray(equity_curve, dtype=float)
    if values.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(values)
    return float(np.max((peaks - values) / peaks))


def calmar(
    returns: Sequence[float],
    equity_curve: Sequence[float],
    periods_per_year: float,
    min_drawdown: float,
) -> float:
    drawdown = max_drawdown(equity_curve)
    denominator = max(drawdown, min_drawdown)
    if denominator <= 0:
        return 0.0
    values = np.asarray(returns, dtype=float)
    annualized = float(np.prod(1.0 + values) ** (periods_per_year / max(values.size, 1)) - 1.0)
    return annualized / denominator


def composite(
    returns: Sequence[float],
    equity_curve: Sequence[float],
    periods_per_year: float,
    mar: float,
    min_drawdown: float,
    metric_weights: Mapping[str, float],
) -> float:
    values = {
        "sharpe": sharpe(returns, periods_per_year),
        "sortino": sortino(returns, periods_per_year, mar),
        "calmar": calmar(returns, equity_curve, periods_per_year, min_drawdown),
    }
    return float(sum(metric_weights[name] * values[name] for name in sorted(metric_weights)))
