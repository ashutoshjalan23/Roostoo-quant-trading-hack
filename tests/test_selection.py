"""Tests for ranked selection and hysteresis."""

from __future__ import annotations

import math

from qtrend.config import SelectionConfig
from qtrend.strategy.selection import select


def config(**changes):
    values = dict(hour_utc=0, top_k=2, keep_rank=3, min_signal=0.0)
    values.update(changes)
    return SelectionConfig(**values)


def test_selection_is_ranked_and_deterministic_on_ties():
    signals = {"BBB": 0.5, "AAA": 0.5, "CCC": 0.2}

    assert select(signals, (), config()) == ("AAA", "BBB")


def test_existing_holding_is_kept_within_keep_rank():
    signals = {"AAA": 0.9, "BBB": 0.8, "CCC": 0.7, "DDD": 0.6}

    assert select(signals, ("CCC",), config()) == ("CCC", "AAA")


def test_new_entries_below_minimum_signal_are_rejected():
    signals = {"AAA": 0.1, "BBB": -0.1}

    assert select(signals, (), config(top_k=2, min_signal=0.0)) == ("AAA",)


def test_non_finite_signals_are_not_ranked():
    signals = {"AAA": math.nan, "BBB": math.inf, "CCC": 0.2}

    assert select(signals, (), config()) == ("CCC",)
