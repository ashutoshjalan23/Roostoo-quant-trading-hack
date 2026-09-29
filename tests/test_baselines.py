"""Tests for pure strategy baselines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from qtrend.data.panel import Panel
from qtrend.strategy.baselines import buy_and_hold, equal_weight, top_volume, without_vol_target

HOUR = timedelta(hours=1)


def view_for():
    rows = 24
    volumes = np.column_stack((np.full(rows, 300.0), np.full(rows, 100.0)))
    panel = Panel(
        index=[datetime(2024, 1, 1, tzinfo=UTC) + (i + 1) * HOUR for i in range(rows)],
        close=np.ones((rows, 2)), quote_volume=volumes, stale=np.zeros((rows, 2), dtype=bool),
        symbols=("AAA", "BBB"),
    )
    return panel.slice_to(panel.end_time)


def test_baseline_weights_are_deterministic():
    assert buy_and_hold("BTC") == {"BTC": 1.0}
    assert equal_weight(("BBB", "AAA")) == {"AAA": 0.5, "BBB": 0.5}
    assert without_vol_target({"BBB": 0.4, "AAA": 0.6}) == {"AAA": 0.6, "BBB": 0.4}


def test_top_volume_uses_trailing_median():
    assert top_volume(view_for(), 1, 24) == {"AAA": 1.0}
