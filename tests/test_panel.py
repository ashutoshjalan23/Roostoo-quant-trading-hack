"""Tests for the data boundary.

The three named gate tests of CLAUDE.md Phase 2 are `test_slice_is_a_view_not_a_copy`,
`test_view_cannot_see_future` and `test_close_time_indexing`. Everything else here exists to
stop those three passing for the wrong reason.

No test in this file reads a private panel attribute: the boundary applies to tests too,
and a test that reaches through it would prove nothing about code that cannot.
"""

from __future__ import annotations

import re
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from qtrend.data.panel import Panel, PanelView

REPO_ROOT = Path(__file__).resolve().parents[1]

HOUR = timedelta(hours=1)
SYMBOLS = ("AAA", "BBB", "CCC")


def build_panel(n_rows: int = 48, symbols: tuple[str, ...] = SYMBOLS, start_hour: int = 0):
    """A panel whose every cell is distinguishable, so a wrong row is visibly wrong."""
    index = [
        datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=start_hour + i + 1)
        for i in range(n_rows)
    ]
    rows, cols = n_rows, len(symbols)
    close = np.arange(rows * cols, dtype=np.float64).reshape(rows, cols) + 100.0
    quote_volume = close * 1_000.0
    stale = np.zeros((rows, cols), dtype=bool)
    stale[::5] = True
    return Panel(
        index=index, close=close, quote_volume=quote_volume, stale=stale, symbols=symbols
    )


def snapshot(view: PanelView) -> dict[str, object]:
    """Everything a PanelView can be asked, as one comparable value."""
    state: dict[str, object] = {
        "n_rows": view.n_rows,
        "symbols": view.symbols,
        "end_time": view.end_time if view.n_rows else None,
        "last_prices": view.last_prices() if view.n_rows else None,
    }
    for symbol in view.symbols:
        state[f"close:{symbol}"] = view.close(symbol).tolist()
        state[f"volume:{symbol}"] = view.quote_volume(symbol).tolist()
        state[f"stale:{symbol}"] = view.stale_mask(symbol).tolist()
        state[f"offsets:{symbol}"] = [
            view.price_at_offset(symbol, back) for back in range(view.n_rows)
        ]
    return state


# --------------------------------------------------------------------------------------
# Gate test 1
# --------------------------------------------------------------------------------------


def test_slice_is_a_view_not_a_copy():
    """slice_to() allocates no new price array."""
    panel = build_panel(n_rows=20_000, symbols=("AAA", "BBB", "CCC", "DDD"))
    one_column_bytes = 20_000 * 8

    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        views = [panel.slice_to(datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=h))
                 for h in range(1, 501)]
        after = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()

    grew = after - before
    assert len(views) == 500
    assert grew < one_column_bytes, (
        f"500 slices allocated {grew} bytes, which is at least one price column "
        f"({one_column_bytes} bytes) -- slice_to is copying"
    )


def test_accessors_share_memory_with_the_panel():
    """Two independent views of the same panel read the same buffer, so neither copied."""
    panel = build_panel(n_rows=100)
    early = panel.slice_to(datetime(2024, 1, 2, tzinfo=UTC))
    late = panel.slice_to(datetime(2024, 1, 3, tzinfo=UTC))

    for symbol in SYMBOLS:
        assert np.shares_memory(early.close(symbol), late.close(symbol))
        assert np.shares_memory(early.quote_volume(symbol), late.quote_volume(symbol))
        assert np.shares_memory(early.stale_mask(symbol), late.stale_mask(symbol))
        assert early.close(symbol).base is not None, "a copy would own its data"


def test_returned_arrays_are_read_only():
    """A strategy cannot write back through the boundary."""
    view = build_panel().slice_to(datetime(2024, 1, 2, tzinfo=UTC))
    for array in (view.close("AAA"), view.quote_volume("AAA"), view.stale_mask("AAA")):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array[0] = 1


# --------------------------------------------------------------------------------------
# Gate test 2
# --------------------------------------------------------------------------------------


def test_view_cannot_see_future():
    """Mutate every row at and after the view's end; every accessor is unchanged."""
    panel = build_panel(n_rows=48)
    cutoff = datetime(2024, 1, 2, tzinfo=UTC)
    view = panel.slice_to(cutoff)
    assert 0 < view.n_rows < panel.n_rows, "cutoff must leave rows on both sides"

    before = snapshot(view)
    panel.randomize_after(view.end_time, seed=20240101)
    after = snapshot(view)

    assert before == after

    fresh = panel.slice_to(cutoff)
    assert snapshot(fresh) == before, "a view taken after the mutation must also be unchanged"


def test_mutation_control_the_perturbation_actually_bites():
    """The control for the test above: perturbing a VISIBLE row does change the view.

    Without this, test_view_cannot_see_future would pass just as happily against a
    randomize_after that did nothing at all.
    """
    panel = build_panel(n_rows=48)
    cutoff = datetime(2024, 1, 2, tzinfo=UTC)
    view = panel.slice_to(cutoff)

    before = snapshot(view)
    one_bar_earlier = view.end_time - HOUR
    panel.randomize_after(one_bar_earlier, seed=20240101)

    assert snapshot(view) != before


def test_randomize_after_changes_every_column():
    panel = build_panel(n_rows=48)
    cutoff = datetime(2024, 1, 2, tzinfo=UTC)
    full_before = snapshot(panel.slice_to(panel.end_time))
    panel.randomize_after(cutoff, seed=7)
    full_after = snapshot(panel.slice_to(panel.end_time))

    for symbol in SYMBOLS:
        for field in ("close", "volume", "stale"):
            assert full_before[f"{field}:{symbol}"] != full_after[f"{field}:{symbol}"], (
                f"randomize_after left {field} for {symbol} untouched"
            )


def test_randomize_after_is_seeded_and_deterministic():
    same_a, same_b, different = build_panel(), build_panel(), build_panel()
    cutoff = datetime(2024, 1, 2, tzinfo=UTC)
    same_a.randomize_after(cutoff, seed=11)
    same_b.randomize_after(cutoff, seed=11)
    different.randomize_after(cutoff, seed=12)

    end = same_a.end_time
    assert snapshot(same_a.slice_to(end)) == snapshot(same_b.slice_to(end))
    assert snapshot(different.slice_to(end)) != snapshot(same_a.slice_to(end))


def test_randomize_after_keeps_prices_positive():
    panel = build_panel()
    panel.randomize_after(datetime(2024, 1, 2, tzinfo=UTC), seed=3)
    view = panel.slice_to(panel.end_time)
    for symbol in SYMBOLS:
        assert np.all(view.close(symbol) > 0)


def test_copy_is_deep():
    """The checker perturbs a copy; the original must not move."""
    panel = build_panel()
    duplicate = panel.copy()
    end = panel.end_time
    before = snapshot(panel.slice_to(end))

    duplicate.randomize_after(datetime(2024, 1, 2, tzinfo=UTC), seed=5)

    assert snapshot(panel.slice_to(end)) == before
    assert snapshot(duplicate.slice_to(end)) != before


# --------------------------------------------------------------------------------------
# Gate test 3
# --------------------------------------------------------------------------------------


def test_close_time_indexing():
    """A bar covering 00:00-01:00 is indexed at its close, not its open."""
    opens = [datetime(2024, 1, 1, hour, tzinfo=UTC) for hour in range(3)]
    panel = Panel.from_bars(
        open_times=opens,
        interval=HOUR,
        close=np.array([[10.0], [11.0], [12.0]]),
        quote_volume=np.array([[1.0], [2.0], [3.0]]),
        stale=np.zeros((3, 1), dtype=bool),
        symbols=("AAA",),
    )

    # The bar that opened at 00:00 is indexed at 01:00.
    assert panel.start_time == datetime(2024, 1, 1, 1, tzinfo=UTC)

    # At 00:30 that bar has not closed, so nothing is visible.
    assert panel.slice_to(datetime(2024, 1, 1, 0, 30, tzinfo=UTC)).n_rows == 0

    # At 00:59:59 it still has not closed.
    assert panel.slice_to(datetime(2024, 1, 1, 0, 59, 59, tzinfo=UTC)).n_rows == 0

    # At 01:00 exactly one bar is visible, and it is the 00:00-01:00 bar.
    at_close = panel.slice_to(datetime(2024, 1, 1, 1, tzinfo=UTC))
    assert at_close.n_rows == 1
    assert at_close.end_time == datetime(2024, 1, 1, 1, tzinfo=UTC)
    assert at_close.last_prices() == {"AAA": 10.0}


def test_slice_to_is_inclusive_of_the_bar_closing_at_t():
    panel = build_panel(n_rows=10)
    first_close = datetime(2024, 1, 1, 1, tzinfo=UTC)
    assert panel.slice_to(first_close).n_rows == 1
    assert panel.slice_to(first_close - timedelta(microseconds=1)).n_rows == 0


def test_slice_before_any_bar_gives_an_empty_view():
    panel = build_panel()
    view = panel.slice_to(datetime(2023, 1, 1, tzinfo=UTC))
    assert view.n_rows == 0
    with pytest.raises(ValueError, match="view is empty"):
        _ = view.end_time
    with pytest.raises(ValueError, match="view is empty"):
        view.last_prices()


# --------------------------------------------------------------------------------------
# The boundary has no hole in it
# --------------------------------------------------------------------------------------


def test_panelview_exposes_exactly_the_specified_accessors():
    """CLAUDE.md Phase 2 lists these eight. An added accessor is a boundary change."""
    public = {name for name in dir(PanelView) if not name.startswith("_")}
    assert public == {
        "close",
        "quote_volume",
        "stale_mask",
        "price_at_offset",
        "last_prices",
        "n_rows",
        "symbols",
        "end_time",
    }


def test_no_module_outside_panel_touches_the_private_arrays():
    """CLAUDE.md Phase 2: grep confirms the private arrays have exactly one reader.

    The names are assembled rather than written out so this test does not match itself.
    """
    private = tuple("_" + name for name in ("close", "index", "quote_volume", "stale"))
    pattern = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(private) + r")(?![A-Za-z0-9_])")
    allowed = (REPO_ROOT / "src" / "qtrend" / "data" / "panel.py").resolve()

    offenders = []
    for directory in ("src", "scripts", "tests"):
        for path in (REPO_ROOT / directory).rglob("*.py"):
            if path.resolve() == allowed:
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {line.strip()}")

    assert offenders == [], "private panel arrays read outside panel.py:\n" + "\n".join(offenders)


def test_view_offers_no_absolute_addressing():
    """No accessor takes a row index or a timestamp. Offsets are relative, and backwards."""
    view = build_panel().slice_to(datetime(2024, 1, 2, tzinfo=UTC))

    with pytest.raises(ValueError, match="would name a future bar"):
        view.price_at_offset("AAA", -1)

    with pytest.raises(IndexError, match="before the start of the visible window"):
        view.price_at_offset("AAA", view.n_rows)


def test_price_at_offset_counts_backwards_from_end_time():
    panel = build_panel(n_rows=48)
    view = panel.slice_to(datetime(2024, 1, 2, tzinfo=UTC))
    prices = view.last_prices()

    assert view.price_at_offset("AAA", 0) == prices["AAA"]
    for back in range(view.n_rows):
        expected = view.close("AAA")[view.n_rows - 1 - back]
        assert view.price_at_offset("AAA", back) == expected


def test_unknown_symbol_is_rejected():
    view = build_panel().slice_to(datetime(2024, 1, 2, tzinfo=UTC))
    for call in (
        lambda: view.close("ZZZ"),
        lambda: view.quote_volume("ZZZ"),
        lambda: view.stale_mask("ZZZ"),
        lambda: view.price_at_offset("ZZZ", 0),
    ):
        with pytest.raises(KeyError, match="not in this panel"):
            call()


# --------------------------------------------------------------------------------------
# Panel invariants
# --------------------------------------------------------------------------------------


def test_symbols_are_sorted_and_columns_follow():
    """Column order is a function of the symbol names, never of the caller's order."""
    index = [datetime(2024, 1, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 2, tzinfo=UTC)]
    close = np.array([[1.0, 2.0], [3.0, 4.0]])
    panel = Panel(
        index=index,
        close=close,
        quote_volume=close * 10,
        stale=np.zeros((2, 2), dtype=bool),
        symbols=("ZZZ", "AAA"),
    )
    assert panel.symbols == ("AAA", "ZZZ")
    view = panel.slice_to(datetime(2024, 1, 1, 2, tzinfo=UTC))
    assert view.close("ZZZ").tolist() == [1.0, 3.0]
    assert view.close("AAA").tolist() == [2.0, 4.0]
    assert list(view.last_prices()) == ["AAA", "ZZZ"]


def test_non_monotonic_index_is_rejected():
    close = np.ones((2, 1))
    with pytest.raises(ValueError, match="strictly increasing"):
        Panel(
            index=[datetime(2024, 1, 1, 2, tzinfo=UTC), datetime(2024, 1, 1, 1, tzinfo=UTC)],
            close=close,
            quote_volume=close,
            stale=np.zeros((2, 1), dtype=bool),
            symbols=("AAA",),
        )


def test_non_uniform_grid_is_rejected():
    """Missing bars are forward-filled and flagged, never dropped (README section 7.1)."""
    close = np.ones((3, 1))
    with pytest.raises(ValueError, match="uniform grid"):
        Panel(
            index=[
                datetime(2024, 1, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 1, 2, tzinfo=UTC),
                datetime(2024, 1, 1, 9, tzinfo=UTC),
            ],
            close=close,
            quote_volume=close,
            stale=np.zeros((3, 1), dtype=bool),
            symbols=("AAA",),
        )


def test_shape_mismatch_is_rejected():
    with pytest.raises(ValueError, match="expected"):
        Panel(
            index=[datetime(2024, 1, 1, 1, tzinfo=UTC)],
            close=np.ones((1, 2)),
            quote_volume=np.ones((1, 1)),
            stale=np.zeros((1, 1), dtype=bool),
            symbols=("AAA",),
        )


def test_duplicate_symbols_are_rejected():
    close = np.ones((1, 2))
    with pytest.raises(ValueError, match="unique"):
        Panel(
            index=[datetime(2024, 1, 1, 1, tzinfo=UTC)],
            close=close,
            quote_volume=close,
            stale=np.zeros((1, 2), dtype=bool),
            symbols=("AAA", "AAA"),
        )


# --------------------------------------------------------------------------------------
# Simulator-only accessors (deliberately on Panel, not PanelView)
# --------------------------------------------------------------------------------------


def test_next_timestamp_and_prices_at_support_next_bar_fills():
    """CLAUDE.md rule 5: a decision at t fills at the NEXT bar."""
    panel = build_panel(n_rows=10)
    decision = datetime(2024, 1, 1, 3, tzinfo=UTC)
    fill_time = panel.next_timestamp(decision)

    assert fill_time == datetime(2024, 1, 1, 4, tzinfo=UTC)
    assert panel.prices_at(fill_time) != panel.slice_to(decision).last_prices()


def test_next_timestamp_at_the_end_raises():
    panel = build_panel(n_rows=5)
    with pytest.raises(IndexError, match="no bar after"):
        panel.next_timestamp(panel.end_time)


def test_prices_at_off_grid_raises():
    panel = build_panel()
    with pytest.raises(KeyError, match="not a bar close time"):
        panel.prices_at(datetime(2024, 1, 1, 1, 30, tzinfo=UTC))


def test_timestamps_between_is_inclusive_and_ascending():
    panel = build_panel(n_rows=10)
    stamps = panel.timestamps_between(
        datetime(2024, 1, 1, 2, tzinfo=UTC), datetime(2024, 1, 1, 5, tzinfo=UTC)
    )
    assert stamps == (
        datetime(2024, 1, 1, 2, tzinfo=UTC),
        datetime(2024, 1, 1, 3, tzinfo=UTC),
        datetime(2024, 1, 1, 4, tzinfo=UTC),
        datetime(2024, 1, 1, 5, tzinfo=UTC),
    )


def test_naive_datetimes_are_treated_as_utc():
    panel = build_panel(n_rows=5)
    aware = panel.slice_to(datetime(2024, 1, 1, 3, tzinfo=UTC))
    naive = panel.slice_to(datetime(2024, 1, 1, 3))
    assert aware.n_rows == naive.n_rows
