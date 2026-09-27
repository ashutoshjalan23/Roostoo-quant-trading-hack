"""The automated look-ahead checker of README section 7.2.

    Perturb all data strictly after t; assert the decision at t is unchanged.

A strategy that reads the future produces a different decision when the future changes. One
that does not, cannot. That is the whole idea, and it is the only test in this repository
that can catch a leak nobody thought of in advance.

Three things here are deliberate and should not be relaxed:

**The check raises, it does not `assert`.** README section 7.2 writes the check with a bare
`assert`. Under `python -O` every bare assert is stripped, and the checker would pass
everything in silence. Every check below raises explicitly.

**Equality is exact.** A pure function given identical visible data returns bit-identical
output (CLAUDE.md rule 8), so any difference at all is a leak. A tolerance here would hide
exactly the small leaks that are hardest to find by reading code. NaN compares equal to NaN,
which is not a tolerance: it is what "the two runs produced the same thing" means when the
output legitimately contains NaN.

**A check that cannot fail is not a pass.** If there are no rows after `t`, or the
perturbation happens not to change anything, the comparison is vacuous and the checker
raises rather than reporting success. A checker that has never caught anything is not a
checker, and neither is one that could not.

The checker also verifies that the perturbation left the *visible* rows untouched. If that
ever fails, the checker itself is broken and no result it produces means anything.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from qtrend.data.panel import Panel, PanelView

StrategyFn = Callable[[PanelView, object], object]


class LookaheadError(AssertionError):
    """A strategy's decision at `t` changed when data after `t` changed."""


class VacuousCheckError(AssertionError):
    """The check could not have failed, so its passing means nothing."""


class CheckerError(AssertionError):
    """The checker itself misbehaved. No result it produced is trustworthy."""


@dataclass(frozen=True)
class Difference:
    """Where two decisions diverged."""

    path: str
    baseline: object
    perturbed: object

    def describe(self) -> str:
        return f"{self.path}: {self.baseline!r} became {self.perturbed!r}"


# --------------------------------------------------------------------------------------
# Comparing two decisions
# --------------------------------------------------------------------------------------


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


def first_difference(baseline: object, perturbed: object, path: str) -> Difference | None:
    """The first place two decisions differ, or None. NaN equals NaN."""
    if isinstance(baseline, np.ndarray) or isinstance(perturbed, np.ndarray):
        left = np.asarray(baseline)
        right = np.asarray(perturbed)
        if left.shape != right.shape:
            return Difference(f"{path}.shape", left.shape, right.shape)
        if left.dtype.kind == "f" or right.dtype.kind == "f":
            same = np.array_equal(left, right, equal_nan=True)
        else:
            same = np.array_equal(left, right)
        return None if same else Difference(path, baseline, perturbed)

    if isinstance(baseline, Mapping) or isinstance(perturbed, Mapping):
        if not (isinstance(baseline, Mapping) and isinstance(perturbed, Mapping)):
            return Difference(path, baseline, perturbed)
        if set(baseline) != set(perturbed):
            missing = sorted(set(baseline) ^ set(perturbed), key=repr)
            return Difference(f"{path}.keys", sorted(baseline, key=repr), missing)
        for key in sorted(baseline, key=repr):
            found = first_difference(baseline[key], perturbed[key], f"{path}[{key!r}]")
            if found is not None:
                return found
        return None

    if isinstance(baseline, str) or isinstance(perturbed, str):
        return None if baseline == perturbed else Difference(path, baseline, perturbed)

    if isinstance(baseline, Sequence) or isinstance(perturbed, Sequence):
        if not (isinstance(baseline, Sequence) and isinstance(perturbed, Sequence)):
            return Difference(path, baseline, perturbed)
        if len(baseline) != len(perturbed):
            return Difference(f"{path}.len", len(baseline), len(perturbed))
        for i, (left, right) in enumerate(zip(baseline, perturbed, strict=True)):
            found = first_difference(left, right, f"{path}[{i}]")
            if found is not None:
                return found
        return None

    if _is_nan(baseline) and _is_nan(perturbed):
        return None
    return None if baseline == perturbed else Difference(path, baseline, perturbed)


# --------------------------------------------------------------------------------------
# Verifying the perturbation is real
# --------------------------------------------------------------------------------------


def _columns(panel: Panel) -> dict[str, np.ndarray]:
    """Every column of a panel, read through the public view accessors."""
    view = panel.slice_to(panel.end_time)
    out: dict[str, np.ndarray] = {}
    for symbol in view.symbols:
        out[f"close:{symbol}"] = np.asarray(view.close(symbol), dtype=np.float64)
        out[f"quote_volume:{symbol}"] = np.asarray(view.quote_volume(symbol), dtype=np.float64)
        out[f"stale:{symbol}"] = np.asarray(view.stale_mask(symbol), dtype=bool)
    return out


def _check_perturbation(original: Panel, perturbed: Panel, t: datetime) -> None:
    """Raise unless the perturbation changed the future and left the past alone."""
    visible = original.slice_to(t).n_rows
    if visible >= original.n_rows:
        raise VacuousCheckError(
            f"no bars after {t.isoformat()}: there is nothing to perturb, so a pass here "
            "would mean nothing"
        )

    before = _columns(original)
    after = _columns(perturbed)

    unchanged_past = [
        name
        for name in sorted(before)
        if not np.array_equal(before[name][:visible], after[name][:visible], equal_nan=True)
    ]
    if unchanged_past:
        raise CheckerError(
            "the perturbation modified visible rows, which it must never do: "
            + ", ".join(unchanged_past)
        )

    changed_future = [
        name
        for name in sorted(before)
        if not np.array_equal(before[name][visible:], after[name][visible:], equal_nan=True)
    ]
    if not changed_future:
        raise VacuousCheckError(
            f"the perturbation at {t.isoformat()} changed nothing after {t.isoformat()}, "
            "so the check could not have failed"
        )


# --------------------------------------------------------------------------------------
# The checker
# --------------------------------------------------------------------------------------


def assert_no_lookahead(
    strategy_fn: StrategyFn,
    panel: Panel,
    t: datetime,
    config: object,
    seed: int,
) -> None:
    """Perturb all data strictly after `t`; assert the decision at `t` is unchanged.

    The signature is the one in README section 7.2. Raises `LookaheadError` on a leak,
    `VacuousCheckError` if the check could not have failed, `CheckerError` if the checker
    itself misbehaved.
    """
    baseline = strategy_fn(panel.slice_to(t), config)

    perturbed = panel.copy()
    perturbed.randomize_after(t, seed=seed)
    _check_perturbation(panel, perturbed, t)

    result = strategy_fn(perturbed.slice_to(t), config)

    difference = first_difference(baseline, result, "decision")
    if difference is not None:
        raise LookaheadError(
            f"look-ahead detected at {t.isoformat()} (seed={seed}): the decision changed "
            f"when only data AFTER {t.isoformat()} changed. {difference.describe()}"
        )


def sample_timestamps(
    panel: Panel,
    *,
    n_samples: int,
    seed: int,
    min_rows: int,
) -> tuple[datetime, ...]:
    """A deterministic sample of timestamps at which the check is meaningful.

    Only timestamps that leave at least `min_rows` visible and at least one bar after are
    eligible; anywhere else the check would be vacuous. Returned ascending.
    """
    if n_samples < 1:
        raise ValueError("n_samples must be at least 1")
    if min_rows < 1:
        raise ValueError("min_rows must be at least 1")

    # Bar i leaves i+1 rows visible, so eligibility is min_rows-1 <= i <= n_rows-2.
    first = min_rows - 1
    last = panel.n_rows - 2
    if first > last:
        raise VacuousCheckError(
            f"no timestamp leaves {min_rows} rows visible and a bar after it in a panel of "
            f"{panel.n_rows} bars"
        )

    eligible = np.arange(first, last + 1)
    take = min(n_samples, eligible.size)
    rng = np.random.default_rng(seed)
    chosen = np.sort(rng.choice(eligible, size=take, replace=False))

    index = panel.timestamps_between(panel.start_time, panel.end_time)
    return tuple(index[int(i)] for i in chosen)


def assert_no_lookahead_across(
    strategy_fn: StrategyFn,
    panel: Panel,
    config: object,
    *,
    timestamps: Sequence[datetime],
    seed: int,
) -> None:
    """Run the check at every timestamp, each with its own derived seed.

    Several seeds matter: one perturbation might, by luck, leave a leaking strategy's
    output unchanged. Seeds are derived from `seed` so the whole sweep is reproducible.
    """
    if len(timestamps) == 0:
        raise VacuousCheckError("no timestamps to check")
    seeds = np.random.SeedSequence(seed).generate_state(len(timestamps))
    for t, derived in zip(timestamps, seeds, strict=True):
        assert_no_lookahead(strategy_fn, panel, t, config, int(derived))
