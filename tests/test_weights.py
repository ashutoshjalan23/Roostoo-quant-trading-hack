"""Tests for pure target weighting."""

from __future__ import annotations

import pytest

from qtrend.config import WeightsConfig
from qtrend.strategy.weights import assign


def test_equal_weights_sum_to_one():
    result = assign(("BBB", "AAA"), {}, WeightsConfig(scheme="equal", max_single_name=0.6))

    assert result == {"AAA": 0.5, "BBB": 0.5}
    assert sum(result.values()) == pytest.approx(1.0)


def test_inverse_volatility_prefers_lower_volatility():
    result = assign(
        ("AAA", "BBB"), {"AAA": 0.1, "BBB": 0.2},
        WeightsConfig(scheme="inverse_vol", max_single_name=0.8),
    )

    assert result["AAA"] == pytest.approx(2 / 3)
    assert result["BBB"] == pytest.approx(1 / 3)


def test_cap_is_redistributed_to_uncapped_names():
    result = assign(
        ("AAA", "BBB", "CCC"), {"AAA": 0.01, "BBB": 0.2, "CCC": 0.2},
        WeightsConfig(scheme="inverse_vol", max_single_name=0.5),
    )

    assert result["AAA"] == 0.5
    assert result["BBB"] == pytest.approx(0.25)
    assert result["CCC"] == pytest.approx(0.25)
