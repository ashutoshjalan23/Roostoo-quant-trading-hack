"""Tests for the pure momentum signal."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from qtrend.backtest.lookahead import assert_no_lookahead
from qtrend.config import SignalConfig
from qtrend.data.panel import Panel
from qtrend.strategy.signal import compute

HOUR = timedelta(hours=1)


def view_for(n_rows: int = 100, stale: list[bool] | None = None):
    prices = np.exp(np.arange(n_rows, dtype=float) / 100.0)
    stale_array = np.zeros(n_rows, dtype=bool) if stale is None else np.asarray(stale)
    panel = Panel(
        index=[datetime(2024, 1, 1, tzinfo=UTC) + (i + 1) * HOUR for i in range(n_rows)],
        close=prices[:, None], quote_volume=np.ones((n_rows, 1)), stale=stale_array[:, None],
        symbols=("AAA",),
    )
    return panel.slice_to(panel.end_time)


def test_signal_uses_skip_and_averages_horizons():
    view = view_for()
    config = SignalConfig(horizons_days=(1, 2), skip_hours=3)
    result = compute(view, ("AAA",), {"AAA": 0.2}, config)

    expected = np.mean([
        24 / 100.0 / (0.2 * math.sqrt(1)),
        48 / 100.0 / (0.2 * math.sqrt(2)),
    ])
    assert result["AAA"] == pytest.approx(expected)


def test_signal_rejects_stale_horizon_and_insufficient_history():
    stale = [False] * 100
    stale[90] = True
    view = view_for(stale=stale)
    config = SignalConfig(horizons_days=(1,), skip_hours=0)
    assert math.isnan(compute(view, ("AAA",), {"AAA": 0.2}, config)["AAA"])

    short = view_for(n_rows=20)
    assert math.isnan(compute(short, ("AAA",), {"AAA": 0.2}, config)["AAA"])


def test_signal_passes_the_no_lookahead_checker():
    panel = Panel(
        index=[datetime(2024, 1, 1, tzinfo=UTC) + (i + 1) * HOUR for i in range(100)],
        close=np.exp(np.arange(100, dtype=float)[:, None] / 100.0),
        quote_volume=np.ones((100, 1)), stale=np.zeros((100, 1), dtype=bool),
        symbols=("AAA",),
    )
    signal_config = SignalConfig(horizons_days=(1,), skip_hours=1)
    def strategy(view, _):
        return compute(view, ("AAA",), {"AAA": 0.2}, signal_config)
    assert_no_lookahead(strategy, panel, datetime(2024, 1, 4, tzinfo=UTC), None, seed=5)
