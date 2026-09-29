"""Tests for pure volatility-targeted exposure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from qtrend.backtest.lookahead import assert_no_lookahead
from qtrend.config import ExposureConfig
from qtrend.data.panel import Panel
from qtrend.strategy.exposure import estimate_rho, scale

HOUR = timedelta(hours=1)


def view_for(close: np.ndarray):
    rows, columns = close.shape
    panel = Panel(
        index=[datetime(2024, 1, 1, tzinfo=UTC) + (i + 1) * HOUR for i in range(rows)],
        close=close, quote_volume=np.ones((rows, columns)),
        stale=np.zeros((rows, columns), dtype=bool), symbols=("AAA", "BBB"),
    )
    return panel.slice_to(panel.end_time)


def config(**changes):
    values = dict(
        target_daily_vol=0.1, rho_estimation="rolling", rho_window_days=2, max_leverage=1.0
    )
    values.update(changes)
    return ExposureConfig(**values)


def test_rho_is_measured_from_the_panel():
    returns = np.tile(np.array([0.01, -0.01, 0.02, -0.02]), 13)
    close = np.exp(np.cumsum(np.column_stack((returns, returns)), axis=0))
    rho = estimate_rho(view_for(close), ("AAA", "BBB"), config())

    assert rho == pytest.approx(1.0)


def test_scale_hits_target_when_leverage_allows_it():
    rows = 50
    steps = np.column_stack((np.ones(rows) * 0.01, -np.ones(rows) * 0.01))
    close = np.exp(np.cumsum(steps, axis=0))
    view = view_for(close)
    result = scale({"AAA": 0.5, "BBB": 0.5}, {"AAA": 0.1, "BBB": 0.1}, view, config())

    assert sum(result.values()) == pytest.approx(1.0)


def test_leverage_cap_is_respected():
    close = np.exp(np.cumsum(np.ones((50, 2)) * 0.01, axis=0))
    result = scale(
        {"AAA": 0.5, "BBB": 0.5}, {"AAA": 0.01, "BBB": 0.01}, view_for(close),
        config(target_daily_vol=1.0, max_leverage=0.4),
    )

    assert sum(result.values()) == pytest.approx(0.4)


def test_exposure_passes_the_no_lookahead_checker():
    rows = 60
    steps = np.column_stack((np.ones(rows) * 0.01, -np.ones(rows) * 0.01))
    close = np.exp(np.cumsum(steps, axis=0))
    panel = Panel(
        index=[datetime(2024, 1, 1, tzinfo=UTC) + (i + 1) * HOUR for i in range(rows)],
        close=close, quote_volume=np.ones((rows, 2)), stale=np.zeros((rows, 2), dtype=bool),
        symbols=("AAA", "BBB"),
    )
    exposure_config = config()
    def strategy(view, _):
        return scale(
            {"AAA": 0.5, "BBB": 0.5}, {"AAA": 0.1, "BBB": 0.1}, view, exposure_config
        )
    assert_no_lookahead(strategy, panel, datetime(2024, 1, 3, tzinfo=UTC), None, seed=6)
