"""Tests for the pure liquidity universe rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from qtrend.backtest.lookahead import assert_no_lookahead
from qtrend.config import UniverseConfig
from qtrend.data.panel import Panel
from qtrend.data.synthetic import random_walk
from qtrend.strategy.universe import eligible

HOUR = timedelta(hours=1)


def build_view(volumes: np.ndarray):
    rows, columns = volumes.shape
    index = [datetime(2024, 1, 1, tzinfo=UTC) + (i + 1) * HOUR for i in range(rows)]
    panel = Panel(
        index=index,
        close=np.ones((rows, columns)),
        quote_volume=volumes,
        stale=np.zeros((rows, columns), dtype=bool),
        symbols=("AAA", "BBB"),
    )
    return panel.slice_to(panel.end_time)


def config(*, volume_window_days: int = 1, floor: float = 2_500.0, exclude: tuple[str, ...] = ()):
    return UniverseConfig(
        quote_asset="USD",
        volume_window_days=volume_window_days,
        min_daily_dollar_volume=floor,
        exclude=exclude,
    )


def test_eligibility_uses_trailing_median_not_mean():
    volumes = np.full((24, 2), 100.0)
    volumes[:-1, 0] = 200.0
    volumes[-1, 0] = 10_000.0

    assert eligible(build_view(volumes), config()) == ("AAA",)


def test_eligibility_requires_a_complete_trailing_window():
    volumes = np.full((23, 2), 10_000.0)

    assert eligible(build_view(volumes), config()) == ()


def test_excluded_symbols_are_never_eligible():
    volumes = np.full((24, 2), 10_000.0)

    assert eligible(build_view(volumes), config(exclude=("BBB",))) == ("AAA",)


def test_universe_passes_the_no_lookahead_checker():
    panel = random_walk(
        symbols=("AAA", "BBB"),
        n_bars=96,
        start=datetime(2024, 1, 1, tzinfo=UTC),
        interval=HOUR,
        seed=7,
        initial_price=100.0,
        daily_vol=0.02,
        drift_daily_log_return=0.0,
        quote_volume_per_bar={"AAA": 10_000.0, "BBB": 10_000.0},
        volume_jitter=0.1,
    )
    assert_no_lookahead(
        eligible,
        panel,
        datetime(2024, 1, 3, tzinfo=UTC),
        config(),
        seed=11,
    )
