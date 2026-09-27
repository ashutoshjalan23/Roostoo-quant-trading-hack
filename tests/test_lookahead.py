"""Tests for the look-ahead checker.

The gate (CLAUDE.md Phase 4) is two assertions: the checker CATCHES a strategy that reads
one bar ahead, and then PASSES a correct trivial one. Everything else here defends those two
from passing for the wrong reason.
"""

from __future__ import annotations

import ast
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from leaky_strategies import (
    always_nan,
    constant_decision,
    honest_momentum,
    leaks_only_at_one_hour,
    leaks_through_stale_only,
    leaks_through_volume_only,
    reads_one_bar_ahead,
    reads_the_whole_future,
    records_the_future_but_decides_nothing,
)
from qtrend.backtest.lookahead import (
    CheckerError,
    LookaheadError,
    VacuousCheckError,
    assert_no_lookahead,
    assert_no_lookahead_across,
    first_difference,
    sample_timestamps,
)
from qtrend.data.synthetic import random_walk, with_gaps

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOKAHEAD_SOURCE = REPO_ROOT / "src" / "qtrend" / "backtest" / "lookahead.py"

HOUR = timedelta(hours=1)
START = datetime(2024, 1, 1, tzinfo=UTC)
SYMBOLS = ("AAA", "BBB", "CCC")
VOLUME = dict.fromkeys(SYMBOLS, 5_000_000.0)
CONFIG = {"horizon_bars": 24, "leak_hour": 9, "bars_ahead": 8}


def panel(n_bars: int = 480, seed: int = 1, jitter: float = 0.3):
    return random_walk(
        symbols=SYMBOLS, n_bars=n_bars, start=START, interval=HOUR, seed=seed,
        initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=jitter,
    )


def gapped_panel(n_bars: int = 480, seed: int = 1):
    """Stale flags present, so the stale column is not uniformly False."""
    return with_gaps(panel(n_bars, seed), gaps={"AAA": (60, 20), "CCC": (200, 30)})


MIDPOINT = START + 240 * HOUR


# --------------------------------------------------------------------------------------
# THE GATE
# --------------------------------------------------------------------------------------


def test_checker_catches_a_strategy_that_reads_one_bar_ahead():
    """The gate. A checker that has never caught anything is not a checker."""
    with pytest.raises(LookaheadError) as excinfo:
        assert_no_lookahead(reads_one_bar_ahead, panel(), MIDPOINT, CONFIG, seed=1)

    message = str(excinfo.value)
    assert "look-ahead detected at 2024-01-11T00:00:00+00:00" in message
    assert "the decision changed when only data AFTER" in message


def test_checker_passes_a_correct_trivial_strategy():
    """The other half of the gate."""
    assert_no_lookahead(constant_decision, panel(), MIDPOINT, CONFIG, seed=1)


def test_checker_passes_a_correct_non_trivial_strategy():
    assert_no_lookahead(honest_momentum, panel(), MIDPOINT, CONFIG, seed=1)


def test_checker_passes_the_honest_strategy_across_many_timestamps():
    data = panel()
    stamps = sample_timestamps(data, n_samples=40, seed=7, min_rows=48)
    assert_no_lookahead_across(honest_momentum, data, CONFIG, timestamps=stamps, seed=7)


def test_checker_catches_the_leak_across_many_timestamps():
    data = panel()
    stamps = sample_timestamps(data, n_samples=40, seed=7, min_rows=48)
    with pytest.raises(LookaheadError):
        assert_no_lookahead_across(reads_one_bar_ahead, data, CONFIG, timestamps=stamps, seed=7)


# --------------------------------------------------------------------------------------
# Every column is perturbed, not just prices
# --------------------------------------------------------------------------------------


def test_checker_catches_a_leak_that_reads_the_whole_future():
    with pytest.raises(LookaheadError):
        assert_no_lookahead(reads_the_whole_future, panel(), MIDPOINT, CONFIG, seed=2)


def test_checker_catches_a_leak_through_volume_alone():
    """Proves quote volume is perturbed. This strategy reads no future price at all."""
    with pytest.raises(LookaheadError):
        assert_no_lookahead(leaks_through_volume_only, panel(), MIDPOINT, CONFIG, seed=3)


def test_checker_catches_a_leak_through_the_stale_mask_alone():
    """Proves the stale column is perturbed. README section 7.2: "prices, volumes,
    everything"."""
    data = gapped_panel()
    stamps = sample_timestamps(data, n_samples=25, seed=4, min_rows=48)
    with pytest.raises(LookaheadError):
        assert_no_lookahead_across(
            leaks_through_stale_only, data, CONFIG, timestamps=stamps, seed=4
        )


def test_sampling_across_timestamps_catches_an_intermittent_leak():
    """A leak on one hour of the day. One timestamp can miss it; a sample does not."""
    data = panel(n_bars=720)
    safe_hour = datetime(2024, 1, 11, (CONFIG["leak_hour"] + 1) % 24, tzinfo=UTC)

    # At an hour it does not leak on, the strategy looks clean.
    assert_no_lookahead(leaks_only_at_one_hour, data, safe_hour, CONFIG, seed=5)

    # Across a sample that reaches the leaking hour, it does not.
    stamps = sample_timestamps(data, n_samples=100, seed=5, min_rows=48)
    assert any(t.hour == CONFIG["leak_hour"] for t in stamps), "sample missed the leaking hour"
    with pytest.raises(LookaheadError):
        assert_no_lookahead_across(leaks_only_at_one_hour, data, CONFIG, timestamps=stamps, seed=5)


# --------------------------------------------------------------------------------------
# A check that could not have failed is not a pass
# --------------------------------------------------------------------------------------


def test_check_at_the_last_bar_is_refused_as_vacuous():
    data = panel()
    with pytest.raises(VacuousCheckError, match="nothing to perturb"):
        assert_no_lookahead(constant_decision, data, data.end_time, CONFIG, seed=1)


def test_check_past_the_end_of_the_panel_is_refused_as_vacuous():
    data = panel()
    with pytest.raises(VacuousCheckError, match="nothing to perturb"):
        assert_no_lookahead(
            constant_decision, data, data.end_time + 10 * HOUR, CONFIG, seed=1
        )


def test_sampling_refuses_a_panel_with_no_eligible_timestamp():
    data = panel(n_bars=10)
    with pytest.raises(VacuousCheckError, match="no timestamp leaves"):
        sample_timestamps(data, n_samples=5, seed=1, min_rows=50)


def test_empty_timestamp_list_is_refused():
    with pytest.raises(VacuousCheckError, match="no timestamps to check"):
        assert_no_lookahead_across(constant_decision, panel(), CONFIG, timestamps=(), seed=1)


def test_a_perturbation_that_touches_visible_rows_is_a_checker_error():
    """If the checker's own perturbation leaked backwards, no result would mean anything."""
    from qtrend.backtest.lookahead import _check_perturbation

    original = panel(seed=1)
    tampered = panel(seed=2)  # differs everywhere, including before the cutoff
    with pytest.raises(CheckerError, match="modified visible rows"):
        _check_perturbation(original, tampered, MIDPOINT)


def test_a_perturbation_that_changes_nothing_is_vacuous():
    from qtrend.backtest.lookahead import _check_perturbation

    original = panel()
    with pytest.raises(VacuousCheckError, match="changed nothing"):
        _check_perturbation(original, original.copy(), MIDPOINT)


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_sample_timestamps_is_deterministic_and_ascending():
    data = panel()
    first = sample_timestamps(data, n_samples=30, seed=42, min_rows=48)
    again = sample_timestamps(data, n_samples=30, seed=42, min_rows=48)
    other = sample_timestamps(data, n_samples=30, seed=43, min_rows=48)

    assert first == again
    assert first != other
    assert list(first) == sorted(first)
    assert len(set(first)) == len(first)


def test_each_timestamp_in_a_sweep_gets_its_own_perturbation():
    """One perturbation can leave a leaking strategy's output unchanged by luck, so the
    sweep derives a fresh seed per check rather than reusing one.

    Checking the SAME timestamp twice isolates the seed: the visible data is identical both
    times, so any difference in what the spy sees comes from the seed alone.
    """
    seen: list[float] = []
    config = dict(CONFIG, seen=seen)
    assert_no_lookahead_across(
        records_the_future_but_decides_nothing,
        panel(),
        config,
        timestamps=(MIDPOINT, MIDPOINT),
        seed=11,
    )

    baseline_first, perturbed_first, baseline_second, perturbed_second = seen
    assert baseline_first == baseline_second, "the unperturbed panel must not move"
    assert perturbed_first != perturbed_second, (
        "both checks saw the same perturbation, so the sweep is reusing one seed"
    )


def test_a_sweep_is_reproducible_and_the_master_seed_controls_it():
    def run(seed: int) -> list[float]:
        seen: list[float] = []
        assert_no_lookahead_across(
            records_the_future_but_decides_nothing,
            panel(),
            dict(CONFIG, seen=seen),
            timestamps=(MIDPOINT, MIDPOINT + 24 * HOUR),
            seed=seed,
        )
        return seen

    assert run(12) == run(12), "the same seed must reproduce the same sweep"
    assert run(12) != run(13), "a different seed must perturb differently"


def test_sampled_timestamps_are_all_eligible():
    data = panel()
    min_rows = 48
    for t in sample_timestamps(data, n_samples=50, seed=8, min_rows=min_rows):
        assert data.slice_to(t).n_rows >= min_rows
        assert t < data.end_time


def test_sampling_caps_at_the_number_available():
    data = panel(n_bars=60)
    stamps = sample_timestamps(data, n_samples=10_000, seed=1, min_rows=48)
    assert len(stamps) == 60 - 48


@pytest.mark.parametrize(("n_samples", "min_rows"), [(0, 10), (-1, 10), (5, 0)])
def test_sampling_rejects_nonsense_arguments(n_samples, min_rows):
    with pytest.raises(ValueError, match="at least 1"):
        sample_timestamps(panel(), n_samples=n_samples, seed=1, min_rows=min_rows)


# --------------------------------------------------------------------------------------
# Comparing decisions
# --------------------------------------------------------------------------------------


def test_nan_compares_equal_to_nan():
    """Otherwise every warm-up bar would be reported as a leak."""
    assert first_difference(math.nan, math.nan, "x") is None
    assert first_difference({"A": math.nan}, {"A": math.nan}, "d") is None
    assert_no_lookahead(always_nan, panel(), MIDPOINT, CONFIG, seed=1)


def test_float_comparison_is_exact_not_approximate():
    """A tolerance would hide exactly the small leaks that reading code misses."""
    tiny = 1e-15
    difference = first_difference({"A": 1.0}, {"A": 1.0 + tiny}, "d")
    assert difference is not None
    assert difference.path == "d['A']"


@pytest.mark.parametrize(
    ("left", "right", "expected_path"),
    [
        ({"A": 1.0}, {"A": 2.0}, "d['A']"),
        ({"A": 1.0}, {"B": 1.0}, "d.keys"),
        ([1, 2, 3], [1, 2, 4], "d[2]"),
        ([1, 2], [1, 2, 3], "d.len"),
        ({"A": [1, {"B": 2}]}, {"A": [1, {"B": 3}]}, "d['A'][1]['B']"),
        (np.array([1.0, 2.0]), np.array([1.0, 3.0]), "d"),
        (np.array([1.0]), np.array([1.0, 2.0]), "d.shape"),
        ("hold", "trade", "d"),
        (None, 1, "d"),
        (1.0, math.nan, "d"),
    ],
)
def test_first_difference_finds_and_names_the_divergence(left, right, expected_path):
    difference = first_difference(left, right, "d")
    assert difference is not None
    assert difference.path == expected_path


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"A": 1.0, "B": 2.0}, {"B": 2.0, "A": 1.0}),
        (np.array([1.0, math.nan]), np.array([1.0, math.nan])),
        ((1, 2, 3), (1, 2, 3)),
        ("same", "same"),
        (None, None),
    ],
)
def test_first_difference_returns_none_for_equal_decisions(left, right):
    assert first_difference(left, right, "d") is None


# --------------------------------------------------------------------------------------
# The checker cannot be disabled by running with -O
# --------------------------------------------------------------------------------------


def test_lookahead_module_contains_no_bare_assert():
    """`python -O` strips bare asserts. A checker that silently disappears is worse than
    no checker, so every check in this module raises explicitly."""
    tree = ast.parse(LOOKAHEAD_SOURCE.read_text(encoding="utf-8"))
    asserts = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert asserts == [], f"bare assert statements at lines {asserts}"


def test_lookahead_errors_are_assertion_errors():
    """So a test suite treats a detected leak as a failure, not an unexpected exception."""
    for error in (LookaheadError, VacuousCheckError, CheckerError):
        assert issubclass(error, AssertionError)
