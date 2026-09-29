"""Tests for the pure volatility forecast."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from qtrend.backtest.lookahead import assert_no_lookahead
from qtrend.config import VolConfig
from qtrend.data.panel import Panel
from qtrend.data.synthetic import random_walk
from qtrend.strategy.volatility import forecast, qlike

HOUR = timedelta(hours=1)


def view_for(prices: list[float], stale: list[bool] | None = None):
    stale = stale or [False] * len(prices)
    panel = Panel(
        index=[datetime(2024, 1, 1, tzinfo=UTC) + (i + 1) * HOUR for i in range(len(prices))],
        close=np.asarray(prices, dtype=float)[:, None],
        quote_volume=np.ones((len(prices), 1)),
        stale=np.asarray(stale, dtype=bool)[:, None],
        symbols=("AAA",),
    )
    return panel, panel.slice_to(panel.end_time)


def test_ewma_forecast_matches_one_return():
    lam = 0.8
    floor = 0.001
    panel, view = view_for([100.0, 110.0])
    result = forecast(view, ("AAA",), VolConfig(lam=lam, min_observations=1, floor=floor))

    expected = math.sqrt((math.log(110.0 / 100.0) ** 2) * 24)
    assert result["AAA"] == pytest.approx(max(expected, floor))


def test_stale_bars_are_excluded_from_returns():
    panel, view = view_for([100.0, 200.0, 100.0], [False, True, False])
    result = forecast(view, ("AAA",), VolConfig(lam=0.5, min_observations=1, floor=0.01))

    assert math.isnan(result["AAA"])


def test_floor_and_minimum_observations_are_enforced():
    panel, view = view_for([100.0, 100.1, 100.2])
    result = forecast(view, ("AAA",), VolConfig(lam=0.5, min_observations=2, floor=0.5))

    assert result["AAA"] == 0.5
    assert math.isnan(
        forecast(view, ("AAA",), VolConfig(lam=0.5, min_observations=3, floor=0.5))["AAA"]
    )


def test_qlike_matches_the_hand_computed_mean():
    forecast_variance = np.array([1.0, 2.0])
    realized_variance = np.array([1.0, 4.0])

    expected = (math.log(1.0) + 1.0 + math.log(2.0) + 2.0) / 2.0
    assert qlike(forecast_variance, realized_variance) == pytest.approx(expected)


def test_qlike_penalizes_underforecasting_a_variance_spike():
    realized_variance = np.array([4.0])

    under = qlike(np.array([0.5]), realized_variance)
    over = qlike(np.array([2.0]), realized_variance)
    assert under > over


@pytest.mark.parametrize(
    ("forecast_variance", "realized_variance", "message"),
    [
        ([0.0], [1.0], "forecast variance"),
        ([1.0], [-1.0], "realized variance"),
        ([1.0], [1.0, 2.0], "shapes differ"),
        ([], [], "at least one"),
    ],
)
def test_qlike_rejects_invalid_variances(forecast_variance, realized_variance, message):
    with pytest.raises(ValueError, match=message):
        qlike(forecast_variance, realized_variance)


def test_volatility_passes_the_no_lookahead_checker():
    panel = random_walk(
        symbols=("AAA",), n_bars=96, start=datetime(2024, 1, 1, tzinfo=UTC), interval=HOUR,
        seed=4, initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar={"AAA": 10_000.0}, volume_jitter=0.0,
    )
    config = VolConfig(lam=0.8, min_observations=2, floor=0.01)
    assert_no_lookahead(forecast, panel, datetime(2024, 1, 3, tzinfo=UTC), config, seed=8)
