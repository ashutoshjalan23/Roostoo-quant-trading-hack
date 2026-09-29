"""Rolling-window summaries and seeded independent bootstrap resampling."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    equity: tuple[float, ...]


def rolling_windows(
    equity_curve: Sequence[tuple[datetime, float]],
    window_days: int,
    step_days: int,
) -> tuple[Window, ...]:
    if window_days <= 0 or step_days <= 0:
        raise ValueError("window_days and step_days must be positive")
    if not equity_curve:
        return ()
    start = equity_curve[0][0]
    end_limit = equity_curve[-1][0]
    windows: list[Window] = []
    while start + timedelta(days=window_days) <= end_limit:
        end = start + timedelta(days=window_days)
        values = tuple(value for timestamp, value in equity_curve if start <= timestamp <= end)
        if values:
            windows.append(Window(start, end, values))
        start += timedelta(days=step_days)
    return tuple(windows)


def bootstrap(
    windows: Sequence[Window], n_resamples: int, seed: int
) -> tuple[tuple[Window, ...], ...]:
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    if not windows:
        return tuple(() for _ in range(n_resamples))
    rng = np.random.default_rng(seed)
    size = len(windows)
    return tuple(
        tuple(windows[int(index)] for index in rng.integers(0, size, size=size))
        for _ in range(n_resamples)
    )
